"""B站搜索实现。"""

import re

import httpx

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, PLATFORM_ERROR, client_kwargs, safe_search

NAME = "bilibili"
LABEL = "B站"

_VALID_TYPES = ("video", "bangumi", "pgc", "live", "article")


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """B站搜索 (API)。"""
    st = search_type if search_type in _VALID_TYPES else "video"
    url = "https://api.bilibili.com/x/web-interface/search/type"
    headers = {
        "User-Agent": DEFAULT_UA,
        "Referer": "https://search.bilibili.com",
    }
    async with httpx.AsyncClient(
        **client_kwargs(config, use_proxy=False), timeout=15, follow_redirects=True
    ) as client:
        # 先访问搜索页获取必要的 cookie (buvid3 等)
        await client.get("https://search.bilibili.com", headers=headers)
        r = await client.get(
            url,
            params={
                "search_type": st,
                "keyword": query,
                "page": 1,
                "page_size": limit,
            },
            headers=headers,
        )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        return f"{PLATFORM_ERROR}: {data.get('message', '未知错误')}"
    results = data.get("data", {}).get("result", [])
    if not results:
        return "B站: 无搜索结果"
    lines = [f"**B站搜索结果** ({st}, {len(results)} 条):\n"]
    for item in results[:limit]:
        title = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
        author = item.get("author", "") or item.get("uname", "")
        play = item.get("play", 0)
        danmaku = item.get("video_review", 0) or item.get("danmaku", 0)
        desc = (item.get("description", "") or "")[:80]
        bvid = item.get("bvid", "")
        link = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        lines.append(f"**{title}**")
        lines.append(f"  UP主: {author} | 播放: {play} | 弹幕: {danmaku}")
        if desc:
            lines.append(f"  简介: {desc}")
        lines.append(f"  {link}\n")
    return "\n".join(lines)
