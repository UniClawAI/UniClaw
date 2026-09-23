"""Jev 结构化决策模块 — 封装 TypeSafe AI System One API。

提供底层原语(choice/score/noul/batch)和通用模式函数(select_one/select_many/yes_no/rate/multi_judge),
供各子系统代码直接调用,不注册为 LLM 工具。

用法:
    from uniclaw.utils.jev import select_one, yes_no, rate

    # 单选:从选项中选一个
    result = await select_one(state="用户输入...", instruction="判断意图", options={"file": "文件操作", "code": "代码编写", "chat": "闲聊"})
    print(result.choice, result.confidence)

    # 是否判断
    result = await yes_no(state="rm -rf /tmp/data", question="这个命令是否有破坏性?")
    print(result.noul)  # 0~1,越高越"是"

    # 量表评级
    result = await rate(state="用户消息...", instruction="评估紧急程度", levels=["低", "中", "高", "紧急"])
    print(result.score)

注意:需要设置环境变量 TYPESAFE_API_KEY。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    Noul,
    NoulCriteria,
    Score,
)

from uniclaw.utils.tokens import count_tokens


# ── 异常类型 ──────────────────────────────────────────────────


class JevConfigError(Exception):
    """API key 未配置或配置无效。"""


class JevAPIError(Exception):
    """TypeSafe API 调用失败。"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ── 返回类型 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ChoiceResult:
    """Choice 问题返回值。"""

    choice: str
    """选中的选项。"""
    probabilities: dict[str, float]
    """各选项概率分布。"""
    confidence: float
    """置信度 0~1,概率分布越集中越高。"""


@dataclass(frozen=True)
class ScoreResult:
    """Score 问题返回值。"""

    score: float
    """评分(可在等级之间插值)。"""
    probabilities: dict[str, float]
    """各等级概率分布。"""
    confidence: float
    """置信度 0~1。"""


@dataclass(frozen=True)
class NoulResult:
    """Noul 问题返回值。"""

    noul: float
    """"是"的概率 0~1。接近 1=强烈为是,接近 0=强烈为否,接近 0.5=不确定。"""


@dataclass(frozen=True)
class BatchResult:
    """批量问题返回值。"""

    answers: dict[str, ChoiceResult | ScoreResult | NoulResult]
    """按 question_id 索引的答案字典。"""


# ── 可用性检查 ────────────────────────────────────────────────


def is_available() -> bool:
    """检查 Jev 是否可用(TYPESAFE_API_KEY 环境变量是否已配置)。

    Returns:
        bool: True 表示可以调用 Jev API。
    """
    return bool(os.environ.get("TYPESAFE_API_KEY", "").strip())


# ── 客户端管理 ────────────────────────────────────────────────


def _resolve_api_key(api_key: str | None = None) -> str:
    """解析 API key: 参数 > 环境变量。"""
    key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
    if not key:
        raise JevConfigError(
            "TYPESAFE_API_KEY 未配置。请设置环境变量 TYPESAFE_API_KEY。"
        )
    return key


def _resolve_model(model: str | None = None) -> str | None:
    """解析模型名: 参数 > 环境变量 > SDK 默认。"""
    return model or os.environ.get("TYPESAFE_DEFAULT_MODEL") or None


def _create_client(
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
):
    """创建 AsyncTypeSafeClient 实例。"""
    key = _resolve_api_key(api_key)
    resolved_model = _resolve_model(model)
    kwargs: dict[str, Any] = {"api_key": key, "timeout": timeout}
    if resolved_model:
        kwargs["model"] = resolved_model
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncTypeSafeClient(**kwargs)


def _parse_choice(answer) -> ChoiceResult:
    """解析 SDK ChoiceAnswer 为 ChoiceResult。"""
    return ChoiceResult(
        choice=answer.choice,
        probabilities=dict(answer.probabilities),
        confidence=answer.confidence,
    )


def _parse_score(answer) -> ScoreResult:
    """解析 SDK ScoreAnswer 为 ScoreResult。"""
    return ScoreResult(
        score=answer.score,
        probabilities=dict(answer.probabilities),
        confidence=answer.confidence,
    )


def _parse_noul(answer) -> NoulResult:
    """解析 SDK NoulAnswer 为 NoulResult。"""
    return NoulResult(noul=answer.noul)


def _classify_answer(qid: str, answer) -> ChoiceResult | ScoreResult | NoulResult:
    """根据 answer 类型自动解析。"""
    if hasattr(answer, "choice"):
        return _parse_choice(answer)
    elif hasattr(answer, "score"):
        return _parse_score(answer)
    elif hasattr(answer, "noul"):
        return _parse_noul(answer)
    else:
        raise JevAPIError(f"未知 answer 类型 (qid={qid}): {type(answer)}")


# ── 请求预算 ──────────────────────────────────────────────────
#
# Jev (jev-1.13) 的输入限制,超限服务端返回 400 max_tokens_exceeded:
# - state 与全部 questions 合计约 64k token
# - state 与最长单个 question 合计约 32k token
# batch() 据此自动分片(问题超总量拆多组请求);state 超预算直接报错,
# 不截断 — 截断后的评估质量无法保证,由调用方回退 LLM。

TOTAL_BUDGET_TOKENS = 64_000
"""state 与全部 questions 合计 token 上限。"""

STATE_WITH_LONGEST_TOKENS = 32_000
"""state 与最长单个 question 合计 token 上限。"""

BUDGET_USAGE = 0.85
"""预算折算系数 — count_tokens 为 cl100k 估算,与 Jev 真实 tokenizer 有偏差,留余量。"""


# ── 底层原语 ──────────────────────────────────────────────────


async def choice(
    state: str | dict,
    instructions: str,
    criteria: dict[str, str],
    *,
    question_id: str = "result",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> ChoiceResult:
    """Choice 原语:从选项中选一个。

    Args:
        state: 待评估的内容(字符串或 JSON 对象)。
        instructions: 评估指令(告诉模型评判什么)。
        criteria: 选项映射,键为选项名,值为选项描述(可为 None)。
        question_id: 答案标识符。
        api_key: TypeSafe API key(为空时从环境变量读取)。
        model: Jev 模型名(为空时用 SDK 默认)。
        base_url: API 地址(为空时用默认)。
        timeout: 请求超时秒数。

    Returns:
        ChoiceResult: 包含 choice, probabilities, confidence。

    Raises:
        JevConfigError: TYPESAFE_API_KEY 未配置。
        JevAPIError: API 调用失败。
    """
    async with _create_client(api_key, model, base_url, timeout) as client:
        try:
            response = await client.system_one(
                state=state,
                questions={
                    question_id: Choice(instructions=instructions, criteria=criteria),
                },
            )
        except Exception as e:
            raise JevAPIError(f"Choice 调用失败: {e}") from e
    return _parse_choice(response.answers[question_id])


async def score(
    state: str | dict,
    instructions: str,
    criteria: list[str],
    *,
    question_id: str = "result",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> ScoreResult:
    """Score 原语:按量表评级。

    Args:
        state: 待评估的内容。
        instructions: 评估指令。
        criteria: 有序等级列表,从低到高排列(如 ["低", "中", "高"])。
        question_id: 答案标识符。
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        ScoreResult: 包含 score, probabilities, confidence。

    Raises:
        JevConfigError: TYPESAFE_API_KEY 未配置。
        JevAPIError: API 调用失败。
    """
    async with _create_client(api_key, model, base_url, timeout) as client:
        try:
            response = await client.system_one(
                state=state,
                questions={
                    question_id: Score(instructions=instructions, criteria=criteria),
                },
            )
        except Exception as e:
            raise JevAPIError(f"Score 调用失败: {e}") from e
    return _parse_score(response.answers[question_id])


async def noul(
    state: str | dict,
    instructions: str,
    criteria: dict[str, str] | None = None,
    *,
    question_id: str = "result",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> NoulResult:
    """Noul 原语:是/否概率判断。

    Args:
        state: 待评估的内容。
        instructions: 评估指令(应为清晰的是否问题)。
        criteria: 可选,可选的真/假描述(如 {"true": "...", false: "..."})。
        question_id: 答案标识符。
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        NoulResult: 包含 noul (float 0~1)。

    Raises:
        JevConfigError: TYPESAFE_API_KEY 未配置。
        JevAPIError: API 调用失败。
    """
    noul_kwargs: dict[str, Any] = {"instructions": instructions}
    if criteria:
        noul_kwargs["criteria"] = NoulCriteria(
            true=criteria.get("true", ""),
            false=criteria.get("false", ""),
        )
    async with _create_client(api_key, model, base_url, timeout) as client:
        try:
            response = await client.system_one(
                state=state,
                questions={question_id: Noul(**noul_kwargs)},
            )
        except Exception as e:
            raise JevAPIError(f"Noul 调用失败: {e}") from e
    return _parse_noul(response.answers[question_id])


def _state_text(state: str | dict) -> str:
    """state 序列化为文本,用于 token 估算。"""
    if isinstance(state, str):
        return state
    return json.dumps(state, ensure_ascii=False, default=str)


def _question_tokens(qid: str, qdef: dict) -> int:
    """估算单个问题占用的 token(instructions + criteria + id 与结构开销)。"""
    parts = [str(qdef.get("instructions") or "")]
    criteria = qdef.get("criteria")
    if criteria is not None:
        parts.append(json.dumps(criteria, ensure_ascii=False, default=str))
    return count_tokens("\n".join(parts)) + count_tokens(qid) + 8


def _check_state_budget(state: str | dict, max_question_tokens: int = 0) -> int:
    """检查 state 与最长问题是否装进 Jev 预算,超限抛 JevAPIError。

    不做截断 — 截断后的 state 评估质量无法保证,宁可让调用方回退 LLM。

    Args:
        state: 待评估内容(str 或 JSON 对象)。
        max_question_tokens: 本次请求中最长问题的估算 token 数。

    Returns:
        int: state 的估算 token 数。

    Raises:
        JevAPIError: state + 最长问题超出 STATE_WITH_LONGEST_TOKENS 预算。
    """
    budget = int(STATE_WITH_LONGEST_TOKENS * BUDGET_USAGE)
    state_tokens = count_tokens(_state_text(state))
    if state_tokens + max_question_tokens > budget:
        raise JevAPIError(
            f"state 超出 Jev 预算: state 约 {state_tokens} token + "
            f"最长问题约 {max_question_tokens} token > 上限 {budget} token, "
            f"截断无法保证评估质量,请回退 LLM"
        )
    return state_tokens


def _split_questions(
    sdk_questions: dict[str, Any],
    question_tokens: dict[str, int],
    state_tokens: int,
) -> list[dict[str, Any]]:
    """按 token 预算把问题切成若干组,每组一次 system_one 请求(state 相同)。

    Args:
        sdk_questions: 全部 SDK 问题对象,按插入顺序切分。
        question_tokens: question_id -> 估算 token 数。
        state_tokens: state 的估算 token 数(每组请求都要重发)。

    Returns:
        list[dict[str, Any]]: 问题分组,至少一组;单个问题超预算时独占一组。
    """
    budget = int(TOTAL_BUDGET_TOKENS * BUDGET_USAGE) - state_tokens
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    used = 0
    for qid, qobj in sdk_questions.items():
        cost = question_tokens.get(qid, 0)
        if current and used + cost > budget:
            chunks.append(current)
            current = {}
            used = 0
        current[qid] = qobj
        used += cost
    if current:
        chunks.append(current)
    return chunks


async def batch(
    state: str | dict,
    questions: dict[str, dict],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> BatchResult:
    """批量问题:一次请求发送多个混合类型问题。

    Jev 的核心优势:多个问题在一次请求中并行评估,成本接近单个问题,
    速度几乎不增加(官方:13 问题 batch 比逐个快 10x,便宜 12x)。

    问题总量超过 Jev 预算时自动拆成多组并行请求(state 原样重发)后合并答案,
    对调用方透明(见 TOTAL_BUDGET_TOKENS)。state 超预算时不截断,
    直接抛 JevAPIError — 截断后的 state 评估质量无法保证,由调用方回退 LLM。

    Args:
        state: 待评估的内容。
        questions: 问题字典,键为 question_id,值为问题定义 dict:
            {"type": "choice"|"score"|"noul", "instructions": "...", "criteria": ...}
            - choice: criteria 为 dict[str, str]
            - score: criteria 为 list[str]
            - noul: criteria 为可选 dict 或 None
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        BatchResult: 包含 answers dict,按 question_id 索引。

    Raises:
        JevConfigError: TYPESAFE_API_KEY 未配置。
        JevAPIError: API 调用失败,或 state 超出 Jev 预算。
    """
    # 构建 SDK 问题对象
    sdk_questions: dict[str, Any] = {}
    for qid, qdef in questions.items():
        qtype = qdef["type"]
        instructions = qdef["instructions"]
        criteria = qdef.get("criteria")

        if qtype == "choice":
            sdk_questions[qid] = Choice(instructions=instructions, criteria=criteria)
        elif qtype == "score":
            sdk_questions[qid] = Score(instructions=instructions, criteria=criteria)
        elif qtype == "noul":
            noul_kwargs: dict[str, Any] = {"instructions": instructions}
            if criteria:
                noul_kwargs["criteria"] = NoulCriteria(
                    true=criteria.get("true", ""),
                    false=criteria.get("false", ""),
                )
            sdk_questions[qid] = Noul(**noul_kwargs)
        else:
            raise ValueError(f"未知问题类型: {qtype!r},支持 choice/score/noul")

    # 预算检查:state + 最长单题装不进 32k 就报错回退;能装下则按总量切分问题
    question_tokens = {
        qid: _question_tokens(qid, qdef) for qid, qdef in questions.items()
    }
    max_q = max(question_tokens.values(), default=0)
    state_tokens = _check_state_budget(state, max_q)
    chunks = _split_questions(sdk_questions, question_tokens, state_tokens)
    if not chunks:
        # 空问题原样透传给 SDK 报 "At least one question is required.",保持既有报错行为
        chunks = [{}]

    async with _create_client(api_key, model, base_url, timeout) as client:
        try:
            if len(chunks) == 1:
                responses = [
                    await client.system_one(state=state, questions=chunks[0])
                ]
            else:
                responses = list(
                    await asyncio.gather(
                        *(
                            client.system_one(state=state, questions=chunk)
                            for chunk in chunks
                        )
                    )
                )
        except Exception as e:
            raise JevAPIError(f"Batch 调用失败: {e}") from e

    merged: dict[str, Any] = {}
    for response in responses:
        merged.update(response.answers)

    answers = {}
    for qid in questions:
        answers[qid] = _classify_answer(qid, merged[qid])
    return BatchResult(answers=answers)


# ── 通用模式函数 ──────────────────────────────────────────────


async def select_one(
    state: str | dict,
    instruction: str,
    options: dict[str, str],
    *,
    question_id: str = "selection",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> ChoiceResult:
    """通用单选:从多个选项中选一个最匹配的。

    适用于:skill 选择、意图路由、工具选择、部门分类、文档分类等
    任何"从 N 个互斥选项中选 1 个"的场景。

    Args:
        state: 待评估的内容(如用户输入、文档文本)。
        instruction: 选择标准(告诉模型按什么维度选)。
        options: 选项映射 {选项名: 选项描述}。
        question_id: 答案标识符。
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        ChoiceResult: 包含 choice, probabilities, confidence。
    """
    return await choice(
        state=state,
        instructions=instruction,
        criteria=options,
        question_id=question_id,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )


async def select_many(
    state: str | dict,
    instruction: str,
    options: dict[str, str],
    *,
    question_id_prefix: str = "sel",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> dict[str, NoulResult]:
    """通用多选:判断每个选项是否被选中。

    内部为每个选项生成一个 Noul 问题(是否选中?),
    通过 batch 一次请求完成。适用于:多标签分类、多工具选择、
    关键词提取等"从 N 个选项中选多个"的场景。

    Args:
        state: 待评估的内容。
        instruction: 选择标准。
        options: 候选项映射 {选项名: 选项描述}。
        question_id_prefix: 问题 ID 前缀(内部使用)。
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        dict[str, NoulResult]: 键为选项名,值为 NoulResult。
        noul 值越高表示越可能被选中(调用方自行设定阈值筛选)。
    """
    questions = {}
    for name, desc in options.items():
        qid = f"{question_id_prefix}_{name}"
        criteria_desc = f"选项含义: {desc}" if desc else ""
        questions[qid] = {
            "type": "noul",
            "instructions": (
                f"根据以下标准判断是否选中此项。"
                f"标准: {instruction}。"
                f"选项: {name}。{criteria_desc}"
            ),
        }

    result = await batch(
        state=state,
        questions=questions,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )

    # 重新映射:去掉前缀,用原始选项名作键
    return {
        qid.removeprefix(f"{question_id_prefix}_"): answer
        for qid, answer in result.answers.items()
    }


async def yes_no(
    state: str | dict,
    question: str,
    true_desc: str = "",
    false_desc: str = "",
    *,
    question_id: str = "judgment",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> NoulResult:
    """通用是否判断:评估一个陈述为真的概率。

    适用于:安全检查、目标完成、段落相关性、注入检测、
    重复判断等任何"是或否"的二元判断场景。

    Args:
        state: 待评估的内容。
        question: 是/否问题(应为清晰的判断标准)。
        true_desc: "是"的含义描述(可选,帮助模型理解什么是"是")。
        false_desc: "否"的含义描述(可选,帮助模型理解什么是"否")。
        question_id: 答案标识符。
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        NoulResult: 包含 noul (float 0~1)。
        接近 1=强烈为是,接近 0=强烈为否,接近 0.5=不确定。
    """
    criteria = None
    if true_desc or false_desc:
        criteria = {"true": true_desc or "是", "false": false_desc or "否"}
    return await noul(
        state=state,
        instructions=question,
        criteria=criteria,
        question_id=question_id,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )


async def rate(
    state: str | dict,
    instruction: str,
    levels: list[str],
    *,
    question_id: str = "rating",
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> ScoreResult:
    """通用量表评级:按定义的等级进行评级。

    适用于:风险等级、严重程度、情绪强度、质量评分、
    完成度等任何"在有序量表上打分"的场景。

    Args:
        state: 待评估的内容。
        instruction: 评估标准。
        levels: 有序等级列表,从低到高(如 ["安全", "低风险", "中风险", "高风险"])。
        question_id: 答案标识符。
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        ScoreResult: 包含 score, probabilities, confidence。
        score 为插值后的连续值(如 2.3 表示在第 2 和第 3 级之间)。
    """
    return await score(
        state=state,
        instructions=instruction,
        criteria=levels,
        question_id=question_id,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )


async def multi_judge(
    state: str | dict,
    questions: list[dict],
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float = 60.0,
) -> BatchResult:
    """多维度批量判断:一次请求评估多个不同维度的问题。

    Jev 的批量能力:多个问题在一次请求中并行评估,
    适合需要同时判断多个维度的场景(如安全护栏同时检测多种风险,
    RAG 段落同时评估相关性/可用性/矛盾性/注入性)。

    Args:
        state: 待评估的内容。
        questions: 问题列表,每项为 dict:
            {
                "id": "问题标识",
                "type": "choice"|"score"|"noul",
                "instructions": "评估指令",
                "criteria": ...  # choice=dict, score=list, noul=dict|None
            }
        api_key: TypeSafe API key。
        model: Jev 模型名。
        base_url: API 地址。
        timeout: 请求超时秒数。

    Returns:
        BatchResult: 包含 answers dict,按 question id 索引。
    """
    qdict = {}
    for q in questions:
        qid = q["id"]
        qdict[qid] = {
            "type": q["type"],
            "instructions": q["instructions"],
        }
        if "criteria" in q:
            qdict[qid]["criteria"] = q["criteria"]

    return await batch(
        state=state,
        questions=qdict,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
    )
