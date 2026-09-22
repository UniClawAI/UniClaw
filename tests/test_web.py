"""webFetch 工具测试:验证元数据头部、HTML 清理逻辑和代理参数。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        result = await webFetch("https://example.com")

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
        result = await webFetch("https://example.com")

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
        result = await webFetch("https://example.com", raw=True)

    assert result.startswith("HTTP 200")
    assert "<p>Raw</p>" in result
    assert "<b>Bold</b>" in result


@pytest.mark.asyncio
async def test_web_fetch_empty_body_reports_empty():
    """页面正文为空时返回 '网页内容为空' 提示。"""
    html = "<html><head><title>Empty</title></head><body></body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com")

    assert "网页内容为空" in result


@pytest.mark.asyncio
async def test_web_fetch_json_passes_through():
    """JSON 响应应原样返回(带元数据头部)。"""
    payload = '{"key": "value"}'
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(payload, "application/json")
        )
        result = await webFetch("https://api.example.com")

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
        result = await webFetch("https://example.com")

    assert result.startswith("[TOOL_ERROR]")


@pytest.mark.asyncio
async def test_web_fetch_returns_full_content_without_truncation():
    """webFetch 不做截断,长正文完整返回(agent 层统一截取)。"""
    html = f"<html><body>{'<p>word</p>' * 200}</body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com")

    assert "HTTP 200" in result
    assert "已截断" not in result
    assert result.count("word") == 200


@pytest.mark.asyncio
async def test_web_fetch_default_direct_no_proxy():
    """默认 proxy=None 时客户端不配置代理(直连)。"""
    html = "<html><body><p>Hi</p></body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com")

    assert "HTTP 200" in result
    assert mock_cls.call_args.kwargs.get("proxy") is None


@pytest.mark.asyncio
async def test_web_fetch_explicit_proxy_passed_through():
    """显式传入 proxy 时应透传给 httpx 客户端。"""
    html = "<html><body><p>Hi</p></body></html>"
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html")
        )
        result = await webFetch("https://example.com", proxy="http://127.0.0.1:7890")

    assert "HTTP 200" in result
    assert mock_cls.call_args.kwargs.get("proxy") == "http://127.0.0.1:7890"
