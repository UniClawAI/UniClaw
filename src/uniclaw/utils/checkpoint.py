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

import pathspec

from uniclaw.context import get_app_dir
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
            "id": str,      # 检查点唯一标识(时间戳)
            "message": str, # 检查点描述信息
            "time": str     # 创建时间(ISO 格式字符串)
        }

    文件不存在或解析失败时返回空列表,不抛异常。

    Args:
        checkpoint_dir: 检查点存储目录路径(由 _get_checkpoint_dir 返回)

    Returns:
        list[dict]: 按 time 字段倒序排列的检查点列表,索引 0 为最新
    """
    index_file = checkpoint_dir / "index.json"
    if index_file.exists():
        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
            return sorted(data, key=lambda x: x.get("time", ""), reverse=True)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_index(checkpoint_dir: Path, index: list) -> None:
    """保存检查点索引。"""
    index_file = checkpoint_dir / "index.json"
    index_file.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_gitignore(root_dir: Path) -> pathspec.PathSpec:
    """加载 .gitignore 文件并编译为 PathSpec 匹配器。

    读取工作目录下的 .gitignore,将其 gitwild 模式规则编译为
    pathspec.PathSpec 对象,供 _get_all_files 等函数用于过滤
    应被忽略的文件(非 git 仓库场景下的替代方案)。

    没有 .gitignore 时,返回默认规则:忽略所有 . 开头的文件和目录。

    Args:
        root_dir: 项目根目录路径,函数会在此目录下查找 .gitignore

    Returns:
        pathspec.PathSpec: 编译后的匹配器,可用于 match_file() 判断文件是否被忽略
    """
    gitignore = root_dir / ".gitignore"
    if gitignore.exists():
        try:
            lines = gitignore.read_text(encoding="utf-8").splitlines()
            return pathspec.GitIgnoreSpec.from_lines(lines)
        except (OSError, UnicodeDecodeError):
            pass
    # 没有 .gitignore 时,默认忽略所有 . 开头的文件和目录
    return pathspec.GitIgnoreSpec.from_lines([".*", "__pycache__/", "node_modules/"])


def _scan_files_sync(root_dir: Path) -> list[str]:
    """同步扫描目录文件列表(供 run_in_executor 调用)。"""
    gitignore_spec = _load_gitignore(root_dir)
    files = []
    for root, dirs, filenames in os.walk(root_dir):
        # 用 gitignore 规则剪枝目录,避免进入不需要遍历的目录
        rel_root = os.path.relpath(root, root_dir)
        dirs[:] = [
            d for d in dirs
            if not gitignore_spec.match_file(
                d if rel_root == "." else f"{rel_root}/{d}"
            )
        ]
        for filename in filenames:
            filepath = Path(root) / filename
            rel_path = str(filepath.relative_to(root_dir))
            if gitignore_spec.match_file(rel_path):
                continue
            files.append(rel_path)
    return files


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
        # 没有 .gitignore 时,补上默认排除规则(与 _load_gitignore 兜底一致)
        if not (root_dir / ".gitignore").exists():
            exclude_args += [
                "--exclude=.*",
                "--exclude=__pycache__/",
                "--exclude=node_modules/",
            ]
        result = await _run_git(
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            *exclude_args,
            cwd=str(git_root),
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().splitlines() if f]

    # 无 git 时扫描目录(同步 I/O,放到线程池避免阻塞事件循环)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _scan_files_sync, root_dir)


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
    """同步拷贝文件到检查点目录(供 run_in_executor 调用)。"""
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


async def _file_create_checkpoint(root_dir: Path, message: str = "") -> bool:
    """文件快照模式:复制所有文件到检查点目录。"""
    all_files = await _get_all_files(root_dir)
    if not all_files:
        return False

    checkpoint_dir = _get_checkpoint_dir(root_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    cp_id = f"cp_{time.strftime('%Y%m%d_%H%M%S')}"
    cp_path = checkpoint_dir / cp_id

    # 文件拷贝是同步 I/O,放到线程池避免阻塞事件循环
    loop = asyncio.get_running_loop()
    copied_files = await loop.run_in_executor(
        None, _copy_files_sync, root_dir, all_files, cp_path
    )

    if not copied_files:
        shutil.rmtree(cp_path, ignore_errors=True)
        return False

    # 清理代理对字符(surrogates),避免 UTF-8 写入失败
    if message:
        msg = message.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
        msg = msg.replace("\n", " ")[:50]
    else:
        msg = cp_id
    meta = {
        "id": cp_id,
        "message": msg,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": copied_files,
        "mode": "file",
    }
    (cp_path / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    index = _load_index(checkpoint_dir)
    index.append({"id": cp_id, "message": msg, "time": meta["time"]})
    _save_index(checkpoint_dir, index)

    return True


def _file_restore_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """文件快照模式:从检查点目录恢复文件。"""
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


def _get_checkpoint_meta(checkpoint_dir: Path, index: int) -> tuple[dict, Path, str]:
    """获取检查点元数据和路径。

    Args:
        checkpoint_dir: 检查点目录
        index: 倒序索引(0 = 最新)

    Returns:
        (meta, cp_path, error_msg) — 如果出错,meta 为空字典,error_msg 非空
    """
    cp_index = _load_index(checkpoint_dir)

    if index < 0 or index >= len(cp_index):
        return {}, Path(), f"检查点序号 {index} 不存在"

    cp_info = cp_index[index]
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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
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


# ── 统一接口 ──────────────────────────────────────────────────────────────────


async def create_checkpoint(root_dir: Path|None, message: str = "") -> bool:
    """创建检查点(根据配置选择模式)。"""
    # 文件模式:检查是否有变化,无差异则跳过创建
    if root_dir is None:
        return True
    if await has_git_commit(root_dir):
        return await git_create_checkpoint(root_dir, message)
    if not await _file_has_diff(root_dir, 0):
        return False
    return await _file_create_checkpoint(root_dir, message)


async def pop_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点并删除(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_pop_checkpoint(root_dir, index)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _file_pop_checkpoint, root_dir, index)


async def apply_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """恢复检查点但保留(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_apply_checkpoint(root_dir, index)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _file_apply_checkpoint, root_dir, index)


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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _file_diff_between, root_dir, index_a, index_b
    )


async def delete_checkpoint(root_dir: Path, index: int = 0) -> tuple[bool, str]:
    """删除检查点(根据配置选择模式)。"""
    if await has_git_commit(root_dir):
        return await git_delete_checkpoint(root_dir, index)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _file_delete_checkpoint, root_dir, index)
