"""Bing 搜索实现(RSS 优先 + HTML 回退, 国内直连无需代理)。"""

import base64
import html as html_lib
import re
from datetime import datetime, timezone

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, NO_RESULTS, http_get, safe_search
from .time_range import parse_time_range

NAME = "bing"
LABEL = "Bing"

# 无区分度的高频英文词, 参与相关性匹配会造成大量误命中
_STOPWORDS = {
    "the",
    "and",
    "for",
    "are",
    "but",
    "not",
    "you",
    "all",
    "any",
    "can",
    "her",
    "was",
    "one",
    "our",
    "out",
    "get",
    "has",
    "him",
    "his",
    "how",
    "its",
    "let",
    "she",
    "too",
    "use",
    "what",
    "when",
    "where",
    "which",
    "will",
    "with",
    "this",
    "that",
    "from",
    "have",
    "been",
    "were",
    "does",
    "did",
}


def _decode_ck_link(url: str) -> str:
    """还原 Bing /ck/a 跳转链接中的真实 URL (u=a1<base64>)。

    非 /ck/a 链接或解码失败时原样返回。
    """
    m = re.search(r"[?&]u=a1([\w-]+)", url)
    if not m:
        return url
    b64 = m.group(1).replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    try:
        decoded = base64.b64decode(b64).decode("utf-8", "ignore")
        return decoded if decoded.startswith("http") else url
    except Exception:
        return url


def _tokens(query: str) -> list[str]:
    """提取查询中的有效 token, 用于结果相关性粗筛。

    - ASCII 词: 长度 >= 3 且不在停用词表
    - 中文连续段: 长度 >= 4 取全部相邻二字组合(免分词), 否则整段
    """
    tokens: list[str] = []
    for raw in query.split():
        for word in re.findall(r"[A-Za-z0-9]{3,}", raw):
            w = word.lower()
            if w not in _STOPWORDS:
                tokens.append(w)
        for cjk in re.findall(r"[\u4e00-\u9fff]{2,}", raw):
            if len(cjk) >= 4:
                tokens.extend(cjk[i : i + 2] for i in range(len(cjk) - 1))
            else:
                tokens.append(cjk)
    return list(dict.fromkeys(tokens))


def _relevant(text: str, tokens: list[str]) -> bool:
    """判断结果是否与查询相关。

    多 token 查询要求至少命中 2 个不同 token(单 token 查询命中即可):
    兜底垃圾页的摘要常天然包含"技术/模型"等高频词, 任一命中的宽松判定
    会漏放这类结果。
    含中文的查询放宽为命中 1 个即可: 中文表达同义性强(新特性/新变化),
    标题/摘要与查询词序不一致是常态, 严格多命中会误杀全部有效结果
    (实测 "Python 3.14 新特性" 中文结果因此被全部过滤为空)。
    无 token 时不过滤。
    """
    if not tokens:
        return True
    low = text.lower()
    hits = sum(1 for t in tokens if t in low)
    if any("\u4e00" <= ch <= "\u9fff" for t in tokens for ch in t):
        return hits >= 1
    return hits >= min(2, len(tokens))


def _parse_rss(xml_text: str) -> list[dict]:
    """解析 Bing RSS 输出 (format=rss) 为结果列表。"""
    results = []
    for item in re.findall(r"<item>(.*?)</item>", xml_text, re.DOTALL):

        def field(tag: str, item=item) -> str:
            m = re.search(rf"<{tag}>(.*?)</{tag}>", item, re.DOTALL)
            if not m:
                return ""
            # 先还原实体(会露出内嵌标签), 再去标签, 最后清理二次编码的实体
            text = html_lib.unescape(m.group(1))
            text = re.sub(r"<[^>]+>", "", text)
            return html_lib.unescape(text).strip()

        title, link, snippet = field("title"), field("link"), field("description")
        if title and link.startswith("http"):
            results.append({"title": title, "link": link, "snippet": snippet})
    return results


def _parse_html(html_text: str) -> list[dict]:
    """解析 Bing HTML 页面为结果列表 (RSS 不可用时的回退路径)。"""
    results = []
    blocks = re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', html_text, re.DOTALL)
    for block in blocks:
        # 从 h2 > a 提取标题和链接(最可靠)
        h2_m = re.search(
            r'<h2[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if not h2_m:
            continue
        # href 中的 & 为 HTML 转义 (&amp;), 先还原再解码跳转链
        link = _decode_ck_link(html_lib.unescape(h2_m.group(1)))
        title = re.sub(r"<[^>]+>", "", h2_m.group(2)).strip()
        # 提取摘要:优先 <p>
        snippet_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        snippet = (
            re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else ""
        )
        results.append({"title": title, "link": link, "snippet": snippet})
    return results


def _freshness_by_days(days: int) -> str:
    """把回溯天数映射为 Bing freshness 档位 (Day/Week/Month/Year)。"""
    if days <= 1:
        return "Day"
    if days <= 7:
        return "Week"
    if days <= 31:
        return "Month"
    if days <= 366:
        return "Year"
    return "Month"  # 超过一年的预设用 Month 近似 (Bing 无更粗档位)


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Bing 网页搜索 (国内直连, 无需代理)。"""
    headers = {"User-Agent": DEFAULT_UA}
    params = {"q": query, "count": str(limit)}
    # time_range -> freshness 参数: 预设映射到 Bing 支持的档位,
    # ISO 区间映射为 "起始..结束" 格式 (仅支持日期部分)
    tr = parse_time_range(time_range)
    if tr.start and tr.end:
        params["freshness"] = f"{tr.start:%Y-%m-%d}..{tr.end:%Y-%m-%d}"
    elif tr.start:
        days = (tr.end or datetime.now(timezone.utc) - tr.start).days
        params["freshness"] = _freshness_by_days(days)
    elif tr.end:
        # 只有结束日期无下界时, Bing 不接受单边 freshness, 忽略
        pass
    tokens = _tokens(query)

    def _relevant_only(items: list[dict]) -> list[dict]:
        """相关性粗筛: Bing 对无法匹配的查询(尤其脚本客户端的中文查询)
        会返回兜底推荐 (本地商户/热门站等), 通过查询 token 与标题摘要的
        包含关系过滤这类垃圾结果。
        """
        if not tokens:
            return items
        return [
            it for it in items if _relevant(f"{it['title']} {it['snippet']}", tokens)
        ]

    # 优先走 RSS 输出: 只含真实搜索结果、链接为原始 URL,
    # 可避开 HTML 页面在无匹配查询时混入的"猜你想搜"推荐块和 /ck/a 跳转链。
    # 注意先过滤再判断是否回退: 若 RSS 返回的全是无关垃圾, 不过滤会误判
    # 为"RSS 成功"而跳过 HTML 回退路径。
    rss_err: Exception | None = None
    try:
        r = await http_get(
            "https://www.bing.com/search",
            params={**params, "format": "rss"},
            headers=headers,
            config=config,
            use_proxy=False,  # 国内可直接访问
        )
        results = _relevant_only(_parse_rss(r.text))
    except Exception as e:
        results = []
        rss_err = e

    # RSS 为空或结果全部不相关时, 回退 HTML 解析
    if not results:
        try:
            r = await http_get(
                "https://www.bing.com/search",
                params=params,
                headers=headers,
                config=config,
                use_proxy=False,
            )
            results = _relevant_only(_parse_html(r.text))
        except Exception:
            # RSS 与 HTML 均异常时抛出真实错误(由 safe_search 转为
            # PLATFORM_ERROR), 避免伪装成"无搜索结果"误导调用方回退判断
            if rss_err is not None:
                raise
            results = []

    results = results[:limit]

    if not results:
        return f"Bing: {NO_RESULTS}"

    lines = [f"**Bing 搜索结果** ({len(results)} 条):\n"]
    for item in results:
        lines.append(f"**{item['title']}**")
        if item["snippet"]:
            lines.append(f"  {item['snippet']}")
        lines.append(f"  {item['link']}\n")
    return "\n".join(lines)
