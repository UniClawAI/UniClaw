"""DuckDuckGo 搜索实现(移植自 web.py 的 _search_ddg, 国内需要代理)。"""

import html
import re
from urllib.parse import unquote

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, NO_RESULTS, http_get, safe_search

NAME = "duckduckgo"
LABEL = "DuckDuckGo"


def _decode_ddg_link(url: str) -> str:
    """还原 DuckDuckGo /l/?uddg=<percent编码URL> 重定向链接中的真实 URL。

    非 DDG 重定向链接或解码失败时原样返回。
    """
    m = re.search(r"[?&]uddg=([^&]+)", url)
    if not m:
        return url
    try:
        decoded = unquote(m.group(1))
        return decoded if decoded.startswith("http") else url
    except Exception:
        return url


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """DuckDuckGo 网页搜索 (海外平台, 配置了代理则使用代理)。"""
    r = await http_get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": DEFAULT_UA},
        config=config,
        use_proxy=True,  # 国内需代理
    )

    titles = re.findall(
        r'class="result__title"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        r.text,
        re.DOTALL,
    )
    snippets = re.findall(
        r'class="result__snippet"[^>]*>(.*?)</div>',
        r.text,
        re.DOTALL,
    )

    results = []
    for i, (link, title) in enumerate(titles[:limit]):
        # 去标签后还需还原 HTML 实体 (&#x27; &amp; 等)
        t = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        s = (
            html.unescape(re.sub(r"<[^>]+>", "", snippets[i])).strip()
            if i < len(snippets)
            else ""
        )
        # 直接在原始 href 上提取 uddg 值再解码:
        # 若先 unquote 整个链接, 真实 URL 中编码的 %26 会提前还原成裸 & 而被截断
        results.append({"title": t, "link": _decode_ddg_link(link), "snippet": s})

    if not results:
        return f"DuckDuckGo: {NO_RESULTS}"
    lines = [f"**DuckDuckGo 搜索结果** ({len(results)} 条):\n"]
    for item in results:
        lines.append(f"**{item['title']}**")
        if item["snippet"]:
            lines.append(f"  {item['snippet']}")
        lines.append(f"  {item['link']}\n")
    return "\n".join(lines)
