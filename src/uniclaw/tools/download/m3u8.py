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
from uniclaw.utils.format import format_size
from uniclaw.utils.http_download import (
    DEFAULT_BLOCK_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    DownloadChunkInfo,
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
    # stem 截断: 超长文件名场景下旁路暂存文件名不能把全路径顶过 MAX_PATH
    return f"{save_path.stem[:24]}_{h}"


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
    """单个 TS/fMP4 分片信息。"""

    index: int  # 分片序号(从 0 开始)
    duration: float  # 分片时长(秒)
    uri: str  # 原始 URI(可能相对)
    url: str  # 解析后的完整 URL
    key: "M3u8Key | None" = None  # 该分片使用的加密密钥
    byte_range: tuple[int, int] | None = None  # #EXT-X-BYTERANGE (offset, length),仅取 URI 的该区间


@dataclass
class M3u8Map:
    """`#EXT-X-MAP` 初始化段信息(fMP4/CMAF 流必有,输出文件开头只放一份)。"""

    uri: str
    url: str
    byte_range: tuple[int, int] | None = None  # (offset, length),无则取整个 URI
    key: "M3u8Key | None" = None


@dataclass
class M3u8Key:
    """HLS 加密密钥信息。"""

    method: str = "AES-128"  # 加密方法,支持 AES-128
    uri: str = ""  # 密钥文件 URI(可能相对)
    iv: str = ""  # IV,十六进制(不含 0x 前缀),为空则用分片序号
    key_bytes: bytes | None = field(default=None, repr=False)  # 已下载的密钥(16 字节)


@dataclass
class M3u8Media:
    """主播放列表 `#EXT-X-MEDIA` 条目(替代音轨/字幕/CLOSED-CAPTIONS)。"""

    type: str  # AUDIO / SUBTITLES / CLOSED-CAPTIONS / VIDEO
    group_id: str
    name: str
    language: str = ""
    is_default: bool = False
    autoselect: bool = False
    uri: str = ""  # 原始 URI(独立媒体播放列表)
    url: str = ""  # 解析后的完整 URL,可单独用 m3u8_download 下载


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
    media_tracks: list["M3u8Media"] = field(default_factory=list)  # 音轨/字幕清单
    discontinuity_count: int = 0  # #EXT-X-DISCONTINUITY 处数(切广告/拼接点)
    selected_variant: str = ""  # 用户选择的分辨率描述
    resolution: str = ""  # 当前下载的分辨率
    encryption_method: str = ""  # 加密方式,如 "AES-128",空表示未加密
    merged_file: str = ""  # 合并后的最终文件路径
    has_byterange: bool = False  # 分片带 #EXT-X-BYTERANGE(展示口径用"区间")
    init_iv_note: str = ""  # init 解密 IV 歧义提示(无则为空)

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
                f"大小: {format_size(self.total_size)}, 耗时: {time_format(self.elapsed)}"
            )
            if self.init_iv_note:
                lines.append(self.init_iv_note)
            if self.discontinuity_count:
                lines.append(
                    f"⚠ 流含 {self.discontinuity_count} 处 discontinuity"
                    "(切广告/拼接点),已按序拼接;若切点处音视频参数有变化,"
                    "产物可能音画不同步,请试播检查"
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
        unit = "区间" if self.has_byterange else "分片"
        if self.status == M3u8Status.MERGING:
            return f"正在合并 {self.total_segments} 个{unit}..."
        if self.status == M3u8Status.PAUSED:
            return (
                f"M3U8 下载已暂停: 已完成 {self.downloaded_segments}/{self.total_segments} {unit} "
                f"({self.progress_percent:.1f}%)"
            )
        # downloading
        unit = "区间" if self.has_byterange else "分片"
        lines = [
            f"文件: {self.save_path}",
            f"进度: {self.downloaded_segments}/{self.total_segments} {unit} "
            f"({self.progress_percent:.1f}%)",
        ]
        if self.resolution:
            lines.append(f"分辨率: {self.resolution}")
        if self.encryption_method:
            lines.append(f"加密: {self.encryption_method}")
        if self.speed > 0:
            lines.append(
                f"速度: {format_size(self.speed)}/s, 剩余: {time_format(self.eta)}"
            )
        lines.append("状态: 下载中")
        return "\n".join(lines)

    def _format_variants(self) -> str:
        """格式化可选变体列表(按码率降序,含音轨/字幕清单)。"""
        if not self.variants:
            return "未发现可用变体选项。"
        lines = ["发现多个变体选项(按码率从高到低),请选择:", ""]
        for v in self.variants:
            # 无 RESOLUTION 的变体是纯音频轨(gear),标注出来别当"未知"
            desc = v.resolution if v.resolution else "音频"
            lines.append(f"  [{v.index}] {desc} ({int(v.bandwidth) // 1000}kbps)")
        if self.media_tracks:
            for ttype, label, tag in (("AUDIO", "音轨", "a"), ("SUBTITLES", "字幕", "s")):
                tracks = [t for t in self.media_tracks if t.type == ttype]
                if not tracks:
                    continue
                lines.append("")
                lines.append(f"{label}({len(tracks)}):")
                for i, t in enumerate(tracks):
                    lang = t.language or t.name or t.group_id
                    default_note = "(默认)" if t.is_default else ""
                    uri_hint = f", URI={t.uri}" if t.uri else "(无独立地址)"
                    lines.append(f"  [{tag}{i}] {lang}{default_note}{uri_hint}")
            lines.append("")
            lines.append(
                "提示: 默认下载变体自带(muxed)音轨;需要独立音轨/字幕时,"
                "把上面的 URI 作为 url 交给 m3u8_download 单独下载为独立文件。"
            )
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


def _parse_byterange_spec(spec: str, default_offset: int = 0) -> tuple[int, int]:
    """解析 `<length>[@<offset>]` 字节区间描述(`#EXT-X-BYTERANGE`/MAP 的 BYTERANGE)。

    非法区间(length<=0/负 offset/非数字)直接抛错 —— 沉默回退成"整文件下载"
    会把单文件 fMP4 流重复拉全量再拼 init,产出体积膨胀且带重复 ftyp 的损坏文件。

    Args:
        spec: 区间描述,如 "100@0";省略 `@offset` 时用 default_offset。
        default_offset: 无 `@offset` 时的偏移(`#EXT-X-BYTERANGE` 为上一分片
            末尾续接;MAP 视为 0)。

    Returns:
        tuple[int, int]: (offset, length)。

    Raises:
        ValueError: 区间描述非法(非数字、length<=0 或 offset<0)。
    """
    spec = spec.strip()
    length_s, at, offset_s = spec.partition("@")
    try:
        length = int(length_s.strip())
    except ValueError:
        raise ValueError(
            f"非法 #EXT-X-BYTERANGE 区间 {spec!r}: length 必须是整数"
        ) from None
    if length <= 0:
        raise ValueError(
            f"非法 #EXT-X-BYTERANGE 区间 {spec!r}: length 必须为正整数"
            "(0/负值会被误当整文件下载,产生膨胀损坏的合并产物)"
        )
    if at:
        try:
            offset = int(offset_s.strip())
        except ValueError:
            raise ValueError(
                f"非法 #EXT-X-BYTERANGE 区间 {spec!r}: offset 必须是整数"
            ) from None
    else:
        offset = default_offset
    if offset < 0:
        raise ValueError(
            f"非法 #EXT-X-BYTERANGE 区间 {spec!r}: offset 不能为负"
        )
    return (offset, length)


def _looks_like_html(data: bytes) -> bool:
    """检查数据是否是 HTML 页面(而非二进制媒体内容)。

    TS 分片是二进制数据,开头不会是 HTML 标记。若服务端返回 404 错误页,
    通常以 "<!DOCTYPE" 或 "<html" 开头。
    """
    head = data[:200].strip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html")


def _playlist_error(content: str) -> str | None:
    """检查文本是否是 M3U8 播放列表,不是则返回报错说明。

    播放列表必以 `#EXTM3U` 开头(部分服务端省略该行但仍有 `#EXTINF`/
    `#EXT-X-` 标签,两者都接受)。HTML 网页/纯文本/防盗链错误页都没有这些
    标签 —— 若不拦下,内容的每一行都会被当成分片 URI 拼 URL 发起下载,
    最终报"分片 0 下载失败",完全掩盖"URL 根本不是播放列表"的真实原因。

    Args:
        content: URL 返回的文本内容。

    Returns:
        str | None: 报错说明(含内容开头预览);是合法播放列表返回 None。
    """
    text = content.lstrip("\ufeff \t\r\n")
    if text.startswith("#EXTM3U") or "#EXTINF:" in content or "#EXT-X-" in content:
        return None
    preview = " ".join(content.strip().split())[:80]
    return (
        f"URL 返回的内容不是有效的 M3U8 播放列表(开头为: {preview!r})。"
        "可能是 HTML 网页/纯文本/防盗链错误页;"
        "请确认 URL 指向 .m3u8 播放列表,或带上 Referer/Cookie 等请求头重试"
    )


def _decrypt_aes128(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 解密,去除 PKCS7 填充。"""
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    if not padded:
        return padded
    # 严格 PKCS7: 填充不合法则原样返回 —— 不加 PKCS7 的打包器(块对齐
    # 直接 CBC)存在,宽松按尾字节截会把无填充流的尾部误切
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded[-pad_len:] == bytes([pad_len]) * pad_len:
        return padded[:-pad_len]
    return padded


def _looks_like_init_section(data: bytes) -> bool:
    """判断解密结果是否像 fMP4 初始化段(box 序列走到尾部)。

    按 MP4 box(size[4]+type[4] 前进)遍历;尾部不足一个 box 头时
    须是零填充/空(容错截断构造)。只看 ftyp 魔数不够 —— 两个候选
    IV 的差异可能恰好落在 box 无关字节,魔数完好会误判。
    """
    if len(data) < 8:
        return False
    if data[4:8] not in (b"ftyp", b"moov", b"styp", b"sidx"):
        return False
    pos = 0
    n = len(data)
    while pos + 8 <= n:
        size = int.from_bytes(data[pos : pos + 4], "big")
        box_type = data[pos + 4 : pos + 8]
        if not box_type.strip(b"\x00"):
            # 全零区 = 尾部对齐填充,合法结尾(容错截断构造/无 padding 流)
            return not data[pos:].strip(b"\x00")
        if size == 0:
            return True
        if size == 1:
            if pos + 16 > n:
                return False
            size = int.from_bytes(data[pos + 8 : pos + 16], "big")
        if size < 8:
            return False
        pos += size
    # 尾部不足一个 box 头: 零填充/空即合法(容错)
    return not data[pos:].strip(b"\x00")


def _decrypt_map_section(
    data: bytes, map_info: "M3u8Map", iv_mode: str = "auto"
) -> tuple[bytes, str]:
    """解密 `#EXT-X-MAP` 初始化段,兼容两种 IV 约定。

    RFC 8216 规定加密 MAP 用生效 KEY 的显式 IV;打包器实践亦常见
    用隐式 IV(全 0)。auto: 逐候选解密并按 box 结构校验,唯一通过
    即采纳;两个都通过(IV 差异恰落在 box 无关字节,分不出)时取
    全 0 并给出歧义提示;都不通过按规范取显式 IV。
    iv_mode=key/zero 可强制指定(歧义场景自纠)。

    Args:
        data: 加密的初始化段字节。
        map_info: MAP 信息(含生效的 key)。
        iv_mode: auto/key/zero。

    Returns:
        tuple[bytes, str]: (解密结果, 歧义提示;无歧义时为空串)。
    """
    key = map_info.key
    zero_iv = b"\x00" * 16
    candidates: list[tuple[str, bytes]] = []
    if key and key.iv:
        try:
            candidates.append(("key", bytes.fromhex(key.iv)))
        except ValueError:
            raise ValueError(
                f"初始化段 IV 非法({key.iv!r}): 不是合法的十六进制"
            ) from None
    candidates.append(("zero", zero_iv))

    if iv_mode == "key":
        return _decrypt_aes128(data, key.key_bytes, candidates[0][1]), ""
    if iv_mode == "zero":
        return _decrypt_aes128(data, key.key_bytes, zero_iv), ""

    passed: list[tuple[str, bytes]] = []
    for mode, iv in candidates:
        plain = _decrypt_aes128(data, key.key_bytes, iv)
        if _looks_like_init_section(plain):
            passed.append((mode, plain))
    if len(passed) == 1:
        return passed[0][1], ""
    if len(passed) >= 2:
        # 两候选都合法(box 校验分不出): 自动双解对比 —— IV 差异只污染
        # 第一块,差异字节上"更接近零填充/minor_version=0"的一方是真解
        # (真解的差异字节通常是补零/版本字段;错解被 IV 差异污染成非零)
        p_key = passed[0][1]
        p_zero = passed[-1][1]
        n = min(len(p_key), len(p_zero))
        key_only_zero = zero_only_zero = True
        for i in range(n):
            if p_key[i] != p_zero[i]:
                if p_key[i] != 0:
                    key_only_zero = False
                if p_zero[i] != 0:
                    zero_only_zero = False
        if zero_only_zero and not key_only_zero:
            return p_zero, ""
        if key_only_zero and not zero_only_zero:
            return p_key, ""
        # 差异字节双方都非零: 分不出,按 RFC 8216 默认显式 IV + 提示
        for mode, plain in passed:
            if mode == "key":
                return plain, (
                    "⚠ init 解密存在 IV 歧义且无法自动比对,已按规范取 KEY 显式 IV;"
                    "若产物异常请用 init_iv=zero 重试"
                )
    return passed[0][1] if passed else _decrypt_aes128(data, key.key_bytes, candidates[0][1]), ""
    return _decrypt_aes128(data, key.key_bytes, candidates[0][1]), ""


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
        init_iv: str = "auto",
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
        self._init_iv_mode = init_iv or "auto"  # init 段 IV: auto/key/zero
        self._init_iv_note = ""
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
        self._media_tracks: list[M3u8Media] = []
        self._media_sequence: int = 0
        self._selected_index: int = 0
        self._map_info: M3u8Map | None = None  # #EXT-X-MAP 初始化段(fMP4)

        # 临时目录:与目标文件同目录,确保 rename 原子性
        stable = _stable_id(url, self.save_path)
        self._tmp_dir = self.save_path.parent / f".{stable}.m3u8"
        self._record = M3u8Record(self._tmp_dir / "progress.json")
        self._init_file = self._tmp_dir / "init.bin"

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

            # 先确认返回的真是播放列表: HTML/纯文本/防盗链错误页会被逐行当成分片
            # URI 发起下载,最终报"分片 0 下载失败",完全误导排障方向
            err = _playlist_error(content)
            if err:
                self.progress.finish(M3u8Status.FAILED, f"{err}(url={self.url})")
                return

            if "#EXT-X-STREAM-INF" in content:
                # 主播放列表:解析可选分辨率
                self._parse_master(content)
                self.progress.variants = self._variants
                self.progress.media_tracks = self._media_tracks
                if not self._variants:
                    self.progress.finish(
                        M3u8Status.FAILED,
                        f"主播放列表中未解析出任何 #EXT-X-STREAM-INF 分辨率选项(url={self.url})",
                    )
                    return
                if self.variant:
                    if not self._apply_variant_choice(self.variant):
                        self.progress.finish(
                            M3u8Status.FAILED, f"无效的分辨率选项: {self.variant}"
                        )
                        return
                    selected = self._variants[self._selected_index]
                    self.progress.resolution = selected.resolution
                    self.progress.selected_variant = (
                        f"{selected.resolution} ({int(selected.bandwidth) // 1000}kbps)"
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
                        f"{selected.resolution} ({int(selected.bandwidth) // 1000}kbps)"
                    )
                    await self._parse_media(own_client, selected.url)
            else:
                # 媒体播放列表
                await self._parse_media(own_client, self.url)
            # _parse_media 校验失败时状态已是 FAILED,不能覆盖回 PENDING
            if self.progress.status != M3u8Status.FAILED:
                self.progress.status = M3u8Status.PENDING

        if client is not None:
            await _do_parse(client)
        else:
            async with httpx.AsyncClient(**self._build_client_kwargs()) as client:
                await _do_parse(client)
        return self.progress

    def _parse_master(self, content: str) -> None:
        """解析主播放列表的 `#EXT-X-STREAM-INF` 变体与 `#EXT-X-MEDIA` 音轨/字幕。

        变体按码率降序编号([0]=最高清) —— Apple 类列表常按升序排,
        原样编号会让 [0] 是最差画质,与直觉相反。
        """
        self._variants = []
        self._media_tracks = []
        lines = content.strip().splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXT-X-MEDIA:"):
                attrs = _parse_attributes(line[len("#EXT-X-MEDIA:") :])
                media_type = attrs.get("TYPE", "")
                if media_type in ("AUDIO", "SUBTITLES"):
                    uri = attrs.get("URI", "")
                    self._media_tracks.append(
                        M3u8Media(
                            type=media_type,
                            group_id=attrs.get("GROUP-ID", ""),
                            name=attrs.get("NAME", ""),
                            language=attrs.get("LANGUAGE", ""),
                            is_default=attrs.get("DEFAULT", "").upper() == "YES",
                            autoselect=attrs.get("AUTOSELECT", "").upper() == "YES",
                            uri=uri,
                            url=_resolve_url(self._base_url, uri) if uri else "",
                        )
                    )
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

        # 码率降序编号: [0]=最高清;同码率时带分辨率的视频变体在前
        self._variants.sort(key=lambda v: (v.bandwidth, bool(v.resolution)), reverse=True)
        for idx, v in enumerate(self._variants):
            v.index = idx
        self._media_tracks.sort(
            key=lambda t: (t.type, not t.is_default, t.language, t.name)
        )

    async def _parse_media(self, client: httpx.AsyncClient, playlist_url: str) -> None:
        """解析媒体播放列表,提取分片列表和密钥信息。

        内容不是播放列表或未包含任何分片时,把 progress 置为 FAILED 并返回,
        不产出误导性的空分片列表。
        """
        content = await self._fetch(client, playlist_url)
        playlist_base = playlist_url[: playlist_url.rfind("/") + 1]

        err = _playlist_error(content)
        if err:
            self.progress.finish(
                M3u8Status.FAILED, f"{err}(playlist={playlist_url})"
            )
            return

        self._segments = []
        self._media_sequence = 0
        self._map_info = None
        self.progress.discontinuity_count = 0
        current_key: M3u8Key | None = None
        current_duration = 0.0
        pending_range: tuple[int, int] | None = None
        last_uri = ""
        last_range_end = 0

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
            elif line.startswith("#EXT-X-MAP:"):
                # fMP4/CMAF 初始化段(ftyp+moov): 输出文件开头只放一份。
                # 不解析它会把每个分片背后的整个媒体文件当独立分片下载/拼接,
                # 产物体积膨胀百倍且不可播放
                attrs = _parse_attributes(line[len("#EXT-X-MAP:") :])
                map_uri = attrs.get("URI", "")
                if map_uri:
                    raw_range = attrs.get("BYTERANGE", "")
                    try:
                        # MAP 省略 offset 视为 0(不是续接语义)
                        map_range = (
                            _parse_byterange_spec(raw_range, default_offset=0)
                            if raw_range.strip()
                            else None
                        )
                    except ValueError as e:
                        self.progress.finish(
                            M3u8Status.FAILED,
                            f"{e}(#EXT-X-MAP URI={map_uri!r}, playlist={playlist_url})",
                        )
                        return
                    self._map_info = M3u8Map(
                        uri=map_uri,
                        url=_resolve_url(playlist_base, map_uri),
                        byte_range=map_range,
                        key=current_key,
                    )
            elif line.startswith("#EXT-X-DISCONTINUITY"):
                # 切广告/拼接点: 此处音视频轨参数可能突变,裸拼接有风险,
                # 至少要在完成信息里提示试播检查
                self.progress.discontinuity_count += 1
            elif line.startswith("#EXT-X-BYTERANGE:"):
                # 格式: #EXT-X-BYTERANGE:<length>[@<offset>];省略 offset 时
                # 接在同 URI 上一分片的末尾(单文件多分片的 fMP4 流)
                spec = line[len("#EXT-X-BYTERANGE:") :].strip()
                try:
                    pending_range = _parse_byterange_spec(
                        spec, default_offset=last_range_end
                    )
                except ValueError as e:
                    # 与分片越界同口径: 拒绝静默整文件回退,明确报错
                    self.progress.finish(
                        M3u8Status.FAILED, f"{e}(playlist={playlist_url})"
                    )
                    return
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
                        byte_range=pending_range,
                    )
                )
                if pending_range is not None:
                    last_range_end = pending_range[0] + pending_range[1]
                if line != last_uri:
                    # 换了资源,无 @offset 的续接位置不能跨 URI 延续
                    if pending_range is None:
                        last_range_end = 0
                    last_uri = line
                pending_range = None
                current_duration = 0.0

        self.progress.total_segments = len(self._segments)
        self.progress.has_byterange = any(s.byte_range for s in self._segments)
        if not self._segments:
            self.progress.finish(
                M3u8Status.FAILED,
                f"媒体播放列表未包含任何分片(playlist={playlist_url})",
            )

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

    async def _read_byte_range(
        self, client: httpx.AsyncClient, url: str, offset: int, length: int
    ) -> bytes | None:
        """按 `#EXT-X-BYTERANGE` 只取资源的 [offset, offset+length) 区间。

        发 Range 请求;服务器无视 Range 返回 200 时,流式跳过前 offset 字节
        再取 length 字节(正确性优先,但省不下带宽)。逐块检查取消/暂停。

        Returns:
            bytes | None: 区间字节;取消或连续失败终止时返回 None。
        """
        data = bytearray()
        headers = {
            **self.headers,
            "Accept-Encoding": "identity",
            "Range": f"bytes={offset}-{offset + length - 1}",
        }
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            ignore_range = resp.status_code == 200
            skip = offset if ignore_range else 0
            take = length
            aiter = resp.aiter_bytes(8192).__aiter__()
            while take > 0:
                if self._cancelled or self._abort_reason:
                    return None
                await self._pause_event.wait()
                try:
                    chunk = await asyncio.wait_for(
                        aiter.__anext__(), timeout=self.block_timeout
                    )
                except StopAsyncIteration:
                    break
                if skip >= len(chunk):
                    skip -= len(chunk)
                    continue
                if skip:
                    chunk = chunk[skip:]
                    skip = 0
                piece = chunk[:take]
                data.extend(piece)
                take -= len(piece)
        return bytes(data)

    async def _ensure_map(self) -> bool:
        """下载/复用 `#EXT-X-MAP` 初始化段,写入 `init.bin`。

        已存在即跳过(断点续传);失败重试 max_retries 次后返回 False。

        Returns:
            bool: init.bin 就绪返回 True;取消或重试耗尽返回 False。
        """
        if self._map_info is None:
            return True
        if self._init_file.exists():
            # 加密流的 init 缓存要像合法 MP4 box —— 旧版本按错 IV 解出的
            # 坏 init 若直接复用,产物永远修不好;不像就重下重解(自愈)
            if self._map_info.key is None or _looks_like_init_section(
                self._init_file.read_bytes()
            ):
                return True
            self._init_file.unlink(missing_ok=True)
        map_info = self._map_info
        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                return False
            await self._pause_event.wait()
            try:
                async with httpx.AsyncClient(
                    **self._build_client_kwargs()
                ) as client:
                    if map_info.byte_range is not None:
                        offset, length = map_info.byte_range
                        data = await self._read_byte_range(
                            client, map_info.url, offset, length
                        )
                        # 服务器截断/误返整文件当 init 会拼出损坏产物 ——
                        # 与分片区间同口径校验,不够就是没拿到目标区间
                        if data is not None and len(data) != length:
                            raise IOError(
                                f"初始化段区间下载不完整: 期望 {length} 字节, "
                                f"实际 {len(data)} 字节({map_info.url})"
                            )
                    else:
                        data = await self._fetch_bytes(client, map_info.url)
                if data is None:
                    return False
                # AES-128 时初始化段同样加密,按声明位置的 key 解密
                if (
                    map_info.key
                    and map_info.key.method == "AES-128"
                    and map_info.key.key_bytes
                ):
                    data, iv_note = _decrypt_map_section(
                        data, map_info, self._init_iv_mode
                    )
                    self._init_iv_note = iv_note
                    self.progress.init_iv_note = iv_note
                self._init_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._init_file.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.rename(self._init_file)
                # downloaded_size 由 start() 的恢复段统一计入 ——
                # 这里再 += 会与恢复段重复(init 双计数,进度虚高一倍)
                return True
            except (
                httpx.HTTPStatusError,
                httpx.RequestError,
                OSError,
                asyncio.TimeoutError,
            ) as e:
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2**attempt * 1.0, 30.0))
                else:
                    logger.error("初始化段下载失败: %s", e)
                    return False
        return False

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
                if seg.byte_range is not None:
                    offset, length = seg.byte_range
                    data = await self._read_byte_range(client, seg.url, offset, length)
                else:
                    data = await self._read_segment(client, seg)
                if data is None:
                    # 分片读取被取消或连续失败终止
                    return 0

                # BYTERANGE 分片: 只该拿到区间长度的字节,少了就是没下全
                if seg.byte_range is not None:
                    expected = seg.byte_range[1]
                    if len(data) != expected:
                        raise IOError(
                            f"分片 {seg.index} 字节区间下载不完整: "
                            f"期望 {expected} 字节, 实际 {len(data)} 字节"
                        )

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

            # fMP4 初始化段(#EXT-X-MAP): 先落地一份,合并时放文件开头;
            # 缺了它所有分片拼起来只是裸 mdat,不可播放
            if self._map_info is not None:
                ok = await self._ensure_map()
                if not ok:
                    if self._cancelled:
                        self.progress.finish(M3u8Status.CANCELLED, "用户取消")
                        return self.progress
                    self.progress.finish(
                        M3u8Status.FAILED,
                        f"初始化段(#EXT-X-MAP)下载失败: {self._map_info.url}",
                    )
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
            if self._map_info is not None and self._init_file.exists():
                self.progress.downloaded_size += self._init_file.stat().st_size

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

            # 取消:保留中间分片工作目录供断点续传(不删最终交付物 save_path)
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
            if self._cancelled:
                # 取消导致的异常(如分片下载中止): 保留中间分片供续传,
                # 取消状态已由 cancel() 置好
                pass
            else:
                self.progress.finish(M3u8Status.FAILED, str(e))
                logger.error("M3U8 下载失败: %s", e)
            return self.progress

    async def _merge_segments(self) -> None:
        """按序合并所有分片为最终文件。

        fMP4 流(`#EXT-X-MAP`)在文件开头写入一份初始化段(ftyp+moov),
        再按序拼接各分片(含 BYTERANGE 分片)—— 每分片重复 init 会导致
        产物体积膨胀百倍且不可播放。
        """
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        # 合并到临时文件,完成后再原子替换,避免中断产生半截文件
        tmp_out = self.save_path.with_suffix(self.save_path.suffix + ".part")
        with open(tmp_out, "wb") as out:
            if self._map_info is not None and self._init_file.exists():
                with open(self._init_file, "rb") as f:
                    while chunk := f.read(1024 * 1024):
                        out.write(chunk)
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
        """取消下载,保留已下载分片供断点续传。

        与 HttpDownloader 的取消语义一致:中间分片目录(含 progress.json)
        保留在磁盘上,重新调用 m3u8_download(同 URL 同 save_path)自动续传,
        不重复下载已完成分片。已完成/已失败的任务取消无效 —— 产物已就绪或
        已定性,不能标成"已取消"。合并阶段(MERGING)的取消不打断合并,
        见 start() 中"合并期间取消仍完成合并"的约定。
        """
        if self.progress.status in (M3u8Status.COMPLETED, M3u8Status.FAILED):
            return
        self._cancelled = True
        self._pause_event.set()
        self.progress.finish(M3u8Status.CANCELLED, "用户取消")

    def cleanup(self) -> None:
        """清理临时目录(分片文件与进度记录)。"""
        import shutil

        if self._tmp_dir.exists():
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
