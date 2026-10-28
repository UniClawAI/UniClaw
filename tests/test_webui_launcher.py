"""webui/launcher.py 测试 — WebUI 启动辅助函数(原本 0% 覆盖)。

覆盖:request_shutdown 优雅关闭、看门狗强制退出、SSL 证书生成、
地址判断、本机 IP 获取、launch 启动参数解析(用 mock 隔离 uvicorn)。
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import uniclaw.webui.launcher as launcher


@pytest.fixture(autouse=True)
def _reset_globals():
    launcher._server_instance = None
    launcher._serve_done = True
    launcher._LAUNCH_KWARGS = {}
    yield
    launcher._server_instance = None
    launcher._serve_done = True
    launcher._LAUNCH_KWARGS = {}


class TestIsIpv6:
    def test_v6(self):
        assert launcher._is_ipv6("::1") is True
        assert launcher._is_ipv6("2001:db8::1") is True

    def test_v4(self):
        assert launcher._is_ipv6("127.0.0.1") is False

    def test_invalid(self):
        assert launcher._is_ipv6("not-an-ip") is False
        assert launcher._is_ipv6("") is False


class TestGetLocalIp:
    def test_ipv4_success(self):
        with patch(
            "uniclaw.webui.launcher.socket.socket"
        ) as MockSocket:
            sock = MockSocket.return_value.__enter__.return_value
            sock.getsockname.return_value = ("192.168.1.5", 0)
            got = launcher._get_local_ip(is_ipv6=False)
        assert got == "192.168.1.5"
        sock.connect.assert_called_once_with(("8.8.8.8", 80))

    def test_ipv4_failure(self):
        with patch(
            "uniclaw.webui.launcher.socket.socket"
        ) as MockSocket:
            MockSocket.return_value.__enter__.side_effect = OSError("no net")
            got = launcher._get_local_ip(is_ipv6=False)
        assert got == ""

    def test_ipv6_success(self):
        with patch(
            "uniclaw.webui.launcher.socket.socket"
        ) as MockSocket:
            sock = MockSocket.return_value.__enter__.return_value
            sock.getsockname.return_value = ("2001:db8::1%eth0", 0)
            got = launcher._get_local_ip(is_ipv6=True)
        assert got == "2001:db8::1"  # 去掉 scope ID

    def test_ipv6_failure(self):
        with patch(
            "uniclaw.webui.launcher.socket.socket"
        ) as MockSocket:
            MockSocket.return_value.__enter__.side_effect = OSError("no v6")
            got = launcher._get_local_ip(is_ipv6=True)
        assert got == ""


class TestRequestShutdown:
    def test_no_server_noop(self):
        launcher._server_instance = None
        launcher.request_shutdown()  # 不应抛异常

    def test_sets_should_exit(self):
        server = MagicMock()
        launcher._server_instance = server
        launcher._serve_done = True
        with patch(
            "uniclaw.webui.launcher.asyncio.create_task",
            new_callable=MagicMock,
        ) as mock_create:
            launcher.request_shutdown()
        # mock 掉了 create_task,协程从未被调度 → close 避免 "never awaited" 警告
        created_coro = mock_create.call_args.args[0]
        created_coro.close()
        assert server.should_exit is True
        assert launcher._serve_done is False
        mock_create.assert_called_once()  # 启动看门狗


class TestForceExitWatchdog:
    @pytest.mark.asyncio
    async def test_completed_no_force(self):
        launcher._serve_done = True
        launcher._server_instance = MagicMock()
        with patch(
            "uniclaw.webui.launcher.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            await launcher._force_exit_watchdog()
        mock_sleep.assert_awaited_once()
        # serve_done=True 时不置 force_exit
        assert launcher._server_instance.force_exit is not True

    @pytest.mark.asyncio
    async def test_no_server_no_force(self):
        launcher._serve_done = False
        launcher._server_instance = None
        with patch(
            "uniclaw.webui.launcher.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await launcher._force_exit_watchdog()
        # 无 server 直接返回,无副作用

    @pytest.mark.asyncio
    async def test_timeout_forces_exit(self):
        server = MagicMock()
        launcher._serve_done = False
        launcher._server_instance = server
        with patch(
            "uniclaw.webui.launcher.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "uniclaw.webui.launcher.logger.warning",
        ), patch(
            "uniclaw.webui.launcher.sys.stdout",
        ):
            await launcher._force_exit_watchdog()
        assert server.force_exit is True


class TestEnsureSslCerts:
    def test_delegates_to_get_or_create(self):
        with patch(
            "uniclaw.utils.ssl_cert.get_or_create_certs",
            return_value=("key.pem", "cert.pem"),
        ) as mock_gocc:
            key, cert = launcher._ensure_ssl_certs("example.com")
        mock_gocc.assert_called_once_with("example.com")
        assert (key, cert) == ("key.pem", "cert.pem")


class TestLaunch:
    """launch() 启动流程(uvicorn 用 mock 隔离)。"""

    @pytest.mark.asyncio
    async def test_launch_http(self):
        with (
            patch(
                "uniclaw.tools.scheduler.scheduler.Scheduler"
            ) as MockScheduler,
            patch(
                "uniclaw.webui.launcher.uvicorn.Config"
            ) as MockConfig,
            patch(
                "uniclaw.webui.launcher.uvicorn.Server"
            ) as MockServer,
            patch(
                "uniclaw.tools.restart.resumer.schedule_pending_restart_resume",
            ) as mock_schedule,
            patch(
                "uniclaw.tools.restart.relauncher.relaunch_if_pending",
                new_callable=AsyncMock,
            ) as mock_relaunch,
        ):
            MockScheduler.get_instance.return_value.start = AsyncMock()
            server = MagicMock()
            server.serve = AsyncMock()
            MockServer.return_value = server

            await launcher.launch(host="127.0.0.1", port=8080, ssl=False)

        # SSL 关闭时不用证书
        MockConfig.assert_called_once()
        _, kwargs = MockConfig.call_args
        assert kwargs["ssl_keyfile"] is None
        assert kwargs["ssl_certfile"] is None
        mock_schedule.assert_called_once()
        mock_relaunch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_launch_ssl(self):
        with (
            patch(
                "uniclaw.tools.scheduler.scheduler.Scheduler"
            ) as MockScheduler,
            patch(
                "uniclaw.webui.launcher._ensure_ssl_certs",
                return_value=("key.pem", "cert.pem"),
            ),
            patch(
                "uniclaw.webui.launcher.uvicorn.Config"
            ) as MockConfig,
            patch(
                "uniclaw.webui.launcher.uvicorn.Server"
            ) as MockServer,
            patch(
                "uniclaw.tools.restart.resumer.schedule_pending_restart_resume",
            ),
            patch(
                "uniclaw.tools.restart.relauncher.relaunch_if_pending",
                new_callable=AsyncMock,
            ),
        ):
            MockScheduler.get_instance.return_value.start = AsyncMock()
            server = MagicMock()
            server.serve = AsyncMock()
            MockServer.return_value = server

            await launcher.launch(host="0.0.0.0", port=8443, ssl=True)

        _, kwargs = MockConfig.call_args
        assert kwargs["ssl_keyfile"] == "key.pem"
        assert kwargs["ssl_certfile"] == "cert.pem"