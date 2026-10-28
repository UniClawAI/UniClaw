"""restart/resumer.py 测试 — 重启恢复调度与端口轮询(原本 0% 覆盖)。

schedule_pending_restart_resume:检测重启标志 → 清标志 → 调度后台恢复任务。
_resume_after_restart:等待端口就绪 → 加载会话 → wake_agent。
_wait_port_ready:轮询直到端口可连接或超时。

注意:resumer 的函数内使用延迟导入,因此 patch 目标是真实源模块
(uniclaw.utils.restart_flag / uniclaw.webui.ws / uniclaw.utils.wakeup),
而不是 resumer 模块属性。
"""

import asyncio
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from uniclaw.tools.restart.resumer import (
    schedule_pending_restart_resume,
    _resume_after_restart,
    _wait_port_ready,
)


class TestSchedulePendingRestartResume:
    """重启标志调度。"""

    def test_no_pending_flag_noop(self, monkeypatch):
        """无重启标志时不调度任何任务。"""
        from uniclaw.tools.restart import resumer

        monkeypatch.setattr(resumer, "asyncio", MagicMock())
        with patch(
            "uniclaw.utils.restart_flag.read_pending_restart",
            return_value=None,
        ) as mock_read:
            schedule_pending_restart_resume("127.0.0.1", 8080)
        mock_read.assert_called_once()
        resumer.asyncio.create_task.assert_not_called()

    def test_pending_without_session_id_noop(self, monkeypatch):
        """有标志但 session_id 为空时不调度。"""
        from uniclaw.tools.restart import resumer

        monkeypatch.setattr(resumer, "asyncio", MagicMock())
        with patch(
            "uniclaw.utils.restart_flag.read_pending_restart",
            return_value={"session_id": ""},
        ) as mock_read, patch(
            "uniclaw.utils.restart_flag.clear_pending_restart"
        ) as mock_clear:
            schedule_pending_restart_resume("127.0.0.1", 8080)
        mock_read.assert_called_once()
        mock_clear.assert_called_once()  # 读到即删
        resumer.asyncio.create_task.assert_not_called()

    def test_empty_dict_is_no_pending(self, monkeypatch):
        """空 dict 标志等价于无标志(falsy),不调度不清理。"""
        from uniclaw.tools.restart import resumer

        monkeypatch.setattr(resumer, "asyncio", MagicMock())
        with patch(
            "uniclaw.utils.restart_flag.read_pending_restart",
            return_value={},
        ) as mock_read, patch(
            "uniclaw.utils.restart_flag.clear_pending_restart"
        ) as mock_clear:
            schedule_pending_restart_resume("127.0.0.1", 8080)
        mock_read.assert_called_once()
        mock_clear.assert_not_called()  # 空 dict falsy, 提前 return
        resumer.asyncio.create_task.assert_not_called()

    def test_pending_schedules_resume(self, monkeypatch):
        """有 session_id 时清标志并调度后台恢复任务。"""
        from uniclaw.tools.restart import resumer

        fake_task = MagicMock()
        monkeypatch.setattr(
            resumer.asyncio,
            "create_task",
            MagicMock(return_value=fake_task),
        )
        captured = {}

        def _capture(coro):
            captured["coro"] = coro
            coro.close()  # 避免 never awaited 警告
            return fake_task

        resumer.asyncio.create_task.side_effect = _capture
        with patch(
            "uniclaw.utils.restart_flag.read_pending_restart",
            return_value={"session_id": "sess_abc"},
        ) as mock_read, patch(
            "uniclaw.utils.restart_flag.clear_pending_restart"
        ) as mock_clear:
            schedule_pending_restart_resume("127.0.0.1", 8080)

        mock_read.assert_called_once()
        mock_clear.assert_called_once()
        resumer.asyncio.create_task.assert_called_once()


class TestWaitPortReady:
    """端口就绪轮询。"""

    @pytest.mark.asyncio
    async def test_port_ready_immediately(self):
        """端口立即可连接则立即返回。"""
        with patch(
            "uniclaw.tools.restart.resumer.asyncio.open_connection",
            new_callable=AsyncMock,
        ) as mock_conn:
            writer = MagicMock()
            writer.close = MagicMock()
            writer.wait_closed = AsyncMock()
            mock_conn.return_value = (MagicMock(), writer)

            await _wait_port_ready("127.0.0.1", 8080, timeout=5)

        mock_conn.assert_awaited_once_with("127.0.0.1", 8080)
        writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_host_mapping(self):
        """0.0.0.0/::/空 host 映射为 127.0.0.1 探测。"""
        for host in ("0.0.0.0", "::", ""):
            with patch(
                "uniclaw.tools.restart.resumer.asyncio.open_connection",
                new_callable=AsyncMock,
            ) as mock_conn:
                writer = MagicMock()
                writer.wait_closed = AsyncMock()
                mock_conn.return_value = (MagicMock(), writer)
                await _wait_port_ready(host, 8080, timeout=1)
            mock_conn.assert_awaited_once_with("127.0.0.1", 8080)

    @pytest.mark.asyncio
    async def test_retries_until_ready(self):
        """端口先拒绝(OSError)后可用,应重试直到成功。"""
        conn = AsyncMock()
        writer = MagicMock()
        writer.wait_closed = AsyncMock()
        conn.side_effect = [OSError("refused"), (MagicMock(), writer)]
        with patch(
            "uniclaw.tools.restart.resumer.asyncio.open_connection",
            conn,
        ), patch(
            "uniclaw.tools.restart.resumer.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await _wait_port_ready("127.0.0.1", 8080, timeout=10)

        assert conn.await_count == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        """持续连不上时抛 TimeoutError,带目标地址信息。"""
        conn = AsyncMock(side_effect=OSError("refused"))
        with patch(
            "uniclaw.tools.restart.resumer.asyncio.open_connection",
            conn,
        ), patch(
            "uniclaw.tools.restart.resumer.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            with pytest.raises(TimeoutError) as excinfo:
                await _wait_port_ready("127.0.0.1", 8080, timeout=2)
        assert "8080" in str(excinfo.value)
        assert "127.0.0.1" in str(excinfo.value)


class TestResumeAfterRestart:
    """恢复流程:端口就绪后加载会话并唤醒 agent。"""

    @pytest.mark.asyncio
    async def test_resume_success(self):
        """正常路径:加载会话成功,wake_agent 返回 True。"""
        config = MagicMock()
        with (
            patch(
                "uniclaw.tools.restart.resumer._wait_port_ready",
                new_callable=AsyncMock,
            ) as mock_wait,
            patch(
                "uniclaw.webui.ws.get_or_load_session",
                new_callable=AsyncMock,
                return_value=config,
            ) as mock_load,
            patch(
                "uniclaw.utils.wakeup.wake_agent",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_wake,
        ):
            await _resume_after_restart("sess_abc", "127.0.0.1", 8080)

        mock_wait.assert_awaited_once_with("127.0.0.1", 8080)
        mock_load.assert_awaited_once_with("sess_abc")
        mock_wake.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resume_failure_logged(self):
        """加载会话抛异常时被捕获并记录,不向外传播。"""
        with (
            patch(
                "uniclaw.tools.restart.resumer._wait_port_ready",
                new_callable=AsyncMock,
            ),
            patch(
                "uniclaw.webui.ws.get_or_load_session",
                new_callable=AsyncMock,
                side_effect=RuntimeError("load failed"),
            ),
            patch(
                "uniclaw.tools.restart.resumer.logger.exception"
            ) as mock_log_exc,
        ):
            await _resume_after_restart("sess_abc", "127.0.0.1", 8080)

        mock_log_exc.assert_called_once()