"""下载工具聚合模块 -- 统一导出 HTTP 与 M3U8 下载的全部工具。

作为 download 包的对外入口,合并 http_tools 与 m3u8_tools 两个子模块的
get_tools/get_all_tools 及具体工具对象,调用方无需关心具体下载类型。

同时保留两个子模块的独立别名,供需要单独获取 HTTP 或 M3U8 工具时使用。
"""

from .http_tools import (
    get_tools as get_http_tools,
    get_all_tools as get_http_all_tools,
    http_download,
    http_download_status,
    http_download_remove,
)
from .m3u8_tools import (
    get_tools as get_m3u8_tools,
    get_all_tools as get_m3u8_all_tools,
    m3u8_download,
    m3u8_download_status,
    m3u8_download_remove,
)

__all__ = [
    "get_tools",
    "get_all_tools",
    "get_http_tools",
    "get_http_all_tools",
    "get_m3u8_tools",
    "get_m3u8_all_tools",
    "http_download",
    "http_download_status",
    "http_download_remove",
    "m3u8_download",
    "m3u8_download_status",
    "m3u8_download_remove",
]


def get_tools() -> list:
    """获取运行时工具列表(HTTP + M3U8)。

    Returns:
        list: 合并后的下载工具列表。
    """
    return [*get_http_tools(), *get_m3u8_tools()]


def get_all_tools() -> list:
    """获取所有工具列表(HTTP + M3U8)。

    Returns:
        list: 合并后的全部下载工具列表。
    """
    return get_tools()
