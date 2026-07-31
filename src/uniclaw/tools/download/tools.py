"""HTTP/HTTPS 下载工具 -- 支持多协程并发、断点续传、代理、重试和文件校验。

仅支持 HTTP/HTTPS 协议,不支持 FTP、磁力链接等其他协议。

提供同步和异步两种下载模式:
- 同步模式: 流式显示进度,下载完成返回结果
- 异步模式: 立即返回任务 ID,后台执行,完成/失败时唤醒模型提醒
"""

import uuid
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse, unquote

from uniclaw.config import AppConfig
from uniclaw.context import Scope, get_app_dir
from uniclaw.tools.base import tool
from uniclaw.tools.download.manager import get_download_manager
from uniclaw.tools.stream import tool_stream
from uniclaw.utils.http_download import (
    DEFAULT_BLOCK_TIMEOUT,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_RETRIES,
    HttpDownloader,
    DownloadStatus,
    file_format,
    time_format,
)


def _guess_filename(url: str) -> str:
    """从 URL 猜测文件名。"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = path.split("/")[-1]
    if filename and "." in filename:
        filename = filename.split("?")[0]
        return filename
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{date_str}_{uuid.uuid4().hex[:8]}"


class DownloadAction(StrEnum):
    """下载任务操作类型。"""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"


def _resolve_save_path(save_path: str, url: str, root_dir: Path | None = None) -> Path:
    """解析保存路径。

    Args:
        save_path: 用户指定的保存路径(可以是相对路径或绝对路径)。
        url: 下载 URL,用于自动猜测文件名。
        root_dir: 项目根目录,相对路径会基于此目录解析。

    Returns:
        解析后的绝对路径。
    """
    if save_path:
        path = Path(save_path).expanduser()
        # 相对路径: 基于 root_dir 解析
        if not path.is_absolute():
            if root_dir:
                return (root_dir / path).resolve()
            # 无 root_dir 时放到用户级 downloads 目录
            base = get_app_dir(Scope.USER) / "downloads"
            base.mkdir(parents=True, exist_ok=True)
            return (base / path).resolve()
        return path.resolve()

    # 无 save_path: 自动保存到 downloads 目录
    base_dir = (
        get_app_dir(root_dir) / "downloads"
        if root_dir
        else (get_app_dir(Scope.USER) / "downloads")
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    filename = _guess_filename(url)
    return base_dir / filename


@tool
async def http_download(
    url: str,
    save_path: str = "",
    concurrency: int = DEFAULT_CONCURRENCY,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
    proxy: str = "",
    cookies: str = "",
    checksum_algorithm: str = "",
    checksum_value: str = "",
    async_mode: bool = False,
    block_timeout: float = DEFAULT_BLOCK_TIMEOUT,
    config: AppConfig = None,
) -> str:
    """通过 HTTP/HTTPS 下载文件,支持多协程并发、断点续传、代理和校验。

    仅支持 HTTP 和 HTTPS 协议。如需下载 FTP 或其他协议的文件,请使用其他工具。

    Args:
        url: HTTP/HTTPS 下载地址。
        save_path: 保存路径(完整文件路径),为空则自动保存到 ~/.UniClaw/downloads/ 并从 URL 识别文件名。
        concurrency: 并发下载数,默认 10。
        chunk_size: 每个分片大小(字节),默认 1MB。
        max_retries: 失败重试次数,默认 3。
        proxy: 代理地址(如 "http://proxy:8080"),为空则直连。
        cookies: Cookie 字符串(如 "key1=val1; key2=val2"),用于需要登录的下载。
        checksum_algorithm: 校验算法(md5/sha256),为空则不校验。
        checksum_value: 预期校验值,需配合 checksum_algorithm 使用。
        async_mode: 异步模式,True 则后台下载并立即返回任务 ID,False 则同步下载并流式显示进度。
        block_timeout: 服务器无响应超时(秒),默认 10 秒。服务器停止发送数据超过此时间会中止并重试。

    Returns:
        str: 同步模式返回下载结果摘要; 异步模式返回任务 ID 和状态查询方式。
    """
    root_dir = config.root_dir if config else None
    resolved_path = _resolve_save_path(save_path, url, root_dir)

    downloader = HttpDownloader(
        url=url,
        save_path=resolved_path,
        concurrency=concurrency,
        chunk_size=chunk_size,
        max_retries=max_retries,
        proxy=proxy,
        cookies=cookies,
        checksum_algorithm=checksum_algorithm,
        checksum_value=checksum_value,
        block_timeout=block_timeout,
    )

    if async_mode:
        manager = get_download_manager()
        task_id = manager.submit(downloader, config)
        return (
            f"下载任务已提交。\n"
            f"任务 ID: {task_id}\n"
            f"保存路径: {resolved_path}\n"
            f"使用 http_download_status(task_id='{task_id}') 查看进度。"
        )

    # 同步模式: 带进度回调,支持取消转后台
    cancel_event = config.current_agent.cancel_event if config else None
    cancelled = False

    async def progress_callback(info):
        nonlocal cancelled
        # 检查取消事件
        if cancel_event and cancel_event.is_set():
            cancelled = True
            downloader.cancel()
            return
        percent = info.downloaded / info.total * 100 if info.total > 0 else 0
        now = datetime.now().strftime("%H:%M:%S")
        # 估算剩余时间
        if info.speed > 0 and info.total > info.downloaded:
            remain_sec = (info.total - info.downloaded) / info.speed
            eta = time_format(remain_sec)
        else:
            eta = "计算中..."
        msg = (
            f"\r[{now}] 下载进度: {file_format(info.downloaded)}/{file_format(info.total)} "
            f"({percent:.1f}%) "
            f"速度: {file_format(info.speed)}/s "
            f"剩余: {eta}"
        )
        await tool_stream(msg)

    progress = await downloader.start(callback=progress_callback)

    # 如果用户取消,转为后台继续下载
    if cancelled and progress.status == DownloadStatus.CANCELLED:
        # 创建新的下载器继续下载(相同的 url+save_path 会生成相同的临时文件路径,自动恢复进度)
        new_downloader = HttpDownloader(
            url=url,
            save_path=resolved_path,
            concurrency=concurrency,
            chunk_size=chunk_size,
            max_retries=max_retries,
            proxy=proxy,
            cookies=cookies,
            checksum_algorithm=checksum_algorithm,
            checksum_value=checksum_value,
            block_timeout=block_timeout,
        )

        manager = get_download_manager()
        task_id = manager.submit(new_downloader, config)
        return (
            f"下载已转为后台任务。\n"
            f"任务 ID: {task_id}\n"
            f"保存路径: {resolved_path}\n"
            f"使用 http_download_status(task_id='{task_id}') 查看进度。"
        )

    return progress.format_status()


@tool
async def http_download_status(
    task_id: str = "",
    action: DownloadAction | None = None,
) -> str:
    """查看或管理 http_download 异步下载任务的状态。

    仅用于查询通过 http_download(async_mode=True) 提交的异步下载任务,不适用于同步下载。

    Args:
        task_id: 下载任务 ID,为空则列出所有异步下载任务。
        action: 操作类型,可选值: pause/resume/cancel,为空则查看状态。

    Returns:
        str: 任务状态信息。
    """
    manager = get_download_manager()

    if action:
        if not task_id:
            return "执行操作需要指定 task_id。"
        if action == DownloadAction.PAUSE:
            return manager.pause(task_id)
        elif action == DownloadAction.RESUME:
            return manager.resume(task_id)
        elif action == DownloadAction.CANCEL:
            return manager.cancel(task_id)
        else:
            actions = ", ".join(a.value for a in DownloadAction)
            return f"未知操作: {action},支持的操作: {actions}"

    return manager.get_status(task_id)


def get_tools() -> list:
    """获取运行时工具列表。"""
    return [http_download, http_download_status]


def get_all_tools() -> list:
    """获取所有工具列表。"""
    return get_tools()
