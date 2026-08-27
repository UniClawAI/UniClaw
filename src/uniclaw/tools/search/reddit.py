"""Reddit 搜索实现 (公开 RSS 端点)。

www.reddit.com 的 .json 端点对无认证请求一律返回 403 (Cloudflare 拦截,
换 UA / 代理均无效), 但 .rss 端点仍然开放。RSS (Atom) 不含点赞/评论数,
其余字段 (标题/链接/版块/作者/时间/正文) 齐全。
"""

import html as html_mod
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search
from .time_range import parse_time_range, reddit_t

NAME = "reddit"
LABEL = "Reddit"

# Reddit 搜索端点接受的 sort 值 (RSS 端点支持 subset)
_VALID_SORTS = ("relevance", "hot", "top", "new", "comments")

_ATOM = "{http://www.w3.org/2005/Atom}"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(content: str) -> str:
    """从 Atom content (HTML 转义文本) 提取纯文本摘要。"""
    if not content:
        return ""
    text = html_mod.unescape(content)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Reddit 站内搜索 (公开 RSS 端点, 无需认证)。"""
    # 未传 time_range 时维持历史默认 month; 传入时映射到最近的 t= 桶
    t_val = reddit_t(parse_time_range(time_range)) if time_range.strip() else "month"
    # 使用用户传入的 sort, 无效值回退 relevance (此前硬编码为 relevance 导致用户传入的 sort 被忽略)
    s = sort if sort in _VALID_SORTS else "relevance"
    # RSS 端点未被墙, 强制直连: 代理出口 IP 是机房地址, 更容易被 Cloudflare 拦
    r = await http_get(
        "https://www.reddit.com/search.rss",
        params={
            "q": query,
            "limit": min(limit, 50),
            "sort": s,
            "t": t_val,
        },
        headers={"User-Agent": DEFAULT_UA},
        config=config,
        use_proxy=False,
    )
    root = ET.fromstring(r.text)
    entries = root.findall(f"{_ATOM}entry")
    # 只保留真实帖子 (id 形如 t3_xxx); 版块信息卡为 t5_xxx
    posts = [e for e in entries if (e.findtext(f"{_ATOM}id") or "").startswith("t3_")]
    if not posts:
        return "Reddit: 无搜索结果"
    lines = [f"**Reddit 搜索结果** ({len(posts)} 条):\n"]
    for e in posts:
        title = (e.findtext(f"{_ATOM}title") or "").strip()
        link_el = e.find(f"{_ATOM}link")
        link = (link_el.get("href") if link_el is not None else "") or ""
        category = e.find(f"{_ATOM}category")
        subreddit = (category.get("term") if category is not None else "") or ""
        author = (e.findtext(f"{_ATOM}author/{_ATOM}name") or "").strip()
        raw_time = (
            e.findtext(f"{_ATOM}published") or e.findtext(f"{_ATOM}updated") or ""
        )
        published = ""
        if raw_time:
            try:
                published = (
                    datetime.fromisoformat(raw_time)
                    .astimezone(timezone.utc)
                    .strftime("%Y-%m-%d")
                )
            except ValueError:
                pass
        snippet = _strip_html(e.findtext(f"{_ATOM}content") or "")[:400]
        lines.append(f"**r/{subreddit}: {title}**")
        meta = f"  作者: {author}" if author else ""
        if published:
            meta = f"{meta} | 日期: {published}" if meta else f"  日期: {published}"
        if meta:
            lines.append(meta)
        if snippet:
            lines.append(f"  {snippet}")
        lines.append(f"  {link}\n")
    return "\n".join(lines)
