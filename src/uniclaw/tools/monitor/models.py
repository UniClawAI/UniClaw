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
        visible: bool = False,
    ):
        self.id = monitor_id
        self.command = command
        self.pattern = pattern
        self.description = description
        self.timeout = timeout
        self.notify_model = notify_model
        self.cwd = cwd
        self.config = config
        self.visible = visible
        self.status = MonitorStatus.RUNNING
        self.process: asyncio.subprocess.Process | None = None
        self.stdout_thread: asyncio.Task | None = None
        self.stderr_thread: asyncio.Task | None = None
        # 可视模式相关:查看器窗口进程、镜像日志/用户输入/退出标记文件
        self.viewer_process: asyncio.subprocess.Process | None = None
        self.viewer_thread: asyncio.Task | None = None
        self.log_path: Path | None = None
        self.input_path: Path | None = None
        self.exit_path: Path | None = None
        self.output_lines: deque[str] = deque(maxlen=1000)
        # 匹配历史: 有界 deque, 防止 pattern 频繁匹配时无限增长
        self.matched_lines: deque[str] = deque(maxlen=200)
        self.start_time = datetime.now()
        self.match_time: datetime | None = None
        # 通知节流: 最近一次通知时间(配合 manager._notify_interval 限制通知频率)
        self.last_notify_time: datetime | None = None
        # 结束唤醒恰好一次标记: 进程结束(退出/超时/异常)自动唤醒 agent,
        # 多条收尾路径(读线程/轮询线程)竞争时只通知一次
        self.exit_notified: bool = False
        # 输出缓冲满警告: 首次达到上限时插入警告(仅一次, 防止刷屏)
        self.buffer_full_warned: bool = False
        # ConPTY 会话(shell 类命令 + 可视模式时使用, 替代管道进程)
        self.pty = None
        # 进程退出码: 退出时由管道/ConPTY 路径写入; None = 尚未退出或未知
        self.exit_code: int | None = None

    def has_exited(self) -> bool:
        """进程是否已结束(含自然退出/超时/异常)。

        MATCHED 是准瞬态(后续未匹配输出会恢复 RUNNING), 视为仍在运行。
        """
        if self.status not in (MonitorStatus.RUNNING, MonitorStatus.MATCHED):
            return True
        if self.pty is not None:
            return bool(self.pty.exited.is_set())
        if self.process is not None and self.process.returncode is not None:
            return True
        return False

    def status_label(self) -> str:
        """状态列展示: 已结束且已知退出码时附带 exit=N。"""
        if self.exit_code is not None and self.status != MonitorStatus.RUNNING:
            return f"{self.status.value}(exit={self.exit_code})"
        return self.status.value

    def exit_summary(self) -> str:
        """退出码/结束方式摘要, 供 monitor_output / monitor_stop 使用。

        Returns:
            str: 如 "exit=42,非正常结束"; 未退出返回空串。
        """
        if not self.has_exited():
            return ""
        if self.exit_code is not None:
            kind = "正常结束" if self.exit_code == 0 else "非正常结束"
            return f"exit={self.exit_code},{kind}"
        if self.status == MonitorStatus.TIMEOUT:
            return "超时被终止,退出码未知"
        return "退出码未知"

    def to_dict(self) -> dict:
        uptime = (datetime.now() - self.start_time).total_seconds()
        return {
            "id": self.id,
            "command": self.command,
            "pattern": self.pattern,
            "description": self.description,
            "visible": self.visible,
            "status": self.status_label(),
            "raw_status": self.status.value,
            "exit_code": self.exit_code,
            "uptime_seconds": int(uptime),
            "output_lines": len(self.output_lines),
            "matched_count": len(self.matched_lines),
        }
