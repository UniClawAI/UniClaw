import asyncio
from collections import deque
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class MonitorStatus(StrEnum):
    """监控状态枚举"""

    RUNNING = "running"
    MATCHED = "matched"
    STOPPED = "stopped"
    TIMEOUT = "timeout"
    ERROR = "error"


class Monitor:
    """单个监控任务"""

    def __init__(
        self,
        monitor_id: str,
        command: str,
        pattern: str,
        description: str,
        timeout: int,
        notify_model: bool = True,
        cwd: Path | None = None,
        config=None,
    ):
        self.id = monitor_id
        self.command = command
        self.pattern = pattern
        self.description = description
        self.timeout = timeout
        self.notify_model = notify_model
        self.cwd = cwd
        self.config = config
        self.status = MonitorStatus.RUNNING
        self.process: asyncio.subprocess.Process | None = None
        self.stdout_thread: asyncio.Task | None = None
        self.stderr_thread: asyncio.Task | None = None
        self.output_lines: deque[str] = deque(maxlen=1000)
        self.matched_lines: list[str] = []
        self.start_time = datetime.now()
        self.match_time: datetime | None = None

    def to_dict(self) -> dict:
        uptime = (datetime.now() - self.start_time).total_seconds()
        return {
            "id": self.id,
            "command": self.command,
            "pattern": self.pattern,
            "description": self.description,
            "status": self.status.value,
            "uptime_seconds": int(uptime),
            "output_lines": len(self.output_lines),
            "matched_count": len(self.matched_lines),
        }
