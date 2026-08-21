"""M3U8/HLS 下载工具 -- 支持多分辨率选择、多协程并发、AES-128 解密、断点续传和合并。

提供同步和异步两种下载模式:
- 同步模式: 流式显示进度,下载完成返回结果
- 异步模式: 立即返回任务 ID,后台执行,完成/失败时唤醒模型提醒
"""

from enum import StrEnum
from pathlib import Path

from uniclaw.config import AppConfig
from uniclaw.tools.base import tool
from uniclaw.tools.download.m3u8 import (
    DEFAULT_M3U8_CONCURRENCY,
    M3u8Downloader,
    M3u8Status,
)
from uniclaw.tools.download.manager import get_m3u8_download_manager
from uniclaw.tools.stream import tool_stream
from uniclaw.utils.http_download import (
    DEFAULT_BLOCK_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DownloadChunkInfo,
    file_format,
    time_format,
)


@tool
async def m3u8_download(
    url: str,
    save_path: str,
    variant: str = "",
    concurrency: int = DEFAULT_M3U8_CONCURRENCY,
    max_retries: int = DEFAULT_MAX_RETRIES,
    proxy: str = "",
    cookies: str = "",
    block_timeout: float = DEFAULT_BLOCK_TIMEOUT,
    async_mode: bool = False,
    config: AppConfig = None,
) -> str:
    """下载 M3U8/HLS 视频流,支持多分辨率选择、多协程并发、AES-128 解密、断点续传和合并。

    播放列表为"主播放列表"(包含多个 #EXT-X-STREAM-INF)时,会列出可用分辨率,
    需通过 variant 参数指定要下载的清晰度(如 "0" 或 "1920x1080")后再调用一次。
    播放列表为"媒体播放列表"(直接包含 TS 分片)时,无需 variant 直接下载。

    Args:
        url: M3U8 播放列表地址(主播放列表或媒体播放列表均可)。
        save_path: 保存路径(必填,必须为绝对路径,如 "D:/videos/movie.ts")。
        variant: 分辨率选择,仅主播放列表且多分辨率时需要。可为序号(如 "0")或分辨率(如 "1920x1080")。
        concurrency: 并发下载分片数,默认 8。
        max_retries: 单个分片失败重试次数,默认 3。
        proxy: 代理地址(如 "http://proxy:8080"),为空则直连。
        cookies: Cookie 字符串(如 "key1=val1; key2=val2"),用于需要登录的下载。
        block_timeout: 分片请求超时(秒),默认 10 秒。
        async_mode: 异步模式,True 则后台下载并立即返回任务 ID,False 则同步下载并流式显示进度。

    Returns:
        str: 多分辨率时的可选列表; 同步模式返回下载结果摘要; 异步模式返回任务 ID 和状态查询方式。
    """
    # save_path 必须为绝对路径
    resolved_path = Path(save_path).expanduser()
    if not resolved_path.is_absolute():
        raise ValueError(
            f"save_path 必须为绝对路径(如 D:/videos/movie.ts),收到: {save_path}"
        )
    resolved_path = resolved_path.resolve()

    downloader = M3u8Downloader(
        url=url,
        save_path=resolved_path,
        concurrency=concurrency,
        max_retries=max_retries,
        proxy=proxy,
        cookies=cookies,
        block_timeout=block_timeout,
        variant=variant,
    )

    # 先解析播放列表,判断是否需要选择分辨率
    await downloader.parse()

    if downloader.progress.status == M3u8Status.SELECTING:
        # 多分辨率:返回可选列表,引导用户重新调用
        return downloader.progress.format_status()

    if downloader.progress.status == M3u8Status.FAILED:
        return downloader.progress.format_status()

    if async_mode:
        manager = get_m3u8_download_manager()
        task_id = manager.submit(downloader, config)
        return (
            f"M3U8 下载任务已提交。\n"
            f"任务 ID: {task_id}\n"
            f"保存路径: {resolved_path}\n"
            f"分辨率: {downloader.progress.selected_variant or '自动'}\n"
            f"使用 {m3u8_download_status.name}(task_id='{task_id}') 查看进度。"
        )

    # 同步模式: 带进度回调,支持取消转后台
    cancel_event = (
        config.current_agent.cancel_event
        if config and config.current_agent
        else None
    )
    cancelled = False

    async def progress_callback(info: DownloadChunkInfo):
        nonlocal cancelled
        # 检查取消事件
        if cancel_event and cancel_event.is_set():
            cancelled = True
            downloader.cancel()
            return
        # 错误通知(如分片被防盗链拦截返回 HTML): 换行输出,避免被进度行覆盖
        if info.error:
            await tool_stream(f"\n[错误] {info.error}\n")
            return
        # 估算剩余时间
        if info.speed > 0 and info.total > info.downloaded:
            remain_sec = (info.total - info.downloaded) / info.speed
            eta = time_format(remain_sec)
        else:
            eta = "计算中..."
        # 分片进度(准确) + 大小进度(估算),两者都显示
        seg_total = downloader.progress.total_segments
        seg_done = downloader.progress.downloaded_segments
        if seg_total > 0:
            seg_percent = seg_done / seg_total * 100
            seg_info = f"分片 {seg_done}/{seg_total} ({seg_percent:.1f}%)"
        else:
            seg_info = "分片 --/--"
        percent = info.downloaded / info.total * 100 if info.total > 0 else 0.0
        msg = (
            f"\rM3U8 下载进度: {seg_info} | "
            f"{file_format(info.downloaded)}/{file_format(info.total)} ({percent:.1f}%) "
            f"速度: {file_format(info.speed)}/s "
            f"剩余: {eta}"
        )
        await tool_stream(msg)

    progress = await downloader.start(callback=progress_callback)

    # 如果用户取消,转为后台继续下载
    if cancelled and progress.status == M3u8Status.CANCELLED:
        new_downloader = M3u8Downloader(
            url=url,
            save_path=resolved_path,
            concurrency=concurrency,
            max_retries=max_retries,
            proxy=proxy,
            cookies=cookies,
            block_timeout=block_timeout,
            variant=variant,
        )
        manager = get_m3u8_download_manager()
        task_id = manager.submit(new_downloader, config)
        return (
            f"M3U8 下载已转为后台任务。\n"
            f"任务 ID: {task_id}\n"
            f"保存路径: {resolved_path}\n"
            f"使用 {m3u8_download_status.name}(task_id='{task_id}') 查看进度。"
        )

    return progress.format_status()


@tool
async def m3u8_download_status(
    task_id: str = "",
    action: str = "",
) -> str:
    """查看或管理 m3u8_download 异步下载任务的状态。

    仅用于查询通过 m3u8_download(async_mode=True) 提交的异步下载任务,不适用于同步下载。

    Args:
        task_id: 下载任务 ID,为空则列出所有异步下载任务。
        action: 操作类型,可选值: pause/resume/cancel,为空则查看状态。

    Returns:
        str: 任务状态信息。
    """
    manager = get_m3u8_download_manager()

    if action:
        if not task_id:
            return "执行操作需要指定 task_id。"
        if action == "pause":
            return manager.pause(task_id)
        elif action == "resume":
            return manager.resume(task_id)
        elif action == "cancel":
            return manager.cancel(task_id)
        else:
            return f"未知操作: {action},支持的操作: pause/resume/cancel"

    return manager.get_status(task_id)


@tool
def m3u8_download_remove(task_id: str) -> str:
    """删除已完成的 M3U8 下载任务,释放内存。

    用于手动清理下载任务列表中已完成、失败或取消的任务。
    正在下载中的任务无法删除,需先使用 m3u8_download_status(action="cancel") 取消。

    Args:
        task_id: 要删除的下载任务 ID。

    Returns:
        str: 操作结果。
    """
    manager = get_m3u8_download_manager()
    return manager.remove_task(task_id)


def get_tools() -> list:
    """获取运行时工具列表。"""
    return [m3u8_download, m3u8_download_status, m3u8_download_remove]


def get_all_tools() -> list:
    """获取所有工具列表。"""
    return get_tools()
