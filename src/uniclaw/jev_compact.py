"""Jev 智能压缩 — 使用 TypeSafe AI 评分决定保留/删除哪些工具调用和结果。

与 LLM 摘要(有损)不同,本方案:
- 不总结文本消息(user/assistant 文本原样保留)
- 通过 Jev batch noul 问题批量评分每个 tool_use + tool_result 配对
- 按配对决策:完整保留 / 仅保留调用 / 删除调用+结果
- 通过对齐工具配对的分割点拆分 old/recent,只对 old 部分评分
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from uniclaw.utils.tokens import count_tokens

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
    """完整保留(调用+结果原文)的配对数。与 modified 互斥,两者之和 = total_pairs。"""
    modified: int
    """被改写(结果降为占位)或整对删除的配对数。与 kept 互斥。"""
    filtered_old_count: int
    """过滤后的 old 消息数。"""
    tokens_saved: int = 0
    """压缩释放的 token 估算值(before - after)。接近 0 说明判官普遍给高分、本次几乎没压下东西。"""


# ── 配置与数据结构 ────────────────────────────────────────────


@dataclass
class JevCompactConfig:
    """Jev 压缩配置。

    阈值按误留/误删代价的不对称性设定:
    - 工具调用一行约 30 token,留错只是浪费,删错是叙事永久断裂 → 低阈值,删除是例外
    - 工具结果可达数千 token,可再生结果(Read/Grep 等)stub 后可重跑取回,
      不可再生结果(多媒体/用户答复/副作用回执)stub 即永久丢失 → 分设两个阈值
    """

    keep_call_threshold: float = 0.3
    """noul >= 此值时保留工具调用。低于此值整对删除 — 仅限判官明确视为可抹除的探索性调用。"""
    keep_result_threshold: float = 0.7
    """可再生结果的 noul >= 此值时保留原文(否则降为占位)。可重跑取回,允许偏激进。"""
    keep_result_threshold_irreplaceable: float = 0.5
    """不可再生结果(多媒体/不可重跑工具)的 keep_result 阈值 — 低于可再生结果,不确定时优先保全文。"""
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
    """结果文本字符数(多媒体块不计入,见 result_has_media)。"""
    result_has_media: bool = False
    """结果是否包含多媒体块(图片/音频/视频),此类结果通常无法重新获取。"""
    result_irreplaceable: bool = False
    """结果是否不可再生(含多媒体,或工具不可重跑取回) — 决定用哪个 keep_result 阈值。"""
    keep_call: float = 1.0
    """Jev noul 评分: 是否保留调用。未评分时默认 1.0(保留,fail-safe)。"""
    keep_result: float = 1.0
    """Jev noul 评分: 是否保留结果原文。未评分时默认 1.0(保留,fail-safe)。"""


def _result_metrics(content: Any) -> tuple[int, bool]:
    """估算工具结果大小。返回 (文本字符数, 是否含多媒体块)。

    多媒体结果(图片/音频/视频)不能按文本字符数衡量,has_media 标记
    用于提示 judge 该结果通常无法重新获取。

    Args:
        content: 工具结果内容(str 或块列表)。

    Returns:
        tuple[int, bool]: (文本字符数, 是否含多媒体块)。
    """
    if isinstance(content, str):
        return len(content), False
    if not isinstance(content, list):
        return (len(str(content)) if content else 0), False

    chars = 0
    has_media = False
    for block in content:
        if isinstance(block, str):
            chars += len(block)
            continue
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                chars += len(text)
            btype = block.get("type")
        else:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                chars += len(text)
            btype = getattr(block, "type", None)
        if btype not in (None, "text"):
            has_media = True
    return chars, has_media


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

    # 可再生工具(Session.COMPACTABLE_TOOLS)的结果可通过重跑取回,
    # 其余(含多媒体)stub 后即永久丢失,评分时走更低的 keep_result 阈值
    from uniclaw.tools.session.session import Session

    compactable = Session.COMPACTABLE_TOOLS

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
        result_chars, result_has_media = _result_metrics(msg.content)
        tool_name = func.get("name", msg.name or "")

        pairs.append(
            ToolPairScore(
                ai_msg_idx=ai_idx,
                tool_call_idx=tc_idx,
                tool_call_id=msg.tool_call_id,
                tool_name=tool_name,
                tool_args=func.get("arguments", msg.args or {}),
                result_msg_idx=i,
                result_chars=result_chars,
                result_has_media=result_has_media,
                result_irreplaceable=(
                    result_has_media or tool_name not in compactable
                ),
            )
        )

    return pairs


# ── State 构建 ────────────────────────────────────────────────


def _truncate(text: str, limit: int) -> str:
    """截断文本到指定长度,超长时保留头尾。

    首尾往往承载任务目标与最新补充,中段省略对判断更友好。

    Args:
        text: 原始文本。
        limit: 最大长度(字符)。

    Returns:
        str: 截断后的文本。
    """
    if len(text) <= limit:
        return text
    if limit <= 40:
        return text[:limit] + "..."
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n[...省略 {omitted} 字符...]\n{text[-tail:]}"


@dataclass
class _Segment:
    """state 中的渲染单元(一行或一组配对标签行)。"""

    order: int
    """原始顺序,输出时按此排序保持对话时序。"""
    text: str
    tokens: int
    pair_num: int | None = None
    """所属配对编号(1-based);None 表示可丢弃的上下文行。"""


def build_jev_state(
    messages: list,
    pairs: list[ToolPairScore],
    max_tokens: int = 25_000,
    max_tool_input: int = 500,
) -> tuple[str, frozenset[int]]:
    """构建 Jev state 字符串。

    将完整对话渲染为文本,但工具结果替换为 "ok, N chars (omitted)" 占位符。
    工具参数截断到 max_tool_input 字符。使用 token 计算控制总大小。

    预算不足时从最旧内容开始丢弃(靠近分割点的较新消息优先保留),
    选取结果是"从最新往旧的连续后缀"— 遇到装不下的组即停止,更旧的一律丢弃,
    不允许中间挖洞。judge 的 keepCall 判断依赖后续上下文(是否被后续调用取代、
    是否影响后续决策),较新的内容缺席而更旧的内容反而在场会导致误判删除。
    且 [tool_call #N] / [tool_result #N] 标签行与所属配对同进同出 —
    只对实际出现在 state 中的配对提问(见返回值 visible_nums),
    避免问题引用不存在的 #N 标签。

    Args:
        messages: 消息列表(通常是 old_msgs)。
        pairs: 工具配对列表。
        max_tokens: state 总 token 上限。
        max_tool_input: 工具参数截断长度(字符)。

    Returns:
        tuple[str, frozenset[int]]: (state 字符串, 实际出现在 state 中的配对编号集合)。
    """
    from uniclaw.tools.session.session import AIMessage, ToolCallMessage, UserMessage

    # 建立 result_msg_idx 集合,用于快速查找
    result_indices = {p.result_msg_idx for p in pairs}
    # tool_call_id -> 配对编号(1-based),与 build_batch_questions 的 #N 一致
    id_to_num = {p.tool_call_id: n for n, p in enumerate(pairs, 1)}

    context_header = (
        "一个编程助手对话正在被压缩以释放上下文空间。"
        "工具输出已被替换为简短的结果摘要。"
        "工具调用与结果带有 #编号(如 [tool_call #3] / [tool_result #3]),"
        "与每个评估问题中给出的编号一一对应。"
    )

    def _seg(text: str, order: int, pair_num: int | None = None) -> _Segment:
        return _Segment(order=order, text=text, tokens=count_tokens(text), pair_num=pair_num)

    segments: list[_Segment] = []
    order = 0

    for i, msg in enumerate(messages):

        if isinstance(msg, UserMessage):
            segments.append(_seg(_truncate(msg.to_str(), 1000), order))
            order += 1
        elif isinstance(msg, AIMessage):
            if msg.content or not msg.tool_calls:
                segments.append(_seg(_truncate(msg.to_str(), 800), order))
                order += 1
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    args_str = func.get("arguments", "{}")
                    if isinstance(args_str, dict):
                        args_str = json.dumps(args_str, ensure_ascii=False)
                    num = id_to_num.get(tc.get("id", ""))
                    label = f" #{num}" if num else ""
                    text = (
                        f"  [tool_call{label}]: "
                        f"{name}({_truncate(args_str, max_tool_input)})"
                    )
                    segments.append(_seg(text, order, pair_num=num))
                    order += 1
        elif isinstance(msg, ToolCallMessage):
            num = id_to_num.get(msg.tool_call_id) if i in result_indices else None
            if num is not None:
                chars, has_media = _result_metrics(msg.content)
                if has_media:
                    size_note = f"{chars} chars 含多媒体" if chars else "含多媒体"
                else:
                    size_note = f"{chars} chars"
                text = f"[tool_result #{num}]: {msg.name} -> ok, {size_note} (omitted)"
            else:
                content = msg.content if isinstance(msg.content, str) else msg.to_content()
                text = f"[tool_result]: {msg.name} -> {_truncate(content, 200)}"
            segments.append(_seg(text, order, pair_num=num))
            order += 1
        else:
            continue

    # 配对组: call + result 标签行同进同出;上下文行单独成组
    pair_segments: dict[int, list[_Segment]] = {}
    units: list[tuple[int, list[_Segment]]] = []  # (组序=组内最大 order, 组内行)
    for seg in segments:
        if seg.pair_num is not None:
            pair_segments.setdefault(seg.pair_num, []).append(seg)
        else:
            units.append((seg.order, [seg]))
    for segs in pair_segments.values():
        units.append((max(s.order for s in segs), segs))

    header_tokens = count_tokens(context_header)
    budget = max(0, max_tokens - header_tokens)
    used = 0
    selected: list[_Segment] = []
    # 越新(组序越大)越优先 — 取从最新往旧的连续后缀,装不下即停:
    # 若跳过中间只收更旧的小单元,会挖出时间空洞,残缺叙事误导 judge
    for _, segs in sorted(units, key=lambda u: u[0], reverse=True):
        cost = sum(s.tokens + 1 for s in segs)
        if used + cost > budget:
            break
        selected.extend(segs)
        used += cost

    visible_nums = frozenset(s.pair_num for s in selected if s.pair_num is not None)
    # 按原始顺序输出,保持对话时序
    selected.sort(key=lambda s: s.order)
    parts = [context_header] + [s.text for s in selected]
    return "\n".join(parts), visible_nums


# ── 问题构建 ──────────────────────────────────────────────────


def build_batch_questions(
    pairs: list[ToolPairScore],
    focus: str = "",
    only_nums: set[int] | frozenset[int] | None = None,
) -> dict[str, dict]:
    """构建 Jev batch 问题。

    每个工具配对生成 2 个 noul 问题:
    - keepCall: 这个工具调用还需要保留吗?
    - keepResult: 这个工具结果需要完整保留吗?

    问题通过 #[编号] 指向 state 中的 [tool_call #N] / [tool_result #N] 标签
    (编号由 pairs 列表顺序决定,与 build_jev_state 一致),
    使 judge 能唯一定位到具体某次调用,同名工具多次调用也不会混淆。

    Args:
        pairs: 工具配对列表。
        focus: 聚焦主题(可选)。非空时提示 Jev 优先保留与该主题相关的内容,
            与 LLM 摘要的 focus 参数语义对齐。
        only_nums: 只为这些配对编号生成问题(来自 build_jev_state 的 visible_nums)。
            编号仍按 pairs 全量顺序计算,与 state 标签保持一致。None 表示全部。

    Returns:
        Jev batch questions 字典。
    """
    questions: dict[str, dict] = {}
    focus_hint = f" 与「{focus}」相关的内容更应保留。" if focus else ""

    for n, pair in enumerate(pairs, 1):
        if only_nums is not None and n not in only_nums:
            continue
        call_qid = f"call_{pair.tool_call_id}"
        result_qid = f"result_{pair.tool_call_id}"
        args_preview = _truncate(
            json.dumps(pair.tool_args, ensure_ascii=False)
            if isinstance(pair.tool_args, dict)
            else str(pair.tool_args),
            120,
        )

        questions[call_qid] = {
            "type": "noul",
            "instructions": (
                f"对话中的 [tool_call #{n}]: 这个工具调用是否应该保留在历史记录中? "
                f"工具: {pair.tool_name}, 参数: {args_preview}。 "
                f"若知道它曾执行过(连同其参数)对理解助手后续行为仍有意义 — "
                f"例如它改变了工作状态、参数记录了关键选择、或其结果影响了后续决策 — 则回答是。 "
                f"若它只是探索性尝试、已被后续调用取代、或抹去后不影响理解,则回答否。"
                f"{focus_hint}"
            ),
        }
        if pair.result_has_media:
            size_desc = (
                "结果包含多媒体内容(图片/音频/视频等),通常无法重新获取。"
            )
            if pair.result_chars:
                size_desc += f"文本部分约 {pair.result_chars} 字符。"
        else:
            size_desc = f"结果大小: {pair.result_chars} 字符。"
        questions[result_qid] = {
            "type": "noul",
            "instructions": (
                f"对话中的 [tool_result #{n}](对应 [tool_call #{n}]): "
                f"这个工具结果的精确内容是否仍需完整保留在历史记录中? "
                f"工具: {pair.tool_name}, {size_desc} "
                f"(结果原文在本上下文中不可见,请结合工具类型与对话进展推断其中可能包含什么。) "
                f"若后续可能需要逐字引用其中的精确信息 — 例如代码、报错、数据值、路径、URL、"
                f"用户给出的答复、状态或清单、配置语义 — 则回答是。 "
                f"若只需知道该工具曾被调用、内容可被概括或重新获取,则回答否"
                f"(否 = 允许把结果降为一行占位说明,不是抹掉这次调用)。"
                f"{focus_hint}"
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
      (不可再生结果用 keep_result_threshold_irreplaceable,可再生结果用 keep_result_threshold)
    - keep_call >= keep_call_threshold → 保留调用,结果替换为占位符
    - 否则 → 删除调用+结果

    文本消息(user/assistant 无工具调用)始终保留。
    改写通过复制消息完成 — `_messages` 与 `history` 共享消息对象,
    原地修改会破坏 history 归档(recall 检索/会话存档依赖原文)。

    Args:
        old_msgs: old 消息列表。
        pairs: 已评分的工具配对列表。
        config: JevCompactConfig 配置。

    Returns:
        (filtered_msgs, kept_count, modified_count)。
        kept 与 modified 互斥: kept = 完整保留的配对数,
        modified = 被改写或删除的配对数,两者之和等于 len(pairs)。
    """
    from uniclaw.tools.session.session import AIMessage, ToolCallMessage

    # 建立需要删除/修改的索引集合
    delete_result_indices: set[int] = set()
    truncate_result_indices: dict[int, str] = {}  # idx -> tool_name
    remove_tool_calls: dict[int, set[str]] = {}  # ai_msg_idx -> {tool_call_id}

    kept = 0
    modified = 0

    for pair in pairs:
        # 不可再生结果显著更容易保全文 — stub 即永久丢失
        result_threshold = (
            config.keep_result_threshold_irreplaceable
            if pair.result_irreplaceable
            else config.keep_result_threshold
        )
        if pair.keep_result >= result_threshold:
            kept += 1
            continue

        if pair.keep_call >= config.keep_call_threshold:
            # 保留调用,截断结果
            truncate_result_indices[pair.result_msg_idx] = pair.tool_name
            modified += 1
            continue

        # 删除调用+结果
        delete_result_indices.add(pair.result_msg_idx)
        if pair.ai_msg_idx not in remove_tool_calls:
            remove_tool_calls[pair.ai_msg_idx] = set()
        remove_tool_calls[pair.ai_msg_idx].add(pair.tool_call_id)
        modified += 1

    # 过滤消息(改写一律复制,不触碰 history 共享的原对象)
    filtered: list = []
    for i, msg in enumerate(old_msgs):
        if isinstance(msg, ToolCallMessage):
            if i in delete_result_indices:
                continue  # 删除
            if i in truncate_result_indices:
                result_chars, has_media = _result_metrics(msg.content)
                if has_media:
                    stub = f"[{truncate_result_indices[i]} 结果已省略, 原含多媒体内容]"
                else:
                    stub = f"[{truncate_result_indices[i]} 结果已省略, {result_chars} 字符]"
                new_msg = copy.copy(msg)
                new_msg.content = stub  # __setattr__ 自动失效哈希缓存
                filtered.append(new_msg)
                continue
            filtered.append(msg)
        elif isinstance(msg, AIMessage) and i in remove_tool_calls:
            # 移除低分 tool_calls(复制后改写,原消息留给 history 归档)
            ids_to_remove = remove_tool_calls[i]
            new_calls = [
                tc for tc in msg.tool_calls
                if tc.get("id") not in ids_to_remove
            ]
            # tool_calls 全部删除且无文本的空 assistant 消息一并丢弃,
            # 避免留下无内容占位
            if not new_calls and not (msg.content or ""):
                continue
            new_msg = copy.copy(msg)
            new_msg.tool_calls = new_calls
            filtered.append(new_msg)
        else:
            filtered.append(msg)

    return filtered, kept, modified


# ── 主入口 ────────────────────────────────────────────────────


async def jev_compact(
    session: Session,
    compact_config: JevCompactConfig | None = None,
    keep_ratio: float = 0.3,
    focus: str = "",
) -> JevCompactResult:
    """Jev 智能压缩主入口。

    使用 Jev batch noul 评分来决定哪些工具调用/结果可以删除。
    文本消息(user/assistant)永远原样保留。

    流程: split → 对 old 部分评分/过滤 → 重建 _messages = filtered_old + [summary] + recent

    Args:
        session: Session 实例。
        compact_config: JevCompactConfig 配置,为 None 时使用默认值。
        keep_ratio: 保留最近消息的比例(用于 _find_split_point)。
        focus: 聚焦主题(可选),提示 Jev 优先保留与该主题相关的内容。

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

    # 1. 计算分割点(已对齐工具配对边界),拆分 old 和 recent
    split = session._find_split_point(keep_ratio=keep_ratio)
    if split <= 0:
        raise JevCompactSkip("分割点 <= 0")

    old_msgs = session._messages[:split]
    recent_msgs = session._messages[split:]

    # 2. 收集 old 部分的工具配对
    pairs = collect_tool_pairs(old_msgs)

    if not pairs:
        raise JevCompactSkip("没有可评分的工具配对")

    # 3. 构建 state 和问题(state 只容纳可见配对,问题也只为可见配对生成)
    state, visible_nums = build_jev_state(
        old_msgs,
        pairs,
        max_tokens=compact_config.max_state_tokens,
        max_tool_input=compact_config.max_tool_input_chars,
    )
    questions = build_batch_questions(pairs, focus=focus, only_nums=visible_nums)
    if not questions:
        raise JevCompactSkip("没有可提问的配对(state 预算不足)")

    # 4. 调用 Jev batch
    result = await batch(state=state, questions=questions)

    # 5. 映射评分结果 — 缺答/未提问的配对 fail-safe 保留,绝不静默删除
    for pair in pairs:
        call_ans = result.answers.get(f"call_{pair.tool_call_id}")
        result_ans = result.answers.get(f"result_{pair.tool_call_id}")
        pair.keep_call = (
            getattr(call_ans, "noul", 1.0) if call_ans is not None else 1.0
        )
        pair.keep_result = (
            getattr(result_ans, "noul", 1.0) if result_ans is not None else 1.0
        )

    # 6. 过滤 old 消息(保留高分工具配对,丢弃低分的)
    tokens_before = session.estimate_tokens()
    filtered_old, kept, modified = filter_old_messages(
        old_msgs, pairs, compact_config
    )

    # 7. 重建 _messages = filtered_old + [summary] + recent
    summary_text = (
        f"[Jev 压缩] 已通过 Jev 智能评分处理 {len(pairs)} 个工具配对: "
        f"完整保留 {kept} 个,改写/删除 {modified} 个。文本消息全部保留。"
    )

    # 与 LLM 摘要(compact())同构:追加历史检索提示与会话笔记快照
    from uniclaw.tools.session.recall import get_recall_hint

    archived_count = len(session.history) - len(recent_msgs)
    recall_hint = get_recall_hint(archived_count, len(session.history))
    if recall_hint:
        summary_text += f"\n\n{recall_hint}"

    if session.session_notes:
        from uniclaw.tools.session.notes import session_note_list

        notes_section = "\n\n## 会话笔记(上轮对话快照,可能已过期,仅供参考)\n"
        for note in session.session_notes:
            notes_section += f"- [{note.name}]: {note.description}\n"
        notes_section += f"使用 {session_note_list.name} 查看全部笔记。\n"
        summary_text += notes_section

    from uniclaw.provider.types import Usage
    from uniclaw.tools.session.session import (
        AIMessage,
        SUMMARY_PREFIX,
        UserMessage,
    )

    session._messages = [
        *filtered_old,
        UserMessage(content=f"{SUMMARY_PREFIX}\n{summary_text}"),
        AIMessage(
            content="已阅读压缩记录,继续当前任务。",
            model_name="",
            usage=Usage.from_dict({}),
        ),
        *recent_msgs,
    ]
    session._compact_end = len(filtered_old) + 2
    session._compact_warned_levels.clear()
    tokens_saved = max(0, tokens_before - session.estimate_tokens())

    return JevCompactResult(
        total_pairs=len(pairs),
        kept=kept,
        modified=modified,
        filtered_old_count=len(filtered_old),
        tokens_saved=tokens_saved,
    )
