"""后台进程管理测试 — 覆盖 Monitor 模型和 MonitorManager 的基本逻辑。"""

import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

from uniclaw.tools.monitor.models import Monitor, MonitorStatus


class TestMonitorStatus:
    """MonitorStatus 枚举测试。"""

    def test_status_values(self):
        """状态枚举值正确。"""
        assert MonitorStatus.RUNNING == "running"
        assert MonitorStatus.MATCHED == "matched"
        assert MonitorStatus.STOPPED == "stopped"
        assert MonitorStatus.TIMEOUT == "timeout"
        assert MonitorStatus.ERROR == "error"


class TestMonitor:
    """Monitor 模型测试。"""

    def test_initial_state(self):
        """初始状态为 RUNNING。"""
        m = Monitor(
            monitor_id="test-1",
            command="echo hello",
            pattern="",
            description="test",
            timeout=0,
        )
        assert m.id == "test-1"
        assert m.command == "echo hello"
        assert m.status == MonitorStatus.RUNNING
        assert m.process is None
        assert len(m.output_lines) == 0
        assert len(m.matched_lines) == 0

    def test_to_dict(self):
        """序列化为字典。"""
        m = Monitor(
            monitor_id="test-1",
            command="echo hello",
            pattern="hello",
            description="test desc",
            timeout=60,
        )
        d = m.to_dict()
        assert d["id"] == "test-1"
        assert d["command"] == "echo hello"
        assert d["pattern"] == "hello"
        assert d["description"] == "test desc"
        assert d["status"] == "running"
        assert "uptime_seconds" in d
        assert "output_lines" in d
        assert "matched_count" in d

    def test_output_lines_maxlen(self):
        """输出行有最大限制。"""
        m = Monitor(
            monitor_id="test-1",
            command="echo",
            pattern="",
            description="",
            timeout=0,
        )
        for i in range(1100):
            m.output_lines.append(f"line {i}")
        assert len(m.output_lines) == 1000

    def test_start_time_set(self):
        """启动时间自动设置。"""
        before = datetime.now()
        m = Monitor(
            monitor_id="test-1",
            command="echo",
            pattern="",
            description="",
            timeout=0,
        )
        after = datetime.now()
        assert before <= m.start_time <= after

    def test_cwd_optional(self):
        """cwd 参数可选。"""
        m = Monitor(
            monitor_id="test-1",
            command="echo",
            pattern="",
            description="",
            timeout=0,
        )
        assert m.cwd is None

    def test_cwd_set(self):
        """cwd 参数可设置。"""
        m = Monitor(
            monitor_id="test-1",
            command="echo",
            pattern="",
            description="",
            timeout=0,
            cwd=Path("/tmp"),
        )
        assert m.cwd == Path("/tmp")

    def test_notify_model_default(self):
        """notify_model 默认为 True。"""
        m = Monitor(
            monitor_id="test-1",
            command="echo",
            pattern="",
            description="",
            timeout=0,
        )
        assert m.notify_model is True


class TestMonitorTools:
    """Monitor 工具函数测试。"""

    @pytest.mark.asyncio
    async def test_monitor_start_empty_command(self):
        """空命令返回错误。"""
        from uniclaw.tools.monitor.tools import monitor_start

        result = await monitor_start(command="   ")
        assert "错误" in result or "不能为空" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.monitor.manager.MonitorManager.get_instance")
    async def test_monitor_list_empty(self, mock_get_instance):
        """无进程时返回提示。"""
        from uniclaw.tools.monitor.tools import monitor_list

        mock_mgr = MagicMock()
        mock_get_instance.return_value = mock_mgr
        mock_mgr.list_monitors = AsyncMock(return_value="当前没有运行中的进程。")

        result = await monitor_list()
        assert "没有" in result or "无" in result or "空" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.monitor.manager.MonitorManager.get_instance")
    async def test_monitor_list_with_processes(self, mock_get_instance):
        """有进程时返回列表。"""
        from uniclaw.tools.monitor.tools import monitor_list

        mock_mgr = MagicMock()
        mock_get_instance.return_value = mock_mgr
        mock_mgr.list_monitors = AsyncMock(
            return_value="共 1 个进程:\n  [running] my-process (ID:test-1 | 运行:5s | 输出:3行)"
        )

        result = await monitor_list()
        assert "test-1" in result
