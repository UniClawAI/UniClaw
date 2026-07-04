"""音频工具函数。"""

from __future__ import annotations

import base64
from pathlib import Path


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
        raise ValueError(f"不支持的音频格式: {ext}，仅支持 mp3/wav")
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:audio/{ext.lstrip('.')};base64,{b64}"
