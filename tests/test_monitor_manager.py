"""MonitorManager 生命周期测试 — 覆盖进程监控的启动/停止/读取/输入/模式更新。"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.monitor.manager import MonitorManager
from uniclaw.tools.monitor.models import Monitor, MonitorStatus

TOOL_ERROR = "[TOOL_ERROR]"


def _make_monitor(
    monitor_id: str = "m1",
    pattern: str = "",
    description: str = "测试",
    timeout: int = 0,
) -> Monitor:
    """构造 Monitor 实例。"""
    return Monitor(monitor_id, "echo hi", pattern, description, timeout)


def _fake_create_task(coro):
    """mock create_task:关闭 coroutine 并返回 fake task,避免未等待警告。"""
    coro.close()
    return MagicMock()


class TestGetInstance:
    """get_instance 单例测试。"""

    def test_singleton(self):
        """多次调用返回同一实例。"""
        a = MonitorManager.get_instance()
        b = MonitorManager.get_instance()
        assert a is b

    def test_new_instance_starts_empty(self):
        """新实例无监控进程。"""
        mgr = MonitorManager()
        assert mgr._monitors == {}


class TestStartMonitor:
    """start_monitor 测试。"""

    @pytest.mark.asyncio
    async def test_invalid_pattern(self):
        """无效正则返回错误。"""
        mgr = MonitorManager()
        result = await mgr.start_monitor("echo hi", pattern="[", description="", timeout=0)
        assert TOOL_ERROR in result
        assert "无效的正则表达式" in result

    @pytest.mark.asyncio
    async def test_max_concurrent(self):
        """达到最大并发数返回提示。"""
        mgr = MonitorManager()
        for i in range(10):
            m = _make_monitor(monitor_id=f"m{i}")
            mgr._monitors[m.id] = m
        result = await mgr.start_monitor("echo hi", pattern="", description="", timeout=0)
        assert TOOL_ERROR in result
        assert "已达到最大并发数" in result

    @pytest.mark.asyncio
    async def test_create_process_exception(self):
        """创建子进程异常返回错误。"""
        mgr = MonitorManager()
        with patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            side_effect=OSError("boom"),
        ):
            result = await mgr.start_monitor("echo hi", pattern="", description="", timeout=0)
        assert TOOL_ERROR in result
        assert "boom" in result
        assert mgr._monitors == {}

    @pytest.mark.asyncio
    async def test_success(self):
        """启动成功。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.returncode = None
        with patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ) as mock_shell, patch(
            "uniclaw.tools.monitor.manager.asyncio.create_task",
            side_effect=_fake_create_task,
        ):
            result = await mgr.start_monitor(
                "echo hi", pattern="", description="", timeout=0
            )
        assert "进程已启动" in result
        assert "命令: echo hi" in result
        assert "匹配模式: 无" in result
        assert "仅记录输出" in result
        assert len(mgr._monitors) == 1
        mid = next(iter(mgr._monitors))
        assert mgr._monitors[mid].command == "echo hi"
        mock_shell.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_with_pattern_and_description(self):
        """带匹配模式和描述启动。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.returncode = None
        with patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ), patch(
            "uniclaw.tools.monitor.manager.asyncio.create_task",
            side_effect=_fake_create_task,
        ):
            result = await mgr.start_monitor(
                "echo hi", pattern="ERROR", description="日志监控", timeout=0
            )
        assert "日志监控" in result
        assert "匹配模式: ERROR" in result
        assert "匹配时通知模型+桌面" in result

    @pytest.mark.asyncio
    async def test_success_with_cwd(self):
        """传 cwd 启动。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.returncode = None
        with patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ) as mock_shell, patch(
            "uniclaw.tools.monitor.manager.asyncio.create_task",
            side_effect=_fake_create_task,
        ):
            await mgr.start_monitor(
                "echo hi", pattern="", description="", timeout=0, cwd=Path("/tmp")
            )
        kwargs = mock_shell.call_args.kwargs
        assert kwargs["cwd"] == str(Path("/tmp"))


class TestStopMonitor:
    """stop_monitor 测试。"""

    @pytest.mark.asyncio
    async def test_not_found(self):
        """进程不存在。"""
        mgr = MonitorManager()
        result = await mgr.stop_monitor("nope")
        assert TOOL_ERROR in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_success(self):
        """停止进程。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.returncode = None
        m.process = process
        task = MagicMock()
        task.done.return_value = False
        m.thread = task
        mgr._monitors["m1"] = m
        with patch.object(mgr, "_kill_process_tree", new=AsyncMock()) as mock_kill:
            result = await mgr.stop_monitor("m1")
        assert "进程已停止: m1" in result
        assert "m1" not in mgr._monitors
        task.cancel.assert_called_once()
        mock_kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_already_finished(self):
        """进程已结束时只取消读取任务,不杀进程。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.returncode = 0
        m.process = process
        task = MagicMock()
        task.done.return_value = True
        m.thread = task
        mgr._monitors["m1"] = m
        with patch.object(mgr, "_kill_process_tree", new=AsyncMock()) as mock_kill:
            result = await mgr.stop_monitor("m1")
        assert "进程已停止: m1" in result
        task.cancel.assert_not_called()
        mock_kill.assert_not_called()


class TestListMonitors:
    """list_monitors 测试。"""

    @pytest.mark.asyncio
    async def test_empty(self):
        """无进程。"""
        mgr = MonitorManager()
        result = await mgr.list_monitors()
        assert "当前没有运行中的进程" in result

    @pytest.mark.asyncio
    async def test_with_monitors(self):
        """有进程时列出。"""
        mgr = MonitorManager()
        mgr._monitors["m1"] = _make_monitor("m1", description="进程A")
        mgr._monitors["m2"] = _make_monitor("m2", description="进程B")
        result = await mgr.list_monitors()
        assert "共 2 个进程" in result
        assert "进程A" in result
        assert "进程B" in result


class TestGetOutput:
    """get_output 测试。"""

    @pytest.mark.asyncio
    async def test_not_found(self):
        """进程不存在。"""
        mgr = MonitorManager()
        result = await mgr.get_output("nope")
        assert TOOL_ERROR in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_no_output(self):
        """无输出。"""
        mgr = MonitorManager()
        mgr._monitors["m1"] = _make_monitor("m1")
        result = await mgr.get_output("m1")
        assert "暂无输出" in result

    @pytest.mark.asyncio
    async def test_with_output(self):
        """返回输出。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", description="进程A")
        m.output_lines.append("line1")
        m.output_lines.append("line2")
        mgr._monitors["m1"] = m
        result = await mgr.get_output("m1")
        assert "进程A" in result
        assert "line1" in result
        assert "line2" in result

    @pytest.mark.asyncio
    async def test_lines_truncated(self):
        """lines 参数截断输出。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        m.output_lines.extend([f"line{i}" for i in range(5)])
        mgr._monitors["m1"] = m
        result = await mgr.get_output("m1", lines=2)
        assert "line4" in result
        assert "line0" not in result


class TestSendInput:
    """send_input 测试。"""

    @pytest.mark.asyncio
    async def test_not_found(self):
        """进程不存在。"""
        mgr = MonitorManager()
        result = await mgr.send_input("nope", "x")
        assert TOOL_ERROR in result

    @pytest.mark.asyncio
    async def test_process_none(self):
        """无进程句柄。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        m.process = None
        mgr._monitors["m1"] = m
        result = await mgr.send_input("m1", "x")
        assert "已结束" in result

    @pytest.mark.asyncio
    async def test_process_ended(self):
        """进程已结束。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.returncode = 0
        m.process = process
        mgr._monitors["m1"] = m
        result = await mgr.send_input("m1", "x")
        assert "已结束" in result

    @pytest.mark.asyncio
    async def test_success(self):
        """发送输入成功。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.returncode = None
        stdin = MagicMock()
        stdin.drain = AsyncMock()
        process.stdin = stdin
        m.process = process
        mgr._monitors["m1"] = m
        result = await mgr.send_input("m1", "hello")
        assert "已向进程 m1 发送输入: hello" in result
        stdin.write.assert_called_once_with(b"hello\n")
        stdin.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_exception(self):
        """写入失败返回错误。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.returncode = None
        stdin = MagicMock()
        stdin.write.side_effect = OSError("closed")
        stdin.drain = AsyncMock()
        process.stdin = stdin
        m.process = process
        mgr._monitors["m1"] = m
        result = await mgr.send_input("m1", "x")
        assert TOOL_ERROR in result
        assert "closed" in result


class TestUpdatePattern:
    """update_pattern 测试。"""

    @pytest.mark.asyncio
    async def test_invalid_pattern(self):
        """无效正则。"""
        mgr = MonitorManager()
        mgr._monitors["m1"] = _make_monitor("m1")
        result = await mgr.update_pattern("m1", "[")
        assert "无效的正则表达式" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        """进程不存在。"""
        mgr = MonitorManager()
        result = await mgr.update_pattern("nope", "x")
        assert TOOL_ERROR in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_success(self):
        """更新模式成功。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="old")
        mgr._monitors["m1"] = m
        result = await mgr.update_pattern("m1", "new-pattern")
        assert "匹配模式已更新" in result
        assert "旧模式: old" in result
        assert "新模式: new-pattern" in result
        assert "匹配时通知" in result
        assert m.pattern == "new-pattern"

    @pytest.mark.asyncio
    async def test_clear_pattern(self):
        """清空模式。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="old")
        mgr._monitors["m1"] = m
        result = await mgr.update_pattern("m1", "")
        assert "新模式: 无" in result
        assert "仅记录输出" in result
        assert m.pattern == ""


class TestGetMatched:
    """get_matched 测试。"""

    @pytest.mark.asyncio
    async def test_not_found(self):
        """进程不存在。"""
        mgr = MonitorManager()
        result = await mgr.get_matched("nope")
        assert TOOL_ERROR in result

    @pytest.mark.asyncio
    async def test_no_match(self):
        """无匹配。"""
        mgr = MonitorManager()
        mgr._monitors["m1"] = _make_monitor("m1", pattern="ERROR")
        result = await mgr.get_matched("m1")
        assert "尚未匹配到任何内容" in result

    @pytest.mark.asyncio
    async def test_with_matches(self):
        """返回匹配行。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="ERROR")
        m.matched_lines.append("ERROR: a")
        m.matched_lines.append("ERROR: b")
        mgr._monitors["m1"] = m
        result = await mgr.get_matched("m1")
        assert "匹配到 2 行" in result
        assert "ERROR: a" in result
        assert "ERROR: b" in result


class TestReadOutput:
    """_read_output 测试。"""

    @pytest.mark.asyncio
    async def test_match(self):
        """匹配模式时更新状态并通知。"""
        mgr = MonitorManager()
        m = _make_monitor(pattern="ERROR")
        process = MagicMock()
        process.stdout.readline = AsyncMock(side_effect=[b"ERROR: boom\n", b""])
        process.returncode = 0
        m.process = process
        with patch.object(mgr, "_notify_match", new=AsyncMock()) as mock_notify:
            await mgr._read_output(m)
        assert m.status == MonitorStatus.MATCHED
        assert list(m.output_lines) == ["ERROR: boom"]
        assert m.matched_lines == ["ERROR: boom"]
        assert m.match_time is not None
        mock_notify.assert_awaited_once_with(m, "ERROR: boom")

    @pytest.mark.asyncio
    async def test_no_match_eof(self):
        """无匹配时进程结束转为 STOPPED。"""
        mgr = MonitorManager()
        m = _make_monitor(pattern="zzz")
        process = MagicMock()
        process.stdout.readline = AsyncMock(side_effect=[b"hello\n", b""])
        process.returncode = 0
        m.process = process
        await mgr._read_output(m)
        assert m.status == MonitorStatus.STOPPED
        assert list(m.output_lines) == ["hello"]
        assert m.matched_lines == []

    @pytest.mark.asyncio
    async def test_timeout(self):
        """超时转为 TIMEOUT。"""
        mgr = MonitorManager()
        m = _make_monitor(timeout=10)
        process = MagicMock()
        process.stdout.readline = AsyncMock(side_effect=[b"x\n", b""])
        process.returncode = 0
        m.process = process
        mock_loop = MagicMock()
        mock_loop.time.side_effect = [100.0, 999.0]
        with patch(
            "uniclaw.tools.monitor.manager.asyncio.get_event_loop",
            return_value=mock_loop,
        ):
            await mgr._read_output(m)
        assert m.status == MonitorStatus.TIMEOUT
        assert list(m.output_lines) == ["x"]

    @pytest.mark.asyncio
    async def test_exception(self):
        """读取异常转为 ERROR 并杀进程树。"""
        mgr = MonitorManager()
        m = _make_monitor()
        process = MagicMock()
        process.stdout.readline = AsyncMock(side_effect=OSError("boom"))
        process.returncode = None
        m.process = process
        with patch.object(mgr, "_kill_process_tree", new=AsyncMock()) as mock_kill:
            await mgr._read_output(m)
        assert m.status == MonitorStatus.ERROR
        mock_kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled(self):
        """取消读取任务时不改变状态。"""
        mgr = MonitorManager()
        m = _make_monitor()
        gate = asyncio.Event()
        process = MagicMock()
        process.stdout.readline = AsyncMock(side_effect=gate.wait)
        process.returncode = None
        m.process = process
        with patch.object(mgr, "_kill_process_tree", new=AsyncMock()) as mock_kill:
            task = asyncio.create_task(mgr._read_output(m))
            await asyncio.sleep(0)
            task.cancel()
            await task
        assert m.status == MonitorStatus.RUNNING
        mock_kill.assert_awaited_once()


class TestNotifyMatch:
    """_notify_match 测试。"""

    @pytest.mark.asyncio
    async def test_notify_model(self):
        """通知用户并唤醒模型。"""
        mgr = MonitorManager()
        m = _make_monitor(pattern="ERROR", description="日志")
        m._config = MagicMock()
        with patch(
            "uniclaw.tools.notify.push_notification",
            new_callable=AsyncMock,
        ) as mock_push, patch(
            "uniclaw.utils.wakeup.wake_agent",
            new_callable=AsyncMock,
        ) as mock_wake:
            await mgr._notify_match(m, "ERROR: x")
        mock_push.assert_awaited_once()
        mock_wake.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_config(self):
        """无 config 时不唤醒模型,仅通知用户。"""
        mgr = MonitorManager()
        m = _make_monitor(pattern="ERROR")
        m._config = None
        with patch(
            "uniclaw.tools.notify.push_notification",
            new_callable=AsyncMock,
        ) as mock_push, patch(
            "uniclaw.utils.wakeup.wake_agent",
            new_callable=AsyncMock,
        ) as mock_wake:
            await mgr._notify_match(m, "ERROR: x")
        mock_push.assert_awaited_once()
        mock_wake.assert_not_called()


class TestKillProcessTree:
    """_kill_process_tree 测试。"""

    @pytest.mark.asyncio
    async def test_windows(self):
        """Windows 使用 taskkill。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.pid = 1234
        proc = MagicMock()
        proc.wait = AsyncMock()
        with patch(
            "uniclaw.tools.monitor.manager.os.name", "nt"
        ), patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=proc,
        ) as mock_exec:
            await mgr._kill_process_tree(process)
        args = mock_exec.call_args[0]
        assert "taskkill" in args
        assert "/T" in args
        assert "1234" in args

    @pytest.mark.asyncio
    async def test_unix(self):
        """Unix 发送 SIGKILL 到进程组。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.pid = 1234
        with patch(
            "uniclaw.tools.monitor.manager.os.name", "posix"
        ), patch(
            "uniclaw.tools.monitor.manager.signal.SIGKILL", 9, create=True
        ), patch(
            "uniclaw.tools.monitor.manager.os.getpgid", return_value=999, create=True
        ), patch(
            "uniclaw.tools.monitor.manager.os.killpg", create=True
        ) as mock_killpg:
            await mgr._kill_process_tree(process)
        mock_killpg.assert_called_once_with(999, 9)

    @pytest.mark.asyncio
    async def test_kill_fallback(self):
        """killpg 失败时直接 kill 进程。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.pid = 1234
        with patch(
            "uniclaw.tools.monitor.manager.os.name", "posix"
        ), patch(
            "uniclaw.tools.monitor.manager.signal.SIGKILL", 9, create=True
        ), patch(
            "uniclaw.tools.monitor.manager.os.getpgid", return_value=999, create=True
        ), patch(
            "uniclaw.tools.monitor.manager.os.killpg",
            side_effect=OSError("denied"),
            create=True,
        ):
            await mgr._kill_process_tree(process)
        process.kill.assert_called_once()
