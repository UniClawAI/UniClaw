"""Google News 搜索实现。"""

import re
import xml.etree.ElementTree as ET

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import iso_date, parse_time_range

NAME = "google_news"
LABEL = "Google News"

_ENDPOINT = "https://news.google.com/rss/search"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Google News 搜索 (公开 RSS, 支持多语言, 无需认证)。"""
    # hl/gl/ceid: 语言/地区, 可通过 config 覆盖
    hl = config.google_news_hl if config else ""
    gl = config.google_news_gl if config else ""
    ceid = config.google_news_ceid if config else ""
    hl = hl or "zh-CN"
    gl = gl or "CN"
    ceid = ceid or "CN:zh"
    # 时间过滤通过 RSS 搜索的原生 after:/before: 查询操作符实现
    tr = parse_time_range(time_range)
    q_param = query
    if tr.start:
        q_param += f" after:{iso_date(tr.start)}"
    if tr.end:
        q_param += f" before:{iso_date(tr.end)}"
    r = await http_get(
        _ENDPOINT,
        params={"q": q_param, "hl": hl, "gl": gl, "ceid": ceid},
        headers={"User-Agent": DEFAULT_UA},
        config=config,
        use_proxy=True,
    )
    root = ET.fromstring(r.text)
    channel = root.find("channel")
    if channel is None:
        return "Google News: 无搜索结果"
    items = channel.findall("item")[:limit]
    if not items:
        return "Google News: 无搜索结果"
    lines = [f"**Google News 搜索结果** ({len(items)} 条):\n"]
    for item in items:
        title = _text(item, "title")
        link = _text(item, "link")
        pub = _text(item, "pubDate")
        desc = _text(item, "description")
        source_el = item.find("source")
        source_name = source_el.text if source_el is not None and source_el.text else ""
        desc_plain = re.sub(r"<[^>]+>", " ", desc)
        desc_plain = re.sub(r"\s+", " ", desc_plain).strip()[:400]
        lines.append(f"**{title}**")
        lines.append(f"  来源: {source_name} | 日期: {pub}")
        if desc_plain:
            lines.append(f"  {desc_plain}")
        lines.append(f"  {link}\n")
    return "\n".join(lines)


def _text(node, path: str) -> str:
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else ""
