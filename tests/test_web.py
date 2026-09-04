"""webFetch 工具测试:验证元数据头部和 HTML 清理逻辑。"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.web import webFetch


class _FakeResponse:
    """模拟 httpx.Response,提供 webFetch 需要的属性。"""

    def __init__(self, text: str, content_type: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        pass


def _mock_client(response: _FakeResponse) -> AsyncMock:
    """构造 mock 的 httpx.AsyncClient 上下文管理器,返回给定响应。"""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = response.raise_for_status
    mock_response.status_code = response.status_code
    mock_response.headers = response.headers
    mock_response.text = response.text
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_web_fetch_html_includes_metadata():
    """HTML 响应应包含 HTTP 状态码、Content-Type 和页面标题。"""
    html = (
        "<html><head><title>Test Page</title></head>"
        "<body><h1>Hello</h1><p>World</p></body></html>"
    )
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html; charset=utf-8")
        )
        result = await webFetch("https://example.com", tool_runtime=ToolRuntime())

    assert "HTTP 200" in result
    assert "text/html" in result
    assert "Title: Test Page" in result
    assert "Hello" in result
    assert "World" in result


@pytest.mark.asyncio
async def test_web_fetch_html_strips_scripts_and_styles():
    """HTML 清理应移除 script/style 内容。"""
    html = (
        "<html><body>"
        "<script>alert(1)</script>"
        "<style>.x{}</style>"
        "<p>Content</p>"
        "</body></html>"
    )
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com", tool_runtime=ToolRuntime())

    assert "alert" not in result
    assert ".x{}" not in result
    assert "Content" in result


@pytest.mark.asyncio
async def test_web_fetch_raw_keeps_html():
    """raw=True 应返回完整 HTML 源码(不清理标签),并带元数据头部。"""
    html = "<html><body><p>Raw</p><b>Bold</b></body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com", raw=True, tool_runtime=ToolRuntime())

    assert result.startswith("HTTP 200")
    assert "<p>Raw</p>" in result
    assert "<b>Bold</b>" in result


@pytest.mark.asyncio
async def test_web_fetch_tiny_max_tokens_reports_truncation():
    """max_tokens 小到装不下正文时,应提示截断而非误报为空。"""
    html = (
        "<html><body><p>Some real content here that is much longer and will "
        "definitely exceed five tokens</p></body></html>"
    )
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com", max_tokens=5, tool_runtime=ToolRuntime())

    assert "HTTP 200" in result
    assert "截断" in result
    assert "网页内容为空" not in result


@pytest.mark.asyncio
async def test_web_fetch_empty_body_reports_empty():
    """页面正文为空时返回 '网页内容为空' 提示。"""
    html = "<html><head><title>Empty</title></head><body></body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com", tool_runtime=ToolRuntime())

    assert "网页内容为空" in result


@pytest.mark.asyncio
async def test_web_fetch_json_passes_through():
    """JSON 响应应原样返回(带元数据头部)。"""
    payload = '{"key": "value"}'
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(payload, "application/json")
        )
        result = await webFetch("https://api.example.com", tool_runtime=ToolRuntime())

    assert "HTTP 200" in result
    assert '"key": "value"' in result


@pytest.mark.asyncio
async def test_web_fetch_error_returns_tool_error():
    """请求失败应返回 TOOL_ERROR 前缀的错误信息。"""
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("boom")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_cls.return_value.__aenter__.return_value = mock_client
        result = await webFetch("https://example.com", tool_runtime=ToolRuntime())

    assert result.startswith("[TOOL_ERROR]")


@pytest.mark.asyncio
async def test_web_fetch_respects_max_tokens():
    """max_tokens 应限制返回体长度,并在截断时标注已截断 token 数。"""
    html = f"<html><body>{'<p>word</p>' * 100}</body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com", max_tokens=100, tool_runtime=ToolRuntime())

    assert "HTTP 200" in result
    assert "已截断" in result
    # 截断提示形如 "[已截断 N 个tokens]",N 为被截掉的 token 数(> 0)
    m = re.search(r"\[已截断 (\d+) 个tokens\]", result)
    assert m, f"缺少截断 token 数提示,实际输出: {result[:200]}"
    assert int(m.group(1)) > 0


@pytest.mark.asyncio
async def test_web_fetch_raw_truncation_notice():
    """raw=True 截断时同样标注已截断 token 数。"""
    html = "<html><body>" + "<p>word</p>" * 200 + "</body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch(
            "https://example.com", raw=True, max_tokens=100, tool_runtime=ToolRuntime()
        )

    assert result.startswith("HTTP 200")
    assert "已截断" in result


@pytest.mark.asyncio
async def test_web_fetch_no_truncation_no_notice():
    """内容未超限时不出现截断提示。"""
    html = "<html><body><p>Short content</p></body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com", tool_runtime=ToolRuntime())

    assert "HTTP 200" in result
    assert "已截断" not in result
    assert "Short content" in result
