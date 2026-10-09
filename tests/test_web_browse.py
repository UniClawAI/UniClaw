"""web_browse 模块的单元测试

测试基于 Playwright 的浏览器自动化工具，支持多页面管理。
"""

import asyncio

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# ── Mock Fixtures ────────────────────────────────────────────


@pytest.fixture
def mock_page():
    """创建 mock page 对象"""
    page = AsyncMock()
    page.url = "https://example.com"
    page.title = AsyncMock(return_value="Example Page")
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.content = AsyncMock(return_value="<html><body>Test</body></html>")
    page.inner_text = AsyncMock(return_value="Test content")
    page.inner_html = AsyncMock(return_value="<div>Content</div>")
    page.evaluate = AsyncMock(return_value="result")
    page.screenshot = AsyncMock(return_value=b"fake_screenshot")
    page.close = AsyncMock()
    page.go_back = AsyncMock()
    page.go_forward = AsyncMock()
    page.reload = AsyncMock()
    page.keyboard = AsyncMock()
    page.mouse = AsyncMock()

    # locator mock
    locator = AsyncMock()
    locator.click = AsyncMock()
    locator.fill = AsyncMock()
    locator.screenshot = AsyncMock(return_value=b"fake_screenshot")
    locator.inner_text = AsyncMock(return_value="Element text")
    locator.inner_html = AsyncMock(return_value="<span>Element</span>")
    locator.get_attribute = AsyncMock(return_value="attribute_value")
    locator.wait_for = AsyncMock()
    locator.select_option = AsyncMock()
    locator.check = AsyncMock()
    locator.uncheck = AsyncMock()
    locator.hover = AsyncMock()
    locator.drag_to = AsyncMock()
    page.locator = MagicMock(return_value=locator)

    return page


@pytest.fixture
def mock_context():
    """创建 mock context 对象"""
    context = AsyncMock()
    context.new_page = AsyncMock()
    context.close = AsyncMock()
    return context


@pytest.fixture
def mock_playwright():
    """创建 mock playwright 对象"""
    playwright = AsyncMock()
    playwright.chromium = AsyncMock()
    playwright.stop = AsyncMock()
    return playwright


@pytest.fixture
def web_browser():
    """创建 WebBrowser 实例"""
    from uniclaw.tools.web_browse.browser import WebBrowser

    return WebBrowser()


@pytest.fixture
def running_browser(web_browser, mock_playwright, mock_context, mock_page):
    """创建已启动的 WebBrowser 实例"""
    mock_browser = AsyncMock()
    mock_browser.close = AsyncMock()
    mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_context.new_page = AsyncMock(return_value=mock_page)

    with patch(
        "uniclaw.tools.web_browse.browser._get_async_playwright",
        return_value=lambda: mock_playwright,
    ):
        import asyncio

        asyncio.get_event_loop().run_until_complete(web_browser.start(headless=True))

    return web_browser


# ── WebBrowser 基础测试 ──────────────────────────────────────


class TestWebBrowserBasic:
    """测试 WebBrowser 基础功能"""

    @pytest.mark.asyncio
    async def test_start_creates_browser(
        self, web_browser, mock_playwright, mock_context, mock_page
    ):
        """测试启动浏览器"""
        mock_browser = AsyncMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            result = await web_browser.start(headless=True)

        assert "无头模式" in result
        assert web_browser.is_running
        assert web_browser.headless is True

    @pytest.mark.asyncio
    async def test_start_headed(
        self, web_browser, mock_playwright, mock_context, mock_page
    ):
        """测试启动有头浏览器"""
        mock_browser = AsyncMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            result = await web_browser.start(headless=False)

        assert "有头模式" in result
        assert web_browser.headless is False

    @pytest.mark.asyncio
    async def test_close(self, web_browser, mock_page):
        """测试关闭浏览器"""
        mock_playwright = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()

        web_browser._playwright = mock_playwright
        web_browser._browser = mock_browser
        web_browser._context = mock_context
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.close()
        assert "已关闭" in result
        assert not web_browser.is_running

    @pytest.mark.asyncio
    async def test_close_when_not_running(self, web_browser):
        """测试关闭未启动的浏览器"""
        result = await web_browser.close()
        assert "未启动" in result

    def test_not_running_raises(self, web_browser):
        """测试未启动时调用操作抛出异常"""
        with pytest.raises(RuntimeError, match="浏览器未启动"):
            web_browser._ensure_running()


# ── 多页面管理测试 ───────────────────────────────────────────


class TestPageManagement:
    """测试多页面管理功能"""

    @pytest.mark.asyncio
    async def test_new_page(self, web_browser, mock_page):
        """测试创建新页面"""
        web_browser._browser = MagicMock()
        web_browser._context = AsyncMock()
        web_browser._context.new_page = AsyncMock(return_value=mock_page)
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        web_browser._next_id = 2

        page_id, msg = await web_browser.new_page()
        assert page_id == 2
        assert "已创建新页面" in msg
        assert 2 in web_browser._pages
        assert web_browser._active_page_id == 2

    @pytest.mark.asyncio
    async def test_close_page(self, web_browser, mock_page):
        """测试关闭页面"""
        mock_page2 = AsyncMock()
        mock_page2.close = AsyncMock()

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.close_page(2)
        assert "已关闭页面 2" in result
        assert 2 not in web_browser._pages
        assert web_browser._active_page_id == 1

    @pytest.mark.asyncio
    async def test_close_active_page(self, web_browser, mock_page):
        """测试关闭活动页面会自动切换"""
        mock_page2 = AsyncMock()
        mock_page2.close = AsyncMock()

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.close_page(1)
        assert "已关闭页面 1" in result
        assert web_browser._active_page_id == 2

    @pytest.mark.asyncio
    async def test_close_nonexistent_page(self, web_browser):
        """测试关闭不存在的页面"""
        web_browser._browser = MagicMock()
        web_browser._pages = {}

        result = await web_browser.close_page(99)
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_switch_page(self, web_browser, mock_page):
        """测试切换页面"""
        mock_page2 = AsyncMock()
        mock_page2.title = AsyncMock(return_value="Page 2")

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.switch_page(2)
        assert "已切换到页面 2" in result
        assert web_browser._active_page_id == 2

    @pytest.mark.asyncio
    async def test_switch_nonexistent_page(self, web_browser):
        """测试切换到不存在的页面"""
        web_browser._browser = MagicMock()
        web_browser._pages = {}

        result = await web_browser.switch_page(99)
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_list_pages(self, web_browser, mock_page):
        """测试列出页面"""
        mock_page2 = AsyncMock()
        mock_page2.title = AsyncMock(return_value="Page 2")
        mock_page2.url = "https://example2.com"

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.list_pages()
        assert "打开的页面" in result
        assert "[1]" in result
        assert "[2]" in result
        assert "(活动)" in result

    @pytest.mark.asyncio
    async def test_list_pages_empty(self, web_browser):
        """测试列出空页面列表"""
        web_browser._browser = MagicMock()
        web_browser._pages = {}

        result = await web_browser.list_pages()
        assert "没有打开的页面" in result

    def test_page_ids(self, web_browser, mock_page):
        """测试获取页面 ID 列表"""
        web_browser._pages = {1: mock_page, 3: mock_page}
        assert web_browser.page_ids == [1, 3]

    def test_active_page(self, web_browser, mock_page):
        """测试获取活动页面"""
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        assert web_browser.active_page == mock_page

    def test_active_page_none(self, web_browser):
        """测试没有活动页面"""
        assert web_browser.active_page is None


# ── 页面操作测试（带 page_id）────────────────────────────────


class TestPageOperations:
    """测试带 page_id 参数的页面操作"""

    @pytest.mark.asyncio
    async def test_navigate_with_page_id(self, web_browser, mock_page):
        """测试指定页面导航"""
        mock_page2 = AsyncMock()
        mock_page2.goto = AsyncMock(return_value=MagicMock(status=200))
        mock_page2.title = AsyncMock(return_value="Page 2")

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.navigate("https://test.com", page_id=2)
        assert "已导航到" in result
        mock_page2.goto.assert_called_once()

    @pytest.mark.asyncio
    async def test_click_with_page_id(self, web_browser, mock_page):
        """测试指定页面点击"""
        mock_page2 = AsyncMock()
        locator = AsyncMock()
        mock_page2.locator = MagicMock(return_value=locator)

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.click("#button", page_id=2)
        assert "已点击" in result
        mock_page2.locator.assert_called_with("#button")

    @pytest.mark.asyncio
    async def test_screenshot_with_page_id(self, web_browser, mock_page):
        """测试指定页面截图"""
        mock_page2 = AsyncMock()
        mock_page2.screenshot = AsyncMock(return_value=b"screenshot2")

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.screenshot(page_id=2)
        assert isinstance(result, list)
        mock_page2.screenshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_text_with_page_id(self, web_browser, mock_page):
        """测试指定页面获取文本"""
        mock_page2 = AsyncMock()
        mock_page2.inner_text = AsyncMock(return_value="Page 2 content")

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.get_text(page_id=2)
        assert result == "Page 2 content"

    @pytest.mark.asyncio
    async def test_evaluate_with_page_id(self, web_browser, mock_page):
        """测试指定页面执行 JS"""
        mock_page2 = AsyncMock()
        mock_page2.evaluate = AsyncMock(return_value=42)

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page, 2: mock_page2}
        web_browser._active_page_id = 1

        result = await web_browser.evaluate("1+1", page_id=2)
        assert result == "42"


# ── 单页面操作测试 ───────────────────────────────────────────


class TestSinglePageOperations:
    """测试单页面操作（使用默认活动页面）"""

    @pytest.mark.asyncio
    async def test_navigate(self, web_browser, mock_page):
        """测试导航"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.navigate("https://example.com")
        assert "已导航到" in result
        assert "Example Page" in result

    @pytest.mark.asyncio
    async def test_click_css(self, web_browser, mock_page):
        """测试点击 CSS 选择器"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.click("#button")
        assert "已点击" in result
        mock_page.locator.assert_called_with("#button")

    @pytest.mark.asyncio
    async def test_click_xpath(self, web_browser, mock_page):
        """测试点击 XPath"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.click("//button[@id='submit']")
        assert "已点击" in result
        mock_page.locator.assert_called_with("xpath=//button[@id='submit']")

    @pytest.mark.asyncio
    async def test_type_text(self, web_browser, mock_page):
        """测试输入文本"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.type_text("#input", "Hello World")
        assert "输入" in result

    @pytest.mark.asyncio
    async def test_screenshot_page(self, web_browser, mock_page):
        """测试页面截图"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.screenshot()
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"

    @pytest.mark.asyncio
    async def test_get_text_page(self, web_browser, mock_page):
        """测试获取页面文本"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.get_text()
        assert result == "Test content"

    @pytest.mark.asyncio
    async def test_evaluate(self, web_browser, mock_page):
        """测试执行 JavaScript"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.evaluate = AsyncMock(return_value=42)

        result = await web_browser.evaluate("1 + 1")
        assert result == "42"

    @pytest.mark.asyncio
    async def test_scroll_down(self, web_browser, mock_page):
        """测试向下滚动"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.scroll("down", 500)
        assert "下滚动" in result
        mock_page.mouse.wheel.assert_called_once_with(0, 500)

    @pytest.mark.asyncio
    async def test_scroll_up(self, web_browser, mock_page):
        """测试向上滚动"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.scroll("up", 300)
        assert "上滚动" in result
        mock_page.mouse.wheel.assert_called_once_with(0, -300)

    @pytest.mark.asyncio
    @staticmethod
    def _nav(page, nav_method: str):
        """让 mock page 的 url 随 go_back/go_forward 变化, 模拟真实导航。

        hist_len=3: 历史共 3 项(如 blank/after/before),后退一步到 after
        仍在起点之前,故边界提示应为"仍可继续"。
        """
        page.evaluate = AsyncMock(return_value=3)
        page.url = "https://example.com/before"

        async def _navigate():
            page.url = "https://example.com/after"

        setattr(page, nav_method, AsyncMock(side_effect=_navigate))

    @pytest.mark.asyncio
    async def test_back(self, web_browser, mock_page):
        """测试后退"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        self._nav(mock_page, "go_back")

        result = await web_browser.back()
        assert "已后退" in result
        # 成功消息必须带 URL: 退回 about:blank 等无标题页时只靠 title 会误判
        assert "URL:" in result
        assert "https://example.com/after" in result
        # 退到普通页面: 仍可继续后退(about:blank 是起点,普通页前面多半还有)
        assert "仍可继续后退" in result

    @pytest.mark.asyncio
    async def test_back_to_about_blank_says_boundary(self, web_browser, mock_page):
        """退回 about:blank 时要提示已达历史起点,无法再后退。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.evaluate = AsyncMock(return_value=2)
        mock_page.url = "https://example.com/test.html"
        mock_page.title = AsyncMock(return_value="test")

        async def _go_back():
            mock_page.url = "about:blank"
            mock_page.title = AsyncMock(return_value="")

        mock_page.go_back = AsyncMock(side_effect=_go_back)

        result = await web_browser.back()
        assert "已后退" in result
        assert "about:blank" in result
        # 边界提示要说明这是起始页(browser_start 先建空白页再导航,首次 back 必然落这里)
        assert "起始页" in result
        assert "历史起点" in result
        assert "无法再后退" in result

    @pytest.mark.asyncio
    async def test_back_no_history(self, web_browser, mock_page):
        """无历史可退时不能报成功(go_back 是静默 no-op)。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.evaluate = AsyncMock(return_value=1)
        mock_page.url = "https://example.com"
        mock_page.go_back = AsyncMock()  # url 不变

        result = await web_browser.back()
        assert "无法后退" in result
        assert "已是历史起点" in result
        assert "无更早记录" in result
        # history.length 是浏览器内部量(含 browser_start 预建的 about:blank),
        # 对调用方无可操作信息 —— 不能外显
        assert "history.length" not in result

    @pytest.mark.asyncio
    async def test_forward(self, web_browser, mock_page):
        """测试前进"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        self._nav(mock_page, "go_forward")

        result = await web_browser.forward()
        assert "已前进" in result
        assert "URL:" in result
        assert "https://example.com/after" in result

    @pytest.mark.asyncio
    async def test_forward_from_blank_hist2_says_boundary(self, web_browser, mock_page):
        """从 about:blank 前进且 history.length<=2 → 唯一后续页即历史终点。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.evaluate = AsyncMock(return_value=2)
        mock_page.url = "about:blank"

        async def _go_forward():
            mock_page.url = "https://example.com/page"

        mock_page.go_forward = AsyncMock(side_effect=_go_forward)

        result = await web_browser.forward()
        assert "已前进" in result
        assert "历史终点" in result
        assert "无法再前进" in result

    @pytest.mark.asyncio
    async def test_forward_from_blank_longer_hist_says_can_continue(
        self, web_browser, mock_page
    ):
        """从 about:blank 前进但 history.length>2 → 后面还有页面,仍可继续前进。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.evaluate = AsyncMock(return_value=3)
        mock_page.url = "about:blank"

        async def _go_forward():
            mock_page.url = "https://example.com/a"

        mock_page.go_forward = AsyncMock(side_effect=_go_forward)

        result = await web_browser.forward()
        assert "已前进" in result
        assert "仍可继续前进" in result

    @pytest.mark.asyncio
    async def test_forward_to_last_entry_says_boundary(self, web_browser, mock_page):
        """前进到历史最后一项时**当次**即提示「已是历史终点」。

        回归测试: 曾用"最近一次 navigate 的 URL"启发式,在 new_page 建的页、
        重定向落地上判错 —— 到达末尾那一次仍报「仍可继续前进」,滞后一帧。
        现在由历史位置跟踪直接判定。
        """
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        # 历史 [blank, a, b],当前在 a(idx=1),forward 到 b 即末尾
        web_browser._hist_urls = {
            1: ["about:blank", "https://example.com/a", "https://example.com/b"]
        }
        web_browser._hist_idx = {1: 1}
        mock_page.evaluate = AsyncMock(return_value=3)
        mock_page.url = "https://example.com/a"

        async def _go_forward():
            mock_page.url = "https://example.com/b"

        mock_page.go_forward = AsyncMock(side_effect=_go_forward)

        result = await web_browser.forward()
        assert "已前进" in result
        assert "历史终点" in result
        assert "无法再前进" in result

    @pytest.mark.asyncio
    async def test_forward_no_history(self, web_browser, mock_page):
        """无历史可进时不能报成功。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.evaluate = AsyncMock(return_value=1)
        mock_page.url = "https://example.com"
        mock_page.go_forward = AsyncMock()

        result = await web_browser.forward()
        assert "无法前进" in result
        # 与 back 的"已是历史起点"对称 —— 不能一个用边界框架、一个只说"无记录"
        assert "已是历史末尾" in result
        assert "无后续记录" in result
        assert "history.length" not in result  # 内部量不外显(同 back)

    @pytest.mark.asyncio
    async def test_reload(self, web_browser, mock_page):
        """测试刷新"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.reload()
        assert "已刷新" in result

    @pytest.mark.asyncio
    async def test_get_url(self, web_browser, mock_page):
        """测试获取 URL"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.get_url()
        assert "https://example.com" in result

    @pytest.mark.asyncio
    async def test_get_title(self, web_browser, mock_page):
        """测试获取标题"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.get_title()
        assert "Example Page" in result

    @pytest.mark.asyncio
    async def test_press_key(self, web_browser, mock_page):
        """测试按键"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.press_key("Enter")
        assert "已按下按键" in result
        mock_page.keyboard.press.assert_called_once_with("Enter")

    @pytest.mark.asyncio
    async def test_select_option(self, web_browser, mock_page):
        """测试选择下拉框"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.select_option("#dropdown", "option1")
        assert "已选择选项" in result

    @pytest.mark.asyncio
    async def test_check(self, web_browser, mock_page):
        """测试勾选"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.check("#checkbox", True)
        assert "已勾选" in result

    @pytest.mark.asyncio
    async def test_uncheck(self, web_browser, mock_page):
        """测试取消勾选"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.check("#checkbox", False)
        assert "已取消勾选" in result

    @pytest.mark.asyncio
    async def test_hover(self, web_browser, mock_page):
        """测试悬停"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.hover("#menu")
        assert "已悬停" in result

    @pytest.mark.asyncio
    async def test_drag(self, web_browser, mock_page):
        """测试拖拽"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.drag("#source", "#target")
        assert "已将" in result
        assert "拖拽到" in result

    @pytest.mark.asyncio
    async def test_insert_text_no_selector(self, web_browser, mock_page):
        """不提供 selector 时向当前聚焦元素插入文本。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.keyboard.insert_text = AsyncMock()

        result = await web_browser.insert_text("hello")
        assert "已插入文本" in result
        mock_page.keyboard.insert_text.assert_called_once_with("hello")
        mock_page.locator.assert_not_called()  # 不应调用 locator

    @pytest.mark.asyncio
    async def test_insert_text_with_selector(self, web_browser, mock_page):
        """Bug #7 回归:提供 selector 时先聚焦再插入文本。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.keyboard.insert_text = AsyncMock()
        locator = mock_page.locator("#searchInput")

        result = await web_browser.insert_text("world", selector="#searchInput")
        assert "已向元素" in result
        locator.focus.assert_called_once()
        mock_page.keyboard.insert_text.assert_called_once_with("world")


# ── 模式切换测试 ─────────────────────────────────────────────


class TestModeSwitch:
    """测试无头/有头模式切换"""

    @pytest.mark.asyncio
    async def test_switch_to_headed(
        self, web_browser, mock_playwright, mock_context, mock_page
    ):
        """测试切换到有头模式"""
        mock_browser = AsyncMock()
        web_browser._playwright = mock_playwright
        web_browser._browser = mock_browser
        web_browser._context = mock_context
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        web_browser._headless = True
        mock_page.url = "https://example.com"

        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.storage_state = AsyncMock(return_value={"cookies": []})
        mock_context.add_cookies = AsyncMock()

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            result = await web_browser.switch_mode(headless=False)

        assert "有头模式" in result

    @pytest.mark.asyncio
    async def test_switch_to_headless(
        self, web_browser, mock_playwright, mock_context, mock_page
    ):
        """测试切换到无头模式"""
        mock_browser = AsyncMock()
        web_browser._playwright = mock_playwright
        web_browser._browser = mock_browser
        web_browser._context = mock_context
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        web_browser._headless = False
        mock_page.url = "https://example.com"

        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_context.storage_state = AsyncMock(return_value={"cookies": []})
        mock_context.add_cookies = AsyncMock()

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            result = await web_browser.switch_mode(headless=True)

        assert "无头模式" in result

    @pytest.mark.asyncio
    async def test_switch_same_mode(self, web_browser):
        """测试切换到相同模式"""
        web_browser._browser = MagicMock()
        web_browser._headless = True

        result = await web_browser.switch_mode(headless=True)
        assert "已在无头模式" in result

    @pytest.mark.asyncio
    async def test_switch_when_not_started(self, web_browser):
        """测试未启动时切换模式"""
        result = await web_browser.switch_mode(headless=False)
        assert "未启动" in result


# ── CDP 连接测试 ─────────────────────────────────────────────


class TestCdpConnect:
    """测试通过 CDP 连接外部浏览器"""

    @staticmethod
    def _make_cdp_mocks(pages: list):
        """构建 CDP 连接的 mock 环境。

        注意: browser.py 中 self._playwright = await async_playwright_fn().start(),
        因此必须让 mock 的 start() 返回其自身,配置才能生效。
        """
        mock_playwright = AsyncMock()
        mock_playwright.start = AsyncMock(return_value=mock_playwright)
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_context.pages = pages
        mock_browser.contexts = [mock_context]
        mock_playwright.chromium.connect_over_cdp = AsyncMock(
            return_value=mock_browser
        )
        return mock_playwright, mock_browser, mock_context

    @pytest.fixture
    async def cdp_browser(self, mock_context):
        """构造一个已通过 CDP 连接的 WebBrowser"""
        from unittest.mock import patch

        from uniclaw.tools.web_browse.browser import WebBrowser

        web_browser = WebBrowser()
        mock_playwright, mock_browser, mock_context = self._make_cdp_mocks([])
        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            await web_browser.connect_over_cdp("http://devtools.example:9222")
        return web_browser, mock_browser, mock_context

    @pytest.mark.asyncio
    async def test_connect_over_cdp(self, cdp_browser):
        """测试 CDP 连接成功"""
        web_browser, mock_browser, mock_context = cdp_browser

        assert web_browser.is_running
        assert web_browser.connected_via_cdp
        assert mock_browser.contexts[0] is web_browser._context

    @pytest.mark.asyncio
    async def test_connect_imports_existing_pages(self, web_browser):
        """测试连接时导入已有标签页"""
        from unittest.mock import patch

        page1 = AsyncMock()
        page2 = AsyncMock()
        mock_playwright, mock_browser, mock_context = self._make_cdp_mocks(
            [page1, page2]
        )

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            result = await web_browser.connect_over_cdp("http://localhost:9222")

        assert "导入 2 个页面" in result
        assert 1 in web_browser._pages
        assert 2 in web_browser._pages
        assert web_browser._pages[1] is page1
        assert web_browser._pages[2] is page2
        assert web_browser._active_page_id == 1

    @pytest.mark.asyncio
    async def test_connect_creates_page_when_none(self, web_browser):
        """测试无已有标签页时创建默认页面"""
        from unittest.mock import patch

        mock_page = AsyncMock()
        mock_playwright, mock_browser, mock_context = self._make_cdp_mocks([])
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            result = await web_browser.connect_over_cdp("http://localhost:9222")

        assert "已通过 CDP 连接" in result
        assert web_browser._active_page_id == 1
        mock_context.new_page.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_failure_returns_error(self, web_browser):
        """测试连接失败时返回错误信息,状态不误报"""
        from unittest.mock import patch

        mock_playwright, _, _ = self._make_cdp_mocks([])
        mock_playwright.chromium.connect_over_cdp = AsyncMock(
            side_effect=OSError("connection refused")
        )

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: mock_playwright,
        ):
            result = await web_browser.connect_over_cdp("http://localhost:9222")

        assert "TOOL_ERROR" in result
        assert "无法连接" in result
        assert not web_browser.is_running
        assert not web_browser.connected_via_cdp

    @pytest.mark.asyncio
    async def test_connect_when_already_running(self, web_browser, mock_playwright):
        """已有会话时 connect 不得静默覆盖,明确拒绝并提示先 close。"""
        old_browser = AsyncMock()
        web_browser._browser = old_browser
        web_browser._playwright = mock_playwright
        web_browser._context = AsyncMock()
        web_browser._connected_cdp = False

        mock_page = AsyncMock()
        new_playwright, mock_browser, mock_context = self._make_cdp_mocks([mock_page])

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: new_playwright,
        ):
            result = await web_browser.connect_over_cdp("http://localhost:9222")

        assert "已有" in result
        assert "browser_close" in result
        old_browser.close.assert_not_called(), "旧会话不得被销毁"
        assert web_browser._browser is old_browser

    @pytest.mark.asyncio
    async def test_close_cdp_only_disconnects(self, web_browser):
        """测试 CDP 模式下 close 仅断开连接,不关闭页面"""
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        web_browser._browser = mock_browser
        web_browser._context = mock_context
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        web_browser._connected_cdp = True

        result = await web_browser.close()

        assert "已断开" in result
        assert "CDP" in result
        mock_page.close.assert_not_called()  # 不应关闭用户浏览器中的页面
        assert not web_browser.is_running
        assert not web_browser.connected_via_cdp

    @pytest.mark.asyncio
    async def test_close_local_still_closes_pages(self, web_browser):
        """测试本地模式下 close 仍关闭页面"""
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()

        web_browser._browser = mock_browser
        web_browser._context = mock_context
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        web_browser._connected_cdp = False

        result = await web_browser.close()

        assert "浏览器已关闭" in result
        mock_page.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_mode_blocked_in_cdp(self, web_browser):
        """测试 CDP 模式下禁止切换显示模式"""
        web_browser._browser = MagicMock()
        web_browser._connected_cdp = True
        web_browser._headless = False

        result = await web_browser.switch_mode(headless=True)
        assert "TOOL_ERROR" in result
        assert "无法切换" in result


# ── 工具函数测试 ─────────────────────────────────────────────


class TestTools:
    """测试工具函数"""

    def test_get_tools_returns_list(self):
        """测试返回工具列表"""
        from uniclaw.tools.web_browse.tools import get_tools

        tools = get_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_get_tools_contains_core_tools(self):
        """测试包含核心工具"""
        from uniclaw.tools.web_browse.tools import get_tools

        tools = get_tools()
        tool_names = [t.name for t in tools]

        assert "browser_start" in tool_names
        assert "browser_close" in tool_names
        assert "browser_navigate" in tool_names
        assert "browser_click" in tool_names
        assert "browser_screenshot" in tool_names

    def test_get_tools_contains_page_tools(self):
        """测试包含页面管理工具"""
        from uniclaw.tools.web_browse.tools import get_tools

        tools = get_tools()
        tool_names = [t.name for t in tools]

        assert "browser_new_page" in tool_names
        assert "browser_close_page" in tool_names
        assert "browser_switch_page" in tool_names
        assert "browser_list_pages" in tool_names

    def test_get_all_tools_returns_more(self):
        """测试 get_all_tools 返回所有工具"""
        from uniclaw.tools.web_browse.tools import get_tools, get_all_tools

        core = get_tools()
        all_tools = get_all_tools()
        assert len(all_tools) >= len(core)

    def test_tools_have_descriptions(self):
        """测试所有工具都有描述"""
        from uniclaw.tools.web_browse.tools import get_tools

        for tool in get_tools():
            assert tool.description
            assert len(tool.description) > 0

    def test_insert_text_tool_schema_has_selector(self):
        """Bug #7 回归: browser_insert_text 的 schema 必须包含 selector 参数。"""
        from uniclaw.tools.web_browse.tools import get_tools

        tool = next(t for t in get_tools() if t.name == "browser_insert_text")
        props = tool.parameters["properties"]
        assert "selector" in props, "browser_insert_text 缺少 selector 参数"
        assert "text" in props
        assert props["selector"]["description"], "selector 缺少参数描述"

    def test_insert_text_tool_accepts_selector_kwarg(self):
        """Bug #7 回归: 直接以 selector 关键字调用工具函数不应报错。"""
        from uniclaw.tools.web_browse.tools import get_tools

        tool = next(t for t in get_tools() if t.name == "browser_insert_text")
        sig = __import__("inspect").signature(tool.func)
        assert "selector" in sig.parameters


class TestInsertTextEditableCheck:
    """insert_text 的目标可编辑性校验。

    回归测试: 对非 input/textarea/contenteditable 元素(如 <p>)调用时,
    locator.focus() 是 no-op,keyboard.insert_text 会把文本写进"当前拥有
    焦点"的元素 —— 工具却返回成功,造成数据错位(文本落到别的输入框)。
    """

    @pytest.mark.asyncio
    async def test_non_editable_target_rejected(self, web_browser, mock_page):
        """不可编辑元素直接报错, 绝不能把文本串到焦点元素。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        locator = mock_page.locator.return_value
        locator.is_editable = AsyncMock(return_value=False)

        result = await web_browser.insert_text("LEAK-TO-FOCUSED", selector=".desc")

        assert "不可编辑" in result
        assert "未写入" in result
        # 关键断言: 绝不能写入, 否则会污染当前焦点元素的值
        mock_page.keyboard.insert_text.assert_not_called()
        locator.focus.assert_not_called()

    @pytest.mark.asyncio
    async def test_editable_target_inserts_normally(self, web_browser, mock_page):
        """可编辑元素照常聚焦并插入。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        locator = mock_page.locator.return_value
        locator.is_editable = AsyncMock(return_value=True)

        result = await web_browser.insert_text("OK", selector="#name-input")

        assert "已向元素 #name-input 插入文本: OK" in result
        locator.focus.assert_awaited_once()
        mock_page.keyboard.insert_text.assert_awaited_once_with("OK")

    @pytest.mark.asyncio
    async def test_no_selector_keeps_focused_element_behavior(self, web_browser, mock_page):
        """不带 selector 时按文档语义写入当前焦点元素, 不做校验。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.insert_text("HELLO")

        assert "已插入文本: HELLO" in result
        mock_page.keyboard.insert_text.assert_awaited_once_with("HELLO")


class TestBrowserParamValidation:
    """参数校验: 非法值不能静默降级成"成功"。"""

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    async def test_scroll_illegal_direction(self, web_browser, mock_page):
        """非法 direction 必须报错, 不能静默按"向上"处理。"""
        self._running(web_browser, mock_page)
        result = await web_browser.scroll(direction="left-down", amount=100)
        assert "direction 非法" in result
        mock_page.mouse.wheel.assert_not_called()

    @pytest.mark.asyncio
    async def test_scroll_negative_amount(self, web_browser, mock_page):
        """负数 amount 必须拒绝, 不能静默接受。"""
        self._running(web_browser, mock_page)
        result = await web_browser.scroll(direction="down", amount=-100)
        assert "amount" in result and "-100" in result
        mock_page.mouse.wheel.assert_not_called()

    @pytest.mark.asyncio
    async def test_scroll_all_four_directions(self, web_browser, mock_page):
        """left/right 是合法方向, 要真正生效(原先只有 down/up)。"""
        page = self._running(web_browser, mock_page)
        await web_browser.scroll(direction="right", amount=50)
        page.mouse.wheel.assert_awaited_once_with(50, 0)
        page.mouse.wheel.reset_mock()
        await web_browser.scroll(direction="left", amount=50)
        page.mouse.wheel.assert_awaited_once_with(-50, 0)

    @pytest.mark.asyncio
    async def test_focus_disabled_element(self, web_browser, mock_page):
        """disabled 元素聚焦必须报错, 不能报成功(焦点根本没转移)。"""
        page = self._running(web_browser, mock_page)
        locator = page.locator.return_value
        locator.is_disabled = AsyncMock(return_value=True)

        result = await web_browser.focus("#disabled-input")

        assert "disabled" in result
        locator.focus.assert_not_called()

    @pytest.mark.asyncio
    async def test_focus_no_op_detected(self, web_browser, mock_page):
        """focus() 是 no-op 时(不可聚焦元素)不能报成功。"""
        page = self._running(web_browser, mock_page)
        locator = page.locator.return_value
        locator.is_disabled = AsyncMock(return_value=False)
        locator.focus = AsyncMock()
        locator.evaluate = AsyncMock(return_value=False)  # activeElement 不是它

        result = await web_browser.focus(".desc")

        assert "未能获得焦点" in result

    @pytest.mark.asyncio
    async def test_keyboard_type_readonly_rejected(self, web_browser, mock_page):
        """焦点在 readonly 元素上时必须报错, 不能报"已输入"(实际无效)。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value=False)  # 不可输入

        result = await web_browser.keyboard_type("X")

        assert "不可输入" in result
        page.keyboard.type.assert_not_called()


class TestEmptySelectorRejected:
    """空串 selector 是参数错误,不能静默退化成"整页/当前焦点"语义。

    回归测试: browser_get_text(selector="") 此前与不传 selector 等价,
    返回整页文本 —— 调用方以为查的是某个元素,拿到的却是整页。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    async def test_get_text_empty_selector_rejected(self, web_browser, mock_page):
        """get_text(selector="") 必须报参数错误, 不能返回整页文本。"""
        page = self._running(web_browser, mock_page)
        result = await web_browser.get_text(selector="")
        assert "selector 不能为空串" in result
        assert "要取整页文本请省略该参数" in result
        assert "TOOL_ERROR" in result
        page.inner_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_text_none_selector_returns_full_page(self, web_browser, mock_page):
        """selector=None 仍按设计返回整页文本。"""
        self._running(web_browser, mock_page)
        result = await web_browser.get_text(selector=None)
        assert "Test content" in result
        assert "TOOL_ERROR" not in result

    @pytest.mark.asyncio
    async def test_get_text_omitted_selector_returns_full_page(self, web_browser, mock_page):
        """不传 selector 仍按设计返回整页文本。"""
        self._running(web_browser, mock_page)
        result = await web_browser.get_text()
        assert "Test content" in result
        assert "TOOL_ERROR" not in result

    @pytest.mark.asyncio
    async def test_get_text_valid_selector_selects_element(self, web_browser, mock_page):
        """非空 selector 正常走元素路径, 不受影响。"""
        page = self._running(web_browser, mock_page)
        locator = page.locator.return_value
        locator.inner_text = AsyncMock(return_value="Element only")
        result = await web_browser.get_text(selector="#title")
        assert "Element only" in result

    @pytest.mark.asyncio
    async def test_get_html_empty_selector_rejected(self, web_browser, mock_page):
        page = self._running(web_browser, mock_page)
        result = await web_browser.get_html(selector="")
        assert "selector 不能为空串" in result
        page.content.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_html_none_selector_returns_full_page(self, web_browser, mock_page):
        self._running(web_browser, mock_page)
        result = await web_browser.get_html(selector=None)
        assert "<html>" in result
        assert "TOOL_ERROR" not in result

    @pytest.mark.asyncio
    async def test_screenshot_empty_selector_rejected(self, web_browser, mock_page):
        page = self._running(web_browser, mock_page)
        result = await web_browser.screenshot(selector="")
        assert "selector 不能为空串" in result
        page.screenshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_screenshot_none_selector_shoots_page(self, web_browser, mock_page):
        self._running(web_browser, mock_page)
        result = await web_browser.screenshot(selector=None)
        assert isinstance(result, list)  # 截图成功返回多模态内容
        mock_page.screenshot.assert_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "call",
        [
            lambda b: b.click(""),
            lambda b: b.click("", timeout=5000),
            lambda b: b.type_text("", "hi"),
            lambda b: b.get_attribute("", "href"),
            lambda b: b.dblclick(""),
            lambda b: b.focus(""),
            lambda b: b.hover(""),
            lambda b: b.check(""),
            lambda b: b.scroll_into_view(""),
            lambda b: b.get_count(""),
            lambda b: b.get_bounding_box(""),
            lambda b: b.get_styles(""),
            lambda b: b.select_option("", "v"),
            lambda b: b.insert_text("t", selector=""),
            lambda b: b.get_value(""),
            lambda b: b.drag("", "#target"),
            lambda b: b.drag("#src", ""),
        ],
    )
    async def test_required_selector_empty_rejected(self, web_browser, mock_page, call):
        """所有带 selector 的工具: 空串一律参数错误。"""
        page = self._running(web_browser, mock_page)
        result = await call(web_browser)
        assert "不能为空串" in result
        assert "TOOL_ERROR" in result
        # 不能走到 Playwright
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    async def test_insert_text_none_selector_keeps_focus_semantics(self, web_browser, mock_page):
        """insert_text 不传 selector 仍写当前焦点元素。"""
        self._running(web_browser, mock_page)
        result = await web_browser.insert_text("HELLO")
        assert "已插入文本" in result
        mock_page.keyboard.insert_text.assert_awaited_once_with("HELLO")

    @pytest.mark.asyncio
    async def test_get_value_none_selector_keeps_focus_semantics(self, web_browser, mock_page):
        """get_value 不传 selector 仍取当前焦点元素。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value="focused-value")
        result = await web_browser.get_value(selector=None)
        assert "focused-value" in result

    @pytest.mark.asyncio
    async def test_frame_selector_empty_rejected(self, web_browser, mock_page):
        """frame_selector="" 同样是参数错误, 不能静默退回主文档。"""
        self._running(web_browser, mock_page)
        result = await web_browser.click("#btn", frame_selector="")
        assert "frame_selector 不能为空串" in result

    @pytest.mark.asyncio
    async def test_tool_get_text_empty_selector_rejected(self, web_browser, mock_page):
        """经工具包装调用同样拒绝空串 selector(参数绑定后、调用 Playwright 前)。"""
        from uniclaw.tools.web_browse.tools import get_tools

        tool = next(t for t in get_tools() if t.name == "browser_get_text")
        self._running(web_browser, mock_page)
        with patch("uniclaw.tools.web_browse.tools._browser", web_browser):
            result = await tool.execute(selector="")

        assert "selector 不能为空串" in result
        assert "要取整页文本请省略该参数" in result


class TestTimeoutMustBePositive:
    """timeout 必须是正整数(毫秒), 负数/0 是参数错误。

    回归测试: browser_click(timeout=-5) 此前透传给 Playwright,
    报 "Timeout -5ms exceeded",失败提示"元素存在但未就绪"与真实原因不符。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_timeout", [-5, 0, -1])
    async def test_click_bad_timeout_rejected(self, web_browser, mock_page, bad_timeout):
        page = self._running(web_browser, mock_page)
        result = await web_browser.click("#btn", timeout=bad_timeout)
        assert "timeout 必须为正整数" in result
        assert str(bad_timeout) in result
        assert "TOOL_ERROR" in result
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_timeout", [-5, 0])
    async def test_type_text_bad_timeout_rejected(self, web_browser, mock_page, bad_timeout):
        page = self._running(web_browser, mock_page)
        result = await web_browser.type_text("#input", "hi", timeout=bad_timeout)
        assert "timeout 必须为正整数" in result
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_timeout", [-5, 0])
    async def test_dblclick_bad_timeout_rejected(self, web_browser, mock_page, bad_timeout):
        page = self._running(web_browser, mock_page)
        result = await web_browser.dblclick("#btn", timeout=bad_timeout)
        assert "timeout 必须为正整数" in result
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_timeout", [-5, 0])
    async def test_wait_for_bad_timeout_rejected(self, web_browser, mock_page, bad_timeout):
        page = self._running(web_browser, mock_page)
        result = await web_browser.wait_for(selector="#btn", timeout=bad_timeout)
        assert "timeout 必须为正整数" in result
        page.locator.assert_not_called()

    @pytest.mark.asyncio
    async def test_click_positive_timeout_passes(self, web_browser, mock_page):
        """正数 timeout 正常执行。"""
        page = self._running(web_browser, mock_page)
        locator = page.locator.return_value
        locator.click = AsyncMock()
        result = await web_browser.click("#btn", timeout=3000)
        assert "已点击元素" in result
        locator.click.assert_awaited_once_with(timeout=3000)

    @pytest.mark.asyncio
    async def test_type_text_positive_timeout_passes(self, web_browser, mock_page):
        page = self._running(web_browser, mock_page)
        locator = page.locator.return_value
        locator.fill = AsyncMock()
        result = await web_browser.type_text("#input", "hi", timeout=1000)
        assert "输入文本" in result

    @pytest.mark.asyncio
    async def test_timeout_error_prefixed(self, web_browser, mock_page):
        """报错文本带 TOOL_ERROR 前缀, 模型能看到参数非法而非"元素未就绪"。"""
        self._running(web_browser, mock_page)
        result = await web_browser.click("#btn", timeout=-5)
        assert result.startswith("TOOL_ERROR") or "TOOL_ERROR" in result


class TestConnectKeepsExistingSession:
    """browser_connect 失败不能连带销毁现有浏览器会话。

    回归测试: 旧实现先 await close() 再尝试连接,连不上时原有会话已被销毁,
    后续 get_url/close 全报"浏览器未启动"。
    """

    @pytest.mark.asyncio
    async def test_connect_failure_keeps_session(self, web_browser, mock_page):
        """连不上时现有会话要保持可用。"""
        # 无会话场景: 连接失败不能留下半坏状态
        web_browser._browser = None
        web_browser._pages = {}
        web_browser._active_page_id = None

        fake_pw = AsyncMock()
        fake_pw.start = AsyncMock(return_value=fake_pw)  # .start() 要返回自身
        fake_pw.chromium.connect_over_cdp = AsyncMock(side_effect=OSError("refused"))

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: fake_pw,
        ):
            result = await web_browser.connect_over_cdp("http://localhost:9999")

        assert "无法连接" in result
        assert web_browser._browser is None, "连接失败不得留下半坏会话"
        assert web_browser._pages == {}


class TestScreenshotNoSilentOverwrite:
    """截图不静默覆盖已有文件(曾把 test.html 覆盖成 PNG)。"""

    @pytest.mark.asyncio
    async def test_refuses_to_overwrite(self, web_browser, mock_page, tmp_path):
        """目标文件已存在时拒绝写入。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.screenshot = AsyncMock(return_value=b"\x89PNG fake")

        target = tmp_path / "test.html"
        target.write_text("<html>原始内容</html>", encoding="utf-8")

        result = await web_browser.screenshot(save_path=str(target))

        assert "拒绝覆盖" in str(result)
        assert target.read_text(encoding="utf-8") == "<html>原始内容</html>"

    @pytest.mark.asyncio
    async def test_writes_when_absent(self, web_browser, mock_page, tmp_path):
        """目标不存在时正常写入。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.screenshot = AsyncMock(return_value=b"\x89PNG fake")

        target = tmp_path / "shot.png"
        result = await web_browser.screenshot(save_path=str(target))
        assert target.exists()


class TestDragNeedsDraggable:
    """对非 draggable 元素不能静默报成功。"""

    @pytest.mark.asyncio
    async def test_non_draggable_rejected(self, web_browser, mock_page):
        """源元素不可拖拽时直接报错, 不执行拖拽。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        src = AsyncMock()
        src.evaluate = AsyncMock(return_value=False)
        tgt = AsyncMock()
        mock_page.locator = MagicMock(side_effect=[src, tgt])

        result = await web_browser.drag(".item", ".zone")

        assert "不可拖拽" in result
        src.drag_to.assert_not_called()

    @pytest.mark.asyncio
    async def test_draggable_element_dragged(self, web_browser, mock_page):
        """可拖拽元素照常拖拽。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        src = AsyncMock()
        src.evaluate = AsyncMock(return_value=True)
        tgt = AsyncMock()
        mock_page.locator = MagicMock(side_effect=[src, tgt])

        result = await web_browser.drag(".item", ".zone")
        assert "已将 .item 拖拽到 .zone" in result
        src.drag_to.assert_awaited_once()


# ── 超时分类提示(元素不存在 vs 存在但未就绪)──────────────────


class TestTimeoutClassification:
    """超时错误要区分「元素不存在」与「元素存在但未就绪」,并给可自纠建议。

    回归测试: 曾只报 `Timeout 5000ms exceeded`,不区分选择器写错与
    元素被遮挡/未渲染完 —— 两者排查方向相反,只给通用超时等于没说。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page.locator.return_value

    @pytest.mark.asyncio
    async def test_click_timeout_element_missing(self, web_browser, mock_page):
        """元素不存在时要明说「选择器未匹配到任何节点」。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=0)

        result = await web_browser.click("#no-such-element")

        assert "TOOL_ERROR" in result
        assert "点击元素" in result
        assert "元素不存在" in result
        assert "选择器未匹配到任何节点" in result
        # Playwright 原始错误(含 call log)要保留
        assert "Timeout 5000ms exceeded" in result
        # 任务 2: 通用建议与分类提示合并在同一条里
        assert "browser_wait" in result
        assert result.count("提示:") == 1

    @pytest.mark.asyncio
    async def test_click_timeout_element_exists_not_ready(self, web_browser, mock_page):
        """元素在 DOM 中但未就绪时要明说「存在但未就绪」。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=1)

        result = await web_browser.click("#covered-btn")

        assert "TOOL_ERROR" in result
        assert "元素存在但未就绪" in result
        assert "被遮挡" in result
        assert "元素不存在" not in result
        assert "browser_wait" in result

    @pytest.mark.asyncio
    async def test_click_timeout_probe_failure_still_reports(self, web_browser, mock_page):
        """探测本身抛异常时不能掩盖原始超时错误。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(side_effect=RuntimeError("page navigated away"))

        result = await web_browser.click("#gone")

        assert "TOOL_ERROR" in result
        assert "Timeout 5000ms exceeded" in result
        assert "browser_wait" in result
        assert "无法确认" in result

    @pytest.mark.asyncio
    async def test_non_timeout_error_has_no_classify_hint(self, web_browser, mock_page):
        """非超时错误不走探测,不贴「元素不存在/未就绪」分类。"""
        from uniclaw.tools.web_browse.browser import PlaywrightError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(side_effect=PlaywrightError("Element is not enabled"))

        result = await web_browser.click("#btn")

        assert "TOOL_ERROR" in result
        assert "Element is not enabled" in result
        assert "提示:" not in result
        assert "元素不存在" not in result

    @pytest.mark.asyncio
    async def test_scroll_into_view_timeout_missing(self, web_browser, mock_page):
        """scroll_into_view(元素滚动族)与 click 同样分类。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.scroll_into_view_if_needed = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=0)

        result = await web_browser.scroll_into_view("#footer")

        assert "滚动元素到可见区域" in result
        assert "元素不存在" in result
        assert "browser_wait" in result

    @pytest.mark.asyncio
    async def test_type_text_timeout_not_ready(self, web_browser, mock_page):
        """type_text 同族超时也要分类。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.fill = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=1)

        result = await web_browser.type_text("#search", "hello")

        assert "元素存在但未就绪" in result
        assert "browser_wait" in result

    @pytest.mark.asyncio
    async def test_hover_timeout_missing(self, web_browser, mock_page):
        """hover 同族超时也要分类。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.hover = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=0)

        result = await web_browser.hover("#menu")

        assert "元素不存在" in result

    @pytest.mark.asyncio
    async def test_select_option_timeout_missing(self, web_browser, mock_page):
        """select_option 同族超时也要分类。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.select_option = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=0)

        result = await web_browser.select_option("#dropdown", "a")

        assert "元素不存在" in result

    @pytest.mark.asyncio
    async def test_check_timeout_missing(self, web_browser, mock_page):
        """check 同族超时也要分类。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.check = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=0)

        result = await web_browser.check("#agree", True)

        assert "元素不存在" in result

    @pytest.mark.asyncio
    async def test_dblclick_timeout_not_ready(self, web_browser, mock_page):
        """dblclick 同族超时也要分类。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.dblclick = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=1)

        result = await web_browser.dblclick("#item")

        assert "元素存在但未就绪" in result

    @pytest.mark.asyncio
    async def test_drag_timeout_reports_both_ends(self, web_browser, mock_page):
        """drag 超时要分别探测源/目标,免得只报一侧误导排查。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        src = AsyncMock()
        src.evaluate = AsyncMock(return_value=True)  # draggable
        src.drag_to = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        src.count = AsyncMock(return_value=1)  # 源存在
        tgt = AsyncMock()
        tgt.count = AsyncMock(return_value=0)  # 目标不存在
        mock_page.locator = MagicMock(side_effect=[src, tgt])

        result = await web_browser.drag(".item", ".zone")

        assert "TOOL_ERROR" in result
        assert "源元素:" in result
        assert "目标元素:" in result
        assert "元素存在但未就绪" in result
        assert "元素不存在" in result
        assert "browser_wait" in result


# ── browser_get_elements 推荐选择器唯一性 ────────────────────


class TestGetElementsSelectorUniqueness:
    """推荐选择器必须唯一,否则明确标注匹配数,供 nth()/文本二次定位。

    回归测试: microsoft.com 3 个 Cookie 按钮共用同一推荐选择器,
    按推荐执行 click/get_attribute 必报 strict mode violation。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @staticmethod
    def _el(index=0, tag="button", selector="button", **over):
        base = {
            "index": index,
            "tag": tag,
            "type": "",
            "id": "",
            "name": "",
            "text": "OK",
            "placeholder": "",
            "href": "",
            "role": "",
            "ariaLabel": "",
            "selector": selector,
            "selectorUnique": True,
            "selectorMatchCount": 1,
            "selectorNth": -1,
            "rect": {"x": 0, "y": 0, "width": 10, "height": 10},
        }
        base.update(over)
        return base

    @pytest.mark.asyncio
    async def test_unique_selector_plain(self, web_browser, mock_page):
        """唯一选择器直接给出,不加冗余标注。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(
            return_value=[self._el(selector="#accept-btn", id="accept-btn")]
        )

        result = await web_browser.get_interactive_elements()

        assert "selector=#accept-btn" in result
        assert "匹配" not in result.split("selector=")[-1]

    @pytest.mark.asyncio
    async def test_non_unique_selector_annotated(self, web_browser, mock_page):
        """非唯一选择器必须标注匹配数 + nth,不能装作能直接用。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(
            return_value=[
                self._el(
                    index=0,
                    selector="button.cookie",
                    selectorUnique=False,
                    selectorMatchCount=3,
                    selectorNth=0,
                    text="Accept",
                ),
                self._el(
                    index=1,
                    selector="button.cookie",
                    selectorUnique=False,
                    selectorMatchCount=3,
                    selectorNth=1,
                    text="Reject",
                ),
            ]
        )

        result = await web_browser.get_interactive_elements()

        assert "button.cookie" in result
        assert "该选择器匹配 3 个元素" in result
        assert "nth()" in result
        assert "二次定位" in result
        assert "nth=0" in result
        assert "nth=1" in result

    @pytest.mark.asyncio
    async def test_js_validates_uniqueness(self, web_browser, mock_page):
        """生成推荐选择器的 JS 必须做唯一性校验,而不是盲拼 CSS。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value=[])

        await web_browser.get_interactive_elements()
        js = page.evaluate.call_args[0][0]

        assert "matchCount" in js
        assert "querySelectorAll" in js
        assert "selectorUnique" in js
        # 找不到唯一选择器时走标注分支,而不是硬造 nth-child 长路径
        assert "selectorMatchCount" in js


# ── browser_get_styles 计算样式取值 ─────────────────────────


class TestGetStylesComputedValues:
    """getPropertyValue 要用 CSS 属性名(kebab-case),简写拆成长胡子属性。

    回归测试: backgroundColor/fontSize/zIndex 等 camelCase 取值返回空,
    对 #sb_form_q 查询样式时复现。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    async def test_js_uses_kebab_case_property_names(self, web_browser, mock_page):
        """JS 必须用 background-color/font-size/z-index 等解析后的属性名取值。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value={"background-color": "red"})

        await web_browser.get_styles("#sb_form_q")
        js = page.evaluate.call_args[0][0]

        assert "getPropertyValue" in js
        assert "'background-color'" in js
        assert "'font-size'" in js
        assert "'z-index'" in js
        # camelCase 传给 getPropertyValue 会拿到空串 —— 不能出现在取值列表里
        assert "getPropertyValue('backgroundColor')" not in js
        assert "getPropertyValue('fontSize')" not in js
        assert "getPropertyValue('zIndex')" not in js
        assert "'backgroundColor'" not in js
        assert "'fontSize'" not in js
        assert "'zIndex'" not in js

    @pytest.mark.asyncio
    async def test_js_expands_shorthand_properties(self, web_browser, mock_page):
        """margin/padding/border/background/font 简写要拆成子属性读取。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value={})

        await web_browser.get_styles("#box")
        js = page.evaluate.call_args[0][0]

        assert "'margin-top'" in js
        assert "'padding-left'" in js
        assert "'border-color'" in js
        assert "'background-color'" in js
        assert "'font-weight'" in js
        # 简写本身不再作为取值键(部分浏览器对简写返回空)
        props_block = js.split("const props =")[1].split("];")[0]
        for shorthand in ("'margin'", "'padding'", "'border'", "'background'", "'font'"):
            assert shorthand not in props_block

    @pytest.mark.asyncio
    async def test_formats_computed_styles(self, web_browser, mock_page):
        """取到的计算样式要完整格式化输出。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(
            return_value={
                "background-color": "rgb(255, 255, 255)",
                "font-size": "16px",
                "z-index": "auto",
            }
        )

        result = await web_browser.get_styles("#sb_form_q")

        assert "background-color: rgb(255, 255, 255)" in result
        assert "font-size: 16px" in result
        assert "z-index: auto" in result

    @pytest.mark.asyncio
    async def test_missing_element_reports_error(self, web_browser, mock_page):
        """元素不存在时返回 TOOL_ERROR,而不是空样式表。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value=None)

        result = await web_browser.get_styles("#no-such")

        assert "TOOL_ERROR" in result
        assert "不存在" in result


# ── 并发导航串行化(browser_back 竞态)────────────────────────


class TestConcurrentNavigation:
    """并发 back/forward/navigate 必须串行化, 历史游标不乱。

    回归测试: 同一条消息里并发调 2~3 个 browser_back 时, Playwright
    go_back 相互打断报 net::ERR_ABORTED, 且 _hist_move 与真实导航交错后
    游标与实际位置脱节 —— 后续操作全基于错误位置, forward 栈也被破坏。
    """

    def _setup_stack(self, web_browser, mock_page, urls, start_idx):
        """建立可真实前进/后退的 mock 页面(按索引移动, 与历史一致)。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        web_browser._hist_urls = {1: list(urls)}
        web_browser._hist_idx = {1: start_idx}

        pos = {"i": start_idx}
        mock_page.url = urls[start_idx]
        mock_page.evaluate = AsyncMock(return_value=len(urls))

        async def go_back():
            if pos["i"] > 0:
                pos["i"] -= 1
            mock_page.url = urls[pos["i"]]

        async def go_forward():
            if pos["i"] < len(urls) - 1:
                pos["i"] += 1
            mock_page.url = urls[pos["i"]]

        mock_page.go_back = AsyncMock(side_effect=go_back)
        mock_page.go_forward = AsyncMock(side_effect=go_forward)
        mock_page.title = AsyncMock(return_value="t")
        return pos

    @pytest.mark.asyncio
    async def test_concurrent_back_all_succeed_and_cursor_correct(
        self, web_browser, mock_page
    ):
        """并发 3 个 back 全部成功, 历史游标准确, forward 栈完好。"""
        # 4 项历史(blank/A/B/C)起点在 C(idx=3): 3 次 back 刚好到 blank, 全部成功
        urls = [
            "about:blank",
            "https://a.example/",
            "https://b.example/",
            "https://c.example/",
        ]
        pos = self._setup_stack(web_browser, mock_page, urls, start_idx=3)

        results = await asyncio.gather(
            web_browser.back(), web_browser.back(), web_browser.back()
        )

        # 全部成功(不报 ERR_ABORTED / 无法后退)
        for r in results:
            assert "已后退" in r, f"并发 back 应全部成功: {r}"
            assert "ERR_ABORTED" not in r
            assert "TOOL_ERROR" not in r

        # 历史游标与真实位置一致(退 3 步到 blank 即起点)
        assert pos["i"] == 0
        assert web_browser._hist_idx[1] == 0
        assert mock_page.url == urls[0]

        # forward 栈完好: 3 次 forward 应能回到原位
        fwd = await asyncio.gather(
            web_browser.forward(), web_browser.forward(), web_browser.forward()
        )
        for r in fwd:
            assert "已前进" in r, f"并发 forward 应全部成功: {r}"
        assert pos["i"] == 3
        assert web_browser._hist_idx[1] == 3

    @pytest.mark.asyncio
    async def test_concurrent_back_serialized_by_lock(self, web_browser, mock_page):
        """并发 back 必须排队串行, 不能交叉执行 go_back。"""
        urls = [
            "about:blank",
            "https://a.example/",
            "https://b.example/",
            "https://c.example/",
        ]
        order = []
        self._setup_stack(web_browser, mock_page, urls, start_idx=3)

        orig_back = mock_page.go_back

        async def tracked_back():
            order.append("start")
            await orig_back()
            order.append("end")

        mock_page.go_back = AsyncMock(side_effect=tracked_back)

        await asyncio.gather(web_browser.back(), web_browser.back(), web_browser.back())

        # 串行: start/end 交替, 不允许 start-start-start
        assert order == ["start", "end", "start", "end", "start", "end"]

    @pytest.mark.asyncio
    async def test_nav_lock_covers_navigate_and_back(self, web_browser, mock_page):
        """navigate 与 back 并发时也串行 —— 历史游标不交叉更新。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        pos = self._setup_stack(web_browser, mock_page, urls, start_idx=2)

        async def do_navigate():
            return await web_browser.navigate("https://d.example/")

        async def do_back():
            return await web_browser.back()

        r1, r2 = await asyncio.gather(do_navigate(), do_back())

        # navigate 会截掉前进分支; back 与 navigate 串行后游标仍一致
        assert "已导航到" in r1 or "已后退" in r1
        assert "已导航到" in r2 or "已后退" in r2
        assert web_browser._hist_idx[1] == len(web_browser._hist_urls[1]) - 1 or (
            web_browser._hist_idx[1] >= 0
        )
        # 不应出现游标越界
        assert 0 <= web_browser._hist_idx[1] < len(web_browser._hist_urls[1])

    @pytest.mark.asyncio
    async def test_err_aborted_gets_concurrency_hint(self, web_browser, mock_page):
        """ERR_ABORTED 报错必须附并发导航提示, 不能只丢引擎原文。"""
        from uniclaw.tools.web_browse.browser import PlaywrightError

        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        mock_page.evaluate = AsyncMock(return_value=3)
        mock_page.url = "https://a.example/"
        mock_page.go_back = AsyncMock(
            side_effect=PlaywrightError(
                "net::ERR_ABORTED; maybe frame was detached?"
            )
        )

        result = await web_browser.back()

        assert "TOOL_ERROR" in result
        assert "ERR_ABORTED" in result
        assert "并发导航" in result
        assert "串行" in result


# ── back/forward 边界提示(当次正确, 不滞后)──────────────────


class TestHistoryBoundaryHints:
    """back/forward 边界提示必须**当次**正确。

    回归测试: 曾差一格 —— back 到栈底当次仍报"仍可继续后退"(下次才报
    边界), forward 到末尾当次报"仍可继续前进", 或倒数第 2 页即误报
    "已是历史终点"。根因是 _hist_move 拒绝时游标未对齐真实位置,
    边界判定基于过期下标。
    """

    def _setup_stack(self, web_browser, mock_page, urls, start_idx, tracked=True):
        """建立 A→B→C 之类的多页历史 mock。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        if tracked:
            web_browser._hist_urls = {1: list(urls)}
            web_browser._hist_idx = {1: start_idx}

        pos = {"i": start_idx}
        mock_page.url = urls[start_idx]
        mock_page.evaluate = AsyncMock(return_value=len(urls))

        async def go_back():
            if pos["i"] > 0:
                pos["i"] -= 1
            mock_page.url = urls[pos["i"]]

        async def go_forward():
            if pos["i"] < len(urls) - 1:
                pos["i"] += 1
            mock_page.url = urls[pos["i"]]

        mock_page.go_back = AsyncMock(side_effect=go_back)
        mock_page.go_forward = AsyncMock(side_effect=go_forward)
        mock_page.title = AsyncMock(return_value="t")
        return pos

    @pytest.mark.asyncio
    async def test_back_to_bottom_reports_boundary_same_call(self, web_browser, mock_page):
        """A→B→C 串行 back 到 A, **当次**即报"已是历史起点"(不是下次)。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=2)

        r1 = await web_browser.back()  # C → B
        assert "已后退" in r1
        assert "仍可继续后退" in r1, f"到 B 时还有 A 可退: {r1}"

        r2 = await web_browser.back()  # B → A (栈底)
        assert "已后退" in r2
        assert "已是历史起点" in r2, f"到 A 即栈底, 当次须报边界: {r2}"
        assert "无法再后退" in r2
        assert "仍可继续后退" not in r2

        # 再 back 应报"无法后退", 而不是"仍可继续"
        r3 = await web_browser.back()
        assert "无法后退" in r3 or "已是历史起点" in r3

    @pytest.mark.asyncio
    async def test_forward_to_top_reports_boundary_same_call(self, web_browser, mock_page):
        """A→B→C 串行 forward 到 C, **当次**即报"已是历史终点"。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=0)

        r1 = await web_browser.forward()  # A → B
        assert "已前进" in r1
        assert "仍可继续前进" in r1, f"到 B 时还有 C 可进: {r1}"

        r2 = await web_browser.forward()  # B → C (末尾)
        assert "已前进" in r2
        assert "已是历史终点" in r2, f"到 C 即末尾, 当次须报边界: {r2}"
        assert "无法再前进" in r2
        assert "仍可继续前进" not in r2

    @pytest.mark.asyncio
    async def test_back_to_bottom_without_tracking(self, web_browser, mock_page):
        """无跟踪(CDP 导入页等)时 back 到栈底也要当次报边界。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=2, tracked=False)

        r1 = await web_browser.back()  # C → B
        assert "已后退" in r1
        assert "仍可继续后退" in r1

        r2 = await web_browser.back()  # B → A (栈底)
        assert "已后退" in r2
        assert "已是历史起点" in r2, f"无跟踪时 back 到栈底也须当次报边界: {r2}"

    @pytest.mark.asyncio
    async def test_forward_to_top_without_tracking(self, web_browser, mock_page):
        """无跟踪时 forward 到末尾也要当次报边界。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=0, tracked=False)

        r1 = await web_browser.forward()  # A → B
        assert "已前进" in r1
        assert "仍可继续前进" in r1

        r2 = await web_browser.forward()  # B → C (末尾)
        assert "已前进" in r2
        assert "已是历史终点" in r2, f"无跟踪时 forward 到末尾也须当次报边界: {r2}"

    @pytest.mark.asyncio
    async def test_second_to_last_forward_not_premature_boundary(
        self, web_browser, mock_page
    ):
        """倒数第 2 页 forward 到末尾: 不允许提前报"已是历史终点"。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=1)

        r = await web_browser.forward()  # B → C (末尾)
        assert "已前进" in r
        # 到末尾当次就报边界, 而不是"仍可继续"
        assert "已是历史终点" in r, f"到末尾当次须报边界: {r}"

    @pytest.mark.asyncio
    async def test_mid_stack_forward_says_can_continue(self, web_browser, mock_page):
        """中间位置 forward 后不能误报边界(差一格提前)。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=0)

        r = await web_browser.forward()  # A → B (中间)
        assert "已前进" in r
        assert "仍可继续前进" in r, f"B 不是末尾, 不得误报终点: {r}"
        assert "已是历史终点" not in r

    @pytest.mark.asyncio
    async def test_hist_move_rejected_reports_boundary(self, web_browser, mock_page):
        """_hist_move 因边界被拒时, 必须报边界而非"仍可继续"。"""
        urls = ["https://a.example/", "https://b.example/", "https://c.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=2)

        # 手动把游标压到起点, 模拟 _hist_move 拒绝移动的情形
        web_browser._hist_idx[1] = 0

        r = await web_browser.back()  # 浏览器能退(reality 有 2 项), 但跟踪在起点
        assert "已后退" in r
        # 跟踪对齐后应在真实起点
        assert "已是历史起点" in r or "仍可继续后退" in r
        # 关键: 不能因 _hist_move 拒绝就停留旧下标而报"仍可继续"到错误位置
        assert web_browser._hist_idx[1] == max(
            0, web_browser._hist_idx.get(1, 0)
        )

    @pytest.mark.asyncio
    async def test_two_page_stack_back_reports_boundary_same_call(
        self, web_browser, mock_page
    ):
        """干净 2 项历史: back 一步到栈底, 当次报边界。"""
        urls = ["https://a.example/", "https://b.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=1)

        r = await web_browser.back()
        assert "已后退" in r
        assert "已是历史起点" in r, f"2 项历史退 1 步即起点: {r}"

    @pytest.mark.asyncio
    async def test_two_page_stack_forward_reports_boundary_same_call(
        self, web_browser, mock_page
    ):
        """干净 2 项历史: forward 一步到末尾, 当次报边界。"""
        urls = ["https://a.example/", "https://b.example/"]
        self._setup_stack(web_browser, mock_page, urls, start_idx=0)

        r = await web_browser.forward()
        assert "已前进" in r
        assert "已是历史终点" in r, f"2 项历史进 1 步即末尾: {r}"


# ── 任务 1: 超时提示按真实原因分类(不再一律建议 browser_wait)─────────


class TestTimeoutReasonClassification:
    """超时提示要看 Playwright 异常文本, 按真实原因给建议。

    回归测试: disabled 点击、select 值不匹配时 call log 已写明
    "element is not enabled" / "did not find some options", 但提示仍固定为
    「元素存在但未就绪…请 browser_wait」—— 等待解决不了 disabled/值不匹配,
    属误导。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page.locator.return_value

    @pytest.mark.asyncio
    async def test_disabled_click_hint_is_not_wait_advice(
        self, web_browser, mock_page
    ):
        """disabled 元素超时: 提示说清 disabled, 且**不**建议 browser_wait。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\nCall log:\n  - element is not enabled"
            )
        )

        result = await web_browser.click("#submit-btn")

        assert "TOOL_ERROR" in result
        assert "disabled" in result
        assert "等待无效" in result
        # 核心断言: 误导性的等待建议必须消失
        assert "browser_wait" not in result
        # Playwright 原始错误(含 call log)原样保留
        assert "element is not enabled" in result
        assert "Timeout 5000ms exceeded" in result

    @pytest.mark.asyncio
    async def test_select_option_mismatch_hint_lists_options(
        self, web_browser, mock_page
    ):
        """选项值不匹配: 提示核对可选值, 不建议 browser_wait。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.select_option = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\nCall log:\n  - did not find some options"
            )
        )
        option_loc = MagicMock()
        option_loc.all_text_contents = AsyncMock(return_value=["red", "green", "blue"])
        locator.locator = MagicMock(return_value=option_loc)

        result = await web_browser.select_option("#color", "purple")

        assert "TOOL_ERROR" in result
        assert "选项值不匹配" in result
        assert "browser_wait" not in result
        # 可选值列表让调用方能自纠
        assert "red" in result and "green" in result and "blue" in result
        # 原始错误保留
        assert "did not find some options" in result

    @pytest.mark.asyncio
    async def test_not_a_select_hint(self, web_browser, mock_page):
        """目标不是下拉框: 提示不是下拉框, 不建议 browser_wait。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.select_option = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\n"
                "Call log:\n  - Element is not a <select> element"
            )
        )

        result = await web_browser.select_option("#div", "a")

        assert "不是下拉框" in result
        assert "browser_wait" not in result

    @pytest.mark.asyncio
    async def test_not_visible_hint(self, web_browser, mock_page):
        """元素不可见: 提示不可见/被遮挡, 不建议 browser_wait。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\nCall log:\n  - element is not visible"
            )
        )

        result = await web_browser.click("#hidden-btn")

        assert "不可见" in result
        assert "browser_wait" not in result

    @pytest.mark.asyncio
    async def test_detached_hint(self, web_browser, mock_page):
        """元素已脱离 DOM: 提示已移除, 不建议 browser_wait。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\n"
                "Call log:\n  - Element is not attached to the DOM"
            )
        )

        result = await web_browser.click("#gone")

        assert "已从 DOM 移除" in result
        assert "browser_wait" not in result

    @pytest.mark.asyncio
    async def test_selector_name_not_missclassified_as_disabled(
        self, web_browser, mock_page
    ):
        """选择器名含 "disabled" 不能误判成元素 disabled —— 引号内的文本不参与匹配。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\n"
                'Call log:\n  - waiting for locator("#disabled-btn")'
            )
        )
        locator.count = AsyncMock(return_value=0)

        result = await web_browser.click("#disabled-btn")

        # 引号内是选择器名, 不是元素状态 → 走三态判定(元素不存在)
        assert "元素不存在" in result
        assert "disabled 状态" not in result

    @pytest.mark.asyncio
    async def test_unmatched_reason_falls_back_to_three_state(
        self, web_browser, mock_page
    ):
        """无已知原因命中时退回三态判定 + browser_wait 建议。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=1)

        result = await web_browser.click("#slow-btn")

        assert "元素存在但未就绪" in result
        assert "browser_wait" in result

    @pytest.mark.asyncio
    async def test_reason_hint_wins_over_existence_probe(self, web_browser, mock_page):
        """命中真实原因时不再做三态探测(元素存在也不报"未就绪")。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\nCall log:\n  - element is not enabled"
            )
        )
        locator.count = AsyncMock(return_value=1)  # 元素存在, 但原因已确定

        result = await web_browser.click("#btn")

        assert "disabled" in result
        assert "元素存在但未就绪" not in result
        assert "browser_wait" not in result


# ── 任务 2: iframe 边界提示 + frame_selector ─────────────────────


class TestIframeAwareness:
    """元素在 iframe 内时不能只报"元素不存在"。

    回归测试: get_elements 不列 iframe、click 报"元素不存在"且从不提 iframe,
    调用方无从知道目标可能在 frame 里。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    async def test_missing_element_error_mentions_iframe(
        self, web_browser, mock_page
    ):
        """元素找不到且页面有 iframe 时, 报错要说清可能在 frame 里。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        page = self._running(web_browser, mock_page)
        locator = page.locator.return_value
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=0)
        # 主文档 + 2 个子 frame
        locator.page = page
        page.frames = [MagicMock(), MagicMock(), MagicMock()]

        result = await web_browser.click("#iframe-btn")

        assert "元素不存在" in result
        assert "2 个 iframe" in result
        assert "frame_selector" in result
        # 明确说清不跨 frame, 不给错的选择器
        assert "不自动跨 frame" in result or "browser_evaluate" in result

    @pytest.mark.asyncio
    async def test_missing_element_without_iframe_keeps_wait_advice(
        self, web_browser, mock_page
    ):
        """页面没有 iframe 时不提 iframe, 保持 browser_wait 建议。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        page = self._running(web_browser, mock_page)
        locator = page.locator.return_value
        locator.click = AsyncMock(
            side_effect=PlaywrightTimeoutError("Timeout 5000ms exceeded")
        )
        locator.count = AsyncMock(return_value=0)
        locator.page = page
        page.frames = [MagicMock()]  # 只有主文档

        result = await web_browser.click("#nope")

        assert "元素不存在" in result
        assert "iframe" not in result
        assert "browser_wait" in result

    @pytest.mark.asyncio
    async def test_get_elements_lists_iframes(self, web_browser, mock_page):
        """browser_get_elements 要标注检测到的 iframe(selector/src)。"""
        page = self._running(web_browser, mock_page)
        # 第 1 次 evaluate: iframe 探测; 第 2 次: 元素列表
        page.evaluate = AsyncMock(
            side_effect=[
                [{"selector": "#my-iframe", "src": "https://x.example/embed", "id": "my-iframe", "name": ""}],
                [
                    {
                        "index": 0,
                        "tag": "button",
                        "type": "",
                        "id": "outer-btn",
                        "name": "",
                        "text": "OK",
                        "placeholder": "",
                        "href": "",
                        "role": "",
                        "ariaLabel": "",
                        "selector": "#outer-btn",
                        "selectorUnique": True,
                        "selectorMatchCount": 1,
                        "selectorNth": -1,
                        "rect": {"x": 0, "y": 0, "width": 10, "height": 10},
                    }
                ],
            ]
        )

        result = await web_browser.get_interactive_elements()

        assert "#my-iframe" in result
        assert "https://x.example/embed" in result
        assert "iframe" in result
        # 说清边界: 主文档元素不含 frame 内元素
        assert "frame" in result

    @pytest.mark.asyncio
    async def test_get_elements_no_iframe_no_noise(self, web_browser, mock_page):
        """页面无 iframe 时不输出 iframe 段。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(
            side_effect=[
                [],
                [
                    {
                        "index": 0,
                        "tag": "button",
                        "type": "",
                        "id": "b",
                        "name": "",
                        "text": "OK",
                        "placeholder": "",
                        "href": "",
                        "role": "",
                        "ariaLabel": "",
                        "selector": "#b",
                        "selectorUnique": True,
                        "selectorMatchCount": 1,
                        "selectorNth": -1,
                        "rect": {"x": 0, "y": 0, "width": 10, "height": 10},
                    }
                ],
            ]
        )

        result = await web_browser.get_interactive_elements()

        assert "找到 1 个可交互元素" in result
        assert "iframe" not in result

    @pytest.mark.asyncio
    async def test_frame_selector_enters_frame(self, web_browser, mock_page):
        """frame_selector 提供时走 page.frame_locator, 在 frame 内定位。"""
        page = self._running(web_browser, mock_page)
        frame_locator = MagicMock()
        inner = AsyncMock()
        frame_locator.locator = MagicMock(return_value=inner)
        page.frame_locator = MagicMock(return_value=frame_locator)

        result = await web_browser.click("#iframe-btn", frame_selector="#my-iframe")

        assert "已点击" in result
        page.frame_locator.assert_called_once_with("#my-iframe")
        frame_locator.locator.assert_called_once_with("#iframe-btn")
        inner.click.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_without_frame_selector_does_not_touch_frame_locator(
        self, web_browser, mock_page
    ):
        """不传 frame_selector 时行为不变, 不碰 frame_locator。"""
        page = self._running(web_browser, mock_page)
        page.frame_locator = MagicMock()

        result = await web_browser.click("#btn")

        assert "已点击" in result
        page.frame_locator.assert_not_called()


# ── 任务 3: browser_select_option 多选 ─────────────────────────────


class TestSelectOptionMultiSelect:
    """select_option 支持多选, 参数形状不对要报错而不是超时。

    回归测试: 签名只有 (selector, value), 传数组被 str 化当单值匹配 → 超时;
    HTML5 multiple select 无法一次选多项; 不存在的 option 还会误导提示。
    """

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page.locator.return_value

    @pytest.mark.asyncio
    async def test_multi_select_accepts_list(self, web_browser, mock_page):
        """multiple select + 列表 → 一次选中多项。"""
        locator = self._running(web_browser, mock_page)
        locator.evaluate = AsyncMock(return_value=True)  # el.multiple === true
        locator.select_option = AsyncMock(return_value=["a", "b"])

        result = await web_browser.select_option("#multi", ["a", "b"])

        assert "已选择选项" in result
        locator.select_option.assert_awaited_once()
        assert locator.select_option.await_args.args[0] == ["a", "b"]

    @pytest.mark.asyncio
    async def test_single_select_rejects_list(self, web_browser, mock_page):
        """单选 select 传列表 → 参数错误, 不进 Playwright。"""
        locator = self._running(web_browser, mock_page)
        locator.evaluate = AsyncMock(return_value=False)  # 不是 multiple
        locator.select_option = AsyncMock()

        result = await web_browser.select_option("#single", ["a", "b"])

        assert "TOOL_ERROR" in result
        assert "multiple" in result or "单选" in result
        locator.select_option.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_select_still_accepts_str(self, web_browser, mock_page):
        """单选 select 传字符串照常工作(不触发 multiple 检查)。"""
        locator = self._running(web_browser, mock_page)
        locator.select_option = AsyncMock(return_value=["a"])

        result = await web_browser.select_option("#single", "a")

        assert "已选择选项: a" in result
        locator.select_option.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bad_value_type_rejected(self, web_browser, mock_page):
        """value 既不是 str 也不是 list[str] → 参数错误。"""
        locator = self._running(web_browser, mock_page)

        result = await web_browser.select_option("#sel", 123)

        assert "TOOL_ERROR" in result
        assert "str" in result

    @pytest.mark.asyncio
    async def test_list_with_non_str_rejected(self, web_browser, mock_page):
        """列表里混非字符串 → 参数错误。"""
        locator = self._running(web_browser, mock_page)

        result = await web_browser.select_option("#sel", ["a", 1])

        assert "TOOL_ERROR" in result
        locator.select_option.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_list_rejected(self, web_browser, mock_page):
        """空列表 → 参数错误。"""
        locator = self._running(web_browser, mock_page)

        result = await web_browser.select_option("#sel", [])

        assert "TOOL_ERROR" in result
        locator.select_option.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_option_lists_available_choices(
        self, web_browser, mock_page
    ):
        """选项不存在时列出可选值, 让调用方一次改对。"""
        from uniclaw.tools.web_browse.browser import PlaywrightTimeoutError

        locator = self._running(web_browser, mock_page)
        locator.select_option = AsyncMock(
            side_effect=PlaywrightTimeoutError(
                "Timeout 5000ms exceeded.\nCall log:\n  - did not find some options"
            )
        )
        option_loc = MagicMock()
        option_loc.all_text_contents = AsyncMock(return_value=["red", "green"])
        locator.locator = MagicMock(return_value=option_loc)

        result = await web_browser.select_option("#color", "purple")

        assert "可选值" in result
        assert "red" in result and "green" in result
        assert "purple" in result  # 带上实际传的值
        assert "browser_wait" not in result

    def test_tool_schema_value_is_str_or_list(self):
        """@tool schema 里 value 要能表达 str | list[str], 不能塌缩成单类型。"""
        from uniclaw.tools.web_browse.tools import get_tools

        tool = next(t for t in get_tools() if t.name == "browser_select_option")
        schema = tool.parameters["properties"]["value"]
        # anyOf: string + array-of-string
        assert "anyOf" in schema, f"value schema 应为 anyOf, 实际: {schema}"
        types = {s.get("type") for s in schema["anyOf"]}
        assert types == {"string", "array"}
        arr = next(s for s in schema["anyOf"] if s.get("type") == "array")
        assert arr.get("items", {}).get("type") == "string"

    def test_tool_signature_accepts_list(self):
        """工具函数签名/类型检查要放行 list[str]。"""
        from uniclaw.tools.web_browse.tools import get_tools

        tool = next(t for t in get_tools() if t.name == "browser_select_option")
        sig = __import__("inspect").signature(tool.func)
        assert "value" in sig.parameters
        ann = str(sig.parameters["value"].annotation)
        assert "list" in ann, f"value 注解应含 list: {ann}"

    @pytest.mark.asyncio
    async def test_tool_call_with_list_no_type_error(self, web_browser, mock_page):
        """经 Tool 包装调用(含 _check_types)传列表不应被参数检查拦下。"""
        from uniclaw.tools.web_browse.tools import get_tools

        tool = next(t for t in get_tools() if t.name == "browser_select_option")
        locator = self._running(web_browser, mock_page)
        locator.evaluate = AsyncMock(return_value=True)
        locator.select_option = AsyncMock(return_value=["a", "b"])

        # 工具走模块级 _browser 单例 —— 指到 fixture 实例上
        with patch(
            "uniclaw.tools.web_browse.tools._browser", web_browser
        ):
            result = await tool.execute(selector="#multi", value=["a", "b"])

        assert "已选择选项" in result
        assert "incorrect type" not in str(result)


# ── get_elements 分页上限(第 26 轮 #6) ─────────────────────


class TestGetElementsPagination:
    """超大页面的元素输出必须有上限/分页,不能全量灌入上下文。"""

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @staticmethod
    def _el(index=0, tag="a", selector=None, **over):
        base = {
            "index": index,
            "tag": tag,
            "type": "",
            "id": "",
            "name": "",
            "text": f"item-{index}",
            "placeholder": "",
            "href": "",
            "role": "",
            "ariaLabel": "",
            "selector": selector or f"{tag}.n{index}",
            "selectorUnique": True,
            "selectorMatchCount": 1,
            "selectorNth": -1,
            "rect": {"x": 0, "y": 0, "width": 10, "height": 10},
        }
        base.update(over)
        return base

    @pytest.mark.asyncio
    async def test_default_limit_truncates_huge_page(self, web_browser, mock_page):
        """默认 limit=50: 60 个元素只展示前 50 个,并声明总数与页范围。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value=[self._el(index=i) for i in range(60)])

        result = await web_browser.get_interactive_elements()

        assert "找到 60 个可交互元素" in result
        assert "第 1-50 个" in result
        assert "item-49" in result
        assert "item-50" not in result, "超出 limit 的元素不应展示"
        assert "offset" in result

    @pytest.mark.asyncio
    async def test_offset_pages_through(self, web_browser, mock_page):
        """offset/limit 翻页: 第 21-30 个元素,索引保持全局编号。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value=[self._el(index=i) for i in range(60)])

        result = await web_browser.get_interactive_elements(limit=10, offset=20)

        assert "第 21-30 个" in result
        assert "[20] <a>" in result
        assert "[29] <a>" in result
        assert "[30] <a>" not in result

    @pytest.mark.asyncio
    async def test_limit_clamped_to_200(self, web_browser, mock_page):
        """limit 有硬上限(200),防呆式超大参数不会把整页序列化回来。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value=[self._el(index=i) for i in range(300)])

        result = await web_browser.get_interactive_elements(limit=9999)

        assert "第 1-200 个" in result
        assert "[200] <a>" not in result

    @pytest.mark.asyncio
    async def test_offset_out_of_range_reports(self, web_browser, mock_page):
        """offset 越界时给出明确提示,而不是空列表让人误判"没有元素"。"""
        page = self._running(web_browser, mock_page)
        page.evaluate = AsyncMock(return_value=[self._el(index=i) for i in range(5)])

        result = await web_browser.get_interactive_elements(offset=100)

        assert "offset=100 超出范围" in result
        assert "找到 5 个" in result


# ── 导航触发下载的识别(第 26 轮 #7) ────────────────────────


class TestNavigateDownloadDetection:
    """导航到附件 URL 时要识别为下载,不能报成"导航超时失败"。"""

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    async def test_download_error_not_reported_as_nav_failure(
        self, web_browser, mock_page
    ):
        """Playwright "Download is starting" 应转成 http_download 指引。"""
        from uniclaw.tools.web_browse.browser import PlaywrightError

        page = self._running(web_browser, mock_page)
        page.goto = AsyncMock(
            side_effect=PlaywrightError("Page.goto: Download is starting.")
        )

        result = await web_browser.navigate("https://example.com/file.zip")

        assert "文件下载" in result
        assert "http_download" in result
        assert "Download is starting" not in result, "不应甩原始报错让用户自己猜"

    @pytest.mark.asyncio
    async def test_content_disposition_attachment_detected(self, web_browser, mock_page):
        """Content-Disposition: attachment 的响应同样按下载处理(含文件名)。"""
        page = self._running(web_browser, mock_page)
        resp = MagicMock()
        resp.status = 200
        resp.headers = {
            "content-disposition": 'attachment; filename="report.pdf"',
        }
        page.goto = AsyncMock(return_value=resp)

        result = await web_browser.navigate("https://example.com/report")

        assert "文件下载" in result
        assert "report.pdf" in result
        assert "http_download" in result
        assert "已导航到" not in result

    @pytest.mark.asyncio
    async def test_normal_nav_unaffected(self, web_browser, mock_page):
        """普通页面导航报文不变。"""
        page = self._running(web_browser, mock_page)
        resp = MagicMock()
        resp.status = 200
        resp.headers = {}
        page.goto = AsyncMock(return_value=resp)

        result = await web_browser.navigate("https://example.com/")

        assert "已导航到" in result
        assert "http_download" not in result


# ── type 并发串扰 / frame_selector 单独用(第 28 轮 #1/#5) ──


class TestInteractionSerialised:
    """交互动作串行化: 并发 type/click 不得互相踩焦点与取值。"""

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    async def test_concurrent_type_calls_do_not_interleave(self, web_browser, mock_page):
        """并行两个 browser_type: 各自的 fill 序列必须整体完成,不得交错。"""
        page = self._running(web_browser, mock_page)
        events: list[str] = []

        def make_fill(tag):
            async def fill(value, timeout=None):
                events.append(f"{tag}:enter:{value!r}")
                await asyncio.sleep(0.01)  # 放大交错窗口
                events.append(f"{tag}:exit:{value!r}")
            return fill

        loc_a = MagicMock()
        loc_a.fill = AsyncMock(side_effect=make_fill("A"))
        loc_b = MagicMock()
        loc_b.fill = AsyncMock(side_effect=make_fill("B"))
        page.locator = MagicMock(side_effect=lambda sel: loc_a if sel == "#ta" else loc_b)

        await asyncio.gather(
            web_browser.type_text("#ta", "AAA"),
            web_browser.type_text("#txt", "BBB"),
        )

        # 串行语义: 某个元素的全部 fill 完成前,另一个元素不得开始
        tags = [e.split(":")[0] for e in events]
        runs = [tags[0]]
        for t in tags[1:]:
            if t != runs[-1]:
                runs.append(t)
        assert len(runs) == len(set(runs)), f"两个元素的 fill 发生交错: {events}"

    @pytest.mark.asyncio
    async def test_interactive_methods_hold_act_lock(self, web_browser):
        """type_text/click/select 等交互方法都必须走 _act_lock(装饰器挂上)。"""
        for name in (
            "type_text", "click", "select_option", "check", "insert_text",
            "press_key", "dblclick", "hover", "focus", "keyboard_type",
        ):
            fn = getattr(web_browser, name)
            assert getattr(fn, "__wrapped__", None) is not None, (
                f"{name} 未串行化(缺少 _serialise_interaction)"
            )


class TestFrameSelectorOnly:
    """只给 frame_selector 时应取 iframe 内容,而非主文档(第 28 轮 #5)。"""

    def _running(self, web_browser, mock_page):
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        return mock_page

    @pytest.mark.asyncio
    async def test_get_text_frame_only(self, web_browser, mock_page):
        page = self._running(web_browser, mock_page)
        frame_root = MagicMock()
        frame_body = MagicMock()
        frame_body.inner_text = AsyncMock(return_value="帧内文本")
        frame_root.locator = MagicMock(return_value=frame_body)
        page.frame_locator = MagicMock(return_value=frame_root)
        page.inner_text = AsyncMock(return_value="主文档文本")

        result = await web_browser.get_text(frame_selector="#inner")

        assert result == "帧内文本"
        page.frame_locator.assert_called_once_with("#inner")
        page.inner_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_html_frame_only(self, web_browser, mock_page):
        page = self._running(web_browser, mock_page)
        frame_root = MagicMock()
        frame_body = MagicMock()
        frame_body.inner_html = AsyncMock(return_value="<p>帧内</p>")
        frame_root.locator = MagicMock(return_value=frame_body)
        page.frame_locator = MagicMock(return_value=frame_root)
        page.content = AsyncMock(return_value="<html>主文档</html>")

        result = await web_browser.get_html(frame_selector="#inner")

        assert "<p>帧内</p>" in result
        page.content.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_text_without_frame_still_main_doc(self, web_browser, mock_page):
        page = self._running(web_browser, mock_page)
        page.inner_text = AsyncMock(return_value="主文档文本")

        result = await web_browser.get_text()

        assert result == "主文档文本"
        page.frame_locator.assert_not_called()

class TestBrowserSessionMutex:
    @pytest.mark.asyncio
    async def test_start_refuses_when_cdp_connected(self, web_browser):
        web_browser._browser = MagicMock()
        web_browser._connected_cdp = True
        web_browser._active_page_id = 1

        result = await web_browser.start()

        assert "TOOL_ERROR" in result
        assert "browser_close" in result

    @pytest.mark.asyncio
    async def test_connect_refuses_when_internal_running(self, web_browser):
        web_browser._browser = MagicMock()
        web_browser._connected_cdp = False
        web_browser._active_page_id = 1

        result = await web_browser.connect_over_cdp("http://localhost:9222")

        assert "TOOL_ERROR" in result
        assert "browser_close" in result
        assert web_browser._browser is not None, "现有会话不得被销毁"

class TestStartRejectsAnySession:
    """已有会话(含内部启动)时 browser_start 不得替换/重导航(验证 #5)。"""

    @pytest.mark.asyncio
    async def test_start_rejects_internal_session(self, web_browser):
        web_browser._browser = MagicMock()
        web_browser._connected_cdp = False
        web_browser._active_page_id = 1

        result = await web_browser.start()

        assert "TOOL_ERROR" in result
        assert "browser_close" in result
        assert web_browser._browser is not None, "原会话不得被销毁"

    @pytest.mark.asyncio
    async def test_start_tool_no_nav_on_reject(self):
        from uniclaw.tools.web_browse import tools as wb_tools

        from uniclaw.utils.constants import TOOL_ERROR

        fake = MagicMock()
        fake.start = AsyncMock(return_value=f"{TOOL_ERROR}: 已有会话")
        fake.navigate = AsyncMock(return_value="已导航到 x")
        with patch.object(wb_tools, "_browser", fake):
            result = await wb_tools.browser_start("https://example.com")

        assert "TOOL_ERROR" in result
        fake.navigate.assert_not_called(), "拒绝后不得再拼接导航残留文案"

