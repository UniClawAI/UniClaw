"""下载任务管理器 -- 管理异步下载任务的生命周期。"""

import asyncio

from uniclaw.config import AppConfig
from uniclaw.utils.http_download import HttpDownloader, DownloadStatus, file_format

# 全局单例
_download_manager: DownloadManager | None = None


def get_download_manager() -> DownloadManager:
    """获取全局下载管理器单例。"""
    global _download_manager
    if _download_manager is None:
        _download_manager = DownloadManager()
    return _download_manager


class DownloadManager:
    """管理异步下载任务。"""

    def __init__(self):
        self._tasks: dict[str, HttpDownloader] = {}

    def submit(self, downloader: HttpDownloader, config: AppConfig) -> str:
        """提交异步下载任务,返回 task_id。"""
        task_id = downloader.task_id
        self._tasks[task_id] = downloader
        downloader.task = asyncio.create_task(self._run_task(task_id, downloader, config))
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

    async def _run_task(
        self, task_id: str, downloader: HttpDownloader, config: AppConfig
    ):
        """后台执行下载,完成后通过 wakeup 通知模型。

        注意: config 是 per-session 的引用,如果下载期间会话结束,
        wake_agent 会安全降级(config.current_agent 为 None 时返回 False)。
        """
        try:
            progress = await downloader.start()

            # 通知模型
            from uniclaw.utils.constants import SYSTEM_PREFIX
            from uniclaw.utils.wakeup import wake_agent

            if progress.status == DownloadStatus.COMPLETED:
                message = (
                    f"{SYSTEM_PREFIX}(download_complete)\n"
                    f"下载完成: {progress.save_path}\n"
                    f"大小: {file_format(progress.total_size)}, "
                    f"耗时: {progress.elapsed:.1f}s"
                )
            elif progress.status == DownloadStatus.CANCELLED:
                message = (
                    f"{SYSTEM_PREFIX}(download_cancelled)\n"
                    f"下载已取消: {progress.save_path}\n"
                    f"进度: {file_format(progress.downloaded_size)}/{file_format(progress.total_size)} "
                    f"({progress.progress_percent:.1f}%)\n"
                    f"可使用 http_download 重新下载以继续"
                )
            else:
                message = (
                    f"{SYSTEM_PREFIX}(download_failed)\n"
                    f"下载失败: {progress.save_path}\n"
                    f"原因: {progress.error}"
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
        finally:
            self._tasks.pop(task_id, None)
