"""macOS 后端 — 基于 pyobjc ApplicationServices 的桌面 UI 自动化。"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 5
MAX_ELEMENTS = 200

# 懒加载 Accessibility 框架
_ax = None


def _get_ax():
    """延迟导入 ApplicationServices。"""
    global _ax
    if _ax is None:
        try:
            import ApplicationServices as _app

            _ax = _app
        except ImportError:
            raise ImportError(
                "macOS 后端需要 pyobjc-framework-ApplicationServices: "
                "pip install pyobjc-framework-ApplicationServices"
            )
    return _ax


def _get_system_wide():
    """获取系统级 accessibility 元素。"""
    ax = _get_ax()
    return ax.AXUIElementCreateSystemWide()


def _get_focused_app():
    """获取当前焦点应用的 AXUIElement。"""
    ax = _get_ax()
    sys_wide = _get_system_wide()
    err, value = ax.AXUIElementCopyAttributeValue(
        sys_wide, "AXFocusedApplication", None
    )
    if err == 0 and value:
        return value
    return None


def _get_children(element, depth: int, max_depth: int, results: list):
    """递归遍历 AX 元素树。"""
    if depth > max_depth or len(results) >= MAX_ELEMENTS:
        return
    ax = _get_ax()
    err, children = ax.AXUIElementCopyAttributeValue(element, "AXChildren", None)
    if err != 0 or not children:
        return
    for child in children:
        if len(results) >= MAX_ELEMENTS:
            return
        role = _get_attr(child, "AXRole")
        if _is_interactive_role(role):
            results.append((child, depth))
        _get_children(child, depth + 1, max_depth, results)


def _get_attr(element, attr: str):
    """安全获取 AX 属性。"""
    ax = _get_ax()
    err, value = ax.AXUIElementCopyAttributeValue(element, attr, None)
    if err == 0:
        return value
    return None


def _is_interactive_role(role: str | None) -> bool:
    """判断是否为可交互角色。"""
    if not role:
        return False
    interactive = {
        "AXButton",
        "AXTextField",
        "AXTextArea",
        "AXComboBox",
        "AXCheckBox",
        "AXRadioButton",
        "AXMenuItem",
        "AXMenuBarItem",
        "AXSlider",
        "AXLink",
        "AXTab",
        "AXDisclosureTriangle",
        "AXPopUpButton",
        "AXIncrementor",
    }
    return role in interactive


def _get_bounds(element) -> tuple[int, int, int, int] | None:
    """获取元素的位置和大小 (x, y, w, h)。"""
    pos = _get_attr(element, "AXPosition")
    size = _get_attr(element, "AXSize")
    if pos and size:
        try:
            import Quartz

            px, py = Quartz.CGPointMake(0, 0), Quartz.CGSizeMake(0, 0)
            ok1, px = Quartz.AXValueGetValue(pos, Quartz.kAXValueCGPointType, None)
            ok2, sz = Quartz.AXValueGetValue(size, Quartz.kAXValueCGSizeType, None)
            if ok1 and ok2:
                return (int(px.x), int(py.y), int(sz.width), int(sz.height))
        except Exception as e:
            logger.debug("获取 macOS 控件位置/大小失败: %s", e)
    return None


def get_interactive_elements(
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> str:
    """获取当前焦点应用中所有可交互的 UI 元素列表。"""
    try:
        app = _get_focused_app()
        if not app:
            return f"{TOOL_ERROR}: 无法获取焦点应用(检查辅助功能权限:系统设置 → 隐私 → 辅助功能)"

        root = app
        app_title = _get_attr(app, "AXTitle") or "(未知应用)"

        elements = []
        _get_children(root, 0, max_depth, elements)

        if not elements:
            return f"应用 '{app_title}' 中未找到可交互元素"

        lines = [f"应用: {app_title}", f"找到 {len(elements)} 个可交互元素:\n"]
        for i, (el, depth) in enumerate(elements):
            role = _get_attr(el, "AXRole") or "Unknown"
            title = _get_attr(el, "AXTitle") or ""
            desc = _get_attr(el, "AXDescription") or ""
            value = _get_attr(el, "AXValue") or ""
            bounds = _get_bounds(el)

            parts = [f"[{i}] {role}"]
            if title:
                parts.append(f'title="{title}"')
            if desc:
                parts.append(f'desc="{desc}"')
            if value and isinstance(value, str):
                parts.append(f'value="{value[:50]}"')
            if bounds:
                x, y, w, h = bounds
                parts.append(f"bounds=({x},{y},{w}x{h})")
            lines.append(" | ".join(parts))

        result = "\n".join(lines)
        if len(result) > 50000:
            return result[:50000]
        return result

    except ImportError as e:
        return f"{TOOL_ERROR}: {e}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


def find_element(
    name: Optional[str] = None,
) -> str:
    """按标题或描述查找元素。"""
    if not name:
        return f"{TOOL_ERROR}: macOS 后端需要提供 name 参数"

    try:
        app = _get_focused_app()
        if not app:
            return f"{TOOL_ERROR}: 无法获取焦点应用"

        # 递归查找
        results = []
        _find_recursive(app, name, 0, 10, results)
        if not results:
            return f"{TOOL_ERROR}: 未找到名为 '{name}' 的元素"

        el = results[0]
        return _format_detail(el)

    except ImportError as e:
        return f"{TOOL_ERROR}: {e}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


def _find_recursive(element, name: str, depth: int, max_depth: int, results: list):
    """递归查找匹配名称的元素。"""
    if depth > max_depth or results:
        return
    title = _get_attr(element, "AXTitle") or ""
    desc = _get_attr(element, "AXDescription") or ""
    if name in title or name in desc:
        results.append(element)
        return
    ax = _get_ax()
    err, children = ax.AXUIElementCopyAttributeValue(element, "AXChildren", None)
    if err == 0 and children:
        for child in children:
            _find_recursive(child, name, depth + 1, max_depth, results)
            if results:
                return


def _format_detail(element) -> str:
    """格式化元素详细信息。"""
    lines = []
    role = _get_attr(element, "AXRole")
    title = _get_attr(element, "AXTitle")
    desc = _get_attr(element, "AXDescription")
    value = _get_attr(element, "AXValue")
    enabled = _get_attr(element, "AXEnabled")
    focused = _get_attr(element, "AXFocused")
    bounds = _get_bounds(element)

    if role:
        lines.append(f"角色: {role}")
    if title:
        lines.append(f'标题: "{title}"')
    if desc:
        lines.append(f'描述: "{desc}"')
    if value and isinstance(value, str):
        lines.append(f'值: "{value}"')
    if bounds:
        x, y, w, h = bounds
        lines.append(f"位置: ({x}, {y})")
        lines.append(f"大小: {w}x{h}")
    if enabled is not None:
        lines.append(f"启用: {enabled}")
    if focused is not None:
        lines.append(f"焦点: {focused}")

    # 支持的动作
    ax = _get_ax()
    err, actions = ax.AXUIElementCopyActionNames(element)
    if err == 0 and actions:
        lines.append(f"动作: {', '.join(actions)}")

    return "\n".join(lines)


async def interact(
    name: Optional[str] = None,
    action: str = "click",
    type_text: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> str:
    """与 UI 元素交互。"""
    import pyautogui

    # 查找元素
    el = None
    if name:
        try:
            app = _get_focused_app()
            if app:
                results = []
                _find_recursive(app, name, 0, 10, results)
                if results:
                    el = results[0]
        except Exception as e:
            logger.debug("macOS 查找 UI 元素失败: %s", e)
            el = None

    # 降级到坐标
    if el is None:
        if x is not None and y is not None:
            return await _fallback(x, y, action, type_text)
        return f"{TOOL_ERROR}: 未找到元素,且未提供降级坐标"

    try:
        ax = _get_ax()
        if action == "click":
            ax.AXUIElementPerformAction(el, "AXPress")
            return f"已点击: {_format_label(el)}"
        elif action == "invoke":
            ax.AXUIElementPerformAction(el, "AXPress")
            return f"已调用: {_format_label(el)}"
        elif action == "focus":
            ax.AXUIElementSetAttributeValue(el, "AXFocused", True)
            return f"已聚焦: {_format_label(el)}"
        elif action == "type":
            if not type_text:
                return f"{TOOL_ERROR}: action='type' 时必须提供 type_text"
            ax.AXUIElementPerformAction(el, "AXPress")
            await asyncio.sleep(0.1)
            pyautogui.typewrite(type_text, interval=0.05)
            return f"已输入文本到: {_format_label(el)}"
        else:
            return f"{TOOL_ERROR}: 不支持的操作 '{action}'"
    except Exception as e:
        return f"{TOOL_ERROR}: 操作失败: {e}"


async def _fallback(x: int, y: int, action: str, type_text: Optional[str]) -> str:
    """坐标降级。"""
    import pyautogui

    if action in ("click", "invoke", "focus"):
        pyautogui.click(x, y)
        return f"已降级到坐标点击: ({x}, {y})"
    elif action == "type":
        pyautogui.click(x, y)
        if type_text:
            await asyncio.sleep(0.1)
            pyautogui.typewrite(type_text, interval=0.05)
        return f"已降级到坐标输入: ({x}, {y})"
    return f"{TOOL_ERROR}: 不支持的操作 '{action}'"


def _format_label(element) -> str:
    """元素简短标签。"""
    role = _get_attr(element, "AXRole") or "Unknown"
    title = _get_attr(element, "AXTitle") or ""
    return f'{role} "{title}"' if title else role


# ── 工具定义 ──────────────────────────────────────────────────────


@tool
def cu_get_elements(
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> str:
    """获取当前焦点应用中可交互的 UI 元素(macOS Accessibility)。

    推荐逐层查找法,避免一次返回过多无关元素:

    第一步 — 浅层探测(设 max_depth=1~2):
      cu_get_elements(max_depth=1)
      → 返回顶层容器(窗口、面板、工具栏),确定目标区域。

    第二步 — 深入目标区域(设 max_depth=4~6):
      cu_get_elements(max_depth=5)
      → 遍历更深层级,找到具体的按钮、输入框等。

    如果元素太多,可配合 cu_find_element(name=...) 精确定位。

    Args:
        max_depth: UI 树遍历深度,越小越快,默认 5。
            第一步探测用 1~2,深入查找用 4~6。

    Returns:
        可交互元素的结构化列表。
    """
    return get_interactive_elements(max_depth)


@tool
def cu_find_element(
    name: Optional[str] = None,
) -> str:
    """在 macOS 当前焦点应用中按标题或描述查找 UI 元素。

    Args:
        name: 元素标题或描述(子串匹配)。

    Returns:
        元素的详细属性和支持的动作。
    """
    return find_element(name)


@tool
async def cu_interact(
    name: Optional[str] = None,
    action: str = "click",
    type_text: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
) -> str:
    """与 macOS 桌面应用中的 UI 元素交互。

    优先通过 Accessibility API 定位元素,未找到时降级到坐标点击。

    Args:
        name: 目标元素标题或描述。
        action: click(点击)、invoke(调用)、focus(聚焦)、type(输入文本)。
        type_text: 要输入的文本,仅 action="type" 时使用。
        x: 降级坐标 x,元素未找到时使用鼠标点击。
        y: 降级坐标 y,元素未找到时使用鼠标点击。

    Returns:
        操作结果消息。
    """
    return await interact(name=name, action=action, type_text=type_text, x=x, y=y)
