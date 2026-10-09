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
from uniclaw.utils.format import format_size

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_CONCURRENCY = 10
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1MB
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30
DEFAULT_BLOCK_TIMEOUT = 10.0


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


# 网络层错误分类关键词(小写匹配,含异常类名与常见报错文本)。
# 顺序有意义: DNS 优先于超时(解析失败也可能带 timeout 字样)。
_NETWORK_ERROR_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            "getaddrinfo failed",
            "nameresolutionerror",
            "gaierror",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
            "failed to resolve",
        ),
        "域名无法解析,请检查 URL 拼写或网络",
    ),
    (
        (
            "connectionrefusederror",
            "connection refused",
            "actively refused",
        ),
        "连接被拒绝,确认服务是否在运行",
    ),
    (
        (
            "timeouterror",
            "timeoutexception",
            "connecttimeout",
            "readtimeout",
            "timed out",
            "timeout",
        ),
        "连接超时,可重试或换用其他源",
    ),
)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """展开异常链(含 __cause__ / 未抑制的 __context__),保留原始错误。"""
    chain: list[BaseException] = []
    seen: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        chain.append(e)
        e = e.__cause__ or (None if e.__suppress_context__ else e.__context__)
    return chain


def explain_network_error(exc: BaseException) -> str | None:
    """网络层错误分类,返回人类可读解释;无法分类时返回 None。

    Args:
        exc: 待分类的异常(会沿异常链识别根因)。

    Returns:
        str | None: 人类可读解释(如"域名无法解析,请检查 URL 拼写或网络"),
            非网络类错误返回 None。
    """
    blob = " ".join(f"{e.__class__.__name__} {e}" for e in _exception_chain(exc)).lower()
    for keywords, hint in _NETWORK_ERROR_HINTS:
        if any(k in blob for k in keywords):
            return hint
    return None


def format_network_error(exc: BaseException) -> str:
    """保留原始错误(含异常链),追加人类可读的网络解释(若可分类)。

    技术人员要看原始错误,非技术用户看解释 —— 追加而非替换。

    Args:
        exc: 捕获到的异常。

    Returns:
        str: 原始错误链文本;可分类时末尾附上解释。
    """
    parts: list[str] = []
    for e in _exception_chain(exc):
        text = (str(e) or e.__class__.__name__).strip()
        # 去重: 相同文本,或已包含在已有段落里(包装消息已内嵌根因时不重复)
        if any(text == p or text in p or p in text for p in parts):
            continue
        parts.append(text)
    original = " <- ".join(parts) if parts else exc.__class__.__name__
    hint = explain_network_error(exc)
    return f"{original}\n{hint}" if hint else original


def _stable_id(url: str, save_path: Path) -> str:
    """根据 URL 和保存路径生成临时文件标识,格式为 {文件名}_{hash}。"""
    key = f"{url}|{save_path}".encode()
    h = hashlib.sha256(key).hexdigest()[:8]
    # stem 截断: 超长文件名场景下旁路暂存文件名不能把全路径顶过 MAX_PATH
    return f"{save_path.stem[:24]}_{h}"


def _path_length_hint(save_path: Path, tmp_path: Path, exc: BaseException) -> str:
    """路径过长(Windows MAX_PATH)导致的文件找不到,给出针对性提示。

    旁路暂存文件与目标同目录,目标文件名已逼近 260 字符时,再拼 `.{id}.tmp`
    会超出限制 —— 表象是 ENOENT("找不到文件"),与真正的网络失败完全不同。

    Args:
        save_path: 最终保存路径。
        tmp_path: 旁路暂存文件路径。
        exc: 捕获到的异常。

    Returns:
        str: 提示文本;与路径长度无关时返回空串。
    """
    blob = f"{type(exc).__name__} {exc}".lower()
    not_found = (
        "no such file" in blob
        or "cannot find" in blob
        or "winerror 2" in blob
        or "errnooent" in blob
        or "filenotfounderror" in blob
        or "找不到" in blob
    )
    if not not_found:
        return ""
    long_paths = [p for p in (save_path, tmp_path) if len(str(p)) >= 240]
    if not long_paths:
        return ""
    shown = max(str(p) for p in long_paths)
    return (
        f"路径过长: {shown[:80]}...(全长 {len(shown)} 字符,接近或超过 Windows "
        f"MAX_PATH=260)。请改用更短的文件名/目录,或启用系统长路径支持后重试"
    )


def _parse_cookies(cookie_str: str) -> dict[str, str]:
    """解析 cookie 字符串为字典。"""
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookies[key.strip()] = value.strip()
    return cookies


def parse_headers(header_str: str) -> dict[str, str]:
    """解析自定义请求头参数(如 Referer/User-Agent),用于防盗链场景。

    支持两种写法:
    1. JSON 对象字符串,如 '{"Referer": "https://a.com/", "User-Agent": "Mozilla/5.0"}'
    2. 每行一条 "Name: Value"(HTTP 头格式)

    Args:
        header_str: 请求头文本,为空返回空字典。

    Returns:
        dict[str, str]: 请求头字典。

    Raises:
        ValueError: 格式非法(JSON 不合法,或存在不含冒号的非空行)。
    """
    s = header_str.strip()
    if not s:
        return {}
    if s.startswith("{"):
        try:
            data = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(f"headers 不是合法 JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"headers JSON 必须是对象(收到 {type(data).__name__})")
        return {str(k): str(v) for k, v in data.items()}
    headers: dict[str, str] = {}
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(
                f"无法解析请求头行 {line!r},应为 \"Name: Value\" 格式"
                "(或整体传 JSON 对象字符串)"
            )
        name, value = line.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


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

    def set_downloaded(self, start: int, size: int, persist: bool = True) -> None:
        """标记一个区块为已下载,自动与相邻/重叠区块合并。

        Args:
            start: 区块起始字节位置。
            size: 区块大小(字节)。
            persist: 是否立即落盘。逐块写入的场景传 False 累积,
                在 range 完成/定期调 save(),避免每 8KB 写一次 JSON。
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
        if persist:
            self.save()

    def missing_in(self, start: int, end: int) -> list[tuple[int, int]]:
        """返回 [start, end] 区间内尚未下载的子区间列表(按序、不重叠)。

        断点续传按字节缺口补下 —— 部分完成的 range 不再整段重下。

        Args:
            start: 区间起始(含)。
            end: 区间结束(含)。

        Returns:
            list[tuple[int, int]]: 缺口子区间;整段已下载时为空列表。
        """
        gaps: list[tuple[int, int]] = []
        cursor = start
        for r_start, r_end in self._ranges:
            if r_end < cursor:
                continue
            if r_start > end:
                break
            if r_start > cursor:
                gaps.append((cursor, min(r_start - 1, end)))
            cursor = max(cursor, r_end + 1)
            if cursor > end:
                break
        if cursor <= end:
            gaps.append((cursor, end))
        return gaps

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
    skipped_note: str = ""  # 目标已存在完整文件被跳过时的说明

    @property
    def progress_percent(self) -> float:
        if self.total_size <= 0:
            return 0.0
        # 显示层封顶 100%: 理论上区间不重叠计数不会超,真超了是 bug,
        # 但先把显示钉住,明细里的 downloaded/total 仍能看出异常
        return min((self.downloaded_size / self.total_size) * 100, 100.0)

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
            lines = [
                f"下载完成: {self.save_path}",
                f"大小: {format_size(self.total_size)}, "
                f"耗时: {time_format(self.elapsed)}",
            ]
            if self.skipped_note:
                lines.append(self.skipped_note)
            return "\n".join(lines)
        if self.status == DownloadStatus.CANCELLED:
            return f"下载已取消: {self.error}"
        if self.status == DownloadStatus.FAILED:
            return f"下载失败: {self.error}"
        if self.status == DownloadStatus.PENDING:
            return "等待开始..."
        # downloading / paused
        lines = [
            f"文件: {self.save_path}",
            f"进度: {format_size(self.downloaded_size)}/{format_size(self.total_size)} "
            f"({self.progress_percent:.1f}%)",
        ]
        if self.speed > 0:
            lines.append(
                f"速度: {format_size(self.speed)}/s, 剩余: {time_format(self.eta)}"
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
        # 禁用自动解压,确保 Content-Length 与实际字节数一致;
        # 自定义请求头(Referer/UA 等)与正式下载保持一致,防盗链站点对 HEAD 同样校验
        headers = {**self.headers, "Accept-Encoding": "identity"}
        try:
            resp = await client.head(self.url, headers=headers)
            resp.raise_for_status()
            resp_headers = resp.headers
        except (httpx.HTTPStatusError, httpx.RequestError):
            # HEAD 请求失败时回退到 GET, 但用流式请求只读响应头,
            # 避免把整个文件缓冲进内存(部分服务器不支持 HEAD, 会返回整个 body)
            async with client.stream("GET", self.url, headers=headers) as resp:
                resp.raise_for_status()
                resp_headers = resp.headers
        size, supports_range = self._parse_info_headers(resp_headers)
        accept = (resp_headers.get("accept-ranges") or "").strip().lower()
        if not supports_range and size > 0 and not accept:
            # Accept-Ranges 头**缺失** ≠ 不支持 Range: 很多服务器 GET 支持 206
            # 却不回这个头。此时走全量单连接路径会让断点续传完全失效
            # (partial 无法记账,取消后重下=全量)。用 1 字节 Range 探测兜底。
            # 明确返回 "none" 的不探测,尊重服务器。
            supports_range = await self._probe_range(client, headers)
        return size, supports_range

    async def _probe_range(self, client: httpx.AsyncClient, headers: dict) -> bool:
        """探测服务器是否真正支持 Range(1 字节试探,206 即支持)。"""
        probe = {**headers, "Range": "bytes=0-0"}
        try:
            async with client.stream("GET", self.url, headers=probe) as resp:
                return resp.status_code == 206
        except (httpx.HTTPStatusError, httpx.RequestError):
            return False

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

    def _count_received(self, size: int) -> None:
        """把刚写入的字节计入实时进度(下载量/速度),供状态查询与进度回调使用。"""
        self.progress.downloaded_size += size
        self._session_downloaded += size
        elapsed = time.time() - self.progress.start_time
        self.progress.speed = self._session_downloaded / elapsed if elapsed > 0 else 0

    def _uncount_received(self, size: int) -> None:
        """回退已计数的字节(本次尝试失败重下时,避免重复累计)。"""
        if size <= 0:
            return
        self.progress.downloaded_size = max(0, self.progress.downloaded_size - size)
        self._session_downloaded = max(0, self._session_downloaded - size)

    async def _download_range(
        self,
        client: httpx.AsyncClient,
        start: int,
        end: int,
        callback: Callable[[DownloadChunkInfo], Awaitable[None]] | None = None,
    ) -> int:
        """下载指定字节范围,流式写入 .tmp 文件的对应位置,带重试和块超时。

        分块边到边写盘边计数,状态查询/进度显示实时反映真实写入量;
        整个 range 完成后才记入断点续传记录(续传以完整 range 为粒度)。
        本次尝试失败/重试时回退已计数字节,避免进度重复累计。
        如果单个块下载时间超过 block_timeout 秒,会中止并重试。
        """
        expected_size = end - start + 1

        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                return 0
            await self._pause_event.wait()

            written = 0  # 本次尝试已写入并计数的字节数
            try:
                # Range 请求时禁用自动解压,避免 Content-Length 与实际字节数不匹配
                headers = {
                    **self.headers,
                    "Range": f"bytes={start}-{end}",
                    "Accept-Encoding": "identity",
                }
                async with client.stream("GET", self.url, headers=headers) as resp:
                    resp.raise_for_status()
                    # 服务器忽略 Range 时返回 200 + 完整文件: 立即抛错交由 start() 降级,
                    # 避免把整个文件缓冲进内存(潜在 OOM)
                    if resp.status_code != 206:
                        raise _RangeNotSupported(
                            f"服务器未按 Range 请求返回 206(实际状态码 {resp.status_code})"
                        )
                    with open(self._tmp_file, "r+b") as f:
                        f.seek(start)
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

                            f.write(chunk)
                            # 逐块落账(不落盘): 部分完成的区间也算已下,
                            # 取消/中断后按缺口续传而不是整 range 重下
                            self._record.set_downloaded(
                                start + written, len(chunk), persist=False
                            )
                            written += len(chunk)
                            self._count_received(len(chunk))

                            if callback:
                                info = DownloadChunkInfo(
                                    chunk_size=len(chunk),
                                    downloaded=self.progress.downloaded_size,
                                    total=self.progress.total_size,
                                    speed=self.progress.speed,
                                )
                                if inspect.iscoroutinefunction(callback):
                                    await callback(info)
                                else:
                                    callback(info)

                # 校验下载大小
                if written != expected_size:
                    raise IOError(
                        f"范围 {start}-{end} 下载不完整: 期望 {expected_size} 字节, 实际 {written} 字节"
                    )

                # 整个 range 下载成功: 逐块累积的区间落盘
                self._record.save()
                return written

            except _RangeNotSupported:
                # 服务器忽略 Range: 不重试,交由 start() 降级为全量下载
                self._uncount_received(written)
                raise
            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                OSError,
                asyncio.TimeoutError,
            ) as e:
                # 本次尝试的字节将被重下覆盖,先回退计数再重试
                self._uncount_received(written)
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
                    # 保留原始错误(网络类根因),外层再由 format_network_error 追加解释
                    raise IOError(f"范围 {start}-{end} 下载失败: {e}") from e

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
                    # 保留原始错误(网络类根因),外层再由 format_network_error 追加解释。
                    # 阶段名不再叫"下载失败" —— format_status 已有该前缀,重复会显示成
                    # "下载失败: 下载失败: ..."
                    raise IOError(f"单连接下载失败: {e}") from e

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
            # 先探明 total_size 再判"已存在完整文件"
            async with httpx.AsyncClient(**client_kwargs) as client:
                total_size, supports_range = await self._get_file_info(client)
                self.progress.total_size = total_size

                # 目标已存在且大小与远端一致、无续传残留: 视为已完成跳过
                # (重下全量浪费流量/时间;同名不同内容风险由 checksum 参数兜底)
                if (
                    total_size > 0
                    and self.save_path.exists()
                    and self.save_path.stat().st_size == total_size
                    and not self._tmp_file.exists()
                    and not self._record.ranges
                    and not (self.checksum_algorithm and self.checksum_value)
                ):
                    self.progress.skipped_note = (
                        f"目标文件已存在且大小一致({format_size(total_size)}),"
                        f"跳过下载;如需强制重下请先删除该文件"
                    )
                    self.progress.finish(DownloadStatus.COMPLETED)
                    return self.progress

                if not supports_range:
                    # 不支持 Range,降级为单连接下载
                    await self._download_full(client, callback)
                    return self.progress

                # 加载已有进度
                self._record.load()

                # 确保 .tmp 文件存在且大小正确
                if not self._tmp_file.exists():
                    # 有进度记录但没有数据文件: 记录已失效(残留的幽灵进度),
                    # 不能据此跳过 range —— 否则会把"从未写入"当成"已下载"
                    self._record.clear()
                    with open(self._tmp_file, "wb") as f:
                        f.truncate(total_size)
                elif self._tmp_file.stat().st_size != total_size:
                    # 旧临时文件与远端文件大小不符(远端已更新),旧进度不可信
                    self._record.clear()
                    with open(self._tmp_file, "wb") as f:
                        f.truncate(total_size)
                else:
                    # 恢复已下载大小
                    self.progress.downloaded_size = self._record.get_downloaded_size()

                # 计算需要下载的范围
                # 按字节缺口下载: 部分完成的 range 只补缺失区间,
                # 断点续传才真正省流量(而非"有记录才跳过整段")
                ranges_to_download: list[tuple[int, int]] = []
                pos = 0
                while pos < total_size:
                    end = min(pos + self.chunk_size - 1, total_size - 1)
                    ranges_to_download.extend(self._record.missing_in(pos, end))
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
            # 部分下载区间落盘,失败重试/下次续传可接着下
            self._record.save()
            if not self._cancelled:
                # 保留原始错误链 + 追加人类可读的网络解释(DNS/拒连/超时),
                # 避免只报 "[Errno 11001] getaddrinfo failed" 让非技术用户看不懂
                msg = format_network_error(e)
                hint = _path_length_hint(self.save_path, self._tmp_file, e)
                if hint:
                    msg = f"{msg}\n{hint}"
                self.progress.finish(DownloadStatus.FAILED, msg)
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
        """取消下载。已完成的任务取消无效 —— 目标文件已就绪,不能标成"已取消"。"""
        if self.progress.status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED):
            return
        self._cancelled = True
        self._pause_event.set()
        # 逐块累积的部分区间落盘 —— 不落的话取消即丢,断点续传无从谈起
        self._record.save()
        self.progress.finish(DownloadStatus.CANCELLED, "用户取消")

    def cleanup(self) -> None:
        """清理断点续传残留(.tmp 与 progress.json)。

        取消/失败时保留它们是为了续传;任务被 remove(用户明确丢弃)时调用,
        避免孤儿 .tmp 永久占用磁盘。
        """
        self._tmp_file.unlink(missing_ok=True)
        self._record.clear()
