"""M3U8/HLS 下载引擎 — 解析播放列表、多协程并发下载、AES-128 解密、断点续传、合并。

支持:
- 主播放列表(master playlist):自动识别多分辨率,生成可选列表供用户选择
- 媒体播放列表(media playlist):直接解析分片(TS)列表
- 多协程并发下载 TS 分片,支持断点续传(按分片维度)
- AES-128-CBC 解密(`#EXT-X-KEY`)
- 按序合并所有分片为单一文件
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Callable, Awaitable
from urllib.parse import urljoin, urlparse, unquote

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from uniclaw.utils.downloader import BaseDownloader
from uniclaw.utils.http_download import (
    DEFAULT_BLOCK_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    DownloadChunkInfo,
    file_format,
    time_format,
    _parse_cookies,
)

logger = logging.getLogger(__name__)

# 默认并发数(分片下载)
DEFAULT_M3U8_CONCURRENCY = 8


def guess_video_name(url: str) -> str:
    """从 M3U8 URL 猜测视频文件名(不含扩展名)。"""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    filename = path.split("/")[-1]
    stem = Path(filename).stem if filename and "." in filename else ""
    generic = {"index", "playlist", "master", "media", "default"}
    if not stem or stem.lower() in generic:
        # 通用播放列表名时,回退到父目录名(如 1080p/index.m3u8 → 1080p)
        parent = path.split("/")[-2] if len(path.split("/")) >= 2 else ""
        if parent and parent.lower() not in generic:
            return parent
        return hashlib.sha256(url.encode()).hexdigest()[:8]
    return stem


def _stable_id(url: str, save_path: Path) -> str:
    """根据 URL 和保存路径生成临时目录标识,格式为 {文件名}_{hash}。"""
    key = f"{url}|{save_path}".encode()
    h = hashlib.sha256(key).hexdigest()[:8]
    return f"{save_path.stem}_{h}"


class M3u8Status(StrEnum):
    """M3U8 下载任务状态。"""

    PENDING = "pending"
    PARSING = "parsing"
    SELECTING = "selecting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    MERGING = "merging"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class M3u8Segment:
    """单个 TS 分片信息。"""

    index: int  # 分片序号(从 0 开始)
    duration: float  # 分片时长(秒)
    uri: str  # 原始 URI(可能相对)
    url: str  # 解析后的完整 URL
    key: "M3u8Key | None" = None  # 该分片使用的加密密钥


@dataclass
class M3u8Key:
    """HLS 加密密钥信息。"""

    method: str = "AES-128"  # 加密方法,支持 AES-128
    uri: str = ""  # 密钥文件 URI(可能相对)
    iv: str = ""  # IV,十六进制(不含 0x 前缀),为空则用分片序号
    key_bytes: bytes | None = field(default=None, repr=False)  # 已下载的密钥(16 字节)


@dataclass
class M3u8Variant:
    """主播放列表中的码率/分辨率选项。"""

    index: int  # 选项序号
    bandwidth: int  # 带宽(bps)
    resolution: str  # 分辨率,如 "1920x1080"
    codecs: str = ""  # 编码信息
    uri: str = ""  # 子播放列表 URI
    url: str = ""  # 解析后的完整 URL


@dataclass
class M3u8Progress:
    """M3U8 下载进度信息。"""

    task_id: str
    url: str
    save_path: str
    total_segments: int = 0
    downloaded_segments: int = 0
    total_size: int = 0  # 估算总大小
    downloaded_size: int = 0
    status: M3u8Status = M3u8Status.PENDING
    start_time: float = 0.0
    end_time: float = 0.0
    error: str | None = None
    speed: float = 0.0
    variants: list[M3u8Variant] = field(default_factory=list)
    selected_variant: str = ""  # 用户选择的分辨率描述
    resolution: str = ""  # 当前下载的分辨率
    encryption_method: str = ""  # 加密方式,如 "AES-128",空表示未加密
    merged_file: str = ""  # 合并后的最终文件路径

    @property
    def progress_percent(self) -> float:
        if self.total_segments <= 0:
            return 0.0
        return (self.downloaded_segments / self.total_segments) * 100

    @property
    def elapsed(self) -> float:
        if self.start_time <= 0:
            return 0.0
        if self.end_time > 0:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    @property
    def eta(self) -> float:
        if self.speed <= 0:
            return 0.0
        remaining = self.total_size - self.downloaded_size
        return remaining / self.speed

    def finish(self, status: M3u8Status, error: str | None = None) -> None:
        """标记任务完成/失败/取消。"""
        self.status = status
        self.error = error
        self.end_time = time.time()

    def format_status(self) -> str:
        """格式化为人类可读的状态字符串。"""
        if self.status == M3u8Status.COMPLETED:
            lines = [f"M3U8 下载完成: {self.merged_file or self.save_path}"]
            if self.resolution:
                lines.append(f"分辨率: {self.resolution}")
            lines.append(
                f"大小: {file_format(self.total_size)}, 耗时: {time_format(self.elapsed)}"
            )
            return "\n".join(lines)
        if self.status == M3u8Status.CANCELLED:
            return f"M3U8 下载已取消: {self.error}"
        if self.status == M3u8Status.FAILED:
            return f"M3U8 下载失败: {self.error}"
        if self.status == M3u8Status.SELECTING:
            return self._format_variants()
        if self.status == M3u8Status.PENDING:
            return "等待开始..."
        if self.status == M3u8Status.PARSING:
            return "正在解析 M3U8 播放列表..."
        if self.status == M3u8Status.MERGING:
            return f"正在合并 {self.total_segments} 个分片..."
        if self.status == M3u8Status.PAUSED:
            return (
                f"M3U8 下载已暂停: 已完成 {self.downloaded_segments}/{self.total_segments} 分片 "
                f"({self.progress_percent:.1f}%)"
            )
        # downloading
        lines = [
            f"文件: {self.save_path}",
            f"进度: {self.downloaded_segments}/{self.total_segments} 分片 "
            f"({self.progress_percent:.1f}%)",
        ]
        if self.resolution:
            lines.append(f"分辨率: {self.resolution}")
        if self.encryption_method:
            lines.append(f"加密: {self.encryption_method}")
        if self.speed > 0:
            lines.append(
                f"速度: {file_format(self.speed)}/s, 剩余: {time_format(self.eta)}"
            )
        lines.append("状态: 下载中")
        return "\n".join(lines)

    def _format_variants(self) -> str:
        """格式化可选分辨率列表。"""
        if not self.variants:
            return "未发现可用分辨率选项。"
        lines = ["发现多个分辨率选项,请选择:", ""]
        for v in self.variants:
            desc = f"{v.resolution}" if v.resolution else "未知分辨率"
            lines.append(f"  [{v.index}] {desc} ({v.bandwidth // 1000}kbps)")
        lines.append("")
        lines.append("使用 m3u8_download 重新调用并传入 variant=序号 即可选择。")
        return "\n".join(lines)


class M3u8Record:
    """M3U8 下载进度记录 — 记录已下载的分片序号,支持断点续传。

    每个分片是一个独立文件,因此以"分片序号集合"而非字节范围记录进度。
    """

    def __init__(self, progress_file: Path):
        self._file = progress_file
        self._downloaded: set[int] = set()

    @property
    def downloaded(self) -> set[int]:
        """已下载的分片序号集合。"""
        return set(self._downloaded)

    def load(self) -> None:
        """从文件加载进度。"""
        if not self._file.exists():
            self._downloaded = set()
            return
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            self._downloaded = set(int(i) for i in data.get("segments", []))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._downloaded = set()

    def save(self) -> None:
        """保存进度到文件。"""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {"segments": sorted(self._downloaded)}
        self._file.write_text(json.dumps(data), encoding="utf-8")

    def mark_downloaded(self, index: int) -> None:
        """标记一个分片为已下载。"""
        self._downloaded.add(index)
        self.save()

    def is_downloaded(self, index: int) -> bool:
        """检查分片是否已下载。"""
        return index in self._downloaded

    def clear(self) -> None:
        """清空记录并删除进度文件。"""
        self._downloaded = set()
        self._file.unlink(missing_ok=True)


def _parse_attributes(attr_str: str) -> dict[str, str]:
    """解析 M3U8 标签属性,如 `URI="key.key",IV=0x1234`。"""
    attrs: dict[str, str] = {}
    # findall 返回 (key, 完整值, 引号内值, 非引号值)
    for key, _, quoted, unquoted in re.findall(
        r'([A-Z0-9\-]+)=("([^"]*)"|([^,]*))', attr_str
    ):
        attrs[key] = quoted if quoted != "" else unquoted
    return attrs


def _resolve_url(base_url: str, uri: str) -> str:
    """将相对 URI 解析为完整 URL。"""
    if uri.startswith(("http://", "https://")):
        return uri
    return urljoin(base_url, uri)


def _safe_int(value: str, default: int = 0) -> int:
    """安全解析整数,解析失败时返回默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _looks_like_html(data: bytes) -> bool:
    """检查数据是否是 HTML 页面(而非二进制媒体内容)。

    TS 分片是二进制数据,开头不会是 HTML 标记。若服务端返回 404 错误页,
    通常以 "<!DOCTYPE" 或 "<html" 开头。
    """
    head = data[:200].strip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def _decrypt_aes128(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 解密,去除 PKCS7 填充。"""
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    # 去除 PKCS7 填充
    pad_len = padded[-1]
    if 1 <= pad_len <= 16:
        return padded[:-pad_len]
    return padded


class M3u8Downloader(BaseDownloader):
    """M3U8/HLS 下载引擎,支持多分辨率选择、多协程并发、解密与合并。"""

    def __init__(
        self,
        url: str,
        save_path: str | Path,
        concurrency: int = DEFAULT_M3U8_CONCURRENCY,
        max_retries: int = DEFAULT_MAX_RETRIES,
        proxy: str = "",
        cookies: str = "",
        headers: dict[str, str] | None = None,
        block_timeout: float = DEFAULT_BLOCK_TIMEOUT,
        variant: str = "",
        consecutive_failure_limit: int = 50,
    ):
        self.url = url
        self.save_path = Path(save_path)
        self.concurrency = concurrency
        self.max_retries = max_retries
        self.proxy = proxy
        self.cookies = cookies
        self.headers = headers or {}
        self.block_timeout = block_timeout
        self.variant = variant
        self.consecutive_failure_limit = consecutive_failure_limit

        # 内部状态
        self.task_id = uuid.uuid4().hex[:12]
        self.progress = M3u8Progress(
            task_id=self.task_id, url=url, save_path=str(save_path)
        )
        self._cancelled = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self.task: asyncio.Task | None = None
        self._abort_reason: str | None = None  # 连续失败过多时设置的终止原因
        self._abort_errors: list[str] = []  # 连续失败分片的具体错误,成功时清零;长度即连续失败数

        # 解析结果
        self._base_url: str = ""  # 主播放列表的基 URL
        self._segments: list[M3u8Segment] = []
        self._variants: list[M3u8Variant] = []
        self._media_sequence: int = 0
        self._selected_index: int = 0

        # 临时目录:与目标文件同目录,确保 rename 原子性
        stable = _stable_id(url, self.save_path)
        self._tmp_dir = self.save_path.parent / f".{stable}.m3u8"
        self._record = M3u8Record(self._tmp_dir / "progress.json")

    def _build_client_kwargs(self) -> dict:
        kwargs = {"timeout": DEFAULT_TIMEOUT, "follow_redirects": True}
        if self.proxy:
            kwargs["proxy"] = self.proxy
        if self.cookies:
            kwargs["cookies"] = _parse_cookies(self.cookies)
        return kwargs

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> str:
        """GET 请求获取文本内容,带重试。"""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                # 用 IOError 而非 CancelledError: 后者是 BaseException,不会被
                # start()/manager 的 except Exception 捕获,导致任务异常而非优雅取消
                raise IOError("下载已取消")
            try:
                resp = await client.get(url, headers=self.headers)
                resp.raise_for_status()
                return resp.text
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2**attempt * 1.0, 30.0))
        raise IOError(f"请求 {url} 失败: {last_exc}")

    async def _fetch_bytes(self, client: httpx.AsyncClient, url: str) -> bytes:
        """GET 请求获取二进制内容(如密钥文件),带重试。"""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                raise IOError("下载已取消")
            try:
                resp = await client.get(url, headers=self.headers)
                resp.raise_for_status()
                return resp.content
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2**attempt * 1.0, 30.0))
        raise IOError(f"请求 {url} 失败: {last_exc}")

    async def parse(self, client: httpx.AsyncClient | None = None) -> M3u8Progress:
        """解析 M3U8 播放列表。

        - 若是主播放列表:填充 variants;多分辨率时进入 SELECTING 状态等待用户选择,
          已传入 variant 或仅一个选项时直接解析对应媒体播放列表。
        - 若是媒体播放列表:直接解析分片。

        Args:
            client: 复用的 httpx 客户端,为空则内部创建。

        Returns:
            M3u8Progress: 解析后的进度信息。
        """
        self.progress.status = M3u8Status.PARSING
        self.progress.start_time = time.time()

        async def _do_parse(own_client: httpx.AsyncClient):
            content = await self._fetch(own_client, self.url)
            self._base_url = self.url[: self.url.rfind("/") + 1]

            if "#EXT-X-STREAM-INF" in content:
                # 主播放列表:解析可选分辨率
                self._parse_master(content)
                self.progress.variants = self._variants
                if self.variant:
                    if not self._apply_variant_choice(self.variant):
                        self.progress.finish(
                            M3u8Status.FAILED, f"无效的分辨率选项: {self.variant}"
                        )
                        return
                    selected = self._variants[self._selected_index]
                    self.progress.resolution = selected.resolution
                    self.progress.selected_variant = (
                        f"{selected.resolution} ({selected.bandwidth // 1000}kbps)"
                    )
                    await self._parse_media(own_client, selected.url)
                elif len(self._variants) > 1:
                    # 多分辨率:等待用户选择
                    self.progress.status = M3u8Status.SELECTING
                    return
                else:
                    # 仅一个选项,直接使用
                    selected = self._variants[0]
                    self.progress.resolution = selected.resolution
                    self.progress.selected_variant = (
                        f"{selected.resolution} ({selected.bandwidth // 1000}kbps)"
                    )
                    await self._parse_media(own_client, selected.url)
            else:
                # 媒体播放列表
                await self._parse_media(own_client, self.url)
            self.progress.status = M3u8Status.PENDING

        if client is not None:
            await _do_parse(client)
        else:
            async with httpx.AsyncClient(**self._build_client_kwargs()) as client:
                await _do_parse(client)
        return self.progress

    def _parse_master(self, content: str) -> None:
        """解析主播放列表的 `#EXT-X-STREAM-INF` 变体。"""
        self._variants = []
        lines = content.strip().splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                attrs = _parse_attributes(line[len("#EXT-X-STREAM-INF:") :])
                # 下一行为子播放列表 URI
                if i + 1 < len(lines):
                    uri = lines[i + 1].strip()
                    if not uri.startswith("#"):
                        self._variants.append(
                            M3u8Variant(
                                index=len(self._variants),
                                bandwidth=_safe_int(attrs.get("BANDWIDTH", 0)),
                                resolution=attrs.get("RESOLUTION", ""),
                                codecs=attrs.get("CODECS", ""),
                                uri=uri,
                                url=_resolve_url(self._base_url, uri),
                            )
                        )
                        i += 1
            i += 1

    async def _parse_media(self, client: httpx.AsyncClient, playlist_url: str) -> None:
        """解析媒体播放列表,提取分片列表和密钥信息。"""
        content = await self._fetch(client, playlist_url)
        playlist_base = playlist_url[: playlist_url.rfind("/") + 1]

        self._segments = []
        self._media_sequence = 0
        current_key: M3u8Key | None = None
        current_duration = 0.0

        for line in content.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                try:
                    self._media_sequence = int(
                        line[len("#EXT-X-MEDIA-SEQUENCE:") :].strip()
                    )
                except ValueError:
                    pass
            elif line.startswith("#EXT-X-KEY:"):
                attrs = _parse_attributes(line[len("#EXT-X-KEY:") :])
                method = attrs.get("METHOD", "NONE").upper()
                if method == "NONE":
                    current_key = None
                else:
                    key = M3u8Key(
                        method=method,
                        uri=attrs.get("URI", ""),
                        iv=attrs.get("IV", "").removeprefix("0x"),
                    )
                    # 预取密钥
                    if key.uri:
                        key_url = _resolve_url(playlist_base, key.uri)
                        key.key_bytes = await self._fetch_bytes(client, key_url)
                        self.progress.encryption_method = method
                    current_key = key
            elif line.startswith("#EXTINF:"):
                # 格式: #EXTINF:10.000,
                try:
                    current_duration = float(
                        line[len("#EXTINF:") :].split(",", 1)[0].strip()
                    )
                except ValueError:
                    current_duration = 0.0
            elif line.startswith("#"):
                continue
            else:
                # 分片 URI
                seg_url = _resolve_url(playlist_base, line)
                self._segments.append(
                    M3u8Segment(
                        index=len(self._segments),
                        duration=current_duration,
                        uri=line,
                        url=seg_url,
                        key=current_key,
                    )
                )
                current_duration = 0.0

        self.progress.total_segments = len(self._segments)

    def _apply_variant_choice(self, choice: str) -> bool:
        """根据序号或分辨率字符串选择变体。"""
        choice = choice.strip()
        # 数字:按序号
        if choice.isdigit():
            idx = int(choice)
            if 0 <= idx < len(self._variants):
                self._selected_index = idx
                return True
            return False
        # 分辨率字符串:如 "1920x1080" 或 "1080p"(p 表示高度)
        norm = choice.lower()
        for i, v in enumerate(self._variants):
            res = v.resolution.lower()
            parts = res.split("x")
            height_p = parts[1] + "p" if len(parts) == 2 else ""
            if res == norm or height_p == norm:
                self._selected_index = i
                return True
        return False

    def _segment_iv(self, seg: M3u8Segment) -> bytes:
        """计算分片的 IV。

        HLS 规范:若 `#EXT-X-KEY` 未指定 IV,则以 Media Sequence Number
        作为 16 字节大端整数作为 IV。
        """
        key = seg.key
        if key and key.iv:
            try:
                return bytes.fromhex(key.iv)
            except ValueError:
                pass
        seq = self._media_sequence + seg.index
        return seq.to_bytes(16, byteorder="big")

    def _segment_file(self, seg: M3u8Segment) -> Path:
        """分片对应的临时文件路径。"""
        return self._tmp_dir / f"seg_{seg.index:06d}.ts"

    async def _read_segment(
        self, client: httpx.AsyncClient, seg: M3u8Segment
    ) -> bytes | None:
        """流式读取整个分片,block_timeout 逐块生效。

        逐块读取时检查取消/连续失败终止/暂停:取消或终止时返回 None,调用方提前退出;
        正常完成返回完整分片字节。

        Args:
            client: 复用的 httpx 客户端。
            seg: 要下载的分片。

        Returns:
            bytes | None: 完整分片字节;取消或连续失败终止时返回 None。
        """
        data = bytearray()
        headers = {**self.headers, "Accept-Encoding": "identity"}
        async with client.stream("GET", seg.url, headers=headers) as resp:
            resp.raise_for_status()
            aiter = resp.aiter_bytes(8192).__aiter__()
            while True:
                if self._cancelled or self._abort_reason:
                    return None
                await self._pause_event.wait()
                try:
                    chunk = await asyncio.wait_for(
                        aiter.__anext__(), timeout=self.block_timeout
                    )
                except StopAsyncIteration:
                    break
                data.extend(chunk)
        return bytes(data)

    def _estimate_total_size(self) -> int:
        """按已下载分片平均大小估算总大小。

        估算值随下载推进逐渐逼近真实大小,0 表示暂无已下载分片可参考。

        Returns:
            int: 估算的总字节数。
        """
        completed_sizes = [
            self._segment_file(s).stat().st_size
            for s in self._segments
            if self._segment_file(s).exists()
        ]
        if not completed_sizes:
            return 0
        avg = sum(completed_sizes) / len(completed_sizes)
        return int(avg * len(self._segments))

    async def _download_segment(
        self,
        client: httpx.AsyncClient,
        seg: M3u8Segment,
        callback: Callable[[DownloadChunkInfo], Awaitable[None]] | None = None,
    ) -> int:
        """下载单个分片,如需解密则在下载后解密写入临时文件。

        Returns:
            写入的字节数。
        """
        seg_file = self._segment_file(seg)
        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                return 0
            if self._abort_reason:
                # 其他分片已触发连续失败终止,本分片不再继续(进入前 _limited_download 已检查,
                # 此处兜底让重试循环中的分片也快速退出)
                return 0
            await self._pause_event.wait()

            try:
                data = await self._read_segment(client, seg)
                if data is None:
                    # 分片读取被取消或连续失败终止
                    return 0

                # 防误判:CDN/防盗链经常对失效分片返回 200 + HTML 错误页,
                # 直接写入会把垃圾内容合并进最终文件。TS 分片不应是文本。
                if _looks_like_html(data):
                    err_msg = (
                        f"分片 {seg.index} 返回 HTML 页面而非媒体内容"
                        "(可能已失效或被防盗链拦截)"
                    )
                    # 先通过 callback 通知错误,再抛异常由 start() 做最终处理
                    if callback:
                        err_info = DownloadChunkInfo(
                            chunk_size=0,
                            downloaded=self.progress.downloaded_size,
                            total=self.progress.total_size,
                            speed=self.progress.speed,
                            error=err_msg,
                        )
                        await callback(err_info)
                    # 用 ValueError 而非 IOError: 后者是 OSError 子类,会被下方
                    # except (OSError...) 捕获并重试,而 HTML 错误页是确定性失败,重试无意义
                    raise ValueError(err_msg)

                # 解密
                if seg.key and seg.key.method == "AES-128" and seg.key.key_bytes:
                    iv = self._segment_iv(seg)
                    data = _decrypt_aes128(data, seg.key.key_bytes, iv)

                # 写入临时文件(先写临时再原子替换,避免崩溃产生半截文件)
                seg_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = seg_file.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.rename(seg_file)

                self.progress.downloaded_size += len(data)
                self._record.mark_downloaded(seg.index)
                self.progress.downloaded_segments += 1

                # 下载成功: 重置连续失败记录(计数以 _abort_errors 长度为准)
                self._abort_errors = []

                # 每下载一个分片即重新估算总大小,progress 的 total 随下载推进逐渐逼近真实值
                self.progress.total_size = self._estimate_total_size()

                # 更新速度
                elapsed = time.time() - self.progress.start_time
                self.progress.speed = (
                    self.progress.downloaded_size / elapsed if elapsed > 0 else 0
                )

                if callback:
                    info = DownloadChunkInfo(
                        chunk_size=len(data),
                        downloaded=self.progress.downloaded_size,
                        total=self.progress.total_size,
                        speed=self.progress.speed,
                    )
                    await callback(info)
                return len(data)

            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                OSError,
                asyncio.TimeoutError,
            ) as e:
                if attempt < self.max_retries:
                    wait_time = min(2**attempt * 1.0, 30.0)
                    logger.warning(
                        "分片 %d 下载失败(尝试 %d/%d): %s, %.1fs 后重试",
                        seg.index,
                        attempt + 1,
                        self.max_retries,
                        e,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    raise IOError(f"分片 {seg.index} 下载失败: {e}") from e

    async def start(
        self,
        callback: Callable[[DownloadChunkInfo], Awaitable[None]] | None = None,
    ) -> M3u8Progress:
        """执行下载。

        Args:
            callback: 每下载一个分片的回调,参数为 DownloadChunkInfo 进度快照。

        Returns:
            M3u8Progress: 下载完成后的进度信息。
        """
        self.progress.status = M3u8Status.DOWNLOADING
        self.progress.start_time = time.time()
        self.progress.downloaded_size = 0
        self._cancelled = False
        self._pause_event.set()
        self._abort_reason = None
        self._abort_errors = []

        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            self._tmp_dir.mkdir(parents=True, exist_ok=True)

            # 加载已有进度
            self._record.load()

            # 若尚未解析分片,先解析
            if not self._segments:
                await self.parse()
                if self.progress.status in (M3u8Status.FAILED, M3u8Status.SELECTING):
                    return self.progress

            # 恢复已下载分片与大小(在 pending 计算之前,确保记录准确)
            existing_files = 0
            for s in self._segments:
                f = self._segment_file(s)
                if f.exists():
                    self.progress.downloaded_size += f.stat().st_size
                    self._record.mark_downloaded(s.index)
                    existing_files += 1
            self.progress.downloaded_segments = existing_files

            # 需要下载的分片:未标记下载或文件缺失
            pending = [
                seg
                for seg in self._segments
                if not (
                    self._record.is_downloaded(seg.index)
                    and self._segment_file(seg).exists()
                )
            ]

            # 估算总大小(按已下载分片平均大小推算;首个分片下载后 _download_segment 会再更新)
            self.progress.total_size = self._estimate_total_size()

            if callback:
                info = DownloadChunkInfo(
                    chunk_size=0,
                    downloaded=self.progress.downloaded_size,
                    total=self.progress.total_size,
                    speed=self.progress.speed,
                )
                await callback(info)

            # 并发下载
            semaphore = asyncio.Semaphore(self.concurrency)

            async def _limited_download(seg: M3u8Segment, client: httpx.AsyncClient):
                async with semaphore:
                    # 已触发连续失败终止,不再发起下载
                    if self._abort_reason:
                        return 0
                    try:
                        await self._download_segment(client, seg, callback)
                    except Exception as e:
                        if self._abort_reason:
                            raise IOError(self._abort_reason)
                        # 记录所有失败分片的具体错误(含分片 URL,便于定位失效链接),长度即连续失败数
                        self._abort_errors.append(f"分片 {seg.index} ({seg.url}): {e}")
                        # 连续失败数(即 _abort_errors 长度)达到阈值则终止整个下载
                        if (
                            self.consecutive_failure_limit > 0
                            and len(self._abort_errors)
                            >= self.consecutive_failure_limit
                        ):
                            # 汇总展示只取前 3 个错误,避免信息过长
                            detail = "; ".join(self._abort_errors[:3])
                            self._abort_reason = (
                                f"连续 {self.consecutive_failure_limit} 个分片下载失败"
                                f"({detail})"
                                "(可能链接已失效或被防盗链拦截)"
                            )
                        raise

            if pending:
                async with httpx.AsyncClient(**self._build_client_kwargs()) as client:
                    tasks = [_limited_download(seg, client) for seg in pending]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    # 连续失败过多已提前终止: 优先以终止原因作为错误信息,
                    # 否则 gather 可能抛 abort 触发前的原始异常,丢失汇总信息
                    if self._abort_reason:
                        raise IOError(self._abort_reason)
                    # 任一异常由外层 except 统一转为 FAILED
                    for result in results:
                        if isinstance(result, Exception):
                            raise result

            # 取消时保留临时文件供续传
            if self._cancelled:
                self.progress.finish(M3u8Status.CANCELLED, "用户取消")
                return self.progress

            # 校验所有分片
            for seg in self._segments:
                if not self._segment_file(seg).exists():
                    self.progress.finish(
                        M3u8Status.FAILED, f"分片 {seg.index} 缺失,下载未完成"
                    )
                    return self.progress

            # 合并
            self.progress.status = M3u8Status.MERGING
            await self._merge_segments()

            # 完成
            self._record.clear()
            self.progress.total_size = self.progress.downloaded_size
            self.progress.merged_file = str(self.save_path)
            self.progress.finish(M3u8Status.COMPLETED)
            # 清理临时目录(分片 + 进度记录),失败/取消时保留以供断点续传
            self.cleanup()
            return self.progress

        except Exception as e:
            if not self._cancelled:
                self.progress.finish(M3u8Status.FAILED, str(e))
                logger.error("M3U8 下载失败: %s", e)
            return self.progress

    async def _merge_segments(self) -> None:
        """按序合并所有分片为最终文件。"""
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        # 合并到临时文件,完成后再原子替换,避免中断产生半截文件
        tmp_out = self.save_path.with_suffix(self.save_path.suffix + ".part")
        with open(tmp_out, "wb") as out:
            for seg in self._segments:
                seg_file = self._segment_file(seg)
                if seg_file.exists():
                    with open(seg_file, "rb") as f:
                        while chunk := f.read(1024 * 1024):
                            out.write(chunk)
        self.save_path.unlink(missing_ok=True)
        tmp_out.rename(self.save_path)

    def pause(self) -> None:
        """暂停下载。"""
        self._pause_event.clear()
        self.progress.status = M3u8Status.PAUSED

    def resume(self) -> None:
        """恢复下载。"""
        self._pause_event.set()
        self.progress.status = M3u8Status.DOWNLOADING

    def cancel(self) -> None:
        """取消下载。"""
        self._cancelled = True
        self._pause_event.set()
        self.progress.finish(M3u8Status.CANCELLED, "用户取消")

    def cleanup(self) -> None:
        """清理临时目录(分片文件与进度记录)。"""
        import shutil

        if self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
