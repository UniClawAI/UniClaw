import asyncio
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Optional
import uuid


async def _run_git(*args: str, cwd: str | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """异步执行 git 命令,返回 CompletedProcess 风格的结果。"""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    result = subprocess.CompletedProcess(
        args=args, returncode=proc.returncode, stdout=stdout, stderr=stderr
    )
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args, stdout, stderr)
    return result


async def get_git_root(root_dir: Path) -> Optional[str]:
    """返回 root_dir 的 git 根目录,如果不在 git 仓库中则返回 None。"""
    try:
        r = await _run_git("git", "rev-parse", "--show-toplevel", cwd=str(root_dir), check=True)
        return r.stdout.strip()
    except Exception:
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
        result = await _run_git("git", "rev-parse", "HEAD", cwd=str(root_dir) if root_dir else None)
        return result.returncode == 0
    except Exception:
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
        "git", "worktree", "add", "-b", branch, wt_path,
        cwd=str(base_dir), check=True,
    )
    return wt_path, branch


async def remove_worktree(wt_path: Path, branch: str, base_dir: Path) -> None:
    """移除 git worktree 并删除其分支(尽力而为)。"""
    try:
        await _run_git("git", "worktree", "remove", "--force", str(wt_path), cwd=str(base_dir))
    except Exception:
        pass
    try:
        await _run_git("git", "branch", "-D", branch, cwd=str(base_dir))
    except Exception:
        pass


# ── Git Stash 检查点 ──────────────────────────────────────────────────────────


async def git_create_checkpoint(root_dir: Path, message: str = "") -> bool:
    """创建检查点:git stash push + apply

    在 assistant turn 开始前调用,捕获当前工作目录状态。
    创建后立即 apply 恢复,不影响工作区内容。
    如果没有变更则静默返回 False(git stash 无变更时返回非 0)。
    message 使用用户消息的前 50 个字符,便于在 stash list 中识别。

    Args:
        root_dir: 仓库根目录路径
        message: 检查点描述,默认使用时间戳

    Returns:
        bool: 是否成功创建(无变更时返回 False)
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return False
    # 截取前 50 字符,去除换行,避免 git message 解析问题
    if message:
        msg = message.replace("\n", " ")[:50]
    else:
        msg = f"checkpoint-{time.strftime('%Y%m%d-%H%M%S')}"
    # 第一步:stash push 保存当前状态
    result = await _run_git(
        "git", "stash", "push", "--include-untracked", "-m", msg,
        cwd=str(git_root),
    )
    if result.returncode != 0:
        return False
    # 第二步:立即 apply 恢复工作区
    await _run_git("git", "stash", "apply", cwd=str(git_root))
    return True


async def _git_restore_helper(root_dir: Path, index: int, use_pop: bool) -> tuple[bool, str]:
    """git stash 恢复的内部实现,供 git_pop_checkpoint 和 git_apply_checkpoint 调用。

    处理流程:
    1. 校验当前目录是否在 git 仓库内
    2. 根据 use_pop 决定执行 `git stash pop` 或 `git stash apply`
    3. 若 stash 恢复时出现冲突(工作区有未提交的同名文件变更),
       采用强制恢复策略:
       a. `git checkout HEAD -- .` 丢弃当前工作区所有修改
       b. `git clean -fd` 删除未跟踪的文件
       c. 重新执行 stash pop/apply

    Args:
        root_dir: 仓库根目录路径,用于定位 git 仓库
        index: stash 序号,对应 `stash@{index}`,0 表示最近一次
        use_pop: True 使用 `git stash pop`(恢复并从 stash 列表中删除),
                 False 使用 `git stash apply`(恢复但保留 stash 记录)

    Returns:
        (success, message) 元组:
        - success: 操作是否成功
        - message: 成功时为操作描述,失败时为错误信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return False, "不在 git 仓库中"

    cmd = "pop" if use_pop else "apply"
    action = "恢复并删除" if use_pop else "恢复"

    # 先尝试 stash pop/apply
    result = await _run_git(
        "git", "stash", cmd, f"stash@{{{index}}}",
        cwd=str(git_root),
    )
    if result.returncode == 0:
        return True, f"已{action}检查点 stash@{{{index}}}"

    stderr = (result.stderr or "").strip()
    # 冲突时,使用 git checkout + git clean 强制恢复
    if "would be overwritten" in stderr or "already exists" in stderr or "conflict" in stderr.lower():
        # 1. 丢弃当前工作区修改
        await _run_git("git", "checkout", "HEAD", "--", ".", cwd=str(git_root))
        # 2. 删除未跟踪的文件
        await _run_git("git", "clean", "-fd", cwd=str(git_root))
        # 3. 从 stash 恢复
        result = await _run_git(
            "git", "stash", cmd, f"stash@{{{index}}}",
            cwd=str(git_root),
        )
        if result.returncode == 0:
            return True, f"已{action}检查点 stash@{{{index}}}"
        return False, f"{action}检查点失败: {(result.stderr or '').strip()}"

    return False, stderr or f"没有可{action}的检查点"


async def git_pop_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点并删除:git stash pop stash@{index}"""
    return await _git_restore_helper(root_dir, index, use_pop=True)


async def git_apply_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点但保留:git stash apply stash@{index}"""
    return await _git_restore_helper(root_dir, index, use_pop=False)


async def git_delete_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """删除检查点:git stash drop stash@{index}

    Args:
        root_dir: 仓库根目录路径
        index: 检查点序号,默认 0(最近的)

    Returns:
        (success, message) — success 是否成功,message 为结果描述或错误信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return False, "不在 git 仓库中"
    # 先获取 stash 信息用于提示
    list_result = await _run_git("git", "stash", "list", cwd=str(git_root))
    stash_lines = (list_result.stdout or "").strip().splitlines()
    if index < 0 or index >= len(stash_lines):
        return False, f"检查点序号 {index} 不存在(共 {len(stash_lines)} 个)"
    stash_name = stash_lines[index]
    # 删除
    result = await _run_git(
        "git", "stash", "drop", f"stash@{{{index}}}",
        cwd=str(git_root),
    )
    if result.returncode == 0:
        return True, f"已删除检查点: {stash_name}"
    return False, (result.stderr or "").strip() or "删除检查点失败"


async def _get_stash_untracked_tree(git_root: Path, stash_ref: str) -> str | None:
    """获取 stash 的第三个 parent tree(未跟踪文件),不存在则返回 None。"""
    result = await _run_git("git", "rev-parse", f"{stash_ref}^3", cwd=str(git_root))
    if result.returncode != 0:
        return None
    tree_result = await _run_git(
        "git", "cat-file", "-p", result.stdout.strip(), cwd=str(git_root),
    )
    if tree_result.returncode != 0:
        return None
    # 提取 tree 行的 hash
    for line in (tree_result.stdout or "").splitlines():
        if line.startswith("tree "):
            return line.split()[1]
    return None


async def _diff_stash_untracked_between(git_root: Path, index_a: int, index_b: int) -> str:
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
    # 已跟踪文件的 diff
    result = await _run_git(
        "git", "diff", f"stash@{{{index}}}",
        cwd=str(git_root),
    )
    tracked = (result.stdout or "").strip()
    # 未跟踪文件的 diff
    untracked = await _diff_stash_untracked_vs_workdir(git_root, index)
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
    # 已跟踪文件的 diff
    result = await _run_git(
        "git", "diff", f"stash@{{{index_a}}}", f"stash@{{{index_b}}}",
        cwd=str(git_root),
    )
    tracked = (result.stdout or "").strip()
    # 未跟踪文件的 diff
    untracked = await _diff_stash_untracked_between(git_root, index_a, index_b)
    parts = [p for p in [tracked, untracked] if p]
    return "\n".join(parts) if parts else "两个检查点没有差异"


async def git_list_checkpoints(root_dir: Path) -> str:
    """列出所有检查点:git stash list

    Args:
        root_dir: 仓库根目录路径

    Returns:
        str: 检查点列表文本,无检查点时返回提示信息
    """
    git_root = await get_git_root(root_dir)
    if not git_root:
        return "不在 git 仓库中"
    result = await _run_git("git", "stash", "list", cwd=str(git_root))
    return (result.stdout or "").strip() or "没有检查点"


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
        from uniclaw.provider import achat
        from uniclaw.tools.session.session import Session

        session = Session()
        session.add_user_message(
            content=f"请为以下 git diff 生成 commit message:\n\n```diff\n{diff_text}\n```"
        )

        resp = await achat(
            system_prompt,
            session,
            model_name=config.mini_model_name[0] if config.mini_model_name else "",
            enable_thinking=False,
            thinking=False,
            config=config,
            temperature=None,
            max_tokens=500,
        )
        return {"message": resp.content.strip()}
    except Exception as e:
        return {"error": f"AI 生成失败: {e}"}
