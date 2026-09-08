"""Web Browse 工具 — 基于 Playwright 的浏览器自动化操作。

提供导航、点击、输入、截图、执行 JS 等完整浏览器控制能力。
支持多页面(标签页)管理。
"""

from __future__ import annotations

from typing import Optional

from uniclaw.tools.base import tool
from .browser import WebBrowser

# 全局浏览器实例
_browser = WebBrowser()


# ── 浏览器生命周期 ────────────────────────────────────────────


@tool
async def browser_start(url: str, headless: bool = True) -> str:
    """启动浏览器并导航到指定 URL。默认使用无头模式,适合自动化任务。如需用户交互(如登录),可设置 headless=False 显示浏览器窗口。

    Args:
        url: 启动后立即导航到该 URL。
        headless: 是否使用无头模式,默认 True。设置为 False 可显示浏览器窗口。

    Returns:
        操作结果消息。
    """
    result = await _browser.start(headless=headless)
    nav_result = await _browser.navigate(url)
    return f"{result}\n{nav_result}"


@tool
async def browser_close() -> str:
    """关闭浏览器并释放所有页面。如果当前是通过 CDP 连接的外部浏览器,则仅断开连接,外部浏览器进程保持运行。

    Returns:
        操作结果消息。
    """
    return await _browser.close()


@tool
async def browser_connect(cdp_url: str = "http://localhost:9222") -> str:
    """通过 CDP (Chrome DevTools Protocol) 连接到已运行的外部浏览器,复用其标签页和登录状态。

    使用前需以远程调试参数启动浏览器(部分 Chrome 版本会忽略无 --user-data-dir 的调试参数,
    此时需额外指定一个独立的用户数据目录),例如:
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\chrome-debug-profile"

    连接后所有页面操作工具(browser_navigate/click/type 等)均可直接使用。
    browser_close 断开连接时不会关闭外部浏览器进程。

    Args:
        cdp_url: 浏览器的 CDP 调试地址,默认 "http://localhost:9222"。

    Returns:
        操作结果消息,包含导入的页面数量。
    """
    return await _browser.connect_over_cdp(cdp_url)


# ── 页面管理 ──────────────────────────────────────────────────


@tool
async def browser_new_page() -> str:
    """创建新页面(标签页)并切换到该页面。

    Returns:
        操作结果消息,包含新页面 ID。
    """
    _, msg = await _browser.new_page()
    return msg


@tool
async def browser_close_page(page_id: int) -> str:
    """关闭指定页面。如果关闭的是活动页面,会自动切换到其他页面。

    Args:
        page_id: 要关闭的页面 ID。

    Returns:
        操作结果消息。
    """
    return await _browser.close_page(page_id)


@tool
async def browser_switch_page(page_id: int) -> str:
    """切换到指定页面。

    Args:
        page_id: 目标页面 ID。

    Returns:
        操作结果消息,包含页面标题。
    """
    return await _browser.switch_page(page_id)


@tool
async def browser_list_pages() -> str:
    """列出所有打开的页面,显示页面 ID、标题和 URL。

    Returns:
        页面列表信息。
    """
    return await _browser.list_pages()


# ── 页面操作 ──────────────────────────────────────────────────


@tool
async def browser_navigate(url: str, page_id: Optional[int] = None) -> str:
    """导航到指定 URL。

    Args:
        url: 要导航到的网页 URL。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息,包含页面标题。
    """
    return await _browser.navigate(url, page_id)


@tool
async def browser_click(
    selector: str, timeout: int = 5000, page_id: Optional[int] = None
) -> str:
    """点击页面元素。

    Args:
        selector: CSS 选择器或 XPath 表达式(以 // 开头表示 XPath)。
        timeout: 等待元素出现的超时时间(毫秒),默认 5000。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.click(selector, timeout, page_id)


@tool
async def browser_type(
    selector: str,
    text: str,
    clear: bool = True,
    timeout: int = 5000,
    page_id: Optional[int] = None,
) -> str:
    """在指定元素中输入文本。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        text: 要输入的文本内容。
        clear: 是否先清空输入框,默认 True。
        timeout: 等待元素出现的超时时间(毫秒),默认 5000。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.type_text(selector, text, clear, timeout, page_id)


@tool
async def browser_screenshot(
    selector: Optional[str] = None,
    full_page: bool = False,
    save_path: Optional[str] = None,
    page_id: Optional[int] = None,
) -> list | str:
    """截取页面或指定元素的截图(仅用于查看页面效果,不要用于定位元素)。

    ⚠️ 如需点击、输入等操作,必须使用 browser_get_elements 获取精确选择器,不要通过截图猜测坐标。
    ⚠️ 如需获取页面文本内容,请使用 browser_get_text,不要用截图来"看"网页文字。

    Args:
        selector: 可选,CSS 选择器或 XPath 表达式,截取特定元素。不提供则截取整个页面。
        full_page: 是否截取完整页面(包括滚动区域),默认 False。
        save_path: 可选,截图保存的本地文件路径(如 "screenshot.png")。不提供则返回 base64 图片供 LLM 查看。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        包含截图的多模态内容列表,或保存成功的消息。
    """
    return await _browser.screenshot(selector, full_page, save_path, page_id)


@tool
async def browser_get_text(
    selector: Optional[str] = None, page_id: Optional[int] = None
) -> str:
    """获取页面或指定元素的文本内容。

    Args:
        selector: 可选,CSS 选择器或 XPath 表达式。不提供则获取整个页面文本。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        文本内容。
    """
    return await _browser.get_text(selector, page_id)


@tool
async def browser_get_html(
    selector: Optional[str] = None, page_id: Optional[int] = None
) -> str:
    """获取页面或指定元素的 HTML 内容。

    Args:
        selector: 可选,CSS 选择器或 XPath 表达式。不提供则获取整个页面 HTML。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        HTML 内容。
    """
    return await _browser.get_html(selector, page_id)


@tool
async def browser_get_attribute(
    selector: str, attribute: str, page_id: Optional[int] = None
) -> str:
    """获取指定元素的属性值。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        attribute: 属性名称(如 href、src、value 等)。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        属性值。
    """
    return await _browser.get_attribute(selector, attribute, page_id)


@tool
async def browser_get_elements(
    page_id: Optional[int] = None,
    include_cursor_interactive: bool = False,
    compact: bool = False,
    depth: Optional[int] = None,
    scope: Optional[str] = None,
) -> str:
    """获取页面上所有可交互元素的信息,可穿透付费墙等遮挡层(优先使用此工具而非截图)。

    ⚠️ 重要:在点击、输入等操作前,必须先调用此工具获取元素选择器,不要依赖截图猜测坐标。
    截图只能看到视觉效果,无法获取精确的元素选择器和属性信息。

    返回按钮、输入框、链接、下拉框等可交互元素的:
    - 标签、类型、id、name、文本内容
    - placeholder、href、role、aria-label 等属性
    - 推荐的 CSS 选择器(可直接用于 click/type 等操作)
    - 元素位置和尺寸

    Args:
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。
        include_cursor_interactive: 是否包含 cursor:pointer 的元素(如 div、span 等自定义可点击元素),默认 False。
        compact: 精简输出,仅保留有文本/id/role 的元素,默认 False。
        depth: 可选,限制 DOM 遍历深度。
        scope: 可选,限定 CSS 选择器范围(如 "#main", ".sidebar")。

    Returns:
        可交互元素列表,包含每个元素的选择器和属性信息。
    """
    return await _browser.get_interactive_elements(
        page_id, include_cursor_interactive, compact, depth, scope
    )


@tool
async def browser_wait(
    selector: Optional[str] = None,
    state: str = "visible",
    timeout: int = 10000,
    page_id: Optional[int] = None,
    text: Optional[str] = None,
    url: Optional[str] = None,
    load_state: Optional[str] = None,
    js_condition: Optional[str] = None,
) -> str:
    """等待指定条件满足。支持多种等待模式,按优先级: js_condition > load_state > url > text > selector。

    示例:
    - browser_wait(selector="#login-btn") — 等待元素可见
    - browser_wait(text="登录成功") — 等待页面出现指定文本
    - browser_wait(url="**/dashboard") — 等待 URL 匹配模式
    - browser_wait(load_state="networkidle") — 等待网络空闲
    - browser_wait(js_condition="window.__ready === true") — 等待 JS 条件为真
    - browser_wait() — 无参数时等待当前页面 load 完成

    Args:
        selector: CSS 选择器或 XPath 表达式(以 // 或 ( 开头表示 XPath)。等待元素达到指定状态。
        state: 元素等待状态,可选值: "attached"(已附加)、"detached"(已分离)、"visible"(可见)、"hidden"(隐藏)。默认 "visible"。
        timeout: 超时时间(毫秒),默认 10000。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。
        text: 等待页面 body 中出现指定文本(轮询检测)。
        url: 等待当前 URL 匹配 glob 模式,如 "**/dashboard"、"**/login**"。
        load_state: 等待页面加载状态,可选值: "load"(页面加载完成)、"domcontentloaded"(DOM 就绪)、"networkidle"(网络空闲)、"network"(网络请求完成)。
        js_condition: 等待 JavaScript 表达式返回 truthy 值,如 "window.__ready === true"。

    Returns:
        操作结果消息。
    """
    return await _browser.wait_for(
        selector=selector,
        state=state,
        timeout=timeout,
        page_id=page_id,
        text=text,
        url=url,
        load_state=load_state,
        js_condition=js_condition,
    )


@tool
async def browser_evaluate(
    expression: str,
    page_id: Optional[int] = None,
    is_base64: bool = False,
    arg: Optional[str] = None,
) -> str:
    """在页面中执行 JavaScript 表达式并返回结果。支持多行代码(最后一行为返回值)。

    Args:
        expression: 要执行的 JavaScript 表达式或代码。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。
        is_base64: 是否为 base64 编码的 JS 代码,默认 False。
        arg: 可选,传递给 JS 表达式的参数,在 JS 中通过 arguments[0] 访问。

    Returns:
        执行结果。
    """
    return await _browser.evaluate(expression, page_id, is_base64, arg)


@tool
async def browser_scroll(
    direction: str = "down", amount: int = 500, page_id: Optional[int] = None
) -> str:
    """滚动页面。

    Args:
        direction: 滚动方向,可选值: "up"(向上)、"down"(向下)。
        amount: 滚动像素量,默认 500。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.scroll(direction, amount, page_id)


@tool
async def browser_back(page_id: Optional[int] = None) -> str:
    """浏览器后退。

    Args:
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.back(page_id)


@tool
async def browser_forward(page_id: Optional[int] = None) -> str:
    """浏览器前进。

    Args:
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.forward(page_id)


@tool
async def browser_reload(page_id: Optional[int] = None) -> str:
    """刷新当前页面。

    Args:
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.reload(page_id)


@tool
async def browser_get_url(page_id: Optional[int] = None) -> str:
    """获取当前页面 URL。

    Args:
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        当前页面 URL。
    """
    return await _browser.get_url(page_id)


@tool
async def browser_get_title(page_id: Optional[int] = None) -> str:
    """获取当前页面标题。

    Args:
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        当前页面标题。
    """
    return await _browser.get_title(page_id)


@tool
async def browser_toggle_mode(headless: bool = True) -> str:
    """切换浏览器显示模式,保留所有页面 URL。CDP 连接的外部浏览器不支持切换。

    当需要用户交互(如登录、验证码)时,可切换到有头模式(headless=False)显示浏览器窗口。
    交互完成后切换回无头模式(headless=True)继续自动化。

    Args:
        headless: True 为无头模式(隐藏窗口),False 为有头模式(显示窗口)。

    Returns:
        操作结果消息。
    """
    return await _browser.switch_mode(headless=headless)


@tool
async def browser_press_key(key: str, page_id: Optional[int] = None) -> str:
    """按下键盘按键。

    Args:
        key: 按键名称,如 "Enter"、"Tab"、"Escape"、"ArrowUp"、"ArrowDown" 等。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.press_key(key, page_id)


@tool
async def browser_select_option(
    selector: str, value: str, page_id: Optional[int] = None
) -> str:
    """选择下拉框选项。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        value: 要选择的选项值。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.select_option(selector, value, page_id)


@tool
async def browser_check(
    selector: str, checked: bool = True, page_id: Optional[int] = None
) -> str:
    """勾选或取消勾选复选框。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        checked: True 为勾选,False 为取消勾选。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.check(selector, checked, page_id)


@tool
async def browser_hover(selector: str, page_id: Optional[int] = None) -> str:
    """鼠标悬停在元素上。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.hover(selector, page_id)


@tool
async def browser_drag(source: str, target: str, page_id: Optional[int] = None) -> str:
    """拖拽元素到目标位置。

    Args:
        source: 源元素的 CSS 选择器或 XPath 表达式。
        target: 目标元素的 CSS 选择器或 XPath 表达式。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.drag(source, target, page_id)


@tool
async def browser_dblclick(
    selector: str, timeout: int = 5000, page_id: Optional[int] = None
) -> str:
    """双击页面元素。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        timeout: 等待元素出现的超时时间(毫秒),默认 5000。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.dblclick(selector, timeout, page_id)


@tool
async def browser_focus(selector: str, page_id: Optional[int] = None) -> str:
    """聚焦到指定元素。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.focus(selector, page_id)


@tool
async def browser_scroll_into_view(selector: str, page_id: Optional[int] = None) -> str:
    """将指定元素滚动到可见区域。适用于需要操作屏幕外的元素时。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.scroll_into_view(selector, page_id)


@tool
async def browser_key_down(key: str, page_id: Optional[int] = None) -> str:
    """按住键盘按键不放。与 browser_key_up 配合使用可实现组合键(如 Shift 选中)。

    Args:
        key: 按键名称,如 "Shift"、"Control"、"Alt" 等。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.key_down(key, page_id)


@tool
async def browser_key_up(key: str, page_id: Optional[int] = None) -> str:
    """松开键盘按键。与 browser_key_down 配合使用。

    Args:
        key: 按键名称,如 "Shift"、"Control"、"Alt" 等。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.key_up(key, page_id)


@tool
async def browser_keyboard_type(text: str, page_id: Optional[int] = None) -> str:
    """使用真实按键事件逐字输入文本。适用于需要触发 keydown/keyup 事件的场景。
    与 browser_type 的区别:browser_type 直接填充值,browser_keyboard_type 模拟真实键盘输入。

    Args:
        text: 要输入的文本内容。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.keyboard_type(text, page_id)


@tool
async def browser_insert_text(
    text: str, selector: Optional[str] = None, page_id: Optional[int] = None
) -> str:
    """插入文本(不触发按键事件)。适用于 input/textarea 的快速填充。

    Args:
        text: 要插入的文本内容。
        selector: 可选,CSS 选择器或 XPath 表达式。提供时先聚焦到该元素再插入,
                  不提供则向当前聚焦元素插入。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        操作结果消息。
    """
    return await _browser.insert_text(text, selector, page_id)


@tool
async def browser_get_value(
    selector: Optional[str] = None, page_id: Optional[int] = None
) -> str:
    """获取表单元素的当前值(input/textarea/select)。不提供 selector 则获取当前聚焦元素的值。

    Args:
        selector: 可选,CSS 选择器或 XPath 表达式。不提供则获取当前聚焦元素。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        元素的当前值。
    """
    return await _browser.get_value(selector, page_id)


@tool
async def browser_get_count(selector: str, page_id: Optional[int] = None) -> str:
    """统计匹配选择器的元素数量。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        匹配元素的数量。
    """
    return await _browser.get_count(selector, page_id)


@tool
async def browser_get_box(selector: str, page_id: Optional[int] = None) -> str:
    """获取元素的边界框(位置和尺寸)。返回 x, y, width, height。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        元素的边界框信息。
    """
    return await _browser.get_bounding_box(selector, page_id)


@tool
async def browser_get_styles(selector: str, page_id: Optional[int] = None) -> str:
    """获取元素的计算样式(computed styles)。返回 display、position、width、height、margin、padding、颜色等。

    Args:
        selector: CSS 选择器或 XPath 表达式。
        page_id: 可选,指定操作的页面 ID。不提供则使用当前活动页面。

    Returns:
        元素的计算样式列表。
    """
    return await _browser.get_styles(selector, page_id)


# ── 工具列表 ──────────────────────────────────────────────────

# 核心浏览器工具(常用)
CORE_BROWSE_TOOLS = [
    browser_start,
    browser_connect,
    browser_close,
    browser_navigate,
    browser_click,
    browser_type,
    browser_screenshot,
    browser_get_text,
    browser_get_elements,
    browser_evaluate,
]

# 页面管理工具
PAGE_MANAGEMENT_TOOLS = [
    browser_new_page,
    browser_close_page,
    browser_switch_page,
    browser_list_pages,
]

# 扩展浏览器工具
EXTENDED_BROWSE_TOOLS = [
    browser_get_html,
    browser_get_attribute,
    browser_wait,
    browser_scroll,
    browser_back,
    browser_forward,
    browser_reload,
    browser_get_url,
    browser_get_title,
    browser_toggle_mode,
    browser_press_key,
    browser_select_option,
    browser_check,
    browser_hover,
    browser_drag,
    browser_dblclick,
    browser_focus,
    browser_scroll_into_view,
    browser_key_down,
    browser_key_up,
    browser_keyboard_type,
    browser_insert_text,
    browser_get_value,
    browser_get_count,
    browser_get_box,
    browser_get_styles,
]


def get_tools() -> list:
    """获取 web browse 工具列表。"""
    return CORE_BROWSE_TOOLS + PAGE_MANAGEMENT_TOOLS + EXTENDED_BROWSE_TOOLS


def get_all_tools() -> list:
    """获取所有 web browse 工具。"""
    return get_tools()
