"""音频工具函数。"""

from __future__ import annotations

import asyncio
import base64
import os
import struct
import tempfile
from pathlib import Path


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

    asr_model = getattr(config, "asr_model", "")
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
