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
    async def test_back(self, web_browser, mock_page):
        """测试后退"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.back()
        assert "已后退" in result

    @pytest.mark.asyncio
    async def test_forward(self, web_browser, mock_page):
        """测试前进"""
        web_browser._browser = MagicMock()
        web_browser._pages = {1: mock_page}
        web_browser._active_page_id = 1

        result = await web_browser.forward()
        assert "已前进" in result

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
