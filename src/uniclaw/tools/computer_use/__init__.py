"""Computer Use 工具包 — 屏幕截图、鼠标键盘控制和桌面 UI 自动化。"""

from .tools import (
    # 工具列表
    get_tools,
    get_all_tools,
    # 系统提示词
    get_cu_system_prompt,
    # 紧急停止热键
    register_emergency_hotkey,
    unregister_emergency_hotkey,
    # 单个工具对象
    cu_screenshot,
    cu_locate_on_screen,
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
    cu_get_elements,
    cu_find_element,
    cu_interact,
)
