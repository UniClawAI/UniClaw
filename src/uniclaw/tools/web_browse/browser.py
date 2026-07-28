"""WebBrowser 类 — 封装 Playwright 浏览器操作。

提供浏览器生命周期管理、页面导航、元素交互、截图等完整浏览器控制能力。
支持多页面(标签页)管理。
"""

from __future__ import annotations

import asyncio
import base64
import logging
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR

# 延迟导入 playwright,避免启动时加载 greenlet DLL
_async_playwright = None


def _get_async_playwright():
    """延迟导入 async_playwright,失败时抛出 RuntimeError。"""
    global _async_playwright
    if _async_playwright is None:
        try:
            from playwright.async_api import async_playwright

            _async_playwright = async_playwright
        except ImportError as e:
            err_str = str(e).lower()
            if "greenlet" in err_str and "dll" in err_str:
                hint = (
                    "greenlet DLL 加载失败,缺少 Visual C++ 运行库。\n\n"
                    "修复方法(任选其一):\n"
                    "1. 安装 VC++ Redistributable: https://aka.ms/vs/17/release/vc_redist.x64.exe\n"
                    "2. uv pip install --force-reinstall greenlet"
                )
            elif "greenlet" in err_str:
                hint = (
                    "greenlet 模块损坏。\n\n"
                    "修复方法:\n"
                    "uv pip install --force-reinstall greenlet"
                )
            elif "playwright" in err_str:
                hint = (
                    "playwright 未安装。\n\n"
                    "修复方法:\n"
                    "uv sync && uv run playwright install chromium"
                )
            else:
                hint = (
                    f"Playwright 导入失败: {e}\n\n"
                    "修复方法:\n"
                    "1. uv pip install --force-reinstall greenlet\n"
                    "2. uv run playwright install chromium"
                )
            raise RuntimeError(hint) from e
    return _async_playwright


class WebBrowser:
    """封装 Playwright 浏览器实例,提供统一的浏览器操作接口。

    支持无头/有头模式切换,多页面管理,保持浏览器状态直到显式关闭。
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._pages: dict[int, object] = {}  # page_id -> page
        self._active_page_id: Optional[int] = None
        self._headless = True
        self._next_id = 1

    @property
    def is_running(self) -> bool:
        """浏览器是否正在运行。"""
        return self._browser is not None

    @property
    def headless(self) -> bool:
        """当前是否为无头模式。"""
        return self._headless

    @property
    def active_page(self):
        """获取当前活动页面。"""
        if self._active_page_id is None:
            return None
        return self._pages.get(self._active_page_id)

    @property
    def active_page_id(self) -> Optional[int]:
        """获取当前活动页面 ID。"""
        return self._active_page_id

    @property
    def page_ids(self) -> list[int]:
        """获取所有页面 ID 列表。"""
        return list(self._pages.keys())

    async def start(self, headless: bool = True) -> str:
        """启动浏览器。

        Args:
            headless: 是否使用无头模式。

        Returns:
            操作结果消息。
        """
        async_playwright_fn = _get_async_playwright()

        if self._browser is not None and self._headless != headless:
            await self.close()

        if self._browser is None:
            self._playwright = await async_playwright_fn().start()
            self._browser = await self._playwright.chromium.launch(headless=headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            )
            # 创建默认页面
            await self.new_page()
            self._headless = headless

        mode = "无头模式" if headless else "有头模式"
        return f"浏览器已启动({mode}),页面 ID: {self._active_page_id}"

    async def close(self) -> str:
        """关闭浏览器并释放资源。"""
        if self._browser is None:
            return f"{TOOL_ERROR}: 浏览器未启动"

        try:
            for page in self._pages.values():
                await page.close()
            self._pages.clear()
            self._active_page_id = None

            if self._context:
                await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            return "浏览器已关闭"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def new_page(self) -> tuple[int, str]:
        """创建新页面(标签页)。

        Returns:
            (page_id, 消息)
        """
        self._ensure_running()
        try:
            page = await self._context.new_page()
            page_id = self._next_id
            self._next_id += 1
            self._pages[page_id] = page
            self._active_page_id = page_id
            return page_id, f"已创建新页面,ID: {page_id}"
        except Exception as e:
            return -1, f"{TOOL_ERROR}: {e}"

    async def close_page(self, page_id: int) -> str:
        """关闭指定页面。

        Args:
            page_id: 页面 ID。

        Returns:
            操作结果消息。
        """
        self._ensure_running()
        if page_id not in self._pages:
            return f"{TOOL_ERROR}: 页面 {page_id} 不存在"

        try:
            page = self._pages[page_id]
            await page.close()
            del self._pages[page_id]

            # 如果关闭的是活动页面,切换到其他页面
            if self._active_page_id == page_id:
                if self._pages:
                    self._active_page_id = next(iter(self._pages))
                else:
                    self._active_page_id = None

            return f"已关闭页面 {page_id}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def switch_page(self, page_id: int) -> str:
        """切换到指定页面。

        Args:
            page_id: 页面 ID。

        Returns:
            操作结果消息。
        """
        self._ensure_running()
        if page_id not in self._pages:
            return f"{TOOL_ERROR}: 页面 {page_id} 不存在"

        self._active_page_id = page_id
        page = self._pages[page_id]
        title = await page.title()
        return f"已切换到页面 {page_id},标题: {title}"

    async def list_pages(self) -> str:
        """列出所有页面。

        Returns:
            页面列表信息。
        """
        self._ensure_running()
        if not self._pages:
            return "没有打开的页面"

        lines = ["打开的页面:"]
        for page_id, page in self._pages.items():
            try:
                title = await page.title()
                url = page.url
                active = " (活动)" if page_id == self._active_page_id else ""
                lines.append(f"  [{page_id}] {title} - {url}{active}")
            except Exception as e:
                logger.debug("获取页面信息失败: %s", e)
                lines.append(f"  [{page_id}] (无法获取信息)")

        return "\n".join(lines)

    async def navigate(self, url: str, page_id: Optional[int] = None) -> str:
        """导航到指定 URL。"""
        page = self._get_page(page_id)
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=30000
            )
            title = await page.title()
            status = response.status if response else "未知"
            pid = page_id or self._active_page_id
            return f"已导航到: {url}\n页面标题: {title}\n状态码: {status}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def click(
        self, selector: str, timeout: int = 5000, page_id: Optional[int] = None
    ) -> str:
        """点击页面元素。"""
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector)
            await locator.click(timeout=timeout)
            return f"已点击元素: {selector}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def type_text(
        self,
        selector: str,
        text: str,
        clear: bool = True,
        timeout: int = 5000,
        page_id: Optional[int] = None,
    ) -> str:
        """在指定元素中输入文本。"""
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector)
            if clear:
                await locator.fill("", timeout=timeout)
            await locator.fill(text, timeout=timeout)
            return f"已在元素 {selector} 中输入文本"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def screenshot(
        self,
        selector: Optional[str] = None,
        full_page: bool = False,
        save_path: Optional[str] = None,
        page_id: Optional[int] = None,
    ) -> list | str:
        """截取页面或指定元素的截图。"""
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._get_locator(page, selector)
                screenshot_bytes = await locator.screenshot()
            else:
                screenshot_bytes = await page.screenshot(full_page=full_page)

            size_kb = len(screenshot_bytes) / 1024

            if save_path:
                from PIL import Image
                from io import BytesIO

                save_file = Path(save_path)
                save_file.parent.mkdir(parents=True, exist_ok=True)
                ext = save_file.suffix.lower()
                if ext in (".jpg", ".jpeg", ".webp", ".bmp"):
                    img = Image.open(BytesIO(screenshot_bytes))
                    img.save(str(save_file))
                else:
                    save_file.write_bytes(screenshot_bytes)
                return f"截图已保存: {save_file.resolve()} ({size_kb:.0f} KB)"

            img_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            return [
                {
                    "type": "text",
                    "text": f"{SYSTEM_PREFIX}[浏览器截图: {size_kb:.0f} KB]",
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_base64}"},
                },
            ]
        except Exception as e:
            return [{"type": "text", "text": f"{TOOL_ERROR}: {e}"}]

    async def get_text(
        self, selector: Optional[str] = None, page_id: Optional[int] = None
    ) -> str:
        """获取页面或指定元素的文本内容。"""
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._get_locator(page, selector)
                text = await locator.inner_text()
            else:
                text = await page.inner_text("body")
            if len(text) > 5000:
                return text[:5000] + f"\n\n...已省略 {len(text) - 5000} 个字符"
            return text
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_html(
        self, selector: Optional[str] = None, page_id: Optional[int] = None
    ) -> str:
        """获取页面或指定元素的 HTML 内容。"""
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._get_locator(page, selector)
                html = await locator.inner_html()
            else:
                html = await page.content()
            if len(html) > 5000:
                return html[:5000] + f"\n\n...已省略 {len(html) - 5000} 个字符"
            return html
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_attribute(
        self, selector: str, attribute: str, page_id: Optional[int] = None
    ) -> str:
        """获取指定元素的属性值。"""
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector)
            value = await locator.get_attribute(attribute)
            return value if value else f"元素 {selector} 没有属性 {attribute}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_interactive_elements(
        self,
        page_id: Optional[int] = None,
        include_cursor_interactive: bool = False,
        compact: bool = False,
        depth: Optional[int] = None,
        scope: Optional[str] = None,
    ) -> str:
        """获取页面上所有可交互元素的信息。

        返回按钮、输入框、链接、下拉框等可交互元素的标签、文本、属性和选择器。
        用于 AI 精确定位元素,避免依赖截图识别。

        Args:
            page_id: 页面 ID。
            include_cursor_interactive: 是否包含 cursor:pointer 的元素(如 div[onclick])。
            compact: 精简输出,仅保留有文本/id/role 的元素。
            depth: 限制 DOM 遍历深度。
            scope: 限定 CSS 选择器范围(如 "#main", ".sidebar")。
        """
        page = self._get_page(page_id)
        try:
            # 构建选择器列表
            base_selectors = [
                "a[href]",
                "button",
                "input",
                "select",
                "textarea",
                '[role="button"]',
                '[role="link"]',
                '[role="tab"]',
                "[onclick]",
                "[tabindex]",
            ]
            if include_cursor_interactive:
                base_selectors.append('[style*="cursor: pointer"]')
                base_selectors.append('[style*="cursor:pointer"]')

            # 范围元素的 CSS 选择器(用于 JS 中 querySelector)
            scope_selector = scope if scope else None

            elements = await page.evaluate(
                """
                (args) => {
                    const selectors = args.selectors;
                    const scopeSelector = args.scope;
                    const maxDepth = args.depth;
                    const root = scopeSelector
                        ? document.querySelector(scopeSelector)
                        : document;
                    if (!root) return [];

                    const allElements = root.querySelectorAll(selectors.join(','));
                    const result = [];
                    let index = 0;

                    function isVisible(el) {
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden')
                            return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    }

                    function getDepth(el) {
                        let depth = 0;
                        let node = el.parentElement;
                        while (node && node !== root) {
                            depth++;
                            node = node.parentElement;
                        }
                        return depth;
                    }

                    function makeSelector(el) {
                        const tag = el.tagName.toLowerCase();
                        const id = el.id;
                        const name = el.getAttribute('name');
                        const cls = el.className;
                        if (id) return '#' + CSS.escape(id);
                        if (name) return tag + '[name="' + name + '"]';
                        if (cls && typeof cls === 'string') {
                            const classes = cls.split(/\\s+/).filter(c => c).slice(0, 2);
                            if (classes.length)
                                return tag + '.' + classes.map(c => CSS.escape(c)).join('.');
                        }
                        return tag;
                    }

                    for (const el of allElements) {
                        if (!isVisible(el)) continue;
                        if (maxDepth !== null && maxDepth !== undefined) {
                            if (getDepth(el) > maxDepth) continue;
                        }

                        const rect = el.getBoundingClientRect();
                        const tag = el.tagName.toLowerCase();
                        const type = el.getAttribute('type') || '';
                        const id = el.id || '';
                        const name = el.getAttribute('name') || '';
                        const className = el.className || '';
                        const href = el.getAttribute('href') || '';
                        const placeholder = el.getAttribute('placeholder') || '';
                        const value = el.value || '';
                        const text = el.textContent?.trim().substring(0, 100) || '';
                        const role = el.getAttribute('role') || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';

                        result.push({
                            index: index++,
                            tag, type, id, name,
                            text: text.substring(0, 50),
                            placeholder,
                            href: href.substring(0, 100),
                            role, ariaLabel,
                            selector: makeSelector(el),
                            rect: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height)
                            }
                        });
                    }
                    return result;
                }
            """,
                {
                    "selectors": base_selectors,
                    "scope": scope_selector,
                    "depth": depth,
                },
            )

            if not elements:
                return "页面上没有找到可交互元素"

            # compact 模式: 仅保留有文本/id/role 的元素
            if compact:
                elements = [
                    el
                    for el in elements
                    if el.get("text") or el.get("id") or el.get("role")
                ]
                if not elements:
                    return "compact 模式下没有找到有文本内容的可交互元素"

            # 格式化输出
            lines = [f"找到 {len(elements)} 个可交互元素:\n"]
            for el in elements:
                parts = [f"[{el['index']}] <{el['tag']}>"]
                if el["type"]:
                    parts.append(f"type={el['type']}")
                if el["id"]:
                    parts.append(f"id={el['id']}")
                if el["name"]:
                    parts.append(f"name={el['name']}")
                if el["text"]:
                    parts.append(f"text=\"{el['text']}\"")
                if el["placeholder"]:
                    parts.append(f"placeholder=\"{el['placeholder']}\"")
                if el["href"]:
                    parts.append(f"href=\"{el['href']}\"")
                if el["role"]:
                    parts.append(f"role={el['role']}")
                if el["ariaLabel"]:
                    parts.append(f"aria=\"{el['ariaLabel']}\"")
                parts.append(f"selector={el['selector']}")
                parts.append(
                    f"pos=({el['rect']['x']},{el['rect']['y']}) {el['rect']['width']}x{el['rect']['height']}"
                )
                lines.append(" | ".join(parts))

            result = "\n".join(lines)
            if len(result) > 50000:
                return result[:50000] + f"\n\n...已省略 {len(result) - 50000} 个字符"
            return result
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def wait_for(
        self,
        selector: Optional[str] = None,
        state: str = "visible",
        timeout: int = 10000,
        page_id: Optional[int] = None,
        text: Optional[str] = None,
        url: Optional[str] = None,
        load_state: Optional[str] = None,
        js_condition: Optional[str] = None,
        poll_interval: float = 0.5,
    ) -> str:
        """等待指定条件满足。

        按优先级判断等待类型: js_condition > load_state > url > text > selector。
        不提供任何条件时,默认等待当前页面加载完成。

        Args:
            selector: CSS 选择器或 XPath 表达式(以 // 或 ( 开头表示 XPath)。
            state: 元素等待状态: attached/detached/visible/hidden,默认 visible。
            timeout: 超时时间(毫秒),默认 10000。
            page_id: 页面 ID,不提供则使用当前活动页面。
            text: 等待页面出现指定文本。
            url: 等待 URL 匹配 glob 模式(如 "**/dashboard")。
            load_state: 等待页面加载状态: load/domcontentloaded/networkidle,或 "network" 等待网络空闲。
            js_condition: 等待 JavaScript 表达式返回 truthy 值。
            poll_interval: 文本/URL/JS 轮询间隔(秒),默认 0.5。
        """
        page = self._get_page(page_id)

        try:
            # 优先级 1: JS 条件
            if js_condition is not None:
                return await self._wait_for_js_condition(
                    page, js_condition, timeout, poll_interval
                )

            # 优先级 2: 页面加载状态
            if load_state is not None:
                return await self._wait_for_load_state(page, load_state, timeout)

            # 优先级 3: URL 匹配
            if url is not None:
                return await self._wait_for_url(page, url, timeout, poll_interval)

            # 优先级 4: 文本出现
            if text is not None:
                return await self._wait_for_text(page, text, timeout, poll_interval)

            # 优先级 5: 元素状态(默认)
            if selector is not None:
                locator = self._to_locator(page, selector)
                await locator.wait_for(state=state, timeout=timeout)
                return f"元素 {selector} 已达到状态: {state}"

            # 无任何条件: 等待当前页面 load 完成
            await page.wait_for_load_state("load", timeout=timeout)
            return "页面已加载完成"

        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def evaluate(
        self,
        expression: str,
        page_id: Optional[int] = None,
        is_base64: bool = False,
        arg: Optional[str] = None,
    ) -> str:
        """在页面中执行 JavaScript 表达式并返回结果。

        Args:
            expression: JavaScript 表达式或代码。支持多行代码(最后一行为返回值)。
            page_id: 页面 ID。
            is_base64: 是否为 base64 编码的 JS 代码。
            arg: 传递给 JS 表达式的参数,在 JS 中通过 arguments[0] 访问。
        """
        page = self._get_page(page_id)
        try:
            if is_base64:
                expression = base64.b64decode(expression).decode("utf-8")

            if arg is not None:
                result = await page.evaluate(expression, arg)
            else:
                result = await page.evaluate(expression)
            return str(result) if result is not None else "执行成功(无返回值)"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def scroll(
        self, direction: str = "down", amount: int = 500, page_id: Optional[int] = None
    ) -> str:
        """滚动页面。"""
        page = self._get_page(page_id)
        try:
            delta = amount if direction == "down" else -amount
            await page.mouse.wheel(0, delta)
            direction_cn = "下" if direction == "down" else "上"
            return f"已向{direction_cn}滚动 {amount} 像素"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def back(self, page_id: Optional[int] = None) -> str:
        """浏览器后退。"""
        page = self._get_page(page_id)
        try:
            await page.go_back()
            title = await page.title()
            return f"已后退,当前页面: {title}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def forward(self, page_id: Optional[int] = None) -> str:
        """浏览器前进。"""
        page = self._get_page(page_id)
        try:
            await page.go_forward()
            title = await page.title()
            return f"已前进,当前页面: {title}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def reload(self, page_id: Optional[int] = None) -> str:
        """刷新当前页面。"""
        page = self._get_page(page_id)
        try:
            await page.reload()
            title = await page.title()
            return f"已刷新页面: {title}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_url(self, page_id: Optional[int] = None) -> str:
        """获取当前页面 URL。"""
        page = self._get_page(page_id)
        return f"当前 URL: {page.url}"

    async def get_title(self, page_id: Optional[int] = None) -> str:
        """获取当前页面标题。"""
        page = self._get_page(page_id)
        try:
            title = await page.title()
            return f"页面标题: {title}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def switch_mode(self, headless: bool) -> str:
        """切换浏览器模式,保留所有页面 URL 和登录状态。

        通过 storage_state 保存和恢复 cookie、localStorage 等状态。

        Args:
            headless: True 为无头模式(隐藏窗口),False 为有头模式(显示窗口)。

        Returns:
            操作结果消息。
        """
        if self._browser is None:
            return f"{TOOL_ERROR}: 浏览器未启动"
        if self._headless == headless:
            return f"浏览器已在{'无头' if headless else '有头'}模式"

        # 保存所有页面的 URL
        page_urls = {pid: page.url for pid, page in self._pages.items()}

        # 保存浏览器状态(cookie、localStorage 等)
        storage_state = await self._context.storage_state()

        await self.close()
        await self.start(headless=headless)

        # 恢复 cookie
        await self._context.add_cookies(storage_state.get("cookies", []))

        # 构建 origin -> localStorage 映射
        origin_storage = {}
        for origin in storage_state.get("origins", []):
            origin_url = origin.get("origin", "")
            local_storage = origin.get("localStorage", [])
            if origin_url and local_storage:
                origin_storage[origin_url] = local_storage

        # 恢复页面,并在导航后恢复对应 origin 的 localStorage
        first = True
        for url in page_urls.values():
            if first:
                page = self.active_page
                first = False
            else:
                _, _ = await self.new_page()
                page = self.active_page

            if url and url != "about:blank":
                await page.goto(url, wait_until="domcontentloaded")
                # 恢复该 origin 的 localStorage
                parsed = urlparse(url)
                origin_key = f"{parsed.scheme}://{parsed.netloc}"
                if origin_key in origin_storage:
                    for item in origin_storage[origin_key]:
                        key = item.get("key", "")
                        value = item.get("value", "")
                        if key:
                            await page.evaluate(
                                f"localStorage.setItem({key!r}, {value!r})"
                            )

        return f"已切换到{'无头' if headless else '有头'}模式"

    async def press_key(self, key: str, page_id: Optional[int] = None) -> str:
        """按下键盘按键。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.press(key)
            return f"已按下按键: {key}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def select_option(
        self, selector: str, value: str, page_id: Optional[int] = None
    ) -> str:
        """选择下拉框选项。"""
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector)
            await locator.select_option(value)
            return f"已选择选项: {value}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def check(
        self, selector: str, checked: bool = True, page_id: Optional[int] = None
    ) -> str:
        """勾选或取消勾选复选框。"""
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector)
            await locator.check() if checked else await locator.uncheck()
            action = "勾选" if checked else "取消勾选"
            return f"已{action}元素: {selector}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def hover(self, selector: str, page_id: Optional[int] = None) -> str:
        """鼠标悬停在元素上。"""
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector)
            await locator.hover()
            return f"已悬停在元素: {selector}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def drag(
        self, source: str, target: str, page_id: Optional[int] = None
    ) -> str:
        """拖拽元素到目标位置。"""
        page = self._get_page(page_id)
        try:
            source_locator = self._get_locator(page, source)
            target_locator = self._get_locator(page, target)
            await source_locator.drag_to(target_locator)
            return f"已将 {source} 拖拽到 {target}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def dblclick(
        self, selector: str, timeout: int = 5000, page_id: Optional[int] = None
    ) -> str:
        """双击页面元素。"""
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector)
            await locator.dblclick(timeout=timeout)
            return f"已双击元素: {selector}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def focus(self, selector: str, page_id: Optional[int] = None) -> str:
        """聚焦到指定元素。"""
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector)
            await locator.focus()
            return f"已聚焦到元素: {selector}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def scroll_into_view(
        self, selector: str, page_id: Optional[int] = None
    ) -> str:
        """将指定元素滚动到可见区域。"""
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector)
            await locator.scroll_into_view_if_needed()
            return f"已将元素 {selector} 滚动到可见区域"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def key_down(self, key: str, page_id: Optional[int] = None) -> str:
        """按住键盘按键不放。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.down(key)
            return f"已按住按键: {key}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def key_up(self, key: str, page_id: Optional[int] = None) -> str:
        """松开键盘按键。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.up(key)
            return f"已松开按键: {key}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def keyboard_type(self, text: str, page_id: Optional[int] = None) -> str:
        """使用真实按键事件逐字输入文本。适用于需要触发 keydown/keyup 事件的场景。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.type(text)
            return f"已通过键盘输入: {text}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def insert_text(self, text: str, page_id: Optional[int] = None) -> str:
        """插入文本(不触发按键事件)。适用于 input/textarea 的快速填充。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.insert_text(text)
            return f"已插入文本: {text}"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_value(
        self, selector: Optional[str] = None, page_id: Optional[int] = None
    ) -> str:
        """获取表单元素的当前值。"""
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._to_locator(page, selector)
                value = await locator.input_value()
            else:
                value = await page.evaluate("document.activeElement?.value || ''")
            return f"元素值: {value}" if value else "元素值为空"
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_count(self, selector: str, page_id: Optional[int] = None) -> str:
        """统计匹配选择器的元素数量。"""
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector)
            count = await locator.count()
            return f'匹配 "{selector}" 的元素数量: {count}'
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_bounding_box(
        self, selector: str, page_id: Optional[int] = None
    ) -> str:
        """获取元素的边界框(位置和尺寸)。"""
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector)
            box = await locator.bounding_box()
            if box is None:
                return f"{TOOL_ERROR}: 元素 {selector} 不可见或不存在"
            return (
                f"边界框: x={box['x']:.0f}, y={box['y']:.0f}, "
                f"width={box['width']:.0f}, height={box['height']:.0f}"
            )
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    async def get_styles(self, selector: str, page_id: Optional[int] = None) -> str:
        """获取元素的计算样式。"""
        page = self._get_page(page_id)
        try:
            styles = await page.evaluate(
                """
                (selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    const cs = window.getComputedStyle(el);
                    const props = [
                        'display','position','width','height',
                        'margin','padding','border',
                        'backgroundColor','color','fontSize','fontWeight',
                        'opacity','overflow','zIndex'
                    ];
                    const result = {};
                    for (const p of props) {
                        result[p] = cs.getPropertyValue(p);
                    }
                    return result;
                }
            """,
                selector,
            )
            if styles is None:
                return f"{TOOL_ERROR}: 元素 {selector} 不存在"
            lines = [f"元素 {selector} 的计算样式:"]
            for prop, value in styles.items():
                lines.append(f"  {prop}: {value}")
            return "\n".join(lines)
        except Exception as e:
            return f"{TOOL_ERROR}: {e}"

    def _get_page(self, page_id: Optional[int] = None):
        """获取指定页面,未指定则返回活动页面。"""
        self._ensure_running()
        if page_id is None:
            page = self.active_page
            if page is None:
                raise RuntimeError("没有活动页面")
            return page
        if page_id not in self._pages:
            raise RuntimeError(f"页面 {page_id} 不存在")
        return self._pages[page_id]

    def _ensure_running(self):
        """确保浏览器已启动,否则抛出异常。"""
        if self._browser is None:
            raise RuntimeError("浏览器未启动,请先调用 browser_start")

    def _get_locator(self, page, selector: str):
        """根据选择器类型获取 locator。"""
        if selector.startswith("//"):
            return page.locator(f"xpath={selector}")
        return page.locator(selector)

    def _to_locator(self, page, selector: str):
        """将选择器转换为 Playwright Locator,支持 CSS 和 XPath(// 或 ( 开头)。"""
        if selector.startswith("//") or selector.startswith("("):
            return page.locator(f"xpath={selector}")
        return page.locator(selector)

    async def _wait_for_text(
        self, page, text: str, timeout: int, poll_interval: float
    ) -> str:
        """轮询等待页面 body 中出现指定文本。"""
        timeout_sec = timeout / 1000
        elapsed = 0.0
        while elapsed < timeout_sec:
            try:
                body_text = await page.inner_text("body")
                if text in body_text:
                    return f'页面已出现文本: "{text}"'
            except Exception as e:
                logger.debug("轮询等待文本时获取 body 失败: %s", e)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise TimeoutError(f'等待文本 "{text}" 超时 ({timeout}ms)')

    async def _wait_for_url(
        self, page, pattern: str, timeout: int, poll_interval: float
    ) -> str:
        """轮询等待当前 URL 匹配 glob 模式。"""
        timeout_sec = timeout / 1000
        elapsed = 0.0
        while elapsed < timeout_sec:
            if fnmatch(page.url, pattern):
                return f"URL 已匹配: {page.url}"
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise TimeoutError(
            f'等待 URL 匹配 "{pattern}" 超时 ({timeout}ms), 当前: {page.url}'
        )

    async def _wait_for_load_state(self, page, load_state: str, timeout: int) -> str:
        """等待页面加载状态。支持 load/domcontentloaded/networkidle/network。"""
        # "network" 映射到 Playwright 的 networkidle
        resolved = "networkidle" if load_state == "network" else load_state
        await page.wait_for_load_state(resolved, timeout=timeout)
        state_names = {
            "load": "页面加载完成",
            "domcontentloaded": "DOM 内容已加载",
            "networkidle": "网络已空闲",
        }
        return state_names.get(resolved, f"页面状态: {resolved}")

    async def _wait_for_js_condition(
        self, page, expression: str, timeout: int, poll_interval: float
    ) -> str:
        """轮询等待 JS 表达式返回 truthy 值。"""
        timeout_sec = timeout / 1000
        elapsed = 0.0
        while elapsed < timeout_sec:
            try:
                result = await page.evaluate(expression)
                if result:
                    return f"JS 条件已满足: {expression} = {result}"
            except Exception as e:
                logger.debug("轮询 JS 条件执行失败: %s", e)
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise TimeoutError(f'等待 JS 条件 "{expression}" 超时 ({timeout}ms)')
