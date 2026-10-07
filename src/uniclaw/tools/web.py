import re
from html import unescape

import httpx

from uniclaw.tools.base import tool
from uniclaw.tools.search.base import DEFAULT_UA
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.http_download import format_network_error


def _is_connect_error(exc: BaseException) -> bool:
    """异常(含异常链)是否属于连接建立失败(连不上代理/目标)。

    只有建连失败才可能是"代理不可达";读超时(已连上)、HTTP 状态码错误不算。
    覆盖 httpx.ConnectError / ConnectTimeout / ProxyError 及被包装的
    ConnectionRefusedError 等。
    """
    connect_types: tuple[type, ...] = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ProxyError,
        ConnectionRefusedError,
        ConnectionResetError,
    )
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, connect_types):
            return True
        cur = cur.__cause__ or (None if cur.__suppress_context__ else cur.__context__)
    return False


def _html_to_text(text: str) -> str:
    """将 HTML 清理为可读纯文本,保留段落结构。

    整块丢弃 head/script/style 以及 textarea/template/svg(其中不是可见文本,
    例如百度首页把结果页 CSS 以转义文本塞在 <textarea> 里,不清理会淹没正文)。
    HTML 实体解码放在剥标签之后,避免转义的标签源码被二次处理成空白。
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<head[^>]*>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE
    )
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"<(textarea|template|svg)[^>]*>.*?</\1>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 块级标签和换行标签转成换行,保留段落分割
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"</?(?:p|div|section|article|h[1-6]|li|tr|td|th|blockquote|pre|ol|ul)[^>]*>",
        "\n",
        text,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    # 先归一化连续换行,再压缩为空行(段落间最多保留一个空行)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


@tool
async def webFetch(url: str, proxy: str | None = None, raw: bool = False) -> str:
    """
    获取网页内容,返回结构化的可读信息。
    这是抓取网页内容的首选工具,比 curl 更适合:自动处理编码、重定向,
    返回包含状态码、标题和纯文本的综合信息,方便 AI 阅读。

    如果内容是 HTML 格式,会移除 script/style/textarea 等非正文内容并清理 HTML 标签,
    解码 HTML 实体,保留段落结构,提取页面标题。

    注意:如果需要获取浏览器渲染后的完整文本(如 SPA 单页应用、需要 JS 执行的页面),
    请使用 browser_get_text,它基于 Playwright,能获取动态渲染内容。

    Args:
        url (str): 要获取内容的网页 URL 地址
        proxy (str): 代理地址(如 http://127.0.0.1:7890),默认为 None 直连不走代理
        raw (bool): 为 True 时返回完整 HTML 源码(不清理标签),默认为 False 返回纯文本

    Returns:
        str: 包含 HTTP 状态码、页面标题和纯文本内容的综合信息,如果发生错误则返回错误信息字符串
    """
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=30) as client:
            r = await client.get(
                url,
                headers={"User-Agent": DEFAULT_UA},
                follow_redirects=True,
            )
        # 4xx/5xx 也是可预期的外部失败,必须在 try 内转成 TOOL_ERROR
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        resp = e.response
        status = resp.status_code if resp is not None else "?"
        ct = resp.headers.get("content-type", "") if resp is not None else ""
        return (
            f"{TOOL_ERROR}: HTTP {status} 请求失败 {url} "
            f"(content-type: {ct or 'unknown'}, proxy={proxy}): {e}"
        )
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        # 复用 http_download 的网络错误解释(DNS/连接被拒/超时),保留原始错误
        detail = format_network_error(e)
        tip = ""
        if proxy and _is_connect_error(e):
            tip = "\n代理不可达,可去掉 proxy 参数直连重试"
        return f"{TOOL_ERROR}: 请求失败 {url} (proxy={proxy}, timeout=30s): {detail}{tip}"

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
            title = unescape(re.sub(r"<[^>]+>", "", title_m.group(1))).strip()
            if title:
                meta += f" | Title: {title}"

    if raw:
        # raw 模式:返回完整 HTML/JSON 源码(不清理),但仍带元数据头部
        if not text:
            return f"{meta}\n\n(网页内容为空)"
        return f"{meta}\n\n{text}"

    if "html" in mime:
        text = _html_to_text(text)

    # 正文为空时明确提示;长度交由 agent 层统一截取
    body = text if text else "(网页内容为空)"
    return f"{meta}\n\n{body}"


def get_tools() -> list:
    """获取Web工具列表"""
    return [webFetch]


def get_all_tools() -> list:
    """获取所有Web工具(无条件返回)"""
    return get_tools()
