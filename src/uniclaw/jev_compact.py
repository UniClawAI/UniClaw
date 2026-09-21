"""Jev 智能压缩 — 使用 TypeSafe AI 评分决定保留/删除哪些工具调用和结果。

与 LLM 摘要(有损)不同,本方案:
- 不总结文本消息(user/assistant 文本原样保留)
- 通过 Jev batch noul 问题批量评分每个 tool_use + tool_result 配对
- 按配对决策:完整保留 / 仅保留调用 / 删除调用+结果
- 通过 _find_split_point 分割 old/recent,只对 old 部分评分
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from uniclaw.utils.tokens import count_tokens, slice_by_tokens

if TYPE_CHECKING:
    from uniclaw.tools.session.session import Session


class JevCompactSkip(Exception):
    """Jev 压缩跳过(不可用/无配对/分割点异常),调用方应回退 LLM。"""


@dataclass
class JevCompactResult:
    """Jev 压缩结果。"""

    total_pairs: int
    """评分的工具配对总数。"""
    kept: int
    """保留的配对数。"""
    modified: int
    """修改/删除的配对数。"""
    filtered_old_count: int
    """过滤后的 old 消息数。"""


# ── 配置与数据结构 ────────────────────────────────────────────


@dataclass
class JevCompactConfig:
    """Jev 压缩配置。"""

    keep_call_threshold: float = 0.5
    """noul >= 此值时保留工具调用。"""
    keep_result_threshold: float = 0.7
    """noul >= 此值时保留工具结果原文。"""
    max_state_tokens: int = 25_000
    """Jev state token 上限(Jev 请求限制约32k)。"""
    max_tool_input_chars: int = 500
    """state 中工具参数截断长度(字符)。"""


@dataclass
class ToolPairScore:
    """工具调用+结果配对的评分数据。"""

    ai_msg_idx: int
    """AIMessage 在 _messages 中的索引。"""
    tool_call_idx: int
    """在 AIMessage.tool_calls 中的索引。"""
    tool_call_id: str
    tool_name: str
    tool_args: Any
    result_msg_idx: int
    """ToolCallMessage 在 _messages 中的索引。"""
    result_chars: int
    """结果字符数。"""
    keep_call: float = 0.0
    """Jev noul 评分: 是否保留调用。"""
    keep_result: float = 0.0
    """Jev noul 评分: 是否保留结果原文。"""


# ── 工具配对收集 ──────────────────────────────────────────────


def collect_tool_pairs(
    messages: list,
) -> list[ToolPairScore]:
    """收集 tool_use + tool_result 配对。

    遍历消息列表,将 AIMessage.tool_calls 与后续 ToolCallMessage 通过
    tool_call_id 配对。

    Args:
        messages: 消息列表(通常是 old_msgs)。

    Returns:
        ToolPairScore 列表。
    """
    from uniclaw.tools.session.session import AIMessage, ToolCallMessage

    if not messages:
        return []

    # 建立 tool_call_id -> (ai_msg_idx, tool_call_idx, tool_call) 索引
    tc_index: dict[str, tuple[int, int, dict]] = {}
    for i in range(len(messages)):
        msg = messages[i]
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for j, tc in enumerate(msg.tool_calls):
                tc_id = tc.get("id", "")
                if tc_id:
                    tc_index[tc_id] = (i, j, tc)

    # 匹配 ToolCallMessage
    pairs: list[ToolPairScore] = []
    for i in range(len(messages)):
        msg = messages[i]
        if not isinstance(msg, ToolCallMessage):
            continue
        if msg.tool_call_id not in tc_index:
            continue

        ai_idx, tc_idx, tc = tc_index[msg.tool_call_id]
        func = tc.get("function", {})
        content = msg.content if isinstance(msg.content, str) else ""

        pairs.append(
            ToolPairScore(
                ai_msg_idx=ai_idx,
                tool_call_idx=tc_idx,
                tool_call_id=msg.tool_call_id,
                tool_name=func.get("name", msg.name or ""),
                tool_args=func.get("arguments", msg.args or {}),
                result_msg_idx=i,
                result_chars=len(content),
            )
        )

    return pairs


# ── State 构建 ────────────────────────────────────────────────


def _truncate(text: str, limit: int) -> str:
    """截断文本到指定长度。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def build_jev_state(
    messages: list,
    pairs: list[ToolPairScore],
    max_tokens: int = 25_000,
    max_tool_input: int = 500,
) -> str:
    """构建 Jev state 字符串。

    将完整对话渲染为文本,但工具结果替换为 "ok, N chars (omitted)" 占位符。
    工具参数截断到 max_tool_input 字符。使用 token 计算控制总大小。

    Args:
        messages: 消息列表(通常是 old_msgs)。
        pairs: 工具配对列表。
        max_tokens: state 总 token 上限。
        max_tool_input: 工具参数截断长度(字符)。

    Returns:
        state 字符串。
    """
    from uniclaw.tools.session.session import AIMessage, ToolCallMessage, UserMessage

    # 建立 result_msg_idx 集合,用于快速查找
    result_indices = {p.result_msg_idx for p in pairs}

    parts: list[str] = []
    total_tokens = 0

    context_header = (
        "一个编程助手对话正在被压缩以释放上下文空间。"
        "工具输出已被替换为简短的结果摘要。"
        "每个问题询问某个工具调用或其完整输出是否仍需保留在历史记录中。"
    )
    parts.append(context_header)
    total_tokens += count_tokens(context_header)

    for i, msg in enumerate(messages):

        if isinstance(msg, UserMessage):
            line = _truncate(msg.to_str(), 1000)
        elif isinstance(msg, AIMessage):
            line = _truncate(msg.to_str(), 800)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    args_str = func.get("arguments", "{}")
                    if isinstance(args_str, dict):
                        args_str = json.dumps(args_str, ensure_ascii=False)
                    line += f"\n  [tool_call]: {name}({_truncate(args_str, max_tool_input)})"
        elif isinstance(msg, ToolCallMessage):
            if i in result_indices:
                # 替换为占位符
                line = f"[tool_result]: {msg.name} -> ok, {len(msg.content) if isinstance(msg.content, str) else 0} chars (omitted)"
            else:
                # 非配对的工具结果(不应出现,但安全处理)
                content = msg.content if isinstance(msg.content, str) else ""
                line = f"[tool_result]: {msg.name} -> {_truncate(content, 200)}"
        else:
            continue

        line_tokens = count_tokens(line)
        if total_tokens + line_tokens > max_tokens:
            # 超出预算:用 slice_by_tokens 精确截断
            remaining = max_tokens - total_tokens
            if remaining > 20:
                parts.append(slice_by_tokens(line, remaining) + "...")
            break

        parts.append(line)
        total_tokens += line_tokens

    return "\n".join(parts)


# ── 问题构建 ──────────────────────────────────────────────────


def build_batch_questions(pairs: list[ToolPairScore]) -> dict[str, dict]:
    """构建 Jev batch 问题。

    每个工具配对生成 2 个 noul 问题:
    - keepCall: 这个工具调用还需要保留吗?
    - keepResult: 这个工具结果需要完整保留吗?

    Args:
        pairs: 工具配对列表。

    Returns:
        Jev batch questions 字典。
    """
    questions: dict[str, dict] = {}

    for pair in pairs:
        call_qid = f"call_{pair.tool_call_id}"
        result_qid = f"result_{pair.tool_call_id}"

        questions[call_qid] = {
            "type": "noul",
            "instructions": (
                f"根据对话上下文,这个工具调用是否应该保留在历史记录中? "
                f"工具: {pair.tool_name}。 "
                f"如果知道这个调用曾经执行过(连同其参数)对理解助手后续行为仍有意义,则应保留。"
            ),
        }
        questions[result_qid] = {
            "type": "noul",
            "instructions": (
                f"这个工具结果是否需要完整保留在历史记录中? "
                f"工具: {pair.tool_name}, 结果大小: {pair.result_chars} 字符。 "
                f"只有当结果的精确内容仍然必需时(如代码片段、错误信息、具体数据值)才应保留。"
                f"如果只需知道工具曾被调用即可,请回答否。"
            ),
        }

    return questions


# ── 决策应用 ──────────────────────────────────────────────────


def filter_old_messages(
    old_msgs: list,
    pairs: list[ToolPairScore],
    config: JevCompactConfig,
) -> tuple[list, int, int]:
    """根据 Jev 评分过滤 old 消息。

    对每个工具配对:
    - keep_result >= threshold → 完整保留调用+结果
    - keep_call >= threshold → 保留调用,结果替换为占位符
    - 否则 → 删除调用+结果

    文本消息(user/assistant 无工具调用)始终保留。

    Args:
        old_msgs: old 消息列表。
        pairs: 已评分的工具配对列表。
        config: JevCompactConfig 配置。

    Returns:
        (filtered_msgs, kept_count, modified_count)。
    """
    from uniclaw.tools.session.session import AIMessage, ToolCallMessage

    # 建立需要删除/修改的索引集合
    delete_result_indices: set[int] = set()
    truncate_result_indices: dict[int, str] = {}  # idx -> tool_name
    remove_tool_calls: dict[int, set[str]] = {}  # ai_msg_idx -> {tool_call_id}

    kept = 0
    modified = 0

    for pair in pairs:
        if pair.keep_result >= config.keep_result_threshold:
            kept += 1
            continue

        if pair.keep_call >= config.keep_call_threshold:
            # 保留调用,截断结果
            truncate_result_indices[pair.result_msg_idx] = pair.tool_name
            kept += 1
            modified += 1
            continue

        # 删除调用+结果
        delete_result_indices.add(pair.result_msg_idx)
        if pair.ai_msg_idx not in remove_tool_calls:
            remove_tool_calls[pair.ai_msg_idx] = set()
        remove_tool_calls[pair.ai_msg_idx].add(pair.tool_call_id)
        modified += 1

    # 过滤消息
    filtered: list = []
    for i, msg in enumerate(old_msgs):
        if isinstance(msg, ToolCallMessage):
            if i in delete_result_indices:
                continue  # 删除
            if i in truncate_result_indices:
                original_len = len(msg.content) if isinstance(msg.content, str) else 0
                msg.content = f"[{truncate_result_indices[i]} 结果已省略, {original_len} 字符]"
                filtered.append(msg)
                continue
            filtered.append(msg)
        elif isinstance(msg, AIMessage) and i in remove_tool_calls:
            # 移除低分 tool_calls
            ids_to_remove = remove_tool_calls[i]
            msg.tool_calls = [
                tc for tc in msg.tool_calls
                if tc.get("id") not in ids_to_remove
            ]
            filtered.append(msg)
        else:
            filtered.append(msg)

    return filtered, kept, modified


# ── 主入口 ────────────────────────────────────────────────────


async def jev_compact(
    session: Session,
    compact_config: JevCompactConfig | None = None,
    keep_ratio: float = 0.3,
) -> JevCompactResult:
    """Jev 智能压缩主入口。

    使用 Jev batch noul 评分来决定哪些工具调用/结果可以删除。
    文本消息(user/assistant)永远原样保留。

    流程: split → 对 old 部分评分/过滤 → 重建 _messages = [filtered_old] + [summary] + [recent]

    Args:
        session: Session 实例。
        compact_config: JevCompactConfig 配置,为 None 时使用默认值。
        keep_ratio: 保留最近消息的比例(用于 _find_split_point)。

    Returns:
        JevCompactResult 压缩结果。

    Raises:
        JevCompactSkip: 跳过压缩(不可用/无配对/分割点异常),调用方应回退 LLM。
    """
    from uniclaw.utils.jev import batch, is_available

    if not is_available():
        raise JevCompactSkip("Jev 不可用(无 TYPESAFE_API_KEY)")

    if compact_config is None:
        compact_config = JevCompactConfig()

    # 1. 计算分割点,拆分 old 和 recent
    split = session._find_split_point(keep_ratio=keep_ratio)
    if split <= 0:
        raise JevCompactSkip("分割点 <= 0")

    old_msgs = session._messages[:split]
    recent_msgs = session._messages[split:]

    # 2. 收集 old 部分的工具配对
    pairs = collect_tool_pairs(old_msgs)

    if not pairs:
        raise JevCompactSkip("没有可评分的工具配对")

    # 3. 构建 state 和问题
    state = build_jev_state(
        old_msgs,
        pairs,
        max_tokens=compact_config.max_state_tokens,
        max_tool_input=compact_config.max_tool_input_chars,
    )
    questions = build_batch_questions(pairs)

    # 4. 调用 Jev batch
    result = await batch(state=state, questions=questions)

    # 5. 映射评分结果
    for pair in pairs:
        call_qid = f"call_{pair.tool_call_id}"
        result_qid = f"result_{pair.tool_call_id}"
        if call_qid in result.answers:
            pair.keep_call = result.answers[call_qid].noul
        if result_qid in result.answers:
            pair.keep_result = result.answers[result_qid].noul

    # 6. 过滤 old 消息(保留高分工具配对,丢弃低分的)
    filtered_old, kept, modified = filter_old_messages(
        old_msgs, pairs, compact_config
    )

    # 7. 重建 _messages = [summary] + filtered_old + recent
    summary_text = (
        f"[Jev 压缩] 已通过 Jev 智能评分处理 {len(pairs)} 个工具配对: "
        f"保留 {kept} 个,修改/删除 {modified} 个。文本消息全部保留。"
    )

    from uniclaw.provider.types import Usage
    from uniclaw.tools.session.session import AIMessage, UserMessage
    from uniclaw.utils.constants import SYSTEM_PREFIX

    session._messages = [
        *filtered_old,
        UserMessage(content=f"{SYSTEM_PREFIX}{summary_text}"),
        AIMessage(
            content="已阅读压缩记录,继续当前任务。",
            model_name="",
            usage=Usage.from_dict({}),
        ),
        *recent_msgs,
    ]
    session._compact_end = len(filtered_old) + 2
    session._compact_warned_levels.clear()

    return JevCompactResult(
        total_pairs=len(pairs),
        kept=kept,
        modified=modified,
        filtered_old_count=len(filtered_old),
    )
