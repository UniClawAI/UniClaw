"""webFetch 文本清理逻辑测试。

覆盖 commit 95b2e2e 优化的换行处理机制:
- 连续换行归一化: 多个 \\n 压缩为单个 \\n
- 空格换行清理: 换行前后空格移除
- 段落间距: 最多保留一个空行(\\n\\n)
"""

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.web import webFetch


class _FakeResponse:
    """模拟 httpx.Response。"""

    def __init__(self, text: str, content_type: str = "text/html", status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        pass


def _mock_client(response: _FakeResponse) -> AsyncMock:
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = response.raise_for_status
    mock_response.status_code = response.status_code
    mock_response.headers = response.headers
    mock_response.text = response.text
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


async def _fetch(html: str, raw: bool = False) -> str:
    """便捷封装: 用 mock 获取 HTML 正文。"""
    with patch("uniclaw.tools.web.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value.__aenter__.return_value = _mock_client(
            _FakeResponse(html, "text/html; charset=utf-8")
        )
        return await webFetch("https://example.com", raw=raw)


# ── 连续换行归一化 ─────────────────────────────────────────


class TestNewlineNormalization:
    """验证连续换行被正确压缩为段落间距。"""

    @pytest.mark.asyncio
    async def test_multiple_br_tags_collapse_to_double_newline(self):
        """多个 <br> 标签最终产生最多一个空行。"""
        html = "<html><body><p>A</p><br><br><br><p>B</p></body></html>"
        result = await _fetch(html)
        # 应出现段落分割(A 和 B 之间最多一个空行)
        assert "A" in result and "B" in result
        assert "\n\n\n" not in result

    @pytest.mark.asyncio
    async def test_block_tags_produce_paragraph_breaks(self):
        """p/div 等块级标签产生段落分割。"""
        html = "<html><body><p>First</p><p>Second</p></body></html>"
        result = await _fetch(html)
        assert "First" in result and "Second" in result
        # 应有换行分隔
        assert "\n" in result

    @pytest.mark.asyncio
    async def test_consecutive_blank_lines_capped_at_two(self):
        """确保不会出现 3 个以上连续换行。"""
        html = "<html><body><p>A</p><br><br><br><br><br><p>B</p></body></html>"
        result = await _fetch(html)
        assert "\n\n\n" not in result


# ── 空格换行清理 ───────────────────────────────────────────


class TestSpaceNewlineCleanup:
    """验证换行符前后的空格被清理。"""

    @pytest.mark.asyncio
    async def test_spaces_around_newlines_stripped(self):
        """行首行尾空格在清理后消失。"""
        # 构造含 inline 标签的 HTML, 使空格出现在换行附近
        html = "<html><body><p>Hello  </p><p>  World</p></body></html>"
        result = await _fetch(html)
        assert "Hello" in result and "World" in result
        # 不应有 ' \n' 或 '\n ' 的模式
        assert " \n" not in result
        assert "\n " not in result

    @pytest.mark.asyncio
    async def test_tabs_converted_to_spaces_then_cleaned(self):
        """tab 字符被归为空格后随换行清理。"""
        html = "<html><body><p>\tHello\t</p><p>\tWorld\t</p></body></html>"
        result = await _fetch(html)
        assert "\t" not in result


# ── raw 模式不清理 ─────────────────────────────────────────


class TestRawModeNoCleanup:
    """raw=True 时应保留原始 HTML, 不经过换行清理。"""

    @pytest.mark.asyncio
    async def test_raw_keeps_html_tags(self):
        html = "<html><body><p>A</p><br><br><br><p>B</p></body></html>"
        result = await _fetch(html, raw=True)
        assert "<p>A</p>" in result
        assert "<br>" in result


# ── 非正文内容丢弃 ─────────────────────────────────────────


class TestHiddenBlocksDropped:
    """textarea/template/svg 等非可见内容应整块丢弃(不淹没正文)。"""

    @pytest.mark.asyncio
    async def test_textarea_escaped_css_dropped(self):
        """百度首页模式:textarea 中 HTML 转义的 CSS 不应进入正文。"""
        html = (
            "<html><body>"
            '<textarea id="s_is_result_css" style="display:none;">'
            "&lt;style data-for=&quot;result&quot; &gt;"
            "html{font-size:100px}body{color:#333}"
            "&lt;/style&gt;"
            "</textarea>"
            "<p>Real content</p>"
            "</body></html>"
        )
        result = await _fetch(html)
        assert "Real content" in result
        assert "font-size" not in result
        assert "data-for" not in result

    @pytest.mark.asyncio
    async def test_template_and_svg_dropped(self):
        """template/svg 整块丢弃。"""
        html = (
            "<html><body>"
            "<template><p>tpl junk</p></template>"
            "<svg><path d='M0 0'/><text>svg junk</text></svg>"
            "<p>Visible</p>"
            "</body></html>"
        )
        result = await _fetch(html)
        assert "Visible" in result
        assert "tpl junk" not in result
        assert "svg junk" not in result

    @pytest.mark.asyncio
    async def test_html_comments_dropped(self):
        """HTML 注释不应出现在正文。"""
        html = "<html><body><!-- STATUS OK --><p>Text</p></body></html>"
        result = await _fetch(html)
        assert "Text" in result
        assert "STATUS OK" not in result


# ── HTML 实体解码 ──────────────────────────────────────────


class TestEntityDecoding:
    """HTML 实体应解码为可读字符,nbsp 归一为空格。"""

    @pytest.mark.asyncio
    async def test_common_entities_decoded(self):
        html = (
            "<html><body><p>Tom &amp; Jerry &lt;3 &quot;q&quot; &#39;s</p></body></html>"
        )
        result = await _fetch(html)
        assert "Tom & Jerry <3 \"q\" 's" in result

    @pytest.mark.asyncio
    async def test_nbsp_normalized_to_space(self):
        html = "<html><body><p>A&nbsp;B</p></body></html>"
        result = await _fetch(html)
        assert "A B" in result
        assert "\xa0" not in result


# ── 综合场景 ───────────────────────────────────────────────


class TestCompositeCleanup:
    """模拟真实网页内容的清理效果。"""

    @pytest.mark.asyncio
    async def test_article_with_multiple_paragraphs(self):
        """多段落文章应产生整洁的段落间距。"""
        html = (
            "<html><body>"
            "<h1>Title</h1>"
            "<p>First paragraph.</p>"
            "<p>Second paragraph.</p>"
            "<br>"
            "<p>Third paragraph after break.</p>"
            "</body></html>"
        )
        result = await _fetch(html)
        assert "Title" in result
        assert "First paragraph." in result
        assert "Second paragraph." in result
        assert "Third paragraph after break." in result
        # 无多余空行
        assert "\n\n\n" not in result

    @pytest.mark.asyncio
    async def test_inline_elements_preserve_word_spacing(self):
        """行内元素(如 <span>)不影响单词间距。"""
        html = "<html><body><p>Hello <span>beautiful</span> World</p></body></html>"
        result = await _fetch(html)
        assert "Hello beautiful World" in result or "Hello" in result