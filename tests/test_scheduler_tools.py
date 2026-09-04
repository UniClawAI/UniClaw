"""调度器工具层测试 — 覆盖 schedule_* 工具和 _validate_action 校验逻辑。"""

import pytest
from unittest.mock import MagicMock, patch

from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.scheduler.tools import (
    _validate_action,
    schedule_create,
    schedule_list,
    schedule_remove,
    schedule_update,
    schedule_toggle,
    get_tools,
    get_all_tools,
)


def _patch_scheduler(mock_scheduler):
    """patch Scheduler.get_instance 返回 mock_scheduler。"""
    return patch(
        "uniclaw.tools.scheduler.scheduler.Scheduler.get_instance",
        return_value=mock_scheduler,
    )


class TestValidateAction:
    """_validate_action JSON 校验测试。"""

    def test_valid_shell(self):
        """合法 shell 类型。"""
        assert _validate_action('{"type": "shell", "command": "ls"}') is None

    def test_valid_agent(self):
        """合法 agent 类型。"""
        assert _validate_action('{"type": "agent", "message": "hello"}') is None

    def test_valid_py(self):
        """合法 py 类型。"""
        assert _validate_action('{"type": "py", "code": "print(1)"}') is None

    def test_valid_monitor(self):
        """合法 monitor 类型。"""
        action = '{"type": "monitor", "command": "curl x", "agent": {"message": "挂了"}}'
        assert _validate_action(action) is None

    def test_invalid_json(self):
        """非法 JSON。"""
        err = _validate_action("not json {")
        assert err is not None
        assert "JSON 解析失败" in err

    def test_missing_type(self):
        """缺少 type 字段。"""
        err = _validate_action('{"command": "ls"}')
        assert err is not None
        assert "type" in err

    def test_unknown_type(self):
        """未知 type。"""
        err = _validate_action('{"type": "bogus"}')
        assert err is not None
        assert "未知的 type" in err

    def test_shell_missing_command(self):
        """shell 缺少 command。"""
        err = _validate_action('{"type": "shell"}')
        assert err is not None
        assert "command" in err

    def test_agent_missing_message(self):
        """agent 缺少 message。"""
        err = _validate_action('{"type": "agent"}')
        assert err is not None
        assert "message" in err

    def test_py_missing_code(self):
        """py 缺少 code。"""
        err = _validate_action('{"type": "py"}')
        assert err is not None
        assert "code" in err

    def test_monitor_missing_command(self):
        """monitor 缺少 command。"""
        err = _validate_action('{"type": "monitor", "agent": {"message": "x"}}')
        assert err is not None
        assert "command" in err

    def test_monitor_missing_agent(self):
        """monitor 缺少 agent 对象。"""
        err = _validate_action('{"type": "monitor", "command": "ls"}')
        assert err is not None
        assert "agent" in err

    def test_monitor_agent_missing_message(self):
        """monitor.agent 缺少 message。"""
        err = _validate_action('{"type": "monitor", "command": "ls", "agent": {}}')
        assert err is not None
        assert "message" in err


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
        assert "schedule_create" in names
        assert "schedule_list" in names
        assert "schedule_remove" in names
        assert "schedule_update" in names
        assert "schedule_toggle" in names


class TestScheduleCreate:
    """schedule_create 测试。"""

    @pytest.mark.asyncio
    async def test_create_success(self):
        """创建成功。"""
        mock_scheduler = MagicMock()
        task = MagicMock()
        task.id = "task-1"
        mock_scheduler.add_task.return_value = task
        with _patch_scheduler(mock_scheduler):
            result = await schedule_create(
                "测试任务",
                "*/5 * * * *",
                '{"type": "shell", "command": "ls"}',
                tool_runtime=ToolRuntime(config=MagicMock()),
            )
        assert "已创建定时任务" in result
        assert "task-1" in result
        mock_scheduler.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_invalid_action(self):
        """非法 action 返回错误。"""
        mock_scheduler = MagicMock()
        with _patch_scheduler(mock_scheduler):
            result = await schedule_create(
                "任务", "*/5 * * * *", '{"type": "bad"}', tool_runtime=ToolRuntime(config=MagicMock())
            )
        assert "未知的 type" in result
        mock_scheduler.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_value_error(self):
        """调度器抛 ValueError。"""
        mock_scheduler = MagicMock()
        mock_scheduler.add_task.side_effect = ValueError("无效 cron")
        with _patch_scheduler(mock_scheduler):
            result = await schedule_create(
                "任务",
                "bad cron",
                '{"type": "shell", "command": "ls"}',
                tool_runtime=ToolRuntime(config=MagicMock()),
            )
        assert "无效 cron" in result


class TestScheduleList:
    """schedule_list 测试。"""

    @pytest.mark.asyncio
    async def test_list_empty(self):
        """无任务返回提示。"""
        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = []
        with _patch_scheduler(mock_scheduler):
            result = await schedule_list()
        assert "暂无定时任务" in result

    @pytest.mark.asyncio
    async def test_list_with_tasks(self):
        """有任务返回列表。"""
        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = [
            {
                "id": "task-1",
                "name": "日报",
                "schedule": "0 9 * * *",
                "action": '{"type": "agent", "message": "日报"}',
                "enabled": True,
                "last_run": "2026-01-01",
            }
        ]
        with _patch_scheduler(mock_scheduler):
            result = await schedule_list()
        assert "共 1 个定时任务" in result
        assert "task-1" in result
        assert "日报" in result
        assert "启用" in result


class TestScheduleRemove:
    """schedule_remove 测试。"""

    @pytest.mark.asyncio
    async def test_remove_success(self):
        """删除成功。"""
        mock_scheduler = MagicMock()
        mock_scheduler.remove_task.return_value = True
        with _patch_scheduler(mock_scheduler):
            result = await schedule_remove("task-1")
        assert "已删除定时任务" in result

    @pytest.mark.asyncio
    async def test_remove_not_found(self):
        """任务不存在。"""
        mock_scheduler = MagicMock()
        mock_scheduler.remove_task.return_value = False
        with _patch_scheduler(mock_scheduler):
            result = await schedule_remove("nonexistent")
        assert "不存在" in result


class TestScheduleUpdate:
    """schedule_update 测试。"""

    @pytest.mark.asyncio
    async def test_no_args(self):
        """未提供 action 和 schedule。"""
        mock_scheduler = MagicMock()
        with _patch_scheduler(mock_scheduler):
            result = await schedule_update("task-1")
        assert "至少需要提供" in result

    @pytest.mark.asyncio
    async def test_update_action(self):
        """更新 action。"""
        mock_scheduler = MagicMock()
        mock_scheduler.update_action.return_value = True
        with _patch_scheduler(mock_scheduler):
            result = await schedule_update(
                "task-1", action='{"type": "shell", "command": "ls"}'
            )
        assert "已更新任务" in result
        mock_scheduler.update_action.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_invalid_action(self):
        """无效 action 返回错误。"""
        mock_scheduler = MagicMock()
        with _patch_scheduler(mock_scheduler):
            result = await schedule_update("task-1", action='{"type": "bad"}')
        assert "未知的 type" in result

    @pytest.mark.asyncio
    async def test_update_action_not_found(self):
        """任务不存在。"""
        mock_scheduler = MagicMock()
        mock_scheduler.update_action.return_value = False
        with _patch_scheduler(mock_scheduler):
            result = await schedule_update(
                "task-1", action='{"type": "shell", "command": "ls"}'
            )
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_update_schedule(self):
        """更新 schedule。"""
        mock_scheduler = MagicMock()
        mock_scheduler.update_schedule.return_value = True
        with _patch_scheduler(mock_scheduler):
            result = await schedule_update("task-1", schedule="0 * * * *")
        assert "已更新任务" in result
        mock_scheduler.update_schedule.assert_called_once()


class TestScheduleToggle:
    """schedule_toggle 测试。"""

    @pytest.mark.asyncio
    async def test_enable(self):
        """启用。"""
        mock_scheduler = MagicMock()
        mock_scheduler.toggle_task.return_value = True
        with _patch_scheduler(mock_scheduler):
            result = await schedule_toggle("task-1", True)
        assert "已启用定时任务" in result

    @pytest.mark.asyncio
    async def test_disable(self):
        """禁用。"""
        mock_scheduler = MagicMock()
        mock_scheduler.toggle_task.return_value = True
        with _patch_scheduler(mock_scheduler):
            result = await schedule_toggle("task-1", False)
        assert "已禁用定时任务" in result

    @pytest.mark.asyncio
    async def test_not_found(self):
        """任务不存在。"""
        mock_scheduler = MagicMock()
        mock_scheduler.toggle_task.return_value = False
        with _patch_scheduler(mock_scheduler):
            result = await schedule_toggle("nonexistent", True)
        assert "不存在" in result
