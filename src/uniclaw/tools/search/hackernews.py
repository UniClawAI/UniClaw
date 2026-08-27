"""Hacker News 搜索实现。"""

import html
import re

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import epoch, parse_time_range

NAME = "hackernews"
LABEL = "Hacker News"


def _clean_html(text: str) -> str:
    """去除 Algolia 返回文本中的 HTML 标签并反转义实体。"""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())


def _display_title(hit: dict) -> str:
    """生成展示标题。

    Algolia 的命中分两类:
    - story(帖子): 有 title 字段
    - comment(评论): title 为空, 父帖标题在 story_title, 正文在 comment_text
    """
    title = (hit.get("title") or "").strip()
    if title:
        return title
    # comment 类型: 优先显示父帖标题(标注为评论), 死帖则截取评论文本
    story_title = (hit.get("story_title") or "").strip()
    if story_title and story_title.lower() != "[dead]":
        return f"[评论于] {story_title}"
    body = _clean_html(hit.get("comment_text") or "")
    if not body:
        return "(无标题)"
    return f"[评论] {body[:80]}…" if len(body) > 80 else f"[评论] {body}"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Hacker News 搜索 (Algolia API)。"""
    endpoint = "search_by_date" if sort == "date" else "search"
    params = {"query": query, "hitsPerPage": limit}
    tr = parse_time_range(time_range)
    filters = []
    if tr.start:
        filters.append(f"created_at_i>{epoch(tr.start)}")
    if tr.end:
        filters.append(f"created_at_i<{epoch(tr.end)}")
    if filters:
        params["numericFilters"] = ",".join(filters)
    r = await http_get(
        f"https://hn.algolia.com/api/v1/{endpoint}",
        params=params,
        headers={"User-Agent": DEFAULT_UA},
        config=config,
        use_proxy=False,
    )
    data = r.json()
    hits = data.get("hits", [])
    if not hits:
        return "Hacker News: 无搜索结果"
    lines = [f"**Hacker News 搜索结果** ({len(hits)} 条):\n"]
    for hit in hits:
        title = _display_title(hit)
        url_val = hit.get("url", "")
        author = hit.get("author", "")
        points = hit.get("points") or 0
        comments = hit.get("num_comments") or 0
        created = hit.get("created_at", "")[:10]
        hn_link = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        link = url_val or hn_link
        lines.append(f"**{title}** 👍{points} 💬{comments}")
        lines.append(f"  作者: {author} | 日期: {created}")
        lines.append(f"  {link}\n")
    return "\n".join(lines)
