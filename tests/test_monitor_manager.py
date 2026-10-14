"""MonitorManager 生命周期测试 — 覆盖进程监控的启动/停止/读取/输入/模式更新。"""

import asyncio
import sys
from datetime import datetime
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


@pytest.fixture(autouse=True)
def notify_mocks():
    """屏蔽桌面通知/模型唤醒/ui 输出副作用,单测只验证监控逻辑本身。"""
    with patch(
        "uniclaw.tools.notify.push_notification",
        new_callable=AsyncMock,
    ) as mock_push, patch(
        "uniclaw.utils.wakeup.wake_agent",
        new_callable=AsyncMock,
    ) as mock_wake, patch(
        "uniclaw.tools.monitor.manager.warn", new_callable=AsyncMock
    ), patch(
        "uniclaw.tools.monitor.manager.err", new_callable=AsyncMock
    ):
        yield mock_push, mock_wake


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
        assert "结束时自动唤醒" in result
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
        """进程不存在: 未找到进程 ID。"""
        mgr = MonitorManager()
        result = await mgr.stop_monitor("nope")
        assert TOOL_ERROR in result
        assert "未找到进程 ID: nope" in result

    @pytest.mark.asyncio
    async def test_success(self):
        """停止运行中的进程。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.returncode = None
        m.process = process
        task = MagicMock()
        task.done.return_value = False
        m.stdout_thread = task
        m.stderr_thread = None
        mgr._monitors["m1"] = m
        with patch.object(mgr, "_kill_process_tree", new=AsyncMock()) as mock_kill:
            result = await mgr.stop_monitor("m1")
        assert "进程已停止: m1" in result
        assert "m1" not in mgr._monitors
        task.cancel.assert_called_once()
        mock_kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_already_finished(self):
        """进程已自然退出: 报退出码并清理, 不伪装成"刚停掉"。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.returncode = 0
        m.process = process
        task = MagicMock()
        task.done.return_value = True
        m.stdout_thread = task
        m.stderr_thread = None
        mgr._monitors["m1"] = m
        with patch.object(mgr, "_kill_process_tree", new=AsyncMock()) as mock_kill:
            result = await mgr.stop_monitor("m1")
        assert "进程已退出" in result
        assert "exit=0" in result
        assert "记录已清理" in result
        assert "m1" not in mgr._monitors
        task.cancel.assert_not_called()
        mock_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_natural_exit_nonzero_reported(self):
        """自然退出(非零退出码)stop 时带上退出码。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        m.status = MonitorStatus.STOPPED
        m.exit_code = 42
        process = MagicMock()
        process.returncode = 42
        m.process = process
        mgr._monitors["m1"] = m
        result = await mgr.stop_monitor("m1")
        assert "进程已退出" in result
        assert "exit=42" in result
        assert "记录已清理" in result

    @pytest.mark.asyncio
    async def test_repeat_stop_no_success_then_fail_flip(self):
        """重复 stop: 首次报告已退出/已停止, 再次统一为未找到, 无语义翻转。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        m.status = MonitorStatus.STOPPED
        m.exit_code = 7
        mgr._monitors["m1"] = m

        first = await mgr.stop_monitor("m1")
        assert "进程已退出" in first
        assert "exit=7" in first

        second = await mgr.stop_monitor("m1")
        assert TOOL_ERROR in second
        assert "未找到进程 ID: m1" in second
        # 不得出现"进程已停止"这种像刚停掉的措辞
        assert "进程已停止" not in first
        assert "进程已停止" not in second


class TestExitCodeVisibility:
    """退出码全程可见 — list / output / 模型字段(任务 1)。"""

    @pytest.mark.asyncio
    async def test_pipe_path_captures_nonzero_exit_code(self):
        """管道路径: 非零退出码写入 Monitor 状态。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=[b"done\n", b""])
        process.returncode = 42
        m.process = process
        await mgr._read_output(m)
        assert m.status == MonitorStatus.STOPPED
        assert m.exit_code == 42

    @pytest.mark.asyncio
    async def test_pipe_path_captures_zero_exit_code(self):
        """管道路径: 正常退出 exit=0 也被记录。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=[b"ok\n", b""])
        process.returncode = 0
        m.process = process
        await mgr._read_output(m)
        assert m.exit_code == 0

    @pytest.mark.asyncio
    async def test_list_shows_nonzero_exit_code(self):
        """monitor_list: 停止条目显示 stopped(exit=42)。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", description="崩溃进程")
        m.status = MonitorStatus.STOPPED
        m.exit_code = 42
        mgr._monitors["m1"] = m
        result = await mgr.list_monitors()
        assert "stopped(exit=42)" in result

    @pytest.mark.asyncio
    async def test_list_shows_zero_exit_code(self):
        """monitor_list: 正常退出也标 exit=0。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", description="正常进程")
        m.status = MonitorStatus.STOPPED
        m.exit_code = 0
        mgr._monitors["m1"] = m
        result = await mgr.list_monitors()
        assert "stopped(exit=0)" in result

    @pytest.mark.asyncio
    async def test_output_footer_reports_nonzero_exit(self):
        """monitor_output: 末尾标注退出码与非正常结束。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", description="崩溃进程")
        m.output_lines.append("stack trace...")
        m.status = MonitorStatus.STOPPED
        m.exit_code = 42
        mgr._monitors["m1"] = m
        result = await mgr.get_output("m1")
        assert "stack trace..." in result
        assert "exit=42" in result
        assert "非正常结束" in result

    @pytest.mark.asyncio
    async def test_output_footer_reports_zero_exit(self):
        """monitor_output: exit=0 标正常结束。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        m.output_lines.append("bye")
        m.status = MonitorStatus.STOPPED
        m.exit_code = 0
        mgr._monitors["m1"] = m
        result = await mgr.get_output("m1")
        assert "exit=0" in result
        assert "正常结束" in result

    @pytest.mark.asyncio
    async def test_output_footer_absent_while_running(self):
        """运行中不追加退出标注。"""
        mgr = MonitorManager()
        m = _make_monitor("m1")
        m.output_lines.append("hello")
        process = MagicMock()
        process.returncode = None
        m.process = process
        mgr._monitors["m1"] = m
        result = await mgr.get_output("m1")
        assert "进程结束" not in result
        assert "exit=" not in result

    @pytest.mark.asyncio
    async def test_to_dict_includes_exit_code(self):
        """to_dict 暴露 exit_code 与带 exit 的状态标签。"""
        m = _make_monitor("m1")
        m.status = MonitorStatus.STOPPED
        m.exit_code = 3
        d = m.to_dict()
        assert d["exit_code"] == 3
        assert d["status"] == "stopped(exit=3)"
        assert d["raw_status"] == "stopped"

    @pytest.mark.asyncio
    async def test_real_process_nonzero_exit_visible(self):
        """真实子进程 sys.exit(42): list 与 output 均可见退出码。"""
        mgr = MonitorManager()
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(42)"'
        result = await mgr.start_monitor(
            cmd, pattern="", description="exit42", timeout=15
        )
        assert "进程已启动" in result, result
        monitor_id = result.split("ID: ")[1].splitlines()[0].strip()
        m = mgr._monitors[monitor_id]
        try:
            for _ in range(200):
                if m.status != MonitorStatus.RUNNING:
                    break
                await asyncio.sleep(0.05)
            assert m.exit_code == 42, f"exit_code={m.exit_code} status={m.status}"

            listed = await mgr.list_monitors()
            assert "exit=42" in listed

            out = await mgr.get_output(monitor_id)
            assert "exit=42" in out
            assert "非正常结束" in out
        finally:
            await mgr.stop_monitor(monitor_id)

    @pytest.mark.asyncio
    async def test_real_process_zero_exit_visible(self):
        """真实子进程 exit 0: list/output 标 exit=0 与正常结束。"""
        mgr = MonitorManager()
        cmd = f'"{sys.executable}" -c "import sys; sys.exit(0)"'
        result = await mgr.start_monitor(
            cmd, pattern="", description="exit0", timeout=15
        )
        assert "进程已启动" in result, result
        monitor_id = result.split("ID: ")[1].splitlines()[0].strip()
        m = mgr._monitors[monitor_id]
        try:
            for _ in range(200):
                if m.status != MonitorStatus.RUNNING:
                    break
                await asyncio.sleep(0.05)
            assert m.exit_code == 0, f"exit_code={m.exit_code} status={m.status}"

            listed = await mgr.list_monitors()
            assert "exit=0" in listed

            out = await mgr.get_output(monitor_id)
            assert "exit=0" in out
            assert "正常结束" in out
        finally:
            await mgr.stop_monitor(monitor_id)

    def test_conpty_path_captures_exit_code(self):
        """ConPTY 路径: PtySession 退出时写入退出码。"""
        import threading

        from uniclaw.tools.monitor.pty_session import PtySession, ScreenBuffer, VtLineParser

        m = _make_monitor("m1")
        session = PtySession.__new__(PtySession)
        session.monitor = m
        session.loop = MagicMock()
        session.loop.call_soon_threadsafe.side_effect = (
            lambda _cb, coro: coro.close()
        )
        session.on_notify = lambda *_: None
        session._last_idle_snapshot = None
        session._parser = VtLineParser(session._commit_line)
        session._screen = ScreenBuffer(5, 20)
        session.exited = threading.Event()
        session._raw_queue = MagicMock()
        # 模拟读线程 EOF 哨兵
        session._raw_queue.get.side_effect = [None]
        session.proc = MagicMock()
        session.proc.exitstatus = 7

        session._process_loop()

        assert m.exit_code == 7
        assert m.status == MonitorStatus.STOPPED
        assert session.exited.is_set()


class TestVisibleDegrade:
    """visible=True 环境不支持 ConPTY 时降级, 不假报弹窗(任务 2)。"""

    @pytest.mark.asyncio
    async def test_visible_without_winpty_degrades_to_pipe(self):
        """缺 pywinpty: 降级管道, 说明原因, 不宣称 ConPTY 窗口。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.returncode = None
        with patch.object(
            MonitorManager, "_winpty_available", staticmethod(lambda: False)
        ), patch(
            "uniclaw.tools.monitor.manager.os.name", "nt"
        ), patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ), patch(
            "uniclaw.tools.monitor.manager.asyncio.create_task",
            side_effect=_fake_create_task,
        ), patch.object(
            mgr, "_spawn_viewer", new=AsyncMock(return_value=None)
        ):
            result = await mgr.start_monitor(
                "cmd /c echo hi",
                pattern="",
                description="",
                timeout=0,
                visible=True,
            )

        assert "进程已启动" in result
        assert "未启用可见控制台" in result
        assert "输出走管道" in result
        assert "monitor_output" in result
        assert "pywinpty" in result or "ConPTY" in result
        # 不得宣称 ConPTY 终端已弹出
        assert "ConPTY 终端模式" not in result

    @pytest.mark.asyncio
    async def test_visible_non_shell_command_explains_reason(self):
        """visible=True + 非 shell 命令: 说明命令类型原因, 不假报 ConPTY。"""
        mgr = MonitorManager()
        process = MagicMock()
        process.returncode = None
        with patch.object(
            MonitorManager, "_winpty_available", staticmethod(lambda: True)
        ), patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            return_value=process,
        ), patch(
            "uniclaw.tools.monitor.manager.asyncio.create_task",
            side_effect=_fake_create_task,
        ), patch.object(
            mgr, "_spawn_viewer", new=AsyncMock(return_value=None)
        ):
            result = await mgr.start_monitor(
                "npm run dev",
                pattern="",
                description="",
                timeout=0,
                visible=True,
            )

        assert "未启用可见控制台" in result
        assert "命令非 shell" in result
        assert "ConPTY 终端模式" not in result

    @pytest.mark.asyncio
    async def test_visible_viewer_spawn_failure_reported(self):
        """镜像窗口弹出失败: 明确报失败, 不宣称已弹窗。"""
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
        ), patch.object(
            mgr, "_spawn_viewer", new=AsyncMock(return_value="无显示环境")
        ):
            result = await mgr.start_monitor(
                "npm run dev",
                pattern="",
                description="",
                timeout=0,
                visible=True,
            )

        assert "弹出失败" in result
        assert "无显示环境" in result
        assert "已弹出" not in result

    @pytest.mark.asyncio
    async def test_visible_conpty_success_claims_window(self):
        """ConPTY 真正可用且弹窗成功: 才允许宣称 ConPTY 终端模式。"""
        mgr = MonitorManager()
        fake_session = MagicMock()
        fake_session.pty = None
        with patch(
            "uniclaw.tools.monitor.manager.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
        ) as mock_shell, patch(
            "uniclaw.tools.monitor.manager.asyncio.create_task",
            side_effect=_fake_create_task,
        ), patch.object(
            MonitorManager, "_winpty_available", staticmethod(lambda: True)
        ), patch(
            "uniclaw.tools.monitor.pty_session.PtySession",
            return_value=fake_session,
        ) as mock_pty, patch.object(
            mgr, "_spawn_viewer", new=AsyncMock(return_value=None)
        ), patch(
            "uniclaw.tools.monitor.manager.os.name", "nt"
        ):
            result = await mgr.start_monitor(
                "cmd /c echo hi",
                pattern="",
                description="",
                timeout=0,
                visible=True,
            )

        mock_pty.assert_called_once()
        mock_shell.assert_not_awaited()
        assert "ConPTY 终端模式" in result
        assert "已弹出" in result

    def test_winpty_available_false_on_non_windows(self):
        """非 Windows 一律不可用。"""
        with patch("uniclaw.tools.monitor.manager.os.name", "posix"):
            assert MonitorManager._winpty_available() is False

    def test_explain_no_console_reused_for_degrade(self):
        """_explain_no_console 按优先级给根因(与 monitor_screen 同源)。"""
        m = _make_monitor("m1")
        m.visible = False
        assert "非可视模式" in MonitorManager._explain_no_console(m)

        m.visible = True
        m.command = "npm run dev"
        with patch("uniclaw.tools.monitor.manager.os.name", "nt"):
            assert "命令非 shell" in MonitorManager._explain_no_console(m)

        m.command = "cmd /c echo hi"
        with patch("uniclaw.tools.monitor.manager.os.name", "nt"), patch.object(
            MonitorManager, "_winpty_available", staticmethod(lambda: False)
        ):
            assert "pywinpty" in MonitorManager._explain_no_console(m)


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

    @pytest.mark.asyncio
    async def test_clears_old_matches(self):
        """更新模式时清空旧匹配记录。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="old")
        m.matched_lines.extend(["old-match-1", "old-match-2", "old-match-3"])
        m.match_time = datetime.now()
        m.last_notify_time = datetime.now()
        mgr._monitors["m1"] = m

        result = await mgr.update_pattern("m1", "new")
        assert m.pattern == "new"
        assert len(m.matched_lines) == 0
        assert m.match_time is None
        assert m.last_notify_time is None
        assert "已清空旧匹配记录" in result
        assert "3 条" in result

    @pytest.mark.asyncio
    async def test_clears_old_matches_no_note_when_empty(self):
        """旧匹配为空时不显示清空提示。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="old")
        mgr._monitors["m1"] = m

        result = await mgr.update_pattern("m1", "new")
        assert "已清空旧匹配记录" not in result

    @pytest.mark.asyncio
    async def test_new_pattern_matches_after_update(self):
        """更新后新 pattern 立即生效, 旧匹配不再累积。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="line-3")
        mgr._monitors["m1"] = m

        # 模拟旧 pattern 匹配
        m.matched_lines.append("line-30")
        m.matched_lines.append("line-31")

        await mgr.update_pattern("m1", "line-8")
        assert len(m.matched_lines) == 0

        # 模拟新 pattern 匹配(直接调用匹配逻辑等价操作)
        m.matched_lines.append("line-80")
        assert list(m.matched_lines) == ["line-80"]

    @pytest.mark.asyncio
    async def test_reset_notify_throttle(self):
        """更新模式后通知节流重置, 新 pattern 首次匹配可立即通知。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="old")
        # 刚刚通知过(处于节流窗口内)
        m.last_notify_time = datetime.now()
        mgr._monitors["m1"] = m

        await mgr.update_pattern("m1", "new")
        assert m.last_notify_time is None

    @pytest.mark.asyncio
    async def test_rescan_matches_stderr_body_without_prefix(self):
        """回扫 stderr 行时按正文匹配(去掉 [stderr] 前缀), 与 _read_output 一致。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="old")
        m.output_lines.append("hello")
        m.output_lines.append("[stderr] ERROR: boom")
        mgr._monitors["m1"] = m

        result = await mgr.update_pattern("m1", "^ERROR")
        assert "回扫历史输出: 新模式命中 1 条" in result
        # 记录的是去掉前缀的正文, 与实时匹配 append 的内容一致
        assert list(m.matched_lines) == ["ERROR: boom"]

    @pytest.mark.asyncio
    async def test_rescan_disabled_when_pattern_cleared(self):
        """清空 pattern 时不回扫, 且返回中不提回扫。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="old")
        m.output_lines.append("old-hit")
        mgr._monitors["m1"] = m

        result = await mgr.update_pattern("m1", "")
        assert "回扫" not in result
        assert list(m.matched_lines) == []


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
        """匹配模式时记录匹配行并通知; 进程结束后归为 STOPPED。"""
        mgr = MonitorManager()
        m = _make_monitor(pattern="ERROR")
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=[b"ERROR: boom\n", b""])
        process.returncode = 0
        m.process = process
        with patch.object(mgr, "_notify_match", new=AsyncMock()) as mock_notify:
            await mgr._read_output(m)
        # 匹配后进程随即结束: 状态不得卡在 matched, 应归为 stopped
        assert m.status == MonitorStatus.STOPPED
        assert list(m.output_lines) == ["ERROR: boom"]
        assert list(m.matched_lines) == ["ERROR: boom"]
        assert m.match_time is not None
        mock_notify.assert_awaited_once_with(m, "ERROR: boom")

    @pytest.mark.asyncio
    async def test_natural_exit_notifies(self):
        """自然退出触发结束唤醒。"""
        mgr = MonitorManager()
        m = _make_monitor()
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=[b"hi\n", b""])
        process.returncode = 0
        m.process = process
        with patch.object(mgr, "_notify_exit", new=AsyncMock()) as mock_exit:
            await mgr._read_output(m)
        assert m.status == MonitorStatus.STOPPED
        mock_exit.assert_awaited_once_with(m)

    @pytest.mark.asyncio
    async def test_no_match_eof(self):
        """无匹配时进程结束转为 STOPPED。"""
        mgr = MonitorManager()
        m = _make_monitor(pattern="zzz")
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=[b"hello\n", b""])
        process.returncode = 0
        m.process = process
        await mgr._read_output(m)
        assert m.status == MonitorStatus.STOPPED
        assert list(m.output_lines) == ["hello"]
        assert list(m.matched_lines) == []

    @pytest.mark.asyncio
    async def test_timeout(self):
        """超时转为 TIMEOUT。"""
        mgr = MonitorManager()
        m = _make_monitor(timeout=10)
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=[b"x\n", b""])
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
    async def test_timeout_notifies(self):
        """超时同样触发结束唤醒。"""
        mgr = MonitorManager()
        m = _make_monitor(timeout=10)
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=[b"x\n", b""])
        process.returncode = 0
        m.process = process
        mock_loop = MagicMock()
        mock_loop.time.side_effect = [100.0, 999.0]
        with patch(
            "uniclaw.tools.monitor.manager.asyncio.get_event_loop",
            return_value=mock_loop,
        ), patch.object(mgr, "_notify_exit", new=AsyncMock()) as mock_exit:
            await mgr._read_output(m)
        assert m.status == MonitorStatus.TIMEOUT
        mock_exit.assert_awaited_once_with(m)

    @pytest.mark.asyncio
    async def test_exception(self):
        """读取异常转为 ERROR 并杀进程树。"""
        mgr = MonitorManager()
        m = _make_monitor()
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=OSError("boom"))
        process.returncode = None
        m.process = process
        await mgr._read_output(m)
        assert m.status == MonitorStatus.ERROR

    @pytest.mark.asyncio
    async def test_exception_notifies(self):
        """读取异常同样触发结束唤醒。"""
        mgr = MonitorManager()
        m = _make_monitor()
        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=OSError("boom"))
        process.returncode = None
        m.process = process
        with patch.object(mgr, "_notify_exit", new=AsyncMock()) as mock_exit:
            await mgr._read_output(m)
        assert m.status == MonitorStatus.ERROR
        mock_exit.assert_awaited_once_with(m)

    @pytest.mark.asyncio
    async def test_cancelled_no_exit_notify(self):
        """取消读取任务不算进程结束, 不触发结束唤醒。"""
        mgr = MonitorManager()
        m = _make_monitor()
        gate = asyncio.Event()

        async def hang_forever(*_):
            await gate.wait()

        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=hang_forever)
        process.returncode = None
        m.process = process
        with patch.object(mgr, "_notify_exit", new=AsyncMock()) as mock_exit:
            task = asyncio.create_task(mgr._read_output(m))
            await asyncio.sleep(0)
            task.cancel()
            await task
        assert m.status == MonitorStatus.RUNNING
        mock_exit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancelled(self):
        """取消读取任务时不改变状态。"""
        mgr = MonitorManager()
        m = _make_monitor()
        gate = asyncio.Event()

        async def hang_forever(*_):
            await gate.wait()

        process = MagicMock()
        process.stdout.read = AsyncMock(side_effect=hang_forever)
        process.returncode = None
        m.process = process
        task = asyncio.create_task(mgr._read_output(m))
        await asyncio.sleep(0)
        task.cancel()
        await task
        assert m.status == MonitorStatus.RUNNING


class _ControlledStream:
    """可控异步流: 测试侧往 queue 塞字节块, 模拟子进程 stdout。"""

    def __init__(self):
        self._q: asyncio.Queue = asyncio.Queue()

    def feed(self, data: bytes):
        self._q.put_nowait(data)

    def eof(self):
        self._q.put_nowait(None)

    async def read(self, n: int) -> bytes:
        item = await self._q.get()
        return b"" if item is None else item


class TestUpdatePatternLiveMatching:
    """update_pattern 后新 pattern 是否走真实 _read_output 匹配路径生效。

    复现报告: 初始 watch_pattern="line-3" 能匹配, update 成 "line-50" 后
    新 pattern 全程不匹配。本类用真实匹配逻辑(而非手工 append matched_lines)
    验证/复现该问题。
    """

    @pytest.mark.asyncio
    async def test_new_pattern_matches_on_later_lines(self):
        """update 发生在目标行产出之前时, 新 pattern 必须匹配后续行。"""
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="line-3")
        stream = _ControlledStream()
        process = MagicMock()
        process.stdout = stream
        process.stderr = None
        process.returncode = None
        m.process = process
        mgr._monitors["m1"] = m

        with patch.object(mgr, "_notify_match", new=AsyncMock()):
            reader = asyncio.create_task(mgr._read_output(m))

            # 阶段1: 旧 pattern 匹配 line-3 / line-30..39
            for i in range(1, 40):
                stream.feed(f"line-{i}\n".encode())
            await asyncio.sleep(0.05)
            assert len(m.matched_lines) == 11, list(m.matched_lines)

            # 阶段2: update 为 line-50
            result = await mgr.update_pattern("m1", "line-50")
            assert "匹配模式已更新" in result
            assert m.pattern == "line-50"
            assert len(m.matched_lines) == 0

            # 阶段3: 继续产出 line-40..60, 其中含 line-50
            for i in range(40, 61):
                stream.feed(f"line-{i}\n".encode())
            stream.eof()
            await asyncio.wait_for(reader, timeout=2)

        assert "line-50" in list(m.matched_lines), (
            f"update 后新 pattern 未匹配: pattern={m.pattern!r} "
            f"matched={list(m.matched_lines)} output_tail={list(m.output_lines)[-10:]}"
        )

    @pytest.mark.asyncio
    async def test_retro_match_when_target_already_emitted(self):
        """目标行在 update 之前已产出时, 回扫 output_lines 必须补匹配。

        复现测试者场景: 快脚本已把 line-50 吐进缓冲, 之后才把 pattern
        改成 line-50 —— 若不回扫, get_matched 将永远为空。
        """
        mgr = MonitorManager()
        m = _make_monitor("m1", pattern="line-3")
        stream = _ControlledStream()
        process = MagicMock()
        process.stdout = stream
        process.stderr = None
        process.returncode = None
        m.process = process
        mgr._monitors["m1"] = m

        with patch.object(mgr, "_notify_match", new=AsyncMock()):
            reader = asyncio.create_task(mgr._read_output(m))

            # line-1..60 全部产出完毕(含 line-50), 旧 pattern 只命中 line-3 系列
            for i in range(1, 61):
                stream.feed(f"line-{i}\n".encode())
            await asyncio.sleep(0.05)
            assert len(m.matched_lines) == 11
            assert "line-50" in list(m.output_lines)

            # 此时才把 pattern 改成 line-50
            result = await mgr.update_pattern("m1", "line-50")
            assert "回扫历史输出: 新模式命中 1 条" in result

            # 再产出几行无关内容 + EOF
            stream.feed(b"line-999\n")
            stream.eof()
            await asyncio.wait_for(reader, timeout=2)

        assert "line-50" in list(m.matched_lines), (
            f"update 应回扫历史输出并命中 line-50: matched={list(m.matched_lines)}"
        )


class TestUpdatePatternRealProcess:
    """真实子进程复现: 快输出脚本 + 迟到的 update_pattern。"""

    def _write_script(self, tmp_path, n_lines: int, delay: float) -> str:
        """生成打印 line-1..n 的脚本, 返回可传给 start_monitor 的命令。"""
        script = tmp_path / f"gen_{n_lines}_{delay}.py"
        script.write_text(
            "import sys, time\n"
            f"for i in range(1, {n_lines + 1}):\n"
            "    print(f'line-{i}', flush=True)\n"
            f"    time.sleep({delay})\n",
            encoding="utf-8",
        )
        return f'"{sys.executable}" "{script}"'

    @pytest.mark.asyncio
    async def test_fast_process_target_already_consumed(self, tmp_path):
        """脚本一口气打完 line-1..200, 随后才 update 成 line-50。

        对应测试者报告: 初始 line-3 匹配 11 行, update 后 get_matched 恒空。
        根因不是字段不一致 —— 而是 line-50 在 update 之前已被 _read_output
        消费完; update_pattern 必须回扫 output_lines 才能命中。
        """
        mgr = MonitorManager()
        cmd = self._write_script(tmp_path, n_lines=200, delay=0)
        result = await mgr.start_monitor(
            cmd, pattern="line-3", description="fast", timeout=15
        )
        assert "进程已启动" in result, result
        monitor_id = result.split("ID: ")[1].splitlines()[0].strip()
        m = mgr._monitors[monitor_id]

        try:
            # 等到旧 pattern 攒够 11 条(或进程结束)
            for _ in range(200):
                if len(m.matched_lines) >= 11 or m.status in (
                    MonitorStatus.STOPPED,
                    MonitorStatus.ERROR,
                ):
                    break
                await asyncio.sleep(0.05)
            assert len(m.matched_lines) == 11, (
                f"初始 pattern 未按预期匹配 11 行: "
                f"matched={list(m.matched_lines)} status={m.status} "
                f"output={list(m.output_lines)[:20]}"
            )

            # 等进程把 line-1..200 全部吐完(模拟"调用 update 时目标行已流过")
            for _ in range(200):
                if m.status in (MonitorStatus.STOPPED, MonitorStatus.ERROR):
                    break
                await asyncio.sleep(0.05)
            assert m.status == MonitorStatus.STOPPED
            assert "line-50" in list(m.output_lines)

            update_result = await mgr.update_pattern(monitor_id, "line-50")
            assert "已清空旧匹配记录" in update_result
            assert "回扫历史输出: 新模式命中 1 条" in update_result
            assert m.pattern == "line-50"

            got = await mgr.get_matched(monitor_id)
            assert "line-50" in got, got
        finally:
            await mgr.stop_monitor(monitor_id)

    @pytest.mark.asyncio
    async def test_slow_process_update_before_target(self, tmp_path):
        """慢速脚本 + 在目标行出现前 update —— 新 pattern 必须生效。"""
        mgr = MonitorManager()
        cmd = self._write_script(tmp_path, n_lines=80, delay=0.05)
        result = await mgr.start_monitor(
            cmd, pattern="line-3", description="slow", timeout=20
        )
        assert "进程已启动" in result, result
        monitor_id = result.split("ID: ")[1].splitlines()[0].strip()
        m = mgr._monitors[monitor_id]

        try:
            # 等初始 pattern 命中(line-3/30..39, 约 2s)
            for _ in range(200):
                if len(m.matched_lines) >= 11:
                    break
                await asyncio.sleep(0.05)
            assert len(m.matched_lines) == 11

            # 趁 line-50 尚未产出(t≈2.0s, line-50 在 t≈2.5s)立刻 update
            await mgr.update_pattern(monitor_id, "line-50")

            # 等到 line-50 出现
            for _ in range(200):
                if "line-50" in list(m.matched_lines):
                    break
                if m.status in (MonitorStatus.STOPPED, MonitorStatus.ERROR):
                    break
                await asyncio.sleep(0.05)

            assert "line-50" in list(m.matched_lines), (
                f"update 后新 pattern 应匹配后续 line-50: "
                f"matched={list(m.matched_lines)} status={m.status}"
            )
        finally:
            await mgr.stop_monitor(monitor_id)


class TestNotifyMatch:
    """_notify_match 测试。"""

    @pytest.mark.asyncio
    async def test_notify_model(self):
        """通知用户并唤醒模型。"""
        mgr = MonitorManager()
        m = _make_monitor(pattern="ERROR", description="日志")
        m.config = MagicMock()
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
        m.config = None
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


class TestNotifyExit:
    """_notify_exit 测试: 进程结束自动唤醒(退出/超时/异常, 恰好一次)。"""

    @pytest.mark.asyncio
    async def test_notify_user_and_model(self, notify_mocks):
        """有 config: 桌面通知 + 唤醒模型, 消息含退出摘要与监控 ID。"""
        mock_push, mock_wake = notify_mocks
        mgr = MonitorManager()
        m = _make_monitor()
        m.config = MagicMock()
        m.status = MonitorStatus.STOPPED
        m.exit_code = 0
        await mgr._notify_exit(m)
        mock_push.assert_awaited_once()
        mock_wake.assert_awaited_once()
        message = mock_wake.call_args[0][0]
        assert "进程已结束" in message
        assert "exit=0" in message
        assert m.id in message

    @pytest.mark.asyncio
    async def test_notify_only_once(self, notify_mocks):
        """多条收尾路径(读线程/轮询线程)竞争时只通知一次。"""
        mock_push, mock_wake = notify_mocks
        mgr = MonitorManager()
        m = _make_monitor()
        m.config = MagicMock()
        m.status = MonitorStatus.STOPPED
        await mgr._notify_exit(m)
        await mgr._notify_exit(m)
        mock_push.assert_awaited_once()
        mock_wake.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_config_desktop_only(self, notify_mocks):
        """无 config 时不唤醒模型,仅通知用户。"""
        mock_push, mock_wake = notify_mocks
        mgr = MonitorManager()
        m = _make_monitor()
        m.config = None
        await mgr._notify_exit(m)
        mock_push.assert_awaited_once()
        mock_wake.assert_not_called()

    @pytest.mark.asyncio
    async def test_wake_ignores_notify_model_switch(self, notify_mocks):
        """notify_model=False 只静音匹配通知, 结束唤醒仍触发。"""
        mock_push, mock_wake = notify_mocks
        mgr = MonitorManager()
        m = _make_monitor()
        m.config = MagicMock()
        m.notify_model = False
        await mgr._notify_exit(m)
        mock_wake.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_monitor_suppresses_notify(self, notify_mocks):
        """agent 主动 stop: 预置 exit_notified, 后续收尾路径不再唤醒。"""
        mock_push, mock_wake = notify_mocks
        mgr = MonitorManager()
        m = _make_monitor("m1")
        m.config = MagicMock()
        process = MagicMock()
        process.returncode = 0
        m.process = process
        mgr._monitors["m1"] = m
        result = await mgr.stop_monitor("m1")
        assert "进程已退出" in result
        assert m.exit_notified is True
        # 模拟收尾路径迟到的结束通知: 不得再打扰 agent
        await mgr._notify_exit(m)
        mock_push.assert_not_called()
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

# ── monitor 溢出提示动态化(第 29 轮 #7) ────────────────────


class TestOverflowNote:
    """溢出提示不占环形缓冲,读取输出时动态附加。"""

    @pytest.mark.asyncio
    async def test_overflow_note_in_output(self):
        from uniclaw.tools.monitor.manager import MonitorManager
        from uniclaw.tools.monitor.models import Monitor

        mgr = MonitorManager()
        m = Monitor(monitor_id="p1", command="echo hi", pattern="", description="", timeout=0)
        for i in range(3):
            m.output_lines.append(f"line-{i}")
        m.buffer_full_warned = True
        mgr._monitors["p1"] = m

        out = await mgr.get_output("p1")

        assert "已被丢弃" in out
        assert "line-2" in out
        # 警告本身不占缓冲: 缓冲里只有真实输出
        assert all(x.startswith("line-") for x in m.output_lines)

    @pytest.mark.asyncio
    async def test_no_note_when_not_overflowed(self):
        from uniclaw.tools.monitor.manager import MonitorManager
        from uniclaw.tools.monitor.models import Monitor

        mgr = MonitorManager()
        m = Monitor(monitor_id="p2", command="echo hi", pattern="", description="", timeout=0)
        m.output_lines.append("line-0")
        mgr._monitors["p2"] = m

        out = await mgr.get_output("p2")

        assert "已被丢弃" not in out

class TestClearKeepsAliveMatched:
    @pytest.mark.asyncio
    async def test_matched_running_not_cleared(self):
        from uniclaw.tools.monitor.manager import MonitorManager
        from uniclaw.tools.monitor.models import Monitor, MonitorStatus

        mgr = MonitorManager()
        alive = Monitor(monitor_id="m1", command="sleep", pattern="", description="", timeout=0)
        alive.status = MonitorStatus.MATCHED
        alive.process = MagicMock()  # has_exited: returncode None → 在跑
        alive.process.returncode = None
        done = Monitor(monitor_id="m2", command="echo", pattern="", description="", timeout=0)
        done.status = MonitorStatus.STOPPED
        mgr._monitors = {"m1": alive, "m2": done}

        result = await mgr.clear_monitors()

        assert "m1" not in mgr._monitors or "m2" not in mgr._monitors
        assert "m1" in mgr._monitors, "仍在运行的 matched 进程不得被清成孤儿"
        assert "m2" not in mgr._monitors
        assert "已清理 1 个" in result
