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
        """进程已结束时只取消读取任务,不杀进程。"""
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
