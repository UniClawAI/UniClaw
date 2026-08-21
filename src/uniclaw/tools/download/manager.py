"""下载任务管理器 -- 管理异步下载任务的生命周期。

按下载类型拆分为两个独立管理器:
- HttpDownloadManager: 管理 HttpDownloader(普通 HTTP/HTTPS 文件下载)
- M3u8DownloadManager: 管理 M3u8Downloader(M3U8/HLS 流下载)

两者共享基类 DownloadManager 的通用逻辑(submit/get_status/pause/resume/cancel/remove_task),
仅 `_run_task`(完成后的唤醒消息)和 `_active_statuses`(活跃状态集合)不同。
"""

import asyncio

from uniclaw.config import AppConfig
from uniclaw.tools.download.m3u8 import M3u8Downloader
from uniclaw.utils.downloader import BaseDownloader
from uniclaw.utils.http_download import HttpDownloader, file_format


class DownloadManager:
    """下载任务管理器基类。

    子类需实现 `_run_task`(后台执行完成后的唤醒消息)并设置 `_active_statuses`
    (视为"活跃"、不可删除的状态集合)。
    """

    #: 视为活跃(不可删除)的状态值集合,子类按自身状态枚举覆盖
    _active_statuses: frozenset[str] = frozenset()

    def __init__(self):
        self._tasks: dict[str, BaseDownloader] = {}

    def submit(self, downloader: BaseDownloader, config: AppConfig) -> str:
        """提交异步下载任务,返回 task_id。"""
        task_id = downloader.task_id
        self._tasks[task_id] = downloader
        downloader.task = asyncio.create_task(
            self._run_task(task_id, downloader, config)
        )
        return task_id

    def get_status(self, task_id: str = "") -> str:
        """获取任务状态。为空则返回所有任务。"""
        if not self._tasks:
            return "当前没有下载任务。"

        if task_id:
            dl = self._tasks.get(task_id)
            if not dl:
                return f"未找到任务: {task_id}"
            return dl.progress.format_status()

        lines = []
        for tid, dl in self._tasks.items():
            lines.append(f"[{tid}] {dl.progress.format_status()}")
        return "\n\n".join(lines)

    def pause(self, task_id: str) -> str:
        """暂停下载任务。"""
        dl = self._tasks.get(task_id)
        if not dl:
            return f"未找到任务: {task_id}"
        dl.pause()
        return f"任务 {task_id} 已暂停。"

    def resume(self, task_id: str) -> str:
        """恢复下载任务。"""
        dl = self._tasks.get(task_id)
        if not dl:
            return f"未找到任务: {task_id}"
        dl.resume()
        return f"任务 {task_id} 已恢复。"

    def cancel(self, task_id: str) -> str:
        """取消下载任务。"""
        dl = self._tasks.get(task_id)
        if not dl:
            return f"未找到任务: {task_id}"
        dl.cancel()
        return f"任务 {task_id} 已取消。"

    def remove_task(self, task_id: str) -> str:
        """手动删除已完成/失败/取消的任务,释放内存。"""
        dl = self._tasks.get(task_id)
        if not dl:
            return f"未找到任务: {task_id}"
        if dl.progress.status.value in self._active_statuses:
            return f"任务 {task_id} 正在下载中,请先取消后再删除。"
        del self._tasks[task_id]
        return f"任务 {task_id} 已删除。"

    async def _run_task(
        self, task_id: str, downloader: BaseDownloader, config: AppConfig
    ):
        """后台执行下载,完成后通过 wakeup 通知模型(子类实现)。"""
        raise NotImplementedError


class HttpDownloadManager(DownloadManager):
    """管理 HttpDownloader 任务(普通 HTTP/HTTPS 文件下载)。"""

    _active_statuses = frozenset({"pending", "downloading", "paused"})

    async def _run_task(
        self, task_id: str, downloader: HttpDownloader, config: AppConfig
    ):
        """后台执行下载,完成后通过 wakeup 通知模型。

        注意: config 是 per-session 的引用,如果下载期间会话结束,
        wake_agent 会安全降级(config.current_agent 为 None 时返回 False)。
        """
        from uniclaw.utils.http_download import DownloadStatus

        try:
            progress = await downloader.start()

            # 通知模型
            from uniclaw.utils.constants import SYSTEM_PREFIX
            from uniclaw.utils.wakeup import wake_agent
            from uniclaw.tools.download.http_tools import (
                http_download,
                http_download_status,
                http_download_remove,
            )

            if progress.status == DownloadStatus.COMPLETED:
                message = (
                    f"{SYSTEM_PREFIX}(download_complete)\n"
                    f"下载完成: {progress.save_path}\n"
                    f"大小: {file_format(progress.total_size)}, "
                    f"耗时: {progress.elapsed:.1f}s\n"
                    f"可使用 {http_download_status.name}(task_id='{task_id}') 查看详情"
                )
            elif progress.status == DownloadStatus.CANCELLED:
                message = (
                    f"{SYSTEM_PREFIX}(download_cancelled)\n"
                    f"下载已取消: {progress.save_path}\n"
                    f"进度: {file_format(progress.downloaded_size)}/{file_format(progress.total_size)} "
                    f"({progress.progress_percent:.1f}%)\n"
                    f"可使用 {http_download.name}(url='{progress.url}') 重新下载以继续,或使用 {http_download_remove.name}(task_id='{task_id}') 删除任务"
                )
            else:
                message = (
                    f"{SYSTEM_PREFIX}(download_failed)\n"
                    f"下载失败: {progress.save_path}\n"
                    f"原因: {progress.error}\n"
                    f"可使用 {http_download_remove.name}(task_id='{task_id}') 删除任务"
                )

            await wake_agent(message, config)

        except Exception as e:
            # 异常时也通知用户
            from uniclaw.utils.constants import SYSTEM_PREFIX
            from uniclaw.utils.wakeup import wake_agent

            message = (
                f"{SYSTEM_PREFIX}(download_error)\n"
                f"下载任务异常: {downloader.save_path}\n"
                f"错误: {e}"
            )
            await wake_agent(message, config)


class M3u8DownloadManager(DownloadManager):
    """管理 M3u8Downloader 任务(M3U8/HLS 流下载)。"""

    _active_statuses = frozenset(
        {"pending", "parsing", "selecting", "downloading", "paused", "merging"}
    )

    async def _run_task(
        self, task_id: str, downloader: M3u8Downloader, config: AppConfig
    ):
        """后台执行下载,完成后通过 wakeup 通知模型。"""
        from uniclaw.tools.download.m3u8 import M3u8Status

        try:
            progress = await downloader.start()

            # 通知模型
            from uniclaw.utils.constants import SYSTEM_PREFIX
            from uniclaw.utils.wakeup import wake_agent
            from uniclaw.tools.download.m3u8_tools import (
                m3u8_download,
                m3u8_download_status,
                m3u8_download_remove,
            )

            if progress.status == M3u8Status.COMPLETED:
                message = (
                    f"{SYSTEM_PREFIX}(download_complete)\n"
                    f"M3U8 下载完成: {progress.merged_file or progress.save_path}\n"
                    f"大小: {file_format(progress.total_size)}, "
                    f"耗时: {progress.elapsed:.1f}s\n"
                    f"可使用 {m3u8_download_status.name}(task_id='{task_id}') 查看详情"
                )
            elif progress.status == M3u8Status.CANCELLED:
                message = (
                    f"{SYSTEM_PREFIX}(download_cancelled)\n"
                    f"M3U8 下载已取消: {progress.save_path}\n"
                    f"进度: {progress.downloaded_segments}/{progress.total_segments} 分片 "
                    f"({progress.progress_percent:.1f}%)\n"
                    f"可使用 {m3u8_download.name}(url='{progress.url}') 重新下载以继续,或使用 {m3u8_download_remove.name}(task_id='{task_id}') 删除任务"
                )
            else:
                message = (
                    f"{SYSTEM_PREFIX}(download_failed)\n"
                    f"M3U8 下载失败: {progress.save_path}\n"
                    f"原因: {progress.error}\n"
                    f"可使用 {m3u8_download_remove.name}(task_id='{task_id}') 删除任务"
                )

            await wake_agent(message, config)

        except Exception as e:
            # 异常时也通知用户
            from uniclaw.utils.constants import SYSTEM_PREFIX
            from uniclaw.utils.wakeup import wake_agent

            message = (
                f"{SYSTEM_PREFIX}(download_error)\n"
                f"M3U8 下载任务异常: {downloader.save_path}\n"
                f"错误: {e}"
            )
            await wake_agent(message, config)


# 全局单例
_http_download_manager: HttpDownloadManager | None = None
_m3u8_download_manager: M3u8DownloadManager | None = None


def get_http_download_manager() -> HttpDownloadManager:
    """获取全局 HTTP 下载管理器单例。"""
    global _http_download_manager
    if _http_download_manager is None:
        _http_download_manager = HttpDownloadManager()
    return _http_download_manager


def get_m3u8_download_manager() -> M3u8DownloadManager:
    """获取全局 M3U8 下载管理器单例。"""
    global _m3u8_download_manager
    if _m3u8_download_manager is None:
        _m3u8_download_manager = M3u8DownloadManager()
    return _m3u8_download_manager


# 向后兼容别名
def get_download_manager() -> HttpDownloadManager:
    """获取全局 HTTP 下载管理器单例(兼容旧接口)。"""
    return get_http_download_manager()
