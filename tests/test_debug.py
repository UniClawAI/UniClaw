"""utils/debug.py 心跳阻塞检测纯逻辑测试。

只测注册/注销/心跳协程/装饰器这些不依赖线程轮询的部分
(watchdog 线程 + time.sleep 轮询不适合单测, 不覆盖):
- _HeartbeatEntry 默认值
- _register 注册与幂等
- _unregister 移除与取消任务
- _heartbeat 刷新 last_active / entry 移除后自动退出
- heartbeat 装饰器: 调用期间注册, 结束后清理(含异常路径)
"""

import asyncio
import threading
import time
from contextlib import suppress
from unittest.mock import patch

import pytest

from uniclaw.utils.debug import (
    _HeartbeatEntry,
    _entries,
    _heartbeat,
    _register,
    _unregister,
    heartbeat,
)


@pytest.fixture
async def clean_registry():
    """清空注册表并屏蔽 watchdog 线程启动, 测试后清理残留心跳任务。"""
    _entries.clear()
    with patch("uniclaw.utils.debug._ensure_watchdog"):
        yield
    for entry in list(_entries.values()):
        if entry.task:
            entry.task.cancel()
            with suppress(asyncio.CancelledError):
                await entry.task
    _entries.clear()


# ── _HeartbeatEntry ──────────────────────────────────────────


class TestHeartbeatEntry:
    def test_last_active_defaults_to_now(self):
        before = time.monotonic()
        entry = _HeartbeatEntry(name="n", thread_id=1, threshold=2.0)
        after = time.monotonic()
        assert entry.task is None
        assert before <= entry.last_active <= after

    def test_explicit_fields(self):
        entry = _HeartbeatEntry(name="n", thread_id=7, threshold=3.0, last_active=1.0)
        assert (entry.name, entry.thread_id, entry.threshold) == ("n", 7, 3.0)
        assert entry.last_active == 1.0


# ── _register / _unregister ──────────────────────────────────


class TestRegister:
    async def test_returns_loop_id_and_entry(self, clean_registry):
        loop = asyncio.get_running_loop()
        loop_id, entry = _register("loop-a", 1.5)
        assert loop_id == id(loop)
        assert _entries[loop_id] is entry
        assert entry.name == "loop-a"
        assert entry.threshold == 1.5
        assert entry.thread_id == threading.get_ident()

    async def test_starts_heartbeat_task(self, clean_registry):
        _, entry = _register("loop-b", 1.0)
        assert isinstance(entry.task, asyncio.Task)
        assert not entry.task.done()

    async def test_idempotent_per_loop(self, clean_registry):
        """同一事件循环重复注册返回已有 entry, 不新建任务也不覆盖参数。"""
        loop_id1, entry1 = _register("first", 1.0)
        task_before = entry1.task
        loop_id2, entry2 = _register("second", 5.0)
        assert loop_id1 == loop_id2
        assert entry1 is entry2
        assert entry2.name == "first"
        assert entry2.threshold == 1.0
        assert entry2.task is task_before


class TestUnregister:
    async def test_removes_entry_and_cancels_task(self, clean_registry):
        loop_id, entry = _register("loop-c", 1.0)
        task = entry.task
        _unregister(loop_id)
        assert loop_id not in _entries
        with suppress(asyncio.CancelledError):
            await task
        assert task.cancelled()

    async def test_unknown_loop_id_noop(self, clean_registry):
        _unregister(123456789)  # 不抛异常
        assert _entries == {}


# ── _heartbeat 协程 ──────────────────────────────────────────


class TestHeartbeatCoroutine:
    async def test_refreshes_last_active(self, clean_registry, monkeypatch):
        monkeypatch.setattr("uniclaw.utils.debug._HEARTBEAT_INTERVAL", 0.01)
        loop_id = 999001
        entry = _HeartbeatEntry(
            name="t", thread_id=threading.get_ident(), threshold=10.0
        )
        stale = time.monotonic() - 5.0
        entry.last_active = stale
        _entries[loop_id] = entry
        task = asyncio.create_task(_heartbeat(loop_id))
        try:
            await asyncio.sleep(0.05)
            assert entry.last_active > stale
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            _entries.pop(loop_id, None)

    async def test_exits_when_entry_removed(self, clean_registry, monkeypatch):
        """entry 被移除后心跳协程正常返回(而非被取消)。"""
        monkeypatch.setattr("uniclaw.utils.debug._HEARTBEAT_INTERVAL", 0.01)
        loop_id = 999002
        entry = _HeartbeatEntry(
            name="t", thread_id=threading.get_ident(), threshold=10.0
        )
        _entries[loop_id] = entry
        task = asyncio.create_task(_heartbeat(loop_id))
        await asyncio.sleep(0.02)  # 至少跑一轮心跳
        _entries.pop(loop_id)
        done, _ = await asyncio.wait({task}, timeout=1.0)
        assert task in done
        assert not task.cancelled()
        assert task.exception() is None


# ── heartbeat 装饰器 ─────────────────────────────────────────


class TestHeartbeatDecorator:
    async def test_registers_during_call_and_cleans_after(self, clean_registry):
        @heartbeat(threshold=2.0)
        async def demo():
            assert id(asyncio.get_running_loop()) in _entries
            return 42

        assert await demo() == 42
        assert _entries == {}

    async def test_uses_qualname_by_default(self, clean_registry):
        captured = {}

        @heartbeat(threshold=2.0)
        async def my_func():
            captured["name"] = next(iter(_entries.values())).name

        await my_func()
        assert captured["name"] == my_func.__qualname__

    async def test_custom_name(self, clean_registry):
        captured = {}

        @heartbeat(threshold=2.0, name="custom-loop")
        async def my_func():
            captured["name"] = next(iter(_entries.values())).name

        await my_func()
        assert captured["name"] == "custom-loop"

    async def test_cleans_up_on_exception(self, clean_registry):
        """被装饰函数抛异常时仍注销心跳, 异常正常向上传播。"""

        @heartbeat(threshold=2.0)
        async def boom():
            raise ValueError("x")

        with pytest.raises(ValueError):
            await boom()
        assert _entries == {}

    async def test_preserves_metadata(self, clean_registry):
        @heartbeat(threshold=2.0)
        async def documented():
            """doc here"""
            return None

        assert documented.__name__ == "documented"
        assert documented.__doc__ == "doc here"
