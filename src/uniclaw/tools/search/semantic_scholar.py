"""Semantic Scholar 搜索实现。"""

import asyncio
import os
from datetime import datetime, timezone

import httpx

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, PLATFORM_ERROR, http_get, safe_search
from .time_range import parse_time_range

NAME = "semantic_scholar"
LABEL = "Semantic Scholar"

_ENDPOINT = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = (
    "title,abstract,url,year,citationCount,influentialCitationCount,"
    "authors,tldr,externalIds,openAccessPdf"
)


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Semantic Scholar 学术搜索 (无需认证, 匿名限 100 req/5min)。"""
    headers = {"User-Agent": DEFAULT_UA}
    key = (
        (config.SEMANTIC_SCHOLAR_API_KEY if config else "")
        or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        or os.environ.get("S2_API_KEY")
    )
    if key:
        headers["x-api-key"] = key

    params = {"query": query, "limit": min(limit, 50), "fields": _FIELDS}
    # year 过滤只有年粒度: 上界取当前年份, 保证开区间预设(如 1y)能覆盖到今年
    tr = parse_time_range(time_range)
    if tr.start or tr.end:
        lo_year = (tr.start or tr.end).year
        hi_year = max((tr.end or datetime.now(timezone.utc)).year, lo_year)
        params["year"] = f"{lo_year}-{hi_year}"
    # api.semanticscholar.org 未被墙, 强制直连: 代理出口 IP 多人共享,
    # 反而更容易撞上共享限流池的 429
    try:
        try:
            r = await http_get(
                _ENDPOINT,
                params=params,
                headers=headers,
                config=config,
                use_proxy=False,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:
                raise
            # 无 key 请求走全局共享限流池, 常态化拥挤: 稍候重试一次
            await asyncio.sleep(3)
            r = await http_get(
                _ENDPOINT,
                params=params,
                headers=headers,
                config=config,
                use_proxy=False,
            )
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return (
                f"{PLATFORM_ERROR}: Semantic Scholar 匿名请求限流 (429)。"
                f"可到 https://www.semanticscholar.org/product/api#api-key-form "
                f"免费申请 API key, 配置为环境变量 SEMANTIC_SCHOLAR_API_KEY "
                f"或 settings.json 后即有独立配额"
            )
        raise
    data = r.json()
    papers = data.get("data") or []
    if not papers:
        return "Semantic Scholar: 无搜索结果"
    lines = [f"**Semantic Scholar 搜索结果** ({len(papers)} 篇):\n"]
    for paper in papers:
        title = (paper.get("title") or "").strip()
        if not title:
            continue
        authors = [a.get("name", "") for a in paper.get("authors") or []]
        author_str = ", ".join(a for a in authors[:3] if a)
        if len(authors) > 3:
            author_str += f", +{len(authors) - 3} more"
        tldr = (paper.get("tldr") or {}).get("text") or ""
        abstract = paper.get("abstract") or ""
        snippet = (tldr or abstract)[:600]
        citations = int(paper.get("citationCount") or 0)
        influential = int(paper.get("influentialCitationCount") or 0)
        url = paper.get("url") or ""
        ext = paper.get("externalIds") or {}
        if not url and ext.get("DOI"):
            url = f"https://doi.org/{ext['DOI']}"
        if not url and ext.get("ArXiv"):
            url = f"https://arxiv.org/abs/{ext['ArXiv']}"
        if not url:
            continue
        year = paper.get("year")
        published = f"{year}" if year else ""
        lines.append(f"**{title}**")
        lines.append(f"  作者: {author_str} | 年份: {published}")
        lines.append(f"  📚{citations} 引用 ({influential} influential)")
        if snippet:
            lines.append(f"  {snippet[:200]}...")
        lines.append(f"  {url}\n")
    return "\n".join(lines)
