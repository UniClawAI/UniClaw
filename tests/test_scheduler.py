"""
定时任务调度器的单元测试
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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

    def test_six_fields_rejected(self):
        """6 字段(秒级)不支持,拒绝并说明需要 5 字段。"""
        with pytest.raises(ValueError, match="需要 5 字段"):
            _parse_cron("0 */5 * * * *")

    def test_six_fields_rejected_with_count(self):
        """6 字段拒绝时指出收到 6 个。"""
        with pytest.raises(ValueError, match="收到 6 个"):
            _parse_cron("1 2 3 4 5 6")

    def test_four_fields_rejected_with_count(self):
        """4 字段拒绝并说明字段数,而不是笼统报错。"""
        with pytest.raises(ValueError, match="需要 5 字段"):
            _parse_cron("5 4 * *")
        with pytest.raises(ValueError, match="收到 4 个"):
            _parse_cron("5 4 * *")

    def test_rejection_includes_format_example(self):
        """拒绝时给出有效格式示例(分 时 日 月 周)。"""
        with pytest.raises(ValueError, match="分 时 日 月 周"):
            _parse_cron("5 4 * *")

    def test_never_fires_feb_31_rejected(self):
        """2 月 31 日永不触发,拒绝并点明原因。"""
        with pytest.raises(ValueError, match="永不会触发"):
            _parse_cron("0 0 31 2 2")
        with pytest.raises(ValueError, match="2 月没有 31 日"):
            _parse_cron("0 0 31 2 2")

    def test_never_fires_apr_31_rejected(self):
        """4 月 31 日永不触发。"""
        with pytest.raises(ValueError, match="4 月没有 31 日"):
            _parse_cron("0 0 31 4 *")

    def test_leap_day_allowed(self):
        """2 月 29 日合法(闰年存在),不能误判为永不触发。"""
        assert _parse_cron("0 0 29 2 *") is not None

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

    ACTION_OK = '{"type": "py", "code": "result=1"}'
    ACTION_BAD_JSON = "shell: echo hello"

    @pytest.mark.asyncio
    async def test_task_runs_when_due(self, scheduler):
        task = scheduler.add_task("", "*/5 * * * *", self.ACTION_OK)
        # 设置 last_run 为 10 分钟前
        old = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        scheduler._tasks[task.id].last_run = old
        scheduler.save_config()

        await scheduler._check_and_run_tasks()

        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] is not None
        assert tasks[0]["last_run"] != old

    @pytest.mark.asyncio
    async def test_task_not_runs_when_not_due(self, scheduler):
        task = scheduler.add_task("", "*/5 * * * *", self.ACTION_OK)
        now_str = datetime.now().isoformat(timespec="seconds")
        scheduler._tasks[task.id].last_run = now_str
        scheduler.save_config()

        await scheduler._check_and_run_tasks()

        # last_run 应该没变（没执行）
        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] == now_str

    @pytest.mark.asyncio
    async def test_first_run_immediately(self, scheduler):
        task = scheduler.add_task("", "* * * * *", self.ACTION_OK)

        await scheduler._check_and_run_tasks()

        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] is not None

    @pytest.mark.asyncio
    async def test_disabled_task_skipped(self, scheduler):
        task = scheduler.add_task("", "* * * * *", self.ACTION_OK)
        scheduler.toggle_task(task.id, False)

        await scheduler._check_and_run_tasks()

        tasks = scheduler.list_tasks()
        # 禁用时 toggle 不改 last_run;若从未执行则保持 None
        assert tasks[0]["last_run"] is None

    @pytest.mark.asyncio
    async def test_last_run_not_written_when_action_never_ran(self, scheduler):
        """动作根本没跑起来(JSON 非法)时,不得写入 last_run 冒充已执行。"""
        task = scheduler.add_task("", "* * * * *", self.ACTION_BAD_JSON)

        await scheduler._check_and_run_tasks()

        tasks = scheduler.list_tasks()
        assert tasks[0]["last_run"] is None

    @pytest.mark.asyncio
    async def test_last_run_uses_real_execution_time(self, scheduler):
        """last_run 是实际执行时刻,不是注册时刻。"""
        task = scheduler.add_task("", "* * * * *", self.ACTION_OK)
        created = task.created
        assert task.last_run is None

        await asyncio.sleep(1)
        await scheduler._check_and_run_tasks()

        last_run = scheduler.list_tasks()[0]["last_run"]
        assert last_run is not None
        assert last_run != created


class TestLastRunSemantics:
    """last_run 是真实执行事实: 注册不伪造,更新/启用不清零历史。
    (第 29 轮 #6: update 把真实执行记录清成"从未执行"是误导,
    "配置更新后是否执行过"由 updated 时间对比体现。)"""

    def test_add_task_last_run_is_none(self, scheduler):
        """新建任务不得把注册时刻写成 last_run。"""
        task = scheduler.add_task("每日报告", "0 4 * * *", '{"type": "py", "code": "1"}')
        assert task.last_run is None
        assert scheduler.list_tasks()[0]["last_run"] is None

    def test_add_task_unique_by_name_last_run_is_none(self, scheduler):
        """unique_by_name 新建同样不得伪造 last_run。"""
        task = scheduler.add_task(
            "巡检", "0 4 * * *", '{"type": "py", "code": "1"}', unique_by_name=True
        )
        assert task.last_run is None

    def test_unique_by_name_update_keeps_last_run(self, scheduler):
        """同名更新保留真实执行记录,不伪造"从未执行"。"""
        t1 = scheduler.add_task(
            "巡检", "0 4 * * *", '{"type": "py", "code": "1"}', unique_by_name=True
        )
        scheduler._tasks[t1.id].last_run = datetime.now().isoformat(timespec="seconds")
        scheduler.save_config()

        t2 = scheduler.add_task(
            "巡检", "0 5 * * *", '{"type": "py", "code": "2"}', unique_by_name=True
        )
        assert t2.id == t1.id
        assert t2.last_run is not None

    def test_update_action_keeps_last_run(self, scheduler):
        task = scheduler.add_task("t", "0 4 * * *", '{"type": "py", "code": "1"}')
        scheduler._tasks[task.id].last_run = datetime.now().isoformat(timespec="seconds")
        scheduler.save_config()

        scheduler.update_action(task.id, '{"type": "py", "code": "2"}')
        assert scheduler.get_task(task.id).last_run is not None

    def test_update_schedule_keeps_last_run(self, scheduler):
        task = scheduler.add_task("t", "0 4 * * *", '{"type": "py", "code": "1"}')
        scheduler._tasks[task.id].last_run = datetime.now().isoformat(timespec="seconds")
        scheduler.save_config()

        scheduler.update_schedule(task.id, "0 5 * * *")
        assert scheduler.get_task(task.id).last_run is not None

    def test_reenable_keeps_last_run(self, scheduler):
        """重新启用保留真实执行记录;启用后是否执行过看 updated 对比。"""
        task = scheduler.add_task("t", "0 4 * * *", '{"type": "py", "code": "1"}')
        scheduler._tasks[task.id].last_run = datetime.now().isoformat(timespec="seconds")
        scheduler.save_config()

        scheduler.toggle_task(task.id, False)
        scheduler.toggle_task(task.id, True)
        assert scheduler.get_task(task.id).last_run is not None


class TestSchedulerSingleton:
    """单例测试"""

    def test_same_instance(self):
        a = Scheduler.get_instance()
        b = Scheduler.get_instance()
        assert a is b


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

class TestSchedulerNoReentry:
    @pytest.mark.asyncio
    async def test_running_task_not_requeued(self):
        """执行中的任务不再派发(防慢任务跨 tick 重复执行)。"""
        from uniclaw.tools.scheduler.scheduler import Scheduler, Task

        sched = Scheduler()
        sched._running = {"t1"}  # 模拟 t1 正在执行
        task = Task(
            id="t1",
            name="n",
            schedule="* * * * *",
            action='{"type": "py", "code": "1"}',
            enabled=True,
            last_run=None,
        )
        sched._tasks = {"t1": task}

        calls = []

        async def fake_execute(task_id, t, config):
            calls.append(task_id)
            return True

        sched._execute_task = fake_execute
        sched.save_config = MagicMock()
        sched.load_config = MagicMock()  # 不从磁盘重载,保持注入的任务

        await sched._check_and_run_tasks(None)
        assert calls == [], "执行中的任务被重复派发"

        sched._running.clear()
        await sched._check_and_run_tasks(None)
        assert calls == ["t1"], "执行完成后应正常派发"

class TestLastRunSurvivesReload:
    """update 中途 load_config 重建任务对象时,last_run 更新不得丢(验证 #3)。"""

    @pytest.mark.asyncio
    async def test_last_run_written_to_live_object(self):
        from uniclaw.tools.scheduler.scheduler import Scheduler, Task

        sched = Scheduler()
        snapshot = Task(
            id="t1",
            name="n",
            schedule="* * * * *",
            action='{"type": "py", "code": "1"}',
            enabled=True,
            last_run=None,
        )
        sched._tasks = {"t1": snapshot}

        async def execute_and_replace(task_id, t, config):
            # 模拟执行期间用户 update → load_config 重建任务对象
            live = Task(
                id="t1",
                name="n",
                schedule="* * * * *",
                action='{"type": "py", "code": "2"}',
                enabled=True,
                last_run=None,
            )
            sched._tasks = {"t1": live}
            return True

        sched._execute_task = execute_and_replace
        sched.load_config = MagicMock()
        sched.save_config = MagicMock()

        await sched._check_and_run_tasks(None)

        assert sched._tasks["t1"].last_run is not None, (
            "last_run 必须回写到当前 self._tasks 对象,否则下个 tick 重触发循环"
        )
        assert snapshot.last_run is not None

class TestShellTimeout:
    """60s 超时报"超时已中止"(验证 #3-3)。

    进程树回收由 run_command 内部完成(taskkill /T 或 killpg,见
    test_process 的超时用例),这里验证 _exec_shell 把 timed_out
    翻译成 TimeoutError 且不伪装成"执行失败"。
    """

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        from uniclaw.tools.scheduler.scheduler import Scheduler
        from uniclaw.utils.process import RunResult

        sched = Scheduler()
        task = MagicMock()
        task.root_dir = None
        task.name = "t"
        task.id = "t1"

        with patch(
            "uniclaw.tools.scheduler.scheduler.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(-1, timed_out=True),
        ) as m:
            with pytest.raises(TimeoutError) as ei:
                await sched._exec_shell("sleep 70", task, None)

        assert m.await_args.args[0] == "sleep 70"
        assert "60s" in str(ei.value) or "超时" in str(ei.value)

    @pytest.mark.asyncio
    async def test_monitor_timeout_raises(self):
        """monitor 同样有超时保护,且同样以 TimeoutError 中止(不再泄漏子进程)。"""
        from uniclaw.tools.scheduler.scheduler import Scheduler
        from uniclaw.utils.process import RunResult

        sched = Scheduler()
        task = MagicMock()
        task.root_dir = None
        task.name = "t"
        task.id = "t1"

        with patch(
            "uniclaw.tools.scheduler.scheduler.run_command",
            new_callable=AsyncMock,
            return_value=RunResult(-1, timed_out=True),
        ):
            with pytest.raises(TimeoutError):
                await sched._exec_monitor("sleep 70", {}, task, "t", None)

