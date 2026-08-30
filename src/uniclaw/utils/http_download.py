"""HTTP 下载引擎 — 支持多协程并发、断点续传、代理、重试和文件校验。"""

import asyncio
import hashlib
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Awaitable
import httpx

from uniclaw.utils.downloader import BaseDownloader

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_CONCURRENCY = 10
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1MB
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30
DEFAULT_BLOCK_TIMEOUT = 10.0


def file_format(size: float) -> str:
    """将字节数转换为人类可读的文件大小。"""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB", "EB"):
        if abs(size) < 1024.0:
            return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}ZB"


def time_format(seconds: float) -> str:
    """将秒数转换为人类可读的时间。"""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes):02d}:{int(secs):02d}"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"
    days, hours = divmod(hours, 24)
    return f"{days}d{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"


def calculate_checksum(path: Path, algorithm: str = "sha256") -> str:
    """计算文件的校验和。"""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class DownloadStatus(StrEnum):
    """下载任务状态。"""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class _RangeNotSupported(IOError):
    """服务器忽略 Range 请求(返回 200 而非 206),需降级为全量下载。"""


def _stable_id(url: str, save_path: Path) -> str:
    """根据 URL 和保存路径生成临时文件标识,格式为 {文件名}_{hash}。"""
    key = f"{url}|{save_path}".encode()
    h = hashlib.sha256(key).hexdigest()[:8]
    return f"{save_path.stem}_{h}"


def _parse_cookies(cookie_str: str) -> dict[str, str]:
    """解析 cookie 字符串为字典。"""
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


class DownloadRecord:
    """下载进度记录 — 管理已下载的字节范围,支持断点续传。

    用法::

        record = DownloadRecord(Path("/downloads/file.progress.json"))
        record.set_downloaded(0, 1024)   # 标记 [0, 1023] 已下载
        record.set_downloaded(1024, 1024) # 自动合并为 [0, 2047]
        record.check_downloaded(0, 2048)  # True
    """

    def __init__(self, progress_file: Path):
        self._file = progress_file
        self._ranges: list[tuple[int, int]] = []  # 不重叠、按序排列的 (start, end)

    @property
    def ranges(self) -> list[tuple[int, int]]:
        """已下载的字节范围列表。"""
        return list(self._ranges)

    def load(self) -> None:
        """从文件加载进度。"""
        if not self._file.exists():
            self._ranges = []
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._ranges = [tuple(r) for r in data.get("ranges", [])]
        except (json.JSONDecodeError, KeyError, TypeError):
            self._ranges = []

    def save(self) -> None:
        """保存进度到文件。"""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {"ranges": self._ranges}
        self._file.write_text(json.dumps(data), encoding="utf-8")

    def set_downloaded(self, start: int, size: int) -> None:
        """标记一个区块为已下载,自动与相邻/重叠区块合并后保存。

        Args:
            start: 区块起始字节位置。
            size: 区块大小(字节)。
        """
        if size <= 0:
            return
        end = start + size - 1
        new_start, new_end = start, end

        merged: list[tuple[int, int]] = []
        used = False
        for r_start, r_end in self._ranges:
            # 与新区间不相邻也不重叠
            if r_start > new_end + 1 or r_end < new_start - 1:
                if not used and r_start > new_end + 1:
                    merged.append((new_start, new_end))
                    used = True
                merged.append((r_start, r_end))
            else:
                # 合并
                new_start = min(new_start, r_start)
                new_end = max(new_end, r_end)
        if not used:
            merged.append((new_start, new_end))

        self._ranges = merged
        self.save()

    def check_downloaded(self, start: int, size: int) -> bool:
        """检查指定区块是否已完全下载。

        Args:
            start: 区块起始字节位置。
            size: 区块大小(字节)。

        Returns:
            True 表示该区块已完整下载。
        """
        if size <= 0:
            return True
        end = start + size - 1
        for r_start, r_end in self._ranges:
            if r_start <= start and end <= r_end:
                return True
        return False

    def get_downloaded_size(self) -> int:
        """获取已下载的总字节数。"""
        return sum(end - start + 1 for start, end in self._ranges)

    def clear(self) -> None:
        """清空记录并删除进度文件。"""
        self._ranges = []
        self._file.unlink(missing_ok=True)


@dataclass
class DownloadChunkInfo:
    """每次下载回调传递的进度快照。"""

    chunk_size: int  # 本次下载的字节数
    downloaded: int  # 已下载总字节数
    total: int  # 文件总大小
    speed: float  # 当前速度(bytes/s)
    error: str | None = None  # 非空时表示错误通知(如分片返回 HTML),而非进度更新


@dataclass
class DownloadProgress:
    """下载进度信息。"""

    task_id: str
    url: str
    save_path: str
    total_size: int = 0
    downloaded_size: int = 0
    status: DownloadStatus = DownloadStatus.PENDING
    start_time: float = 0.0
    end_time: float = 0.0  # 下载完成/失败时记录,用于计算固定耗时
    error: str | None = None
    speed: float = 0.0

    @property
    def progress_percent(self) -> float:
        if self.total_size <= 0:
            return 0.0
        return (self.downloaded_size / self.total_size) * 100

    @property
    def elapsed(self) -> float:
        if self.start_time <= 0:
            return 0.0
        # 已结束的任务返回固定耗时
        if self.end_time > 0:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    @property
    def eta(self) -> float:
        if self.speed <= 0:
            return 0.0
        remaining = self.total_size - self.downloaded_size
        return remaining / self.speed

    def finish(self, status: DownloadStatus, error: str | None = None) -> None:
        """标记下载完成/失败,记录结束时间。"""
        self.status = status
        self.error = error
        self.end_time = time.time()

    def format_status(self) -> str:
        """格式化为人类可读的状态字符串。"""
        if self.status == DownloadStatus.COMPLETED:
            return (
                f"下载完成: {self.save_path}\n"
                f"大小: {file_format(self.total_size)}, "
                f"耗时: {time_format(self.elapsed)}"
            )
        if self.status == DownloadStatus.CANCELLED:
            return f"下载已取消: {self.error}"
        if self.status == DownloadStatus.FAILED:
            return f"下载失败: {self.error}"
        if self.status == DownloadStatus.PENDING:
            return "等待开始..."
        # downloading / paused
        lines = [
            f"文件: {self.save_path}",
            f"进度: {file_format(self.downloaded_size)}/{file_format(self.total_size)} "
            f"({self.progress_percent:.1f}%)",
        ]
        if self.speed > 0:
            lines.append(
                f"速度: {file_format(self.speed)}/s, 剩余: {time_format(self.eta)}"
            )
        lines.append(
            f"状态: {'已暂停' if self.status == DownloadStatus.PAUSED else '下载中'}"
        )
        return "\n".join(lines)


class HttpDownloader(BaseDownloader):
    """HTTP 下载引擎,支持多协程并发、断点续传、代理、重试。"""

    def __init__(
        self,
        url: str,
        save_path: str | Path,
        concurrency: int = DEFAULT_CONCURRENCY,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        proxy: str = "",
        cookies: str = "",
        headers: dict[str, str] | None = None,
        checksum_algorithm: str = "",
        checksum_value: str = "",
        block_timeout: float = DEFAULT_BLOCK_TIMEOUT,
    ):
        self.url = url
        self.save_path = Path(save_path)
        self.concurrency = concurrency
        self.chunk_size = chunk_size
        self.max_retries = max_retries
        self.proxy = proxy
        self.cookies = cookies
        self.headers = headers or {}
        self.checksum_algorithm = checksum_algorithm
        self.checksum_value = checksum_value
        self.block_timeout = block_timeout

        # 内部状态
        self.task_id = uuid.uuid4().hex[:12]
        self.progress = DownloadProgress(
            task_id=self.task_id,
            url=url,
            save_path=str(save_path),
        )
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # 初始为非暂停状态
        self._cancelled = False
        self._session_downloaded = 0  # 本次会话下载的字节数(用于速度计算)
        self.task: asyncio.Task | None = None

        # 临时文件放在目标目录,确保同一文件系统下 rename() 为原子操作
        stable = _stable_id(url, self.save_path)
        self._tmp_file = self.save_path.parent / f".{stable}.tmp"
        self._record = DownloadRecord(
            self.save_path.parent / f".{stable}.progress.json"
        )

    def _build_client_kwargs(self) -> dict[str, Any]:
        """构建 httpx 客户端参数。"""
        kwargs: dict[str, Any] = {"timeout": DEFAULT_TIMEOUT, "follow_redirects": True}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        if self.cookies:
            kwargs["cookies"] = _parse_cookies(self.cookies)
        return kwargs

    async def _get_file_info(self, client: httpx.AsyncClient) -> tuple[int, bool]:
        """获取文件大小和是否支持 Range。优先使用 HEAD 请求,失败时回退到 GET。"""
        # 禁用自动解压,确保 Content-Length 与实际字节数一致
        headers = {"Accept-Encoding": "identity"}
        try:
            resp = await client.head(self.url, headers=headers)
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError):
            # HEAD 请求失败时回退到 GET, 但用流式请求只读响应头,
            # 避免把整个文件缓冲进内存(部分服务器不支持 HEAD, 会返回整个 body)
            async with client.stream("GET", self.url, headers=headers) as resp:
                resp.raise_for_status()
                return self._parse_info_headers(resp.headers)
        return self._parse_info_headers(resp.headers)

    def _parse_info_headers(self, headers) -> tuple[int, bool]:
        """从响应头解析 (文件大小, 是否支持 Range)。

        无 Content-Length 时(如 chunked 编码),返回 (0, False) 降级为流式下载。
        """
        content_length = headers.get("content-length")
        if content_length is None:
            return 0, False
        accept_ranges = headers.get("accept-ranges", "")
        supports_range = "bytes" in accept_ranges.lower()
        return int(content_length), supports_range

    def _verify_checksum(self) -> None:
        """校验已下载文件的完整性,失败时删除临时文件与记录并抛异常。"""
        if not (self.checksum_algorithm and self.checksum_value):
            return
        actual = calculate_checksum(self._tmp_file, self.checksum_algorithm)
        if actual.lower() != self.checksum_value.lower():
            # 文件损坏: 删除临时文件与记录,避免被断点续传误用
            self._tmp_file.unlink(missing_ok=True)
            self._record.clear()
            raise ValueError(f"校验失败: 期望 {self.checksum_value}, 实际 {actual}")

    async def _download_range(
        self,
        client: httpx.AsyncClient,
        start: int,
        end: int,
        callback: Callable[[DownloadChunkInfo], Awaitable[None]] | None = None,
    ) -> int:
        """下载指定字节范围,直接写入 .tmp 文件的对应位置,带重试和块超时。

        整个 range 下载完成并校验后才更新进度和回调,无需回退。
        如果单个块下载时间超过 block_timeout 秒,会中止并重试。
        """
        expected_size = end - start + 1

        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                return 0
            await self._pause_event.wait()

            try:
                # Range 请求时禁用自动解压,避免 Content-Length 与实际字节数不匹配
                headers = {
                    **self.headers,
                    "Range": f"bytes={start}-{end}",
                    "Accept-Encoding": "identity",
                }
                buf = bytearray()
                async with client.stream("GET", self.url, headers=headers) as resp:
                    resp.raise_for_status()
                    # 服务器忽略 Range 时返回 200 + 完整文件: 立即抛错交由 start() 降级,
                    # 避免把整个文件缓冲进内存(潜在 OOM)
                    if resp.status_code != 206:
                        raise _RangeNotSupported(
                            f"服务器未按 Range 请求返回 206(实际状态码 {resp.status_code})"
                        )
                    aiter = resp.aiter_bytes(8192).__aiter__()
                    while True:
                        if self._cancelled:
                            return 0
                        await self._pause_event.wait()

                        try:
                            chunk = await asyncio.wait_for(
                                aiter.__anext__(), timeout=self.block_timeout
                            )
                        except StopAsyncIteration:
                            break

                        buf.extend(chunk)

                downloaded = len(buf)
                with open(self._tmp_file, "r+b") as f:
                    f.seek(start)
                    f.write(buf)

                # 校验下载大小
                if downloaded != expected_size:
                    raise IOError(
                        f"范围 {start}-{end} 下载不完整: 期望 {expected_size} 字节, 实际 {downloaded} 字节"
                    )

                # 整个 range 下载成功,一次性更新进度
                self.progress.downloaded_size += expected_size
                self._session_downloaded += expected_size
                self._record.set_downloaded(start, expected_size)

                # 更新速度
                elapsed = time.time() - self.progress.start_time
                self.progress.speed = (
                    self._session_downloaded / elapsed if elapsed > 0 else 0
                )

                # 回调
                if callback:
                    info = DownloadChunkInfo(
                        chunk_size=expected_size,
                        downloaded=self.progress.downloaded_size,
                        total=self.progress.total_size,
                        speed=self.progress.speed,
                    )
                    if inspect.iscoroutinefunction(callback):
                        await callback(info)
                    else:
                        callback(info)
                return downloaded

            except _RangeNotSupported:
                # 服务器忽略 Range: 不重试,交由 start() 降级为全量下载
                raise
            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                OSError,
                asyncio.TimeoutError,
            ) as e:
                if attempt < self.max_retries:
                    wait_time = min(2**attempt * 1.0, 30.0)
                    logger.warning(
                        "范围 %d-%d 下载失败(尝试 %d/%d): %s, %.1fs 后重试",
                        start,
                        end,
                        attempt + 1,
                        self.max_retries,
                        e,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise IOError(f"范围 {start}-{end} 下载失败") from e

    async def _download_full(
        self,
        client: httpx.AsyncClient,
        callback: Callable[[DownloadChunkInfo], Awaitable[None]] | None = None,
    ) -> None:
        """不支持 Range 时,单连接下载整个文件。"""
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                return
            await self._pause_event.wait()

            # 重试时回退进度
            if attempt > 0:
                self.progress.downloaded_size = 0
                self._session_downloaded = 0

            try:
                headers = {**self.headers, "Accept-Encoding": "identity"}
                async with client.stream("GET", self.url, headers=headers) as resp:
                    resp.raise_for_status()
                    with open(self._tmp_file, "wb") as f:
                        aiter = resp.aiter_bytes(8192).__aiter__()
                        while True:
                            if self._cancelled:
                                return
                            await self._pause_event.wait()

                            try:
                                chunk = await asyncio.wait_for(
                                    aiter.__anext__(), timeout=self.block_timeout
                                )
                            except StopAsyncIteration:
                                break

                            f.write(chunk)
                            self.progress.downloaded_size += len(chunk)
                            self._session_downloaded += len(chunk)

                            # 更新速度
                            elapsed = time.time() - self.progress.start_time
                            speed = (
                                self._session_downloaded / elapsed if elapsed > 0 else 0
                            )
                            self.progress.speed = speed

                            if callback:
                                info = DownloadChunkInfo(
                                    chunk_size=len(chunk),
                                    downloaded=self.progress.downloaded_size,
                                    total=self.progress.total_size,
                                    speed=speed,
                                )
                                if inspect.iscoroutinefunction(callback):
                                    await callback(info)
                                else:
                                    callback(info)

                # 校验大小(仅当已知文件大小时,避免 chunked 无 Content-Length 误判)
                actual_size = self._tmp_file.stat().st_size
                if self.progress.total_size > 0 and actual_size != self.progress.total_size:
                    raise IOError(
                        f"下载不完整: 期望 {self.progress.total_size} 字节, 实际 {actual_size} 字节"
                    )
                # 无 Content-Length(chunked)时用磁盘实际大小回填 total_size,
                # 让完成摘要显示真实大小而非 0.00B
                if self.progress.total_size <= 0:
                    self.progress.total_size = actual_size

                # 校验完整性(可选)
                self._verify_checksum()

                # 完成: 重命名 + 清理(临时文件与目标同目录, rename() 恒为原子操作)
                self.save_path.unlink(missing_ok=True)
                self._tmp_file.rename(self.save_path)
                self._record.clear()
                self.progress.finish(DownloadStatus.COMPLETED)
                return

            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                OSError,
                asyncio.TimeoutError,
            ) as e:
                if attempt < self.max_retries:
                    wait_time = min(2**attempt * 1.0, 30.0)
                    logger.warning(
                        "下载失败(尝试 %d/%d): %s, %.1fs 后重试",
                        attempt + 1,
                        self.max_retries,
                        e,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise IOError("下载失败") from e

    async def start(
        self,
        callback: Callable[[DownloadChunkInfo], Awaitable[None]] | None = None,
    ) -> DownloadProgress:
        """执行下载。

        Args:
            callback: 每下载一块数据的回调,参数为 DownloadChunkInfo 进度快照。

        Returns:
            DownloadProgress: 下载完成后的进度信息。
        """
        self.progress.status = DownloadStatus.DOWNLOADING
        self.progress.start_time = time.time()
        self.progress.downloaded_size = 0
        self._session_downloaded = 0  # 本次会话下载的字节数(用于速度计算)
        self._cancelled = False
        self._pause_event.set()

        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)

            # 获取文件信息
            client_kwargs = self._build_client_kwargs()
            async with httpx.AsyncClient(**client_kwargs) as client:
                total_size, supports_range = await self._get_file_info(client)
                self.progress.total_size = total_size

                if not supports_range:
                    # 不支持 Range,降级为单连接下载
                    await self._download_full(client, callback)
                    return self.progress

                # 加载已有进度
                self._record.load()

                # 确保 .tmp 文件存在且大小正确
                if not self._tmp_file.exists():
                    with open(self._tmp_file, "wb") as f:
                        f.truncate(total_size)
                else:
                    # 恢复已下载大小
                    self.progress.downloaded_size = self._record.get_downloaded_size()

                # 计算需要下载的范围
                ranges_to_download: list[tuple[int, int]] = []
                pos = 0
                while pos < total_size:
                    end = min(pos + self.chunk_size - 1, total_size - 1)
                    if not self._record.check_downloaded(pos, end - pos + 1):
                        ranges_to_download.append((pos, end))
                    pos = end + 1

                # 并发下载
                semaphore = asyncio.Semaphore(self.concurrency)

                async def _limited_download(start: int, end: int):
                    async with semaphore:
                        await self._download_range(client, start, end, callback)

                if ranges_to_download:
                    tasks = [_limited_download(s, e) for s, e in ranges_to_download]
                    # return_exceptions=True 让所有任务完成,不提前取消
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    # 服务器忽略 Range(返回 200 而非 206): 降级为单连接全量下载
                    if any(isinstance(r, _RangeNotSupported) for r in results):
                        self.progress.downloaded_size = 0
                        self._session_downloaded = 0
                        self._record.clear()
                        await self._download_full(client, callback)
                        return self.progress
                    # 检查是否有失败的任务
                    for result in results:
                        if isinstance(result, Exception):
                            raise result

                # 如果被取消,直接返回取消状态,保留临时文件供续传
                if self._cancelled:
                    self.progress.finish(DownloadStatus.CANCELLED, "用户取消")
                    return self.progress

                # 检查是否所有分片都已下载完成
                if not self._record.check_downloaded(0, total_size):
                    self.progress.finish(DownloadStatus.FAILED, "下载未完成")
                    return self.progress

                # 校验(所有分片已完成)
                self._verify_checksum()

                # 下载完成: 重命名 .tmp → 最终文件(临时文件与目标同目录, rename() 恒为原子操作)
                self.save_path.unlink(missing_ok=True)
                self._tmp_file.rename(self.save_path)

                # 下载完成: 清理进度文件
                self._record.clear()

                self.progress.finish(DownloadStatus.COMPLETED)
                return self.progress

        except Exception as e:
            if not self._cancelled:
                self.progress.finish(DownloadStatus.FAILED, str(e))
                logger.error("下载失败: %s", e)
            return self.progress

    def pause(self) -> None:
        """暂停下载。"""
        self._pause_event.clear()
        self.progress.status = DownloadStatus.PAUSED

    def resume(self) -> None:
        """恢复下载。"""
        self._pause_event.set()
        self.progress.status = DownloadStatus.DOWNLOADING

    def cancel(self) -> None:
        """取消下载。"""
        self._cancelled = True
        self._pause_event.set()
        self.progress.finish(DownloadStatus.CANCELLED, "用户取消")
