"""scheduler.Task 序列化与 update_action / update_schedule 的补充测试。

现有 test_scheduler.py 覆盖 CRUD 与调度循环,
本文件补齐 Task 字典往返、update_* 及 unique_by_name 分支。
"""

import pytest
from unittest.mock import patch

from uniclaw.tools.scheduler.scheduler import Scheduler, Task


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


class TestTaskSerialization:
    """Task.to_dict / from_dict 字典往返。"""

    def test_to_dict_roundtrip(self):
        task = Task(
            id="t1",
            name="任务",
            schedule="0 9 * * *",
            action="shell: echo hi",
        )
        data = task.to_dict()
        restored = Task.from_dict(data)
        assert restored == task

    def test_from_dict_full_fields(self):
        task = Task.from_dict(
            {
                "id": "t1",
                "name": "n",
                "schedule": "* * * * *",
                "action": "a",
                "root_dir": "D:/x",
                "enabled": False,
                "session_id": "s1",
                "last_run": "2026-01-01T00:00:00",
                "created": "2026-01-01T00:00:00",
                "updated": "2026-01-01T00:00:00",
            }
        )
        assert task.root_dir == "D:/x"
        assert task.enabled is False
        assert task.session_id == "s1"
        assert task.last_run is not None

    def test_from_dict_missing_fields_use_defaults(self):
        """缺省字段用默认值填充,不抛 KeyError。"""
        task = Task.from_dict({})
        assert task.id == ""
        assert task.enabled is True
        assert task.session_id is None
        assert task.last_run is None

    def test_to_dict_covers_all_fields(self):
        task = Task(id="t", name="n", schedule="s", action="a")
        assert set(task.to_dict()) == {
            "id",
            "name",
            "schedule",
            "action",
            "root_dir",
            "enabled",
            "session_id",
            "last_run",
            "created",
            "updated",
        }


class TestUpdateAction:
    """update_action 更新动作内容。"""

    def test_update_action(self, scheduler):
        task = scheduler.add_task("任务", "0 9 * * *", "shell: echo old")
        assert scheduler.update_action(task.id, "shell: echo new") is True
        updated = scheduler.get_task(task.id)
        assert updated.action == "shell: echo new"
        assert updated.updated is not None

    def test_update_action_persists(self, scheduler, tmp_config):
        task = scheduler.add_task("任务", "0 9 * * *", "shell: echo old")
        scheduler.update_action(task.id, "shell: echo new")
        # 重新加载验证已写盘
        scheduler.load_config()
        assert scheduler.get_task(task.id).action == "shell: echo new"

    def test_update_action_not_found(self, scheduler):
        assert scheduler.update_action("no-such-id", "shell: x") is False


class TestUpdateSchedule:
    """update_schedule 更新调度时间。"""

    def test_update_schedule(self, scheduler):
        task = scheduler.add_task("任务", "0 9 * * *", "shell: echo hi")
        assert scheduler.update_schedule(task.id, "*/5 * * * *") is True
        assert scheduler.get_task(task.id).schedule == "*/5 * * * *"

    def test_update_schedule_invalid_rejected(self, scheduler):
        """非法 cron 拒绝(抛 ValueError)且不改原值。"""
        task = scheduler.add_task("任务", "0 9 * * *", "shell: echo hi")
        with pytest.raises(ValueError):
            scheduler.update_schedule(task.id, "not-a-cron")
        assert scheduler.get_task(task.id).schedule == "0 9 * * *"

    def test_update_schedule_not_found(self, scheduler):
        assert scheduler.update_schedule("no-such-id", "* * * * *") is False


class TestUniqueByName:
    """add_task(unique_by_name=True) 同名任务去重。"""

    def test_first_create_then_update(self, scheduler):
        t1 = scheduler.add_task("巡检", "0 9 * * *", "shell: a", unique_by_name=True)
        t2 = scheduler.add_task("巡检", "0 10 * * *", "shell: b", unique_by_name=True)
        assert t2.id == t1.id
        assert scheduler.get_task(t1.id).schedule == "0 10 * * *"
        assert len(scheduler.list_tasks()) == 1

    def test_unique_off_creates_separate_tasks(self, scheduler):
        scheduler.add_task("巡检", "0 9 * * *", "shell: a")
        scheduler.add_task("巡检", "0 10 * * *", "shell: b")
        assert len(scheduler.list_tasks()) == 2
