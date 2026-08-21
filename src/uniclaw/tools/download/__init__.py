"""下载工具模块 -- HTTP/HTTPS 下载与 M3U8/HLS 流下载,支持多协程并发和断点续传。"""

from .tools import (
    get_tools,
    get_all_tools,
    get_http_tools,
    get_http_all_tools,
    get_m3u8_tools,
    get_m3u8_all_tools,
)
from .manager import (
    DownloadManager,
    HttpDownloadManager,
    M3u8DownloadManager,
    get_download_manager,
    get_http_download_manager,
    get_m3u8_download_manager,
)

__all__ = [
    "get_tools",
    "get_all_tools",
    "get_http_tools",
    "get_http_all_tools",
    "get_m3u8_tools",
    "get_m3u8_all_tools",
    "DownloadManager",
    "HttpDownloadManager",
    "M3u8DownloadManager",
    "get_download_manager",
    "get_http_download_manager",
    "get_m3u8_download_manager",
]
