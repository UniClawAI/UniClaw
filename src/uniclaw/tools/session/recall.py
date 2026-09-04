"""
历史消息检索工具

提供对 Session.history 中被压缩移出当前上下文的旧消息的搜索和检索能力。
"""

from __future__ import annotations

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.tools.session.session import (
    Session,
    SUMMARY_PREFIX,
    UserMessage,
    AIMessage,
    ToolCallMessage,
)
from uniclaw.utils.message import MessageRole


def _count_recent_messages(session: Session) -> int:
    """计算 _messages 中非摘要消息的条数(即保留的最近消息)。

    优先按 _compact_count 计算:摘要对(用户摘要 + 助手确认)固定占头部,
    不应计入最近消息。仅当头部不是标准摘要格式时回退到前缀扫描
    (兼容旧格式或摘要头被删除的会话)。
    """
    compact_count = session._compact_count
    if compact_count > 0:
        head = session._messages[:compact_count]
        if any(
            isinstance(m, UserMessage)
            and isinstance(m.content, str)
            and m.content.startswith(SUMMARY_PREFIX)
            for m in head
        ):
            return max(0, len(session._messages) - compact_count)
    count = 0
    for msg in reversed(session._messages):
        if (
            isinstance(msg, UserMessage)
            and isinstance(msg.content, str)
            and msg.content.startswith(SUMMARY_PREFIX)
        ):
            break
        count += 1
    return count


def _get_archived_messages(session: Session) -> list[tuple[int, object]]:
    """获取 history 中已被压缩移出当前上下文的消息。

    compact() 后 _messages = [摘要消息...] + [最近消息...],
    其中最近消息与 history 末尾重叠,其余即为已归档消息。

    Returns:
        list of (original_index_in_history, message)
    """
    history = session.history
    current = session._messages
    if not history or not current:
        return []

    # history 末尾 recent_count 条是当前上下文中的消息,前面的是已归档的
    recent_count = _count_recent_messages(session)
    archived_end = max(0, len(history) - recent_count)
    return [(i, history[i]) for i in range(archived_end)]


def _format_message(idx: int, msg) -> str:
    """格式化单条消息,带序号。"""
    if isinstance(msg, UserMessage):
        role = "用户"
        content = msg.to_content()
    elif isinstance(msg, AIMessage):
        role = "助手"
        content = msg.to_content()
    elif isinstance(msg, ToolCallMessage):
        role = "工具"
        content = msg.to_content()
        if msg.name:
            role = f"工具:{msg.name}"
    else:
        role = "未知"
        content = str(msg)

    # 截断过长内容
    if len(content) > 5000:
        content = content[:5000] + f"... (共{len(content)}字符)"

    return f"#{idx} [{role}]: {content}"


@tool
def recall_history(
    keywords: list[str],
    context_size: int = 3,
    max_results: int = 10,
    tool_runtime: ToolRuntime = None,
) -> str:
    """搜索会话历史中被压缩移出当前上下文的旧消息。

    按关键词对历史消息做检索(任一关键词命中即匹配,命中次数越多排越前),
    消息序号可用于 get_history_range 工具进一步查看前后文。

    Args:
        keywords: 搜索关键词列表(如 ["数据库", "migration", "报错"])
        context_size: 每条匹配结果前后各包含的上下文消息条数,默认3
        max_results: 最多返回的匹配条数,默认10
    """
    config = tool_runtime.config

    # 关键词预处理
    import re

    kw_list = [kw.strip() for kw in keywords if kw.strip()]
    if not kw_list:
        return "请提供至少一个搜索关键词。"

    session = config.current_agent.session

    archived = _get_archived_messages(session)
    if not archived:
        return "当前没有被压缩的历史消息,所有消息都在上下文中。"

    # 正则搜索: 任一关键词命中即匹配,命中数越多排越前
    pattern = re.compile("|".join(re.escape(kw) for kw in kw_list), re.IGNORECASE)

    matched = []  # (index_in_history, hit_count)
    for idx, msg in archived:
        content = msg.to_content() if hasattr(msg, "to_content") else ""
        if not content:
            continue
        hits = len(pattern.findall(content))
        if hits > 0:
            matched.append((idx, hits))

    if not matched:
        return f"未在历史消息中找到匹配关键词 {keywords} 的内容。共 {len(archived)} 条历史消息可搜索。"

    # 按命中数降序,取 max_results
    matched.sort(key=lambda x: x[1], reverse=True)
    matched = matched[:max_results]

    # 构建需要展示的消息集合:匹配消息 ± context_size
    history = session.history
    show_indices = set()
    for idx, _ in matched:
        start = max(0, idx - context_size)
        end = min(len(history), idx + context_size + 1)
        for i in range(start, end):
            show_indices.add(i)

    # 排序并格式化
    show_indices = sorted(show_indices)
    matched_indices = {idx for idx, _ in matched}

    lines = [
        f"找到 {len(matched)} 条匹配消息(关键词: {keywords}),"
        f"共 {len(show_indices)} 条消息含上下文:\n"
    ]

    last_idx = -1
    for idx in show_indices:
        # 添加省略提示(不连续时)
        if idx > last_idx + 1 and last_idx >= 0:
            lines.append("  ···")
        last_idx = idx

        if idx in matched_indices:
            hits = dict(matched).get(idx, 0)
            marker = f">>> 匹配 (命中{hits}次)"
        else:
            marker = "    上下文"
        lines.append(f"  {marker} {_format_message(idx, history[idx])}")

    lines.append(
        f"\n提示: 使用 get_history_range(start, end) 可查看指定序号范围的完整历史消息。"
    )

    return "\n".join(lines)


@tool
def get_history_range(
    start: int,
    end: int,
    tool_runtime: ToolRuntime = None,
) -> str:
    """获取会话历史中指定序号范围的消息。

    用于在 recall_history 找到感兴趣的消息后,查看其更远的前后文。
    序号从 0 开始,end 不包含(左闭右开区间)。

    Args:
        start: 起始序号(从0开始,包含)
        end: 结束序号(从0开始,不包含)
    """
    config = tool_runtime.config
    session = config.current_agent.session
    history = session.history
    total = len(history)

    if total == 0:
        return "当前会话没有历史消息。"

    # 类型安全(LLM 可能传字符串)
    start = int(start) if start else 0
    end = int(end) if end else total

    # 边界修正
    start = max(0, start)
    end = min(total, end)

    if start >= end:
        return f"{TOOL_ERROR}: 无效范围: start({start}) >= end({end})。历史消息共 {total} 条(序号 0 ~ {total - 1})。"

    # 限制单次返回量,避免上下文爆炸
    if end - start > 100:
        end = start + 100

    slice_msgs = history[start:end]

    # 计算归档边界: history 末尾 N 条在当前上下文中,其余已归档
    archived_boundary = max(0, len(history) - _count_recent_messages(session))

    lines = [
        f"历史消息 #{start} ~ #{end - 1}(共 {len(slice_msgs)} 条,总 {total} 条):\n"
    ]

    for i, msg in enumerate(slice_msgs):
        idx = start + i
        status = "●" if idx >= archived_boundary else "○"  # ●=在当前上下文, ○=已压缩
        lines.append(f"  {status} {_format_message(idx, msg)}")

    lines.append("\n● = 在当前上下文中  ○ = 已被压缩移出上下文")

    return "\n".join(lines)


def get_recall_hint(archived_count: int, total_count: int) -> str:
    """返回历史检索提示段,由 Session.compact() 追加到压缩摘要消息末尾。

    仅在存在被压缩的历史消息时返回内容。传入的计数由调用方(compact)
    计算,本函数不依赖 session 的当前状态。

    Args:
        archived_count: 被压缩移出当前上下文的归档消息数。
        total_count: 完整历史消息总数(len(session.history))。

    Returns:
        str: 提示段;archived_count <= 0 时返回空串。
    """
    if archived_count <= 0:
        return ""
    return (
        f"## 历史上下文\n"
        f"截至上次压缩,会话共有 {total_count} 条完整历史消息,"
        f"其中 {archived_count} 条已被压缩移出当前上下文。\n"
        f"当用户提及或你需要回忆已压缩的早期内容时,"
        f"使用 {recall_history.name} 工具搜索历史(支持多关键词)。\n"
        f"找到感兴趣的消息后,可用 {get_history_range.name} 查看更远的前后文。\n"
        f"注意:历史消息的序号可用于 {get_history_range.name} 精确定位。"
    )


def get_tools() -> list:
    """获取历史检索工具列表"""
    return [recall_history, get_history_range]


def get_all_tools() -> list:
    """获取所有历史检索工具"""
    return get_tools()
