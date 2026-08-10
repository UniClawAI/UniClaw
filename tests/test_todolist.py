"""任务清单和目标系统测试 — 覆盖 TodoList、GoalManager 的核心逻辑。"""

import pytest
from uniclaw.tools.todolist.todolist import TodoList, TodoStatus
from uniclaw.tools.todolist.goal import GoalManager, GoalStatus


class TestTodoList:
    """TodoList 任务清单测试。"""

    def test_empty_list(self):
        """新建清单为空。"""
        tl = TodoList()
        assert tl.is_empty()
        assert tl.get_list() == ""

    def test_add_item(self):
        """添加任务。"""
        tl = TodoList()
        item = tl.add("写测试")
        assert item.content == "写测试"
        assert item.status == TodoStatus.PENDING
        assert len(tl.items) == 1

    def test_add_multiple_items(self):
        """添加多个任务。"""
        tl = TodoList()
        tl.add("任务1")
        tl.add("任务2")
        tl.add("任务3")
        assert len(tl.items) == 3
        assert tl.items[0].id == 0
        assert tl.items[1].id == 1
        assert tl.items[2].id == 2

    def test_update_status_pending_to_in_progress(self):
        """pending → in_progress。"""
        tl = TodoList()
        tl.add("写测试")
        result = tl.update_status(0, "in_progress")
        assert tl.items[0].status == TodoStatus.IN_PROGRESS
        assert "[*]" in result

    def test_update_status_in_progress_to_completed(self):
        """in_progress → completed。"""
        tl = TodoList()
        tl.add("写测试")
        tl.update_status(0, "in_progress")
        result = tl.update_status(0, "completed")
        assert tl.items[0].status == TodoStatus.COMPLETED
        assert "[✓]" in result

    def test_cannot_complete_pending_item(self):
        """pending 不能直接标记为 completed。"""
        tl = TodoList()
        tl.add("写测试")
        result = tl.update_status(0, "completed")
        assert "错误" in result or "TOOL_ERROR" in result or "只有" in result

    def test_only_one_in_progress(self):
        """同一时间只能有一个 in_progress。"""
        tl = TodoList()
        tl.add("任务1")
        tl.add("任务2")
        tl.update_status(0, "in_progress")
        tl.update_status(1, "in_progress")
        # 第一个应被自动改回 pending
        assert tl.items[0].status == TodoStatus.PENDING
        assert tl.items[1].status == TodoStatus.IN_PROGRESS

    def test_update_invalid_index(self):
        """无效索引返回错误。"""
        tl = TodoList()
        tl.add("任务1")
        result = tl.update_status(5, "in_progress")
        assert "错误" in result or "超出范围" in result

    def test_update_empty_list(self):
        """空列表更新返回错误。"""
        tl = TodoList()
        result = tl.update_status(0, "in_progress")
        assert "错误" in result or "列表为空" in result

    def test_clear(self):
        """清空清单。"""
        tl = TodoList()
        tl.add("任务1")
        tl.add("任务2")
        tl.clear()
        assert tl.is_empty()
        assert len(tl.items) == 0

    def test_get_list_format(self):
        """列表格式正确。"""
        tl = TodoList()
        tl.add("待办任务")
        tl.add("进行中任务")
        tl.add("已完成任务")
        # 任务0: pending
        # 任务1: → in_progress
        tl.update_status(1, "in_progress")
        # 任务2: → in_progress (任务1 回到 pending)
        tl.update_status(2, "in_progress")
        # 任务2: → completed
        tl.update_status(2, "completed")
        # 任务0: → in_progress
        tl.update_status(0, "in_progress")

        result = tl.get_list()
        assert "[ ]" in result  # pending (任务1)
        assert "[*]" in result  # in_progress (任务0)
        assert "[✓]" in result  # completed (任务2)

    def test_status_string_values(self):
        """状态枚举的字符串值。"""
        assert TodoStatus.PENDING == "pending"
        assert TodoStatus.IN_PROGRESS == "in_progress"
        assert TodoStatus.COMPLETED == "completed"


class TestGoalManager:
    """GoalManager 目标停止条件测试。"""

    def test_initial_state(self):
        """初始状态无目标。"""
        gm = GoalManager()
        assert gm.active is False
        assert gm.goal == ""
        assert gm.reentry_count == 0

    def test_set_goal(self):
        """设置目标。"""
        gm = GoalManager()
        gm.set_goal("完成所有测试")
        assert gm.active is True
        assert gm.goal == "完成所有测试"

    def test_set_goal_strips_whitespace(self):
        """设置目标时去除首尾空格。"""
        gm = GoalManager()
        gm.set_goal("  完成所有测试  ")
        assert gm.goal == "完成所有测试"

    def test_clear_goal(self):
        """清除目标。"""
        gm = GoalManager()
        gm.set_goal("完成所有测试")
        gm.clear_goal()
        assert gm.active is False
        assert gm.goal == ""

    def test_set_goal_resets_reentry(self):
        """设置新目标时重置重入计数。"""
        gm = GoalManager()
        gm.set_goal("目标1")
        gm.increment_reentry()
        gm.increment_reentry()
        gm.set_goal("目标2")
        assert gm.reentry_count == 0

    def test_clear_goal_resets_reentry(self):
        """清除目标时重置重入计数。"""
        gm = GoalManager()
        gm.set_goal("目标")
        gm.increment_reentry()
        gm.clear_goal()
        assert gm.reentry_count == 0

    def test_check_reentry_within_limit(self):
        """未超过重入限制时返回 True。"""
        gm = GoalManager(max_reentry=3)
        assert gm.check_reentry() is True

    def test_check_reentry_at_limit(self):
        """达到重入限制时返回 False。"""
        gm = GoalManager(max_reentry=2)
        gm.set_goal("目标")
        gm.increment_reentry()
        gm.increment_reentry()
        assert gm.check_reentry() is False

    def test_increment_reentry(self):
        """重入计数递增。"""
        gm = GoalManager()
        assert gm.reentry_count == 0
        gm.increment_reentry()
        assert gm.reentry_count == 1
        gm.increment_reentry()
        assert gm.reentry_count == 2

    def test_reset_reentry(self):
        """重置重入计数。"""
        gm = GoalManager()
        gm.increment_reentry()
        gm.increment_reentry()
        gm.reset_reentry()
        assert gm.reentry_count == 0

    def test_default_max_reentry(self):
        """默认最大重入次数。"""
        gm = GoalManager()
        assert gm.max_reentry == 3

    def test_custom_max_reentry(self):
        """自定义最大重入次数。"""
        gm = GoalManager(max_reentry=5)
        assert gm.max_reentry == 5

    def test_get_status_no_goal(self):
        """无目标时的状态文本。"""
        gm = GoalManager()
        status = gm.get_status()
        # 无目标时应返回某种空/未设置状态
        assert isinstance(status, str)

    def test_get_status_with_goal(self):
        """有目标时的状态文本。"""
        gm = GoalManager()
        gm.set_goal("完成测试")
        gm.increment_reentry()
        status = gm.get_status()
        assert "完成测试" in status
