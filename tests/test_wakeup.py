"""utils/wakeup.py 纯逻辑测试。

只测不依赖 TUI/WeChat/网络的部分:
- wake_agent 的 None 防御与队列注入分支
- _needs_drain 各运行模式判定
- mark_draining 标志读写
- _last_assistant_message 提取
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from uniclaw.agent import AgentStatus
from uniclaw.config import RunMode
from uniclaw.utils.wakeup import (
    _last_assistant_message,
    _needs_drain,
    mark_draining,
    wake_agent,
)


def _make_config(run_mode=RunMode.CONSOLE, is_wechat=False, agent=None):
    return SimpleNamespace(
        run_mode=run_mode,
        is_wechat=is_wechat,
        current_agent=agent,
    )


def _make_task(status_value, future_done=True):
    """构造带 status/future/user_queue 的假 AgentTask。"""
    future = MagicMock()
    future.done.return_value = future_done
    return SimpleNamespace(
        status=status_value,
        future=future,
        user_queue=asyncio.Queue(),
        cancel_event=asyncio.Event(),
        session=SimpleNamespace(get_assistant_messages=lambda separator="\n": []),
    )


class TestWakeAgentGuards:
    """wake_agent 防御分支。"""

    async def test_none_config(self):
        assert await wake_agent("msg", None) is False

    async def test_none_agent(self):
        config = _make_config(agent=None)
        assert await wake_agent("msg", config) is False

    async def test_exception_returns_false(self):
        """内部异常时返回 False 而非抛出。"""
        task = _make_task(AgentStatus.RUNNING)
        task.user_queue = None  # put_nowait 将抛 AttributeError
        config = _make_config(agent=task)
        assert await wake_agent("msg", config) is False


class TestWakeAgentQueueInjection:
    """wake_agent 运行中 agent 的队列注入分支。"""

    async def test_running_agent_gets_message(self):
        """RUNNING 状态 → 注入 user_queue,不启动新 agent。"""
        task = _make_task(AgentStatus.RUNNING)
        config = _make_config(agent=task)
        assert await wake_agent("hello", config) is True
        assert task.user_queue.get_nowait() == "hello"

    async def test_waiting_agent_gets_message(self):
        task = _make_task(AgentStatus.WAITING)
        config = _make_config(agent=task)
        assert await wake_agent("hello", config) is True
        assert task.user_queue.qsize() == 1

    async def test_pending_with_alive_future_gets_message(self):
        """PENDING 且 future 存活(进程内未启动但已排队)→ 注入队列。"""
        task = _make_task(AgentStatus.PENDING, future_done=False)
        config = _make_config(agent=task)
        assert await wake_agent("hello", config) is True
        assert task.user_queue.qsize() == 1

    async def test_completed_agent_starts_new_run(self):
        """COMPLETED → 不注入队列,走 start_agent 重新进入循环。"""
        task = _make_task(AgentStatus.COMPLETED)
        config = _make_config(agent=task)
        with patch("uniclaw.agent.MultiAgent") as mock_ma:
            mock_ma.get_instance.return_value.start_agent.return_value = task
            assert await wake_agent("hello", config) is True
            mock_ma.get_instance.return_value.start_agent.assert_called_once()
        assert task.user_queue.qsize() == 0


class TestNeedsDrain:
    """_needs_drain 各模式判定。"""

    def test_console_active_tui_session(self, monkeypatch):
        """TUI 活跃会话不需要后台消费。"""
        monkeypatch.setattr(
            "uniclaw.utils.wakeup._is_active_tui_session", lambda config: True
        )
        config = _make_config(run_mode=RunMode.CONSOLE)
        assert _needs_drain(config) is False

    def test_console_inactive_session(self, monkeypatch):
        """TUI 非活跃会话(如后台任务)需要消费。"""
        monkeypatch.setattr(
            "uniclaw.utils.wakeup._is_active_tui_session", lambda config: False
        )
        config = _make_config(run_mode=RunMode.CONSOLE)
        assert _needs_drain(config) is True

    def test_wechat_no_draining(self):
        """WeChat(WEBUI 运行模式)且 _collect_response 未运行 → 需要消费。"""
        task = _make_task(AgentStatus.IDLE if hasattr(AgentStatus, "IDLE") else "idle")
        config = _make_config(run_mode=RunMode.WEBUI, is_wechat=True, agent=task)
        assert _needs_drain(config) is True

    def test_wechat_already_draining(self):
        """WeChat 已有内联消费者 → 不需要。"""
        task = _make_task("idle")
        mark_draining(task, True)
        config = _make_config(run_mode=RunMode.WEBUI, is_wechat=True, agent=task)
        assert _needs_drain(config) is False

    def test_wechat_flag_missing_defaults_to_drain(self):
        """无 _wechat_draining 标志的旧任务对象默认需要消费。"""
        task = SimpleNamespace(status="idle")  # 无 _wechat_draining 属性
        config = _make_config(run_mode=RunMode.WEBUI, is_wechat=True, agent=task)
        assert _needs_drain(config) is True

    def test_webui_never_drains(self):
        """WebUI 有自己的桥接,永不后台消费。"""
        config = _make_config(run_mode=RunMode.WEBUI)
        assert _needs_drain(config) is False


class TestMarkDraining:
    def test_set_and_clear(self):
        task = _make_task("IDLE")
        mark_draining(task)
        assert task._wechat_draining is True
        mark_draining(task, False)
        assert task._wechat_draining is False


class TestLastAssistantMessage:
    def test_returns_last(self):
        task = SimpleNamespace(
            session=SimpleNamespace(
                get_assistant_messages=lambda separator="\n": ["第一条", "第二条"]
            )
        )
        assert _last_assistant_message(task) == "第二条"

    def test_returns_none_when_empty(self):
        task = SimpleNamespace(
            session=SimpleNamespace(get_assistant_messages=lambda separator="\n": [])
        )
        assert _last_assistant_message(task) is None
