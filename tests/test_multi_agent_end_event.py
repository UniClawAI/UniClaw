"""multi_agent.py EndEvent 发送顺序测试。

覆盖 commit 5aed767 修复的 notify_parent 子代理 EndEvent 发送时机问题:
- notify_parent=True 时, _run_cleanup 跳过 EndEvent(depth=0), 由 _run_proc 发送
- notify_parent=False 时, _run_cleanup 正常补发 EndEvent(depth=0)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.agent import MultiAgent, AgentStatus, EndEvent


@pytest.fixture
def mock_multi_agent():
    """返回一个可测试的 MultiAgent 实例(绕过 __new__ 单例)。"""
    obj = MultiAgent.__new__(MultiAgent)
    obj.send_event_to_user = AsyncMock()
    return obj


def _make_config(parent_status=AgentStatus.COMPLETED, has_running=False, depth=1):
    """构造 mock AppConfig(子代理)。"""
    config = MagicMock()
    config.is_sub = True
    config.depth = depth
    config.root_dir = "/tmp"

    # parent_config
    parent_config = MagicMock()
    parent_config.has_running_subs.return_value = has_running
    parent_task = MagicMock()
    parent_task.status = parent_status
    parent_config.current_agent = parent_task
    config.parent_config = parent_config

    # sub_configs.remove
    parent_config.sub_configs = MagicMock()
    return config, parent_config


def _make_task(notify_parent=False, status=AgentStatus.COMPLETED):
    """构造 mock AgentTask。"""
    task = MagicMock()
    task.notify_parent = notify_parent
    task.status = status
    task.worktree_path = None
    task.name = "test-sub"
    return task


class TestRunCleanupNotifyParent:
    """_run_cleanup 中 notify_parent 控制 EndEvent(depth=0) 发送的测试。"""

    @pytest.mark.asyncio
    async def test_notify_parent_false_sends_end_event_to_parent(
        self, mock_multi_agent
    ):
        """notify_parent=False 时, _run_cleanup 补发 EndEvent(depth=0) 给父代理。"""
        config, parent_config = _make_config(
            parent_status=AgentStatus.COMPLETED, has_running=False
        )
        task = _make_task(notify_parent=False)

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mock_multi_agent._run_cleanup(task, config)

        # 应该发送两次: depth=1 给自己, depth=0 给父代理
        assert mock_multi_agent.send_event_to_user.call_count == 2
        calls = mock_multi_agent.send_event_to_user.call_args_list
        # 第一次: EndEvent(depth=1) 发给自己
        assert isinstance(calls[0][0][0], EndEvent)
        assert calls[0][0][0].depth == 1
        assert calls[0][0][1] is config
        # 第二次: EndEvent(depth=0) 发给父代理
        assert isinstance(calls[1][0][0], EndEvent)
        assert calls[1][0][0].depth == 0
        assert calls[1][0][1] is parent_config

    @pytest.mark.asyncio
    async def test_notify_parent_true_skips_end_event_to_parent(
        self, mock_multi_agent
    ):
        """notify_parent=True 时, _run_cleanup 不发送 EndEvent(depth=0) 给父代理。

        此时由 _run_proc 在 _notify_parent 之后发送, 避免 bridge 提前退出。
        """
        config, parent_config = _make_config(
            parent_status=AgentStatus.COMPLETED, has_running=False
        )
        task = _make_task(notify_parent=True)

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mock_multi_agent._run_cleanup(task, config)

        # 只发送一次: depth=1 给自己, 不发送 depth=0 给父代理
        assert mock_multi_agent.send_event_to_user.call_count == 1
        call = mock_multi_agent.send_event_to_user.call_args_list[0]
        assert isinstance(call[0][0], EndEvent)
        assert call[0][0].depth == 1

    @pytest.mark.asyncio
    async def test_parent_running_skips_end_event(self, mock_multi_agent):
        """父代理仍在运行时, 不发送 EndEvent(depth=0)。"""
        config, parent_config = _make_config(
            parent_status=AgentStatus.RUNNING, has_running=False
        )
        task = _make_task(notify_parent=False)

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mock_multi_agent._run_cleanup(task, config)

        assert mock_multi_agent.send_event_to_user.call_count == 1
        assert mock_multi_agent.send_event_to_user.call_args_list[0][0][0].depth == 1

    @pytest.mark.asyncio
    async def test_parent_pending_skips_end_event(self, mock_multi_agent):
        """父代理处于 PENDING 状态时, 不发送 EndEvent(depth=0)。"""
        config, parent_config = _make_config(
            parent_status=AgentStatus.PENDING, has_running=False
        )
        task = _make_task(notify_parent=False)

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mock_multi_agent._run_cleanup(task, config)

        assert mock_multi_agent.send_event_to_user.call_count == 1

    @pytest.mark.asyncio
    async def test_has_running_subs_skips_end_event(self, mock_multi_agent):
        """父代理仍有其他运行中子代理时, 不发送 EndEvent(depth=0)。"""
        config, parent_config = _make_config(
            parent_status=AgentStatus.COMPLETED, has_running=True
        )
        task = _make_task(notify_parent=False)

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mock_multi_agent._run_cleanup(task, config)

        assert mock_multi_agent.send_event_to_user.call_count == 1

    @pytest.mark.asyncio
    async def test_parent_failed_sends_end_event(self, mock_multi_agent):
        """父代理失败时(notify_parent=False), 仍补发 EndEvent(depth=0)。"""
        config, parent_config = _make_config(
            parent_status=AgentStatus.FAILED, has_running=False
        )
        task = _make_task(notify_parent=False)

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mock_multi_agent._run_cleanup(task, config)

        assert mock_multi_agent.send_event_to_user.call_count == 2
        assert mock_multi_agent.send_event_to_user.call_args_list[1][0][0].depth == 0

    @pytest.mark.asyncio
    async def test_no_parent_config_skips_parent_end_event(self, mock_multi_agent):
        """无父代理配置时, 只发送自己的 EndEvent。"""
        config = MagicMock()
        config.is_sub = True
        config.depth = 1
        config.root_dir = "/tmp"
        config.parent_config = None
        task = _make_task(notify_parent=False)

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mock_multi_agent._run_cleanup(task, config)

        assert mock_multi_agent.send_event_to_user.call_count == 1
        assert mock_multi_agent.send_event_to_user.call_args_list[0][0][0].depth == 1
