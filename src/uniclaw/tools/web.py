import re
import httpx
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.utils.truncation import truncate_text_by_tokens
from uniclaw.config import AppConfig


def _get_proxy(config: AppConfig | None) -> str | None:
    """从 config 中提取有效的代理地址,无效则返回 None。"""
    if config is None:
        return None
    proxy = config.proxy_url
    return proxy if isinstance(proxy, str) and proxy.startswith("http") else None


@tool
async def webFetch(
    url: str, max_tokens: int = 12000, raw: bool = False, tool_runtime: ToolRuntime = None
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
    config = tool_runtime.config
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
            # 先归一化连续换行,再压缩为空行(段落间最多保留一个空行)
            text = re.sub(r"\n{2,}", "\n", text)
            text = re.sub(r" *\n *", "\n", text)
            text = re.sub(r"\n{2,}", "\n\n", text)
            text = text.strip()

        # 正文为空时明确提示;有内容但被截断时,由 truncate_text_by_tokens 附上截断 token 数
        if not text:
            body = "(网页内容为空)"
        else:
            body = truncate_text_by_tokens(text, max_tokens)
        return f"{meta}\n\n{body}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


def get_tools() -> list:
    """获取Web工具列表"""
    return [webFetch]


def get_all_tools() -> list:
    """获取所有Web工具(无条件返回)"""
    return get_tools()
