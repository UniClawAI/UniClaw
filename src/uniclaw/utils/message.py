"""消息工具函数

从对话消息中提取和构建上下文摘要等通用功能。
"""

from enum import StrEnum


class MessageRole(StrEnum):
    """消息角色枚举"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


def extract_text(
    message: str | list[dict[str, str | dict[str, str]]], separator: str = " "
) -> str:
    """提取消息的文本内容,兼容多模态消息。

    Args:
        message: 消息内容,支持以下格式:
            - str: 直接返回
            - list: 多模态消息,提取 type="text" 的部分拼接
            - 其他: 转为字符串
        separator: 多个文本块之间的分隔符,默认为空格

    Returns:
        提取的文本内容
    """
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        texts = [
            item.get("text", "")
            for item in message
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return separator.join(texts)
    return str(message)


def build_context_summary(
    messages: list,
    max_messages: int = 0,
    max_chars: int = 0,
    roles: tuple = (MessageRole.USER, MessageRole.ASSISTANT),
) -> str:
    """从对话消息中提取最近消息作为上下文摘要。

    Args:
        messages: 消息列表,每条为 {"role": str, "content": str|list}
        max_messages: 最多取几条消息,0 表示全部
        max_chars: 摘要总字符上限,0 表示不限制
        roles: 要提取的角色,默认只取 user 和 assistant

    Returns:
        格式化的上下文文本,每行 "[role]: content"
    """
    if not messages:
        return ""

    # 过滤目标角色
    filtered = []
    for msg in messages:
        if msg.get("role") not in roles:
            continue
        content = extract_text(msg.get("content", ""))
        if content and content.strip():
            filtered.append((msg["role"], content.strip()))

    if not filtered:
        return ""

    # 截取最近 N 条
    if max_messages > 0:
        filtered = filtered[-max_messages:]

    # 截断到总字符上限
    if max_chars > 0:
        lines = []
        total = 0
        for role, content in filtered:
            if total + len(content) > max_chars:
                remaining = max_chars - total
                if remaining > 50:
                    content = content[:remaining] + "..."
                else:
                    break
            lines.append(f"[{role}]: {content}")
            total += len(content)
        return "\n".join(lines)

    return "\n".join(f"[{role}]: {content}" for role, content in filtered)
