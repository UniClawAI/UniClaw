"""LLM 共享工具函数 — HTTP 客户端缓存、参数解析、消息转换等。"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from cachetools import TTLCache

from uniclaw.provider.types import Effort, Protocol, Usage

if TYPE_CHECKING:
    from uniclaw.config import AppConfig, ProviderProfile

REQUEST_TIMEOUT_SECONDS = 60 * 3


# ── URL 比较与检测 ─────────────────────────────────────────────


def compare_urls(url1, url2):
    p1 = urlparse(url1)
    p2 = urlparse(url2)
    return (
        p1.scheme == p2.scheme
        and p1.netloc.lower() == p2.netloc.lower()
        and p1.path.rstrip("/") == p2.path.rstrip("/")
    )


def is_google_api(openai_api_base):
    return compare_urls(
        openai_api_base,
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    )


def is_openrouter_api(openai_api_base):
    return compare_urls(
        openai_api_base,
        "https://openrouter.ai/api/v1/",
    )


def is_openrouter_base_url(base_url: str) -> bool:
    """判断是否为 OpenRouter 端点(按 host 匹配)。

    OpenRouter 同时提供 OpenAI 兼容端点(https://openrouter.ai/api/v1/)
    和 Anthropic 兼容端点(https://openrouter.ai/api/v1/anthropic 或
    https://openrouter.ai/api),按 host 判断可覆盖所有形态。
    """
    return urlparse(base_url).netloc.lower() == "openrouter.ai"


def is_anthropic_api(base_url: str) -> bool:
    """判断是否为 Anthropic 官方 API。"""
    return compare_urls(base_url, "https://api.anthropic.com")


# ── HTTP 客户端缓存 ────────────────────────────────────────────

_http_client_cache: dict[str, httpx.Client] = TTLCache(maxsize=8, ttl=3600)
_async_http_client_cache: dict[str, httpx.AsyncClient] = TTLCache(maxsize=8, ttl=3600)


def create_http_client(base_url: str, proxy_url: str = "") -> httpx.Client | None:
    """创建带代理的同步 HTTP 客户端(带缓存)。"""
    if "://127.0.0.1" in base_url:
        return None
    if isinstance(proxy_url, str) and proxy_url.startswith("http"):
        cache_key = f"{base_url}:{proxy_url}"
        if cache_key not in _http_client_cache:
            _http_client_cache[cache_key] = httpx.Client(proxy=proxy_url)
        return _http_client_cache[cache_key]
    return None


def create_async_http_client(
    base_url: str, proxy_url: str = ""
) -> httpx.AsyncClient | None:
    """创建带代理的异步 HTTP 客户端(带缓存)。"""
    if "://127.0.0.1" in base_url:
        return None
    if isinstance(proxy_url, str) and proxy_url.startswith("http"):
        cache_key = f"{base_url}:{proxy_url}"
        if cache_key not in _async_http_client_cache:
            _async_http_client_cache[cache_key] = httpx.AsyncClient(proxy=proxy_url)
        return _async_http_client_cache[cache_key]
    return None


# ── 参数解析 ───────────────────────────────────────────────────


def parse_model_ref(
    ref: str, providers: dict[str, ProviderProfile]
) -> tuple[str | None, str]:
    """解析 provider/model 格式的模型引用。

    第一个 '/' 前为 provider 名(需在 providers 中),后面全部为模型名。
    如果第一个 '/' 前不在 providers 中,或没有 '/',则整体作为模型名。

    Args:
        ref: 模型引用,如 "mimo/mimo-v2.5" 或 "openrouter/openai/gpt-4o"
        providers: provider 配置字典

    Returns:
        (provider_name, model_name) 或 (None, ref)
    """
    if "/" in ref:
        provider_name, _, model = ref.partition("/")
        if provider_name in providers:
            return (provider_name, model)
    return (None, ref)


def resolve_model_provider(
    config: AppConfig, model_ref: str
) -> tuple[ProviderProfile | None, str]:
    """解析模型引用并返回对应的 provider profile 和实际模型名。

    Args:
        config: AppConfig 实例
        model_ref: 模型引用字符串

    Returns:
        (ProviderProfile 或 None, 实际模型名)
    """
    if not config or not hasattr(config, "providers"):
        return (None, model_ref)

    provider_name, model_name = parse_model_ref(model_ref, config.providers)
    if provider_name:
        return (config.providers[provider_name], model_name)
    return (None, model_ref)


def resolve_params(config: AppConfig | None = None, **kwargs):
    """从 config 提取 LLM 参数作为默认值,kwargs 中的显式值优先。

    如果 model_name 带 provider 前缀(如 "openrouter/openai/gpt-4o"),会从对应的
    provider profile 读取 api_key/base_url,覆盖默认值。
    """
    if config is not None:
        defaults = {
            "model_name": config.model_name[0] if config.model_name else "",
            "multimodal_model_name": (
                config.multimodal_model_name[0]
                if config.multimodal_model_name
                else None
            ),
            # 全局 proxy_url 不应用到任何模型,模型只使用它自己配置的代理。
            # 这里保留空串键位,避免下游 p["proxy_url"] 读取时 KeyError;
            # provider profile 的自有代理在下方(profile.proxy_url)覆盖该值。
            "proxy_url": "",
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "top_p": config.top_p,
        }
        for key, val in defaults.items():
            # temperature/max_tokens/top_p 用 is None 判断,因为 0 也是合法值
            if key in ("temperature", "max_tokens", "top_p"):
                if kwargs.get(key) is None:
                    kwargs[key] = val
            else:
                if not kwargs.get(key):
                    kwargs[key] = val

    # 从 model_name 解析 provider profile
    model_name = kwargs.get("model_name", "")
    if model_name and config is not None:
        profile, actual_model = resolve_model_provider(config, model_name)
        if profile is not None:
            kwargs["model_name"] = actual_model
            if profile.protocol == "anthropic":
                kwargs["anthropic_api_key"] = profile.api_key
                kwargs["anthropic_base_url"] = profile.base_url
            else:
                kwargs["openai_api_key"] = profile.api_key
                kwargs["openai_api_base"] = profile.base_url
            if profile.proxy_url:
                kwargs["proxy_url"] = profile.proxy_url

    # 兜底:保证返回的 dict 始终含 proxy_url 键(config 为 None 或调用方未传时),
    # 无自有代理则保持空串(直连),避免下游 p["proxy_url"] 读取时 KeyError。
    kwargs.setdefault("proxy_url", "")

    return kwargs


def get_protocol(config: AppConfig | None = None, **kwargs) -> Protocol:
    """根据 model_name 解析使用哪个 LLM 协议。"""
    model_ref = kwargs.get("model_name") or ""
    if config is not None:
        if not model_ref and config.model_name:
            model_ref = config.model_name[0]
        if model_ref:
            profile, _ = resolve_model_provider(config, model_ref)
            if profile is not None:
                try:
                    return Protocol(profile.protocol)
                except ValueError:
                    pass
    return Protocol.OPENAI


# ── extra_body 构建 ────────────────────────────────────────────

# OpenRouter 粘性路由 session_id: 系统提示词前缀取前 N 字符做哈希。
# 相同系统提示词的不同会话 → 相同 session_id → 路由到同一上游 → 跨会话共享前缀缓存。
# 500 字当前落在行为准则段(静态区),不受日期/PID/root_dir 影响;若提示词结构变动,
# 需要重新校验该边界(见 context.py 的环境段位置)。
OPENROUTER_SESSION_PREFIX_CHARS = 500


def make_session_id(prefix_text: str) -> str:
    """根据系统提示词前缀生成稳定的 OpenRouter session_id。

    session_id 仅用于粘性路由(路由到同一上游,让前缀缓存保持温热),
    缓存命中仍要求完整 prompt 前缀逐字节一致。

    Args:
        prefix_text: 系统提示词的前缀文本。

    Returns:
        32 位十六进制哈希,满足 OpenRouter session_id ≤256 字符限制。
    """
    return hashlib.sha256(prefix_text.encode("utf-8")).hexdigest()[:32]


def build_extra_body(
    openai_api_base: str,
    enable_thinking: bool,
    thinking: bool,
    session_prefix: str = "",
) -> dict | None:
    """构建 thinking/reasoning 相关的 extra_body。

    Args:
        openai_api_base: OpenAI 兼容 API 的 base_url。
        enable_thinking: 是否启用思考模式。
        thinking: 当前是否处于思考状态。
        session_prefix: 系统提示词前缀,非空且走 OpenRouter 时用于生成
            粘性路由 session_id(相同前缀的会话路由到同一上游,共享缓存)。
    """
    if is_google_api(openai_api_base):
        return None
    thinking_type = "enabled" if thinking else "disabled"
    extra_body = {
        "enable_thinking": enable_thinking,
        "thinking": {"type": thinking_type},
    }
    if is_openrouter_api(openai_api_base):
        if not thinking:
            extra_body["reasoning"] = {"effort": Effort.NONE}
        # session_id 是 OpenRouter 专有字段,其他提供商会因未知参数报 400
        if session_prefix:
            extra_body["session_id"] = make_session_id(session_prefix)
    return extra_body


# ── usage 字段读取 ─────────────────────────────────────────────


def usage_field(obj, name: str, default=0):
    """从 usage 对象读取字段,兼容 dict 与 SDK 模型 extra 字段。

    OpenAI/Anthropic SDK 对未知字段(如 DeepSeek 的 prompt_cache_hit_tokens)
    会放入 model_extra 而非实例属性,直接 getattr 取不到,需要从 model_extra 兜底。

    Args:
        obj: usage 对象或 dict。
        name: 字段名。
        default: 字段缺失时的默认值。

    Returns:
        字段值,缺失时返回 default。
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    val = getattr(obj, name, None)
    if val is None:
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            val = extra.get(name)
    return val if val is not None else default


# ── 消息格式转换 ───────────────────────────────────────────────


def safe_parse_args(arguments: str) -> dict:
    """尝试解析工具参数 JSON,不完整时返回空 dict。"""
    if not arguments:
        return {}
    try:
        import json

        result = json.loads(arguments)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def is_multimodal_error(e: Exception) -> bool:
    """判断是否为多模态内容不支持的错误(HTTP 400/404 + 相关关键词)。"""
    status = getattr(e, "status_code", None) or getattr(e, "status", None)
    if status not in (400, 404):
        return False
    msg = str(e).lower()
    return any(
        kw in msg
        for kw in (
            "image",
            "audio",
            "video",
            "multimodal",
            "input_audio",
            "image_url",
            "video_url",
        )
    )


# ── 用量记录 ───────────────────────────────────────────────────


async def record_usage_async(model_name: str, usage: Usage | None):
    """记录 token 用量 (异步)。"""
    if not usage or (not usage.input_tokens and not usage.output_tokens):
        return
    from uniclaw.utils.usage import record_usage

    await record_usage(usage.input_tokens, usage.output_tokens, model=model_name)
