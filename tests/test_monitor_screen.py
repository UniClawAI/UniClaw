"""monitor_screen 工具测试 — ScreenBuffer 全屏快照能力。"""

from unittest.mock import AsyncMock, MagicMock, Mock

from uniclaw.tools.monitor.manager import MonitorManager
from uniclaw.tools.monitor.models import Monitor
from uniclaw.tools.monitor.pty_session import PtySession, ScreenBuffer, VtLineParser


def _notify(*_args):
    pass


def screen_text(*chunks: str, rows: int = 5, cols: int = 20) -> str:
    """构造 ScreenBuffer 并按顺序喂入 chunks, 返回整屏文本。"""
    buf = ScreenBuffer(rows, cols)
    for chunk in chunks:
        buf.feed(chunk)
    return buf.text()


class TestScreenBufferBasics:
    """基础打印与换行。"""

    def test_plain_lines(self):
        assert screen_text("hello\r\nworld") == "hello\nworld"

    def test_half_typed_input_stays_on_screen(self):
        # 未回车的键入只存在于屏幕上(单行解析器会扣留提示符行),
        # 这是 monitor_screen 区别于 monitor_output 的核心价值。
        assert screen_text("D:\\code>echo 未回车") == "D:\\code>echo 未回车"

    def test_carriage_return_overwrites_from_line_start(self):
        assert screen_text("abc\rXY") == "XYc"

    def test_top_rows_scroll_off(self):
        # 8 行全带换行, rows=5 时前 4 行(L0-L3)依次滚出
        text = screen_text("".join(f"L{i}\r\n" for i in range(8)), rows=5, cols=20)
        assert "L3" not in text
        assert "L4" in text
        assert "L7" in text

    def test_blank_tail_collapsed(self):
        assert screen_text("a\r\nb\r\n\r\n\r\n") == "a\nb"


class TestScreenBufferControlChars:
    """控制字符与 CSI 序列处理。"""

    def test_backspace_moves_cursor_and_next_write_overwrites(self):
        # cmd 回显退格模式: BS 移回光标, 下一个可打印字符覆盖原位
        assert screen_text("echo hi\x08X") == "echo hX"
        # BS SP BS: 净效果等价于删除最后一个字符
        assert screen_text("abc\x08 \x08d") == "abd"

    def test_del_removes_previous_char(self):
        assert screen_text("ab\x7fc") == "ac"

    def test_tab_advances_to_next_stop_without_erasing(self):
        # tab 只移动光标不清格: 已有内容不能被吞掉
        assert screen_text("ab\tc", cols=20) == "ab      c"
        # abcdefgh 后退 5 格(col8->3)覆盖写 X, 再 tab 跳到 col8 写 Y
        assert screen_text("abcdefgh\x08\x08\x08\x08\x08X\tY", cols=20) == ("abcXefghY")

    def test_cup_positions_cursor(self):
        assert screen_text("aaaaa\r\nbbbbb\x1b[2;2HX") == "aaaaa\nbXbbb"

    def test_cursor_movement_csi(self):
        # ESC[2D 左移两格后覆盖写
        assert screen_text("abcdef\x1b[2DXY") == "abcdXY"

    def test_column_absolute_csi_G(self):
        assert screen_text("abcdef\x1b[3GX") == "abXdef"

    def test_erase_in_line_modes(self):
        # K 系列只清格不移动光标: hello 占 col0-4, 光标停在 col5,
        # XX 从 col5 写起, 前面的空格是 K1/K2 清出的
        assert screen_text("hello\x1b[Kworld") == "helloworld"  # K0 从光标清到行尾
        assert screen_text("hello\x1b[1KXX") == "     XX"  # K1 清行首到光标(含)
        assert screen_text("hello\x1b[2KXX") == "     XX"  # K2 清整行

    def test_erase_in_display_modes(self):
        # J2 全清: 后续内容从顶部开始
        assert screen_text("aaa\r\nbbb\x1b[2J\x1b[Hccc") == "ccc"
        # J0 光标到屏幕底
        assert screen_text("aaa\r\nbbb\x1b[2;2H\x1b[J") == "aaa\nb"

    def test_scroll_up_down(self):
        # S 上滚: 原第 0 行滚出, 内容整体上移
        assert screen_text("a\r\nb\r\nc\x1b[1S") == "b\nc"
        # T 下滚: 内容整体下移, 顶部空出
        assert screen_text("a\r\nb\x1b[1T") == "\na\nb"

    def test_osc_dcs_skipped(self):
        assert screen_text("\x1b]0;title\x07text") == "text"
        assert screen_text("\x1bP+q544e\x1b\\after") == "after"

    def test_incomplete_sequence_buffered_across_chunks(self):
        # 颜色序列跨块截断时不能把半截 ESC 文本画到屏幕上
        assert screen_text("数据\x1b[3", "1m更多") == "数据更多"

    def test_wide_char_occupies_two_cells(self):
        # 中文占两列: 第 3 列定位落在"中"的主格, 覆盖后占位格清为空格
        assert screen_text("AB中", cols=20) == "AB中"
        assert screen_text("AB中\x1b[3GX", cols=20) == "ABX"

    def test_combining_char_attaches_to_previous_cell(self):
        assert screen_text("e\u0301x") == "e\u0301x"

    def test_wide_char_at_line_end_wraps(self):
        # 行尾只剩一列放不下宽字符: 折行后再写
        assert screen_text("abc中", cols=4) == "abc\n中"

    def test_auto_wrap_on_full_line(self):
        assert screen_text("abcdefghij", cols=4) == "abcd\nefgh\nij"


class TestScreenBufferResize:
    """窗口尺寸变化。"""

    def test_resize_keeps_intersection(self):
        buf = ScreenBuffer(3, 10)
        buf.feed("aaaa\r\nbbbb\r\ncccc")
        buf.resize(2, 4)
        assert buf.text() == "aaaa\nbbbb"
        buf.resize(4, 6)
        # 放大恢复: 旧内容仍在, 新增区域空白
        assert buf.text() == "aaaa\nbbbb"

    def test_resize_clamps_cursor(self):
        buf = ScreenBuffer(5, 20)
        buf.feed("hello")
        buf.resize(2, 3)
        # 光标 col=5 被 clamp 到 cols-1=2, X 覆盖 col2 的 l, 不越界
        buf.feed("X")
        assert buf.text() == "heX"


class TestSessionDispatch:
    """PtySession 双路分发契约。"""

    def _make_session(self, monitor: Monitor) -> PtySession:
        session = PtySession.__new__(PtySession)
        session.monitor = monitor
        session.loop = Mock()
        session.loop.call_soon_threadsafe.side_effect = (
            lambda _callback, coroutine: coroutine.close()
        )
        session.on_notify = _notify
        session._last_idle_snapshot = None
        session._parser = VtLineParser(session._commit_line)
        session._screen = ScreenBuffer(PtySession.PTY_ROWS, PtySession.PTY_COLS)
        return session

    def test_dispatch_feeds_both_parsers(self):
        monitor = Monitor("m1", "cmd", "READY", "", 0)
        session = self._make_session(monitor)

        session._dispatch("hello world\r\nREADY\r\n")

        # 单行流照常提交到 output_lines / 匹配
        assert list(monitor.output_lines) == ["hello world", "READY"]
        assert monitor.status.value == "matched"
        # 屏幕网格同步维护最终画面
        assert session.screen_text() == "hello world\nREADY"

    def test_screen_survives_parser_exception_isolation(self):
        monitor = Monitor("m1", "cmd", "", "", 0)
        session = self._make_session(monitor)
        session._parser = VtLineParser(lambda _line: None)

        # 单行解析器的回调抛异常不应影响屏幕网格(feed 内部已兜底)
        session._dispatch("visible\r\n")
        assert "visible" in session.screen_text()


class TestManagerGetScreen:
    """MonitorManager.get_screen 测试。"""

    def _make_manager(self) -> MonitorManager:
        return MonitorManager()

    async def test_not_found(self):
        mgr = self._make_manager()
        result = await mgr.get_screen("nope")
        assert "[TOOL_ERROR]" in result
        assert "不存在" in result

    async def test_pty_mode_returns_snapshot(self):
        mgr = self._make_manager()
        m = Monitor("m1", "cmd", "", "shell 会话", 0)
        m.pty = MagicMock()
        m.pty.screen_text.return_value = "D:\\code>dir"
        mgr._monitors["m1"] = m

        result = await mgr.get_screen("m1")

        assert "D:\\code>dir" in result
        assert "当前屏幕" in result

    async def test_pipe_mode_falls_back_to_history(self):
        mgr = self._make_manager()
        m = Monitor("m1", "npm run dev", "", "dev server", 0)
        m.output_lines.extend(["ready in 300ms", "listening :8080"])
        mgr._monitors["m1"] = m

        result = await mgr.get_screen("m1")

        assert "管道模式无真实控制台" in result
        assert "ready in 300ms" in result
        assert "listening :8080" in result

    async def test_pipe_mode_empty_history(self):
        mgr = self._make_manager()
        m = Monitor("m1", "sleep 100", "", "", 0)
        mgr._monitors["m1"] = m

        result = await mgr.get_screen("m1")
        assert "暂无输出" in result

    async def test_pty_mode_blank_screen(self):
        mgr = self._make_manager()
        m = Monitor("m1", "cmd", "", "", 0)
        m.pty = MagicMock()
        m.pty.screen_text.return_value = ""
        mgr._monitors["m1"] = m

        result = await mgr.get_screen("m1")
        assert "屏幕空白" in result


async def test_monitor_screen_tool_delegates(monkeypatch):
    """工具层薄封装委托给 manager.get_screen。"""
    from uniclaw.tools.monitor import tools as monitor_tools

    mgr = MagicMock()
    mgr.get_screen = AsyncMock(return_value="screen-ok")
    monkeypatch.setattr(MonitorManager, "get_instance", classmethod(lambda cls: mgr))

    result = await monitor_tools.monitor_screen("m1")
    assert result == "screen-ok"
    mgr.get_screen.assert_awaited_once_with("m1")
