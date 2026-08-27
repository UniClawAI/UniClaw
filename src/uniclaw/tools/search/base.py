"""搜索平台共享基础: 代理、缓存、HTTP 请求、超时包装、安全搜索装饰器。"""

import asyncio
import functools

import httpx
from cachetools import TTLCache

from uniclaw.config import AppConfig

# 单个平台搜索失败的前缀(区别于整个工具调用失败的 TOOL_ERROR)
PLATFORM_ERROR = "[PLATFORM_ERROR]"

# 平台"无搜索结果"的统一标记(区别于 PLATFORM_ERROR 的异常失败),
# 供调用方(web.py 等)判定空结果并触发下一级回退
NO_RESULTS = "无搜索结果"

# 搜索结果缓存: 128 条, 10 分钟过期
search_cache = TTLCache(maxsize=128, ttl=600)

# 通用浏览器 User-Agent(各平台使用)
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def get_proxy(config: AppConfig | None) -> str | None:
    """从 config 中提取有效的代理地址, 无效则返回 None。"""
    if config is None:
        return None
    proxy = config.proxy_url
    return proxy if isinstance(proxy, str) and proxy.startswith("http") else None


def cache_key(query: str, platform: str, **kwargs) -> str:
    """构建缓存 key。"""
    parts = [query, platform]
    for k, v in sorted(kwargs.items()):
        if v:
            parts.append(f"{k}={v}")
    return "|".join(parts)


def client_kwargs(config: AppConfig | None, use_proxy: bool = True) -> dict:
    """构造 httpx.AsyncClient 的关键字参数(含代理, 不含 timeout)。

    use_proxy=False 时强制直连, 适用于国内可直接访问的平台
    (如 B站), 避免走了代理反而更慢或连不上。
    """
    if not use_proxy:
        return {}
    proxy = get_proxy(config)
    return {"proxy": proxy} if proxy else {}


async def http_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    config: AppConfig | None = None,
    timeout: int = 15,
    follow_redirects: bool = True,
    use_proxy: bool = True,
) -> httpx.Response:
    """带代理与超时的 GET, 返回响应(已 raise_for_status)。

    use_proxy=False 时强制直连(国内可直接访问的平台)。
    """
    async with httpx.AsyncClient(
        **client_kwargs(config, use_proxy),
        timeout=timeout,
        follow_redirects=follow_redirects,
    ) as client:
        r = await client.get(url, params=params, headers=headers)
    r.raise_for_status()
    return r


async def http_get_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    config: AppConfig | None = None,
    timeout: int = 15,
    use_proxy: bool = True,
) -> dict:
    """带代理与超时的 GET 并解析 JSON。"""
    r = await http_get(
        url,
        params=params,
        headers=headers,
        config=config,
        timeout=timeout,
        use_proxy=use_proxy,
    )
    return r.json()


def safe_search(func):
    """将平台搜索函数包装为异常安全: 任何失败都返回 PLATFORM_ERROR 信息, 而非抛异常或假装无结果。"""

    @functools.wraps(func)
    async def wrapper(
        query: str,
        limit: int,
        sort: str,
        search_type: str,
        config: AppConfig | None,
        time_range: str = "",
    ) -> str:
        try:
            return await func(query, limit, sort, search_type, config, time_range)
        except asyncio.TimeoutError:
            return (
                f"{PLATFORM_ERROR}: 搜索超时, 可能被网络限制, 请检查代理设置(proxy_url)"
            )
        except httpx.ConnectError:
            return f"{PLATFORM_ERROR}: 连接失败, 无法访问该平台。请在 settings.json 中配置 proxy_url 代理"
        except httpx.ConnectTimeout:
            return (
                f"{PLATFORM_ERROR}: 连接超时, 可能被网络限制, 请检查代理设置(proxy_url)"
            )
        except httpx.HTTPStatusError as e:
            return f"{PLATFORM_ERROR}: HTTP {e.response.status_code}"
        except Exception as e:
            return f"{PLATFORM_ERROR}: {e}"

    return wrapper


async def search_with_timeout(searcher, *args, timeout: int = 15, **kwargs) -> str:
    """带超时的搜索包装(在 safe_search 之外的兜底, 主要处理整体超时)。"""
    try:
        return await asyncio.wait_for(searcher(*args, **kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        return f"{PLATFORM_ERROR}: 搜索超时({timeout}秒),可能被网络限制,请检查代理设置(proxy_url)"
    except httpx.ConnectError:
        return f"{PLATFORM_ERROR}: 连接超时,无法访问该平台。请在 settings.json 中配置 proxy_url 代理"
    except httpx.ConnectTimeout:
        return f"{PLATFORM_ERROR}: 连接超时({timeout}秒),可能被网络限制,请检查代理设置(proxy_url)"
    except httpx.HTTPStatusError as e:
        return f"{PLATFORM_ERROR}: HTTP {e.response.status_code}"
    except Exception as e:
        return f"{PLATFORM_ERROR}: {e}"
