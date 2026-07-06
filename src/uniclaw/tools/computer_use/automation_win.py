"""Windows UI Automation 后端 — 基于 uiautomation 的桌面 UI 自动化。"""

from __future__ import annotations

from typing import Optional

import uiautomation as auto
from uniclaw.tools.base import tool

from uniclaw.utils.constants import TOOL_ERROR

# ControlType 数值 → 可读名称(去掉 "Control" 后缀)
_TYPE_NAMES = {
    auto.ControlType.ButtonControl: "Button",
    auto.ControlType.EditControl: "Edit",
    auto.ControlType.ComboBoxControl: "ComboBox",
    auto.ControlType.CheckBoxControl: "CheckBox",
    auto.ControlType.RadioButtonControl: "RadioButton",
    auto.ControlType.TabItemControl: "TabItem",
    auto.ControlType.TreeItemControl: "TreeItem",
    auto.ControlType.ListItemControl: "ListItem",
    auto.ControlType.MenuItemControl: "MenuItem",
    auto.ControlType.HyperlinkControl: "Hyperlink",
    auto.ControlType.SliderControl: "Slider",
    auto.ControlType.SpinnerControl: "Spinner",
    auto.ControlType.SplitButtonControl: "SplitButton",
    auto.ControlType.TextControl: "Text",
    auto.ControlType.ListControl: "List",
    auto.ControlType.TreeControl: "Tree",
    auto.ControlType.MenuControl: "Menu",
    auto.ControlType.ToolBarControl: "ToolBar",
    auto.ControlType.StatusBarControl: "StatusBar",
    auto.ControlType.TabControl: "Tab",
    auto.ControlType.WindowControl: "Window",
    auto.ControlType.PaneControl: "Pane",
    auto.ControlType.HeaderControl: "Header",
    auto.ControlType.HeaderItemControl: "HeaderItem",
    auto.ControlType.DataGridControl: "DataGrid",
    auto.ControlType.DataItemControl: "DataItem",
    auto.ControlType.DocumentControl: "Document",
    auto.ControlType.GroupControl: "Group",
    auto.ControlType.ScrollBarControl: "ScrollBar",
    auto.ControlType.ThumbControl: "Thumb",
    auto.ControlType.ImageControl: "Image",
    auto.ControlType.CustomControl: "Custom",
}

# 默认最大遍历深度
DEFAULT_MAX_DEPTH = 3
# 最大返回元素数
MAX_ELEMENTS = 300


def _get_type_name(control_type: int) -> str:
    """
    获取控件类型的可读名称(去掉 "Control" 后缀)。

    优先从预定义的 _TYPE_NAMES 映射表中查找可读名称,
    若未命中则回退到 uiautomation 库的 ControlTypeNames 进行动态解析。

    Args:
        control_type: 控件类型的整数值,对应 auto.ControlType 中定义的常量。

    Returns:
        控件类型的可读名称字符串,已去除 "Control" 后缀；
        若无法识别则返回 "Unknown(<control_type>)" 格式的字符串。
    """
    # 优先从预定义映射表查找,覆盖常见控件类型
    name = _TYPE_NAMES.get(control_type)
    if name:
        return name
    # 回退到 uiautomation 库的 ControlTypeNames 枚举,动态获取完整名称并去除 "Control" 后缀
    full_name = auto.ControlTypeNames.get(control_type, f"Unknown({control_type})")
    if full_name.endswith("Control"):
        return full_name[:-7]
    return full_name


def _get_states(control) -> list[str]:
    """
    获取控件的状态列表。

    通过逐一检测控件的键盘焦点、启用状态和屏幕可见性,
    返回当前控件所处状态的字符串列表。每个状态的检测相互独立,
    单个属性访问失败不会影响其他状态的检测。

    Args:
        control: UI 自动化控件对象,需支持 HasKeyboardFocus、IsEnabled、
                 IsOffscreen 等属性。

    Returns:
        list[str]: 控件当前状态字符串列表,可能的值包括：
                   - "focused": 控件当前拥有键盘焦点；
                   - "disabled": 控件处于禁用状态；
                   - "offscreen": 控件不在屏幕可见区域内。
    """
    states = []
    # 检测控件是否拥有键盘焦点
    try:
        if control.HasKeyboardFocus:
            states.append("focused")
    except Exception:
        pass
    # 检测控件是否处于禁用状态
    try:
        if not control.IsEnabled:
            states.append("disabled")
    except Exception:
        pass
    # 检测控件是否在屏幕可见区域之外
    try:
        if control.IsOffscreen:
            states.append("offscreen")
    except Exception:
        pass
    return states


def get_interactive_elements(
    max_depth: int = DEFAULT_MAX_DEPTH,
    scope_name: Optional[str] = None,
) -> str:
    """从桌面根节点获取 UI 元素列表(不做角色过滤,返回所有控件)。

    只跳过完全空的控件(无名称、无 AutomationId、无屏幕位置)。
    返回结果包含按钮、输入框、文本标签、面板、树节点等所有控件,
    避免过滤导致关键信息丢失。

    Args:
        max_depth: 最大遍历深度,默认 3。探测窗口用 1,深入查找用 3~5。
        scope_name: 限定在某个窗口/容器范围内遍历(按标题子串匹配)。

    Returns:
        格式化的元素列表文本(按窗口分组)。
    """
    root = auto.GetRootControl()

    # 限定在某个范围内
    if scope_name:
        try:
            scope = auto.FindControl(
                root,
                lambda ctrl, depth: scope_name in (ctrl.Name or ""),
                maxDepth=max_depth,
            )
            if scope:
                root = scope
            else:
                return f"{TOOL_ERROR}: 未找到名为 '{scope_name}' 的元素"
        except Exception as e:
            return f"{TOOL_ERROR}: 查找窗口失败: {e}"

    # 遍历 UI 树 — 返回所有控件
    elements = []
    try:
        for control, depth in auto.WalkControl(
            root, maxDepth=max_depth, includeTop=False
        ):
            if len(elements) >= MAX_ELEMENTS:
                break

            try:
                name = control.Name or ""
            except Exception:
                name = ""
            try:
                aid = control.AutomationId or ""
            except Exception:
                aid = ""
            try:
                type_name = _get_type_name(control.ControlType)
            except Exception:
                type_name = "Unknown"

            rect = None
            try:
                r = control.BoundingRectangle
                if r and r.width() > 0 and r.height() > 0:
                    rect = (r.left, r.top, r.width(), r.height())
            except Exception:
                pass

            # 跳过完全空的控件(无名称、无ID、无位置)
            if not rect and not name and not aid:
                continue

            states = _get_states(control)

            # 记录所属窗口名
            try:
                win_ctrl = control
                while (
                    win_ctrl and win_ctrl.ControlType != auto.ControlType.WindowControl
                ):
                    win_ctrl = win_ctrl.GetParentControl()
                window_name = win_ctrl.Name if win_ctrl else ""
            except Exception:
                window_name = ""

            elements.append(
                {
                    "index": len(elements),
                    "type": type_name,
                    "name": name,
                    "automation_id": aid,
                    "rect": rect,
                    "states": states,
                    "depth": depth,
                    "window": window_name,
                }
            )
    except Exception as e:
        return f"{TOOL_ERROR}: 遍历 UI 树失败: {e}"

    if not elements:
        return "未找到可交互元素"

    # 格式化输出(按窗口分组)
    by_window: dict[str, list] = {}
    for el in elements:
        w = el["window"] or "(未知窗口)"
        by_window.setdefault(w, []).append(el)

    lines = [f"找到 {len(elements)} 个可交互元素:\n"]
    for win_name, els in by_window.items():
        lines.append(f"── {win_name} ──")
        for el in els:
            parts = [f"[{el['index']}] {el['type']}"]
            if el["name"]:
                parts.append(f'name="{el["name"]}"')
            if el["automation_id"]:
                parts.append(f'automation_id="{el["automation_id"]}"')
            if el["rect"]:
                l, t, w, h = el["rect"]
                parts.append(f"bounds=({l},{t},{w}x{h})")
            if el["states"]:
                parts.append(f'states=[{",".join(el["states"])}]')
            lines.append("  " + " | ".join(parts))
        lines.append("")

    result = "\n".join(lines)
    if len(result) > 50000:
        return result[:50000] + f"\n\n...已省略 {len(result) - 50000} 个字符"
    return result


def _get_native_handle(ctrl) -> int:
    """获取控件所属父窗口的原生 HWND。

    沿 UIA 树向上找到父窗口(WindowControl),它必定有窗口句柄。
    """
    try:
        last_hwnd = 0
        parent = ctrl.GetParentControl()
        while parent:
            try:
                if parent.ControlType == auto.ControlType.WindowControl:
                    last_hwnd = int(parent.NativeWindowHandle)
                ctrl = parent
                parent = ctrl.GetParentControl()
            except Exception:
                pass
        return last_hwnd
    except Exception as e:
        print(e)
    return 0


def _get_top_level_hwnd(hwnd: int) -> int:
    """沿 HWND 父链走到最顶级窗口。"""
    import ctypes

    user32 = ctypes.windll.user32
    while hwnd:
        parent = user32.GetParent(hwnd)
        if not parent:
            break
        hwnd = parent
    return hwnd


def _get_window_title(hwnd: int) -> str:
    """获取指定 HWND 的窗口标题。"""
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _activate_window(hwnd: int) -> None:
    """激活指定窗口。最小化时先恢复,再通过 Alt 按键 + SetForegroundWindow 激活。"""
    import ctypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9

    # 已经是前台窗口则跳过
    if user32.GetForegroundWindow() == hwnd:
        return

    # 最小化的窗口先恢复
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    # 模拟 Alt 按键获取输入权限,再激活
    VK_MENU = 0x12
    KEYEVENTF_EXTENDEDKEY = 0x01
    KEYEVENTF_KEYUP = 0x02
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)


def _check_occluded(ctrl) -> Optional[str]:
    """用 WindowFromPoint 检测元素中心点是否被其他窗口遮挡。

    Returns:
        None 表示未被遮挡;否则返回错误消息。
    """
    import ctypes

    try:
        r = ctrl.BoundingRectangle
        if not r or r.width() <= 0 or r.height() <= 0:
            return None  # 无法获取位置,跳过遮挡检测

        # 元素中心点
        cx = int((r.left + r.right) / 2)
        cy = int((r.top + r.bottom) / 2)

        user32 = ctypes.windll.user32
        # WindowFromPoint 在 64 位下接收 c_int64 而非 POINT 结构体
        packed_point = ctypes.c_int64(cx | (cy << 32))

        # 获取元素所属顶层窗口句柄
        ctrl_hwnd = _get_native_handle(ctrl)
        if not ctrl_hwnd:
            return None  # 无法获取,放行

        # 被其他窗口遮挡,尝试激活所属窗口
        try:
            _activate_window(ctrl_hwnd)
            import time

            time.sleep(0.3)
        except Exception:
            pass

        # 重新检测
        hwnd_at_point = user32.WindowFromPoint(packed_point)
        if hwnd_at_point:
            top_at_point = _get_top_level_hwnd(hwnd_at_point)
            if top_at_point == ctrl_hwnd:
                return None  # 聚焦后已解除遮挡
            # 获取遮挡窗口标题
            blocker_title = _get_window_title(top_at_point)
            blocker_info = f'("{blocker_title}")' if blocker_title else ""
            return f"{TOOL_ERROR}: 元素 {_element_label(ctrl)} 被窗口{blocker_info}遮挡,请先关闭或最小化该窗口"

        return f"{TOOL_ERROR}: 元素 {_element_label(ctrl)} 被遮挡且无法识别遮挡窗口"
    except Exception:
        return None  # 检测失败时不阻塞操作


def _ensure_visible(ctrl) -> Optional[str]:
    """确保控件可见且未被遮挡。

    检查顺序:离屏 → 边界矩形 → 遮挡(WindowFromPoint)。

    Returns:
        None 表示可见可操作;否则返回错误/警告消息。
    """
    # 1. 离屏检查
    try:
        if ctrl.IsOffscreen:
            hwnd = _get_native_handle(ctrl)
            if hwnd:
                _activate_window(hwnd)
                import time

                time.sleep(0.3)
            if ctrl.IsOffscreen:
                return f"{TOOL_ERROR}: 元素 {_element_label(ctrl)} 不在屏幕上(最小化或滚动出视野)"
    except Exception:
        pass

    # 2. 边界矩形检查
    try:
        r = ctrl.BoundingRectangle
        if not r or r.width() <= 0 or r.height() <= 0:
            return None  # 无法获取位置,跳过后续检查
    except Exception:
        pass

    # 3. 遮挡检查
    occluded = _check_occluded(ctrl)
    if occluded:
        return occluded

    return None


def find_element(
    name: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
) -> str:
    """精确查找单个元素,返回详细信息。

    Args:
        name: 元素名称(精确匹配)。
        automation_id: 自动化 ID。
        control_type: 控件类型名称(如 "Button", "Edit")。

    Returns:
        元素详细信息文本。
    """
    if not name and not automation_id:
        return f"{TOOL_ERROR}: 必须提供 name 或 automation_id"

    root = auto.GetRootControl()
    ct = _resolve_control_type(control_type) if control_type else None

    def _match(ctrl, depth):
        if name and name not in (ctrl.Name or ""):
            return False
        if automation_id and automation_id != (ctrl.AutomationId or ""):
            return False
        if ct and ctrl.ControlType != ct:
            return False
        return True

    try:
        ctrl = auto.FindControl(root, _match, maxDepth=20)
        if not ctrl:
            return f"{TOOL_ERROR}: 未找到匹配的元素"

        return _format_element_detail(ctrl)
    except Exception as e:
        return f"{TOOL_ERROR}: 查找失败: {e}"


def interact(
    name: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    action: str = "click",
    type_text: Optional[str] = None,
) -> str:
    """与桌面 UI 元素交互。

    Args:
        name: 元素名称。
        automation_id: 自动化 ID。
        control_type: 控件类型名称。
        action: 操作类型 — click/invoke/focus/type。
        type_text: 要输入的文本(action="type" 时使用)。

    Returns:
        操作结果消息。
    """
    action = action.lower()
    # 尝试 UIA 定位
    ctrl = None
    if name or automation_id:
        root = auto.GetRootControl()
        ct = _resolve_control_type(control_type) if control_type else None

        def _match(c, d):
            if name and name not in (c.Name or ""):
                return False
            if automation_id and automation_id != (c.AutomationId or ""):
                return False
            if ct and c.ControlType != ct:
                return False
            return True

        try:
            ctrl = auto.FindControl(root, _match, maxDepth=20)
        except Exception:
            ctrl = None

    # UIA 未找到,降级到坐标
    if ctrl is None:
        return f"{TOOL_ERROR}: 未找到匹配的元素"

    # 执行 UIA 操作
    try:
        # 点击/调用前检查元素可见性
        if action in ("click", "invoke"):
            warn = _ensure_visible(ctrl)
            if warn:
                return warn

        if action == "click":
            ctrl.Click()
            return f"已点击元素: {_element_label(ctrl)}"
        elif action == "invoke":
            try:
                pattern = ctrl.GetPattern(auto.PatternId.InvokePattern)
                pattern.Invoke()
                return f"已调用元素: {_element_label(ctrl)}"
            except Exception:
                ctrl.Click()
                return f"已点击元素(Invoke 不支持): {_element_label(ctrl)}"
        elif action == "focus":
            ctrl.SetFocus()
            return f"已聚焦元素: {_element_label(ctrl)}"
        elif action == "type":
            if not type_text:
                return f"{TOOL_ERROR}: action='type' 时必须提供 type_text"
            ctrl.SetFocus()
            import time

            time.sleep(0.1)
            # 优先使用 ValuePattern
            try:
                vp = ctrl.GetPattern(auto.PatternId.ValuePattern)
                vp.SetValue(type_text)
                return f"已通过 ValuePattern 输入文本到: {_element_label(ctrl)}"
            except Exception:
                pass
            # 降级到 SendKeys
            ctrl.SendKeys(type_text)
            return f"已通过 SendKeys 输入文本到: {_element_label(ctrl)}"
        else:
            return (
                f"{TOOL_ERROR}: 不支持的操作 '{action}',可选: click/invoke/focus/type"
            )
    except Exception as e:
        return f"{TOOL_ERROR}: 操作失败: {e}"


def _element_label(ctrl) -> str:
    """生成元素的简短标签。"""
    try:
        name = ctrl.Name or ""
    except Exception:
        name = ""
    try:
        aid = ctrl.AutomationId or ""
    except Exception:
        aid = ""
    try:
        type_name = _get_type_name(ctrl.ControlType)
    except Exception:
        type_name = "Unknown"

    parts = [type_name]
    if name:
        parts.append(f'"{name}"')
    if aid:
        parts.append(f"[{aid}]")
    return " ".join(parts)


def _format_element_detail(ctrl) -> str:
    """格式化单个元素的详细信息。"""
    lines = []
    try:
        lines.append(f"类型: {_get_type_name(ctrl.ControlType)}")
    except Exception:
        pass
    try:
        lines.append(f'名称: "{ctrl.Name}"')
    except Exception:
        pass
    try:
        aid = ctrl.AutomationId
        if aid:
            lines.append(f"AutomationId: {aid}")
    except Exception:
        pass
    try:
        cn = ctrl.ClassName
        if cn:
            lines.append(f"ClassName: {cn}")
    except Exception:
        pass
    try:
        r = ctrl.BoundingRectangle
        if r:
            lines.append(f"位置: ({r.left}, {r.top})")
            lines.append(f"大小: {r.width()}x{r.height()}")
    except Exception:
        pass
    try:
        lines.append(f"启用: {ctrl.IsEnabled}")
    except Exception:
        pass
    try:
        lines.append(f"可见: {not ctrl.IsOffscreen}")
    except Exception:
        pass
    try:
        lines.append(f"焦点: {ctrl.HasKeyboardFocus}")
    except Exception:
        pass

    # 列出支持的模式
    patterns = []
    pattern_map = {
        auto.PatternId.InvokePattern: "Invoke",
        auto.PatternId.ValuePattern: "Value",
        auto.PatternId.TogglePattern: "Toggle",
        auto.PatternId.SelectionItemPattern: "SelectionItem",
        auto.PatternId.ExpandCollapsePattern: "ExpandCollapse",
        auto.PatternId.ScrollItemPattern: "ScrollItem",
    }
    for pid, pname in pattern_map.items():
        try:
            if ctrl.GetPattern(pid):
                patterns.append(pname)
        except Exception:
            pass
    if patterns:
        lines.append(f"支持的模式: {', '.join(patterns)}")

    # 子元素数量
    try:
        children = ctrl.GetChildren()
        lines.append(f"子元素数: {len(children)}")
    except Exception:
        pass

    return "\n".join(lines)


def _resolve_control_type(name: str) -> Optional[int]:
    """将控件类型名称解析为 ControlType 常量。"""
    name_lower = name.lower()
    _NAME_TO_TYPE = {
        "button": auto.ControlType.ButtonControl,
        "edit": auto.ControlType.EditControl,
        "combobox": auto.ControlType.ComboBoxControl,
        "checkbox": auto.ControlType.CheckBoxControl,
        "radiobutton": auto.ControlType.RadioButtonControl,
        "tabitem": auto.ControlType.TabItemControl,
        "treeitem": auto.ControlType.TreeItemControl,
        "listitem": auto.ControlType.ListItemControl,
        "menuitem": auto.ControlType.MenuItemControl,
        "hyperlink": auto.ControlType.HyperlinkControl,
        "slider": auto.ControlType.SliderControl,
        "spinner": auto.ControlType.SpinnerControl,
        "splitbutton": auto.ControlType.SplitButtonControl,
        "text": auto.ControlType.TextControl,
        "list": auto.ControlType.ListControl,
        "tree": auto.ControlType.TreeControl,
        "menu": auto.ControlType.MenuControl,
        "toolbar": auto.ControlType.ToolBarControl,
        "statusbar": auto.ControlType.StatusBarControl,
        "tab": auto.ControlType.TabControl,
        "window": auto.ControlType.WindowControl,
        "pane": auto.ControlType.PaneControl,
        "header": auto.ControlType.HeaderControl,
        "headeritem": auto.ControlType.HeaderItemControl,
        "datagrid": auto.ControlType.DataGridControl,
        "dataitem": auto.ControlType.DataItemControl,
        "document": auto.ControlType.DocumentControl,
        "group": auto.ControlType.GroupControl,
        "image": auto.ControlType.ImageControl,
        "custom": auto.ControlType.CustomControl,
    }
    return _NAME_TO_TYPE.get(name_lower)


# ── 工具定义 ──────────────────────────────────────────────────────


@tool
def cu_get_elements(
    max_depth: int = DEFAULT_MAX_DEPTH,
    scope_name: Optional[str] = None,
) -> str:
    """获取桌面上的 UI 元素(Windows UI Automation)。

    返回所有控件(按钮、输入框、文本标签、面板、树节点等),
    跳过完全空的控件。

    推荐逐层查找法,避免一次返回过多无关元素:

    第一步 — 列出顶层窗口(设 max_depth=1):
      cu_get_elements(max_depth=1)
      → 返回桌面下所有窗口标题,确定目标窗口名称。

    第二步 — 深入目标窗口(设 scope_name + 较大 max_depth):
      cu_get_elements(scope_name="窗口标题", max_depth=5)
      → 只遍历该窗口内部,返回其中的所有控件。

    如果第二步元素仍然太多,可以再次缩小 scope_name 到某个子容器
    (如工具栏、面板),进一步聚焦。

    Args:
        max_depth: UI 树遍历深度,越小越快,默认 3。
            第一步探测用 1,深入查找用 3~5。
        scope_name: 限定在某个窗口/容器范围内遍历(按标题子串匹配)。
            从第一步结果中选取目标窗口名填入。

    Returns:
        UI 元素的结构化列表(按窗口分组),包含类型、名称、位置等信息。
    """
    return get_interactive_elements(max_depth, scope_name)


@tool
def cu_find_element(
    name: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
) -> str:
    """精确查找 Windows 桌面应用中的单个 UI 元素。

    支持按名称、AutomationId 或控件类型查找。返回元素的位置、大小、
    状态和支持的交互模式(如 Invoke、Value、Toggle)。

    Args:
        name: 元素名称(精确匹配)。
        automation_id: 元素的 AutomationId(最可靠的定位方式)。
        control_type: 控件类型,如 "Button"、"Edit"、"ComboBox"。

    Returns:
        元素的详细属性信息。
    """
    return find_element(name, automation_id, control_type)


@tool
def cu_interact(
    name: Optional[str] = None,
    automation_id: Optional[str] = None,
    control_type: Optional[str] = None,
    action: str = "click",
    type_text: Optional[str] = None,
) -> str:
    """与 Windows 桌面应用中的 UI 元素交互。

    优先通过 UI Automation 定位元素(name/automation_id),
    未找到时若提供了 x/y 坐标则降级到鼠标点击。

    Args:
        name: 目标元素名称。
        automation_id: 目标元素的 AutomationId。
        control_type: 控件类型,如 "Button"、"Edit"。
        action: 操作类型 — click(点击)、invoke(调用)、focus(聚焦)、type(输入文本)。
        type_text: 要输入的文本,仅 action="type" 时使用。

    Returns:
        操作结果消息。
    """
    return interact(name, automation_id, control_type, action, type_text)
