"""检查点系统 - 支持两种模式:文件快照 和 git stash。

自动检测:有 git 用 git_stash,否则用 file。
"""

import asyncio
import difflib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Optional

from uniclaw.context import get_app_dir

_MS_FACTOR = 1000  # 秒 → 毫秒换算因子
from uniclaw.utils.git import (
    get_git_root,
    is_git_installed,
    has_git_commit,
    git_create_checkpoint,
    git_pop_checkpoint,
    git_apply_checkpoint,
    git_delete_checkpoint,
    git_list_checkpoints,
    git_diff_checkpoint,
    git_diff_current,
    git_diff_between,
)

# ── 通用函数 ──────────────────────────────────────────────────────────────────


def _get_checkpoint_dir(root_dir: Path) -> Path:
    """获取检查点存储目录。"""

    return get_app_dir(root_dir) / "checkpoints"


def _load_index(checkpoint_dir: Path) -> list:
    """加载检查点索引文件,返回按时间倒序排列的检查点列表。

    索引文件路径为 `<checkpoint_dir>/index.json`,每个条目结构:
        {
            "id": str,          # 检查点唯一标识(时间戳)
            "message": str,     # 检查点描述信息
            "time": str         # 创建时间("%Y-%m-%d %H:%M:%S" 字符串)
            "created_at": int   # 整数毫秒(新数据),旧数据可能缺失
        }

    排序:主键 created_at(整数毫秒),缺失时回退解析 time 字符串。
    这让同一秒内的多个 checkpoint 按创建先后(新→旧)排列 —
    仅按秒级 time 字符串倒序会保持文件插入序,同秒内旧的反而排前。

    文件不存在或解析失败时返回空列表,不抛异常。

    Args:
        checkpoint_dir: 检查点存储目录路径(由 _get_checkpoint_dir 返回)

    Returns:
        list[dict]: 按创建时间倒序排列的检查点列表,索引 0 为最新
    """
    index_file = checkpoint_dir / "index.json"
    if index_file.exists():
        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        # 统一换算为毫秒排序键:created_at 优先,缺失回退 time 字符串解析
        def _sort_ms(e: dict) -> int:
            ca = e.get("created_at")
            if ca is not None:
                return _norm_ms(int(ca))
            try:
                t = time.mktime(time.strptime(e.get("time", ""), "%Y-%m-%d %H:%M:%S"))
            except (TypeError, ValueError):
                return 0
            return int(t * _MS_FACTOR)

        return sorted(data, key=_sort_ms, reverse=True)
    return []


def _save_index(checkpoint_dir: Path, index: list) -> None:
    """保存检查点索引。"""
    index_file = checkpoint_dir / "index.json"
    index_file.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _scan_files_sync(root_dir: Path) -> list[str]:
    """同步扫描目录文件列表(供 asyncio.to_thread 调用)。

    使用 get_not_ignored_files 批量过滤文件。
    """
    from uniclaw.utils.gitignore import get_not_ignored_files

    # 收集所有文件
    all_files: list[Path] = []
    for root, dirs, filenames in os.walk(root_dir):
        root_path = Path(root)
        for filename in filenames:
            all_files.append(root_path / filename)

    # 批量获取未被 .gitignore 忽略的文件
    not_ignored = get_not_ignored_files(all_files)

    # 返回相对路径列表
    return [str(f.relative_to(root_dir)) for f in not_ignored]


async def _get_all_files(root_dir: Path) -> list[str]:
    """获取工作目录下文件列表(相对路径)。

    有 git 时使用 git ls-files,否则扫描目录并应用 .gitignore。
    """
    from uniclaw.utils.git import _run_git

    # 有 git 时使用 git
    git_root = await get_git_root(root_dir)
    # 没有 git 仓库但安装了 git,自动 init
    if not git_root and await is_git_installed():
        await _run_git("git", "init", cwd=str(root_dir))
        git_root = await get_git_root(root_dir)
    if git_root:
        app_dir_name = get_app_dir(root_dir).name  # ".UniClaw"
        # 基础排除:应用数据目录
        exclude_args = [f"--exclude={app_dir_name}/"]
        # 没有 .gitignore 时,补上默认排除规则(与 load_gitignore_spec 兜底一致)
        if not (root_dir / ".gitignore").exists():
            exclude_args += [
                "--exclude=.*",
                "--exclude=__pycache__/",
                "--exclude=node_modules/",
            ]
        result = await _run_git(
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            *exclude_args,
            # 在 root_dir 下执行:输出相对 root_dir,与 _copy_files_sync 的拼接基准一致
            cwd=str(root_dir),
        )
        if result.returncode == 0:
            # -z 用 NUL 分隔且按原始字节输出路径,不做 core.quotepath 转义
            # (默认转义会把中文文件名变成 "\xxx" 字面量,导致快照静默漏拷贝)
            return [f for f in result.stdout.split("\0") if f]

    # 无 git 时扫描目录(同步 I/O,放到线程池避免阻塞事件循环)
    return await asyncio.to_thread(_scan_files_sync, root_dir)


def _read_file_content(filepath: Path) -> Optional[str]:
    """安全读取文件内容。"""
    try:
        return filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _generate_diff(
    old_content: str, new_content: str, old_label: str, new_label: str
) -> str:
    """生成 unified diff。"""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines, fromfile=old_label, tofile=new_label
    )
    return "".join(diff)


# ── 文件快照模式 ──────────────────────────────────────────────────────────────


def _copy_files_sync(root_dir: Path, all_files: list[str], cp_path: Path) -> list[str]:
    """同步拷贝文件到检查点目录(供 asyncio.to_thread 调用)。"""
    files_path = cp_path / "files"
    files_path.mkdir(parents=True, exist_ok=True)
    copied_files = []
    for rel_path in all_files:
        src = root_dir / rel_path
        dst = files_path / rel_path
        if src.exists() and src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_files.append(rel_path)
    return copied_files


async def _file_create_checkpoint(
    root_dir: Path, message: str = "", created_at: int | None = None
) -> bool:
    """文件快照模式:复制所有文件到检查点目录。

    created_at: 本次 turn 用户消息的入库时间戳(整数毫秒,与消息 created_at
    同源,回退精确匹配);为 None 时用当前创建时刻。
    """
    all_files = await _get_all_files(root_dir)
    if not all_files:
        return False

    checkpoint_dir = _get_checkpoint_dir(root_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    # cp_id 用可读时间格式(%Y%m%d_%H%M%S_毫秒),避免同秒内多个
    # checkpoint 的目录相互覆盖,且列表里一眼可读
    _now = time.time()
    cp_id = (
        f"cp_{time.strftime('%Y%m%d_%H%M%S', time.localtime(_now))}"
        f"_{int((_now % 1) * 1000):03d}"
    )
    cp_path = checkpoint_dir / cp_id

    # 文件拷贝是同步 I/O,放到线程池避免阻塞事件循环
    copied_files = await asyncio.to_thread(_copy_files_sync, root_dir, all_files, cp_path)

    if not copied_files:
        shutil.rmtree(cp_path, ignore_errors=True)
        return False

    # 清理代理对字符(surrogates),避免 UTF-8 写入失败
    if message:
        msg = message.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
        msg = msg.replace("\n", " ")[:50]
    else:
        msg = cp_id
    # 关联时间戳统一为整数毫秒:优先用传入的 turn 消息入库时刻(与消息
    # created_at 同源,整数直等匹配);缺省用当前创建时刻
    now_ts = int(time.time() * 1000)
    created = created_at if created_at is not None else now_ts
    meta = {
        "id": cp_id,
        "message": msg,
        "time": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(now_ts / 1000)
        ),
        "created_at": created,  # 整数毫秒,供"删除并回退"精确匹配
        "files": copied_files,
        "mode": "file",
    }
    (cp_path / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    index = _load_index(checkpoint_dir)
    index.append({"id": cp_id, "message": msg, "time": meta["time"], "created_at": created})
    _save_index(checkpoint_dir, index)

    return True


async def _file_create_anchor(
    root_dir: Path, message: str = "", created_at: int | None = None
) -> bool:
    """文件快照模式:工作区无改动时记录一条时间锚点(轻量,不复制文件)。

    该 turn 未修改文件 → 工作区状态与最近真实快照一致,回退该消息时刻时
    定位到最近真实快照即可。锚点只入 index.json(标记 anchor=true),
    保证 `find_checkpoint_index` 按 created_at 能命中该消息的时刻。
    """
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    if message:
        msg = message.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
        msg = msg.replace("\n", " ")[:50]
    else:
        msg = "未修改文件"
    now_ts = int(time.time() * 1000)
    created = created_at if created_at is not None else now_ts

    index = _load_index(checkpoint_dir)
    index.append(
        {
            "id": f"anchor_{now_ts}",
            "message": msg,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts / 1000)),
            "created_at": created,
            "anchor": True,
        }
    )
    _save_index(checkpoint_dir, index)
    return True


def _file_restore_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """文件快照模式:从检查点目录恢复文件。

    锚点条目(anchor=true)本身无文件快照,恢复时向前定位最近的真实快照,
    与锚点时刻的工作区状态一致。若向前没有真实快照(全是锚点或空),
    视为无需恢复(锚点前的文件状态即当前要回退到的状态)。
    """
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    if not checkpoint_dir.exists():
        return False, "没有检查点"

    cp_index = _load_index(checkpoint_dir)
    if not cp_index:
        return False, "没有检查点"

    if index < 0 or index >= len(cp_index):
        return False, f"检查点序号 {index} 不存在(共 {len(cp_index)} 个)"

    # 锚点:向前找最近的真实快照
    if cp_index[index].get("anchor") is True:
        for j in range(index, len(cp_index)):
            if cp_index[j].get("anchor") is not True:
                index = j
                break
        else:
            return True, "该时刻未修改文件,无需恢复(已是最初状态)"
        # 继续按 index 指向的真实快照恢复

    cp_info = cp_index[index]
    cp_path = checkpoint_dir / cp_info["id"]

    if not cp_path.exists():
        return False, f"检查点目录不存在: {cp_info['id']}"

    meta_file = cp_path / "meta.json"
    if not meta_file.exists():
        return False, f"检查点元数据不存在: {cp_info['id']}"

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"读取检查点元数据失败: {e}"

    files_path = cp_path / "files"
    restored = []
    for rel_path in meta.get("files", []):
        src = files_path / rel_path
        dst = root_dir / rel_path
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            restored.append(rel_path)

    return True, f"已恢复检查点 {cp_info['id']},恢复了 {len(restored)} 个文件"


def _file_pop_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """文件快照模式:恢复检查点并删除。"""
    success, msg = _file_restore_checkpoint(root_dir, index)
    if success:
        # 恢复成功后删除检查点
        _file_delete_checkpoint(root_dir, index)
    return success, msg


def _file_apply_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """文件快照模式:恢复检查点但保留。"""
    return _file_restore_checkpoint(root_dir, index)


def _file_delete_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """文件快照模式:删除检查点。"""
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    if not checkpoint_dir.exists():
        return False, "没有检查点"

    cp_index = _load_index(checkpoint_dir)
    if not cp_index:
        return False, "没有检查点"

    if index < 0 or index >= len(cp_index):
        return False, f"检查点序号 {index} 不存在(共 {len(cp_index)} 个)"

    cp_info = cp_index[index]
    cp_path = checkpoint_dir / cp_info["id"]

    # 删除检查点目录
    if cp_path.exists():
        shutil.rmtree(cp_path)

    # 从索引中移除
    cp_index.pop(index)
    _save_index(checkpoint_dir, cp_index)

    return True, f"已删除检查点: {cp_info['id']}"


def _file_list_checkpoints(root_dir: Path) -> str:
    """文件快照模式:列出所有检查点。"""
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    if not checkpoint_dir.exists():
        return "没有检查点"

    cp_index = _load_index(checkpoint_dir)
    if not cp_index:
        return "没有检查点"

    lines = []
    for i, cp in enumerate(cp_index):
        lines.append(f"[{i}] {cp['id']} - {cp['message']} ({cp['time']})")

    return "\n".join(lines)


def _file_diff_two_dirs(
    files_a: list[str],
    files_b: list[str],
    dir_a: Path,
    dir_b: Path,
    label_a: str,
    label_b: str,
    empty_msg: str,
) -> str:
    """文件快照模式:比较两个目录的文件差异。

    Args:
        files_a: 第一个目录的文件列表
        files_b: 第二个目录的文件列表
        dir_a: 第一个目录路径
        dir_b: 第二个目录路径
        label_a: 第一个目录的标签
        label_b: 第二个目录的标签
        empty_msg: 无差异时的提示信息

    Returns:
        str: diff 文本
    """
    all_files = sorted(set(files_a) | set(files_b))
    diffs = []

    for rel_path in all_files:
        content_a = _read_file_content(dir_a / rel_path)
        content_b = _read_file_content(dir_b / rel_path)

        if content_a is None and content_b is None:
            continue

        if content_a is None:
            diffs.append(f"+++ 仅在 {label_b}: {rel_path}\n{content_b}")
        elif content_b is None:
            diffs.append(f"+++ 仅在 {label_a}: {rel_path}\n{content_a}")
        else:
            diff = _generate_diff(
                content_a, content_b, f"{label_a}:{rel_path}", f"{label_b}:{rel_path}"
            )
            if diff:
                diffs.append(diff)

    return "\n".join(diffs) if diffs else empty_msg


def _resolve_real_index(checkpoint_dir: Path, index: int) -> int:
    """锚点条目解析:index 指向 anchor=true 时,向前找最近真实快照的序号。

    锚点(未修改文件的 turn)自身无文件快照,其"文件状态"等于之前最近的
    真实快照。返回真实快照序号;没有真实快照(全锚点/空)返回 -1。
    """
    cp_index = _load_index(checkpoint_dir)
    if index < 0 or index >= len(cp_index):
        return -1
    if cp_index[index].get("anchor") is not True:
        return index
    for j in range(index, len(cp_index)):
        if cp_index[j].get("anchor") is not True:
            return j
    return -1


def _get_checkpoint_meta(checkpoint_dir: Path, index: int) -> tuple[dict, Path, str]:
    """获取检查点元数据和路径。

    锚点条目(anchor=true)自动解析到最近的真实快照,命令行 diff/比较
    看到的是该时刻对应的文件状态。

    Args:
        checkpoint_dir: 检查点目录
        index: 倒序索引(0 = 最新)

    Returns:
        (meta, cp_path, error_msg) — 如果出错,meta 为空字典,error_msg 非空
    """
    real = _resolve_real_index(checkpoint_dir, index)
    if real < 0:
        return {}, Path(), "检查点序号不存在或均为锚点"

    cp_index = _load_index(checkpoint_dir)
    cp_info = cp_index[real]
    cp_path = checkpoint_dir / cp_info["id"]
    meta_file = cp_path / "meta.json"

    if not meta_file.exists():
        return {}, Path(), "检查点元数据不存在"

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, Path(), "读取检查点元数据失败"

    return meta, cp_path, ""


async def _file_diff_checkpoint(root_dir: Path, index: int = 0) -> str:
    """文件快照模式:查看检查点与当前文件的差异。"""
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    if not checkpoint_dir.exists():
        return "没有检查点"

    meta, cp_path, err = _get_checkpoint_meta(checkpoint_dir, index)
    if err:
        return err

    current_files = await _get_all_files(root_dir)
    return await asyncio.to_thread(
        _file_diff_two_dirs,
        meta.get("files", []),
        current_files,
        cp_path / "files",
        root_dir,
        "checkpoint",
        "current",
        "检查点与当前文件没有差异",
    )


async def _file_has_diff(root_dir: Path, index: int = 0) -> bool:
    """文件快照模式:检查最新检查点与当前文件是否有差异。"""
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    if not checkpoint_dir.exists():
        return True  # 没有检查点,需要创建

    meta, cp_path, err = _get_checkpoint_meta(checkpoint_dir, index)
    if err:
        return True  # 获取失败,保守返回有差异

    current_files = await _get_all_files(root_dir)
    return await asyncio.to_thread(
        _file_has_diff_sync,
        meta.get("files", []),
        current_files,
        cp_path / "files",
        root_dir,
    )


def _file_has_diff_sync(
    files_a: list[str],
    files_b: list[str],
    dir_a: Path,
    dir_b: Path,
) -> bool:
    """同步检查两个目录是否有差异。"""
    if set(files_a) != set(files_b):
        return True  # 文件列表不同

    for rel_path in files_a:
        content_a = _read_file_content(dir_a / rel_path)
        content_b = _read_file_content(dir_b / rel_path)
        if content_a != content_b:
            return True  # 文件内容不同

    return False


async def _file_diff_current(root_dir: Path) -> str:
    """文件快照模式:查看当前未提交的变更。"""
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    if checkpoint_dir.exists():
        cp_index = _load_index(checkpoint_dir)
        if cp_index:
            return await _file_diff_checkpoint(root_dir, 0)

    return "当前没有检查点可供对比"


def _file_diff_between(root_dir: Path, index_a: int, index_b: int) -> str:
    """文件快照模式:比较两个检查点的差异。"""
    checkpoint_dir = _get_checkpoint_dir(root_dir)
    if not checkpoint_dir.exists():
        return "没有检查点"

    meta_a, cp_a, err_a = _get_checkpoint_meta(checkpoint_dir, index_a)
    if err_a:
        return err_a

    meta_b, cp_b, err_b = _get_checkpoint_meta(checkpoint_dir, index_b)
    if err_b:
        return err_b

    return _file_diff_two_dirs(
        files_a=meta_a.get("files", []),
        files_b=meta_b.get("files", []),
        dir_a=cp_a / "files",
        dir_b=cp_b / "files",
        label_a=f"checkpoint[{index_a}]",
        label_b=f"checkpoint[{index_b}]",
        empty_msg="两个检查点没有差异",
    )


def _norm_ms(value: int) -> int:
    """将 created_at 归一化为毫秒:旧数据可能是秒级(< 1e12),乘 1000 对齐。

    新数据一律整数毫秒(约 1.7e12);旧 checkpoint 的 created_at 是秒级
    (约 1.7e9),统一为毫秒基准后参与比较。
    """
    return value * _MS_FACTOR if value < 1_000_000_000_000 else value


def _find_exact(items: list[dict], timestamp: int) -> Optional[int]:
    """精确匹配:created_at == timestamp(整数毫秒直等;旧秒级数据自动对齐)。"""
    for i in range(len(items) - 1, -1, -1):
        ca = items[i].get("created_at")
        if ca is None:
            continue
        if _norm_ms(int(ca)) == timestamp:
            return i
    return None


# ── 统一接口 ──────────────────────────────────────────────────────────────────


async def find_checkpoint_index(root_dir: Path, timestamp: int) -> Optional[int]:
    """按消息入库时刻定位 checkpoint 序号(0 = 最新,按时间倒序)。

    语义:消息与 checkpoint 共享同一整数毫秒 turn_ts(create 与入库同一来源)。
    给定被删消息的 created_at(整数毫秒),只做精确匹配(整数直等) —
    找不到说明该消息时刻没有对应 checkpoint(如该 turn 工作区无差异跳过
    创建,或数据时间错位),返回 None 由调用方明确提示,不做模糊兜底。

    Args:
        root_dir: 工作目录
        timestamp: 被删消息的入库时间戳(整数毫秒)

    Returns:
        int: checkpoint 序号,找不到返回 None
    """
    if await has_git_commit(root_dir):
        from uniclaw.utils.git import _merged_checkpoint_items, get_git_root

        git_root = await get_git_root(root_dir)
        if not git_root:
            return None
        items = await _merged_checkpoint_items(git_root)
        return _find_exact(items, timestamp)

    checkpoint_dir = _get_checkpoint_dir(root_dir)
    index = _load_index(checkpoint_dir)  # 已按 time 倒序(新→旧)
    return _find_exact(index, timestamp)


async def create_checkpoint(
    root_dir: Path | None,
    message: str = "",
    created_at: int | None = None,
) -> bool:
    """创建检查点(根据配置选择模式)。

    有改动:文件模式复制文件快照 / git 模式 stash push;
    无改动:文件模式记录**时间锚点**(无真实快照,恢复时定位到最近快照),
    git 模式记 HEAD 锚点 — 保证每个 turn 都有 checkpoint 可回退,
    即使该 turn 没有修改文件。

    Args:
        root_dir: 工作目录
        message: 检查点描述(缺省用时间戳)
        created_at: 本次 turn 用户消息的入库时间戳(整数毫秒)。
            与消息 created_at 同源,保证回退整数直等匹配;为 None 时内部用创建时刻。
    """
    if root_dir is None:
        return True
    if await has_git_commit(root_dir):
        return await git_create_checkpoint(root_dir, message, created_at)
    if not await _file_has_diff(root_dir, 0):
        # 无文件差异:落一条时间锚点,保证该消息时刻可回退
        return await _file_create_anchor(root_dir, message, created_at)
    return await _file_create_checkpoint(root_dir, message, created_at)


async def pop_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点并删除(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_pop_checkpoint(root_dir, index)
    return await asyncio.to_thread(_file_pop_checkpoint, root_dir, index)


async def apply_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点但保留(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_apply_checkpoint(root_dir, index)
    return await asyncio.to_thread(_file_apply_checkpoint, root_dir, index)


async def list_checkpoints(root_dir: Path) -> str:
    """列出所有检查点(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_list_checkpoints(root_dir)
    return _file_list_checkpoints(root_dir)


async def diff_checkpoint(root_dir: Path, index: int = 0) -> str:
    """查看检查点的变更内容(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_diff_checkpoint(root_dir, index)
    return await _file_diff_checkpoint(root_dir, index)


async def diff_current(root_dir: Path) -> str:
    """查看当前未提交的变更(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_diff_current(root_dir)
    return await _file_diff_current(root_dir)


async def diff_between(root_dir: Path, index_a: int, index_b: int) -> str:
    """比较两个检查点的差异(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_diff_between(root_dir, index_a, index_b)
    return await asyncio.to_thread(_file_diff_between, root_dir, index_a, index_b)


async def delete_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """删除检查点(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_delete_checkpoint(root_dir, index)
    return await asyncio.to_thread(_file_delete_checkpoint, root_dir, index)
