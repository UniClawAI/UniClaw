"""/subagent 斜杠命令测试 — 覆盖注册、子命令分发与参数解析。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.commands import COMMANDS, COMMAND_SUBCOMMANDS
from uniclaw.commands.subagent import cmd_subagent


@pytest.fixture
def config():
    """配置替身。"""
    cfg = MagicMock()
    cfg.depth = 0
    cfg.root_dir = None
    return cfg


@pytest.fixture
def ui():
    """捕获命令的 UI 输出(info/ok/warn/err)。"""
    with (
        patch("uniclaw.commands.subagent.info", new_callable=AsyncMock) as info,
        patch("uniclaw.commands.subagent.ok", new_callable=AsyncMock) as ok,
        patch("uniclaw.commands.subagent.warn", new_callable=AsyncMock) as warn,
        patch("uniclaw.commands.subagent.err", new_callable=AsyncMock) as err,
    ):
        yield {"info": info, "ok": ok, "warn": warn, "err": err}


def _msgs(ui, key: str) -> str:
    """拼接某个 UI 通道的全部文本,便于断言。"""
    return "\n".join(str(c.args[0]) for c in ui[key].call_args_list)


class TestRegistration:
    """命令注册与子命令补全。"""

    def test_registered(self):
        assert "subagent" in COMMANDS
        assert COMMANDS["subagent"] is cmd_subagent

    def test_subcommands_registered(self):
        assert COMMAND_SUBCOMMANDS["subagent"] == [
            "list",
            "send",
            "close",
            "check",
        ]

    @pytest.mark.asyncio
    async def test_handler_returns_bool(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_list_tasks",
            new_callable=AsyncMock,
            return_value="没有子智能体任务。",
        ):
            assert await cmd_subagent("list", config) is True


class TestDispatch:
    """子命令分发。"""

    @pytest.mark.asyncio
    async def test_no_args_shows_usage_and_list(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_list_tasks",
            new_callable=AsyncMock,
            return_value="没有子智能体任务。",
        ) as mock_list:
            await cmd_subagent("", config)
        assert "/subagent list" in _msgs(ui, "info")
        assert "/subagent send" in _msgs(ui, "info")
        mock_list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_calls_list_tasks(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_list_tasks",
            new_callable=AsyncMock,
            return_value="任务ID | 名称",
        ) as mock_list:
            await cmd_subagent("list", config)
        mock_list.assert_awaited_once()
        rt = mock_list.await_args.kwargs["tool_runtime"]
        assert rt.config is config

    @pytest.mark.asyncio
    async def test_send_splits_target_and_message(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_send_to_child",
            new_callable=AsyncMock,
            return_value="消息已排队",
        ) as mock_send:
            await cmd_subagent("send worker 继续第二轮 加个测试", config)
        mock_send.assert_awaited_once()
        args = mock_send.await_args.args
        assert args[0] == "worker"
        assert args[1] == "继续第二轮 加个测试"

    @pytest.mark.asyncio
    async def test_send_without_message_warns(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_send_to_child",
            new_callable=AsyncMock,
        ) as mock_send:
            await cmd_subagent("send worker", config)
        mock_send.assert_not_awaited()
        assert "用法" in _msgs(ui, "warn")

    @pytest.mark.asyncio
    async def test_close_dispatches(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_close",
            new_callable=AsyncMock,
            return_value="已关闭子智能体",
        ) as mock_close:
            await cmd_subagent("close worker", config)
        mock_close.assert_awaited_once()
        assert mock_close.await_args.args[0] == "worker"

    @pytest.mark.asyncio
    async def test_close_without_target_warns(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_close",
            new_callable=AsyncMock,
        ) as mock_close:
            await cmd_subagent("close", config)
        mock_close.assert_not_awaited()
        assert "用法" in _msgs(ui, "warn")

    @pytest.mark.asyncio
    async def test_check_dispatches(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_check_result",
            new_callable=AsyncMock,
            return_value="状态:running",
        ) as mock_check:
            await cmd_subagent("check task-1", config)
        mock_check.assert_awaited_once()
        assert mock_check.await_args.args[0] == "task-1"

    @pytest.mark.asyncio
    async def test_tool_error_routed_to_err_without_prefix(self, config, ui):
        with patch(
            "uniclaw.tools.multi_agent.tools.subagent_close",
            new_callable=AsyncMock,
            return_value="[TOOL_ERROR]: 未找到名为 'x' 的子智能体",
        ):
            await cmd_subagent("close x", config)
        assert "未找到" in _msgs(ui, "err")
        assert "[TOOL_ERROR]" not in _msgs(ui, "err")