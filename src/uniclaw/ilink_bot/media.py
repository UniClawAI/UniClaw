from __future__ import annotations

import io
import struct
from typing import TYPE_CHECKING

import requests

from .crypto import decode_aes_key, decrypt_aes_ecb
from .models import MediaContent

if TYPE_CHECKING:
    from .client import IlinkBotClient

try:
    import pysilk

    _HAS_SILK = True
except ImportError:
    _HAS_SILK = False

_EXT_MAP = {"image": ".jpg", "voice": ".silk", "video": ".mp4", "file": ""}


def detect_ext(data: bytes, media_type: str) -> str:
    if not data:
        return _EXT_MAP.get(media_type, "")
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"#!AMR":
        return ".amr"
    if data[:10] == b"#!SILK_V3 ":
        return ".silk"
    return _EXT_MAP.get(media_type, "")


def download_media(media: MediaContent, bot: IlinkBotClient | None = None) -> bytes:
    headers = bot._headers() if bot else {}
    resp = requests.get(media.url, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.content
    if media.aes_key:
        data = decrypt_aes_ecb(data, decode_aes_key(media.aes_key))
    return data


def media_filename(media: MediaContent, data: bytes) -> str:
    ext = detect_ext(data, media.type)
    return media.file_name or f"{media.type}_{media.md5 or 'unknown'}{ext}"


def silk_to_wav(data: bytes, sample_rate: int = 24000) -> bytes:
    out = io.BytesIO()
    pysilk.decode(io.BytesIO(data), out, sample_rate=sample_rate)
    pcm = out.getvalue()
    return _pcm_to_wav(pcm, sample_rate=sample_rate)


def wav_to_silk(data: bytes, sample_rate: int = 24000) -> bytes:
    """将 WAV 或 raw PCM 音频转换为 SILK 格式(微信语音)。

    Args:
        data: WAV 文件字节(带 RIFF header)或 raw PCM16 数据。
        sample_rate: 采样率,WAV 格式时自动从 header 读取。

    Returns:
        SILK 编码的音频字节。
    """
    if not _HAS_SILK:
        raise RuntimeError("pysilk 未安装,无法编码 SILK 格式")
    if data[:4] == b"RIFF":
        import wave

        with wave.open(io.BytesIO(data)) as wf:
            sample_rate = wf.getframerate()
            data = wf.readframes(wf.getnframes())
    inp = io.BytesIO(data)
    out = io.BytesIO()
    pysilk.encode(inp, out, sample_rate=sample_rate, bit_rate=64000, tencent=True)
    return out.getvalue()


def _pcm_to_wav(
    pcm: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    bits: int = 16,
) -> bytes:
    data_size = len(pcm)
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(
        struct.pack(
            "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits
        )
    )
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm)
    return buf.getvalue()
