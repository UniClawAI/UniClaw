"""WebBrowser 类 — 封装 Playwright 浏览器操作。

提供浏览器生命周期管理、页面导航、元素交互、截图等完整浏览器控制能力。
支持多页面(标签页)管理。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import logging
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from uniclaw.tools.base import ToolUserError
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.utils.format import format_size

# Playwright 异常类。
# 不从 playwright.async_api 导入 — 那会连带加载 greenlet DLL(见 _get_async_playwright 注释)。
# playwright._impl._errors 是纯异常定义,与 async_api 导出的是同一组类。
# PlaywrightTimeoutError 是 PlaywrightError 的子类,except PlaywrightError 即覆盖超时。
try:
    from playwright._impl._errors import Error as PlaywrightError
    from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - playwright 未安装时不会进行任何浏览器调用

    class PlaywrightError(Exception):
        """占位异常(playwright 未安装时使用)。"""

    class PlaywrightTimeoutError(PlaywrightError):
        """占位超时异常(playwright 未安装时使用)。"""


# 元素查找统一超时(毫秒)。
# 不统一的话"元素不存在"有的等 5 秒(click/type)、有的等 30 秒(Playwright 默认),
# 调用方无法预期一次失败要多久,报错风格也跟着分裂。
_SELECTOR_TIMEOUT_MS = 5000

# 页面导航超时(毫秒): 要等 DOMContentLoaded, 比元素查找宽裕
_NAVIGATE_TIMEOUT_MS = 30000

# 元素操作超时的通用建议(与分类提示拼在同一条里, 不单独成段)
_TIMEOUT_WAIT_ADVICE = (
    "无头模式下动态渲染的元素可能需要 browser_wait 等待出现后再操作"
)

# Playwright 异常文本 → 真实原因提示(按顺序匹配, 命中即止)。
# 这些原因靠 browser_wait 解决不了(disabled 不会自己启用、选项值不会自己
# 冒出来), 所以命中时**不**再附等待建议 —— 一律建议等待正是此前的误导点。
# 匹配用小写子串, 对应 Playwright call log 的真实措辞:
#   "element is not enabled" / "option being selected is not enabled"
#   "did not find some options" / "Element is not a <select> element"
#   "element is not visible" / "Element is not attached to the DOM"
_TIMEOUT_REASON_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("not enabled", "disabled"),
        "元素处于 disabled 状态,等待无效;需先启用该元素",
    ),
    (
        ("did not find some options", "no such option"),
        "选项值不匹配,请先用 get_text/get_html 核对可选值",
    ),
    (
        ("not a <select>", "not a select"),
        "目标不是下拉框",
    ),
    (
        ("not visible", "hidden"),
        "元素不可见(可能被遮挡或隐藏)",
    ),
    (
        ("not attached", "detached"),
        "元素已从 DOM 移除",
    ),
)


def _timeout_reason_hint(error_text: str) -> Optional[str]:
    """按 Playwright 异常文本匹配真实原因;无匹配返回 None。

    匹配前先剔除引号内的选择器/属性值 —— 否则 `#disabled-btn`、`class="hidden"`
    这类名字会把「元素不存在」误判成 disabled/hidden。
    """
    text = (error_text or "").lower()
    if not text:
        return None
    text = re.sub(r'"[^"]*"|\'[^\']*\'', " ", text)
    for needles, hint in _TIMEOUT_REASON_RULES:
        if any(n in text for n in needles):
            return hint
    return None


async def _select_options_hint(locator) -> str:
    """列出下拉框的全部可选值,供选项不匹配时自纠。best-effort,绝不抛。"""
    try:
        texts = await locator.locator("option").all_text_contents()
    except Exception:
        return ""
    if not isinstance(texts, list):
        return ""
    cleaned = [t.strip() for t in texts if isinstance(t, str) and t.strip()]
    if not cleaned:
        return "可选值: (该下拉框没有 option 子项)"
    shown = cleaned[:50]
    listing = ", ".join(repr(t) for t in shown)
    if len(cleaned) > len(shown):
        listing += f", ...(共 {len(cleaned)} 项)"
    return f"可选值({len(cleaned)} 项): {listing}"


async def _iframe_hint(page) -> str:
    """元素找不到时顺带探测页面 iframe, 说清「目标可能在 frame 里」。

    本工具的 CSS/XPath 定位只查主文档、不跨 frame —— 目标在 iframe 里时
    报「元素不存在」极易误导。探测 best-effort, 绝不抛(同 _timeout_hint)。
    """
    if page is None:
        return ""
    try:
        # page.frames 含主文档自身, 减 1 即子 frame 数(含嵌套)
        n = len(page.frames) - 1
    except Exception:
        return ""
    if n <= 0:
        return ""
    return (
        f"页面含 {n} 个 iframe,目标元素可能在其中;"
        f"当前工具不自动跨 frame 查找,请用 frame_selector=\"<iframe 选择器>\" "
        f"在 frame 内定位操作,或用 browser_evaluate 切换/在 frame 内操作。"
    )


def _format_iframe_note(iframes: list) -> str:
    """把 _list_iframes 的结果渲染成边界说明(selector/src)。"""
    lines = [
        f"页面含 {len(iframes)} 个 iframe(元素列表只覆盖主文档,"
        f"frame 内元素不在其中;操作 frame 内元素请用 frame_selector 或 browser_evaluate):"
    ]
    for item in iframes:
        sel = item.get("selector") or "iframe"
        src = item.get("src") or "(无 src / about:blank)"
        lines.append(f"  - {sel}  src={src}")
    return "\n".join(lines)


async def _timeout_hint(locator, error_text: str = "") -> str:
    """元素操作超时后的分类提示: 先看异常文本里的真实原因, 再做三态探测。

    两级判定:
    1. Playwright 异常文本(含 call log)命中已知原因(disabled / 选项值不匹配 /
       不是 select / 不可见 / 已脱离 DOM)→ 直接给该原因的自纠建议, **不再**
       建议 browser_wait —— 等待对这些原因无效。
    2. 无命中 → 探测元素是否在 DOM 中, 区分「元素不存在」与「元素存在但未就绪
       (被遮挡/未渲染完)」—— 两者排查方向相反: 前者是选择器写错/页面不对(还要
       顺带看 iframe), 后者要等渲染或滚出遮挡。

    Playwright 原始错误(含 call log)由调用方原样保留, 本函数只产出补充提示。
    探测本身绝不抛: 元素已消失、页面正跳转时 count() 也可能抛,
    这里连非预期异常一并兜住(与工具主逻辑的异常约定相反 ——
    这是 best-effort 增强, 不能让它掩盖原始超时错误)。
    """
    reason = _timeout_reason_hint(error_text)
    if reason is not None:
        return reason

    exists: Optional[bool]
    try:
        exists = await locator.count() > 0
    except Exception:
        exists = None

    if exists is True:
        return f"元素存在但未就绪(可能被遮挡/未渲染完)。{_TIMEOUT_WAIT_ADVICE}"
    if exists is False:
        page = getattr(locator, "page", None)
        iframe_note = await _iframe_hint(page)
        if iframe_note:
            return f"元素不存在(选择器未匹配到任何节点)。{iframe_note}"
        return f"元素不存在(选择器未匹配到任何节点)。{_TIMEOUT_WAIT_ADVICE}"
    return f"无法确认元素是否存在(探测失败)。{_TIMEOUT_WAIT_ADVICE}"

# 导航类操作的并发提示(ERR_ABORTED 等引擎错误的可自纠建议)
_NAV_CONCURRENCY_HINT = (
    "可能是并发导航导致,请串行调用 browser_back/forward/navigate"
)


def _nav_err(action: str, e) -> str:
    """导航类操作报错: 原始引擎错误 + 并发导航提示(ERR_ABORTED 时)。

    Playwright 的 net::ERR_ABORTED / frame was detached 多半是多个导航
    互相打断,只丢引擎原文调用方无法自纠,要说明根因与规避方式。
    """
    msg = f"{TOOL_ERROR}: {action}: {e}"
    err = str(e)
    if "ERR_ABORTED" in err or "frame was detached" in err:
        msg += f"\n提示: {_NAV_CONCURRENCY_HINT}"
    return msg


def _download_nav_message(url: str, filename: str = "") -> str:
    """URL 触发文件下载而非页面导航时的统一提示。

    浏览器把 Content-Disposition: attachment 的响应当下载而不是导航,
    page.goto 会以 "Download is starting" 失败或超时 —— 这不是导航失败,
    要明确告诉调用方改走 http_download,否则会在超时报错里打转。
    """
    from uniclaw.tools.download.http_tools import http_download  # 循环导入例外: 取工具规范名

    name_part = f"(文件名: {filename})" if filename else ""
    return (
        f"{TOOL_ERROR}: {url} 触发的是文件下载{name_part},不是页面导航,浏览器导航不会成功。\n"
        f"请改用 {http_download.name}(url='{url}') 下载该文件"
        "(需要 Referer/User-Agent 等防盗链头时,给 http_download 传 headers 参数)。"
    )


def _is_download_nav_error(e) -> bool:
    """判断导航异常是否是"触发了下载"而非真正的导航失败。"""
    return "download is starting" in str(e).lower()


def _response_header(response, name: str) -> str:
    """安全读取响应头字符串(非字符串值一律当空,避免 mock/异常响应炸后续解析)。"""
    if response is None:
        return ""
    value = response.headers.get(name)
    return value if isinstance(value, str) else ""


def _serialise_interaction(method):
    """把交互动作(type/click/select 等)串行到 WebBrowser._act_lock。

    这些操作都是"清空/定位→写入/点击"多步构成,步间有 await 让出点;
    并发调用会互相踩焦点与取值(实测: 两个 browser_type 的文本全落进
    后一个元素)。串行化后并发调用排队执行,结果与单次调用一致。
    """

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        async with self._act_lock:
            return await method(self, *args, **kwargs)

    return wrapper


def _reject_empty_selector(
    value: Optional[str],
    name: str = "selector",
    *,
    omit_hint: Optional[str] = None,
) -> Optional[str]:
    """空串选择器是参数错误,不是"查整页/当前焦点"。合法时返回 None。

    None/未传不在此拦 —— 各工具有各自的省略语义(get_text 取整页、screenshot
    截整页、insert_text 写当前焦点等)。空串必须拒绝: 调用方以为查的是某个
    元素,静默退化成整页/焦点会拿错数据还以为拿到了。

    Args:
        value: 收到的选择器(None 表示未传)。
        name: 参数名(selector/frame_selector/source/target),报错用。
        omit_hint: 该参数可省略时的省略说明;必填参数不传,给通用提示。
    """
    if value != "":
        return None
    hint = omit_hint or "请传 CSS 选择器或 XPath 表达式"
    return f"{TOOL_ERROR}: {name} 不能为空串;{hint}"


def _reject_bad_timeout(timeout) -> Optional[str]:
    """timeout 必须是正整数(毫秒)。合法时返回 None。

    负数/0 透传给 Playwright 会报 "Timeout -5ms exceeded",失败提示还会被
    _timeout_hint 分类成"元素存在但未就绪"等,与真实原因(参数非法)完全不符。
    """
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        return f"{TOOL_ERROR}: timeout 必须为正整数(毫秒),收到: {timeout}"
    return None


# 延迟导入 playwright,避免启动时加载 greenlet DLL
_async_playwright = None


def _is_initial_page(url: str) -> bool:
    """判断是否为历史起点空白页(about:blank / 空 URL)。

    browser_start/new_page 会先建 about:blank 再 navigate,所以退回
    about:blank 基本就是历史起点 —— 用于 back/forward 边界提示。
    """
    u = (url or "").strip().lower()
    return u == "" or u == "about:blank" or u.startswith("about:blank#")


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
        # 历史位置跟踪: 浏览器没有"当前历史下标"API, back/forward 的边界提示
        # (是否已到起点/末尾)只能自己记。_last_urls 那种"最近一次 navigate 的
        # URL"启发式在 new_page 建的页、重定向落地上都会判错。
        self._hist_urls: dict[int, list[str]] = {}  # page_id -> 访问过的 URL(时间序)
        self._hist_idx: dict[int, int] = {}  # page_id -> 当前下标
        self._headless = True
        self._next_id = 1
        self._connected_cdp = False  # 是否通过 CDP 连接外部浏览器
        self._cdp_url: Optional[str] = None
        # 导航状态锁: 会改动导航/历史游标的操作(back/forward/navigate/reload/
        # switch_page)必须串行 —— 并发 go_back 会相互打断(Playwright 报
        # ERR_ABORTED), 且 "导航 + 读 URL + 更新 _hist_*" 交错后游标与真实位置
        # 脱节。整实例一把即可: 导航操作频率低, 而 switch_page 改的
        # _active_page_id 影响所有操作的 pid 解析, 每页锁盖不住这块共享状态。
        self._nav_lock = asyncio.Lock()
        # 交互动作锁: type/click 等改页面状态的操作必须串行 ——
        # 并发 type_text 的 "清空→写入" 两步之间存在 await 交错窗口,
        # 两个调用会互相踩焦点/取值,文本落进错误的元素(实测必现)
        self._act_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """浏览器是否正在运行。"""
        return self._browser is not None

    @property
    def connected_via_cdp(self) -> bool:
        """是否通过 CDP 连接外部浏览器。"""
        return self._connected_cdp

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
        from uniclaw.tools.web_browse.tools import browser_close, browser_start  # 循环导入例外: 取工具规范名

        async_playwright_fn = _get_async_playwright()

        # 会话互斥: 已有任何会话(内部启动或 CDP)时拒绝 —— start 会替换/
        # 复用旧会话并把导航目标打进去,自动化脚本的页面会被原地顶掉。
        # 一律提示先 browser_close,显式重建
        if self._browser is not None:
            kind = "CDP 连接的外部浏览器" if self._connected_cdp else "内部启动的浏览器"
            return (
                f"{TOOL_ERROR}: 当前已有 {kind} 会话(页面 ID {self._active_page_id}),"
                f"{browser_start.name} 会替换该会话并重新导航。"
                f"请先 {browser_close.name} 再重新启动"
            )

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
            self._connected_cdp = False
            self._cdp_url = None

        mode = "无头模式" if headless else "有头模式"
        return f"浏览器已启动({mode}),页面 ID: {self._active_page_id}"

    async def connect_over_cdp(self, cdp_url: str = "http://localhost:9222") -> str:
        """通过 CDP (Chrome DevTools Protocol) 连接到已运行的外部浏览器。

        适用于用户手动启动的浏览器,如:
            chrome.exe --remote-debugging-port=9222

        连接后复用该浏览器已有的登录状态和标签页,后续页面操作与本地启动的浏览器完全一致。

        Args:
            cdp_url: 浏览器的 CDP 调试地址,如 "http://localhost:9222"。

        Returns:
            操作结果消息。
        """
        from uniclaw.tools.web_browse.tools import browser_close  # 循环导入例外: 取工具规范名

        async_playwright_fn = _get_async_playwright()

        # 会话互斥: 已有会话(内部启动或另一 CDP 连接)时拒绝覆盖,
        # 防止旧会话被静默销毁后操作对象不明
        if self._browser is not None:
            kind = "CDP 连接的外部浏览器" if self._connected_cdp else "内部启动的浏览器"
            return (
                f"{TOOL_ERROR}: 当前已有 {kind} 会话(页面 ID {self._active_page_id}),"
                f"connect_over_cdp 会替换掉该会话。请先 {browser_close.name} 再连接"
            )

        # 先在临时变量里连,成功了才替换现有会话 —— 否则连不上时原有的
        # 浏览器会话已被 close() 销毁,后续 get_url/close 全报"浏览器未启动"
        pw = None
        try:
            pw = await async_playwright_fn().start()
            new_browser = await pw.chromium.connect_over_cdp(cdp_url)
        except (PlaywrightError, PlaywrightTimeoutError, OSError) as e:
            if pw is not None:
                try:
                    await pw.stop()
                except Exception:
                    pass
            return (
                f"{TOOL_ERROR}: 无法连接到浏览器 {cdp_url} ({e})\n"
                f"现有浏览器会话未受影响"
                f"({'仍在运行,页面 ID ' + str(self._active_page_id) if self._browser else '当前无会话'})\n"
                "请确认浏览器已带远程调试参数启动(部分版本需同时指定 --user-data-dir 指向独立的用户数据目录),如:\n"
                '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                "--remote-debugging-port=9222 --user-data-dir=\"C:\\chrome-debug-profile\""
            )

        # 连接成功, 此时才替换旧会话
        if self._browser is not None:
            await self.close()
        self._playwright = pw
        self._browser = new_browser

        self._context = self._browser.contexts[0] if self._browser.contexts else None
        if self._context is None:
            self._context = await self._browser.new_context()

        # 导入该浏览器已有的标签页
        self._pages.clear()
        self._active_page_id = None
        for page in self._context.pages:
            page_id = self._next_id
            self._next_id += 1
            self._pages[page_id] = page
            if self._active_page_id is None:
                self._active_page_id = page_id
        if self._active_page_id is None:
            await self.new_page()

        self._connected_cdp = True
        self._cdp_url = cdp_url
        self._headless = False

        return (
            f"已通过 CDP 连接到浏览器 {cdp_url},"
            f"导入 {len(self._pages)} 个页面,当前活动页面 ID: {self._active_page_id}"
        )

    async def close(self) -> str:
        """关闭浏览器并释放资源。"""
        if self._browser is None:
            return f"{TOOL_ERROR}: 浏览器未启动"

        # 清理序列每步独立 try,单步失败不阻断后续释放,避免资源泄漏
        errors: list[str] = []

        # CDP 连接的外部浏览器只断开连接,不关闭用户浏览器进程
        if self._connected_cdp:
            cdp_url = self._cdp_url
            self._pages.clear()
            self._active_page_id = None
            self._hist_urls.clear()
            self._hist_idx.clear()
            self._connected_cdp = False
            self._cdp_url = None
            self._context = None
            if self._browser:
                try:
                    await self._browser.close()  # disconnect: 仅断开 CDP 连接
                except (PlaywrightError, PlaywrightTimeoutError) as e:
                    errors.append(f"断开 CDP 连接失败 ({cdp_url}): {e}")
                finally:
                    self._browser = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except (PlaywrightError, PlaywrightTimeoutError) as e:
                    errors.append(f"停止 Playwright 失败: {e}")
                finally:
                    self._playwright = None
            if errors:
                return f"{TOOL_ERROR}: " + "; ".join(errors)
            return "已断开与外部浏览器的 CDP 连接(浏览器保持运行;可再次 browser_connect 重连)"

        for page_id, page in list(self._pages.items()):
            try:
                await page.close()
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                errors.append(f"关闭页面 {page_id} 失败: {e}")
        self._pages.clear()
        self._active_page_id = None
        self._hist_urls.clear()
        self._hist_idx.clear()

        if self._context:
            try:
                await self._context.close()
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                errors.append(f"关闭浏览器上下文失败: {e}")
            finally:
                self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                errors.append(f"关闭浏览器失败: {e}")
            finally:
                self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                errors.append(f"停止 Playwright 失败: {e}")
            finally:
                self._playwright = None

        if errors:
            return f"{TOOL_ERROR}: 浏览器清理完成但有错误: " + "; ".join(errors)
        return "浏览器已关闭"

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
            self._hist_init(page_id, page.url or "about:blank")
            return page_id, f"已创建新页面,ID: {page_id}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return -1, f"{TOOL_ERROR}: 创建新页面失败: {e}"

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
            self._hist_drop(page_id)

            # 如果关闭的是活动页面,切换到其他页面
            if self._active_page_id == page_id:
                if self._pages:
                    self._active_page_id = next(iter(self._pages))
                else:
                    self._active_page_id = None

            return f"已关闭页面 {page_id}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 关闭页面 {page_id} 失败: {e}"

    async def switch_page(self, page_id: int) -> str:
        """切换到指定页面。

        Args:
            page_id: 页面 ID。

        Returns:
            操作结果消息。
        """
        async with self._nav_lock:
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
            except PlaywrightError as e:
                logger.debug("获取页面信息失败 (page_id=%s): %s", page_id, e)
                lines.append(f"  [{page_id}] (无法获取信息)")

        return "\n".join(lines)

    async def navigate(self, url: str, page_id: Optional[int] = None) -> str:
        """导航到指定 URL。"""
        async with self._nav_lock:
            page = self._get_page(page_id)
            pid = page_id if page_id is not None else self._active_page_id
            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=_NAVIGATE_TIMEOUT_MS
                )
                # 附件下载响应: 有些站点 Content-Disposition: attachment 但
                # Playwright 仍返回 response,此时不能报"导航成功"
                disposition = _response_header(response, "content-disposition").lower()
                if disposition.startswith("attachment"):
                    filename = ""
                    m = re.search(r'filename\*?="?([^";]+)"?', disposition)
                    if m:
                        filename = m.group(1)
                    return _download_nav_message(url, filename)
                title = await page.title()
                status = response.status if response else "未知"
                # 记落地 URL 而非请求 URL —— 重定向/规范化后两者不一致
                self._hist_push(pid, page.url or url)
                return f"已导航到: {url}\n页面标题: {title}\n状态码: {status}"
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                # 触发文件下载时 Playwright 以 "Download is starting" 中止导航
                # (还可能被包成超时) —— 这不是导航失败,提示改走 http_download
                if _is_download_nav_error(e):
                    return _download_nav_message(url)
                return _nav_err(f"导航到 {url} 失败 (timeout=30000ms)", e)

    @_serialise_interaction
    async def click(
        self,
        selector: str,
        timeout: int = _SELECTOR_TIMEOUT_MS,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """点击页面元素。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        if bad := _reject_bad_timeout(timeout):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector, frame_selector)
            await locator.click(timeout=timeout)
            return f"已点击元素: {selector}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "点击元素", selector, e, locator=locator, timeout=timeout
            )

    @_serialise_interaction
    async def type_text(
        self,
        selector: str,
        text: str,
        clear: bool = True,
        timeout: int = _SELECTOR_TIMEOUT_MS,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """在指定元素中输入文本。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        if bad := _reject_bad_timeout(timeout):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector, frame_selector)
            if clear:
                await locator.fill("", timeout=timeout)
            await locator.fill(text, timeout=timeout)
            return f"已在元素 {selector} 中输入文本"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "在元素中输入文本",
                selector,
                e,
                locator=locator,
                timeout=timeout,
                clear=clear,
            )

    async def screenshot(
        self,
        selector: Optional[str] = None,
        full_page: bool = False,
        save_path: Optional[str] = None,
        page_id: Optional[int] = None,
    ) -> list | str:
        """截取页面或指定元素的截图。"""
        if bad := _reject_empty_selector(
            selector, omit_hint="要截取整页请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._get_locator(page, selector)
                screenshot_bytes = await locator.screenshot()
            else:
                screenshot_bytes = await page.screenshot(full_page=full_page)
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            target = f"元素 {selector}" if selector else "页面"
            return [
                {
                    "type": "text",
                    "text": f"{TOOL_ERROR}: 截取{target}截图失败 (full_page={full_page}): {e}",
                }
            ]

        size_str = format_size(len(screenshot_bytes))

        if save_path:
            save_file = Path(save_path)
            # 不静默覆盖已有文件 —— 曾把 test.html 覆盖成 PNG,数据直接丢失
            if save_file.exists():
                return [
                    {
                        "type": "text",
                        "text": (
                            f"{TOOL_ERROR}: 目标文件已存在,拒绝覆盖: {save_file}\n"
                            f"请换一个 save_path,或先删除该文件后重试"
                        ),
                    }
                ]
            try:
                from io import BytesIO

                from PIL import Image

                save_file.parent.mkdir(parents=True, exist_ok=True)
                ext = save_file.suffix.lower()
                if ext in (".jpg", ".jpeg", ".webp", ".bmp"):
                    img = Image.open(BytesIO(screenshot_bytes))
                    img.save(str(save_file))
                else:
                    save_file.write_bytes(screenshot_bytes)
            except ImportError as e:
                return [
                    {
                        "type": "text",
                        "text": f"{TOOL_ERROR}: 保存截图需要 PIL (save_path={save_path}): {e}",
                    }
                ]
            except OSError as e:
                return [
                    {
                        "type": "text",
                        "text": f"{TOOL_ERROR}: 写入截图文件失败 (save_path={save_path}): {e}",
                    }
                ]
            return f"截图已保存: {save_file.resolve()} ({size_str})"

        img_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        return [
            {
                "type": "text",
                "text": f"{SYSTEM_PREFIX}[浏览器截图: {size_str}]",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_base64}"},
            },
        ]

    async def get_text(
        self,
        selector: Optional[str] = None,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """获取页面或指定元素的文本内容。"""
        if bad := _reject_empty_selector(
            selector, omit_hint="要取整页文本请省略该参数"
        ):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._get_locator(page, selector, frame_selector)
                text = await locator.inner_text(timeout=_SELECTOR_TIMEOUT_MS)
            elif frame_selector:
                # 只给 frame_selector 不给 selector: 取该 iframe 内全文,
                # 而不是退回主文档(否则 frame 里的内容永远取不到)
                locator = self._get_locator(page, "body", frame_selector)
                text = await locator.inner_text(timeout=_SELECTOR_TIMEOUT_MS)
            else:
                text = await page.inner_text("body")
            if len(text) > 5000:
                return text[:5000] + f"\n\n...已省略 {len(text) - 5000} 个字符"
            return text
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            loc = self._get_locator(page, selector, frame_selector) if selector else None
            return await self._sel_err(
                "获取文本", selector, e, locator=loc, timeout=_SELECTOR_TIMEOUT_MS
            )

    async def get_html(
        self,
        selector: Optional[str] = None,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """获取页面或指定元素的 HTML 内容。"""
        if bad := _reject_empty_selector(
            selector, omit_hint="要取整页 HTML 请省略该参数"
        ):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._get_locator(page, selector, frame_selector)
                html = await locator.inner_html(timeout=_SELECTOR_TIMEOUT_MS)
            elif frame_selector:
                # 只给 frame_selector: 取该 iframe 的 HTML,而非整个主文档
                locator = self._get_locator(page, "body", frame_selector)
                html = await locator.inner_html(timeout=_SELECTOR_TIMEOUT_MS)
            else:
                html = await page.content()
            if len(html) > 5000:
                return html[:5000] + f"\n\n...已省略 {len(html) - 5000} 个字符"
            return html
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            loc = self._get_locator(page, selector, frame_selector) if selector else None
            return await self._sel_err(
                "获取 HTML", selector, e, locator=loc, timeout=_SELECTOR_TIMEOUT_MS
            )

    async def get_attribute(
        self,
        selector: str,
        attribute: str,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """获取指定元素的属性值。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector, frame_selector)
            value = await locator.get_attribute(attribute, timeout=_SELECTOR_TIMEOUT_MS)
            return value if value else f"元素 {selector} 没有属性 {attribute}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "获取元素属性", selector, e,
                locator=locator, timeout=_SELECTOR_TIMEOUT_MS, attribute=attribute,
            )

    async def get_interactive_elements(
        self,
        page_id: Optional[int] = None,
        include_cursor_interactive: bool = False,
        compact: bool = False,
        depth: Optional[int] = None,
        scope: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
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
            limit: 最多返回的元素数(1-200),默认 50。
            offset: 跳过前 N 个元素后开始返回(分页)。
        """
        if bad := _reject_empty_selector(
            scope, "scope", omit_hint="要查整页请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
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

        # 先探测 iframe 边界(要在主 evaluate 之前 —— 共用 page.evaluate,
        # 顺序反过来会把测试里 inspect call_args 的断言指到 iframe 探测脚本上)。
        # 检测失败就安静跳过, 不影响主功能。
        iframes = await self._list_iframes(page)

        try:
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

                    function matchCount(sel) {
                        // 统计 sel 在整份文档里命中几个节点; 非法选择器返回 -1。
                        // 用 querySelectorAll 而非 Playwright locator.count():
                        // 同一次 evaluate 内完成, 省一轮往返, 且本函数只产出 CSS。
                        try {
                            return document.querySelectorAll(sel).length;
                        } catch (e) {
                            return -1;
                        }
                    }

                    function makeSelector(el) {
                        // 按可靠性顺序试候选, 只接受「唯一」的选择器。
                        // 找不到唯一 CSS 时宁可标注"匹配 N 个"+ nth 也不瞎编
                        // 脆弱的 nth-child 长路径 —— 标注清楚比给个错的选择器好。
                        const tag = el.tagName.toLowerCase();
                        const id = el.id;
                        const cls = el.className;
                        const candidates = [];
                        if (id) candidates.push('#' + CSS.escape(id));

                        // 稳定的标识属性: 有值就试, 但同样要过唯一性校验
                        for (const attr of ['data-testid', 'data-test', 'data-cy',
                                            'aria-label', 'placeholder', 'title', 'name']) {
                            const v = el.getAttribute(attr);
                            if (v) {
                                const escaped = String(v)
                                    .replace(/\\\\/g, '\\\\\\\\')
                                    .replace(/"/g, '\\\\"');
                                candidates.push(tag + '[' + attr + '="' + escaped + '"]');
                            }
                        }
                        if (cls && typeof cls === 'string') {
                            const classes = cls.split(/\\s+/).filter(c => c).slice(0, 2);
                            if (classes.length)
                                candidates.push(tag + '.' + classes.map(c => CSS.escape(c)).join('.'));
                        }
                        candidates.push(tag);

                        for (const sel of candidates) {
                            if (matchCount(sel) === 1) {
                                return { selector: sel, unique: true, matchCount: 1, nth: -1 };
                            }
                        }

                        // 无唯一选择器: 取第一个能匹配 el 的候选作推荐(最具体的优先),
                        // 并记录它在全档匹配集中的下标, 供调用方 nth() 二次定位
                        let base = tag;
                        for (const sel of candidates) {
                            try {
                                if (el.matches(sel)) {
                                    base = sel;
                                    break;
                                }
                            } catch (e) {}
                        }
                        let nth = -1;
                        const n = matchCount(base);
                        try {
                            if (n > 0) {
                                nth = Array.from(document.querySelectorAll(base)).indexOf(el);
                            }
                        } catch (e) {}
                        return { selector: base, unique: false, matchCount: n, nth: nth };
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
                        const selInfo = makeSelector(el);

                        result.push({
                            index: index++,
                            tag, type, id, name,
                            text: text.substring(0, 50),
                            placeholder,
                            href: href.substring(0, 100),
                            role, ariaLabel,
                            selector: selInfo.selector,
                            selectorUnique: selInfo.unique,
                            selectorMatchCount: selInfo.matchCount,
                            selectorNth: selInfo.nth,
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
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return (
                f"{TOOL_ERROR}: 获取可交互元素失败 (scope={scope!r}, depth={depth}, "
                f"include_cursor_interactive={include_cursor_interactive}, compact={compact}): {e}"
            )

        # 同一趟再列 iframe 边界: 元素列表只含主文档, 不说清这点调用方会以为
        # "页面上没有这个按钮"。

        if not elements:
            msg = "页面上没有找到可交互元素"
            if iframes:
                msg += "。" + _format_iframe_note(iframes)
            return msg

        # compact 模式: 仅保留有文本/id/role 的元素
        if compact:
            elements = [
                el
                for el in elements
                if el.get("text") or el.get("id") or el.get("role")
            ]
            if not elements:
                msg = "compact 模式下没有找到有文本内容的可交互元素"
                if iframes:
                    msg += "。" + _format_iframe_note(iframes)
                return msg

        # 分页: 超大页面(几千个元素)全量序列化会灌爆上下文,
        # 默认只返回前 limit 个,总数与页范围写在头部,需要更多再翻页
        total = len(elements)
        offset = max(0, offset)
        limit = 200 if limit > 200 else (1 if limit < 1 else limit)
        page_els = elements[offset : offset + limit]
        if not page_els:
            return (
                f"找到 {total} 个可交互元素,但 offset={offset} 超出范围"
                f"(有效范围 0-{total - 1})。请调小 offset 重试。"
            )

        # 格式化输出 — 不在此捕获:el['index'] 等 KeyError 属 JS 返回结构异常,是 bug,应外抛
        start_shown = offset + 1
        end_shown = offset + len(page_els)
        lines = [
            f"找到 {total} 个可交互元素"
            f"(本次显示第 {start_shown}-{end_shown} 个,"
            f"limit={limit}, offset={offset};需要更多请调大 limit 或用 offset 翻页):\n"
        ]
        for el in page_els:
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
            sel = el["selector"]
            if el["selectorUnique"]:
                parts.append(f"selector={sel}")
            else:
                n = el["selectorMatchCount"]
                nth = el["selectorNth"]
                if n > 1:
                    nth_part = f", nth={nth}" if nth >= 0 else ""
                    parts.append(
                        f"selector={sel}"
                        f"(该选择器匹配 {n} 个元素{nth_part}, 请配合 nth()/文本二次定位)"
                    )
                else:
                    parts.append(f"selector={sel}")
            parts.append(
                f"pos=({el['rect']['x']},{el['rect']['y']}) {el['rect']['width']}x{el['rect']['height']}"
            )
            lines.append(" | ".join(parts))

        result = "\n".join(lines)
        if iframes:
            result += "\n\n" + _format_iframe_note(iframes)
        if len(result) > 50000:
            return result[:50000] + f"\n\n...已省略 {len(result) - 50000} 个字符"
        return result

    async def _list_iframes(self, page) -> list:
        """列出页面主文档里的 iframe(selector/src),供标注查找边界。

        只看主文档一层 DOM(含嵌套 iframe 元素本身),不做递归进 frame。
        best-effort: 探测失败返回空列表,不影响主功能。
        """
        try:
            raw = await page.evaluate(
                """
                () => Array.from(document.querySelectorAll('iframe')).map((f, i) => {
                    const id = f.id || '';
                    const name = f.getAttribute('name') || '';
                    const src = f.getAttribute('src') || '';
                    let selector = 'iframe';
                    if (id) selector = '#' + CSS.escape(id);
                    else if (name) selector = 'iframe[name="' + name + '"]';
                    else selector = 'iframe:nth-of-type(' + (i + 1) + ')';
                    return { selector, src, id, name };
                })
                """
            )
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            logger.debug("列出 iframe 失败: %s", e)
            return []
        if not isinstance(raw, list):
            return []
        # 只认探测脚本产出的形状(必含 src 键) —— 测试 mock 的 page.evaluate
        # 会返回元素列表, 不能误当成 iframe 清单
        return [x for x in raw if isinstance(x, dict) and "src" in x]

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
        if bad := _reject_empty_selector(
            selector, omit_hint="不查元素时请省略该参数"
        ):
            return bad
        if bad := _reject_bad_timeout(timeout):
            return bad
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

        except (PlaywrightError, PlaywrightTimeoutError) as e:
            # Playwright 超时(TimeoutError 是 Error 子类)与页面/元素层面错误
            cond = (
                f"js_condition={js_condition!r}"
                if js_condition is not None
                else f"load_state={load_state!r}"
                if load_state is not None
                else f"url={url!r}"
                if url is not None
                else f"text={text!r}"
                if text is not None
                else f"selector={selector!r}"
            )
            return f"{TOOL_ERROR}: 等待条件失败 ({cond}, state={state}, timeout={timeout}ms): {e}"
        except TimeoutError as e:
            # 轮询辅助方法抛出的内置 TimeoutError(已含上下文)
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
        if is_base64:
            try:
                expression = base64.b64decode(expression).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError) as e:
                return (
                    f"{TOOL_ERROR}: expression 不是合法的 base64/UTF-8 "
                    f"(is_base64=True, 原始长度={len(expression)}): {e}"
                )

        try:
            if arg is not None:
                result = await page.evaluate(expression, arg)
            else:
                result = await page.evaluate(expression)
            return str(result) if result is not None else "执行成功(无返回值)"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: JS 执行失败 (expression={expression[:200]!r}, is_base64={is_base64}): {e}"

    async def scroll(
        self, direction: str = "down", amount: int = 500, page_id: Optional[int] = None
    ) -> str:
        """滚动页面。"""
        # 参数校验: 非法 direction 不能静默降级成"向上",负数 amount 也不能静默接受
        dir_map = {"down": (0, 1, "下"), "up": (0, -1, "上"),
                   "right": (1, 0, "右"), "left": (-1, 0, "左")}
        if direction not in dir_map:
            return (
                f"{TOOL_ERROR}: direction 非法: {direction!r}"
                f"(可选值: down/up/left/right)"
            )
        if not isinstance(amount, int) or amount < 0:
            return (
                f"{TOOL_ERROR}: amount 必须是非负整数,收到: {amount!r}"
                f"(滚动方向由 direction 决定)"
            )

        page = self._get_page(page_id)
        try:
            sx, sy, cn = dir_map[direction]
            await page.mouse.wheel(sx * amount, sy * amount)
            return f"已向{cn}滚动 {amount} 像素"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 页面滚动失败 (direction={direction}, amount={amount}): {e}"

    async def back(self, page_id: Optional[int] = None) -> str:
        """浏览器后退。"""
        async with self._nav_lock:
            page = self._get_page(page_id)
            pid = page_id if page_id is not None else self._active_page_id
            try:
                before = page.url
                hist_len = await page.evaluate("() => window.history.length")
                await page.go_back()
                # 无历史可退时 go_back 是静默 no-op —— 必须比对 URL,
                # 否则会返回"已后退,当前页面: <原标题>",误导调用方
                if page.url == before:
                    return (
                        f"{TOOL_ERROR}: 无法后退 —— 已是历史起点,当前无更早记录"
                        f"(页面 {before})"
                    )
                title = await page.title()
                self._hist_realign(
                    pid,
                    before_url=before,
                    after_url=page.url,
                    hist_len=hist_len,
                    direction=-1,
                )
                # 带上 URL: 退回 about:blank 等无标题页面时, 仅标题会让人误以为没退成功
                msg = f"已后退,当前页面: {title}(URL: {page.url})"
                if _is_initial_page(page.url):
                    msg += "。已退回起始页 about:blank(browser_start 先建空白页再导航)"
                at_start = self._hist_at_start(pid)
                if at_start is None:
                    # 无跟踪信息且对齐失败 → 启发式兜底
                    at_start = _is_initial_page(page.url)
                if at_start:
                    msg += "。已是历史起点,无法再后退"
                else:
                    msg += "。仍可继续后退"
                return msg
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                return _nav_err("浏览器后退失败", e)

    async def forward(self, page_id: Optional[int] = None) -> str:
        """浏览器前进。"""
        async with self._nav_lock:
            page = self._get_page(page_id)
            pid = page_id if page_id is not None else self._active_page_id
            try:
                before = page.url
                hist_len = await page.evaluate("() => window.history.length")
                await page.go_forward()
                # 同 back: 无历史可进时是静默 no-op
                if page.url == before:
                    return (
                        f"{TOOL_ERROR}: 无法前进 —— 已是历史末尾,当前无后续记录"
                        f"(页面 {before})"
                    )
                title = await page.title()
                self._hist_realign(
                    pid,
                    before_url=before,
                    after_url=page.url,
                    hist_len=hist_len,
                    direction=+1,
                )
                # 同 back: 带上 URL, 无标题页面(about:blank)也能看出确实导航了
                msg = f"已前进,当前页面: {title}(URL: {page.url})"
                at_end = self._hist_at_end(pid)
                if at_end is None:
                    # 无跟踪且对齐失败 → 启发式兜底:
                    # 从 about:blank 前进且 history.length<=2 → 唯一后续页即终点
                    at_end = _is_initial_page(before) and hist_len <= 2
                if at_end:
                    msg += "。已是历史终点,无法再前进"
                else:
                    msg += "。仍可继续前进"
                return msg
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                return _nav_err("浏览器前进失败", e)

    async def reload(self, page_id: Optional[int] = None) -> str:
        """刷新当前页面。"""
        async with self._nav_lock:
            page = self._get_page(page_id)
            try:
                await page.reload()
                title = await page.title()
                return f"已刷新页面: {title}"
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                return _nav_err("刷新页面失败", e)

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
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 获取页面标题失败: {e}"

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
        if self._connected_cdp:
            return (
                f"{TOOL_ERROR}: 已通过 CDP 连接外部浏览器,无法切换显示模式\n"
                "显示模式由外部浏览器进程自身决定,如需无头模式请直接以 headless 参数启动浏览器"
            )
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

    @_serialise_interaction
    async def press_key(self, key: str, page_id: Optional[int] = None) -> str:
        """按下键盘按键。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.press(key)
            return f"已按下按键: {key}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 按下按键 {key} 失败: {e}"

    @_serialise_interaction
    async def select_option(
        self,
        selector: str,
        value: str | list[str],
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """选择下拉框选项。

        value 可传单个选项值(单选)或字符串列表(仅限 HTML multiple 多选框,
        一次选中多项)。选项不存在时报错并列出全部可选值,便于调用方自纠。
        """
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        # ── 参数归一化/校验: 传错形状是调用方错误,不能放到底层被 str 化后超时 ──
        if isinstance(value, str):
            values = [value]
            multi_call = False
        elif isinstance(value, list):
            values = value
            multi_call = True
        else:
            return (
                f"{TOOL_ERROR}: value 类型错误: 应为 str 或 list[str],"
                f"收到 {type(value).__name__} ({value!r})"
            )
        if not values:
            return f"{TOOL_ERROR}: value 不能为空"
        if not all(isinstance(v, str) for v in values):
            return (
                f"{TOOL_ERROR}: value 列表元素必须都是字符串,"
                f"收到 {value!r}"
            )

        page = self._get_page(page_id)
        locator = self._get_locator(page, selector, frame_selector)
        try:
            # 多选入参必须对应 multiple select —— 单选框传列表是参数错误,
            # 不是等待问题(底层 Playwright 只会到最后一次匹配,行为静默且难查)
            if multi_call:
                is_multiple = await locator.evaluate("el => el.multiple === true")
                if not is_multiple:
                    return (
                        f"{TOOL_ERROR}: 参数错误: value 传了列表 {value!r},"
                        f"但 {selector!r} 不是 multiple 多选框,"
                        f"单选 select 请只传一个选项值字符串"
                    )
                await locator.select_option(values, timeout=_SELECTOR_TIMEOUT_MS)
                return f"已选择选项: {values}"
            await locator.select_option(values[0], timeout=_SELECTOR_TIMEOUT_MS)
            return f"已选择选项: {values[0]}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            # 选项值不匹配时把可选值一并列出,调用方一次就能改对。
            # 该失败在 Playwright 里是重试到超时(call log 含 "did not find some
            # options"),也可能以非超时 Error 直接抛("Element is not a <select>")。
            text = str(e).lower()
            option_mismatch = (
                "did not find some options" in text or "no such option" in text
            )
            hint = None
            base = _timeout_reason_hint(str(e))
            if base is None and isinstance(e, PlaywrightTimeoutError):
                base = await _timeout_hint(locator, error_text=str(e))
            if base is not None or option_mismatch:
                opts = await _select_options_hint(locator) if option_mismatch else ""
                hint = f"{base}。{opts}" if base and opts else (base or opts or None)
            return await self._sel_err(
                "选择选项", selector, e,
                locator=locator, timeout=_SELECTOR_TIMEOUT_MS, value=value,
                hint=hint,
            )

    @_serialise_interaction
    async def check(
        self,
        selector: str,
        checked: bool = True,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """勾选或取消勾选复选框。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector, frame_selector)
            if checked:
                await locator.check(timeout=_SELECTOR_TIMEOUT_MS)
            else:
                await locator.uncheck(timeout=_SELECTOR_TIMEOUT_MS)
            action = "勾选" if checked else "取消勾选"
            return f"已{action}元素: {selector}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "勾选操作", selector, e,
                locator=locator, timeout=_SELECTOR_TIMEOUT_MS, checked=checked,
            )

    @_serialise_interaction
    async def hover(
        self,
        selector: str,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """鼠标悬停在元素上。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._get_locator(page, selector, frame_selector)
            await locator.hover(timeout=_SELECTOR_TIMEOUT_MS)
            return f"已悬停在元素: {selector}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "鼠标悬停元素", selector, e,
                locator=locator, timeout=_SELECTOR_TIMEOUT_MS,
            )

    @_serialise_interaction
    async def drag(
        self, source: str, target: str, page_id: Optional[int] = None
    ) -> str:
        """拖拽元素到目标位置。"""
        if bad := _reject_empty_selector(source, "source"):
            return bad
        if bad := _reject_empty_selector(target, "target"):
            return bad
        page = self._get_page(page_id)
        try:
            source_locator = self._get_locator(page, source)
            target_locator = self._get_locator(page, target)
            # 非 draggable 元素 drag_to 也会"成功"返回,实际拖不动 —— 先确认
            draggable = await source_locator.evaluate(
                """el => el.draggable === true
                    || el.getAttribute('draggable') === 'true'
                    || el.isContentEditable
                    || !!el.closest('[draggable="true"]')"""
            )
            if not draggable:
                return (
                    f"{TOOL_ERROR}: 元素 {source} 不可拖拽"
                    f"(需 HTML5 draggable=true 或 contenteditable),未执行拖拽。"
                    f"若目标是自定义拖拽实现,请改用 browser_evaluate 触发对应事件"
                )
            await source_locator.drag_to(target_locator, timeout=_SELECTOR_TIMEOUT_MS)
            return f"已将 {source} 拖拽到 {target}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            # 超时可能出在源或目标 —— 两边都探测, 免得只报一侧误导排查
            hint = None
            if isinstance(e, PlaywrightTimeoutError):
                src_state = await _timeout_hint(source_locator, error_text=str(e))
                tgt_state = await _timeout_hint(target_locator, error_text=str(e))
                hint = f"源元素: {src_state}; 目标元素: {tgt_state}"
            return await self._sel_err(
                "拖拽", source, e,
                locator=source_locator, timeout=_SELECTOR_TIMEOUT_MS,
                target=target, hint=hint,
            )

    @_serialise_interaction
    async def dblclick(
        self,
        selector: str,
        timeout: int = _SELECTOR_TIMEOUT_MS,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """双击页面元素。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        if bad := _reject_bad_timeout(timeout):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector, frame_selector)
            await locator.dblclick(timeout=timeout)
            return f"已双击元素: {selector}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "双击元素", selector, e, locator=locator, timeout=timeout
            )

    @_serialise_interaction
    async def focus(
        self,
        selector: str,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """聚焦到指定元素。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector, frame_selector)
            # disabled 元素 focus() 是 no-op,若不校验就会报成功但焦点没转移
            if await locator.is_disabled():
                return f"{TOOL_ERROR}: 元素 {selector} 处于 disabled 状态,无法聚焦"
            await locator.focus(timeout=_SELECTOR_TIMEOUT_MS)
            # 同样确认焦点真的转移了(不可聚焦元素 focus() 也是 no-op)
            if not await locator.evaluate("el => el === document.activeElement"):
                return (
                    f"{TOOL_ERROR}: 元素 {selector} 未能获得焦点"
                    f"(可能不可聚焦,如非交互元素)"
                )
            return f"已聚焦到元素: {selector}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "聚焦元素", selector, e,
                locator=locator, timeout=_SELECTOR_TIMEOUT_MS,
            )

    async def scroll_into_view(
        self,
        selector: str,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """将指定元素滚动到可见区域。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector, frame_selector)
            await locator.scroll_into_view_if_needed(timeout=_SELECTOR_TIMEOUT_MS)
            return f"已将元素 {selector} 滚动到可见区域"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "滚动元素到可见区域", selector, e,
                locator=locator, timeout=_SELECTOR_TIMEOUT_MS,
            )

    @_serialise_interaction
    async def key_down(self, key: str, page_id: Optional[int] = None) -> str:
        """按住键盘按键不放。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.down(key)
            return f"已按住按键: {key}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 按住按键 {key} 失败: {e}"

    @_serialise_interaction
    async def key_up(self, key: str, page_id: Optional[int] = None) -> str:
        """松开键盘按键。"""
        page = self._get_page(page_id)
        try:
            await page.keyboard.up(key)
            return f"已松开按键: {key}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 松开按键 {key} 失败: {e}"

    @_serialise_interaction
    async def keyboard_type(self, text: str, page_id: Optional[int] = None) -> str:
        """使用真实按键事件逐字输入文本。适用于需要触发 keydown/keyup 事件的场景。"""
        page = self._get_page(page_id)
        try:
            # 键盘输入落在"当前焦点元素"上;若它 readonly/disabled 或不是输入控件,
            # 按键会被忽略但工具会报成功 —— 必须先确认可输入
            editable = await page.evaluate(
                """() => {
                    const el = document.activeElement;
                    if (!el) return false;
                    if (el.disabled || el.readOnly) return false;
                    return el.isContentEditable || /^(INPUT|TEXTAREA)$/.test(el.tagName);
                }"""
            )
            if not editable:
                return (
                    f"{TOOL_ERROR}: 当前焦点元素不可输入(readonly/disabled 或非输入控件),"
                    f"文本未输入。请先用 browser_focus 指向可编辑的 input/textarea,"
                    f"或改用 browser_type(selector=...) 指定目标"
                )
            await page.keyboard.type(text)
            return f"已通过键盘输入: {text}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 键盘输入失败 (text={text!r}): {e}"

    @_serialise_interaction
    async def insert_text(
        self,
        text: str,
        selector: Optional[str] = None,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """插入文本(不触发按键事件)。适用于 input/textarea 的快速填充。

        不提供 selector 时向当前聚焦元素插入文本;提供时先聚焦到指定元素再插入。
        目标必须是可编辑元素(input/textarea/contenteditable)。
        """
        if bad := _reject_empty_selector(
            selector, omit_hint="向当前焦点元素插入请省略该参数"
        ):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._to_locator(page, selector, frame_selector)
                # focus() 对不可聚焦元素是 no-op —— 此时 keyboard.insert_text
                # 会写进"当前拥有焦点"的元素,造成数据错位。必须先确认可编辑。
                if not await locator.is_editable():
                    return (
                        f"{TOOL_ERROR}: 元素 {selector} 不可编辑"
                        f"(需为 input/textarea/contenteditable),文本未写入。"
                        f"若目标是普通文本节点请改用 browser_evaluate;"
                        f"若要输入到已有输入框请用 browser_type"
                    )
                await locator.focus()
            await page.keyboard.insert_text(text)
            if selector:
                return f"已向元素 {selector} 插入文本: {text}"
            return f"已插入文本: {text}"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 插入文本失败 (selector={selector!r}, text={text!r}): {e}"

    async def get_value(
        self,
        selector: Optional[str] = None,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """获取表单元素的当前值。"""
        if bad := _reject_empty_selector(
            selector, omit_hint="取当前聚焦元素的值请省略该参数"
        ):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            if selector:
                locator = self._to_locator(page, selector, frame_selector)
                value = await locator.input_value(timeout=_SELECTOR_TIMEOUT_MS)
            else:
                value = await page.evaluate("document.activeElement?.value || ''")
            return f"元素值: {value}" if value else "元素值为空"
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            loc = self._to_locator(page, selector, frame_selector) if selector else None
            return await self._sel_err(
                "获取元素值", selector, e, locator=loc, timeout=_SELECTOR_TIMEOUT_MS
            )

    async def get_count(
        self,
        selector: str,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """统计匹配选择器的元素数量。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector, frame_selector)
            count = await locator.count()
            return f'匹配 "{selector}" 的元素数量: {count}'
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 统计元素数量失败 (selector={selector!r}): {e}"

    async def get_bounding_box(
        self,
        selector: str,
        page_id: Optional[int] = None,
        frame_selector: Optional[str] = None,
    ) -> str:
        """获取元素的边界框(位置和尺寸)。"""
        if bad := _reject_empty_selector(selector):
            return bad
        if bad := _reject_empty_selector(
            frame_selector, "frame_selector", omit_hint="不跨 frame 时请省略该参数"
        ):
            return bad
        page = self._get_page(page_id)
        try:
            locator = self._to_locator(page, selector, frame_selector)
            box = await locator.bounding_box(timeout=_SELECTOR_TIMEOUT_MS)
            if box is None:
                return f"{TOOL_ERROR}: 元素 {selector} 不可见或不存在"
            return (
                f"边界框: x={box['x']:.0f}, y={box['y']:.0f}, "
                f"width={box['width']:.0f}, height={box['height']:.0f}"
            )
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return await self._sel_err(
                "获取元素边界框", selector, e,
                locator=locator, timeout=_SELECTOR_TIMEOUT_MS,
            )

    async def get_styles(self, selector: str, page_id: Optional[int] = None) -> str:
        """获取元素的计算样式。"""
        if bad := _reject_empty_selector(selector):
            return bad
        page = self._get_page(page_id)
        try:
            styles = await page.evaluate(
                """
                (selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    const cs = window.getComputedStyle(el);
                    // getPropertyValue 只认 CSS 属性名(kebab-case),
                    // 传 backgroundColor/fontSize/zIndex 这类 camelCase 会拿到空串。
                    // 简写(background/font/margin/padding/border)拆成长胡子属性,
                    // 部分浏览器对简写同样返回空。
                    const props = [
                        'display', 'position', 'width', 'height',
                        'margin-top', 'margin-right', 'margin-bottom', 'margin-left',
                        'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
                        'border-width', 'border-style', 'border-color',
                        'background-color', 'background-image',
                        'color', 'font-size', 'font-weight', 'font-family', 'line-height',
                        'opacity', 'overflow', 'z-index'
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
        except (PlaywrightError, PlaywrightTimeoutError) as e:
            return f"{TOOL_ERROR}: 获取元素样式失败 (selector={selector!r}): {e}"
        if styles is None:
            return f"{TOOL_ERROR}: 元素 {selector} 不存在"
        # 格式化不在此捕获:styles.items() 的 AttributeError 属 bug,应外抛
        lines = [f"元素 {selector} 的计算样式:"]
        for prop, value in styles.items():
            lines.append(f"  {prop}: {value}")
        return "\n".join(lines)

    # ── 历史位置跟踪(back/forward 边界提示用)────────────────────

    def _hist_init(self, pid: Optional[int], url: str) -> None:
        """新页面: 历史只有一项(初始页),游标停在起点。"""
        if pid is None:
            return
        self._hist_urls[pid] = [url]
        self._hist_idx[pid] = 0

    def _hist_push(self, pid: Optional[int], url: str) -> None:
        """navigate: 截掉前进分支后追加一项,游标移到末尾。"""
        if pid is None:
            return
        urls = self._hist_urls.setdefault(pid, [])
        idx = self._hist_idx.get(pid, 0)
        del urls[idx + 1 :]
        urls.append(url)
        self._hist_idx[pid] = len(urls) - 1

    def _hist_move(self, pid: Optional[int], step: int) -> bool:
        """back/forward 后把游标移动 step(-1/+1);已到边界则不动并返回 False。"""
        if pid is None:
            return False
        urls = self._hist_urls.get(pid) or []
        idx = self._hist_idx.get(pid, 0)
        new = idx + step
        if not urls or new < 0 or new >= len(urls):
            return False
        self._hist_idx[pid] = new
        return True

    def _hist_realign(
        self,
        pid: Optional[int],
        *,
        before_url: str,
        after_url: str,
        hist_len: int,
        direction: int,
    ) -> None:
        """浏览器完成 back/forward 后,把历史跟踪对齐到真实位置。

        两条路径:
        1. ``_hist_move`` 成功 —— 跟踪基本可信,只需把比真实历史短的缺项
           补到刚到达的那一端(否则到端点会差一格误报边界)。
        2. ``_hist_move`` 被拒(或无跟踪)—— 跟踪缺项盖不住真实移动,用
           (before, after) + window.history.length 重建最小窗口: 后退时假设
           before 在可达末端、缺项在 after 之前;前进时假设 before 在起点、
           缺项在 after 之后。这是浏览器 back/forward 未暴露当前下标时
           能做到的最好对齐。

        对齐后游标落在 after_url 上,``_hist_at_start`` / ``_hist_at_end``
        判的就是**移动后**位置,边界提示不会滞后或提前一格。

        Args:
            pid: 页面 ID。
            before_url: 导航前 URL。
            after_url: 导航后 URL(浏览器真实落地)。
            hist_len: window.history.length(历史总条数)。
            direction: -1 后退 / +1 前进。
        """
        if pid is None:
            return
        urls = self._hist_urls.get(pid)
        if urls:
            moved = self._hist_move(pid, direction)
            if moved:
                # 跟踪比真实历史短时补到刚到达的那一端,否则端点判断差一格
                missing = hist_len - len(urls)
                if missing > 0:
                    if direction < 0 and self._hist_idx.get(pid, 0) <= 0:
                        urls[:0] = [""] * missing
                        self._hist_idx[pid] = missing
                    elif (
                        direction > 0
                        and self._hist_idx.get(pid, 0) >= len(urls) - 1
                    ):
                        urls.extend([""] * missing)
                return
            # 移动被拒: 跟踪边界之外浏览器却动了 —— 重建
        # 无跟踪 / 移动被拒: 用 (before, after) + hist_len 重建最小窗口
        pad = max(0, hist_len - 2)
        if direction < 0:
            self._hist_urls[pid] = [""] * pad + [after_url, before_url]
            self._hist_idx[pid] = pad
        else:
            self._hist_urls[pid] = [before_url, after_url] + [""] * pad
            self._hist_idx[pid] = 1

    def _hist_at_start(self, pid: Optional[int]) -> Optional[bool]:
        """是否已在历史起点;无跟踪信息时返回 None,由调用方用启发式兜底。"""
        urls = self._hist_urls.get(pid)
        if not urls:
            return None
        return self._hist_idx.get(pid, 0) <= 0

    def _hist_at_end(self, pid: Optional[int]) -> Optional[bool]:
        """是否已在历史末尾;无跟踪信息时返回 None,由调用方用启发式兜底。"""
        urls = self._hist_urls.get(pid)
        if not urls:
            return None
        return self._hist_idx.get(pid, 0) >= len(urls) - 1

    def _hist_drop(self, pid: int) -> None:
        self._hist_urls.pop(pid, None)
        self._hist_idx.pop(pid, None)

    def _get_page(self, page_id: Optional[int] = None):
        """获取指定页面,未指定则返回活动页面。

        Raises:
            ToolUserError: 页面不存在/浏览器未启动/无活动页面 —— 这些是模型传了
                错误 page_id 或调用顺序不对导致的**预期失败**,不是代码 bug,
                故用 ToolUserError 让 Tool.__call__ 直接透出消息且不写错误日志。
        """
        self._ensure_running()
        if page_id is None:
            page = self.active_page
            if page is None:
                raise ToolUserError("没有活动页面")
            return page
        if page_id not in self._pages:
            raise ToolUserError(
                f"页面 {page_id} 不存在(当前可用 page_id: "
                f"{sorted(self._pages) or '无'})"
            )
        return self._pages[page_id]

    def _ensure_running(self):
        """确保浏览器已启动,否则抛出异常。"""
        if self._browser is None:
            raise ToolUserError("浏览器未启动,请先调用 browser_start")

    async def _sel_err(
        self,
        action: str,
        selector: str,
        e,
        *,
        locator=None,
        timeout: int | None = None,
        hint: str | None = None,
        **ctx,
    ) -> str:
        """统一的元素操作报错格式: 操作名 + 定位上下文 + 原始错误 [+ 超时分类提示]。

        格式固定成 `ACTION 失败(selector=..., [其它上下文], [timeout=...ms]): 原因`,
        模型能稳定解析出 selector/timeout,不用为每个工具各记一套措辞。

        Playwright 原始错误(含 call log)保留在末尾 —— 诊断性强。
        超时错误会追加一行「提示:」: 先按异常文本识别真实原因(disabled / 选项值
        不匹配 / 不是 select / 不可见 / 已脱离 DOM), 无命中再按元素是否存在
        做三态分类, 给出可自纠建议。传入 hint 可覆盖自动生成的分类提示(如 drag
        要同时看源/目标)。
        """
        parts = [f"selector={selector!r}"]
        parts += [f"{k}={v!r}" for k, v in ctx.items()]
        if timeout is not None:
            parts.append(f"timeout={timeout}ms")
        msg = f"{TOOL_ERROR}: {action}失败({', '.join(parts)}): {e}"
        if isinstance(e, PlaywrightTimeoutError):
            if hint is None:
                hint = (
                    await _timeout_hint(locator, error_text=str(e))
                    if locator is not None
                    else _timeout_reason_hint(str(e)) or _TIMEOUT_WAIT_ADVICE
                )
            msg += f"\n提示: {hint}"
        elif hint is not None:
            # 非超时错误默认不贴分类提示(原始错误已够清楚), 但调用方显式传了
            # 补充信息(如 select_option 附的可选值列表)时照常展示
            msg += f"\n提示: {hint}"
        return msg

    def _get_locator(self, page, selector: str, frame_selector: Optional[str] = None):
        """根据选择器类型获取 locator。

        frame_selector 提供时先用 page.frame_locator() 进入该 iframe 再定位,
        从而操作 frame 内元素;不提供则只查主文档(不跨 frame)。
        """
        root = page.frame_locator(frame_selector) if frame_selector else page
        if selector.startswith("//"):
            return root.locator(f"xpath={selector}")
        return root.locator(selector)

    def _to_locator(self, page, selector: str, frame_selector: Optional[str] = None):
        """将选择器转换为 Playwright Locator,支持 CSS 和 XPath(// 或 ( 开头)。

        frame_selector 语义同 _get_locator —— 指定时在 iframe 内定位。
        """
        root = page.frame_locator(frame_selector) if frame_selector else page
        if selector.startswith("//") or selector.startswith("("):
            return root.locator(f"xpath={selector}")
        return root.locator(selector)

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
            except PlaywrightError as e:
                # 页面导航/加载中可能瞬时失败,继续轮询
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
            except (PlaywrightError, PlaywrightTimeoutError) as e:
                # JS 表达式错误属参数校验失败,立即返回真实原因,不吞进轮询直到超时
                return (
                    f"{TOOL_ERROR}: JS 条件执行失败 "
                    f"(expression={expression!r}, timeout={timeout}ms): {e}"
                )
            if result:
                return f"JS 条件已满足: {expression} = {result}"
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise TimeoutError(f'等待 JS 条件 "{expression}" 超时 ({timeout}ms)')
