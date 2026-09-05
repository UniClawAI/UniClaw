"""
会话笔记工具

提供当前会话的笔记管理功能。笔记随会话持久化保存,压缩时自动注入摘要。
用于记录重要信息,避免上下文压缩时丢失关键内容。
"""

from __future__ import annotations

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.tools.session.session import SessionNote
from uniclaw.utils.constants import TOOL_ERROR


@tool
async def session_note_add(
    name: str,
    description: str,
    content: str,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    在当前会话中添加一条笔记。笔记随会话持久化,压缩时自动保留摘要。

    适用场景: 记录重要决策结论、关键变量值、代码片段、API 用法等需要跨压缩保留的信息。

    Args:
        name: 笔记名称,唯一标识(如 "数据库配置"、"API 密钥格式")。
        description: 一句话摘要,压缩时会显示此描述。
        content: 笔记完整内容。

    Returns:
        str: 操作结果。
    """
    config = tool_runtime.config
    session = config.current_agent.session

    # 检查重名
    for note in session.session_notes:
        if note.name == name:
            return f"{TOOL_ERROR}: 已存在名为 '{name}' 的笔记,请使用 {session_note_update.name} 更新,或先删除再添加。"

    session.session_notes.append(SessionNote(name=name, description=description, content=content))
    return f"✅ 已添加笔记 '{name}'(当前共 {len(session.session_notes)} 条笔记)"


@tool
async def session_note_update(
    name: str,
    description: str = "",
    content: str = "",
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    更新当前会话中已有笔记的描述和/或内容。只更新提供的字段,留空的字段保持不变。

    Args:
        name: 要更新的笔记名称。
        description: 新的一句话摘要,留空则不更新。
        content: 新的完整内容,留空则不更新。

    Returns:
        str: 操作结果。
    """
    config = tool_runtime.config
    session = config.current_agent.session

    for note in session.session_notes:
        if note.name == name:
            updated_fields = []
            if description:
                note.description = description
                updated_fields.append("描述")
            if content:
                note.content = content
                updated_fields.append("内容")
            if not updated_fields:
                return f"未更新任何字段(description 和 content 均为空)。"
            return f"✅ 已更新笔记 '{name}' 的 {'、'.join(updated_fields)}"

    return f"{TOOL_ERROR}: 未找到名为 '{name}' 的笔记。使用 {session_note_list.name} 查看所有笔记。"


@tool
async def session_note_delete(
    name: str,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    删除当前会话中的一条笔记。

    Args:
        name: 要删除的笔记名称。

    Returns:
        str: 操作结果。
    """
    config = tool_runtime.config
    session = config.current_agent.session

    for i, note in enumerate(session.session_notes):
        if note.name == name:
            session.session_notes.pop(i)
            return f"✅ 已删除笔记 '{name}'(剩余 {len(session.session_notes)} 条笔记)"

    return f"{TOOL_ERROR}: 未找到名为 '{name}' 的笔记。使用 {session_note_list.name} 查看所有笔记。"


@tool
def session_note_list(
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    列出当前会话的所有笔记(仅显示名称和摘要,不显示完整内容)。

    Returns:
        str: 笔记列表。
    """
    config = tool_runtime.config
    session = config.current_agent.session

    if not session.session_notes:
        return f"当前会话没有任何笔记。使用 {session_note_add.name} 添加笔记。"

    lines = [f"📝 会话笔记(共 {len(session.session_notes)} 条):"]
    lines.append("=" * 50)
    for i, note in enumerate(session.session_notes, 1):
        lines.append(f"  [{i}] {note.name}")
        lines.append(f"      {note.description}")
    lines.append("=" * 50)
    lines.append(f"使用 {session_note_get.name}(name=\"xxx\") 查看完整内容。")
    return "\n".join(lines)


@tool
def session_note_get(
    name: str,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    查看当前会话中指定笔记的完整内容。

    Args:
        name: 笔记名称。

    Returns:
        str: 笔记的完整信息(名称、描述、内容)。
    """
    config = tool_runtime.config
    session = config.current_agent.session

    for note in session.session_notes:
        if note.name == name:
            lines = [
                f"📝 笔记：{note.name}",
                "=" * 50,
                f"描述：{note.description}",
                "-" * 50,
                "内容：",
                note.content,
                "=" * 50,
            ]
            return "\n".join(lines)

    return f"{TOOL_ERROR}: 未找到名为 '{name}' 的笔记。使用 {session_note_list.name} 查看所有笔记。"


def get_tools() -> list:
    """获取会话笔记工具列表"""
    return [
        session_note_add,
        session_note_update,
        session_note_delete,
        session_note_list,
        session_note_get,
    ]


def get_all_tools() -> list:
    """获取所有会话笔记工具(与 get_tools 相同)"""
    return get_tools()
