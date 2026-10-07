"""音频工具函数。"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import tempfile
from pathlib import Path

# 裸 PCM16 的默认采样参数(TTS 流式返回 format=pcm16 时无头部信息)
_RAW_PCM16_PARAMS = (24000, 1, 16)


def pcm_to_wav(
    pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16
) -> bytes:
    """将 PCM 原始数据转为 WAV 格式。

    Args:
        pcm: PCM 原始数据。
        sample_rate: 采样率,默认 24000。
        channels: 声道数,默认 1(单声道)。
        bits: 每个采样的位数,默认 16。

    Returns:
        WAV 格式的数据。
    """
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm


def extract_pcm(data: bytes) -> tuple[bytes, int, int, int]:
    """从 WAV 或裸 PCM16 中提取 PCM 数据与格式参数。

    按 RIFF/WAVE 魔数识别 WAV,遍历 chunk 取 fmt/data;
    非 WAV 按裸 PCM16 处理(采样率 24000Hz/单声道/16bit,TTS 流式默认)。

    Args:
        data: 音频字节(WAV 或裸 PCM16)。

    Returns:
        (pcm_bytes, sample_rate, channels, bits) 元组。
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        sr, ch, bits = _RAW_PCM16_PARAMS
        return data, sr, ch, bits

    sample_rate, channels, bits = _RAW_PCM16_PARAMS
    pcm = b""
    pos = 12
    n = len(data)
    while pos + 8 <= n:
        cid = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        body = data[pos + 8 : pos + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            channels = int.from_bytes(body[2:4], "little")
            sample_rate = int.from_bytes(body[4:8], "little")
            bits = int.from_bytes(body[14:16], "little")
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)  # chunk 按偶数字节对齐
    return pcm, sample_rate, channels, bits


def ensure_wav(data: bytes) -> bytes:
    """确保音频字节是 WAV:裸 PCM16 包上 WAV 头,已是 WAV 则原样返回。"""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return data
    sr, ch, bits = _RAW_PCM16_PARAMS
    return pcm_to_wav(data, sample_rate=sr, channels=ch, bits=bits)


def concat_audio_segments(raw_list: list[bytes]) -> bytes:
    """将多段音频按序拼接为单个 WAV 字节。

    各段先解出 PCM(兼容 WAV 与裸 PCM16),拼接后再封装为 WAV。
    段间采样率/声道/位深不一致时抛 ValueError(同一 TTS 后端不应出现,
    出现即为外部服务行为异常,由调用方转为 TOOL_ERROR)。

    Args:
        raw_list: 各段音频字节,按播放顺序。

    Returns:
        WAV 格式字节。

    Raises:
        ValueError: 段列表为空,或段间音频格式参数不一致。
    """
    if not raw_list:
        raise ValueError("没有可拼接的音频段")

    pcms: list[bytes] = []
    ref: tuple[int, int, int] | None = None
    for i, raw in enumerate(raw_list):
        pcm, sr, ch, bits = extract_pcm(raw)
        params = (sr, ch, bits)
        if ref is None:
            ref = params
        elif params != ref:
            raise ValueError(
                f"音频段格式不一致: 段{i + 1} 为 {sr}Hz/{ch}ch/{bits}bit,"
                f"与首段 {ref[0]}Hz/{ref[1]}ch/{ref[2]}bit 不同"
            )
        pcms.append(pcm)

    sr, ch, bits = ref
    return pcm_to_wav(b"".join(pcms), sample_rate=sr, channels=ch, bits=bits)


def concat_audio_b64(parts: list[str]) -> str:
    """将多段 base64 音频按序拼接为单个 base64 WAV。

    段数为 1 时先规范化为 WAV(裸 PCM16 包头)再返回,保证保存路径
    产出的 .wav 文件带头可播放,也让 save_audio 的魔数扩展名纠正生效。

    Args:
        parts: 各段 base64 音频字符串,按播放顺序。

    Returns:
        base64 编码的 WAV 字符串。

    Raises:
        ValueError: 段列表为空,或段间音频格式参数不一致。
    """
    if not parts:
        raise ValueError("没有可拼接的音频段")
    raw_list = [base64.b64decode(p) for p in parts]
    if len(raw_list) == 1:
        return base64.b64encode(ensure_wav(raw_list[0])).decode("ascii")
    return base64.b64encode(concat_audio_segments(raw_list)).decode("ascii")


async def _convert_to_mp3(src: Path) -> Path:
    """使用 ffmpeg 将音频文件转换为 mp3,返回临时文件路径。"""
    fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    tmp = Path(tmp_path)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-codec:a",
        "libmp3lame",
        "-qscale:a",
        "2",
        str(tmp),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg 转换失败 (exit {proc.returncode}): {src}")
    return tmp


def voice_file_to_data_uri(file_path: str) -> str:
    """将音频文件路径转为 data URI(仅支持 mp3/wav)。

    Args:
        file_path: 音频文件路径。

    Returns:
        data:audio/mp3;base64,... 格式的字符串。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 不支持的音频格式。
    """
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"音频文件不存在: {file_path}")
    ext = p.suffix.lower()
    if ext not in {".mp3", ".wav"}:
        raise ValueError(f"不支持的音频格式: {ext},仅支持 mp3/wav")
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:audio/{ext.lstrip('.')};base64,{b64}"


async def asr(
    file_path: str,
    config,
    *,
    asr_options: dict | None = None,
) -> str:
    """语音识别:将音频文件转为文字。

    使用配置中的 asr_model 调用 LLM 进行语音识别。
    非 mp3/wav 格式会自动通过 ffmpeg 转换为 mp3。

    Args:
        file_path: 音频文件路径。
        config: AppConfig 实例。
        asr_options: ASR 选项,如 {"language": "zh"}。默认 {"language": "zh"}。

    Returns:
        识别出的文字。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: asr_model 未配置。
        RuntimeError: ffmpeg 转换失败。
    """
    from uniclaw.provider.openai_provider import achat

    asr_model = config.asr_model if config else ""
    if not asr_model:
        raise ValueError("asr_model 未配置,请通过 /model 命令设置 ASR 模型")

    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"音频文件不存在: {file_path}")

    ext = p.suffix.lower()
    if ext not in {".mp3", ".wav"}:
        converted_tmp = await _convert_to_mp3(p)
        try:
            data_uri = voice_file_to_data_uri(str(converted_tmp))
        finally:
            converted_tmp.unlink(missing_ok=True)
    else:
        data_uri = voice_file_to_data_uri(file_path)

    messages = [
        {
            "role": "user",
            "content": [{"type": "input_audio", "input_audio": {"data": data_uri}}],
        }
    ]
    if asr_options is None:
        asr_options = {"language": "zh"}

    ai_msg = await achat(
        messages,
        model_name=asr_model,
        asr_options=asr_options,
        max_tokens=None,
        config=config,
    )
    return ai_msg.content or ""
