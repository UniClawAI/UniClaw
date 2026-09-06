"""gitignore 规则匹配 — 判断文件是否被 .gitignore 忽略。

供 rag loader、checkpoint 等需要按 .gitignore 过滤文件的模块使用。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pathspec

# 无 .gitignore 时的兜底规则:隐藏文件 + 常见依赖/构建目录
DEFAULT_IGNORE_PATTERNS = [
    ".*",
    "__pycache__/",
    "node_modules/",
]

# 兜底规则的编译结果,模块内复用
_DEFAULT_SPEC = pathspec.GitIgnoreSpec.from_lines(DEFAULT_IGNORE_PATTERNS)


def load_gitignore_spec(
    gitignore_path: Path | str | None,
    default_fallback: bool = True,
) -> pathspec.PathSpec | None:
    """加载指定 .gitignore 文件并编译为 GitIgnoreSpec。

    接受任意位置的 .gitignore 文件路径,不限定于某个目录。
    多个规则文件可分别编译后用 spec1 + spec2 合并。

    Args:
        gitignore_path: .gitignore 文件路径。为 None、文件不存在或
            读取失败时,返回 default_fallback 决定的兜底值。
        default_fallback: 兜底行为。True(默认)返回 DEFAULT_IGNORE_PATTERNS
            编译的规则;False 返回 None,表示不产生规则。

    Returns:
        pathspec.PathSpec | None: 编译后的匹配器;default_fallback=False
            且无有效规则文件时为 None。
    """
    if gitignore_path is not None:
        gitignore = Path(gitignore_path)
        if gitignore.exists():
            try:
                lines = gitignore.read_text(encoding="utf-8").splitlines()
                return pathspec.GitIgnoreSpec.from_lines(lines)
            except (OSError, UnicodeDecodeError):
                pass
    return _DEFAULT_SPEC if default_fallback else None


def is_ignored_by_gitignore(
    paths: Sequence[Path | str],
    spec: pathspec.PathSpec | None = None,
) -> list[Path]:
    """批量判断文件是否被忽略,返回被忽略的文件列表。

    对每个文件,从其所在目录开始逐级向上查找 .gitignore;找到的第一层
    规则文件决定结果,更上层的规则不再参与(规则相对其所在目录解释,
    与 git 的"就近覆盖"语义一致)。所有层级都没有 .gitignore 时应用
    spec — 为 None 时使用 DEFAULT_IGNORE_PATTERNS 兜底(隐藏文件 +
    __pycache__/node_modules)。

    同一次调用内按目录缓存规则文件,适合批量判断。
    传入的路径应已规范化(不含 .. 或符号链接),相对路径按进程
    工作目录解释。

    Args:
        paths: 待判断的文件/目录路径列表
        spec: 全局兜底规则,仅当所有层级都没有 .gitignore 时应用。
            传入会替换默认兜底规则。

    Returns:
        list[Path]: paths 中被忽略的文件(保持输入顺序)
    """
    if spec is None:
        spec = _DEFAULT_SPEC

    # 1) 预加载所有相关目录的 .gitignore(批量优化)
    # 收集所有唯一目录及其祖先目录
    all_dirs: set[Path] = set()
    path_list = [Path(p) if not isinstance(p, Path) else p for p in paths]

    for path in path_list:
        directory = path.parent
        while True:
            if directory in all_dirs:
                break  # 已经收集过这个目录及其祖先
            all_dirs.add(directory)
            if directory == directory.parent:
                break
            directory = directory.parent

    # 批量加载所有目录的 .gitignore
    layer_cache: dict[Path, pathspec.PathSpec | None] = {}
    for directory in all_dirs:
        layer_cache[directory] = _load_dir_gitignore(directory)

    # 2) 批量判断每个文件
    ignored: list[Path] = []
    for path in path_list:
        matched = False

        # 逐级向上找第一个含 .gitignore 的目录,用它判断
        directory = path.parent
        while True:
            layer = layer_cache.get(directory)
            if layer is not None:
                matched = _match_relative(layer, path, directory)
                break
            if directory == directory.parent:
                break
            directory = directory.parent

        # 所有层级都没有 .gitignore → 全局兜底规则,按路径分量逐级检查
        if not matched:
            parts = path.parts[1:] if path.is_absolute() else path.parts
            matched = _match_parts(spec, parts)

        if matched:
            ignored.append(path)
    return ignored


def _match_relative(spec: pathspec.PathSpec, path: Path, base: Path) -> bool:
    """检查 path 是否命中 spec(相对 base 解释,含各级前缀)。

    Args:
        spec: 编译后的忽略规则
        path: 待判断的文件路径
        base: 相对路径基准目录(通常为 .gitignore 所在目录)

    Returns:
        bool: 命中返回 True
    """
    try:
        rel_parts = path.relative_to(base).parts
    except ValueError:
        # path 不在 base 之下(如跨盘符),无法用该层规则判断
        return False
    return _match_parts(spec, rel_parts)


def _match_parts(spec: pathspec.PathSpec, parts: tuple[str, ...]) -> bool:
    """将路径分量逐级转为 gitignore 风格前缀并检查。

    目录级前缀带尾斜杠,使 "build/" 这类仅匹配目录的规则能命中
    目录及其下文件;最后一级为文件本身,不带尾斜杠。

    Args:
        spec: 编译后的忽略规则
        parts: 路径分量元组

    Returns:
        bool: 任一级前缀命中返回 True
    """
    accum = ""
    for i, part in enumerate(parts):
        accum = part if not accum else f"{accum}/{part}"
        candidate = accum if i == len(parts) - 1 else accum + "/"
        if spec.match_file(candidate):
            return True
    return False


def _load_dir_gitignore(directory: Path) -> pathspec.PathSpec | None:
    """加载单个目录下的 .gitignore。

    Args:
        directory: 目录路径

    Returns:
        pathspec.PathSpec | None: 编译后的规则;目录下没有 .gitignore
            或读取失败时返回 None(该目录不产生规则,由全局 spec 兜底)
    """
    return load_gitignore_spec(directory / ".gitignore", default_fallback=False)


def get_not_ignored_files(
    paths: Sequence[Path | str],
    spec: pathspec.PathSpec | None = None,
) -> list[Path]:
    """批量判断文件是否被忽略,返回没被忽略的文件列表。

    与 is_ignored_by_gitignore 相反,返回未被 .gitignore 忽略的文件。

    Args:
        paths: 待判断的文件/目录路径列表
        spec: 全局兜底规则,仅当所有层级都没有 .gitignore 时应用。
            传入会替换默认兜底规则。

    Returns:
        list[Path]: paths 中未被忽略的文件(保持输入顺序)
    """
    # 先统一转换为 Path 对象,避免重复转换
    path_list = [p if isinstance(p, Path) else Path(p) for p in paths]
    ignored = set(is_ignored_by_gitignore(path_list, spec))
    return [p for p in path_list if p not in ignored]
