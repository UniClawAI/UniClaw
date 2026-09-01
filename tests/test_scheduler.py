"""
定时任务调度器的单元测试
"""

import json
import pytest
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timedelta

from uniclaw.tools.scheduler.scheduler import Scheduler, _parse_cron
from uniclaw.tools.scheduler.tools import _validate_action


@pytest.fixture(autouse=True)
def tmp_config(tmp_path):
    """每个测试使用独立的临时配置文件"""
    fake_dir = tmp_path / ".UniClaw" / "schedule"
    fake_dir.mkdir(parents=True)
    config_file = fake_dir / "scheduler.json"
    with patch.object(Scheduler, "_instance", None):
        with patch(
            "uniclaw.tools.scheduler.scheduler.get_app_dir",
            return_value=fake_dir.parent,
        ):
            yield config_file


@pytest.fixture
def scheduler():
    return Scheduler.get_instance()


class TestParseCron:
    """Cron 表达式解析测试"""

    def test_every_minute(self):
        result = _parse_cron("* * * * *")
        assert result is not None

    def test_every_5_minutes(self):
        result = _parse_cron("*/5 * * * *")
        assert result is not None

    def test_daily_at_9am(self):
        result = _parse_cron("0 9 * * *")
        assert result is not None

    def test_weekdays_at_9am(self):
        result = _parse_cron("0 9 * * 1-5")
        assert result is not None

    def test_with_seconds(self):
        result = _parse_cron("0 */5 * * * *")
        assert result is not None

    def test_invalid_cron(self):
        with pytest.raises(ValueError):
            _parse_cron("invalid")
        with pytest.raises(ValueError):
            _parse_cron("60 * * * *")
        with pytest.raises(ValueError):
            _parse_cron("")


class TestValidateAction:
    """action JSON 校验测试 (Bug #8 回归)"""

    def test_shell_missing_command(self):
        err = _validate_action('{"type": "shell"}')
        assert err is not None
        assert "缺少 'command'" in err

    def test_shell_empty_command(self):
        """Bug #8 回归:空命令必须被拒绝。"""
        err = _validate_action('{"type": "shell", "command": ""}')
        assert err is not None
        assert "不能为空" in err

    def test_shell_whitespace_command(self):
        """纯空白命令同样拒绝。"""
        err = _validate_action('{"type": "shell", "command": "   "}')
        assert err is not None
        assert "不能为空" in err

    def test_shell_valid_command(self):
        assert _validate_action('{"type": "shell", "command": "git status"}') is None

    def test_monitor_empty_command(self):
        """monitor 类型的 command 空值同样拒绝。"""
        err = _validate_action(
            '{"type": "monitor", "command": "", "agent": {"message": "挂了"}}'
        )
        assert err is not None
        assert "不能为空" in err

    def test_monitor_valid(self):
        action = (
            '{"type": "monitor", "command": "curl -sf http://localhost:8080", '
            '"agent": {"message": "挂了"}}'
        )
        assert _validate_action(action) is None

    def test_get_next_run_time(self):
        cron = _parse_cron("0 9 * * *")
        now = datetime.now()
        next_run = cron.get_next(datetime, start_time=now)
        assert next_run > now
        assert next_run.hour == 9
        assert next_run.minute == 0


class TestSchedulerCRUD:
    """调度器 CRUD 操作测试"""

    def test_add_task(self, scheduler):
        task = scheduler.add_task("测试任务", "0 9 * * *", "shell: echo hello")
        assert task is not None
        tasks = scheduler.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["id"] == task.id
        assert tasks[0]["name"] == "测试任务"
        assert tasks[0]["schedule"] == "0 9 * * *"
        assert tasks[0]["action"] == "shell: echo hello"
        assert tasks[0]["enabled"] is True

    def test_add_task_returns_unique_ids(self, scheduler):
        t1 = scheduler.add_task("任务1", "0 9 * * *", "shell: echo 1")
        t2 = scheduler.add_task("任务2", "0 9 * * *", "shell: echo 2")
        assert t1 is not None
        assert t2 is not None
        assert t1.id != t2.id

    def test_add_task_invalid_schedule(self, scheduler):
        with pytest.raises(ValueError, match="无效的 Cron 表达式"):
            scheduler.add_task("测试", "invalid", "shell: echo hello")

    def test_remove_task(self, scheduler):
        task = scheduler.add_task("测试", "0 9 * * *", "shell: echo hello")
        assert scheduler.remove_task(task.id) is True
        assert scheduler.list_tasks() == []

    def test_remove_task_not_found(self, scheduler):
        assert scheduler.remove_task("nonexistent") is False

    def test_toggle_task(self, scheduler):
        task = scheduler.add_task("测试", "0 9 * * *", "shell: echo hello")
        assert scheduler.toggle_task(task.id, False) is True
        tasks = scheduler.list_tasks()
        assert tasks[0]["enabled"] is False
        assert scheduler.toggle_task(task.id, True) is True
        tasks = scheduler.list_tasks()
        assert tasks[0]["enabled"] is True

    def test_toggle_task_not_found(self, scheduler):
        assert scheduler.toggle_task("nonexistent", True) is False

    def test_persistence(self, scheduler, tmp_config):
        task = scheduler.add_task("持久化测试", "0 9 * * *", "shell: echo hello")
        # 直接读文件验证持久化
        data = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert task.id in data["tasks"]
        assert data["tasks"][task.id]["name"] == "持久化测试"


class TestSchedulerExecution:
    """调度器执行逻辑测试"""

    @pytest.mark.asyncio
    async def test_task_runs_when_due(self, scheduler):
        task = scheduler.add_task("", "*/5 * * * *", "shell: echo hello")
        # 设置 last_run 为 10 分钟前
        scheduler._tasks[task.id].last_run = (
            datetime.now() - timedelta(minutes=10)
        ).isoformat(timespec="seconds")
        scheduler.save_config()

        await scheduler._check_and_run_tasks()

        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] is not None

    @pytest.mark.asyncio
    async def test_task_not_runs_when_not_due(self, scheduler):
        task = scheduler.add_task("", "*/5 * * * *", "shell: echo hello")
        now_str = datetime.now().isoformat(timespec="seconds")
        scheduler._tasks[task.id].last_run = now_str
        scheduler.save_config()

        await scheduler._check_and_run_tasks()

        # last_run 应该没变（没执行）
        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] == now_str

    @pytest.mark.asyncio
    async def test_first_run_immediately(self, scheduler):
        task = scheduler.add_task("", "* * * * *", "shell: echo hello")

        await scheduler._check_and_run_tasks()

        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] is not None

    @pytest.mark.asyncio
    async def test_disabled_task_skipped(self, scheduler):
        task = scheduler.add_task("", "* * * * *", "shell: echo hello")
        scheduler.toggle_task(task.id, False)

        await scheduler._check_and_run_tasks()

        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] is None


class TestSchedulerSingleton:
    """单例测试"""

    def test_same_instance(self):
        a = Scheduler.get_instance()
        b = Scheduler.get_instance()
        assert a is b


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
