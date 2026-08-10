"""Windows UI Automation 后端测试 — 覆盖 automation_win.py 的核心逻辑。

说明:
- automation_win 基于 uiautomation 库(auto),测试通过 patch.object(aw.auto, ...) 隔离真实 UI。
- 遍历逻辑依赖 auto.WalkControl 返回 (control, depth) 迭代,用 iter([...]) 模拟。
- 控件用 MagicMock 构造,GetParentControl 默认返回 None,避免窗口追溯死循环。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.tools.computer_use import automation_win as aw
from uniclaw.utils.constants import TOOL_ERROR


def _make_ctrl(
    name="",
    aid="",
    ctype=None,
    rect=(10, 20, 100, 30),
    parent=None,
    focused=False,
):
    """构造控件 mock。

    rect 为 (left, top, width, height) 元组;None 表示无位置。
    """
    ctrl = MagicMock()
    ctrl.Name = name
    ctrl.AutomationId = aid
    ctrl.ControlType = ctype if ctype is not None else aw.auto.ControlType.ButtonControl
    ctrl.HasKeyboardFocus = focused
    ctrl.IsEnabled = True
    ctrl.IsOffscreen = False
    ctrl.ClassName = ""
    if rect:
        r = MagicMock()
        r.left, r.top = rect[0], rect[1]
        r.right, r.bottom = rect[0] + rect[2], rect[1] + rect[3]
        r.width.return_value = rect[2]
        r.height.return_value = rect[3]
        ctrl.BoundingRectangle = r
    else:
        ctrl.BoundingRectangle = None
    ctrl.GetParentControl.return_value = parent
    return ctrl


class TestGetTypeName:
    """_get_type_name 测试。"""

    def test_known_type(self):
        """预定义映射。"""
        assert aw._get_type_name(aw.auto.ControlType.ButtonControl) == "Button"
        assert aw._get_type_name(aw.auto.ControlType.EditControl) == "Edit"
        assert aw._get_type_name(aw.auto.ControlType.WindowControl) == "Window"

    def test_unknown_fallback(self):
        """未知类型回退 Unknown 格式。"""
        assert aw._get_type_name(9999) == "Unknown(9999)"

    def test_dynamic_suffix_stripped(self):
        """动态解析去 Control 后缀。"""
        with patch.dict(aw.auto.ControlTypeNames, {9999: "FooControl"}):
            assert aw._get_type_name(9999) == "Foo"

    def test_dynamic_no_suffix(self):
        """动态解析无后缀原样返回。"""
        with patch.dict(aw.auto.ControlTypeNames, {9999: "Foo"}):
            assert aw._get_type_name(9999) == "Foo"


class TestGetStates:
    """_get_states 测试。"""

    def test_all_states(self):
        """全部状态。"""
        ctrl = MagicMock()
        ctrl.HasKeyboardFocus = True
        ctrl.IsEnabled = False
        ctrl.IsOffscreen = True
        assert aw._get_states(ctrl) == ["focused", "disabled", "offscreen"]

    def test_no_states(self):
        """无状态。"""
        ctrl = MagicMock()
        ctrl.HasKeyboardFocus = False
        ctrl.IsEnabled = True
        ctrl.IsOffscreen = False
        assert aw._get_states(ctrl) == []

    def test_attr_exception_skipped(self):
        """属性访问异常跳过对应状态。"""

        class C:
            @property
            def HasKeyboardFocus(self):
                raise RuntimeError("x")

            @property
            def IsEnabled(self):
                return True

            @property
            def IsOffscreen(self):
                raise RuntimeError("y")

        assert aw._get_states(C()) == []


class TestGetNativeHandle:
    """_get_native_handle 测试。"""

    def test_no_parent(self):
        """无父控件返回 0。"""
        ctrl = MagicMock()
        ctrl.GetParentControl.return_value = None
        assert aw._get_native_handle(ctrl) == 0

    def test_window_handle(self):
        """沿父链找到窗口句柄。"""
        win = MagicMock()
        win.ControlType = aw.auto.ControlType.WindowControl
        win.NativeWindowHandle = 12345
        win.GetParentControl.return_value = None
        btn = MagicMock()
        btn.ControlType = aw.auto.ControlType.ButtonControl
        btn.GetParentControl.return_value = win
        assert aw._get_native_handle(btn) == 12345

    def test_outer_window_wins(self):
        """最外层窗口句柄优先。"""
        outer = MagicMock()
        outer.ControlType = aw.auto.ControlType.WindowControl
        outer.NativeWindowHandle = 111
        outer.GetParentControl.return_value = None
        inner = MagicMock()
        inner.ControlType = aw.auto.ControlType.WindowControl
        inner.NativeWindowHandle = 222
        inner.GetParentControl.return_value = outer
        btn = MagicMock()
        btn.ControlType = aw.auto.ControlType.ButtonControl
        btn.GetParentControl.return_value = inner
        assert aw._get_native_handle(btn) == 111


class TestResolveControlType:
    """_resolve_control_type 测试。"""

    def test_known(self):
        """常见类型。"""
        assert aw._resolve_control_type("button") == aw.auto.ControlType.ButtonControl
        assert aw._resolve_control_type("edit") == aw.auto.ControlType.EditControl

    def test_case_insensitive(self):
        """大小写不敏感。"""
        assert aw._resolve_control_type("Button") == aw.auto.ControlType.ButtonControl

    def test_unknown(self):
        """未知类型返回 None。"""
        assert aw._resolve_control_type("nope") is None


class TestGetInteractiveElements:
    """get_interactive_elements 测试。"""

    def test_walk_all(self):
        """完整遍历输出格式化元素。"""
        btn = _make_ctrl(name="确定", rect=(10, 20, 100, 30))
        edit = _make_ctrl(
            name="", aid="txt1", ctype=aw.auto.ControlType.EditControl, rect=(0, 0, 200, 50)
        )
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "WalkControl", return_value=iter([(btn, 1), (edit, 1)])
        ):
            result = aw.get_interactive_elements()
        assert "找到 2 个可交互元素" in result
        assert "[0] Button" in result
        assert 'name="确定"' in result
        assert "bounds=(10,20,100x30)" in result
        assert "[1] Edit" in result
        assert 'automation_id="txt1"' in result
        assert "bounds=(0,0,200x50)" in result

    def test_empty(self):
        """无元素。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "WalkControl", return_value=iter([])
        ):
            result = aw.get_interactive_elements()
        assert result == "未找到可交互元素"

    def test_skip_empty_controls(self):
        """跳过完全空的控件。"""
        empty = _make_ctrl(rect=None)
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "WalkControl", return_value=iter([(empty, 1)])
        ):
            result = aw.get_interactive_elements()
        assert result == "未找到可交互元素"

    def test_states_included(self):
        """输出包含状态。"""
        ctrl = _make_ctrl(name="确定", focused=True)
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "WalkControl", return_value=iter([(ctrl, 1)])
        ):
            result = aw.get_interactive_elements()
        assert "states=[focused]" in result

    def test_window_grouping(self):
        """按窗口分组。"""
        win = _make_ctrl(name="记事本", ctype=aw.auto.ControlType.WindowControl)
        btn = _make_ctrl(name="确定", parent=win)
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "WalkControl", return_value=iter([(btn, 1), (win, 0)])
        ):
            result = aw.get_interactive_elements()
        assert "── 记事本 ──" in result
        assert "── (未知窗口) ──" not in result

    def test_scope_found(self):
        """限定范围查找成功。"""
        scope = MagicMock()
        btn = _make_ctrl(name="确定")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=scope
        ), patch.object(aw.auto, "WalkControl", return_value=iter([(btn, 1)])) as walk_mock:
            result = aw.get_interactive_elements(scope_name="窗口标题", max_depth=5)
            assert "找到 1 个可交互元素" in result
            walk_mock.assert_called_once_with(scope, maxDepth=5, includeTop=False)

    def test_scope_not_found(self):
        """范围未找到。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=None
        ):
            result = aw.get_interactive_elements(scope_name="窗口")
        assert f"{TOOL_ERROR}: 未找到名为 '窗口' 的元素" == result

    def test_scope_exception(self):
        """范围查找异常。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", side_effect=RuntimeError("boom")
        ):
            result = aw.get_interactive_elements(scope_name="窗口")
        assert f"{TOOL_ERROR}: 查找窗口失败: boom" == result

    def test_walk_exception(self):
        """遍历异常。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "WalkControl", side_effect=RuntimeError("boom")
        ):
            result = aw.get_interactive_elements()
        assert f"{TOOL_ERROR}: 遍历 UI 树失败: boom" == result

    def test_max_elements_cap(self):
        """超过 MAX_ELEMENTS 截断。"""
        controls = [_make_ctrl(name=f"el{i}") for i in range(305)]
        pairs = ((c, i) for i, c in enumerate(controls))
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "WalkControl", return_value=pairs
        ):
            result = aw.get_interactive_elements()
        assert "找到 300 个可交互元素" in result


class TestFindElement:
    """find_element 测试。"""

    def test_no_criteria(self):
        """无任何条件。"""
        result = aw.find_element()
        assert f"{TOOL_ERROR}: 必须提供 name 或 automation_id" == result

    def test_success(self):
        """查找成功返回详情。"""
        ctrl = _make_ctrl(name="确定", aid="btn1")
        ctrl.GetChildren.return_value = []
        ctrl.GetPattern.return_value = None
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = aw.find_element(name="确定")
        assert "类型: Button" in result
        assert '名称: "确定"' in result
        assert "AutomationId: btn1" in result
        assert "子元素数: 0" in result

    def test_not_found(self):
        """未找到。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=None
        ):
            result = aw.find_element(name="x")
        assert f"{TOOL_ERROR}: 未找到匹配的元素" == result

    def test_exception(self):
        """查找异常。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", side_effect=RuntimeError("boom")
        ):
            result = aw.find_element(automation_id="x")
        assert f"{TOOL_ERROR}: 查找失败: boom" == result

    def test_control_type_filter(self):
        """带控件类型过滤。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()) as root_mock, patch.object(
            aw.auto, "FindControl", return_value=MagicMock()
        ) as find_mock:
            aw.find_element(name="x", control_type="Button")
            find_mock.assert_called_once()
            args = find_mock.call_args[0]
            assert args[0] is root_mock.return_value
            assert callable(args[1])


class TestInteract:
    """interact 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_click(self, mock_vis):
        """点击。"""
        ctrl = _make_ctrl(name="确定")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="click")
        assert result == '已点击元素: Button "确定"'
        ctrl.Click.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_click_blocked(self, mock_vis):
        """点击被遮挡拦截。"""
        mock_vis.return_value = "错误:元素被遮挡"
        ctrl = _make_ctrl(name="确定")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="click")
        assert result == "错误:元素被遮挡"
        ctrl.Click.assert_not_called()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_invoke(self, mock_vis):
        """invoke 模式。"""
        ctrl = _make_ctrl(name="确定")
        pattern = MagicMock()
        ctrl.GetPattern.return_value = pattern
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="invoke")
        assert "已调用元素" in result
        pattern.Invoke.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_invoke_fallback(self, mock_vis):
        """InvokePattern 不可用降级为点击。"""
        ctrl = _make_ctrl(name="确定")
        ctrl.GetPattern.side_effect = RuntimeError("no pattern")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="invoke")
        assert "已点击元素(Invoke 不支持)" in result
        ctrl.Click.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_focus(self, mock_vis):
        """聚焦。"""
        ctrl = _make_ctrl(name="确定")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="focus")
        assert "已聚焦元素" in result
        ctrl.SetFocus.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_type_no_text(self, mock_vis):
        """type 无文本。"""
        ctrl = _make_ctrl(name="确定")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="type")
        assert f"{TOOL_ERROR}: action='type' 时必须提供 type_text" == result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_type_value_pattern(self, mock_vis):
        """type 通过 ValuePattern。"""
        ctrl = _make_ctrl(name="确定")
        vp = MagicMock()
        ctrl.GetPattern.return_value = vp
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="type", type_text="hello")
        assert "已通过 ValuePattern 输入文本到" in result
        vp.SetValue.assert_called_once_with("hello")

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_type_sendkeys_fallback(self, mock_vis):
        """ValuePattern 不可用降级 SendKeys。"""
        ctrl = _make_ctrl(name="确定")
        ctrl.GetPattern.side_effect = RuntimeError("no pattern")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="type", type_text="hi")
        assert "已通过 SendKeys 输入文本到" in result
        ctrl.SendKeys.assert_called_once_with("hi")

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_unsupported_action(self, mock_vis):
        """不支持的操作。"""
        ctrl = _make_ctrl(name="确定")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="hover")
        assert "不支持的操作 'hover'" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_action_case_insensitive(self, mock_vis):
        """action 大小写不敏感。"""
        ctrl = _make_ctrl(name="确定")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="CLICK")
        assert "已点击元素" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_operation_exception(self, mock_vis):
        """操作异常。"""
        ctrl = _make_ctrl(name="确定")
        ctrl.Click.side_effect = RuntimeError("boom")
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=ctrl
        ):
            result = await aw.interact(name="确定", action="click")
        assert f"{TOOL_ERROR}: 操作失败: boom" == result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._ensure_visible", new_callable=AsyncMock, return_value=None)
    async def test_not_found(self, mock_vis):
        """UIA 未找到。"""
        with patch.object(aw.auto, "GetRootControl", return_value=MagicMock()), patch.object(
            aw.auto, "FindControl", return_value=None
        ):
            result = await aw.interact(name="x", action="click")
        assert f"{TOOL_ERROR}: 未找到匹配的元素" == result


class TestCtypesHelpers:
    """ctypes 窗口辅助函数测试。"""

    @patch("ctypes.windll.user32")
    def test_top_level_hwnd(self, user32):
        """沿父链走到顶层。"""
        user32.GetParent.side_effect = [100, 200, 0]
        assert aw._get_top_level_hwnd(50) == 200

    @patch("ctypes.windll.user32")
    def test_top_level_hwnd_root(self, user32):
        """本身就是顶层。"""
        user32.GetParent.return_value = 0
        assert aw._get_top_level_hwnd(50) == 50

    @patch("ctypes.windll.user32")
    def test_activate_foreground(self, user32):
        """已是前台窗口跳过。"""
        user32.GetForegroundWindow.return_value = 100
        aw._activate_window(100)
        user32.SetForegroundWindow.assert_not_called()

    @patch("ctypes.windll.user32")
    def test_activate_minimized(self, user32):
        """最小化窗口先恢复再激活。"""
        user32.GetForegroundWindow.return_value = 0
        user32.IsIconic.return_value = True
        aw._activate_window(100)
        user32.ShowWindow.assert_called_once_with(100, 9)
        user32.SetForegroundWindow.assert_called_once_with(100)

    @patch("ctypes.create_unicode_buffer")
    @patch("ctypes.windll.user32")
    def test_window_title(self, user32, mock_buf):
        """获取窗口标题。"""
        buf = MagicMock()
        buf.value = "标题"
        mock_buf.return_value = buf
        assert aw._get_window_title(100) == "标题"
        user32.GetWindowTextW.assert_called_once_with(100, buf, 256)


class TestOccluded:
    """_check_occluded 测试。"""

    @pytest.mark.asyncio
    async def test_invalid_rect(self):
        """无效矩形跳过。"""
        ctrl = MagicMock()
        r = MagicMock()
        r.width.return_value = 0
        r.height.return_value = 10
        ctrl.BoundingRectangle = r
        assert await aw._check_occluded(ctrl) is None

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._get_native_handle", return_value=0)
    async def test_no_handle(self, mock_h):
        """无法获取句柄放行。"""
        ctrl = MagicMock()
        r = MagicMock()
        r.width.return_value = 10
        r.height.return_value = 10
        r.left = r.right = r.top = r.bottom = 0
        ctrl.BoundingRectangle = r
        assert await aw._check_occluded(ctrl) is None

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win._get_native_handle", return_value=0)
    async def test_rect_access_exception(self, mock_h):
        """矩形访问异常返回 None。"""
        ctrl = MagicMock()
        ctrl.BoundingRectangle.side_effect = RuntimeError("boom")
        assert await aw._check_occluded(ctrl) is None


class TestCuToolsWrapper:
    """@tool 包装的 cu_* 工具测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win.get_interactive_elements", return_value="mock_list")
    async def test_cu_get_elements(self, mock_ge):
        """获取元素列表。"""
        result = await aw.cu_get_elements(max_depth=1)
        assert result == "mock_list"
        mock_ge.assert_called_once_with(1, None)

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win.find_element", return_value="mock_detail")
    async def test_cu_find_element(self, mock_fe):
        """查找元素。"""
        result = await aw.cu_find_element(name="确定")
        assert result == "mock_detail"
        mock_fe.assert_called_once_with("确定", None, None)

    @pytest.mark.asyncio
    @patch("uniclaw.tools.computer_use.automation_win.interact", new_callable=AsyncMock, return_value="mock_interact")
    async def test_cu_interact(self, mock_int):
        """交互元素。"""
        result = await aw.cu_interact(name="确定", action="click", type_text=None)
        assert result == "mock_interact"
        mock_int.assert_awaited_once_with("确定", None, None, "click", None)
