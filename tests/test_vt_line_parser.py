from unittest.mock import Mock

from uniclaw.tools.monitor.models import Monitor, MonitorStatus
from uniclaw.tools.monitor.pty_session import PtySession, VtLineParser


async def _notify(*_args):
    pass


def parse(*chunks: str) -> list[str]:
    lines: list[str] = []
    parser = VtLineParser(lines.append)
    for chunk in chunks:
        parser.feed(chunk)
    return lines


def test_conpty_crlf_frames_commit_once_per_visible_line():
    assert parse(
        "\rWindows IP 配置\r\r\n\r\r\r\n\r以太网适配器 以太网:\r\r\n"
    ) == ["Windows IP 配置", "", "以太网适配器 以太网:"]


def test_backspace_editing_does_not_leave_key_echo_fragments():
    assert parse(
        "\rD:\\code>d",
        "\x08 \x08echo \"hi\"\r\n\"hi\"\r\r\n",
    ) == ['D:\\code>echo "hi"', '"hi"']


def test_ime_full_line_redraw_overwrites_previous_frame():
    assert parse(
        '\rD:\\code>echo "你',
        '\rD:\\code>echo "你好"\r\n"你好"\r\r\n',
    ) == ['D:\\code>echo "你好"', '"你好"']


def test_carriage_return_progress_updates_collapse_to_final_state():
    assert parse("40%\r75%\r100%\r\n") == ["100%"]


def test_csi_cursor_left_overwrites_existing_characters():
    assert parse("abc\x1b[2Dxy\r\n") == ["axy"]


def test_csi_erase_line_clears_only_the_requested_tail():
    assert parse("hello\x1b[Kworld\r\n") == ["helloworld"]


def test_osc_title_is_not_visible_output():
    assert parse("\x1b]0;title\x07text\r\n") == ["text"]


def test_incomplete_csi_is_buffered_across_chunks():
    # 颜色序列在块边界被截断时，不能把半截 ESC 文本泄漏到输出。
    assert parse("数据\x1b[3", "1m更多\r\n") == ["数据更多"]


def test_wide_character_cursor_uses_terminal_columns():
    assert parse("中文AB\x1b[2Dx\r\n") == ["中文xB"]


def test_pattern_matching_contract_is_preserved():
    monitor = Monitor("m1", "cmd", "DONE", "", 0)
    session = PtySession.__new__(PtySession)
    session.monitor = monitor
    session.loop = Mock()
    session.loop.call_soon_threadsafe.side_effect = (
        lambda _callback, coroutine: coroutine.close()
    )
    session.on_notify = _notify
    session._last_idle_snapshot = None
    session._parser = VtLineParser(session._commit_line)

    session._parser.feed("xx\rDONE\r\n")

    assert list(monitor.output_lines) == ["DONE"]
    assert list(monitor.matched_lines) == ["DONE"]
    assert monitor.status == MonitorStatus.MATCHED
    session.loop.call_soon_threadsafe.assert_called_once()


def test_consecutive_blank_lines_are_collapsed_by_emit_contract():
    monitor = Monitor("m1", "cmd", "", "", 0)
    session = PtySession.__new__(PtySession)
    session.monitor = monitor
    session.loop = Mock()
    session.on_notify = _notify
    session._last_idle_snapshot = None
    session._parser = VtLineParser(session._commit_line)

    session._parser.feed("a\r\n\r\r\n\r\r\nb\r\r\n")

    assert list(monitor.output_lines) == ["a", "", "b"]
