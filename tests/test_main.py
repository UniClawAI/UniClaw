"""main.py 入口点测试 — 覆盖 argparse 参数解析与模式分发(main.py 原本 0% 覆盖)。

用真实的 asyncio.run(传入的 coro 被真正 await),以便断言 launcher.launch
收到的参数;同时屏蔽后台预加载线程与首次启动向导。
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture(autouse=True)
def _silence_preload_threads():
    """main() 内部会启动 tiktoken/OpenAI/MCP 后台预加载线程,测试中屏蔽。"""
    with patch(
        "uniclaw.main.threading.Thread", new=MagicMock()
    ) as mock_thread:
        yield mock_thread


class TestMainModeDispatch:
    """main() 根据 --mode 分发到不同 launcher。"""

    def test_console_mode_default(self, monkeypatch):
        """默认 console 模式:调用 console.launcher.launch,不跑向导(非首次)。"""
        import uniclaw.main as main_mod

        monkeypatch.setattr("sys.argv", ["uniclaw"])
        mock_launch = AsyncMock()
        with (
            patch("uniclaw.main.is_first_launch", return_value=False),
            patch("uniclaw.main.run_setup_wizard", new_callable=AsyncMock) as mock_wizard,
            patch("uniclaw.console.launcher.launch", new=mock_launch),
        ):
            main_mod.main()

        mock_wizard.assert_not_awaited()
        mock_launch.assert_awaited_once()

    def test_console_first_launch_runs_wizard(self, monkeypatch):
        """首次启动(console)先运行设置向导再进 REPL。"""
        import uniclaw.main as main_mod

        monkeypatch.setattr("sys.argv", ["uniclaw"])
        mock_launch = AsyncMock()
        with (
            patch("uniclaw.main.is_first_launch", return_value=True),
            patch("uniclaw.main.run_setup_wizard", new_callable=AsyncMock) as mock_wizard,
            patch("uniclaw.console.launcher.launch", new=mock_launch),
        ):
            main_mod.main()

        mock_wizard.assert_awaited_once()
        mock_launch.assert_awaited_once()

    def test_webui_mode_default_ssl(self, monkeypatch):
        """--mode webui 分发到 webui.launcher.launch,默认启用 SSL。"""
        import uniclaw.main as main_mod

        monkeypatch.setattr("sys.argv", ["uniclaw", "--mode", "webui", "--port", "9090"])
        mock_launch = AsyncMock()
        with (
            patch("uniclaw.webui.launcher.launch", new=mock_launch),
        ):
            main_mod.main()

        mock_launch.assert_awaited_once()
        kwargs = mock_launch.await_args.kwargs
        assert kwargs["port"] == 9090
        assert kwargs["ssl"] is True
        assert kwargs["host"] == "127.0.0.1"

    def test_webui_no_ssl(self, monkeypatch):
        """--no-ssl 关闭 HTTPS。"""
        import uniclaw.main as main_mod

        monkeypatch.setattr("sys.argv", ["uniclaw", "--mode", "webui", "--no-ssl"])
        mock_launch = AsyncMock()
        with (
            patch("uniclaw.webui.launcher.launch", new=mock_launch),
        ):
            main_mod.main()

        assert mock_launch.await_args.kwargs["ssl"] is False

    def test_webui_custom_host_domain(self, monkeypatch):
        """自定义 host 与 domain 透传给 launch。"""
        import uniclaw.main as main_mod

        monkeypatch.setattr(
            "sys.argv",
            ["uniclaw", "--mode", "webui", "--host", "0.0.0.0", "--domain", "example.com"],
        )
        mock_launch = AsyncMock()
        with (
            patch("uniclaw.webui.launcher.launch", new=mock_launch),
        ):
            main_mod.main()

        kwargs = mock_launch.await_args.kwargs
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["domain"] == "example.com"

    def test_invalid_mode_rejected(self, monkeypatch):
        """非法 mode 值触发 argparse 报错(exit code 2)。"""
        import uniclaw.main as main_mod

        monkeypatch.setattr("sys.argv", ["uniclaw", "--mode", "invalid"])
        with pytest.raises(SystemExit) as excinfo:
            main_mod.main()
        assert excinfo.value.code == 2

    def test_default_console_no_mode_flag(self, monkeypatch):
        """不带 --mode 参数时 args.mode 为默认值 'console'。"""
        import uniclaw.main as main_mod

        monkeypatch.setattr("sys.argv", ["uniclaw"])
        mock_launch = AsyncMock()
        with (
            patch("uniclaw.main.is_first_launch", return_value=False),
            patch("uniclaw.console.launcher.launch", new=mock_launch),
        ):
            main_mod.main()
        # console launch 不带任何参数调用
        mock_launch.assert_awaited_once()
        assert mock_launch.await_args.args == ()


class TestMainPreload:
    """main() 启动 3 个后台预加载线程(tiktoken/OpenAI/MCP)。"""

    def test_main_spawns_all_preload_threads(self, monkeypatch):
        import uniclaw.main as main_mod

        started: list[str] = []

        class _FakeThread:
            def __init__(self, target=None, daemon=None):
                self.target = target
                self.daemon = daemon

            def start(self):
                started.append(self.target)

        monkeypatch.setattr(main_mod.threading, "Thread", _FakeThread)
        monkeypatch.setattr("sys.argv", ["uniclaw"])

        with (
            patch("uniclaw.main.is_first_launch", return_value=False),
            patch("uniclaw.console.launcher.launch", new=AsyncMock()),
        ):
            main_mod.main()

        assert len(started) == 3  # tiktoken + openai + mcp

    def test_preload_threads_are_daemon(self, monkeypatch):
        """后台预加载线程都是守护线程,进程退出不阻塞。"""
        import uniclaw.main as main_mod

        threads: list = []
        monkeypatch.setattr(
            main_mod.threading,
            "Thread",
            lambda target=None, daemon=None: threads.append(
                type("T", (), {"start": lambda s: None, "daemon": daemon})()
            )
            or threads[-1],
        )
        monkeypatch.setattr("sys.argv", ["uniclaw"])
        with (
            patch("uniclaw.main.is_first_launch", return_value=False),
            patch("uniclaw.console.launcher.launch", new=AsyncMock()),
        ):
            main_mod.main()

        assert len(threads) == 3
        for t in threads:
            assert t.daemon is True
