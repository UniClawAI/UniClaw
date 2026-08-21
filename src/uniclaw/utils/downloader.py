"""下载器抽象基类 — HttpDownloader 与 M3u8Downloader 的公共接口。

DownloadManager 仅依赖这里的接口统一管理任务生命周期,不关心具体下载引擎。
"""

import asyncio
from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path
from typing import Awaitable, Callable, Protocol


class ProgressProtocol(Protocol):
    """下载进度公共接口,DownloadProgress / M3u8Progress 均需满足。"""

    status: StrEnum

    def format_status(self) -> str:
        """格式化为人类可读的状态字符串。"""


class BaseDownloader(ABC):
    """下载器抽象基类。

    定义 HttpDownloader / M3u8Downloader 的公共接口:

    - 属性: task_id / save_path / task / progress
    - 方法: start() / pause() / resume() / cancel()

    子类只需实现这些成员,即可被 DownloadManager 统一管理。
    """

    task_id: str
    save_path: Path
    task: asyncio.Task | None
    progress: ProgressProtocol

    @abstractmethod
    async def start(
        self,
        callback: Callable[..., Awaitable[None]] | None = None,
    ) -> ProgressProtocol:
        """执行下载,返回进度对象。

        Args:
            callback: 可选进度回调,每次下载一块数据时调用。
        """

    @abstractmethod
    def pause(self) -> None:
        """暂停下载。"""

    @abstractmethod
    def resume(self) -> None:
        """恢复下载。"""

    @abstractmethod
    def cancel(self) -> None:
        """取消下载。"""
