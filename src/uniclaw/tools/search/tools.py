"""平台搜索工具 — 支持 GitHub / arXiv / Stack Overflow / Hacker News / B站 / Reddit / Google News / Semantic Scholar / OpenAlex / Polymarket / SEC EDGAR / HuggingFace / alphaXiv / Exa / Bing / DuckDuckGo。"""

import asyncio
import re
import time

from uniclaw.config import AppConfig
from uniclaw.tools.base import tool

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
    semantic_scholar,
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
    "semantic_scholar": semantic_scholar.search,
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


def _split_results(platforms: list[str], results: list[str]) -> tuple[dict, dict]:
    """把各平台原始结果分离为成功与失败两组。

    Args:
        platforms: 平台名列表 (与 results 一一对应)
        results: 各平台原始返回 (可能含 PLATFORM_ERROR 前缀的失败信息)

    Returns:
        (success, errors): success[平台] = 成功文本; errors[平台] = 失败文本
    """
    success, errors = {}, {}
    for p, r in zip(platforms, results):
        if r.startswith(PLATFORM_ERROR):
            errors[p] = r
        else:
            success[p] = r
    return success, errors


def _join_results(success: dict) -> str:
    """把成功平台结果拼成整体文本 (多平台时加分隔线)。"""
    if not success:
        return ""
    if len(success) == 1:
        return next(iter(success.values()))
    parts = []
    for p, r in success.items():
        parts.append(f"\n{'='*20} {p.upper()} {'='*20}\n{r}")
    return "\n".join(parts)


def _count_results(text: str) -> int:
    """从结果文本中粗略统计条目数: 每个条目包含一行缩进的链接行。"""
    if not text:
        return 0
    return len(re.findall(r"(?m)^\s+https?://", text))


def _with_unsupported_hint(platform: str, result: str, *, has_range: bool) -> str:
    """显式传入 time_range 时, 给不支持时间过滤的平台结果附加提示。

    仅在 webSearch 层调用 (平台函数自身不感知), 提示不进缓存;
    未传 time_range 时原样返回, 避免默认搜索混入无关提示。
    """
    if not has_range or platform not in TIME_RANGE_UNSUPPORTED:
        return result
    return f"[提示] {platform} 平台不支持时间范围过滤, 已忽略该参数\n{result}"


async def _rerank_with_llm(
    query: str,
    content: str,
    config: AppConfig | None,
    intent: str = "",
) -> str | None:
    """用 LLM 对超过阈值的结果重排, 剔除低价值项目。失败时返回 None (回退原文本)。

    Args:
        query: 搜索关键词
        content: 各平台成功结果拼接后的整体文本 (由 _join_results 生成)
        config: 应用配置 (用于获取 mini_model_name)
        intent: 搜索意图 (用户要找什么内容), 供 LLM 判断相关度参考, 可为空

    Returns:
        str: 重排后的结果文本; 失败时返回 None
    """
    try:
        from uniclaw.provider.fallback import achat
        from uniclaw.tools.session.session import Session

        model_name = config.mini_model_name if config else ""
        session = Session()
        msg = [f"搜索关键词: {query}"]
        if intent:
            msg.append(f"搜索意图: {intent}")
        msg.append(f"各平台返回的搜索结果:\n\n{content}")
        session.add_user_message(content="\n\n".join(msg))
        response = await achat(
            system_prompt=_RERANK_SYSTEM_PROMPT,
            session=session,
            model_name=model_name,
            config=config,
        )
        text = (response.content or "").strip()
        return text or None
    except Exception:
        return None


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
    config: AppConfig = None,
) -> str:
    """并发搜索多个平台并合并结果。
    结果超过 10 条时自动用 LLM 重新排序并剔除低价值项目,
    失败平台的错误信息会附在末尾。

    Args:
        query: 搜索关键词
        limit: 每个平台返回的结果数量(默认 10)
        sort: 排序方式(平台特有), 如 github: stars/forks/updated,
            arxiv: relevance/lastUpdatedDate
        search_type: 搜索类型(平台特有), 如 github: repositories/code/issues,
            bilibili: video/bangumi
        timeout: 单个平台搜索超时秒数(默认 15), 超时返回错误信息
        intent: 搜索意图 (要找什么内容), 供 LLM 重排时判断相关度参考, 可为空
        platforms: 要搜索的平台, 逗号分隔 (如 "github,arxiv"), 默认 "exa"
            只做通用网页搜索。可选平台: github / arxiv / stackoverflow /
            hackernews / bilibili / reddit / google_news / semantic_scholar /
            openalex / polymarket / sec_edgar / huggingface / alphaxiv / exa /
            bing / duckduckgo。按查询内容针对性选择(找代码选 github,
            查论文选 arxiv/semantic_scholar/openalex, 找视频选 bilibili),
            全部平台太耗时, 仅确需全面调研时传 "all"
        time_range: 时间范围过滤, 查询近期内容时使用。支持预设
            ("1d"/"7d"/"30d"/"90d"/"180d"/"1y"/"all") 或 ISO 区间
            ("2024-01-01..2024-06-30", 单边可省略, 如 "2024-01-01.."),
            映射到各平台原生过滤器(github pushed:, arxiv submittedDate,
            google_news after:, openalex publication_date 等);
            bilibili/polymarket/bing/duckduckgo 不支持, 结果中会附提示
    """
    selected_platforms = _resolve_platforms(platforms)
    total = len(selected_platforms)

    # 入口统一校验: 空 query 快速失败, 避免透传各平台 (github API 会返回
    # 晦涩的 HTTP 422)
    if not query or not query.strip():
        return "错误: 搜索关键词不能为空, 请提供 query 参数"

    # 入口统一校验: 非法 time_range 快速失败, 避免每个平台重复报错
    if time_range.strip():
        parse_time_range(time_range)

    # 并发搜索各平台 (平台级缓存: 命中直接返回, 成功写缓存, 失败不缓存)
    has_range = bool(time_range.strip())

    async def _run(p: str) -> str:
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
            return _with_unsupported_hint(p, hit, has_range=has_range)

        start = time.monotonic()
        searcher = PLATFORM_SEARCHERS[p]
        result = await search_with_timeout(
            searcher,
            query=query,
            limit=limit,
            sort=sort,
            search_type=search_type,
            config=config,
            time_range=time_range,
            timeout=timeout,
        )
        elapsed = time.monotonic() - start
        # 缓存只存干净的搜索结果; 耗时标注在每次真实搜索的返回结果中都附加
        # (成功失败都要算时间)。失败信息保持以 PLATFORM_ERROR 开头, 不缓存,
        # 供 _split_results 识别。
        is_error = result.startswith(PLATFORM_ERROR)
        if not is_error:
            search_cache[p_ck] = result
            # 不支持时间过滤的平台提示每次现拼, 不进缓存
            result = _with_unsupported_hint(p, result, has_range=has_range)
        return f"{result}\n[搜索用时 {elapsed:.1f}秒]"

    # as_completed: 谁先完成先处理, 立即推送该平台结果到 UI, 不等最慢的平台
    from uniclaw.tools.stream import tool_stream

    async def _run_named(p: str) -> tuple[str, str]:
        return p, await _run(p)

    result_map: dict[str, str] = {}
    done_count = 0
    for done in asyncio.as_completed([_run_named(p) for p in selected_platforms]):
        p, result = await done
        done_count += 1
        result_map[p] = result
        # 耗时标注已在 result 末尾, 此处只推送进度 (放在最后一行)
        await tool_stream(f"[{p}]\n{result}\n搜索完成 ({done_count}/{total})\n")
    results = [result_map[p] for p in selected_platforms]
    success, errors = _split_results(selected_platforms, results)

    combined = _join_results(success)

    # 超过阈值时用 LLM 重排、剔除低价值项目 (失败则回退原文本)
    if _count_results(combined) > RERANK_THRESHOLD:
        reranked = await _rerank_with_llm(query, combined, config, intent)
        if reranked:
            combined = reranked

    # 最终返回必须包含失败平台的错误信息
    if errors:
        error_block = "\n".join(f"[{p}]\n{e}" for p, e in errors.items())
        combined = (
            f"{combined}\n\n以下平台搜索失败:\n{error_block}"
            if combined
            else error_block
        )

    return combined


def get_tools() -> list:
    """返回搜索工具列表。"""
    return [webSearch]


def get_all_tools() -> list:
    """返回全部搜索工具。"""
    return [webSearch]
