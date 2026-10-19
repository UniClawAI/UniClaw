import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Optional, Any
import uuid

from uniclaw.utils.process import run_command

logger = logging.getLogger(__name__)


async def _run_git(
    *args: str,
    cwd: str | None = None,
    check: bool = False,
    timeout: float = 60,
) -> subprocess.CompletedProcess:
    """异步执行 git 命令,返回 CompletedProcess 风格的结果。

    经 run_command 统一处理超时(进程树回收)与解码。超时抛 TimeoutError,
    避免 git 卡死(如锁竞争)时永久挂起调用方。

    Args:
        *args: git 命令行(含可执行文件名,如 "git", "status")。
        cwd: 工作目录。
        check: 为 True 且命令失败时抛 subprocess.CalledProcessError。
        timeout: 超时秒数。

    Returns:
        subprocess.CompletedProcess: 带 returncode/stdout/stderr 的结果。

    Raises:
        FileNotFoundError: git 未安装。
        TimeoutError: 执行超时(TimeoutError 是 OSError 子类)。
        subprocess.CalledProcessError: check 为 True 且命令失败。
    """
    r = await run_command(list(args), cwd=cwd, timeout=timeout)
    if r.timed_out:
        raise TimeoutError(f"git 命令超时({timeout}秒): {' '.join(args)}")
    result = subprocess.CompletedProcess(
        args=args, returncode=r.code, stdout=r.stdout, stderr=r.stderr
    )
    if check and r.code != 0:
        raise subprocess.CalledProcessError(r.code, args, r.stdout, r.stderr)
    return result


async def get_git_root(root_dir: Path) -> Optional[str]:
    """返回 root_dir 的 git 根目录,如果不在 git 仓库中则返回 None。"""
    try:
        r = await _run_git(
            "git", "rev-parse", "--show-toplevel", cwd=str(root_dir), check=True
        )
        return r.stdout.strip()
    except Exception as e:
        logger.debug("获取 git 根目录失败: %s", e)
        return None


async def is_git_installed() -> bool:
    """检测 git 是否已安装。"""
    try:
        result = await _run_git("git", "--version")
        return result.returncode == 0
    except FileNotFoundError:
        return False


async def is_git_repo(root_dir: Path = None) -> bool:
    """检测是否在 git 仓库中(已 git init)。

    Args:
        root_dir: 仓库根目录路径

    Returns:
        bool: 是否在 git 仓库中
    """
    if root_dir:
        return await get_git_root(root_dir) is not None
    return True


async def has_git_commit(root_dir: Path = None) -> bool:
    """检测 git 仓库是否有至少一次 commit。

    git stash 需要至少一次 commit 才能使用。

    Args:
        root_dir: 仓库根目录路径

    Returns:
        bool: 是否有 commit
    """
    if not await is_git_repo(root_dir):
        return False
    try:
        result = await _run_git(
            "git", "rev-parse", "HEAD", cwd=str(root_dir) if root_dir else None
        )
        return result.returncode == 0
    except Exception as e:
        logger.debug("检查 git commit 失败: %s", e)
        return False


async def create_worktree(base_dir: Path) -> tuple:
    """创建一个临时的 git worktree。

    返回:
        (worktree_path, branch_name)
    异常:
        失败时抛出 subprocess.CalledProcessError 或 OSError。
    """
    branch = f"nano-agent-{uuid.uuid4().hex[:8]}"
    # mkdtemp 给我们一个路径；删除空目录以便 git 可以创建它
    wt_path = tempfile.mkdtemp(prefix="nano-agent-wt-")
    os.rmdir(wt_path)
    await _run_git(
        "git",
        "worktree",
        "add",
        "-b",
        branch,
        wt_path,
        cwd=str(base_dir),
        check=True,
    )
    return wt_path, branch


async def remove_worktree(wt_path: Path, branch: str, base_dir: Path) -> None:
    """移除 git worktree 并删除其分支(尽力而为)。"""
    try:
        await _run_git(
            "git", "worktree", "remove", "--force", str(wt_path), cwd=str(base_dir)
        )
    except Exception as e:
        logger.debug("移除 git worktree 失败: %s", e)
    try:
        await _run_git("git", "branch", "-D", branch, cwd=str(base_dir))
    except Exception as e:
        logger.debug("删除 git 分支失败: %s", e)


# ── Git Stash 检查点 ──────────────────────────────────────────────────────────
# HEAD 锚点:工作区无改动时 git stash 无法创建条目,为保证"每个聊天 turn 都有
# 检查点可恢复",在 .git/uniclaw-head-anchors.json 记录一条轻量锚点(HEAD hash +
# 时间 + 消息)。恢复锚点 = 丢弃所有未提交改动,回到 HEAD 干净状态。


def _checkpoint_anchor_path(git_root: str) -> Path:
    """HEAD 锚点文件路径(.git 内:不进 commit、不受 git clean 影响)。"""
    return Path(git_root) / ".git" / "uniclaw-head-anchors.json"


def _load_head_anchors(git_root: str) -> list[dict]:
    """读取 HEAD 锚点列表,文件缺失/损坏时返回空列表(不影响 stash 主流程)。"""
    try:
        data = json.loads(_checkpoint_anchor_path(git_root).read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [a for a in data if isinstance(a, dict) and a.get("type") == "head"]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _save_head_anchors(git_root: str, anchors: list[dict]) -> None:
    _checkpoint_anchor_path(git_root).write_text(
        json.dumps(anchors, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _merged_checkpoint_items(git_root: str) -> list[dict]:
    """合并 stash 条目与 HEAD 锚点,按时间倒序(新→旧)。

    Returns:
        条目列表,每个元素:
        - stash: {"kind": "stash", "stash_index": k, "text": <stash list 行>, "time": ts}
        - head:  {"kind": "head", "anchor_index": k, "text": "[HEAD] msg", "time": ts}
    """
    items: list[dict[str, Any]] = []
    # stash 条目:默认输出提供序号与消息, --format=%ct 提供每行时间戳(顺序一致)
    stash_result = await _run_git("git", "stash", "list", cwd=str(git_root))
    ts_result = await _run_git("git", "stash", "list", "--format=%ct", cwd=str(git_root))
    stash_lines = (stash_result.stdout or "").splitlines()
    ts_lines = (ts_result.stdout or "").splitlines()
    for i, line in enumerate(stash_lines):
        ts = 0
        if i < len(ts_lines) and ts_lines[i].strip().isdigit():
            ts = int(ts_lines[i])
        items.append({"kind": "stash", "stash_index": i, "text": line, "time": ts})
    # HEAD 锚点(ts 损坏时按 0 处理,避免整个列表崩溃)
    for i, a in enumerate(_load_head_anchors(git_root)):
        msg = a.get("message", "") or f"anchor-{i}"
        try:
            ts = int(a.get("ts", 0))
        except (TypeError, ValueError):
            ts = 0
        items.append(
            {
                "kind": "head",
                "anchor_index": i,
                "text": f"[HEAD] {msg}",
                "time": ts,
            }
        )
    # 新→旧;同秒时 stash 排前(保持 stash 栈的相对顺序)
    # 注意 reverse=True 时 key 大者在前,故 stash(1) > head(0)
    items.sort(key=lambda x: (x["time"], 0 if x["kind"] == "head" else 1), reverse=True)
    return items


async def git_create_checkpoint(root_dir: Path, message: str = "") -> bool:
    """创建检查点:git stash push + apply(有改动时);HEAD 锚点(无改动时)

    在 assistant turn 开始前调用,捕获当前工作目录状态。
    有未提交改动:stash push 捕获(含暂存区与未跟踪文件),随后立即 apply 恢复;
    工作区无改动:git stash 无法创建条目,改记为 HEAD 锚点,
    保证每个聊天 turn 都有检查点可恢复。
    两者都在 list/pop/apply 中按时间统一排序呈现。
    message 使用用户消息的前 50 个字符,便于在检查点列表中识别。

    Args:
        root_dir: 仓库根目录路径
        message: 检查点描述,默认使用时间戳

    Returns:
        bool: 是否成功创建(不在 git 仓库 / HEAD 不可解析时返回 False)
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return False
    # 截取前 50 字符,去除换行,避免 git message 解析问题
    if message:
        msg = message.replace("\n", " ")[:50]
    else:
        msg = f"checkpoint-{time.strftime('%Y%m%d-%H%M%S')}"
    # 工作区无改动时 git stash 无法创建条目:记录 HEAD 锚点,
    # 保证每个聊天 turn 都有检查点可恢复(恢复锚点 = 丢弃未提交改动回到 HEAD)
    status_result = await _run_git(
        "git", "status", "--porcelain", cwd=str(git_root)
    )
    if status_result.returncode != 0 or not status_result.stdout.strip():
        head_result = await _run_git("git", "rev-parse", "HEAD", cwd=str(git_root))
        if head_result.returncode != 0:
            return False
        now = int(time.time())
        anchors = _load_head_anchors(git_root)
        anchors.append(
            {
                "type": "head",
                "message": msg,
                "head": head_result.stdout.strip(),
                "ts": now,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            }
        )
        _save_head_anchors(git_root, anchors)
        return True
    # 第一步:stash push 保存当前状态
    result = await _run_git(
        "git",
        "stash",
        "push",
        "--include-untracked",
        "-m",
        msg,
        cwd=str(git_root),
    )
    if result.returncode != 0:
        return False
    # 第二步:立即 apply 恢复工作区(--index 还原暂存区;索引无法精确还原时降级为裸 apply)
    result = await _run_git(
        "git",
        "stash",
        "apply",
        "--index",
        cwd=str(git_root),
    )
    if result.returncode != 0:
        # --index 失败(stash 记录的索引无法与当前索引对齐),降级为裸 apply,
        # 至少恢复工作区内容;此场景下暂存区状态可能无法还原。
        result = await _run_git("git", "stash", "apply", cwd=str(git_root))
    if result.returncode != 0:
        logger.warning(
            "检查点已创建,但工作区还原失败(改动全部保留在 stash): %s",
            (result.stderr or "").strip(),
        )
    return True


async def _git_stash_restore(
    git_root: str, cmd: str, index: int
) -> subprocess.CompletedProcess:
    """执行 stash pop/apply,优先带 --index 还原暂存区(检查点保存时含索引状态)。

    带 --index 失败时(git 报 "conflicts in index. Try without --index."),
    stash 记录仍保留且不产生部分应用,可安全降级为裸命令重试 ——
    代价是暂存区状态无法还原,但工作区内容不丢。
    """
    result = await _run_git(
        "git", "stash", cmd, "--index", f"stash@{{{index}}}", cwd=git_root
    )
    if result.returncode == 0:
        return result
    return await _run_git("git", "stash", cmd, f"stash@{{{index}}}", cwd=git_root)


async def _git_restore_helper(
    root_dir: Path, index: int, use_pop: bool
) -> tuple[bool, str]:
    """检查点恢复的内部实现,供 git_pop_checkpoint 和 git_apply_checkpoint 调用。

    检查点列表为 stash 条目与 HEAD 锚点的统一索引(按时间倒序):
    - stash 条目:对应 stash@{k},执行 stash pop/apply(优先 --index 还原暂存区,
      失败自动降级;冲突时 checkout + clean 强制恢复)
    - HEAD 锚点:工作区无改动时的基线,恢复 = 丢弃所有未提交改动回到 HEAD
      (git checkout HEAD -- . + git clean -fd)

    Args:
        root_dir: 仓库根目录路径,用于定位 git 仓库
        index: 统一检查点序号,0 表示最近一次
        use_pop: True 使用 git stash pop(恢复并从 stash 列表中删除),
                 False 使用 git stash apply(恢复但保留 stash 记录)

    Returns:
        (success, message) 元组:
        - success: 操作是否成功
        - message: 成功时为操作描述,失败时为错误信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return False, "不在 git 仓库中"

    items = await _merged_checkpoint_items(git_root)
    if index < 0 or index >= len(items):
        return False, f"检查点序号 {index} 不存在(共 {len(items)} 个)"
    item = items[index]

    # HEAD 锚点:丢弃所有未提交改动,回到 HEAD 干净状态
    if item["kind"] == "head":
        name = item["text"]
        for cmd_args in (
            ("git", "checkout", "HEAD", "--", "."),
            ("git", "clean", "-fd"),
        ):
            r = await _run_git(*cmd_args, cwd=str(git_root))
            if r.returncode != 0:
                return False, f"恢复检查点失败({cmd_args[1]}): {(r.stderr or '').strip()}"
        # 验证工作区确实回到干净态,避免"伪成功"
        verify = await _run_git("git", "status", "--porcelain", cwd=str(git_root))
        if verify.returncode != 0 or verify.stdout.strip():
            return False, "恢复检查点失败: 工作区仍有未提交改动,请手动检查"
        if use_pop:
            anchors = _load_head_anchors(git_root)
            ai = item["anchor_index"]
            if 0 <= ai < len(anchors):
                anchors.pop(ai)
                _save_head_anchors(git_root, anchors)
                return True, f"已恢复并删除检查点: {name}"
        return True, f"已恢复检查点: {name}"

    # stash 条目:记录在合并列表中的 stash 序号即 stash@{k}
    stash_index = item["stash_index"]
    cmd = "pop" if use_pop else "apply"
    action = "恢复并删除" if use_pop else "恢复"

    # 先尝试 stash pop/apply(带 --index 还原暂存区,失败自动降级)
    result = await _git_stash_restore(git_root, cmd, stash_index)
    if result.returncode == 0:
        return True, f"已{action}检查点 stash@{{{stash_index}}}"

    stderr = (result.stderr or "").strip()
    # 冲突时,使用 git checkout + git clean 强制恢复
    if (
        "would be overwritten" in stderr
        or "already exists" in stderr
        or "conflict" in stderr.lower()
    ):
        # 1. 丢弃当前工作区修改
        await _run_git("git", "checkout", "HEAD", "--", ".", cwd=str(git_root))
        # 2. 删除未跟踪的文件
        await _run_git("git", "clean", "-fd", cwd=str(git_root))
        # 3. 从 stash 恢复(带 --index 还原暂存区,失败自动降级)
        result = await _git_stash_restore(git_root, cmd, stash_index)
        if result.returncode == 0:
            return True, f"已{action}检查点 stash@{{{stash_index}}}"
        return False, f"{action}检查点失败: {(result.stderr or '').strip()}"

    return False, stderr or f"没有可{action}的检查点"


async def git_pop_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点并删除:git stash pop stash@{index}"""
    return await _git_restore_helper(root_dir, index, use_pop=True)


async def git_apply_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点但保留:git stash apply stash@{index}"""
    return await _git_restore_helper(root_dir, index, use_pop=False)


async def git_delete_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """删除检查点(统一索引):HEAD 锚点直接移除;stash 条目执行 git stash drop。

    Args:
        root_dir: 仓库根目录路径
        index: 检查点序号,默认 0(最近的)

    Returns:
        (success, message) — success 是否成功,message 为结果描述或错误信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return False, "不在 git 仓库中"
    items = await _merged_checkpoint_items(git_root)
    if index < 0 or index >= len(items):
        return False, f"检查点序号 {index} 不存在(共 {len(items)} 个)"
    item = items[index]
    # HEAD 锚点:从锚点文件移除
    if item["kind"] == "head":
        anchors = _load_head_anchors(git_root)
        ai = item["anchor_index"]
        if 0 <= ai < len(anchors):
            anchors.pop(ai)
            _save_head_anchors(git_root, anchors)
            return True, f"已删除检查点: {item['text']}"
        return False, f"检查点序号 {index} 不存在"
    # stash 条目:git stash drop
    stash_index = item["stash_index"]
    result = await _run_git(
        "git",
        "stash",
        "drop",
        f"stash@{{{stash_index}}}",
        cwd=str(git_root),
    )
    if result.returncode == 0:
        return True, f"已删除检查点: {item['text']}"
    return False, (result.stderr or "").strip() or "删除检查点失败"


async def _get_stash_untracked_tree(git_root: Path, stash_ref: str) -> str | None:
    """获取 stash 的第三个 parent tree(未跟踪文件),不存在则返回 None。"""
    result = await _run_git("git", "rev-parse", f"{stash_ref}^3", cwd=str(git_root))
    if result.returncode != 0:
        return None
    tree_result = await _run_git(
        "git",
        "cat-file",
        "-p",
        result.stdout.strip(),
        cwd=str(git_root),
    )
    if tree_result.returncode != 0:
        return None
    # 提取 tree 行的 hash
    for line in (tree_result.stdout or "").splitlines():
        if line.startswith("tree "):
            return line.split()[1]
    return None


async def _diff_stash_untracked_between(
    git_root: Path, index_a: int, index_b: int
) -> str:
    """比较两个 stash 之间未跟踪文件的差异。"""
    tree_a = await _get_stash_untracked_tree(git_root, f"stash@{{{index_a}}}")
    tree_b = await _get_stash_untracked_tree(git_root, f"stash@{{{index_b}}}")
    if not tree_a and not tree_b:
        return ""
    EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    if not tree_a:
        tree_a = EMPTY_TREE
    if not tree_b:
        tree_b = EMPTY_TREE
    result = await _run_git("git", "diff", tree_a, tree_b, cwd=str(git_root))
    return (result.stdout or "").strip()


async def _diff_stash_untracked_vs_workdir(git_root: Path, index: int) -> str:
    """比较 stash 中的未跟踪文件与当前工作目录的差异。"""
    tree_hash = await _get_stash_untracked_tree(git_root, f"stash@{{{index}}}")
    if not tree_hash:
        return ""
    # 将当前未跟踪文件暂存到 index,与 stash 的 untracked tree 对比
    await _run_git("git", "add", "--intent-to-add", ".", cwd=str(git_root))
    try:
        result = await _run_git("git", "diff", "--cached", tree_hash, cwd=str(git_root))
        return (result.stdout or "").strip()
    finally:
        await _run_git("git", "reset", cwd=str(git_root))


async def git_diff_checkpoint(root_dir: Path, index: int = 0) -> str:
    """查看检查点与当前文件的差异(含未跟踪文件)。

    Args:
        root_dir: 仓库根目录路径
        index: 检查点序号,默认 0(最近的)

    Returns:
        str: 变更的 diff 文本,无变更时返回提示信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return "不在 git 仓库中"
    items = await _merged_checkpoint_items(git_root)
    if index < 0 or index >= len(items):
        return f"检查点序号 {index} 不存在(共 {len(items)} 个)"
    item = items[index]
    if item["kind"] == "head":
        return "HEAD 基线检查点(工作区无改动时记录),恢复将丢弃当前所有未提交改动"
    # 已跟踪文件的 diff
    result = await _run_git(
        "git",
        "diff",
        f"stash@{{{item['stash_index']}}}",
        cwd=str(git_root),
    )
    tracked = (result.stdout or "").strip()
    # 未跟踪文件的 diff
    untracked = await _diff_stash_untracked_vs_workdir(git_root, item["stash_index"])
    parts = [p for p in [tracked, untracked] if p]
    return "\n".join(parts) if parts else "检查点与当前文件没有差异"


async def git_diff_current(root_dir: Path) -> str:
    """查看当前未提交的变更:git diff

    Args:
        root_dir: 仓库根目录路径

    Returns:
        str: 变更的 diff 文本,无变更时返回提示信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return "不在 git 仓库中"
    result = await _run_git("git", "diff", cwd=str(git_root))
    return (result.stdout or "").strip() or "当前没有未提交的变更"


async def git_diff_between(root_dir: Path, index_a: int, index_b: int) -> str:
    """比较两个检查点的差异(含未跟踪文件)。

    Args:
        root_dir: 仓库根目录路径
        index_a: 第一个检查点序号
        index_b: 第二个检查点序号

    Returns:
        str: 变更的 diff 文本,无差异时返回提示信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return "不在 git 仓库中"
    items = await _merged_checkpoint_items(git_root)
    if index_a < 0 or index_a >= len(items) or index_b < 0 or index_b >= len(items):
        return f"检查点序号不存在(共 {len(items)} 个)"
    item_a, item_b = items[index_a], items[index_b]
    if item_a["kind"] == "head" or item_b["kind"] == "head":
        return "暂不支持 HEAD 基线检查点与其他检查点直接比较"
    # 已跟踪文件的 diff
    result = await _run_git(
        "git",
        "diff",
        f"stash@{{{item_a['stash_index']}}}",
        f"stash@{{{item_b['stash_index']}}}",
        cwd=str(git_root),
    )
    tracked = (result.stdout or "").strip()
    # 未跟踪文件的 diff
    untracked = await _diff_stash_untracked_between(
        git_root, item_a["stash_index"], item_b["stash_index"]
    )
    parts = [p for p in [tracked, untracked] if p]
    return "\n".join(parts) if parts else "两个检查点没有差异"


async def git_list_checkpoints(root_dir: Path) -> str:
    """列出所有检查点(stash 条目 + HEAD 锚点,按时间倒序)。

    Args:
        root_dir: 仓库根目录路径

    Returns:
        str: 检查点列表文本,无检查点时返回提示信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return "不在 git 仓库中"
    items = await _merged_checkpoint_items(git_root)
    if not items:
        return "没有检查点"
    lines = []
    for i, it in enumerate(items):
        ts = it.get("time", 0)
        ts_str = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
            if ts
            else "-"
        )
        lines.append(f"[{i}] {it['text']} ({ts_str})")
    return "\n".join(lines)


async def git_generate_commit_message(root_dir: Path, config) -> dict:
    """AI 生成 commit message。

    获取暂存区 diff(fallback 到未暂存 diff),调用 LLM 生成
    Conventional Commits 格式的中文 commit message。

    Args:
        root_dir: 仓库根目录路径
        config: AppConfig 实例,用于调用 LLM

    Returns:
        dict: 成功返回 {"message": "..."}, 失败返回 {"error": "..."}
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return {"error": "当前目录不是 git 仓库"}

    # 优先暂存区,fallback 到未暂存修改
    result = await _run_git("git", "diff", "--cached", cwd=git_root)
    diff_text = result.stdout or ""

    if not diff_text.strip():
        result = await _run_git("git", "diff", cwd=git_root)
        diff_text = result.stdout or ""

    if not diff_text.strip():
        return {"error": "未检测到变化"}

    # 截断过长的 diff(避免超出 token 限制)
    max_diff_chars = 30000
    if len(diff_text) > max_diff_chars:
        diff_text = diff_text[:max_diff_chars] + "\n... (diff 过长,已截断)"

    system_prompt = """\
你是一个专业的 git commit message 生成器。根据 git diff 内容,生成规范的 Conventional Commits 格式的 commit message。

## 格式

<type>(<scope>): <description>
<空行>
<body>
<空行>
<footer>

## type 选择规则

- feat: 新增功能或用户可见的行为变化
- fix: Bug 修复
- refactor: 重构代码(不改变外部行为)
- docs: 仅文档变更
- style: 代码格式调整(不影响逻辑,如空格、分号)
- test: 新增或修改测试
- chore: 构建流程、依赖管理、工具配置等杂项
- perf: 性能优化
- ci: CI/CD 配置变更

## scope 规则

- 受影响最大的模块或文件名(不含路径),如 agent、webui、config
- 涉及多个模块的变更可省略 scope

## description 规则

- 一句话概括变更的核心目的
- 祈使句(如"添加"而非"添加了"),中文
- 不超过 50 字符,首字母小写,末尾不加句号

## body 规则

- body 是 commit message 的重要组成部分,大多数有意义的变更都应该有 body
- 用简洁的条目式描述具体做了什么,每条一行
- 每行不超过 72 字符
- 说明变更的内容、方式或动机,而不仅仅是重复标题

## footer 规则

- 有破坏性变更时添加: BREAKING CHANGE: <描述>

## 输出要求

- 只输出 commit message 文本,不要任何解释、代码块或额外格式
- body 用条目式(- 开头),不要写成段落

## 示例

feat(git): 添加 AI 生成 commit message 功能

- 新增 git_generate_commit_message 函数,通过 LLM 分析 git diff
  内容并生成符合 Conventional Commits 规范的中文 commit message
- 在 WebUI 中添加对应的 API 端点 /api/git/ai-commit-message
- 前端界面上增加 AI 生成 commit 按钮,支持异步请求和错误处理

fix(auth): 修复 token 过期后无法自动刷新的问题

- 检测到 401 响应时自动触发 refresh_token 流程
- 刷新失败时清除本地缓存并跳转登录页
- 添加刷新锁防止并发请求重复刷新

refactor: 统一配置加载逻辑

- 将分散在各模块的 settings.json 读取合并到 config.py
- 使用 pydantic-settings 做类型校验和默认值处理"""

    try:
        from uniclaw.provider.fallback import achat
        from uniclaw.tools.session.session import Session

        session = Session()
        session.add_user_message(
            content=f"请为以下 git diff 生成 commit message:\n\n```diff\n{diff_text}\n```"
        )

        resp = await achat(
            system_prompt,
            session,
            model_name=config.mini_model_name,
            enable_thinking=False,
            thinking=False,
            config=config,
            temperature=None,
            max_tokens=500,
        )
        return {"message": resp.content.strip()}
    except Exception as e:
        return {"error": f"AI 生成失败: {e}"}
