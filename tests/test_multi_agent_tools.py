"""多 Agent 工具测试 — 覆盖 subagent_* 工具的调用逻辑。"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.agent import AgentStatus
from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.multi_agent.tools import (
    subagent_send_to_child,
    subagent_send_to_parent,
    subagent_close,
    subagent_check_result,
    subagent_list_tasks,
    subagent_list_definitions,
    subagent_get_definition,
    get_tools,
    get_all_tools,
    get_sub_agent_system_prompt,
)


def _make_task(
    task_id="task-1",
    name="test-agent",
    status="running",
    finished=False,
    keep_alive=False,
):
    """构造一个够用的 AgentTask 替身。"""
    task = MagicMock()
    task.id = task_id
    task.name = name
    task.status = status
    task.closed = False
    task.is_finished = finished
    task.keep_alive = keep_alive
    task.worktree_branch = None
    task.result_read_index = 0
    task.result = None
    task.session.get_assistant_messages.return_value = []
    return task


@pytest.fixture
def mock_mgr():
    """返回模拟的 MultiAgent 实例。"""
    mgr = MagicMock()
    mgr.send_message.return_value = True
    mgr.close_agent.return_value = "closed"
    mgr.list_tasks.return_value = []
    mgr.id2AgentTask = {}
    return mgr


def _patch_get_instance(mock_mgr):
    """patch uniclaw.agent.MultiAgent.get_instance 返回 mock_mgr。"""
    return patch(
        "uniclaw.agent.MultiAgent.get_instance", return_value=mock_mgr
    )


class TestToolRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_eight(self):
        """get_tools 返回 8 个工具。"""
        result = get_tools()
        assert len(result) == 8

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())

    def test_tool_names(self):
        """工具名称齐全。"""
        names = [t.name for t in get_all_tools()]
        assert "subagent_create" in names
        assert "subagent_send_to_child" in names
        # 子代理专属,不进通用列表(主 agent 经 get_tools/search_tools 均不可见)
        assert "subagent_send_to_parent" not in names
        assert "subagent_close" in names
        assert "subagent_check_result" in names
        assert "subagent_list_tasks" in names
        assert "subagent_discuss" in names
        assert "subagent_get_definition" in names
        assert "subagent_list_definitions" in names


class TestSendToChild:
    """subagent_send_to_child 测试 —— 只能触达当前 agent 自己的子代理。"""

    @staticmethod
    def _rt(*tasks):
        """构造父 agent 的 tool_runtime, sub_configs 只装自己的子代理。"""
        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=t) for t in tasks]
        return ToolRuntime(config=config)

    @pytest.mark.asyncio
    async def test_send_success(self, mock_mgr):
        """发送成功。"""
        mock_mgr.send_message.return_value = True
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_to_child(
                "task-1", "hello", tool_runtime=self._rt(_make_task())
            )
        assert "已排队" in result

    @pytest.mark.asyncio
    async def test_send_by_name(self, mock_mgr):
        """按名称解析后发送成功。"""
        mock_mgr.send_message.return_value = True
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_to_child(
                "worker", "hello", tool_runtime=self._rt(_make_task(name="worker"))
            )
        assert "已排队" in result
        assert "worker" in result

    @pytest.mark.asyncio
    async def test_agent_not_found(self, mock_mgr):
        """子代理不存在,报错列出当前 agent 可用的 name/ID。"""
        mock_mgr.send_message.return_value = False
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_to_child(
                "nonexistent", "hello", tool_runtime=self._rt(_make_task())
            )
        assert "未找到" in result
        assert "可用" in result

    @pytest.mark.asyncio
    async def test_agent_not_running(self, mock_mgr):
        """智能体未运行。"""
        mock_mgr.send_message.return_value = False
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_to_child(
                "task-1",
                "hello",
                tool_runtime=self._rt(_make_task(status="stopped")),
            )
        assert "未运行" in result

    @pytest.mark.asyncio
    async def test_cannot_reach_other_agents_children(self, mock_mgr):
        """不能越过边界发给别的 agent 的任务(全局存在但不是自己的子代理)。"""
        outsider = _make_task(task_id="other-1", name="outsider")
        mock_mgr.id2AgentTask = {"other-1": outsider}  # 全局查表能看到
        mock_mgr.send_message.return_value = True

        with _patch_get_instance(mock_mgr):
            result = await subagent_send_to_child(
                "other-1", "hello", tool_runtime=self._rt()  # 自己没有子代理
            )

        assert "未找到" in result or "没有子智能体" in result
        mock_mgr.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_name_multiple_children(self, mock_mgr):
        """同名多个子代理时要求改用 ID。"""
        t1 = _make_task(task_id="task-1", name="worker")
        t2 = _make_task(task_id="task-2", name="worker")
        mock_mgr.send_message.return_value = True
        with _patch_get_instance(mock_mgr):
            result = await subagent_send_to_child(
                "worker", "hello", tool_runtime=self._rt(t1, t2)
            )
        assert "同名" in result
        assert "task-1" in result and "task-2" in result


class TestSendToParent:
    """subagent_send_to_parent 测试。"""

    @staticmethod
    def _make_config(notify_parent: bool = True):
        """构造子代理 config(notify_parent 标志区分异步/同步子代理)。"""
        config = MagicMock()
        parent_task = MagicMock()
        parent_task.name = "parent"
        config.parent_config = MagicMock()
        config.parent_agent = parent_task
        task = MagicMock()
        task.name = "child"
        task.id = "task-1"
        task.status = "running"
        task.notify_parent = notify_parent
        config.current_agent = task
        return config

    @pytest.mark.asyncio
    async def test_no_parent(self):
        """非子智能体(无父代理)时返回错误,不能假装发送成功。"""
        config = MagicMock()
        config.parent_config = None
        config.parent_agent = None
        result = await subagent_send_to_parent(
            "hello", tool_runtime=ToolRuntime(config=config)
        )
        assert "没有父智能体" in result
        assert "已发送" not in result
        assert result.startswith("[TOOL_ERROR]")

    @pytest.mark.asyncio
    async def test_root_agent_no_parent_task(self):
        """根 agent: parent_config 存在但没有父任务,返回错误而非假成功。"""
        config = MagicMock()
        config.parent_config = MagicMock()  # 形似有父 config
        config.parent_agent = None  # 但没有父任务
        task = MagicMock()
        task.notify_parent = True
        config.current_agent = task
        with patch(
            "uniclaw.utils.wakeup.wake_agent", new_callable=AsyncMock
        ) as mock_wake:
            result = await subagent_send_to_parent(
                "hello", tool_runtime=ToolRuntime(config=config)
            )
        assert "没有父智能体" in result
        assert "已发送" not in result
        assert mock_wake.await_count == 0

    @pytest.mark.asyncio
    async def test_self_parent_rejected(self):
        """自指 parent(父任务是自己)时返回错误,不能把消息发给自己冒充成功。"""
        config = MagicMock()
        task = MagicMock()
        task.notify_parent = True
        config.current_agent = task
        config.parent_config = MagicMock()
        config.parent_agent = task  # 自指
        with patch(
            "uniclaw.utils.wakeup.wake_agent", new_callable=AsyncMock
        ) as mock_wake:
            result = await subagent_send_to_parent(
                "hello", tool_runtime=ToolRuntime(config=config)
            )
        assert "没有父智能体" in result
        assert "已发送" not in result
        assert mock_wake.await_count == 0

    @pytest.mark.asyncio
    async def test_sync_subagent_rejected(self):
        """同步子代理(父代理阻塞等待)不允许发送。"""
        config = self._make_config(notify_parent=False)
        with patch(
            "uniclaw.utils.wakeup.wake_agent", new_callable=AsyncMock
        ) as mock_wake:
            result = await subagent_send_to_parent(
                "hello", tool_runtime=ToolRuntime(config=config)
            )
        assert "同步子智能体" in result
        assert mock_wake.await_count == 0

    @pytest.mark.asyncio
    async def test_send_success(self):
        """异步子代理有父代理时调用 wake_agent 送达。"""
        config = self._make_config(notify_parent=True)
        with patch(
            "uniclaw.utils.wakeup.wake_agent", new_callable=AsyncMock
        ) as mock_wake:
            mock_wake.return_value = True
            result = await subagent_send_to_parent(
                "hello", tool_runtime=ToolRuntime(config=config)
            )
        assert "已发送" in result
        assert mock_wake.await_count == 1
        # 注入的消息包含发送者标识与原始内容
        sent_text = mock_wake.await_args.args[0]
        assert "[child_agent]" in sent_text
        assert "task-1" in sent_text
        assert "hello" in sent_text

    @pytest.mark.asyncio
    async def test_wake_failed(self):
        """wake_agent 返回 False 时返回错误。"""
        config = self._make_config(notify_parent=True)
        with patch(
            "uniclaw.utils.wakeup.wake_agent", new_callable=AsyncMock
        ) as mock_wake:
            mock_wake.return_value = False
            result = await subagent_send_to_parent(
                "hello", tool_runtime=ToolRuntime(config=config)
            )
        assert "失败" in result


class TestClose:
    """subagent_close 测试 —— 只能关闭当前 agent 自己的子代理。"""

    @staticmethod
    def _rt(*tasks):
        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=t) for t in tasks]
        return ToolRuntime(config=config)

    @pytest.mark.asyncio
    async def test_close_success(self, mock_mgr):
        """首次关闭: 明确说明已关闭并从列表移除。"""
        task = _make_task()

        def _close(t):
            t.closed = True
            return "closed"

        mock_mgr.close_agent.side_effect = _close
        with _patch_get_instance(mock_mgr):
            result = await subagent_close(
                "task-1", tool_runtime=self._rt(task)
            )
        assert "已关闭" in result
        assert "移除" in result
        assert task.closed is True

    @pytest.mark.asyncio
    async def test_close_by_name(self, mock_mgr):
        """按名称关闭(与 check_result 解析策略一致)。"""
        task = _make_task(name="worker")
        mock_mgr.close_agent.return_value = "closed"
        with _patch_get_instance(mock_mgr):
            result = await subagent_close("worker", tool_runtime=self._rt(task))
        assert "已关闭" in result
        assert "worker" in result

    @pytest.mark.asyncio
    async def test_close_idempotent(self, mock_mgr):
        """重复 close 明确提示"已关闭过",不假装再次成功。"""
        task = _make_task()
        task.closed = True
        with _patch_get_instance(mock_mgr):
            result = await subagent_close("task-1", tool_runtime=self._rt(task))
        assert "已关闭过" in result
        assert "已关闭子智能体" not in result
        # 不应再次发起关闭信号
        mock_mgr.close_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_finished_task(self, mock_mgr):
        """关闭已结束任务: 报告终态并移除,不假称发送了关闭信号。"""
        task = _make_task(status="failed", finished=True)
        mock_mgr.close_agent.return_value = "closed"
        with _patch_get_instance(mock_mgr):
            result = await subagent_close("task-1", tool_runtime=self._rt(task))
        assert "已结束" in result
        assert "failed" in result
        assert "移除" in result

    @pytest.mark.asyncio
    async def test_close_not_found(self, mock_mgr):
        """关闭不存在的智能体: 报错列出当前 agent 可用 name/ID。"""
        mock_mgr.close_agent.return_value = "closed"
        with _patch_get_instance(mock_mgr):
            result = await subagent_close(
                "nonexistent", tool_runtime=self._rt(_make_task())
            )
        assert "未找到" in result
        assert "可用" in result

    @pytest.mark.asyncio
    async def test_cannot_close_other_agents_child(self, mock_mgr):
        """不能关掉别的 agent 的子代理(全局存在但不是自己的)。"""
        outsider = _make_task(task_id="other-1", name="outsider")
        mock_mgr.id2AgentTask = {"other-1": outsider}

        with _patch_get_instance(mock_mgr):
            result = await subagent_close("other-1", tool_runtime=self._rt())

        assert "未找到" in result or "没有子智能体" in result
        mock_mgr.close_agent.assert_not_called()  # 绝不能真的关掉
        assert outsider.closed is False

    @pytest.mark.asyncio
    async def test_close_detaches_from_sub_configs(self, mock_mgr):
        """close 后任务从调用方 sub_configs 移除,list_tasks 不再残留。"""
        task = _make_task(status="failed")
        mock_mgr.id2AgentTask = {"task-1": task}
        mock_mgr.close_agent.return_value = "closed"

        def _detach(owner_config, t):
            if owner_config is not None:
                owner_config.sub_configs = [
                    s for s in owner_config.sub_configs if s.current_agent is not t
                ]

        mock_mgr.detach_sub_config.side_effect = _detach

        sub = MagicMock()
        sub.current_agent = task
        config = MagicMock()
        config.sub_configs = [sub]
        with _patch_get_instance(mock_mgr):
            await subagent_close("task-1", tool_runtime=ToolRuntime(config=config))
            listed = await subagent_list_tasks(tool_runtime=ToolRuntime(config=config))
        assert config.sub_configs == []
        assert "test-agent" not in listed

    @pytest.mark.asyncio
    async def test_close_twice_reports_not_found(self, mock_mgr):
        """重复 close 报「未找到」—— 关闭即移出 sub_configs,不再记忆历史。"""
        task = _make_task(task_id="task-1", name="worker", status="running")

        def _close(t):
            t.closed = True
            return "closed"

        mock_mgr.close_agent.side_effect = _close

        def _detach(owner_config, t):
            if owner_config is not None:
                owner_config.sub_configs = [
                    s for s in owner_config.sub_configs if s.current_agent is not t
                ]

        mock_mgr.detach_sub_config.side_effect = _detach

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=task)]

        with _patch_get_instance(mock_mgr):
            first = await subagent_close(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )
            second = await subagent_close(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )

        assert "已关闭" in first
        # 关闭后已移出 sub_configs,再次 close 找不到它(列表空时报「没有子智能体」)
        assert "未找到" in second or "没有子智能体" in second
        assert second.startswith("[TOOL_ERROR]")  # 不假装成功
        # 不应重复发起关闭信号
        assert mock_mgr.close_agent.call_count == 1

    @pytest.mark.asyncio
    async def test_close_never_existed_reports_not_found(self, mock_mgr):
        """从未存在报「未找到」。"""
        other = _make_task(task_id="other-1", name="other")
        mock_mgr.close_agent.return_value = "closed"

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=other)]

        with _patch_get_instance(mock_mgr):
            result = await subagent_close(
                "nonexistent", tool_runtime=ToolRuntime(config=config)
            )

        assert "未找到" in result
        mock_mgr.close_agent.assert_not_called()


class TestCheckResult:
    """subagent_check_result 测试 —— 只能查询当前 agent 自己的子代理。"""

    @staticmethod
    def _rt(*tasks):
        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=t) for t in tasks]
        return ToolRuntime(config=config)

    @pytest.mark.asyncio
    async def test_check_not_found(self, mock_mgr):
        """查询不存在的任务: 报错列出当前 agent 可用 name/ID。"""
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "nonexistent", tool_runtime=self._rt(_make_task())
            )
        assert "未找到" in result
        assert "可用" in result

    @pytest.mark.asyncio
    async def test_check_found(self, mock_mgr):
        """查询存在的任务(按 ID)。"""
        mock_task = _make_task()
        mock_task.session.get_assistant_messages.return_value = ["结果1", "结果2"]
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "task-1", tool_runtime=self._rt(mock_task)
            )
        assert "状态:" in result
        assert "名称:" in result
        assert "结果1" in result

    @pytest.mark.asyncio
    async def test_check_found_by_name(self, mock_mgr):
        """按名称查询(与 close 解析策略一致)。"""
        mock_task = _make_task(name="test-agent")
        mock_task.session.get_assistant_messages.return_value = ["结果1"]
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "test-agent", tool_runtime=self._rt(mock_task)
            )
        assert "名称:test-agent" in result
        assert "结果1" in result

    @pytest.mark.asyncio
    async def test_check_full(self, mock_mgr):
        """full=True 返回完整历史。"""
        mock_task = _make_task()
        mock_task.session.get_assistant_messages.return_value = ["结果1", "结果2"]
        mock_task.result = "最终结果"
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "task-1", full=True, tool_runtime=self._rt(mock_task)
            )
        assert "最终结果" in result or "结果1" in result

    @pytest.mark.asyncio
    async def test_cannot_read_other_agents_child(self, mock_mgr):
        """不能越界读取别的 agent 的子代理结果。"""
        outsider = _make_task(task_id="other-1", name="outsider")
        outsider.session.get_assistant_messages.return_value = ["别人的结果"]
        mock_mgr.id2AgentTask = {"other-1": outsider}

        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result("other-1", tool_runtime=self._rt())

        assert "未找到" in result or "没有子智能体" in result
        assert "别人的结果" not in result


class TestResultLifecycle:
    """子代理完成后的生命周期 —— 通知引导的读取路径必须有效,读取后回收。"""

    @staticmethod
    def _rt(*tasks):
        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=t) for t in tasks]
        return ToolRuntime(config=config)

    @staticmethod
    def _detach_side_effect():
        """让 mock_mgr.detach_sub_config 真正从 sub_configs 移除,便于断言回收。"""

        def _detach(owner_config, t):
            if owner_config is not None:
                owner_config.sub_configs = [
                    s for s in owner_config.sub_configs if s.current_agent is not t
                ]

        return _detach

    @pytest.mark.asyncio
    async def test_notification_guided_read_still_works(self, mock_mgr):
        """缺陷1核心: completed 任务在收到 [child_agent] 通知后,按通知里的 ID
        check_result 必须能读到最终输出(不能被即时回收)。"""
        task = _make_task(task_id="task-1", name="worker", status="completed", finished=True)
        task.session.get_assistant_messages.return_value = ["最终输出"]
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()

        rt = self._rt(task)
        # 通知引导的读取路径:按任务 ID 查询
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result("task-1", tool_runtime=rt)

        assert "最终输出" in result
        assert "没有子智能体" not in result

    @pytest.mark.asyncio
    async def test_check_finished_keeps_task(self, mock_mgr):
        """已结束的任务读取后**也不**回收 —— 纯读,回收只由 subagent_close 做。

        读操作不该有副作用: 读了就消失会让人误以为子代理结束了,
        也无法"读完一轮 → 再派活"地多轮协作。
        """
        task = _make_task(task_id="task-1", name="worker", status="completed", finished=True)
        task.session.get_assistant_messages.return_value = ["最终输出"]
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=task)]
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )

        assert "最终输出" in result
        assert "自动回收" not in result
        assert len(config.sub_configs) == 1  # 仍在, 等 subagent_close
        mock_mgr.detach_sub_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_running_keeps_task(self, mock_mgr):
        """运行中的任务读取后保留在 sub_configs,可继续交互。"""
        task = _make_task(task_id="task-1", name="worker", status="running", finished=False)
        task.session.get_assistant_messages.return_value = ["中间输出"]
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=task)]
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )

        assert "中间输出" in result
        assert "自动回收" not in result
        assert len(config.sub_configs) == 1  # 未回收

    @pytest.mark.asyncio
    async def test_check_keepalive_completed_not_recycled(self, mock_mgr):
        """keep_alive 驻留(future 存活)的子代理读取**不**回收。

        回合结果维度(status)与存活维度(is_finished)正交: keep_alive 回合
        结束时 status=completed 但 future 仍存活,它存在的意义就是等下一条
        消息(多轮对话 / sleep_timer 唤醒)。若读取即回收, send_to_child
        只能走一次就失效了。要结束驻留请显式 subagent_close。
        """
        task = _make_task(
            task_id="task-1",
            name="worker",
            status="completed",
            finished=False,
            keep_alive=True,
        )
        task.session.get_assistant_messages.return_value = ["轮1输出"]
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()
        mock_mgr.close_agent.return_value = "closed"

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=task)]
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )

        assert "轮1输出" in result
        assert "自动回收" not in result
        assert config.sub_configs != []  # 仍在, 可继续 send_to_child
        mock_mgr.detach_sub_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_then_send_round_trip(self, mock_mgr):
        """多轮往返: 读取一轮结果后仍能 send_to_child 发起下一轮。

        这是 keep_alive 驻留的核心用途 —— 若读取即回收, send 只能走一次。
        """
        task = _make_task(
            task_id="task-1", name="worker", status="completed",
            finished=False, keep_alive=True,
        )
        task.session.get_assistant_messages.return_value = ["轮1输出"]
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()
        mock_mgr.send_message.return_value = True

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=task)]
        with _patch_get_instance(mock_mgr):
            r1 = await subagent_check_result(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )
            sent = await subagent_send_to_child(
                "task-1", "继续第二轮", tool_runtime=ToolRuntime(config=config)
            )

        assert "轮1输出" in r1
        assert "已排队" in sent  # 读完还能发 —— 子代理没被回收

    @pytest.mark.asyncio
    async def test_check_terminal_read_keeps_task(self, mock_mgr):
        """任何终态读取后都保留 —— 回收只由 subagent_close 做。"""
        for terminal in ("completed", "failed", "cancelled"):
            task = _make_task(
                task_id="task-1", name="worker", status=terminal, finished=True
            )
            task.session.get_assistant_messages.return_value = ["最终输出"]
            mock_mgr.detach_sub_config.reset_mock()
            mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()

            config = MagicMock()
            config.sub_configs = [MagicMock(current_agent=task)]
            with _patch_get_instance(mock_mgr):
                result = await subagent_check_result(
                    "task-1", tool_runtime=ToolRuntime(config=config)
                )

            assert "最终输出" in result, terminal
            assert "自动回收" not in result, terminal
            assert len(config.sub_configs) == 1, terminal
            mock_mgr.detach_sub_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_still_works_before_read(self, mock_mgr):
        """显式 close 是唯一的回收路径(读取不回收)。"""
        task = _make_task(task_id="task-1", name="worker", status="completed", finished=True)
        mock_mgr.close_agent.return_value = "closed"
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=task)]
        with _patch_get_instance(mock_mgr):
            result = await subagent_close(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )

        assert "已结束" in result
        assert config.sub_configs == []


class TestTurnCompletionStateMachine:
    """回合完成态迁移 —— status 表示本回合结果,keep_alive 驻留是另一维度。

    复现缺陷: keep_alive 子代理产出最终输出后状态卡在 waiting,
    通知/check_result/list_tasks 三处不一致,读取后也不回收。
    """

    @staticmethod
    def _rt(*tasks):
        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=t) for t in tasks]
        return ToolRuntime(config=config)

    @staticmethod
    def _detach_side_effect():
        def _detach(owner_config, t):
            if owner_config is not None:
                owner_config.sub_configs = [
                    s for s in owner_config.sub_configs if s.current_agent is not t
                ]

        return _detach

    @pytest.mark.asyncio
    async def test_run_cleanup_sets_completed_not_waiting(self):
        """_run_cleanup: keep_alive 子代理回合结束后 status=COMPLETED,不是 WAITING。

        keep_alive 驻留用 future 存活表达,不能覆盖'本回合已完成'这个事实。
        """
        from uniclaw.agent import MultiAgent, AgentStatus

        for keep_alive in (True, False):
            mgr = MultiAgent.__new__(MultiAgent)
            mgr.send_event_to_user = AsyncMock()
            config = MagicMock()
            config.is_sub = True
            config.depth = 1
            config.root_dir = "/tmp"
            config.parent_config = None
            task = MagicMock()
            task.status = AgentStatus.RUNNING
            task.keep_alive = keep_alive
            task.notify_parent = True
            task.worktree_path = None
            task.name = "worker"

            with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
                await mgr._run_cleanup(task, config)

            assert task.status == AgentStatus.COMPLETED, f"keep_alive={keep_alive}"

    @pytest.mark.asyncio
    async def test_notification_shows_completed(self, mock_mgr):
        """[child_agent] 通知的 状态 字段应为 completed(与 check_result/list_tasks 一致)。

        通过 start_sub_agent 的 _notify_message 闭包验证: _run_cleanup 先置
        COMPLETED,随后 _notify_parent 读到的状态就是 completed。
        """
        from uniclaw.agent import MultiAgent, AgentStatus

        mgr = MultiAgent.__new__(MultiAgent)
        mgr.send_event_to_user = AsyncMock()
        config = MagicMock()
        config.is_sub = True
        config.depth = 1
        config.root_dir = "/tmp"
        config.parent_config = None
        task = MagicMock()
        task.status = AgentStatus.RUNNING
        task.keep_alive = True
        task.notify_parent = False  # 跳过 wake_agent
        task.worktree_path = None
        task.name = "worker"

        with patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock):
            await mgr._run_cleanup(task, config)

        # 回合结束后 status 必须是 completed,通知里 f"状态: {task.status}" 才一致
        assert task.status == AgentStatus.COMPLETED
        notify_line = f"状态: {task.status}"
        assert notify_line == "状态: completed"

    @pytest.mark.asyncio
    async def test_three_surfaces_consistent(self, mock_mgr):
        """通知文案中的状态 == check_result 状态 == list_tasks 状态 == completed。"""
        task = _make_task(
            task_id="task-1",
            name="worker",
            status="completed",
            finished=False,
            keep_alive=True,
        )
        task.session.get_assistant_messages.return_value = ["最终输出"]
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()
        mock_mgr.close_agent.return_value = "closed"

        # list_tasks
        rt = self._rt(task)
        list_out = await subagent_list_tasks(tool_runtime=rt)
        assert "completed" in list_out

        # check_result(读取后回收,故 list_tasks 的断言放在前面)
        check_out = await subagent_check_result("task-1", tool_runtime=rt)
        assert "状态:completed" in check_out or "状态: completed" in check_out

        # 通知文案(_notify_message 的状态字段即 f"状态: {task.status}")
        assert f"状态: {task.status}" == "状态: completed"

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_send_to_child_completed_but_resident(self, mock_mgr):
        """已完成但驻留(未读取/未回收)的 keep_alive 子代理仍应能收消息。

        这正是 keep_alive 的用途: 回合结束后驻留等下一条消息。
        """
        task = _make_task(
            task_id="task-1",
            name="worker",
            status="completed",
            finished=False,
            keep_alive=True,
        )
        mock_mgr.send_message.return_value = True

        with _patch_get_instance(mock_mgr):
            result = await subagent_send_to_child(
                "task-1", "补充要求", tool_runtime=self._rt(task)
            )

        assert "已排队" in result
        mock_mgr.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_running_does_not_recycle(self, mock_mgr):
        """运行中读取不回收(还能继续 send_to_child 交互)。"""
        task = _make_task(
            task_id="task-1", name="worker", status="running", finished=False
        )
        task.session.get_assistant_messages.return_value = ["中间输出"]
        mock_mgr.detach_sub_config.side_effect = self._detach_side_effect()

        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=task)]
        with _patch_get_instance(mock_mgr):
            result = await subagent_check_result(
                "task-1", tool_runtime=ToolRuntime(config=config)
            )

        assert "中间输出" in result
        assert "自动回收" not in result
        assert len(config.sub_configs) == 1


class TestSendAndCloseResident:
    """send_message / close_agent 对'已完成但驻留'子代理的处理。"""

    @staticmethod
    def _fresh_mgr():
        from uniclaw.agent import MultiAgent

        mgr = MultiAgent.get_instance()
        mgr.id2AgentTask = {}
        return mgr

    @staticmethod
    def _live_task(status="completed", keep_alive=True):
        """真实 AgentTask + 存活 future(keep_alive 驻留)。"""
        from uniclaw.agent.types import AgentTask

        session = MagicMock()
        session.id = "test-resident"
        task = AgentTask(name="worker", prompt="p", session=session)
        task.status = status
        task.keep_alive = keep_alive
        future = MagicMock()
        future.done.return_value = False  # 驻留中: future 未完成
        task.future = future
        return task

    def test_send_message_completed_resident(self):
        """status=completed 但 future 存活(驻留)→ 可接收消息。"""
        mgr = self._fresh_mgr()
        task = self._live_task()
        mgr.id2AgentTask[task.id] = task
        assert mgr.send_message(task.id, "hello") is True
        assert task.user_queue.qsize() == 1

    def test_send_message_rejects_truly_finished(self):
        """future 已结束的 completed 任务不能再收消息。"""
        from uniclaw.agent import AgentStatus

        mgr = self._fresh_mgr()
        task = self._live_task()
        task.future.done.return_value = True
        mgr.id2AgentTask[task.id] = task
        assert mgr.send_message(task.id, "hello") is False

    def test_send_message_rejects_closed(self):
        mgr = self._fresh_mgr()
        task = self._live_task()
        task.closed = True
        mgr.id2AgentTask[task.id] = task
        assert mgr.send_message(task.id, "hello") is False

    def test_close_completed_resident_cancels_process(self):
        """关闭'已完成但驻留'的 keep_alive 子代理必须发取消信号,否则僵尸驻留。"""
        mgr = self._fresh_mgr()
        task = self._live_task()
        assert task.status == "completed"
        assert mgr.close_agent(task) == "closed"
        assert task.closed is True
        assert task.cancel_event.is_set()

    def test_close_truly_finished_no_cancel(self):
        mgr = self._fresh_mgr()
        task = self._live_task()
        task.future.done.return_value = True
        assert mgr.close_agent(task) == "closed"
        assert not task.cancel_event.is_set()


class TestListTasks:
    """subagent_list_tasks 测试。"""

    @staticmethod
    def _make_sub_config(task: MagicMock) -> MagicMock:
        """构造一个 sub_config,其 current_agent 为给定任务 mock。"""
        sub = MagicMock()
        sub.current_agent = task
        return sub

    @pytest.mark.asyncio
    async def test_list_empty(self):
        """无任务时返回提示。"""
        config = MagicMock()
        config.sub_configs = []
        result = await subagent_list_tasks(tool_runtime=ToolRuntime(config=config))
        assert "没有子智能体任务" in result

    @pytest.mark.asyncio
    async def test_list_with_tasks(self):
        """有任务时每行一个任务,字段用 | 分隔。"""
        task = MagicMock()
        task.id = "20261005_105609_eede855b89e3"  # 真实长度 ID(28字符)
        task.name = "中文代理"
        task.status = "running"
        task.worktree_branch = None
        task.prompt = "do something"
        task.closed = False
        config = MagicMock()
        config.sub_configs = [self._make_sub_config(task)]
        result = await subagent_list_tasks(tool_runtime=ToolRuntime(config=config))
        assert "20261005_105609_eede855b89e3" in result
        assert "中文代理" in result
        assert "running" in result
        assert "do something" in result
        assert " | " in result  # 字段以 | 分隔

    @pytest.mark.asyncio
    async def test_list_filters_closed(self):
        """已关闭的任务不出现(避免僵尸条目残留)。"""
        open_task = MagicMock()
        open_task.id = "id-open"
        open_task.name = "open"
        open_task.status = "running"
        open_task.worktree_branch = None
        open_task.prompt = "p"
        open_task.closed = False
        closed_task = MagicMock()
        closed_task.id = "id-closed"
        closed_task.name = "closed-agent"
        closed_task.status = "failed"
        closed_task.worktree_branch = None
        closed_task.prompt = "p"
        closed_task.closed = True
        config = MagicMock()
        config.sub_configs = [
            self._make_sub_config(open_task),
            self._make_sub_config(closed_task),
        ]
        result = await subagent_list_tasks(tool_runtime=ToolRuntime(config=config))
        assert "open" in result
        assert "closed-agent" not in result

    @pytest.mark.asyncio
    async def test_list_scope_current_config(self):
        """只列当前 config 的子代理,主/根会话任务不出现(不查全局管理器)。"""
        sub_task = MagicMock()
        sub_task.id = "20261005_105610_1234567890ab"
        sub_task.name = "worker"
        sub_task.status = "completed"
        sub_task.worktree_branch = None
        sub_task.prompt = "子代理任务"
        sub_task.closed = False
        config = MagicMock()
        config.sub_configs = [self._make_sub_config(sub_task)]
        result = await subagent_list_tasks(tool_runtime=ToolRuntime(config=config))
        assert "worker" in result
        # 主/根会话任务不在 sub_configs 中,不应出现
        assert "root" not in result

    @pytest.mark.asyncio
    async def test_list_without_runtime(self):
        """无 tool_runtime(无配置)时返回提示。"""
        result = await subagent_list_tasks()
        assert "没有子智能体任务" in result


class TestListDefinitions:
    """subagent_list_definitions 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_list_empty(self, mock_load, mock_mgr):
        """无定义时返回提示。"""
        mock_load.return_value = {}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_list_definitions(tool_runtime=ToolRuntime(config=config))
        assert "没有可用的智能体类型" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_list_with_definitions(self, mock_load, mock_mgr):
        """有定义时返回列表。"""
        mock_def = MagicMock()
        mock_def.source = "built-in"
        mock_def.description = "test agent"
        mock_def.model_name = None
        mock_def.tools = None
        mock_load.return_value = {"coder": mock_def}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_list_definitions(tool_runtime=ToolRuntime(config=config))
        assert "coder" in result
        assert "可用的智能体类型" in result


class TestGetDefinition:
    """subagent_get_definition 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_get_not_found(self, mock_load, mock_mgr):
        """查询不存在的类型。"""
        mock_load.return_value = {}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_get_definition("nonexistent", tool_runtime=ToolRuntime(config=config))
        assert "未找到类型" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    async def test_get_found(self, mock_load, mock_mgr):
        """查询存在的类型。"""
        mock_def = MagicMock()
        mock_def.name = "coder"
        mock_def.source = "built-in"
        mock_def.description = "code writer"
        mock_def.model_name = "gpt-4o"
        mock_def.tools = ["Read", "Write"]
        mock_def.system_prompt = "You are a coder."
        mock_load.return_value = {"coder": mock_def}
        config = MagicMock()
        config.root_dir = None
        result = await subagent_get_definition("coder", tool_runtime=ToolRuntime(config=config))
        assert "coder" in result
        assert "code writer" in result
        assert "gpt-4o" in result


class TestGetSubAgentSystemPrompt:
    """get_sub_agent_system_prompt 测试。"""

    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    def test_empty_when_no_definitions(self, mock_load):
        """无定义时返回空字符串。"""
        mock_load.return_value = {}
        result = get_sub_agent_system_prompt()
        assert result == ""

    @patch("uniclaw.tools.multi_agent.tools.load_agent_definitions")
    def test_contains_type_names(self, mock_load):
        """包含类型名称。"""
        mock_def = MagicMock()
        mock_def.tools = None
        mock_load.return_value = {"coder": mock_def, "reviewer": mock_def}
        result = get_sub_agent_system_prompt()
        assert "coder" in result
        assert "reviewer" in result


class TestCloseAgent:
    """MultiAgent.close_agent / detach_sub_config — 幂等与回收。"""

    @staticmethod
    def _fresh_mgr():
        from uniclaw.agent import MultiAgent

        mgr = MultiAgent.get_instance()
        mgr.id2AgentTask = {}
        return mgr

    def test_close_running_sends_cancel(self):
        from uniclaw.agent import MultiAgent

        mgr = self._fresh_mgr()
        task = _make_task(status="running")
        assert mgr.close_agent(task) == "closed"
        assert task.closed is True
        assert task.cancel_event.set.called

    def test_close_finished_no_cancel(self):
        mgr = self._fresh_mgr()
        task = _make_task(status="failed", finished=True)
        assert mgr.close_agent(task) == "closed"
        assert task.closed is True
        task.cancel_event.set.assert_not_called()

    def test_close_twice_already_closed(self):
        mgr = self._fresh_mgr()
        task = _make_task()
        assert mgr.close_agent(task) == "closed"
        assert mgr.close_agent(task) == "already_closed"

    def test_close_skips_messaging_closed_task(self):
        mgr = self._fresh_mgr()
        task = _make_task()
        task.closed = True
        mgr.id2AgentTask = {"task-1": task}
        assert mgr.send_message("task-1", "hi") is False

    def test_detach_sub_config_removes_task(self):
        mgr = self._fresh_mgr()
        task = _make_task()
        sub = MagicMock()
        sub.current_agent = task
        other = MagicMock()
        other.current_agent = _make_task(task_id="other")
        owner = MagicMock()
        owner.sub_configs = [sub, other]
        mgr.detach_sub_config(owner, task)
        assert owner.sub_configs == [other]


class TestIsFinished:
    """AgentTask.is_finished — 判断执行过程是否真正结束。"""

    @staticmethod
    def _real_task(status, future=None):
        from uniclaw.agent.types import AgentTask

        session = MagicMock()
        session.id = "test-is-finished"
        task = AgentTask(name="t", prompt="p", session=session)
        task.status = status
        task.future = future
        return task

    def test_finished_when_future_done(self):
        """future 已完成 → 真正结束(即使 status 短暂为 completed)。"""
        from uniclaw.agent import AgentStatus

        future = MagicMock()
        future.done.return_value = True
        task = self._real_task(AgentStatus.COMPLETED, future)
        assert task.is_finished is True

    def test_not_finished_when_future_pending(self):
        """keep_alive 轮间: future 未完成,状态短暂 COMPLETED 也不算结束。"""
        from uniclaw.agent import AgentStatus

        future = MagicMock()
        future.done.return_value = False
        task = self._real_task(AgentStatus.COMPLETED, future)
        assert task.is_finished is False

    def test_finished_when_no_future_and_terminal(self):
        """从未启动(无 future)但已终态 → 算结束(启动失败路径)。"""
        from uniclaw.agent import AgentStatus

        task = self._real_task(AgentStatus.FAILED, future=None)
        assert task.is_finished is True

    def test_not_finished_when_no_future_and_running(self):
        """无 future 且仍在运行 → 未结束。"""
        from uniclaw.agent import AgentStatus

        task = self._real_task(AgentStatus.RUNNING, future=None)
        assert task.is_finished is False


class TestDepthLimit:
    """_run_init 深度检查 — max_agent_depth 表示最大子代理嵌套层数。"""

    @staticmethod
    def _make_run_config(depth: int, max_depth: int):
        config = MagicMock()
        config.depth = depth
        config.max_agent_depth = max_depth
        config.is_sub = depth > 0
        task = MagicMock()
        task.status = "pending"
        task.result = None
        task.event_queue = None  # 无 UI 队列,跳过事件投递
        task.session = MagicMock()
        config.current_agent = task
        config.parent_config = None
        return config, task

    @staticmethod
    def _patch_extras():
        return (
            patch("uniclaw.agent.multi_agent.run_hooks", new_callable=AsyncMock),
            patch(
                "uniclaw.agent.multi_agent.maybe_time_notice", return_value=None
            ),
        )

    @pytest.mark.asyncio
    async def test_nested_depth_allowed_at_max(self):
        """depth == max_agent_depth 是允许的(孙代理 depth=2, max=2)。"""
        from uniclaw.agent import MultiAgent

        mgr = MultiAgent.get_instance()
        config, task = self._make_run_config(depth=2, max_depth=2)
        p1, p2 = self._patch_extras()
        with p1, p2:
            ok = await mgr._run_init("hello", config)
        assert ok is True
        assert task.status == "running"

    @pytest.mark.asyncio
    async def test_over_max_depth_rejected_with_reason(self):
        """depth > max_agent_depth 拒绝,task.result 带明确原因(不为空)。"""
        from uniclaw.agent import MultiAgent

        mgr = MultiAgent.get_instance()
        config, task = self._make_run_config(depth=3, max_depth=2)
        ok = await mgr._run_init("hello", config)
        assert ok is False
        assert task.status == "failed"
        assert task.result and "最大嵌套深度" in task.result

    @pytest.mark.asyncio
    async def test_max_one_allows_first_level_sub(self):
        """max_agent_depth=1 仍允许 depth=1 的子代理(UI 标签"最大子代理深度")。"""
        from uniclaw.agent import MultiAgent

        mgr = MultiAgent.get_instance()
        config, task = self._make_run_config(depth=1, max_depth=1)
        p1, p2 = self._patch_extras()
        with p1, p2:
            ok = await mgr._run_init("hello", config)
        assert ok is True


class TestFailedResultPreserved:
    """嵌套失败时 task.result 不得被空 assistant 输出覆盖。"""

    @pytest.mark.asyncio
    async def test_over_max_depth_result_survives_run_proc(self):
        """start_sub_agent 超深失败后,task.result 保留失败原因而非"(无输出)"。"""
        from uniclaw.agent import MultiAgent, AgentStatus
        from uniclaw.agent.types import AgentTask

        mgr = MultiAgent.get_instance()
        session = MagicMock()
        session.id = "nested-test-session"
        session.get_assistant_messages.return_value = ""  # 空 assistant 输出
        session.root_dir = None
        task = AgentTask(name="nested", prompt="hello", session=session)
        task.event_queue = None

        config = MagicMock()
        config.depth = 5
        config.max_agent_depth = 2
        config.is_sub = True
        config.parent_config = None
        config.parent_agent = None
        config.root_dir = None
        config.model_name = ["test-model"]
        config.writable_dirs = []
        config.current_agent = task

        with (
            patch(
                "uniclaw.agent.multi_agent.get_base_system_prompt",
                return_value="base",
            ),
            patch(
                "uniclaw.agent.multi_agent.get_env_system_prompt",
                return_value="env",
            ),
            patch(
                "uniclaw.tools.registry.get_registry_system_prompt",
                new_callable=AsyncMock,
                return_value="",
            ),
            patch(
                "uniclaw.agent.multi_agent.maybe_time_notice", return_value=None
            ),
        ):
            started = await mgr.start_sub_agent(
                user_message="hello",
                config=config,
                system_prompt=None,
                inherit_events=False,
                notify_parent=False,
                keep_alive=False,
            )
            await started.future

        assert started.status == AgentStatus.FAILED
        # 关键: 失败原因保留,不会被空 assistant 输出抹成 "" 导致 "(无输出 — 状态: failed)"
        assert started.result and "最大嵌套深度" in started.result


class TestStatusText:
    """状态展示要标出驻留态 —— keep_alive 子代理回合结束后 status=COMPLETED
    但 future 仍存活,只显示 completed 会让人以为彻底结束、不能再来消息了。
    """

    @staticmethod
    def _task(status, *, closed=False, finished=False):
        t = _make_task(status=status)
        t.closed = closed
        t.is_finished = finished
        t.can_accept_messages = (not closed) and (not finished)
        return t

    def test_completed_and_resident_shows_waiting(self):
        """回合完成但驻留待消息 → 明确标注。"""
        from uniclaw.tools.multi_agent.tools import _status_text

        assert _status_text(self._task(AgentStatus.COMPLETED)) == "completed(驻留待消息)"

    def test_completed_and_finished_plain(self):
        """真正结束(不再收消息)→ 只显示状态。"""
        from uniclaw.tools.multi_agent.tools import _status_text

        assert _status_text(self._task(AgentStatus.COMPLETED, finished=True)) == "completed"

    def test_other_statuses_unchanged(self):
        """其余状态不做标注。"""
        from uniclaw.tools.multi_agent.tools import _status_text

        for st in (AgentStatus.PENDING, AgentStatus.RUNNING, AgentStatus.FAILED):
            assert _status_text(self._task(st)) == str(st)

    @pytest.mark.asyncio
    async def test_list_tasks_shows_resident_marker(self, mock_mgr):
        """list_tasks 的状态列带驻留标注。"""
        resident = self._task(AgentStatus.COMPLETED)
        sub = MagicMock()
        sub.current_agent = resident
        config = MagicMock()
        config.sub_configs = [sub]

        from uniclaw.tools.multi_agent.tools import subagent_list_tasks

        out = await subagent_list_tasks(tool_runtime=ToolRuntime(config=config))
        assert "驻留待消息" in out


class TestAsyncLifecycleUntilClose:
    """异步子代理只要不 subagent_close 就一直留在 sub_configs 里。

    回收唯一出口是 subagent_close;check_result 是纯读,启动失败也不能悄悄回收 ——
    否则调用方拿不到 task_id、也不知道该关谁。
    """

    @staticmethod
    def _rt(*tasks):
        config = MagicMock()
        config.sub_configs = [MagicMock(current_agent=t) for t in tasks]
        config.root_dir = None
        return ToolRuntime(config=config)

    @pytest.mark.asyncio
    async def test_async_startup_failure_keeps_task(self, mock_mgr):
        """异步启动失败 → 任务保留,报错带 task_id 供 close。"""
        from uniclaw.agent import AgentStatus
        from uniclaw.tools.multi_agent.tools import subagent_create

        task = _make_task(task_id="task-1", name="worker", status=AgentStatus.FAILED)
        task.result = "超过最大嵌套深度"
        mock_mgr.start_sub_agent = AsyncMock(return_value=task)
        mock_mgr.detach_sub_config.side_effect = lambda cfg, t: None

        rt = self._rt()
        config = rt.config
        with _patch_get_instance(mock_mgr):
            result = await subagent_create(
                prompt="x", subagent_type="coder", name="worker",
                wait=False, tool_runtime=rt,
            )

        assert "生成智能体时出错" in result
        assert "task-1" in result          # 带 task_id,调用方才知道关谁
        assert "subagent_close" in result  # 给出关闭指引
        mock_mgr.detach_sub_config.assert_not_called()  # 不悄悄回收

    @pytest.mark.asyncio
    async def test_sync_startup_failure_recycles(self, mock_mgr):
        """同步启动失败 → 错误已内联返回,直接回收(调用方不会再碰)。"""
        from uniclaw.agent import AgentStatus
        from uniclaw.tools.multi_agent.tools import subagent_create

        task = _make_task(task_id="task-1", name="worker", status=AgentStatus.FAILED)
        task.result = "超过最大嵌套深度"
        mock_mgr.start_sub_agent = AsyncMock(return_value=task)
        mock_mgr.detach_sub_config.side_effect = lambda cfg, t: None

        rt = self._rt()
        with _patch_get_instance(mock_mgr):
            result = await subagent_create(
                prompt="x", subagent_type="coder", name="worker",
                wait=True, tool_runtime=rt,
            )

        assert "生成智能体时出错" in result
        mock_mgr.detach_sub_config.assert_called_once()
