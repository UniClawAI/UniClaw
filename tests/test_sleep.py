"""异步定时器测试 — 覆盖 sleep_timer 和 wait 的参数校验与子代理可用性。"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.sleep import (
    sleep_timer,
    sleep_timer_available,
    wait,
    get_tools,
    get_all_tools,
)


def _fake_create_task(coro):
    """模拟 asyncio.create_task:关闭协程避免 'never awaited' 警告。"""
    coro.close()
    return MagicMock()


def _make_config(is_sub: bool = False, keep_alive: bool = False):
    """构造 mock config(is_sub/keep_alive 区分主 agent、同步/异步子代理)。"""
    config = MagicMock()
    config.is_sub = is_sub
    task = MagicMock()
    task.keep_alive = keep_alive
    config.current_agent = task
    config.unavailable_tool_reasons = {}
    config.record_unavailable_tools = (
        lambda names, reason: config.unavailable_tool_reasons.update(
            {n: reason for n in names}
        )
    )
    config.clear_unavailable_tools = lambda names: [
        config.unavailable_tool_reasons.pop(n, None) for n in names
    ]
    return config


class TestSleepTimerAvailability:
    """sleep_timer 可用性判断(主 agent/同步子代理/异步子代理)。"""

    def test_none_config_available(self):
        """config 为 None 时不限制。"""
        assert sleep_timer_available(None) is True

    def test_main_agent_available(self):
        """主 agent 可用。"""
        assert sleep_timer_available(_make_config(is_sub=False)) is True

    def test_sync_subagent_unavailable(self):
        """同步子代理不可用(回合结束即返回结果,定时唤醒无人接收)。"""
        assert sleep_timer_available(_make_config(is_sub=True, keep_alive=False)) is False

    def test_async_subagent_available(self):
        """异步子代理可用(keep_alive 驻留可被唤醒)。"""
        assert sleep_timer_available(_make_config(is_sub=True, keep_alive=True)) is True


class TestSleepTimerRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_two(self):
        """默认返回 sleep_timer 和 wait 两个工具。"""
        result = get_tools()
        assert [t.name for t in result] == [sleep_timer.name, wait.name]

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())

    def test_main_agent_get_tools(self):
        """主 agent 返回两个工具,无不可用记录。"""
        config = _make_config(is_sub=False)
        result = get_tools(config)
        assert [t.name for t in result] == [sleep_timer.name, wait.name]
        assert config.unavailable_tool_reasons == {}

    def test_sync_subagent_get_tools(self):
        """同步子代理只剩 wait,并记录 sleep_timer 不可用原因。"""
        config = _make_config(is_sub=True, keep_alive=False)
        result = get_tools(config)
        assert [t.name for t in result] == [wait.name]
        assert sleep_timer.name in config.unavailable_tool_reasons

    def test_async_subagent_get_tools(self):
        """异步子代理返回两个工具,清除不可用记录。"""
        config = _make_config(is_sub=True, keep_alive=True)
        config.unavailable_tool_reasons[sleep_timer.name] = "旧原因"
        result = get_tools(config)
        assert [t.name for t in result] == [sleep_timer.name, wait.name]
        assert config.unavailable_tool_reasons == {}


class TestSleepTimer:
    """sleep_timer 参数校验测试。"""

    @pytest.mark.asyncio
    async def test_invalid_seconds_zero(self):
        """秒数为 0 返回错误。"""
        result = await sleep_timer(seconds=0, tool_runtime=ToolRuntime())
        assert "错误" in result or "1-3600" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_negative(self):
        """秒数为负返回错误。"""
        result = await sleep_timer(seconds=-1, tool_runtime=ToolRuntime())
        assert "错误" in result or "1-3600" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_too_large(self):
        """秒数超过 3600 返回错误。"""
        result = await sleep_timer(seconds=3601, tool_runtime=ToolRuntime())
        assert "错误" in result or "1-3600" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.sleep.asyncio")
    async def test_valid_seconds(self, mock_asyncio):
        """有效秒数返回确认消息。"""
        mock_asyncio.create_task = _fake_create_task
        result = await sleep_timer(seconds=30, tool_runtime=ToolRuntime())
        assert "30" in result
        assert "唤醒" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.sleep.asyncio")
    async def test_with_name(self, mock_asyncio):
        """带名称参数。"""
        mock_asyncio.create_task = _fake_create_task
        result = await sleep_timer(seconds=10, name="等待服务启动", tool_runtime=ToolRuntime())
        assert "等待服务启动" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.sleep.asyncio")
    async def test_async_subagent_allowed(self, mock_asyncio):
        """异步子代理可正常设置唤醒。"""
        mock_asyncio.create_task = _fake_create_task
        config = _make_config(is_sub=True, keep_alive=True)
        result = await sleep_timer(seconds=30, tool_runtime=ToolRuntime(config=config))
        assert "30" in result
        assert "唤醒" in result


class TestWait:
    """wait 参数校验测试。"""

    @pytest.mark.asyncio
    async def test_invalid_seconds_zero(self):
        """秒数为 0 返回错误。"""
        result = await wait(seconds=0, tool_runtime=ToolRuntime())
        assert "错误" in result or "1-30" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_too_large(self):
        """秒数超过 30 返回错误。"""
        result = await wait(seconds=31, tool_runtime=ToolRuntime())
        assert "错误" in result or "1-30" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_negative(self):
        """秒数为负返回错误。"""
        result = await wait(seconds=-5, tool_runtime=ToolRuntime())
        assert "错误" in result or "1-30" in result

    @pytest.mark.asyncio
    async def test_sync_subagent_hint(self):
        """同步子代理超限提示交回父 agent,不推荐 sleep_timer。"""
        config = _make_config(is_sub=True, keep_alive=False)
        result = await wait(seconds=31, tool_runtime=ToolRuntime(config=config))
        assert "同步子代理" in result
        assert "交回父" in result

    @pytest.mark.asyncio
    async def test_async_subagent_hint(self):
        """异步子代理超限提示改用 sleep_timer。"""
        config = _make_config(is_sub=True, keep_alive=True)
        result = await wait(seconds=31, tool_runtime=ToolRuntime(config=config))
        assert sleep_timer.name in result
