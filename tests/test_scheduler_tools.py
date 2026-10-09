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

    @pytest.mark.asyncio
    async def test_list_never_run_shows_never(self):
        """last_run 为 None 时显示「从未执行」,不能显示 None 或伪造时刻。"""
        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = [
            {
                "id": "task-1",
                "name": "每日报告",
                "schedule": "0 4 * * *",
                "action": '{"type": "shell", "command": "echo hi"}',
                "enabled": True,
                "last_run": None,
            }
        ]
        with _patch_scheduler(mock_scheduler):
            result = await schedule_list()
        assert "从未执行" in result
        assert "None" not in result

    @pytest.mark.asyncio
    async def test_list_executed_shows_real_time(self):
        """执行过则显示真实时刻。"""
        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = [
            {
                "id": "task-1",
                "name": "每日报告",
                "schedule": "0 4 * * *",
                "action": '{"type": "shell", "command": "echo hi"}',
                "enabled": True,
                "last_run": "2026-10-07T18:06:16",
            }
        ]
        with _patch_scheduler(mock_scheduler):
            result = await schedule_list()
        assert "2026-10-07T18:06:16" in result
        assert "从未执行" not in result


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


# ── 重复创建拒绝 / bash 语法透明提示(第 28 轮 #6/#7) ──────


class TestDuplicateCreate:
    """name+schedule+action 三项全同的重复任务必须拒绝。"""

    @pytest.mark.asyncio
    async def test_exact_duplicate_rejected(self):
        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = [
            {
                "id": "task-1",
                "name": "每日报告",
                "schedule": "0 9 * * *",
                "action": '{"type": "shell", "command": "ls"}',
                "enabled": True,
            }
        ]
        with _patch_scheduler(mock_scheduler):
            result = await schedule_create(
                "每日报告",
                "0 9 * * *",
                '{"type": "shell", "command": "ls"}',
                tool_runtime=ToolRuntime(config=MagicMock()),
            )
        assert "已存在" in result
        assert "task-1" in result
        mock_scheduler.add_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_ignores_key_order(self):
        """action JSON 键序不同但等价,同样视为重复。"""
        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = [
            {
                "id": "task-1",
                "name": "t",
                "schedule": "* * * * *",
                "action": '{"command": "ls", "type": "shell"}',
                "enabled": True,
            }
        ]
        with _patch_scheduler(mock_scheduler):
            result = await schedule_create(
                "t",
                "* * * * *",
                '{"type": "shell", "command": "ls"}',
                tool_runtime=ToolRuntime(config=MagicMock()),
            )
        assert "已存在" in result

    @pytest.mark.asyncio
    async def test_different_action_still_created(self):
        mock_scheduler = MagicMock()
        mock_scheduler.list_tasks.return_value = [
            {
                "id": "task-1",
                "name": "t",
                "schedule": "* * * * *",
                "action": '{"type": "shell", "command": "ls"}',
                "enabled": True,
            }
        ]
        task = MagicMock()
        task.id = "task-2"
        mock_scheduler.add_task.return_value = task
        with _patch_scheduler(mock_scheduler):
            result = await schedule_create(
                "t",
                "* * * * *",
                '{"type": "shell", "command": "pwd"}',
                tool_runtime=ToolRuntime(config=MagicMock()),
            )
        assert "已创建定时任务" in result
        mock_scheduler.add_task.assert_called_once()


class TestBashSyntaxWarning:
    """Windows 下 $()/反引号命令要透明提示(cmd 不展开)。"""

    def test_dollar_paren_warned_on_win32(self, monkeypatch):
        from uniclaw.tools.scheduler.tools import _win32_bash_syntax_warning

        monkeypatch.setattr("uniclaw.tools.scheduler.tools.sys.platform", "win32")
        warn = _win32_bash_syntax_warning(
            '{"type": "shell", "command": "echo $(date +%H%M%S)"}'
        )
        assert "bash" in warn
        assert "不会展开" in warn

    def test_backtick_warned_on_win32(self, monkeypatch):
        from uniclaw.tools.scheduler.tools import _win32_bash_syntax_warning

        monkeypatch.setattr("uniclaw.tools.scheduler.tools.sys.platform", "win32")
        warn = _win32_bash_syntax_warning(
            '{"type": "shell", "command": "echo `date`"}'
        )
        assert "不会展开" in warn

    def test_no_warning_for_plain_command(self, monkeypatch):
        from uniclaw.tools.scheduler.tools import _win32_bash_syntax_warning

        monkeypatch.setattr("uniclaw.tools.scheduler.tools.sys.platform", "win32")
        assert (
            _win32_bash_syntax_warning('{"type": "shell", "command": "git status"}')
            == ""
        )

    def test_no_warning_when_bash_wrapped(self, monkeypatch):
        from uniclaw.tools.scheduler.tools import _win32_bash_syntax_warning

        monkeypatch.setattr("uniclaw.tools.scheduler.tools.sys.platform", "win32")
        assert (
            _win32_bash_syntax_warning(
                '{"type": "shell", "command": "bash -c \'echo $(date)\'"}'
            )
            == ""
        )

    def test_no_warning_on_posix(self, monkeypatch):
        from uniclaw.tools.scheduler.tools import _win32_bash_syntax_warning

        monkeypatch.setattr("uniclaw.tools.scheduler.tools.sys.platform", "linux")
        assert (
            _win32_bash_syntax_warning(
                '{"type": "shell", "command": "echo $(date)"}'
            )
            == ""
        )


# ── 死循环检测: 结果有进展不误报(第 28 轮 #8) ──────────


class TestLoopDetectionProgress:
    """同签名连发但每次结果都在变化时,不算死循环(back 连续后退场景)。"""

    def _make(self):
        from unittest.mock import AsyncMock

        from uniclaw.agent.multi_agent import MultiAgent

        ma = MagicMock(spec=MultiAgent)
        ma.send_event_to_user = AsyncMock(return_value=None)
        return ma

    @pytest.mark.asyncio
    async def test_progressing_results_not_blocked(self):
        from uniclaw.agent import multi_agent as ma_mod

        inst = self._make()
        task = MagicMock()
        config = MagicMock()
        records: list[tuple] = []
        calls = [
            {
                "id": f"c{i}",
                "function": {"name": "browser_back", "arguments": "{}"},
            }
            for i in range(5)
        ]

        async def fake_execute(*args, **kwargs):
            return False

        inst._execute_tool_calls = fake_execute

        blocked = []
        for i, tc in enumerate(calls):
            # 每次执行后回填不同结果指纹(页面真的在后退)
            result = await ma_mod.MultiAgent._check_infinite_loop(
                inst, [tc], records, task, config
            )
            blocked.append(result)
            if records:  # 触发死循环时记录已被清空,无需回填
                sig, _ = records[-1]
                records[-1] = (sig, f"已后退 URL=/page{4 - i}")

        assert blocked == [False] * 5, (
            "每次结果都在推进,连续 5 次同签名也不该判死循环"
        )

    @pytest.mark.asyncio
    async def test_stuck_results_blocked(self):
        from uniclaw.agent import multi_agent as ma_mod

        inst = self._make()
        task = MagicMock()
        config = MagicMock()
        records: list[tuple] = []
        calls = [
            {
                "id": f"c{i}",
                "function": {"name": "browser_back", "arguments": "{}"},
            }
            for i in range(6)
        ]

        blocked = []
        for tc in calls:
            result = await ma_mod.MultiAgent._check_infinite_loop(
                inst, [tc], records, task, config
            )
            blocked.append(result)
            if records:
                sig, _ = records[-1]
                records[-1] = (sig, "无法后退 —— 已是历史起点")  # 结果原地打转

        # 第 5 次起判定死循环(前 4 次记录 + 本次占位,结果指纹全同)
        assert blocked[4] is True
        assert blocked[3] is False
