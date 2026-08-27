"""HuggingFace Papers 搜索实现。"""

import os
from datetime import date

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import parse_time_range

NAME = "huggingface"
LABEL = "HuggingFace"

_ENDPOINT = "https://huggingface.co/api/daily_papers"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """HuggingFace 每日论文搜索 (公开 API, 无需认证, 客户端按主题过滤)。"""
    headers = {"User-Agent": DEFAULT_UA}
    token = (
        (config.hf_token if config else "")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGINGFACE_TOKEN")
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = await http_get(_ENDPOINT, headers=headers, config=config, use_proxy=False)
    data = r.json()
    if not isinstance(data, list):
        return "HuggingFace: 无搜索结果"

    q_terms = [t.lower() for t in query.split() if len(t) > 2]
    if not q_terms:
        q_terms = [query.lower().strip()]

    # API 不支持服务端时间过滤, 客户端按发布日期过滤
    tr = parse_time_range(time_range)

    lines = []
    count = 0
    for item in data:
        paper = item.get("paper") or {}
        title = (paper.get("title") or item.get("title") or "").strip()
        if not title:
            continue
        summary = paper.get("summary") or item.get("summary") or ""
        hay = (title + " " + summary).lower()
        if not any(t in hay for t in q_terms):
            continue
        if tr.start or tr.end:
            raw_date = (
                paper.get("publishedAt")
                or paper.get("submittedOnDailyAt")
                or item.get("publishedAt")
                or ""
            )
            try:
                d = date.fromisoformat(raw_date[:10])
            except ValueError:
                d = None  # 日期缺失/无法解析时保留条目, 避免误杀
            if d is not None:
                if tr.start and d < tr.start.date():
                    continue
                if tr.end and d > tr.end.date():
                    continue
        arxiv_id = paper.get("id") or ""
        url = f"https://huggingface.co/papers/{arxiv_id}" if arxiv_id else ""
        if not url:
            continue
        upvotes = int(paper.get("upvotes") or 0)
        num_comments = int(item.get("numComments") or 0)
        authors = [a.get("name", "") for a in (paper.get("authors") or [])]
        author_str = ", ".join(a for a in authors[:3] if a)
        if len(authors) > 3:
            author_str += f", +{len(authors) - 3} more"
        published = (
            paper.get("publishedAt")
            or paper.get("submittedOnDailyAt")
            or item.get("publishedAt")
            or ""
        )[:10]
        lines.append(f"**{title}**")
        lines.append(
            f"  作者: {author_str} | 日期: {published} | 👍{upvotes} 💬{num_comments}"
        )
        if summary:
            lines.append(f"  {summary.strip().replace(chr(10), ' ')[:200]}...")
        lines.append(f"  {url}\n")
        count += 1
        if count >= limit:
            break

    if not lines:
        return "HuggingFace: 无搜索结果"
    return f"**HuggingFace 搜索结果** ({count} 篇):\n\n" + "\n".join(lines)
