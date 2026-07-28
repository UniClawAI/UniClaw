"""调试辅助工具:检测事件循环阻塞并写入日志文件。

核心原理:每个事件循环注册独立的心跳任务,每 0.1 秒重置时间戳。
watchdog 线程统一扫描所有注册项,心跳超时说明对应事件循环被同步阻塞。
"""

import asyncio
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps

# 日志文件路径:写入用户级 .UniClaw/logs/ 目录
from uniclaw.context import get_app_dir

_LOG_DIR = get_app_dir(Path.cwd()) / "logs"
_LOG_FILE = _LOG_DIR / "slow_await.log"

# ── 全局状态 ────────────────────────────────────────────────────────────────

_HEARTBEAT_INTERVAL = 0.1  # 心跳间隔(秒)
_DEFAULT_THRESHOLD = 1.0  # 默认警告阈值(秒)


@dataclass
class _HeartbeatEntry:
    """单个事件循环的心跳监控项。"""

    name: str
    thread_id: int
    threshold: float
    last_active: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None


_entries: dict[int, _HeartbeatEntry] = {}  # {loop_id: entry}
_entries_lock = threading.Lock()
_watchdog_thread: threading.Thread | None = None
_watchdog_running = False


def _write_log(msg: str):
    """写入日志文件,带时间戳。"""
    os.makedirs(_LOG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")


# ── Watchdog 线程 ───────────────────────────────────────────────────────────


def _watchdog():
    """watchdog 线程:统一扫描所有注册的心跳项,超时则写入堆栈日志。"""
    while _watchdog_running:
        with _entries_lock:
            if _entries:
                interval = min(e.threshold for e in _entries.values())
                interval = max(interval, 0.1)
            else:
                interval = 0.1

        time.sleep(interval)
        now = time.monotonic()

        with _entries_lock:
            items = list(_entries.items())

        frames = sys._current_frames()

        for _, entry in items:
            elapsed = now - entry.last_active
            if elapsed < entry.threshold:
                continue

            main_frame = frames.get(entry.thread_id)
            if main_frame is None:
                continue

            lines = [
                f"⚠️  [{entry.name}] 事件循环阻塞超过 {elapsed:.1f}s",
                f"--- Thread {entry.thread_id} (blocked) ---",
            ]
            for line in traceback.format_stack(main_frame):
                lines.append(line.rstrip())

            log_msg = "\n".join(lines)
            _write_log(log_msg)
            print(f"\n{log_msg}\n", file=sys.stderr)


def _ensure_watchdog():
    """懒启动 watchdog 线程(只创建一次)。"""
    global _watchdog_thread, _watchdog_running
    if _watchdog_running:
        return
    _watchdog_running = True
    _watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
    _watchdog_thread.start()


# ── 异步心跳 ────────────────────────────────────────────────────────────────


async def _heartbeat(loop_id: int):
    """异步心跳:每 0.1 秒重置对应事件循环的时间戳。entry 被移除后自动退出。"""
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        with _entries_lock:
            entry = _entries.get(loop_id)
            if entry is None:
                return
            entry.last_active = time.monotonic()


def _register(name: str, threshold: float) -> tuple[int, _HeartbeatEntry]:
    """注册心跳监控项并启动心跳任务,返回 (loop_id, entry)。"""
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    thread_id = threading.current_thread().ident

    with _entries_lock:
        if loop_id in _entries:
            return loop_id, _entries[loop_id]
        entry = _HeartbeatEntry(
            name=name,
            thread_id=thread_id,
            threshold=threshold,
        )
        _entries[loop_id] = entry

    _ensure_watchdog()
    entry.task = asyncio.create_task(_heartbeat(loop_id))
    return loop_id, entry


def _unregister(loop_id: int):
    """移除心跳监控项并取消心跳任务。"""
    with _entries_lock:
        entry = _entries.pop(loop_id, None)
    if entry and entry.task:
        entry.task.cancel()


# ── 公开接口 ────────────────────────────────────────────────────────────────


def heartbeat(threshold: float = _DEFAULT_THRESHOLD, name: str | None = None):
    """装饰器:为被装饰的异步函数启用心跳阻塞检测。

    函数调用期间自动注册心跳,返回后自动清理。

    用法:
        @heartbeat(threshold=5.0)
        async def my_coro():
            ...
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            loop_id, _ = _register(name or func.__qualname__, threshold)
            try:
                return await func(*args, **kwargs)
            finally:
                _unregister(loop_id)

        return wrapper

    return decorator
