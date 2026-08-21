import re
import httpx
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.truncation import truncate_text_by_tokens
from cachetools import TTLCache
from uniclaw.config import AppConfig

# 搜索结果缓存:64 条,5 分钟过期
_search_cache = TTLCache(maxsize=64, ttl=600)


def _get_proxy(config: AppConfig | None) -> str | None:
    """从 config 中提取有效的代理地址,无效则返回 None。"""
    if config is None:
        return None
    proxy = config.proxy_url
    return proxy if isinstance(proxy, str) and proxy.startswith("http") else None


async def _search_exa(
    query: str, api_key: str, proxy: str | None = None, max_results: int = 8
) -> list[dict]:
    """Exa 语义搜索(AI 优化搜索引擎)。"""
    url = "https://api.exa.ai/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "numResults": max_results,
        "type": "auto",
        "contents": {
            "text": {"maxCharacters": 500},
        },
    }
    client_kwargs = {"proxy": proxy} if proxy else {}
    async with httpx.AsyncClient(**client_kwargs, timeout=15) as client:
        r = await client.post(url, json=payload, headers=headers)
    r.raise_for_status()
    data = r.json()

    results = []
    for item in data.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "link": item.get("url", ""),
                "snippet": item.get("text", ""),
            }
        )
    return results


async def _search_bing(query: str, max_results: int = 8) -> list[dict]:
    """Bing 搜索(国内直连,无需代理)。"""
    url = "https://www.bing.com/search"
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            url,
            params={"q": query, "count": str(max_results)},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            },
            follow_redirects=True,
        )
    r.raise_for_status()

    results = []
    # 提取搜索结果块
    blocks = re.findall(
        r'<li class="b_algo"[^>]*>(.*?)</li>',
        r.text,
        re.DOTALL,
    )
    for block in blocks[:max_results]:
        # 从 h2 > a 提取标题和链接(最可靠)
        h2_m = re.search(
            r'<h2[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if not h2_m:
            continue
        link = h2_m.group(1)
        title = re.sub(r"<[^>]+>", "", h2_m.group(2)).strip()
        # 提取摘要:优先 <p>,其次 <div class="b_caption"><p>
        snippet_m = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL)
        snippet = (
            re.sub(r"<[^>]+>", "", snippet_m.group(1)).strip() if snippet_m else ""
        )
        results.append({"title": title, "link": link, "snippet": snippet})
    return results


async def _search_ddg(
    query: str, proxy: str | None, max_results: int = 8
) -> list[dict]:
    """DuckDuckGo 搜索(国内需要代理)。"""
    url = "https://html.duckduckgo.com/html/"
    client_kwargs = {"proxy": proxy} if proxy else {}
    async with httpx.AsyncClient(**client_kwargs, timeout=15) as client:
        r = await client.get(
            url,
            params={"q": query},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            },
            follow_redirects=True,
        )
    r.raise_for_status()

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
    for i, (link, title) in enumerate(titles[:max_results]):
        t = re.sub(r"<[^>]+>", "", title).strip()
        s = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
        results.append({"title": t, "link": link, "snippet": s})
    return results


@tool
async def webFetch(
    url: str, max_tokens: int = 12000, raw: bool = False, config: AppConfig = None
) -> str:
    """
    获取网页内容,返回结构化的可读信息。
    这是抓取网页内容的首选工具,比 curl 更适合:自动处理编码、重定向、代理,
    返回包含状态码、标题和纯文本的综合信息,方便 AI 阅读。

    如果内容是 HTML 格式,会移除 script 和 style 标签并清理 HTML 标签,
    保留段落结构,提取页面标题。返回的文本长度可通过 max_tokens 控制。

    注意:如果需要获取浏览器渲染后的完整文本(如 SPA 单页应用、需要 JS 执行的页面),
    请使用 browser_get_text,它基于 Playwright,能获取动态渲染内容。

    Args:
        url (str): 要获取内容的网页 URL 地址
        max_tokens (int): 正文内容的最大 token 数,默认为 12000(元数据头部不计入此上限)
        raw (bool): 为 True 时返回完整 HTML 源码(不清理标签),默认为 False 返回纯文本

    Returns:
        str: 包含 HTTP 状态码、页面标题和纯文本内容的综合信息,如果发生错误则返回错误信息字符串
    """
    try:
        proxy = _get_proxy(config)
        client_kwargs = {"proxy": proxy} if proxy else {}

        async with httpx.AsyncClient(**client_kwargs, timeout=30) as client:
            r = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                },
                follow_redirects=True,
            )
        r.raise_for_status()

        ct = r.headers.get("content-type", "")
        text = r.text
        mime = ct.split(";")[0].strip()

        # 构建统一的元数据头部(状态码 + 类型 + 标题),所有模式均返回
        meta = f"HTTP {r.status_code} | {mime or 'unknown'}"
        if "html" in mime:
            title_m = re.search(
                r"<title[^>]*>(.*?)</title>", text, re.DOTALL | re.IGNORECASE
            )
            if title_m:
                title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                if title:
                    meta += f" | Title: {title}"

        if raw:
            # raw 模式:返回完整 HTML/JSON 源码(不清理),但仍带元数据头部
            body = truncate_text_by_tokens(text, max_tokens)
            if not body:
                return f"{meta}\n\n(网页内容为空)"
            return f"{meta}\n\n{body}"

        if "html" in mime:
            # 清理 HTML：保留段落结构
            text = re.sub(
                r"<head[^>]*>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE
            )
            text = re.sub(
                r"<script[^>]*>.*?</script>",
                "",
                text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            text = re.sub(
                r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE
            )
            # 块级标签和换行标签转成换行,保留段落分割
            text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
            text = re.sub(
                r"</?(?:p|div|section|article|h[1-6]|li|tr|td|th|blockquote|pre|ol|ul)[^>]*>",
                "\n",
                text,
            )
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = text.strip()

        # 正文为空时明确提示;有内容但被截断时,由 truncate_text_by_tokens 附上截断 token 数
        if not text:
            body = "(网页内容为空)"
        else:
            body = truncate_text_by_tokens(text, max_tokens)
        return f"{meta}\n\n{body}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def webSearch(query: str, config: AppConfig = None) -> str:
    """
    执行网络搜索并返回格式化的搜索结果。
    Args:
        query (str): 搜索查询字符串

    Returns:
        str: 格式化的搜索结果,每条结果包含标题、链接和摘要
    """
    # 检查缓存
    cached = _search_cache.get(query)
    if cached is not None:
        return cached

    proxy = _get_proxy(config)
    raw_results = []
    errors = []

    # 1. 优先使用 Exa(需配置 EXA_API_KEY,国内需代理)
    exa_key = config.EXA_API_KEY if config else ""
    if exa_key:
        try:
            raw_results = await _search_exa(query, exa_key, proxy)
        except Exception as e:
            errors.append(f"Exa: {e}")

    # 2. Exa 未配置或失败 → 尝试 Bing(国内直连)
    if not raw_results:
        try:
            raw_results = await _search_bing(query)
        except Exception as e:
            errors.append(f"Bing: {e}")

    # 3. Bing 失败 → 尝试 DuckDuckGo
    if not raw_results:
        try:
            raw_results = await _search_ddg(query, proxy)
        except Exception as e:
            errors.append(f"DuckDuckGo: {e}")

    # 3. 都失败
    if not raw_results:
        return f"{TOOL_ERROR}: 未找到搜索结果" + (
            f" ({'; '.join(errors)})" if errors else ""
        )

    # 格式化输出
    lines = []
    for r in raw_results[:8]:
        lines.append(f"**{r['title']}**\n{r['link']}\n{r['snippet']}")
    result = "\n\n".join(lines)

    # 写入缓存
    _search_cache[query] = result
    return result


def get_tools() -> list:
    """获取Web工具列表"""
    return [webFetch, webSearch]


def get_all_tools() -> list:
    """获取所有Web工具(无条件返回)"""
    return get_tools()
