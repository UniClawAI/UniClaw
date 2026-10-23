"""平台搜索工具 — 支持 GitHub / arXiv / Stack Overflow / Hacker News / B站 / Reddit / Google News / OpenAlex / Polymarket / SEC EDGAR / HuggingFace / alphaXiv / Exa / Bing / DuckDuckGo。"""

import asyncio
import re
import time

from uniclaw.config import AppConfig
from uniclaw.console.ui import warn
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR

from .base import PLATFORM_ERROR, cache_key, search_cache, search_with_timeout
from .time_range import parse_time_range

# 每个平台模块暴露统一的 search(query, limit, sort, search_type, config) -> str
from . import (
    alphaxiv,
    arxiv,
    bilibili,
    bing,
    duckduckgo,
    exa,
    github,
    google_news,
    hackernews,
    huggingface,
    openalex,
    polymarket,
    reddit,
    sec_edgar,
    stackoverflow,
)

# 平台路由表: 平台名 -> 搜索函数
PLATFORM_SEARCHERS = {
    "github": github.search,
    "arxiv": arxiv.search,
    "stackoverflow": stackoverflow.search,
    "hackernews": hackernews.search,
    "bilibili": bilibili.search,
    "reddit": reddit.search,
    "google_news": google_news.search,
    "openalex": openalex.search,
    "polymarket": polymarket.search,
    "sec_edgar": sec_edgar.search,
    "huggingface": huggingface.search,
    "alphaxiv": alphaxiv.search,
    "exa": exa.search,
    "bing": bing.search,
    "duckduckgo": duckduckgo.search,
}

# 超过该条数时使用 LLM 重新排序并剔除低价值项目
RERANK_THRESHOLD = 10

# 不支持时间范围过滤的平台 (显式传 time_range 时在结果前附加提示)
# bing 已实现 freshness 映射, 从该集合移除
TIME_RANGE_UNSUPPORTED = frozenset({"bilibili", "polymarket", "duckduckgo"})


def _resolve_platforms(platforms_str: str) -> list[str]:
    """解析平台筛选参数: 逗号分隔的平台名列表, 空或 'all' 表示全部。

    Args:
        platforms_str: 逗号分隔的平台名 (如 "github,arxiv"), 或 "all"/空字符串

    Returns:
        list[str]: 要搜索的平台名列表 (始终非空, 无效名被忽略)

    Raises:
        ValueError: 提供的平台名全部无效
    """
    if not platforms_str or platforms_str.strip().lower() == "all":
        return list(PLATFORM_SEARCHERS.keys())
    selected = []
    for name in platforms_str.split(","):
        name = name.strip().lower()
        if name in PLATFORM_SEARCHERS:
            selected.append(name)
    if not selected:
        raise ValueError(
            f"未找到可用的平台: '{platforms_str}'。"
            f"支持: {', '.join(PLATFORM_SEARCHERS.keys())}"
        )
    return selected


_RERANK_SYSTEM_PROMPT = (
    "你是一个搜索结果精炼助手。用户会给你一段多平台搜索结果文本, 每个平台的结果以分隔线标注。"
    "你的任务: 剔除与查询无关、重复或价值低的结果, 剩余结果按平台分组: "
    "各平台整体按与查询的相关度从高到低排序, 同一平台内的结果也按相关度从高到低排序。"
    "只输出整理后的结果列表, 保留原有的平台分隔线标注, 每条结果必须完整保留原有格式: 标题、链接, "
    "以及该结果的所有元数据字段(如评分、作者、日期、引用数、来源、摘要等), 一律不得删减。"
    "不要添加任何解释或开头语。"
    "每个平台分组的末尾添加一行统计(不要空行): ═══ 本平台过滤掉 X 条无关信息 ═══ "
    "(X 为该平台被剔除的条目数; 全部保留则写 0)。"
    "若某平台的条目全部被剔除, 该平台分隔线下不保留任何条目, 改为添加一行: "
    "═══ 本平台 N 条结果全部为无关信息, 已剔除 ═══ (N 为原条目数)。"
)


def _split_results(
    platforms: list[str], results: list[list[str]]
) -> tuple[dict, dict]:
    """把各平台原始结果分离为成功与失败两组。

    Args:
        platforms: 平台名列表 (与 results 一一对应)
        results: 各平台原始返回 (list[str]; 失败时首元素带 PLATFORM_ERROR 前缀)

    Returns:
        (success, errors): success[平台] = 成功结果列表; errors[平台] = 失败文本列表
    """
    success, errors = {}, {}
    for p, r in zip(platforms, results):
        if not r:
            # 防御: 平台返回空列表(理论边界, 正常路径不会发生)。
            # 归入失败并给出明确文案, 避免产生"只有分隔行"的孤岛。
            errors[p] = ["[PLATFORM_ERROR]: 平台返回空结果"]
        elif r[0].startswith(PLATFORM_ERROR):
            errors[p] = r
        else:
            success[p] = r
    return success, errors


def _join_results(success: dict) -> list[str]:
    """把成功平台结果拼成整体列表 (多平台时插入平台分隔行)。

    单平台时直接返回该平台列表(首元素为平台标题行);
    多平台时在每平台结果前插入 `===== PLATFORM =====` 分隔行。
    """
    if not success:
        return []
    if len(success) == 1:
        return next(iter(success.values()))
    parts: list[str] = []
    for p, r in success.items():
        parts.append(f"{'='*20} {p.upper()} {'='*20}")
        parts.extend(r)
    return parts


def _count_results(text: str | list[str]) -> int:
    """粗略统计结果条目数: 含 URL 的元素/行计为一条。"""
    if isinstance(text, str):
        return len(re.findall(r"(?m)^\s+https?://", text)) if text else 0
    return sum(1 for item in text if re.search(r"https?://", item))


def _has_items(text: str | list[str]) -> bool:
    """内容里是否含实际结果条目(而非只有平台分隔头/空壳)。

    判定标准是出现 URL —— 各平台条目都带链接, 而多平台空结果只拼出
    `==== PLATFORM ====` 分隔头。不能用 _count_results(按"缩进链接行"启发式
    统计, 部分平台格式不含这种行时恒为 0)。
    """
    if isinstance(text, str):
        return bool(text) and bool(re.search(r"https?://", text))
    return any(re.search(r"https?://", item) for item in text)


def _has_literal_overlap(query: str, text: str | list[str]) -> bool:
    """查询词与结果内容是否有字面重合(4 字符连续子串)。

    用 n-gram 而不是整词当子串: 整词匹配对随机串几乎不可能命中(分支形同虚设),
    而平台兜底页常含查询碎片 —— 4 字符连续子串是"真有关系"的合理下限。
    短于 4 字符的词元退化为整词匹配。

    短查询(<=2 token)与含中文的查询跳过字面重合检查、直接视为相关:
    - 短查询(如 "AI")结果标题常用同义/展开写法(人工智能), 字面判定误杀率极高;
    - 中文同义表达是常态(新特性/新变化), 与查询词字面不一致很常见。
    """
    tokens = _query_tokens(query)
    if any("一" <= ch <= "鿿" for t in tokens for ch in t):
        # 中文查询: 中文同义表达是常态, 字面判定误杀率高, 不过滤 (语义由 JEV/LLM 重排把关)
        return True
    if tokens and all(len(t) <= 3 for t in tokens):
        # 全为极短词 (<=3 字符, 如 AI/MCP): 短词字面重合判定无意义, 不过滤
        return True
    if not tokens:
        return True
    items = [text] if isinstance(text, str) else text
    for tok in tokens:
        n = 4 if len(tok) >= 4 else len(tok)
        if n <= 0:
            continue
        for item in items:
            low = item.lower()
            if any(tok[i : i + n] in low for i in range(len(tok) - n + 1)):
                return True
    return False


def _query_tokens(query: str) -> list[str]:
    """提取查询词 token, 用于结果相关性粗筛(与 bing._tokens 同思路)。

    - ASCII 词: 长度 >= 3
    - 中文连续段: 长度 >= 4 取相邻二字组合, 否则整段
    """
    tokens: list[str] = []
    for raw in query.split():
        for word in re.findall(r"[A-Za-z0-9]{3,}", raw):
            tokens.append(word.lower())
        for cjk in re.findall(r"[一-鿿]{2,}", raw):
            if len(cjk) >= 4:
                tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
            else:
                tokens.append(cjk)
    return list(dict.fromkeys(tokens))


def _with_unsupported_hint(
    platform: str, result: list[str], *, has_range: bool
) -> list[str]:
    """显式传入 time_range 时, 给不支持时间过滤的平台结果附加提示。

    仅在 webSearch 层调用 (平台函数自身不感知), 提示不进缓存;
    未传 time_range 时原样返回, 避免默认搜索混入无关提示。
    """
    if not has_range or platform not in TIME_RANGE_UNSUPPORTED:
        return result
    return [f"[提示] {platform} 平台不支持时间范围过滤, 已忽略该参数", *result]


async def _rerank_with_llm(
    query: str,
    content: str | list[str],
    config: AppConfig | None,
    intent: str = "",
) -> str | None:
    """用 LLM 对超过阈值的结果重排, 剔除低价值项目。失败时返回 None (回退原文本)。

    输入拼接成整体文本交给 LLM; 输出是 LLM 精炼后的整体文本 (收敛为单条)。

    Args:
        query: 搜索关键词
        content: 各平台成功结果拼接后的整体内容 (str 或 list[str])
        config: 应用配置 (用于获取 mini_model_name)
        intent: 搜索意图 (用户要找什么内容), 供 LLM 判断相关度参考, 可为空

    Returns:
        str: 重排后的结果文本; 失败时返回 None
    """
    from uniclaw.provider.fallback import achat
    from uniclaw.tools.session.session import Session

    combined = (
        content
        if isinstance(content, str)
        else "\n\n".join(content)
    )
    model_name = config.mini_model_name if config else ""
    session = Session()
    msg = [f"搜索关键词: {query}"]
    if intent:
        msg.append(f"搜索意图: {intent}")
    msg.append(f"各平台返回的搜索结果:\n\n{combined}")
    session.add_user_message(content="\n\n".join(msg))

    try:
        response = await achat(
            system_prompt=_RERANK_SYSTEM_PROMPT,
            session=session,
            model_name=model_name,
            config=config,
        )
    except Exception as e:
        # 非 bug: LLM 调用失败(模型全挂/网络不通/鉴权失败)。重排是纯增强,
        # 失败回退原文本即可 —— 但必须留痕, 否则重排静默失效难以排查。
        # try 只包 achat: 上面的会话构建、下面的 response 解析都是自家代码,
        # 那里的 KeyError/AttributeError 是 bug, 按约定要抛给 Tool.__call__
        await warn(f"LLM 重排失败: {type(e).__name__}: {e}", config)
        return None

    text = (response.content or "").strip()
    return text or None


# JEV 相关性判断阈值: noul >= 该值视为"与查询相关", 保留条目
_RERANK_JEV_KEEP_THRESHOLD = 0.5

# 单个结果条目送入 JEV state 的最大字符数(截断过长条目, 控预算与信息密度)
_RERANK_JEV_ITEM_MAX_CHARS = 500


async def _rerank_with_jev(
    query: str,
    content: str | list[str],
    config: AppConfig | None,
    intent: str = "",
) -> list[str] | None:
    """用 JEV 对结果逐条判断相关性并按分排序。失败/不可用时返回 None (回退 LLM)。

    与 LLM 版(整块精炼)不同: JEV 用 select_many 对**每条结果条目**单独做
    相关性判断(noul), 然后剔除低于阈值的条目、按分数降序重排 ——
    保持"每条结果一个 str"的结构化形态, 而不是收敛为整块文本。

    只对含 URL 的结果条目打分(分隔行/平台标题行/提示行/无结果文本不参与);
    结果列表由排序后的条目组成(丢弃分隔行 —— 顺序已代表相关度);
    无结果平台的占位信息(如 "GitHub: 无搜索结果")保留在结果末尾。

    Args:
        query: 搜索关键词
        content: 各平台成功结果拼接后的整体内容 (str 或 list[str])
        config: 应用配置 (用于获取 proxy_url)
        intent: 搜索意图 (用户要找什么内容), 供 JEV 判断相关度参考, 可为空

    Returns:
        list[str] | None: 重排后的结果列表 (每条一个 str); JEV 不可用或失败时返回 None
    """
    from uniclaw.utils.jev import is_available, select_many

    if not is_available():
        return None

    # 兼容 str 输入 (与 _rerank_with_llm 签名一致)
    if isinstance(content, str):
        content = [content]

    # 提取含 URL 的结果条目; 分隔行/标题行/提示行/无结果文本不含 URL, 不参与打分
    items = [item for item in content if re.search(r"https?://", item)]
    if not items:
        return None

    # 每条结果截断后作为选项; 键用序号字符串, select_many 返回按键索引的 noul
    options = {}
    for i, item in enumerate(items):
        # 截断: 保留标题与链接等关键信息; 超长摘要截掉
        snippet = item if len(item) <= _RERANK_JEV_ITEM_MAX_CHARS else (
            item[: _RERANK_JEV_ITEM_MAX_CHARS] + "…"
        )
        options[str(i)] = snippet

    state = f"搜索关键词: {query}"
    if intent:
        state += f"\n搜索意图: {intent}"

    try:
        results = await select_many(
            state=state,
            instruction="这条搜索结果是否与查询相关、是用户需要的内容。"
            "关注标题、摘要与链接是否包含查询所需信息。",
            options=options,
            proxy_url=config.proxy_url if config else None,
        )
    except Exception as e:
        # 非 bug: JEV 调用失败(网络/限流/超预算/未配置)或不可用。
        # 重排是纯增强, 失败回退 LLM 即可 —— 但必须留痕, 否则静默失效难排查
        await warn(f"JEV 重排失败, 回退 LLM: {type(e).__name__}: {e}", config)
        return None

    # 按 noul 分数: 低于阈值剔除, 其余按分数降序重排 (分数相同保持原序)
    # select_many 返回 dict[str, NoulResult] (键为选项名)
    scored = []
    for key, noul_result in results.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue  # bug 级: select_many 返回意外键, 跳过不影响主体
        if 0 <= idx < len(items):
            scored.append((noul_result.noul, items[idx]))

    # 过滤后按分数降序重排; Python sort 稳定, 同分保持原序
    ranked = [item for _, item in sorted(
        ((s, it) for s, it in scored if s >= _RERANK_JEV_KEEP_THRESHOLD),
        key=lambda pair: pair[0],
        reverse=True,
    )]

    # 全部被剔除时返回 None, 让调用方回退 LLM 版 (LLM 能输出"全部无关"的明确文案)
    if not ranked:
        return None

    return ranked


def _non_ranked_items(success: dict) -> list[str]:
    """提取 JEV 重排无法打分、但必须保留的非结果元素。

    两类内容不含 URL, 不参与 JEV 逐条打分, 重排后需补回, 否则会静默丢失:
    1. 无结果平台的状态文本(平台整体无任何 URL 条目):
       如 "arXiv: 无搜索结果" / "(无输出)" —— 平台被搜索过但无结果。
    2. 有结果平台中的个别无 URL 条目(如 bilibili 缺 bvid 导致链接为空):
       平台有其它含 URL 条目, 但这一条缺 URL —— 内容仍有价值, 不应丢弃。

    判断: 对每个平台, 先看整体是否无 URL(→ 整个平台是"无结果"状态);
    否则逐个元素挑出不含 URL 的成员(→ 有结果平台中的残缺条目)。
    """
    texts: list[str] = []
    for r in success.values():
        if not any(re.search(r"https?://", item) for item in r):
            # 整个平台无 URL: 提取全部非 URL 元素(状态文本 + 平台标题行)
            texts.extend(item for item in r if not re.search(r"https?://", item))
        else:
            # 平台有 URL 条目: 只挑不含 URL 的残缺结果条目。
            # 排除平台标题行(形如 "**X 搜索结果** (N 条):", 含"搜索结果"字样),
            # 保留残缺条目(形如 "**具体标题**\n  UP主:...", 不含"搜索结果"字样)。
            texts.extend(
                item for item in r
                if not re.search(r"https?://", item)
                and "搜索结果" not in item
            )
    return texts


@tool
async def webSearch(
    query: str,
    limit: int = 10,
    sort: str = "",
    search_type: str = "",
    timeout: int = 15,
    intent: str = "",
    platforms: str = "exa",
    time_range: str = "",
    tool_runtime: ToolRuntime = None,
) -> str:
    """并发搜索多个平台并合并结果。
    结果超过 10 条时自动重排并剔除低价值项目 (优先用 JEV 逐条判断相关性,
    不可用或失败时回退 LLM 整块精炼), 失败平台的错误信息会附在末尾。
    返回拼接好的搜索结果文本 (各结果条目之间空行分隔)。

    Args:
        query: 搜索关键词
        limit: 每个平台返回的结果数量(默认 10)
        sort: 排序方式(平台特有), 如 github: stars/forks/updated,
            arxiv: relevance/lastUpdatedDate
        search_type: 搜索类型(平台特有), 如 github: repositories/code/issues,
            bilibili: video/bangumi, huggingface: papers(默认)/models/datasets
        timeout: 单个平台搜索超时秒数(默认 15), 超时返回错误信息
        intent: 搜索意图 (要找什么内容), 供 LLM 重排时判断相关度参考, 可为空
        platforms: 要搜索的平台, 逗号分隔 (如 "github,arxiv"), 默认 "exa"
            只做通用网页搜索。可选平台: github / arxiv / stackoverflow /
            hackernews / bilibili / reddit / google_news / openalex /
            polymarket / sec_edgar / huggingface / alphaxiv / exa /
            bing / duckduckgo。按查询内容针对性选择(找代码选 github,
            查论文选 arxiv/openalex, 找视频选 bilibili,
            搜模型/数据集选 huggingface + search_type=models/datasets),
            全部平台太耗时, 仅确需全面调研时传 "all"
        time_range: 时间范围过滤, 查询近期内容时使用。支持预设
            ("1d"/"7d"/"30d"/"90d"/"180d"/"1y"/"all") 或 ISO 区间
            ("2024-01-01..2024-06-30", 单边可省略, 如 "2024-01-01.."),
            映射到各平台原生过滤器(github pushed:, arxiv submittedDate,
            google_news after:, openalex publication_date 等);
            bilibili/polymarket/bing/duckduckgo 不支持, 结果中会附提示

    Returns:
        str: 拼接好的搜索结果文本 (各结果条目之间空行分隔)
    """
    if limit < 1:
        return (
            f"{TOOL_ERROR}: limit 必须 >= 1(收到: {limit});"
            f"limit=0 不会返回任何结果,请传正整数或省略该参数(默认 10)"
        )

    selected_platforms = _resolve_platforms(platforms)
    total = len(selected_platforms)

    config = tool_runtime.config

    # 入口统一校验: 空 query 快速失败, 避免透传各平台 (github API 会返回
    # 晦涩的 HTTP 422)
    if not query or not query.strip():
        return f"{TOOL_ERROR}: 搜索关键词不能为空, 请提供 query 参数"

    # 乱码/纯符号查询: 各平台对无匹配查询会返回兜底推荐
    # (如"随机生成器"类页面), 直接如实报未找到, 不透传垃圾结果
    if not re.search(r"\w", query):
        return f"未找到与 {query!r} 相关的结果(查询词不含有效关键词)"

    # 入口统一校验: 非法 time_range 快速失败, 避免每个平台重复报错
    if time_range.strip():
        parse_time_range(time_range)

    # 并发搜索各平台 (平台级缓存: 命中直接返回, 成功写缓存, 失败不缓存)
    has_range = bool(time_range.strip())

    async def _run(p: str) -> tuple[list[str], float]:
        """搜索单平台, 返回 (结果列表, 耗时秒数)。

        结果列表只含真实内容 (平台标题/结果条目/无结果提示/PLATFORM_ERROR;
        耗时标注不进入列表 —— 它只在流式推送中展示, 保证"每条结果一个 str"
        的纯粹性, 也让缓存命中与真实搜索的返回结构完全一致。
        耗时秒数供流式推送展示, 缓存命中时为 0.0。
        """
        p_ck = cache_key(
            query,
            p,
            limit=limit,
            sort=sort,
            search_type=search_type,
            time_range=time_range,
        )
        hit = search_cache.get(p_ck)
        if hit is not None:
            return _with_unsupported_hint(p, hit, has_range=has_range), 0.0

        start = time.monotonic()
        searcher = PLATFORM_SEARCHERS[p]
        # 个别平台对特定参数组合需要更长的等待:
        # arxiv + time_range 的日期范围查询实测极慢 (15s 几乎必超时),
        # 内部 http 超时已放大到 60s, 外层 wait_for 不能先把它杀了
        eff_timeout = timeout
        if p == "arxiv" and has_range:
            eff_timeout = max(timeout, 75)
        result = await search_with_timeout(
            searcher,
            query=query,
            limit=limit,
            sort=sort,
            search_type=search_type,
            config=config,
            time_range=time_range,
            timeout=eff_timeout,
        )
        elapsed = time.monotonic() - start
        # 缓存只存干净的搜索结果; 失败信息保持以 PLATFORM_ERROR 开头, 不缓存,
        # 供 _split_results 识别。
        is_error = bool(result) and result[0].startswith(PLATFORM_ERROR)
        if not is_error:
            search_cache[p_ck] = result
            # 不支持时间过滤的平台提示每次现拼, 不进缓存
            result = _with_unsupported_hint(p, result, has_range=has_range)
        return result, elapsed

    # as_completed: 谁先完成先处理, 立即推送该平台结果到 UI, 不等最慢的平台
    async def _run_named(p: str) -> tuple[str, list[str], float]:
        r, elapsed = await _run(p)
        return p, r, elapsed

    result_map: dict[str, list[str]] = {}
    done_count = 0
    for done in asyncio.as_completed([_run_named(p) for p in selected_platforms]):
        p, result, elapsed = await done
        done_count += 1
        result_map[p] = result
        # 结果与耗时都推送到 UI (耗时只作进度展示, 不进返回列表)
        await tool_runtime.stream(
            f"[{p}]\n{'\n'.join(result)}\n"
            f"[搜索用时 {elapsed:.1f}秒] 搜索完成 ({done_count}/{total})\n"
        )
    results = [result_map[p] for p in selected_platforms]
    success, errors = _split_results(selected_platforms, results)

    combined = _join_results(success)

    # 超过阈值时重排、剔除低价值项目:
    # 优先 JEV 逐条判断相关性 (保持"每条结果一个 str"的结构化形态);
    # JEV 不可用或失败时回退 LLM 版 (整块精炼, 收敛为单条文本)。
    # 重排前提取 JEV 无法打分的非结果元素(无结果平台状态/无 URL 残缺条目),
    # JEV 版重排只保留排序结果会丢弃它们, 需在成功后补回;
    # LLM 版会自行处理这些内容, 无需补回。
    reranked = None
    if _count_results(combined) > RERANK_THRESHOLD:
        non_ranked = _non_ranked_items(success)
        reranked = await _rerank_with_jev(query, combined, config, intent)
        if reranked:
            # JEV 版: 排序后的结果列表, 每条一个 str; 补回非结果元素
            combined = [*reranked, *non_ranked]
        else:
            reranked = await _rerank_with_llm(query, combined, config, intent)
            if reranked:
                # LLM 版: 精炼后的整体文本, 收敛为单条结果
                combined = [reranked]

    # 平台对无匹配查询会返回兜底/近邻结果(如"随机生成器"类推荐页、whois/主页)。
    # 查询词与结果零字面重合 = 无语义价值, 直接报未找到, 不透传垃圾结果。
    # 两个要点:
    #   1. 保留 `reranked is None` 逃生口 —— JEV/LLM 重排是语义筛过的结果, 信任它;
    #   2. 只在**确实有结果条目**时才判, 且判定用 _has_items 而非 _count_results
    #      —— 后者按"缩进链接行"启发式统计, 部分平台格式不含这种行时恒为 0,
    #      判定整条被跳过(实测噪声串因此全部漏过)。
    if (
        reranked is None
        and combined
        and _has_items(combined)
        and not _has_literal_overlap(query, combined)
    ):
        return f"未找到与 {query!r} 相关的结果(返回结果与查询词零字面重合, 已过滤)"

    # 最终返回必须包含失败平台的错误信息
    if errors:
        # errors[p] 是 list[str]: 每条错误信息一个元素
        error_block = "\n\n".join(
            f"[{p}]\n" + "\n".join(e) for p, e in errors.items()
        )
        combined = [
            *combined,
            "以下平台搜索失败:",
            error_block,
        ] if combined else [error_block]

    # 拼接为文本返回: 各结果条目/平台块之间空行分隔
    return "\n\n".join(combined)


def get_tools() -> list:
    """返回搜索工具列表。"""
    return [webSearch]


def get_all_tools() -> list:
    """返回全部搜索工具。"""
    return [webSearch]
