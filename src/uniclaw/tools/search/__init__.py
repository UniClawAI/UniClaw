"""平台搜索工具包。

对外暴露与旧 search.py 相同的接口(webSearch, get_tools 等),
同时将每个平台拆分为独立模块。
"""

from .base import (
    DEFAULT_UA,
    NO_RESULTS,
    PLATFORM_ERROR,
    cache_key,
    client_kwargs,
    get_proxy,
    http_get,
    http_get_json,
    safe_search,
    search_cache,
    search_with_timeout,
)
from .tools import (
    PLATFORM_SEARCHERS,
    get_all_tools,
    get_tools,
    webSearch,
)

# 向下兼容: 旧下划线命名仍可用
_cache_key = cache_key
_get_proxy = get_proxy
_http_get = http_get
_http_get_json = http_get_json
_search_cache = search_cache
_search_with_timeout = search_with_timeout
_PLATFORM_SEARCHERS = PLATFORM_SEARCHERS

__all__ = [
    "webSearch",
    "get_tools",
    "get_all_tools",
    "PLATFORM_SEARCHERS",
    "NO_RESULTS",
    "get_proxy",
    "cache_key",
    "search_with_timeout",
    "search_cache",
    "http_get",
    "http_get_json",
    "safe_search",
    "DEFAULT_UA",
    "client_kwargs",
    "PLATFORM_ERROR",
    # 向下兼容
    "_PLATFORM_SEARCHERS",
    "_get_proxy",
    "_cache_key",
    "_search_with_timeout",
    "_search_cache",
    "_http_get",
    "_http_get_json",
]
