"""平台分发层 — 根据 sys.platform 导入对应的桌面 UI 自动化后端。"""

import sys

if sys.platform == "win32":
    from .automation_win import cu_get_elements, cu_find_element, cu_interact
elif sys.platform == "darwin":
    from .automation_mac import cu_get_elements, cu_find_element, cu_interact
else:
    from .automation_linux import cu_get_elements, cu_find_element, cu_interact
