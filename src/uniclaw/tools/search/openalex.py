"""OpenAlex 搜索实现。"""

import os

import httpx

from uniclaw.config import AppConfig

from .base import (
    DEFAULT_UA,
    PLATFORM_ERROR,
    http_get_with_retry,
    safe_search,
)
from .time_range import iso_date, parse_time_range

NAME = "openalex"
LABEL = "OpenAlex"

_ENDPOINT = "https://api.openalex.org/works"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """OpenAlex 学术搜索 (250M+ 论文, 开放免费, 无需认证)。"""
    params = {
        "search": query,
        "per-page": min(limit, 50),
        "sort": "relevance_score:desc",
    }
    tr = parse_time_range(time_range)
    filters = []
    if tr.start:
        filters.append(f"from_publication_date:{iso_date(tr.start)}")
    if tr.end:
        filters.append(f"to_publication_date:{iso_date(tr.end)}")
    if filters:
        params["filter"] = ",".join(filters)
    email = (config.research_email if config else "") or os.environ.get(
        "OPENALEX_EMAIL"
    )
    if email:
        params["mailto"] = email
    # OpenAlex 匿名请求偶发 429, 内部指数退避重试 (2s → 4s)
    try:
        r = await http_get_with_retry(
            _ENDPOINT,
            params=params,
            headers={"User-Agent": DEFAULT_UA},
            config=config,
            use_proxy=False,
            retries=2,
            backoff=2.0,
        )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return (
                f"{PLATFORM_ERROR}: OpenAlex 请求限流 (429)。"
                f"匿名配额当前拥挤, 已自动重试仍失败, 可稍后重试; "
                f"配置 research_email 或 OPENALEX_EMAIL 可提升配额"
            )
        raise
    data = r.json()
    works = data.get("results") or []
    if not works:
        return "OpenAlex: 无搜索结果"
    lines = [f"**OpenAlex 搜索结果** ({len(works)} 篇):\n"]
    for work in works:
        title = (work.get("title") or work.get("display_name") or "").strip()
        if not title:
            continue
        url = (
            work.get("doi")
            or (work.get("primary_location") or {}).get("landing_page_url")
            or work.get("id")
            or ""
        )
        if not url:
            continue
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in (work.get("authorships") or [])
        ]
        author_str = ", ".join(a for a in authors[:3] if a)
        if len(authors) > 3:
            author_str += f", +{len(authors) - 3} more"
        citations = int(work.get("cited_by_count") or 0)
        year = work.get("publication_year")
        published = f"{year}" if year else (work.get("publication_date") or "")
        abstract_idx = work.get("abstract_inverted_index") or {}
        abstract = _reconstruct_abstract(abstract_idx)[:600]
        venue = ((work.get("primary_location") or {}).get("source") or {}).get(
            "display_name", ""
        )
        lines.append(f"**{title}**")
        lines.append(f"  作者: {author_str} | 年份: {published}")
        lines.append(f"  📚{citations} 引用")
        if venue:
            lines.append(f"  期刊: {venue}")
        if abstract:
            lines.append(f"  {abstract[:200]}...")
        lines.append(f"  {url}\n")
    return "\n".join(lines)


def _reconstruct_abstract(idx: dict) -> str:
    """OpenAlex 将摘要以 {word: [positions]} 倒排索引形式提供, 需重建原文。"""
    if not idx:
        return ""
    words_at: dict[int, str] = {}
    for word, positions in idx.items():
        for p in positions:
            words_at[p] = word
    if not words_at:
        return ""
    ordered = [words_at[i] for i in sorted(words_at.keys())]
    return " ".join(ordered)
