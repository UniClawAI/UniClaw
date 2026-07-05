"""Computer Use 工具 — 提供屏幕截图、鼠标、键盘控制和桌面 UI 自动化功能。"""

import asyncio
import base64
import io
from typing import Optional

import mss
import pyautogui
from uniclaw.tools.base import tool
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from PIL import Image, ImageDraw

# 禁用 pyautogui 的安全暂停和故障保护(在受控环境中使用)
pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = True


def get_cu_system_prompt(config) -> str:
    """返回 Computer Use 模式的系统提示词。"""
    if not config.computer_use_enabled:
        return ""
    return f"""
# Computer Use 模式
你现在拥有完整的计算机控制能力,可以直接操作鼠标、键盘和屏幕。

## ⚠️ 必须遵守的操作顺序
**禁止直接截图猜坐标。** 每次操作桌面应用前,必须先用 `{cu_get_elements.name}` 获取元素列表,
再用 `{cu_interact.name}` 操作。截图仅作为最后手段。

1. `{cu_get_elements.name}` → 获取可交互元素列表
2. `{cu_find_element.name}` → 精确查找某个元素的详细信息
3. `{cu_interact.name}` → 操作元素(click/invoke/focus/type)
4. `{cu_get_elements.name}` → 验证操作结果

**重要:** 通过 `{cu_get_elements.name}` / `{cu_find_element.name}` 找到的元素,
必须用 `{cu_interact.name}` 操作,不要用 `{cu_mouse_click.name}` / `{cu_mouse_double_click.name}`。
因为 `{cu_interact.name}` 通过 API 直接操作元素,不受窗口遮挡影响;
坐标点击则会被遮挡窗口拦截导致失败。

## 降级方案(仅当上述工具返回错误时)
如果 cu_get_elements 返回错误或空结果,才使用截图+坐标:
1. `{cu_screenshot.name}` 截图 → 分析坐标 → `{cu_mouse_move.name}` + `{cu_mouse_click.name}`
2. 不确定坐标时用"试探法":移动鼠标 → 截图 → 观察十字标记 → 调整

## 操作提示
- 坐标系统:左上角 (0,0),向右为 x+,向下为 y+
- 输入英文用 `{cu_keyboard_type.name}`,输入中文用 `{cu_keyboard_type_unicode.name}`
- 组合键:`{cu_keyboard_press.name}("ctrl+c")`
"""


# ── 紧急停止热键 ───────────────────────────────────────────────────
_hotkey_listener = None
def _emergency_stop():
    """紧急停止:仅取消启用了 Computer Write 工具的 agent 任务。"""
    try:
        from uniclaw.agent import MultiAgent

        cu_write_names = {t.name for t in WRITE_TOOLS}
        ma = MultiAgent.get_instance()
        for task in ma.list_tasks():
            if task.allowed_tools_set & cu_write_names:
                task.cancel_event.set()
    except Exception:
        pass


def register_emergency_hotkey() -> bool:
    """注册 Ctrl+U 紧急停止热键(全局,不依赖 config)。"""
    global _hotkey_listener
    if _hotkey_listener is not None:
        return True
    try:
        from pynput import keyboard

        def _on_activate():
            _emergency_stop()

        hotkey = keyboard.HotKey(
            keyboard.HotKey.parse("<ctrl>+u"),
            _on_activate,
        )

        def _on_press(key):
            hotkey.press(listener.canonical(key))

        def _on_release(key):
            hotkey.release(listener.canonical(key))

        listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
        listener.daemon = True
        listener.start()
        _hotkey_listener = listener
        return True
    except Exception:
        return False


def unregister_emergency_hotkey():
    """注销紧急停止热键。"""
    global _hotkey_listener
    if _hotkey_listener is not None:
        try:
            _hotkey_listener.stop()
        except Exception:
            pass
        _hotkey_listener = None

# ── screenshot 同步实现 ────────────────────────────────────────────


def _screenshot_impl(
    region: Optional[str] = None, path: Optional[str] = None
) -> list[dict] | str:
    with mss.mss() as sct:
        if region:
            try:
                x, y, w, h = map(int, region.split(","))
                monitor = {"left": x, "top": y, "width": w, "height": h}
            except ValueError:
                return [
                    {
                        "type": "text",
                        "text": "错误:区域格式无效,请使用 'x,y,width,height' 格式",
                    }
                ]
        else:
            monitor = sct.monitors[0]  # 整个屏幕

        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

        # 获取鼠标位置并绘制标记
        cursor_x, cursor_y = pyautogui.position()
        draw = ImageDraw.Draw(img)

        # 计算相对坐标(如果指定了区域)
        if region:
            rel_x = cursor_x - monitor["left"]
            rel_y = cursor_y - monitor["top"]
        else:
            rel_x = cursor_x
            rel_y = cursor_y

        # 绘制十字标记
        cross_size = 20
        cross_color = (255, 0, 0)  # 红色
        cross_width = 3

        # 水平线
        draw.line(
            [(rel_x - cross_size, rel_y), (rel_x + cross_size, rel_y)],
            fill=cross_color,
            width=cross_width,
        )
        # 垂直线
        draw.line(
            [(rel_x, rel_y - cross_size), (rel_x, rel_y + cross_size)],
            fill=cross_color,
            width=cross_width,
        )
        # 中心圆点
        circle_size = 5
        draw.ellipse(
            [
                (rel_x - circle_size, rel_y - circle_size),
                (rel_x + circle_size, rel_y + circle_size),
            ],
            fill=cross_color,
        )

        # 保存到文件
        if path:
            img.save(path)
            return f"截图已保存到 {path} ({screenshot.width}x{screenshot.height} | 鼠标位置: ({cursor_x}, {cursor_y}))"

        # 转换为 base64
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        size_kb = buffer.getbuffer().nbytes / 1024
        img_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return [
            {
                "type": "text",
                "text": f"{SYSTEM_PREFIX}[屏幕截图: {screenshot.width}x{screenshot.height}, {size_kb:.0f} KB | 鼠标位置: ({cursor_x}, {cursor_y})]",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_base64}"},
            },
        ]


# ── 异步工具 ─────────────────────────────────────────────────────


@tool
async def cu_screenshot(
    region: Optional[str] = None, path: Optional[str] = None
) -> list[dict] | str:
    """截取屏幕截图并返回图像数据。

    ⚠️ 观察屏幕上有什么元素请使用 cu_get_elements 或 cu_find_element,
    本工具仅用于:需要视觉判断的场景(如看图片内容、验证渲染效果、
    游戏画面等无法通过结构化元素发现获取的信息)。

    Args:
        region: 可选的截图区域,格式为 "x,y,width,height"(如 "100,200,800,600")。
                如果不提供,则截取整个屏幕。
        path: 可选的保存路径。提供时截图保存到该文件并返回路径字符串;
              不提供时返回 base64 图像数据列表供 LLM 直接分析。

    Returns:
        提供 path 时返回路径字符串,否则返回包含图像的多模态内容列表。
    """
    return await asyncio.to_thread(_screenshot_impl, region, path)


# ── 同步工具 ─────────────────────────────────────────────────────


@tool
def cu_mouse_move(x: int, y: int, duration: float = 0.5) -> str:
    """移动鼠标到指定位置。

    Args:
        x: 目标位置的 x 坐标。
        y: 目标位置的 y 坐标。
        duration: 移动耗时(秒),默认 0.5 秒。

    Returns:
        操作结果消息。
    """
    pyautogui.moveTo(x, y, duration=duration)
    return f"鼠标已移动到 ({x}, {y})"


@tool
def cu_mouse_click(
    x: int,
    y: int,
    button: str = "left",
    clicks: int = 1,
    interval: float = 0.1,
) -> str:
    """在指定位置点击鼠标。

    Args:
        x: 点击位置的 x 坐标。
        y: 点击位置的 y 坐标。
        button: 鼠标按钮,可选 "left"、"right"、"middle",默认 "left"。
        clicks: 点击次数,默认 1。设为 2 可双击。
        interval: 多次点击之间的间隔(秒),默认 0.1。

    Returns:
        操作结果消息。
    """
    pyautogui.click(x, y, clicks=clicks, button=button, interval=interval)
    action = "双击" if clicks == 2 else "点击"
    return f"已在 ({x}, {y}) {action}鼠标{button}键"


@tool
def cu_mouse_double_click(x: int, y: int, button: str = "left") -> str:
    """在指定位置双击鼠标。

    Args:
        x: 点击位置的 x 坐标。
        y: 点击位置的 y 坐标。
        button: 鼠标按钮,可选 "left"、"right"、"middle",默认 "left"。

    Returns:
        操作结果消息。
    """
    pyautogui.doubleClick(x, y, button=button)
    return f"已在 ({x}, {y}) 双击鼠标{button}键"


@tool
def cu_mouse_drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.5,
    button: str = "left",
) -> str:
    """拖拽鼠标从起始位置到结束位置。

    Args:
        start_x: 起始位置的 x 坐标。
        start_y: 起始位置的 y 坐标。
        end_x: 结束位置的 x 坐标。
        end_y: 结束位置的 y 坐标。
        duration: 拖拽耗时(秒),默认 0.5。
        button: 鼠标按钮,可选 "left"、"right"、"middle",默认 "left"。

    Returns:
        操作结果消息。
    """
    pyautogui.moveTo(start_x, start_y)
    pyautogui.drag(end_x - start_x, end_y - start_y, duration=duration, button=button)
    return f"已从 ({start_x}, {start_y}) 拖拽到 ({end_x}, {end_y})"


@tool
def cu_mouse_scroll(
    clicks: int, x: Optional[int] = None, y: Optional[int] = None
) -> str:
    """滚动鼠标滚轮。

    Args:
        clicks: 滚动量。正数向上滚动,负数向下滚动。
        x: 可选,滚动位置的 x 坐标。不提供则在当前位置滚动。
        y: 可选,滚动位置的 y 坐标。不提供则在当前位置滚动。

    Returns:
        操作结果消息。
    """
    if x is not None and y is not None:
        pyautogui.scroll(clicks, x=x, y=y)
        return f"已在 ({x}, {y}) 滚动 {clicks} 格"
    else:
        pyautogui.scroll(clicks)
        direction = "上" if clicks > 0 else "下"
        return f"已向{direction}滚动 {abs(clicks)} 格"


@tool
def cu_keyboard_type(text: str, interval: float = 0.05) -> str:
    """模拟键盘输入英文文本。仅支持 ASCII 字符,不要传入中文或其他非 ASCII 字符。

    Args:
        text: 要输入的文本内容。只允许英文、数字和常见符号。如需输入中文,请使用 keyboard_type_unicode。
        interval: 按键之间的间隔(秒),默认 0.05。

    Returns:
        操作结果消息。
    """
    pyautogui.typewrite(text, interval=interval)
    return f"已输入文本:{text}"


@tool
def cu_keyboard_type_unicode(text: str, interval: float = 0.05) -> str:
    """模拟键盘输入 Unicode 文本(支持中文等非 ASCII 字符)。

    Args:
        text: 要输入的文本内容。
        interval: 按键之间的间隔(秒),默认 0.05。

    Returns:
        操作结果消息。
    """
    for char in text:
        pyautogui.write(char, interval=interval)
    return f"已输入 Unicode 文本:{text}"


@tool
def cu_keyboard_press(keys: str) -> str:
    """按下键盘按键或组合键。

    Args:
        keys: 按键名称,多个按键用 '+' 连接表示组合键。
              常用按键:enter, tab, escape, space, backspace, delete,
              up, down, left, right, home, end, pageup, pagedown,
              f1-f12, ctrl, alt, shift, win。
              示例:"ctrl+c"(复制)、"alt+tab"(切换窗口)、"enter"(回车)。

    Returns:
        操作结果消息。
    """
    key_list = [k.strip() for k in keys.split("+")]
    pyautogui.hotkey(*key_list)
    return f"已按下按键:{keys}"


@tool
def cu_keyboard_key_down(key: str) -> str:
    """按下并保持键盘按键。

    Args:
        key: 按键名称,如 "shift"、"ctrl"、"alt"。

    Returns:
        操作结果消息。
    """
    pyautogui.keyDown(key)
    return f"已按下并保持:{key}"


@tool
def cu_keyboard_key_up(key: str) -> str:
    """释放键盘按键。

    Args:
        key: 按键名称,如 "shift"、"ctrl"、"alt"。

    Returns:
        操作结果消息。
    """
    pyautogui.keyUp(key)
    return f"已释放按键:{key}"


@tool
def cu_locate_on_screen(image_path: str, confidence: float = 0.8) -> str:
    """在屏幕上查找指定图像的位置。

    Args:
        image_path: 要查找的图像文件路径。
        confidence: 匹配置信度,范围 0-1,默认 0.8。

    Returns:
        找到的图像中心坐标,或未找到的错误消息。
    """
    try:
        location = pyautogui.locateOnScreen(image_path, confidence=confidence)
        if location:
            center = pyautogui.center(location)
            return f"找到图像,中心位置:({center.x}, {center.y})"
        else:
            return f"{TOOL_ERROR}: 未在屏幕上找到指定图像"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── desktop UI 工具(从平台后端导入)─────────────────────────────────
from .automation import cu_get_elements, cu_find_element, cu_interact


# 只读工具(安全,始终可用)
READONLY_TOOLS = [
    cu_screenshot,
    cu_locate_on_screen,
    cu_get_elements,
    cu_find_element,
]

# 写入工具(需要启用 computer use 才可用)
WRITE_TOOLS = [
    cu_mouse_move,
    cu_mouse_click,
    cu_mouse_double_click,
    cu_mouse_drag,
    cu_mouse_scroll,
    cu_keyboard_type,
    cu_keyboard_type_unicode,
    cu_keyboard_press,
    cu_keyboard_key_down,
    cu_keyboard_key_up,
    cu_interact,
]


def get_tools(config) -> list:
    """获取 computer use 工具列表(根据启用状态返回)"""
    if config.computer_use_enabled:
        return READONLY_TOOLS + WRITE_TOOLS
    return READONLY_TOOLS


def get_all_tools() -> list:
    """获取所有 computer use 工具"""
    return READONLY_TOOLS + WRITE_TOOLS
