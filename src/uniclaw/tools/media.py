import asyncio
import base64
import logging
import mimetypes
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

from uniclaw.config import AppConfig
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR
from uniclaw.tools.base import tool

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".tiff",
    ".tif",
    ".svg",
    ".ico",
}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

SIZE_LIMITS = {
    "image": 20 * 1024 * 1024,
    "audio": 25 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
}

_ffmpeg_available: bool | None = None


def is_image_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in IMAGE_EXTENSIONS


def is_media_file(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in MEDIA_EXTENSIONS


def _detect_media_type(suffix: str) -> str | None:
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _check_ffmpeg() -> bool:
    global _ffmpeg_available
    if _ffmpeg_available is not None:
        return _ffmpeg_available
    _ffmpeg_available = shutil.which("ffmpeg") is not None
    return _ffmpeg_available


def _read_image(p: Path, suffix: str) -> list:
    size_kb = p.stat().st_size / 1024
    blocks = [
        {"type": "text", "text": f"{SYSTEM_PREFIX}[图片: {p.name}, {size_kb:.0f} KB]"}
    ]
    if suffix == ".svg":
        text = p.read_text(encoding="utf-8")
        blocks.append({"type": "text", "text": text})
    else:
        mime_type = mimetypes.guess_type(str(p))[0] or "image/png"
        data = p.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            }
        )
    return blocks


def _get_media_info(p: Path) -> dict:
    """用 ffprobe 提取媒体元数据。"""
    info = {}
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,bit_rate:stream=codec_name,sample_rate,channels",
                "-of",
                "json",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            import json

            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            media_stream = next(
                (s for s in streams if s.get("codec_name")),
                streams[0] if streams else {},
            )
            if fmt.get("duration"):
                info["时长"] = f"{float(fmt['duration']):.1f}s"
            if fmt.get("bit_rate"):
                info["比特率"] = f"{int(fmt['bit_rate']) // 1000} kbps"
            if media_stream.get("sample_rate"):
                info["采样率"] = f"{media_stream['sample_rate']} Hz"
            if media_stream.get("channels"):
                info["声道数"] = str(media_stream["channels"])
            if media_stream.get("codec_name"):
                info["编码"] = media_stream["codec_name"]
    except Exception as e:
        logger.warning("ffprobe 提取媒体信息失败: %s", e)
    return info


def _make_data_uri(p: Path, mime_type: str) -> str:
    """读取文件并构造 data URI。"""
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def _is_url(path: str) -> bool:
    return path.startswith("http://") or path.startswith("https://")


def _url_ext(url: str) -> str:
    """从 URL 中提取小写扩展名(去掉查询参数)。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return Path(parsed.path).suffix.lower()


def _read_url(url: str, fps: int = 2) -> list | str:
    """处理 URL 格式的媒体资源。"""
    suffix = _url_ext(url)
    media_type = _detect_media_type(suffix)
    if media_type is None:
        all_exts = sorted(IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)
        return f"{TOOL_ERROR}: 无法从 URL 识别媒体格式 '{suffix}',支持的格式: {', '.join(all_exts)}"

    if media_type == "image":
        return [
            {"type": "text", "text": f"{SYSTEM_PREFIX}[图片: {url}]"},
            {"type": "image_url", "image_url": {"url": url}},
        ]
    elif media_type == "audio":
        return [
            {"type": "text", "text": f"{SYSTEM_PREFIX}[音频: {url}]"},
            {"type": "input_audio", "input_audio": {"data": url}},
        ]
    else:
        return [
            {"type": "text", "text": f"{SYSTEM_PREFIX}[视频: {url}]"},
            {
                "type": "video_url",
                "video_url": {"url": url},
                "fps": fps,
                "media_resolution": "default",
            },
        ]


def _read_audio(p: Path) -> list:
    size_kb = p.stat().st_size / 1024
    # info = _get_media_info(p) if _check_ffmpeg() else {}
    # info_str = ", ".join(f"{k}: {v}" for k, v in info.items())
    # detail = f", {info_str}" if info_str else ""
    mime_type = mimetypes.guess_type(str(p))[0] or "audio/mpeg"
    data_uri = _make_data_uri(p, mime_type)
    return [
        {"type": "text", "text": f"{SYSTEM_PREFIX}[音频: {p.name}, {size_kb:.0f} KB]"},
        {
            "type": "input_audio",
            "input_audio": {"data": data_uri},
        },
    ]


def _read_video(p: Path, fps: int = 2) -> list:
    size_kb = p.stat().st_size / 1024
    # info = _get_media_info(p) if _check_ffmpeg() else {}
    # info_str = ", ".join(f"{k}: {v}" for k, v in info.items())
    # detail = f", {info_str}" if info_str else ""
    mime_type = mimetypes.guess_type(str(p))[0] or "video/mp4"
    data_uri = _make_data_uri(p, mime_type)
    return [
        {"type": "text", "text": f"{SYSTEM_PREFIX}[视频: {p.name}, {size_kb:.0f} KB]"},
        {
            "type": "video_url",
            "video_url": {"url": data_uri},
            "fps": fps,
            "media_resolution": "default",
        },
    ]


def _read_media_impl(file_path: str, fps: int = 2) -> list | str:
    if _is_url(file_path):
        return _read_url(file_path, fps)

    p = Path(file_path)
    if not p.exists():
        return f"{TOOL_ERROR}: 文件不存在: {file_path}"
    if p.is_dir():
        return f"{TOOL_ERROR}: {file_path} 是一个目录"

    suffix = p.suffix.lower()
    media_type = _detect_media_type(suffix)
    if media_type is None:
        all_exts = sorted(IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS)
        return (
            f"{TOOL_ERROR}: 不支持的格式 '{suffix}',支持的格式: {', '.join(all_exts)}"
        )

    size_limit = SIZE_LIMITS[media_type]
    size_bytes = p.stat().st_size
    if size_bytes > size_limit:
        size_mb = size_bytes / (1024 * 1024)
        limit_mb = size_limit / (1024 * 1024)
        return f"{TOOL_ERROR}: 文件过大 ({size_mb:.1f} MB),{media_type} 最大支持 {limit_mb:.0f} MB"

    try:
        if media_type == "image":
            return _read_image(p, suffix)
        elif media_type == "audio":
            return _read_audio(p)
        else:
            return _read_video(p, fps)
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


def _get_description_prompt(content: list[dict]) -> str:
    """根据多模态内容类型返回对应的描述提示词。"""
    for item in content:
        item_type = item.get("type", "")
        if item_type == "image_url":
            return "请详细描述这张图片的内容。包括:图片中的所有可见元素、文字内容、颜色搭配、布局构图、人物或物体的位置关系等。"
        elif item_type == "input_audio":
            return "请详细描述这段音频的内容。包括:语音内容(如有)、背景音乐、音效、说话人语气情感、时长等。"
        elif item_type == "video_url":
            return "请详细描述这段视频的内容。包括:画面场景、人物动作、文字字幕、语音对话、背景音乐、视频时长和节奏等。"
    return "请详细描述这个媒体文件的内容。"


@tool
async def ReadMedia(
    file_path: str, fps: int = 2, as_text: bool = False, config: AppConfig = None
) -> list | str:
    """
    读取媒体文件(图片、音频、视频)并返回多模态内容供分析。

    支持本地文件路径和网络URL两种输入:
    - 本地文件: /path/to/file.mp3
    - 网络URL: https://example.com/audio.mp3

    支持格式:
    - 图片: png, jpg, jpeg, gif, webp, bmp, tiff, tif, svg, ico
    - 音频: mp3, wav, m4a, ogg, flac, aac, wma
    - 视频: mp4, avi, mkv, mov, webm, flv

    Args:
        file_path: 媒体文件的本地路径或网络URL
        fps: 视频抽帧速率(帧/秒),默认2

    Returns:
        list: 多模态内容块列表(成功时),str: 文字描述或错误信息
    """
    result = await asyncio.to_thread(_read_media_impl, file_path, fps)

    # 如果不是要求转为文本,或者原始结果已经是错误信息,直接返回
    if not as_text or isinstance(result, str):
        return result

    # 使用多模态模型将多模态内容转换为文字描述
    if not config or not config.multimodal_model_name:
        return f"{TOOL_ERROR}: 未配置多模态模型(multimodal_model_name),无法转换为文字描述"

    try:
        from uniclaw.provider.fallback import achat

        # 从多模态内容类型获取对应的描述提示词
        prompt = _get_description_prompt(result)

        # 创建临时 session 用于调用多模态模型
        from uniclaw.tools.session.session import Session

        temp_session = Session()
        temp_session.add_user_message(
            content=[
                {"type": "text", "text": prompt},
                *result,
            ]
        )

        # multimodal_model_name 是列表,achat 支持列表回退
        response = await achat(
            system_prompt="你是一个媒体内容描述助手,请详细描述用户发送的媒体文件内容。",
            session=temp_session,
            model_name=config.multimodal_model_name,
            config=config,
        )
        return response.content or f"{TOOL_ERROR}: 多模态模型返回为空"
    except Exception as e:
        return f"{TOOL_ERROR}: 转换文字描述失败: {e}"


@tool
async def GenerateImage(
    prompt: str,
    save_path: str | None = None,
    size: str = "1024x768",
    config=None,
) -> list | str:
    """
    使用 AI 生成图片。可以保存到文件或返回多模态数据供分析。

    Args:
        prompt: 图片描述提示词
        save_path: 保存路径(如 "output.png"),为空时返回多模态数据供 AI 直接分析
        size: 图片尺寸,如 "2K", "1024x1024", "1024x768"

    Returns:
        save_path 非空时返回保存路径字符串;save_path 为空时返回多模态内容块列表(可直接用于视觉分析)
    """
    from uniclaw.provider.openai_provider import agenerate_image

    model_name = config.image_model

    try:
        results = await agenerate_image(
            prompt=prompt,
            model_name=model_name,
            size=size,
            config=config,
        )
    except Exception as e:
        return f"{TOOL_ERROR}: 图片生成失败: {e}"

    if not results:
        return f"{TOOL_ERROR}: 图片生成返回为空"

    result = results[0]
    is_url = result.startswith("http://") or result.startswith("https://")

    # 保存到文件
    if save_path:
        p = Path(save_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if is_url:
            import urllib.request

            urllib.request.urlretrieve(result, str(p))
        else:
            import base64 as b64mod

            p.write_bytes(b64mod.b64decode(result))
        return f"已保存到: {p.resolve()}"

    # 返回多模态数据
    image_url = result if is_url else f"data:image/png;base64,{result}"
    return [
        {"type": "text", "text": f"{SYSTEM_PREFIX}[AI 生成图片, prompt: {prompt}]"},
        {
            "type": "image_url",
            "image_url": {"url": image_url},
        },
    ]


def get_tools(config=None) -> list:
    tools = [ReadMedia]
    if config and config.image_model:
        tools.append(GenerateImage)
    return tools


def get_all_tools() -> list:
    return [ReadMedia, GenerateImage]
