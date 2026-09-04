"""文件发送工具 — 将文件发送给用户。

WebUI 模式下:
- 文本文件: 返回 [show_doc:...] 标记,前端直接渲染 Markdown(内容不进入模型上下文)
- 其他文件: 返回 [file_download:...] 标记,前端显示下载图标
"""

import time
import uuid
from pathlib import Path

from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.tools.base import tool, ToolRuntime

# 临时文件下载映射: file_id → {"path": Path, "name": str, "expires_at": int}
_file_downloads: dict[str, dict] = {}

# 默认下载链接有效期(分钟)
DEFAULT_EXPIRE_MINUTES = 30

# 文本预览大小上限(超过则只提供下载)
MAX_PREVIEW_SIZE = 5 * 1024 * 1024


def _cleanup_expired():
    """清理过期的下载链接。"""
    now = int(time.time())
    expired = [k for k, v in _file_downloads.items() if v["expires_at"] < now]
    for k in expired:
        del _file_downloads[k]


def register_download(
    file_path: Path, file_name: str, expire_minutes: int = DEFAULT_EXPIRE_MINUTES
) -> tuple[str, int]:
    """注册一个文件下载,返回 (下载ID, 过期时间戳)。"""
    _cleanup_expired()
    file_id = uuid.uuid4().hex[:12]
    expires_at = int(time.time()) + expire_minutes * 60
    _file_downloads[file_id] = {
        "path": file_path,
        "name": file_name,
        "expires_at": expires_at,
    }
    return file_id, expires_at


def get_download(file_id: str) -> dict | None:
    """获取下载信息,不存在或已过期返回 None。"""
    _cleanup_expired()
    return _file_downloads.get(file_id)


def _is_text_file(p: Path) -> bool:
    """判断文件是否为可预览的文本文件(UTF-8 可解码且无 NUL 字节)。"""
    if p.stat().st_size > MAX_PREVIEW_SIZE:
        return False
    try:
        with p.open("rb") as f:
            head = f.read(MAX_PREVIEW_SIZE)
        if b"\x00" in head:
            return False
        head.decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


@tool
async def send_file(
    file_path: str,
    file_name: str = "",
    expire_minutes: int = DEFAULT_EXPIRE_MINUTES,
    tool_runtime: ToolRuntime = None,
) -> str:
    """发送文件给用户。

    WebUI 模式下: 文本文件直接在前端渲染 Markdown 显示,其他文件提供下载链接。
    WeChat 模式下直接发送文件,Console 模式下忽略。

    Args:
        file_path: 文件的绝对路径或相对于工作目录的路径
        file_name: 自定义文件名(可选,默认使用原始文件名)
        expire_minutes: 下载链接有效期(分钟),默认30分钟

    Returns:
        str: 发送结果消息
    """
    config = tool_runtime.config
    if config is None:
        return f"{TOOL_ERROR}: 无法获取配置"

    p = Path(file_path)
    if not p.is_absolute():
        root_dir = config.root_dir
        if root_dir is None:
            return f"{TOOL_ERROR}: 当前会话无工作目录,请使用绝对路径"
        p = root_dir / p

    if not p.exists():
        return f"{TOOL_ERROR}: 文件不存在: {file_path}"
    if p.is_dir():
        return f"{TOOL_ERROR}: 路径是一个目录,不是文件: {file_path}"

    # 确定文件名
    name = file_name if file_name else p.name

    # 根据当前模式直接发送
    if config.is_webui:
        # WebUI: 生成临时下载 ID,返回包含 file_id 和过期时间的标记
        # 前端解析标记后: 文本文件直接渲染 Markdown,其他文件显示下载图标
        file_id, expires_at = register_download(p, name, expire_minutes)
        if _is_text_file(p):
            return f"[show_doc:{file_id}:{name}:{expires_at}]"
        return f"[file_download:{file_id}:{name}:{expires_at}]"
    else:
        # 微信模式: 通过 bot 直接发送
        bot = getattr(config, "wechat_ctx", None)
        if bot:
            try:
                bot.reply_file(p, file_name=name)
                return f"文件已发送: {name}"
            except Exception as e:
                return f"文件发送失败: {e}"
        else:
            # Console 模式或其他
            return f"文件发送不支持当前模式: {name}"


def get_tools(config=None) -> list:
    """获取运行工具列表,Console 模式下不注册 send_file。"""
    if config is not None and config.is_console:
        config.record_unavailable_tools(
            [send_file.name],
            "Console 模式下不提供文件发送",
        )
        return []
    if config:
        config.clear_unavailable_tools([send_file.name])
    return [send_file]


def get_all_tools() -> list:
    return get_tools()
