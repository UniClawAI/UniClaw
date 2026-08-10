"""Computer Use 工具测试 — 覆盖 tools.py 层的截图、鼠标键盘、紧急热键和工具列表。

注意:
- 所有 @tool 包装的工具其 __call__ 都是 async,即使源函数是同步 def,测试中必须 await。
- pyautogui 通过 _get_pyautogui() 延迟导入,测试统一 patch 该函数返回 MagicMock。
- 注册紧急停止热键会修改模块级 _hotkey_listener,每个用例用 patch.object 隔离。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch

from uniclaw.tools.computer_use import tools as cu_mod
from uniclaw.utils.constants import TOOL_ERROR


class TestGetCuSystemPrompt:
    """get_cu_system_prompt 测试。"""

    def test_disabled_returns_empty(self):
        """未启用返回空字符串。"""
        config = MagicMock()
        config.computer_use_enabled = False
        assert cu_mod.get_cu_system_prompt(config) == ""

    def test_enabled_returns_prompt(self):
        """启用返回完整提示词。"""
        config = MagicMock()
        config.computer_use_enabled = True
        result = cu_mod.get_cu_system_prompt(config)
        assert "# Computer Use 模式" in result
        assert "先观察,再操作" in result
        assert "cu_get_elements" in result
        assert "cu_interact" in result
        assert "cu_screenshot" in result
        assert "cu_keyboard_press" in result


class TestEmergencyStop:
    """_emergency_stop 测试。"""

    def test_cancels_write_tasks(self):
        """只取消启用了写入工具的 agent 任务。"""
        ma = MagicMock()
        task1 = MagicMock()
        task1.allowed_tools_set = {"cu_mouse_click", "Read"}
        task2 = MagicMock()
        task2.allowed_tools_set = {"Read"}
        ma.list_tasks.return_value = [task1, task2]
        with patch("uniclaw.agent.MultiAgent.get_instance", return_value=ma):
            cu_mod._emergency_stop()
        task1.cancel_event.set.assert_called_once()
        task2.cancel_event.set.assert_not_called()

    def test_no_write_tools(self):
        """无写入工具的任务不被取消。"""
        ma = MagicMock()
        task = MagicMock()
        task.allowed_tools_set = {"cu_screenshot", "Read"}
        ma.list_tasks.return_value = [task]
        with patch("uniclaw.agent.MultiAgent.get_instance", return_value=ma):
            cu_mod._emergency_stop()
        task.cancel_event.set.assert_not_called()

    def test_exception_swallowed(self):
        """get_instance 抛异常时静默。"""
        with patch(
            "uniclaw.agent.MultiAgent.get_instance", side_effect=RuntimeError("boom")
        ):
            cu_mod._emergency_stop()  # 不应抛出

    def test_empty_tasks(self):
        """无任务时无操作。"""
        ma = MagicMock()
        ma.list_tasks.return_value = []
        with patch("uniclaw.agent.MultiAgent.get_instance", return_value=ma):
            cu_mod._emergency_stop()


class TestEmergencyHotkey:
    """register/unregister_emergency_hotkey 测试。"""

    @patch("pynput.keyboard")
    def test_register_success(self, mock_keyboard):
        """注册成功返回 True 并启动监听器。"""
        listener = MagicMock()
        mock_keyboard.Listener.return_value = listener
        with patch.object(cu_mod, "_hotkey_listener", None):
            result = cu_mod.register_emergency_hotkey()
            assert result is True
            assert cu_mod._hotkey_listener is listener
        listener.start.assert_called_once()
        assert listener.daemon is True
        mock_keyboard.HotKey.parse.assert_called_once_with("<ctrl>+u")

    @patch("pynput.keyboard")
    def test_register_twice_skips(self, mock_keyboard):
        """已有监听器时直接返回 True。"""
        listener = MagicMock()
        mock_keyboard.Listener.return_value = listener
        with patch.object(cu_mod, "_hotkey_listener", MagicMock()):
            result = cu_mod.register_emergency_hotkey()
        assert result is True
        listener.start.assert_not_called()

    @patch("pynput.keyboard")
    def test_register_failure(self, mock_keyboard):
        """Listener 构造失败返回 False。"""
        mock_keyboard.Listener.side_effect = RuntimeError("boom")
        with patch.object(cu_mod, "_hotkey_listener", None):
            result = cu_mod.register_emergency_hotkey()
        assert result is False

    def test_unregister_none(self):
        """无监听器时无操作。"""
        with patch.object(cu_mod, "_hotkey_listener", None):
            cu_mod.unregister_emergency_hotkey()  # 不应抛出

    def test_unregister_stops(self):
        """有监听器时停止并置空。"""
        listener = MagicMock()
        with patch.object(cu_mod, "_hotkey_listener", listener):
            cu_mod.unregister_emergency_hotkey()
            assert cu_mod._hotkey_listener is None
        listener.stop.assert_called_once()

    def test_unregister_stop_exception(self):
        """stop 抛异常时静默并置空。"""
        listener = MagicMock()
        listener.stop.side_effect = RuntimeError("boom")
        with patch.object(cu_mod, "_hotkey_listener", listener):
            cu_mod.unregister_emergency_hotkey()  # 不应抛出
            assert cu_mod._hotkey_listener is None


def _screenshot_mock(width=320, height=240, left=0, top=0):
    """构造 mss 截图上下文 mock。"""
    sct = MagicMock()
    sct.monitors = [{"left": 0, "top": 0, "width": 800, "height": 600}]
    shot = MagicMock()
    shot.size = (width, height)
    shot.width = width
    shot.height = height
    shot.bgra = bytes(width * height * 4)
    sct.grab.return_value = shot
    return sct


class TestScreenshotImpl:
    """_screenshot_impl 测试。"""

    def test_invalid_region(self):
        """区域格式无效返回错误块。"""
        with patch("uniclaw.tools.computer_use.tools.mss.mss") as mss_mock:
            sct = _screenshot_mock()
            mss_mock.return_value.__enter__.return_value = sct
            result = cu_mod._screenshot_impl("abc")
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "区域格式无效" in result[0]["text"]
        sct.grab.assert_not_called()

    def test_full_screen_base64(self):
        """整屏截图返回 base64 多模态。"""
        pa = MagicMock()
        pa.position.return_value = (100, 200)
        with patch("uniclaw.tools.computer_use.tools.mss.mss") as mss_mock, patch(
            "uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa
        ):
            sct = _screenshot_mock()
            mss_mock.return_value.__enter__.return_value = sct
            result = cu_mod._screenshot_impl()
        assert isinstance(result, list)
        assert result[0]["type"] == "text"
        assert "屏幕截图" in result[0]["text"]
        assert "320x240" in result[0]["text"]
        assert "鼠标位置: (100, 200)" in result[0]["text"]
        assert result[1]["type"] == "image_url"
        url = result[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_region_save_path(self, tmp_path):
        """区域截图保存到文件。"""
        pa = MagicMock()
        pa.position.return_value = (100, 200)
        target = tmp_path / "shot.png"
        with patch("uniclaw.tools.computer_use.tools.mss.mss") as mss_mock, patch(
            "uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa
        ):
            sct = _screenshot_mock(width=100, height=80)
            mss_mock.return_value.__enter__.return_value = sct
            result = cu_mod._screenshot_impl("10,20,100,80", str(target))
        assert result == (
            f"截图已保存到 {target} (100x80 | 鼠标位置: (100, 200))"
        )
        assert target.exists()
        # 用整屏监控参数调用 grab
        assert sct.grab.call_args[0][0] == {
            "left": 10,
            "top": 20,
            "width": 100,
            "height": 80,
        }


class TestCuScreenshotTool:
    """cu_screenshot 工具测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.tools._screenshot_impl", return_value="mock")
    async def test_screenshot(self, mock_impl):
        """异步调用透传参数。"""
        result = await cu_mod.cu_screenshot("1,2,3,4", "/tmp/x.png")
        assert result == "mock"
        mock_impl.assert_called_once_with("1,2,3,4", "/tmp/x.png")

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.tools._screenshot_impl", return_value="mock")
    async def test_screenshot_defaults(self, mock_impl):
        """无参数调用。"""
        result = await cu_mod.cu_screenshot()
        assert result == "mock"
        mock_impl.assert_called_once_with(None, None)


class TestMouseTools:
    """鼠标工具测试。"""

    @pytest.mark.asyncio
    async def test_mouse_move(self):
        """移动鼠标。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_move(10, 20, duration=0.3)
        assert result == "鼠标已移动到 (10, 20)"
        pa.moveTo.assert_called_once_with(10, 20, duration=0.3)

    @pytest.mark.asyncio
    async def test_mouse_move_default_duration(self):
        """默认耗时。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_move(1, 2)
        pa.moveTo.assert_called_once_with(1, 2, duration=0.5)

    @pytest.mark.asyncio
    async def test_mouse_click_single(self):
        """单击。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_click(10, 20)
        assert result == "已在 (10, 20) 点击鼠标left键"
        pa.click.assert_called_once_with(10, 20, clicks=1, button="left", interval=0.1)

    @pytest.mark.asyncio
    async def test_mouse_click_double(self):
        """双击。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_click(10, 20, button="right", clicks=2)
        assert result == "已在 (10, 20) 双击鼠标right键"

    @pytest.mark.asyncio
    async def test_mouse_double_click(self):
        """双击工具。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_double_click(5, 6, button="middle")
        assert result == "已在 (5, 6) 双击鼠标middle键"
        pa.doubleClick.assert_called_once_with(5, 6, button="middle")

    @pytest.mark.asyncio
    async def test_mouse_drag(self):
        """拖拽。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_drag(0, 0, 100, 50, duration=1.0)
        assert result == "已从 (0, 0) 拖拽到 (100, 50)"
        pa.moveTo.assert_called_once_with(0, 0)
        pa.drag.assert_called_once_with(100, 50, duration=1.0, button="left")

    @pytest.mark.asyncio
    async def test_mouse_scroll_at_position(self):
        """指定位置滚动。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_scroll(3, x=5, y=6)
        assert result == "已在 (5, 6) 滚动 3 格"
        pa.scroll.assert_called_once_with(3, x=5, y=6)

    @pytest.mark.asyncio
    async def test_mouse_scroll_up(self):
        """当前位置向上滚动。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_scroll(3)
        assert result == "已向上滚动 3 格"
        pa.scroll.assert_called_once_with(3)

    @pytest.mark.asyncio
    async def test_mouse_scroll_down(self):
        """当前位置向下滚动。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_mouse_scroll(-2)
        assert result == "已向下滚动 2 格"
        pa.scroll.assert_called_once_with(-2)


class TestKeyboardTools:
    """键盘工具测试。"""

    @pytest.mark.asyncio
    async def test_keyboard_type(self):
        """输入英文。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_keyboard_type("hello", interval=0.1)
        assert result == "已输入文本:hello"
        pa.typewrite.assert_called_once_with("hello", interval=0.1)

    @pytest.mark.asyncio
    async def test_keyboard_type_unicode(self):
        """逐字符输入 Unicode。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_keyboard_type_unicode("中文")
        assert result == "已输入 Unicode 文本:中文"
        pa.write.assert_has_calls([call("中", interval=0.05), call("文", interval=0.05)])

    @pytest.mark.asyncio
    async def test_keyboard_press(self):
        """组合键。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_keyboard_press("ctrl+c")
        assert result == "已按下按键:ctrl+c"
        pa.hotkey.assert_called_once_with("ctrl", "c")

    @pytest.mark.asyncio
    async def test_keyboard_press_single(self):
        """单键。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_keyboard_press("enter")
        assert result == "已按下按键:enter"
        pa.hotkey.assert_called_once_with("enter")

    @pytest.mark.asyncio
    async def test_keyboard_press_strips_spaces(self):
        """按键名去除空白。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            await cu_mod.cu_keyboard_press("ctrl + shift + s")
        pa.hotkey.assert_called_once_with("ctrl", "shift", "s")

    @pytest.mark.asyncio
    async def test_keyboard_key_down(self):
        """按下保持。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_keyboard_key_down("shift")
        assert result == "已按下并保持:shift"
        pa.keyDown.assert_called_once_with("shift")

    @pytest.mark.asyncio
    async def test_keyboard_key_up(self):
        """释放按键。"""
        pa = MagicMock()
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_keyboard_key_up("shift")
        assert result == "已释放按键:shift"
        pa.keyUp.assert_called_once_with("shift")


class TestLocateOnScreen:
    """cu_locate_on_screen 测试。"""

    @pytest.mark.asyncio
    async def test_found(self):
        """找到图像返回中心坐标。"""
        pa = MagicMock()
        location = MagicMock()
        pa.locateOnScreen.return_value = location
        center = MagicMock()
        center.x, center.y = 100, 200
        pa.center.return_value = center
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_locate_on_screen("/img.png", confidence=0.9)
        assert result == "找到图像,中心位置:(100, 200)"
        pa.locateOnScreen.assert_called_once_with("/img.png", confidence=0.9)

    @pytest.mark.asyncio
    async def test_not_found(self):
        """未找到。"""
        pa = MagicMock()
        pa.locateOnScreen.return_value = None
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_locate_on_screen("/img.png")
        assert result == f"{TOOL_ERROR}: 未在屏幕上找到指定图像"

    @pytest.mark.asyncio
    async def test_exception(self):
        """定位异常返回错误。"""
        pa = MagicMock()
        pa.locateOnScreen.side_effect = RuntimeError("boom")
        with patch("uniclaw.tools.computer_use.tools._get_pyautogui", return_value=pa):
            result = await cu_mod.cu_locate_on_screen("/img.png")
        assert result == f"{TOOL_ERROR}: boom"


class TestToolLists:
    """工具列表测试。"""

    def test_readonly_names(self):
        """只读工具集合。"""
        assert {t.name for t in cu_mod.READONLY_TOOLS} == {
            "cu_screenshot",
            "cu_locate_on_screen",
            "cu_get_elements",
            "cu_find_element",
        }

    def test_write_names(self):
        """写入工具集合。"""
        names = {t.name for t in cu_mod.WRITE_TOOLS}
        assert names == {
            "cu_mouse_move",
            "cu_mouse_click",
            "cu_mouse_double_click",
            "cu_mouse_drag",
            "cu_mouse_scroll",
            "cu_keyboard_type",
            "cu_keyboard_type_unicode",
            "cu_keyboard_press",
            "cu_keyboard_key_down",
            "cu_keyboard_key_up",
            "cu_interact",
        }

    def test_readonly_disjoint_write(self):
        """只读与写入工具无交集。"""
        readonly = {t.name for t in cu_mod.READONLY_TOOLS}
        write = {t.name for t in cu_mod.WRITE_TOOLS}
        assert readonly.isdisjoint(write)

    def test_get_tools_enabled(self):
        """启用时返回全部。"""
        config = MagicMock()
        config.computer_use_enabled = True
        tools = cu_mod.get_tools(config)
        assert len(tools) == len(cu_mod.READONLY_TOOLS) + len(cu_mod.WRITE_TOOLS)

    def test_get_tools_disabled(self):
        """未启用时只有只读工具。"""
        config = MagicMock()
        config.computer_use_enabled = False
        tools = cu_mod.get_tools(config)
        assert tools == cu_mod.READONLY_TOOLS

    def test_get_all_tools(self):
        """无条件返回全部。"""
        tools = cu_mod.get_all_tools()
        assert len(tools) == len(cu_mod.READONLY_TOOLS) + len(cu_mod.WRITE_TOOLS)
        names = {t.name for t in tools}
        assert "cu_screenshot" in names
        assert "cu_mouse_move" in names
