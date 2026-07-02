"""
会话管理工具

提供 AI 可直接调用的会话管理功能,包括查看会话列表、查看会话详情、删除会话等。
"""

from __future__ import annotations

from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.tools.session.session_manager import SessionManager
from uniclaw.console.ui import ok, err
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


@tool
def session_list(
    limit: int = 20,
) -> str:
    """
    列出保存的会话历史,显示会话ID、标题、时间和消息数量等信息。

    Args:
        task_id: 可选,按任务ID筛选会话
        limit: 返回的最大会话数量,默认20

    Returns:
        str: 格式化的会话列表信息

    Examples:
        # 列出最近20个会话
        session_list()

        # 列出最近50个会话
        session_list(limit=50)
    """
    sessions = SessionManager.list_sessions(limit=limit)

    if not sessions:
        return "没有找到任何会话历史"

    lines = []
    lines.append(f"📋 会话列表(共 {len(sessions)} 个):")
    lines.append("=" * 60)

    for idx, s in enumerate(sessions, 1):
        session_id = s.get("session_id", "unknown")
        title = s.get("title", "无标题")
        message_count = s.get("message_count", 0)
        start_time = s.get("start_time", "")
        end_time = s.get("end_time", "")

        lines.append(f"\n[{idx}] {title}")
        lines.append(f"    会话ID: {session_id}")
        lines.append(f"    消息数: {message_count}")
        if start_time:
            lines.append(f"    开始时间: {start_time}")
        if end_time:
            lines.append(f"    结束时间: {end_time}")

    lines.append("\n" + "=" * 60)
    lines.append("💡 使用 session_detail 查看详情,使用 session_delete 删除会话")

    return "\n".join(lines)


@tool
def session_detail(
    session_id: str,
) -> str:
    """
    查看指定会话的详细信息,包括完整的会话历史。

    Args:
        session_id: 会话ID(从 session_list 获取)

    Returns:
        str: 会话的详细信息

    Examples:
        # 查看指定会话详情
        session_detail(session_id="20260520_105455_a3f2b8c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c")
    """

    session = SessionManager.load_session(session_id)

    if not session:
        return f"{TOOL_ERROR}: 未找到会话ID为 '{session_id}' 的会话"

    messages = session.to_openai_messages()

    lines = []
    lines.append(f"📝 会话详情:{session.title or '无标题'}")
    lines.append("=" * 60)

    # 基本信息
    lines.append(f"会话ID: {session_id}")
    lines.append(f"消息数量: {len(messages)}")
    if session.start_time:
        lines.append(f"开始时间: {session.start_time}")

    # Token统计
    tokens = session.estimate_tokens()
    lines.append(f"\nToken统计:")
    lines.append(f"  估算Token: {tokens}")

    # 会话历史
    if messages:
        lines.append("\n" + "=" * 60)
        lines.append("会话历史:")
        lines.append("=" * 60)
        lines.append(session.to_str())

    return "\n".join(lines)


@tool
async def session_delete(
    session_id: str,
    config: AppConfig = None,
) -> str:
    """
    删除指定的会话历史。

    ⚠️ 警告:此操作不可恢复,请谨慎使用。

    Args:
        session_id: 要删除的会话ID(从 session_list 获取)

    Returns:
        str: 删除操作结果

    Examples:
        # 删除指定会话
        session_delete(session_id="20260520_105455_a3f2b8c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c")
    """

    # 先确认会话存在
    session = SessionManager.load_session(session_id)
    if not session:
        return f"{TOOL_ERROR}: 未找到会话ID为 '{session_id}' 的会话"

    title = session.title or "无标题"

    # 执行删除
    success = SessionManager.delete_session(session_id)

    if success:
        await ok(f"✓ 已删除会话: {title}", config)
        # WebUI 模式:通知前端会话已删除
        if config and config.is_webui:
            try:
                from uniclaw.webui.ws import notify_session_deleted
                await notify_session_deleted(session_id, config.root_dir)
            except Exception:
                pass
        return f"✅ 成功删除会话 '{title}'\n会话ID: {session_id}"
    else:
        await err(f"✗ 删除会话失败: {title}", config)
        return f"{TOOL_ERROR}: 删除会话失败: {title}\n会话ID: {session_id}"


@tool
async def session_update_title(
    session_id: str,
    title: str,
    config: AppConfig = None,
) -> str:
    """
    更新指定会话的标题。

    Args:
        session_id: 会话ID(从 session_list 获取)
        title: 新的标题

    Returns:
        str: 更新操作结果

    Examples:
        # 更新会话标题
        session_update_title(
            session_id="20260520_105455_a3f2b8c1-4d5e-6f7a-8b9c-0d1e2f3a4b5c",
            title="新的会话标题"
        )
    """

    # 先确认会话存在
    session = SessionManager.load_session(session_id)
    if not session:
        return f"{TOOL_ERROR}: 未找到会话ID为 '{session_id}' 的会话"

    old_title = session.title or "无标题"

    # 执行更新
    success = SessionManager.update_title(session_id, title)

    if success:
        await ok(f"✓ 已更新会话标题: {old_title} -> {title}", config)
        return f"✅ 成功更新会话标题\n旧标题: {old_title}\n新标题: {title}\n会话ID: {session_id}"
    else:
        await err(f"✗ 更新会话标题失败: {session_id}", config)
        return f"{TOOL_ERROR}: 更新会话标题失败\n会话ID: {session_id}"


def get_tools() -> list:
    """获取会话管理工具列表"""
    return [
        session_list,
        session_detail,
        session_delete,
        session_update_title,
    ]


def get_all_tools() -> list:
    """获取所有会话管理工具(与 get_tools 相同)"""
    return get_tools()
