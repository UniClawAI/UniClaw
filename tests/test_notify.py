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
        mock_win.return_value = True
        result = await push_notification(message="test", title="Test")
        assert "已发送" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock)
    async def test_windows_notification_failure(self, mock_win, mock_sys):
        """Windows 通知失败。"""
        mock_sys.platform = "win32"
        mock_win.return_value = False
        result = await push_notification(message="test")
        assert "错误" in result or "不支持" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_macos", new_callable=AsyncMock)
    async def test_macos_notification(self, mock_mac, mock_sys):
        """macOS 通知。"""
        mock_sys.platform = "darwin"
        mock_mac.return_value = True
        result = await push_notification(message="test")
        assert "已发送" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.notify.sys")
    @patch("uniclaw.tools.notify._notify_linux", new_callable=AsyncMock)
    async def test_linux_notification(self, mock_linux, mock_sys):
        """Linux 通知。"""
        mock_sys.platform = "linux"
        mock_linux.return_value = True
        result = await push_notification(message="test")
        assert "已发送" in result

    @pytest.mark.asyncio
    async def test_default_title(self):
        """默认标题为 UniClaw。"""
        with patch("uniclaw.tools.notify.sys") as mock_sys, \
             patch("uniclaw.tools.notify._notify_windows", new_callable=AsyncMock) as mock_win:
            mock_sys.platform = "win32"
            mock_win.return_value = True
            result = await push_notification(message="test")
            # 验证 _notify_windows 被调用时 title 默认为 "UniClaw"
            mock_win.assert_called_once()
            call_args = mock_win.call_args
            assert call_args[0][0] == "UniClaw"
