"""异步定时器测试 — 覆盖 sleep_timer 和 wait 的参数校验。"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from uniclaw.tools.sleep import sleep_timer, wait, get_tools, get_all_tools


def _fake_create_task(coro):
    """模拟 asyncio.create_task:关闭协程避免 'never awaited' 警告。"""
    coro.close()
    return MagicMock()


class TestSleepTimerRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_two(self):
        """返回 sleep_timer 和 wait 两个工具。"""
        result = get_tools()
        assert len(result) == 2

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())


class TestSleepTimer:
    """sleep_timer 参数校验测试。"""

    @pytest.mark.asyncio
    async def test_invalid_seconds_zero(self):
        """秒数为 0 返回错误。"""
        result = await sleep_timer(seconds=0)
        assert "错误" in result or "1-3600" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_negative(self):
        """秒数为负返回错误。"""
        result = await sleep_timer(seconds=-1)
        assert "错误" in result or "1-3600" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_too_large(self):
        """秒数超过 3600 返回错误。"""
        result = await sleep_timer(seconds=3601)
        assert "错误" in result or "1-3600" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.sleep.asyncio")
    async def test_valid_seconds(self, mock_asyncio):
        """有效秒数返回确认消息。"""
        mock_asyncio.create_task = _fake_create_task
        result = await sleep_timer(seconds=30)
        assert "30" in result
        assert "唤醒" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.sleep.asyncio")
    async def test_with_name(self, mock_asyncio):
        """带名称参数。"""
        mock_asyncio.create_task = _fake_create_task
        result = await sleep_timer(seconds=10, name="等待服务启动")
        assert "等待服务启动" in result


class TestWait:
    """wait 参数校验测试。"""

    @pytest.mark.asyncio
    async def test_invalid_seconds_zero(self):
        """秒数为 0 返回错误。"""
        result = await wait(seconds=0)
        assert "错误" in result or "1-30" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_too_large(self):
        """秒数超过 30 返回错误。"""
        result = await wait(seconds=31)
        assert "错误" in result or "1-30" in result

    @pytest.mark.asyncio
    async def test_invalid_seconds_negative(self):
        """秒数为负返回错误。"""
        result = await wait(seconds=-5)
        assert "错误" in result or "1-30" in result
