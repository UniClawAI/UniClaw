"""web_browse 模块的单元测试

测试基于 Playwright 的浏览器自动化工具，支持多页面管理。
"""

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
        """让 mock page 的 url 随 go_back/go_forward 变化, 模拟真实导航。"""
        page.evaluate = AsyncMock(return_value=2)
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
    async def test_forward_to_last_navigated_url_says_boundary(
        self, web_browser, mock_page
    ):
        """前进到最近一次 navigate 的 URL 时视为历史终点。"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1
        web_browser._last_urls = {1: "https://example.com/b"}
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
        """测试已启动时再连接会先关闭旧浏览器"""
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

        assert "已通过 CDP 连接" in result
        old_browser.close.assert_called_once()

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


class TestConnectKeepsExistingSession:
    """browser_connect 失败不能连带销毁现有浏览器会话。

    回归测试: 旧实现先 await close() 再尝试连接,连不上时原有会话已被销毁,
    后续 get_url/close 全报"浏览器未启动"。
    """

    @pytest.mark.asyncio
    async def test_connect_failure_keeps_session(self, web_browser, mock_page):
        """连不上时现有会话要保持可用。"""
        old_browser = AsyncMock()
        web_browser._browser = old_browser
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        fake_pw = AsyncMock()
        fake_pw.start = AsyncMock(return_value=fake_pw)  # .start() 要返回自身
        fake_pw.chromium.connect_over_cdp = AsyncMock(side_effect=OSError("refused"))

        with patch(
            "uniclaw.tools.web_browse.browser._get_async_playwright",
            return_value=lambda: fake_pw,
        ):
            result = await web_browser.connect_over_cdp("http://localhost:9999")

        assert "无法连接" in result
        assert "未受影响" in result
        # 关键: 现有会话不能被销毁
        old_browser.close.assert_not_called()
        assert web_browser._browser is old_browser
        assert web_browser._active_page_id == 1
        assert 1 in web_browser._pages


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
