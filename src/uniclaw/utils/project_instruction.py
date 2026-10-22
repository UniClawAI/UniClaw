"""项目指令文件(AGENTS.md / CLAUDE.md)的解析与净化。

集中处理两个项目指令文件的优先级、读取、净化和按需注入消息格式,
供 context.py 构建 system prompt 与 multi_agent.py 按需加载使用。
"""

from pathlib import Path

# 子目录 CLAUDE.md 按需注入的消息前缀(用于识别与去重)
SUBDIR_CLAUDE_MD_TAG = "[system](子目录项目指令)"


def is_subdir_claude_md_msg(content: str) -> Path | None:
    """判断消息内容是否为按需加载的子目录 CLAUDE.md 注入。

    Args:
        content: 消息内容。

    Returns:
        Path | None: 是注入消息时返回对应的指令文件路径,否则返回 None。
    """
    if not content.startswith(SUBDIR_CLAUDE_MD_TAG):
        return None
    # 格式: "[system](子目录项目指令) 已加载 {path}\n{content}"(路径独占首行)
    line = content.split("\n", 1)[0]
    marker = "已加载 "
    idx = line.find(marker)
    if idx < 0:
        return None
    path_str = line[idx + len(marker):].rstrip()
    return Path(path_str) if path_str else None


# 项目指令文件的候选文件名,按优先级排序:AGENTS.md 优先于 CLAUDE.md
PROJECT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")


def _resolve_project_instruction(dir_path: Path) -> Path | None:
    """在目录中解析要使用的项目指令文件(AGENTS.md 优先,其次 CLAUDE.md)。

    Args:
        dir_path: 目录路径。

    Returns:
        Path | None: 存在时返回按优先级选定的指令文件路径,否则 None。
    """
    for name in PROJECT_INSTRUCTION_FILES:
        p = dir_path / name
        if p.is_file():
            return p
    return None


def _sanitize_claude_md(content: str, path: Path) -> str | None:
    """净化项目指令内容:大小限制 + 防提示词注入过滤 + 包装说明。

    Args:
        content: 原始文件内容。
        path: 指令文件路径(用于说明文字)。

    Returns:
        str | None: 净化后的包装内容;空文件/全被过滤时返回 None。
    """
    content = content.strip()
    if not content:
        return None

    # 限制文件大小(最大 10KB):按字节截断,避免从多字节字符中间切开产生乱码
    max_size = 10 * 1024
    if len(content.encode("utf-8")) > max_size:
        content = (
            content.encode("utf-8")[:max_size].decode("utf-8", errors="ignore")
            + "\n... (文件过大,已截断)"
        )

    # 防止提示词注入:移除试图覆盖先前指令、伪装系统消息的行。
    # 只收窄到真正危险的注入模式 — 例如 "ignore all previous instructions"
    # 或 "you are now ... act as ..."。AGENTS.md/CLAUDE.md 是项目指令文件,
    # 内容里出现 system:/assistant: 前缀行(如分节说明)是正常写法,不应删除。
    lines = content.split("\n")
    safe_lines = []
    for line in lines:
        stripped = line.strip().lower()
        # 试图让模型无视先前指令的行
        if (
            stripped.startswith("ignore")
            and ("previous" in stripped or "above" in stripped)
        ):
            continue
        # 伪装"你现在是 XX,扮演 XX"的系统指令
        if "you are now" in stripped and (
            "act as" in stripped or "pretend" in stripped
        ):
            continue
        # 伪装"忽略上方所有内容,只执行以下系统指令"的整块注入
        if (
            stripped.startswith("system:")
            and ("ignore" in stripped or "override" in stripped)
        ):
            continue
        safe_lines.append(line)

    safe_content = "\n".join(safe_lines)
    if not safe_content:
        return None

    # 项目指令说明
    description = (
        f"## {path.name}\n"
        f"项目指令文件(路径:{path}),"
        f"定义所在目录及其子目录的项目特定规范和约束,每次对话自动加载。\n"
        f'用户要求"记住项目规范"、"添加项目指令"时,写入此文件。\n'
        f"建议内容:代码风格、架构规范、工作流程、技术栈、禁止事项。"
    )
    return f"{description}\n\n{safe_content}"


def get_root_project_instruction(session) -> tuple[str, Path] | None:
    """加载会话根目录的项目指令文件(AGENTS.md 优先,其次 CLAUDE.md)。

    Args:
        session: 含 root_dir 的 Session。

    Returns:
        tuple[str, Path] | None: (净化后的包装内容, 指令文件路径);无文件时 None。
    """
    root_dir = session.root_dir
    if not root_dir:
        return None
    path = _resolve_project_instruction(root_dir)
    if path is None:
        return None
    content = path.read_text(encoding="utf-8").strip()
    sanitized = _sanitize_claude_md(content, path)
    return (sanitized, path) if sanitized else None


def get_claude_md(session) -> str:
    """加载项目根目录项目指令(AGENTS.md 优先,其次 CLAUDE.md),防止提示词注入"""
    resolved = get_root_project_instruction(session)
    if resolved is None:
        return ""
    sanitized, path = resolved
    return sanitized


def get_subdir_project_instruction(dir_path: Path) -> tuple[str, Path] | None:
    """读取子目录项目指令文件(AGENTS.md 优先,其次 CLAUDE.md)。

    Args:
        dir_path: 子目录路径。

    Returns:
        tuple[str, Path] | None: (净化后的包装内容, 指令文件路径);无文件时 None。
    """
    path = _resolve_project_instruction(dir_path)
    if path is None:
        return None
    content = path.read_text(encoding="utf-8").strip()
    sanitized = _sanitize_claude_md(content, path)
    return (sanitized, path) if sanitized else None


def get_subdir_claude_md(dir_path: Path) -> str | None:
    """读取子目录的项目指令(AGENTS.md 优先,其次 CLAUDE.md)并按需加载格式净化。

    Args:
        dir_path: 子目录路径。

    Returns:
        str | None: 净化后的内容;文件不存在/为空/读取失败时返回 None。
    """
    resolved = get_subdir_project_instruction(dir_path)
    return resolved[0] if resolved else None