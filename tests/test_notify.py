"""桌面通知测试 — 覆盖 push_notification 参数校验和平台分发。"""

import pytest
from unittest.mock import patch, AsyncMock

from uniclaw.tools.notify import push_notification, get_tools, get_all_tools


class TestNotifyRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_one(self):
        """返回 1 个工具。"""
        result = get_tools()
        assert len(result) == 1

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())


class TestPushNotification:
    """push_notification 测试。"""

    @pytest.mark.asyncio
    async def test_empty_message_returns_error(self):
        """空消息返回错误。"""
        result = await push_notification(message="")
        assert "错误" in result or "不能为空" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock)
    async def test_windows_notification_success(self, mock_win, mock_sys):
        """Windows 通知成功。"""
        mock_sys.platform = "win32"
        mock_win.return_value = ""
        result = await push_notification(message="test", title="Test")
        assert "已发送" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock)
    async def test_windows_notification_failure(self, mock_win, mock_sys):
        """Windows 通知失败时回传真实原因。"""
        mock_sys.platform = "win32"
        mock_win.return_value = "PowerShell 退出码 1: Toast XML 无效"
        result = await push_notification(message="test")
        assert "失败" in result
        assert "Toast XML 无效" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock)
    async def test_failure_never_blames_unsupported_platform(self, mock_win, mock_sys):
        """失败不能谎报"平台不支持" —— 三个平台都已实现,失败是环境或内容问题。

        静默假结果 bug 的回归测试: 旧实现把任何失败都归因成"当前平台不支持",
        LLM 据此认为无解而放弃重试,真实原因被丢进 print。
        """
        mock_sys.platform = "win32"
        mock_win.return_value = "调用 PowerShell 异常: 系统找不到指定的文件"
        result = await push_notification(message="test")
        assert "不支持" not in result
        assert "系统找不到指定的文件" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_macos", new_callable=AsyncMock)
    async def test_macos_notification(self, mock_mac, mock_sys):
        """macOS 通知。"""
        mock_sys.platform = "darwin"
        mock_mac.return_value = ""
        result = await push_notification(message="test")
        assert "已发送" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_linux", new_callable=AsyncMock)
    async def test_linux_notification(self, mock_linux, mock_sys):
        """Linux 通知。"""
        mock_sys.platform = "linux"
        mock_linux.return_value = ""
        result = await push_notification(message="test")
        assert "已发送" in result

    @pytest.mark.asyncio
    async def test_default_title(self):
        """默认标题为 UniClaw。"""
        with patch("uniclaw.tools.notify.sys") as mock_sys, \
             patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock) as mock_win:
            mock_sys.platform = "win32"
            mock_win.return_value = ""
            result = await push_notification(message="test")
            # 验证 _notify_windows 被调用时 title 默认为 "UniClaw"
            mock_win.assert_called_once()
            call_args = mock_win.call_args
            assert call_args[0][0] == "UniClaw"


class TestPlatformFailureReasonPassthrough:
    """平台助手返回失败原因串时,必须原样透出到 TOOL_ERROR,不能静默成功或笼统报错。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock)
    async def test_windows_reason_string_surfaces(self, mock_win, mock_sys):
        """Windows 助手返回原因串 → 报错包含该原因。"""
        mock_sys.platform = "win32"
        mock_win.return_value = "PowerShell 退出码 1: Toast XML 无效"
        result = await push_notification(message="test")
        assert "Toast XML 无效" in result
        assert "已发送" not in result  # 不能静默成功

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_macos", new_callable=AsyncMock)
    async def test_macos_reason_string_surfaces(self, mock_mac, mock_sys):
        """macOS 助手返回原因串 → 报错包含该原因。"""
        mock_sys.platform = "darwin"
        mock_mac.return_value = "osascript 退出码 1: 用户拒绝了通知权限"
        result = await push_notification(message="test")
        assert "用户拒绝了通知权限" in result
        assert "已发送" not in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_linux", new_callable=AsyncMock)
    async def test_linux_reason_string_surfaces(self, mock_linux, mock_sys):
        """Linux 助手返回原因串 → 报错包含该原因。"""
        mock_sys.platform = "linux"
        mock_linux.return_value = "notify-send 退出码 1: 无法打开显示"
        result = await push_notification(message="test")
        assert "无法打开显示" in result
        assert "已发送" not in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock)
    async def test_platform_exception_surfaces(self, mock_win, mock_sys):
        """平台助手直接抛异常 → 报错包含异常原因,不能静默成功。"""
        mock_sys.platform = "win32"
        mock_win.side_effect = RuntimeError("toast backend crashed")
        result = await push_notification(message="test")
        assert "toast backend crashed" in result
        assert "已发送" not in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock)
    async def test_failure_reason_not_replaced_by_generic(self, mock_win, mock_sys):
        """失败原因不能被笼统的"发送失败"覆盖 — 必须保留具体原因串。"""
        mock_sys.platform = "win32"
        mock_win.return_value = "找不到 powershell 可执行文件"
        result = await push_notification(message="test")
        # 具体原因必须出现,而不是只剩"发送失败"四个字
        assert "找不到 powershell 可执行文件" in result
