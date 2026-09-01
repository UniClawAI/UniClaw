"""TodoList 工具层测试 — 覆盖 todolist_* 工具和 OverseerManager。"""

import pytest
from unittest.mock import MagicMock, patch

from uniclaw.tools.todolist.tools import (
    todolist_create,
    todolist_update,
    todolist_clear,
    todolist_cancel,
    todolist_list,
    get_list_system_prompt,
    get_tools,
    get_all_tools,
)
from uniclaw.tools.todolist.todolist import TodoList, TodoStatus
from uniclaw.tools.todolist.overseer import OverseerManager
from uniclaw.utils.constants import TOOL_ERROR


def _make_config(todo: TodoList) -> MagicMock:
    """构造带真实 TodoList 的 mock config。"""
    config = MagicMock()
    agent = MagicMock()
    agent.todolist = todo
    agent.cancel_event = MagicMock()
    config.current_agent = agent
    return config


class TestToolRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_five(self):
        """get_tools 返回 5 个工具。"""
        result = get_tools()
        assert len(result) == 5

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())

    def test_tool_names(self):
        """工具名称齐全。"""
        names = [t.name for t in get_tools()]
        assert "todolist_create" in names
        assert "todolist_update" in names
        assert "todolist_clear" in names
        assert "todolist_cancel" in names
        assert "todolist_list" in names


class TestCreate:
    """todolist_create 测试。"""

    @pytest.mark.asyncio
    async def test_create_basic(self):
        """正常创建清单。"""
        todo = TodoList()
        config = _make_config(todo)
        result = await todolist_create(["步骤1", "步骤2"], config=config)
        assert "已创建任务清单" in result
        assert "共 2 个步骤" in result
        assert len(todo.items) == 2
        assert todo.items[0].status == TodoStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_create_overwrites_existing(self):
        """创建时覆盖已有清单。"""
        todo = TodoList()
        todo.add("旧任务")
        config = _make_config(todo)
        result = await todolist_create(["新任务"], config=config)
        assert "共 1 个步骤" in result
        assert len(todo.items) == 1
        assert todo.items[0].content == "新任务"

    @pytest.mark.asyncio
    async def test_create_empty_items(self):
        """空列表创建返回 0 个步骤。"""
        todo = TodoList()
        config = _make_config(todo)
        result = await todolist_create([], config=config)
        assert "共 0 个步骤" in result


class TestUpdate:
    """todolist_update 测试。"""

    @pytest.mark.asyncio
    async def test_update_valid_status(self):
        """有效状态更新。"""
        todo = TodoList()
        todo.add("任务")
        config = _make_config(todo)
        result = await todolist_update(0, "in_progress", config=config)
        assert "[*]" in result
        assert todo.items[0].status == TodoStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_update_invalid_status(self):
        """无效状态返回错误。"""
        todo = TodoList()
        todo.add("任务")
        config = _make_config(todo)
        result = await todolist_update(0, "bogus", config=config)
        assert "无效状态" in result

    @pytest.mark.asyncio
    async def test_update_empty_list(self):
        """空清单更新返回错误。"""
        todo = TodoList()
        config = _make_config(todo)
        result = await todolist_update(0, "pending", config=config)
        assert "没有任务清单" in result

    @pytest.mark.asyncio
    async def test_update_completed_flow(self):
        """pending → in_progress → completed 完整流程。"""
        todo = TodoList()
        todo.add("任务")
        config = _make_config(todo)
        await todolist_update(0, "in_progress", config=config)
        result = await todolist_update(0, "completed", config=config)
        assert "[✓]" in result
        assert todo.items[0].status == TodoStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_update_all_completed_returns_clear_hint(self):
        """全部步骤完成时返回 todolist_clear 提示。"""
        todo = TodoList()
        todo.add("任务1")
        todo.add("任务2")
        config = _make_config(todo)
        await todolist_update(0, "in_progress", config=config)
        partial = await todolist_update(0, "completed", config=config)
        assert "todolist_clear" not in partial
        await todolist_update(1, "in_progress", config=config)
        result = await todolist_update(1, "completed", config=config)
        assert "全部 2 个步骤均已完成" in result
        assert f"请立即调用 {todolist_clear.name}" in result

    @pytest.mark.asyncio
    async def test_update_error_no_hint(self):
        """更新失败时不附加清空提示。"""
        todo = TodoList()
        todo.add("任务")
        config = _make_config(todo)
        # pending 不能直接改为 completed
        result = await todolist_update(0, "completed", config=config)
        assert TOOL_ERROR in result
        assert "todolist_clear" not in result


class TestClear:
    """todolist_clear 测试。"""

    @pytest.mark.asyncio
    async def test_clear_with_items(self):
        """清空有内容的清单。"""
        todo = TodoList()
        todo.add("任务1")
        todo.add("任务2")
        config = _make_config(todo)
        result = await todolist_clear(config=config)
        assert "已清空任务清单" in result
        assert "共 2 个步骤" in result
        assert todo.is_empty()


class TestCancel:
    """todolist_cancel 测试。"""

    @pytest.mark.asyncio
    async def test_cancel_sets_event(self):
        """取消时设置 cancel_event。"""
        todo = TodoList()
        config = _make_config(todo)
        result = await todolist_cancel(config=config)
        assert "任务暂停" in result
        config.current_agent.cancel_event.set.assert_called_once()


class TestList:
    """todolist_list 测试。"""

    @pytest.mark.asyncio
    async def test_list_empty(self):
        """空清单返回提示。"""
        todo = TodoList()
        config = _make_config(todo)
        result = await todolist_list(config=config)
        assert "没有任务清单" in result

    @pytest.mark.asyncio
    async def test_list_with_items(self):
        """有内容时返回清单。"""
        todo = TodoList()
        todo.add("任务1")
        todo.add("任务2")
        config = _make_config(todo)
        result = await todolist_list(config=config)
        assert "任务1" in result
        assert "任务2" in result


class TestGetListSystemPrompt:
    """get_list_system_prompt 测试。"""

    def test_none_returns_empty(self):
        """None 返回空字符串。"""
        assert get_list_system_prompt(None) == ""

    def test_empty_todolist(self):
        """空清单返回提示。"""
        prompt = get_list_system_prompt(TodoList())
        assert "todolist_create" in prompt
        assert "拆解为多个步骤" in prompt

    def test_with_items(self):
        """有任务时返回重要指令。"""
        todo = TodoList()
        todo.add("任务")
        prompt = get_list_system_prompt(todo)
        assert "重要指令" in prompt
        assert "不要等待用户催促" in prompt

    def test_overseer_mode_prompt(self):
        """监工模式下提示不同。"""
        todo = TodoList()
        todo.add("任务")
        todo.overseer.start()
        prompt = get_list_system_prompt(todo)
        assert "在 reason 中说明" in prompt


class TestOverseerManager:
    """OverseerManager 测试。"""

    def test_default_inactive(self):
        """默认未激活。"""
        om = OverseerManager()
        assert om.active is False

    def test_start(self):
        """启动后激活。"""
        om = OverseerManager()
        om.start()
        assert om.active is True

    def test_stop(self):
        """停止后取消激活。"""
        om = OverseerManager()
        om.start()
        om.stop()
        assert om.active is False


class TestVerify:
    """verify_completion / verify_modification 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.todolist.overseer._run_reviewer", return_value=(True, ""))
    async def test_verify_completion_passed(self, mock_reviewer):
        """审核通过。"""
        from uniclaw.tools.todolist.overseer import verify_completion

        passed, reason = await verify_completion("任务内容", _make_config(TodoList()))
        assert passed is True
        assert reason == ""

    @pytest.mark.asyncio
    @patch(
        "uniclaw.tools.todolist.overseer._run_reviewer",
        return_value=(False, "未实现功能"),
    )
    async def test_verify_completion_failed(self, mock_reviewer):
        """审核不通过。"""
        from uniclaw.tools.todolist.overseer import verify_completion

        passed, reason = await verify_completion("任务内容", _make_config(TodoList()))
        assert passed is False
        assert reason == "未实现功能"

    @pytest.mark.asyncio
    @patch("uniclaw.tools.todolist.overseer._run_reviewer", return_value=(True, ""))
    async def test_verify_modification_passed(self, mock_reviewer):
        """修改审核通过。"""
        from uniclaw.tools.todolist.overseer import verify_modification

        passed, reason = await verify_modification(
            "重建清单", ["旧"], ["新"], "旧清单不合理", _make_config(TodoList())
        )
        assert passed is True

    @pytest.mark.asyncio
    @patch(
        "uniclaw.tools.todolist.overseer._run_reviewer",
        return_value=(False, "理由不充分"),
    )
    async def test_verify_modification_failed(self, mock_reviewer):
        """修改审核不通过。"""
        from uniclaw.tools.todolist.overseer import verify_modification

        passed, reason = await verify_modification(
            "重建清单", ["旧"], ["新"], "无理由", _make_config(TodoList())
        )
        assert passed is False
        assert reason == "理由不充分"


class TestOverseerModeTools:
    """监工模式下的工具分派测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.todolist.overseer._run_reviewer", return_value=(True, ""))
    async def test_create_in_overseer_mode(self, mock_reviewer):
        """监工模式下创建清单需审核。"""
        todo = TodoList()
        todo.overseer.start()
        todo.add("旧任务")
        config = _make_config(todo)
        result = await todolist_create(
            ["新任务1", "新任务2"], reason="旧清单不合理", config=config
        )
        assert "已重建清单" in result
        mock_reviewer.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.todolist.overseer._run_reviewer", return_value=(True, ""))
    async def test_update_in_overseer_mode(self, mock_reviewer):
        """监工模式下标记完成需审核。"""
        todo = TodoList()
        todo.overseer.start()
        todo.add("任务")
        todo.items[0].status = TodoStatus.IN_PROGRESS
        config = _make_config(todo)
        result = await todolist_update(
            0, "completed", reason="已完成功能", config=config
        )
        assert "已标记为完成" in result
        mock_reviewer.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.todolist.overseer._run_reviewer", return_value=(True, ""))
    async def test_update_overseer_all_completed_hint(self, mock_reviewer):
        """监工模式下全部完成时同样返回清空提示。"""
        todo = TodoList()
        todo.overseer.start()
        todo.add("任务")
        todo.items[0].status = TodoStatus.IN_PROGRESS
        config = _make_config(todo)
        result = await todolist_update(
            0, "completed", reason="已完成功能", config=config
        )
        assert f"请立即调用 {todolist_clear.name}" in result

    @pytest.mark.asyncio
    @patch(
        "uniclaw.tools.todolist.overseer._run_reviewer",
        return_value=(False, "功能未实现"),
    )
    async def test_update_overseer_rejected(self, mock_reviewer):
        """监工模式下审核不通过则不标记完成。"""
        todo = TodoList()
        todo.overseer.start()
        todo.add("任务")
        todo.items[0].status = TodoStatus.IN_PROGRESS
        config = _make_config(todo)
        result = await todolist_update(0, "completed", reason="糊弄一下", config=config)
        assert "审核未通过" in result
        # 状态应保持 in_progress
        assert todo.items[0].status == TodoStatus.IN_PROGRESS
