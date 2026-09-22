"""LLM 调用错误分类 — 按异常类型分类,为 fallback 重试策略提供依据。

借鉴 CheetahClaws 的 error_classifier 思路,将 provider 抛出的异常分为:
RATE_LIMIT / CONTEXT_OVERFLOW / AUTH / SERVER_ERROR / TIMEOUT / UNKNOWN,
每类错误采用不同的重试次数与退避策略。

注意:分类函数不 import openai/anthropic 顶层模块(用类名匹配),
保证无 SDK 环境(如纯测试环境)下也能正常工作。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCategory(StrEnum):
    """错误分类枚举。

    Attributes:
        RATE_LIMIT: 速率限制 (429),等待后可重试
        CONTEXT_OVERFLOW: 上下文溢出,压缩后可重试
        AUTH: 认证/权限错误 (401/403),重试无意义
        SERVER_ERROR: 服务器错误 (5xx),短暂等待后可重试
        TIMEOUT: 请求超时,可重试
        UNKNOWN: 未分类错误,不重试
    """

    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    AUTH = "auth"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


# ── 各分类的判定关键词(消息统一小写后匹配) ──────────────────────
_CONTEXT_OVERFLOW_KEYWORDS = (
    "context_length_exceeded",
    "maximum context",
    "context window",
    "too many tokens",
    "token limit",
    "prompt is too long",
)


def _status_code(e: Exception) -> int | None:
    """从异常对象中提取 HTTP 状态码(兼容 status_code/status 两种属性)。"""
    status = getattr(e, "status_code", None)
    if status is None:
        status = getattr(e, "status", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _class_name(e: Exception) -> str:
    """异常类名(小写,用于 SDK 类型匹配,避免 import SDK 模块)。"""
    return type(e).__name__.lower()


def _message(e: Exception) -> str:
    """异常消息(小写,用于关键词匹配)。"""
    return str(e).lower()


def classify_error(e: Exception) -> ErrorCategory:
    """将异常分类到 ErrorCategory。

    判定顺序:先看状态码,再看类名,最后看消息关键词。

    Args:
        e: provider 抛出的异常

    Returns:
        ErrorCategory: 对应的错误分类,无法识别时返回 UNKNOWN。
    """
    status = _status_code(e)
    name = _class_name(e)
    msg = _message(e)

    # 状态码优先判定
    if status is not None:
        if status == 429:
            return ErrorCategory.RATE_LIMIT
        if status in (401, 403):
            return ErrorCategory.AUTH
        if status in (500, 502, 503, 529):
            return ErrorCategory.SERVER_ERROR
        if status == 408:
            return ErrorCategory.TIMEOUT
        if status in (400, 413):
            # 400/413 需结合消息关键词确认是上下文溢出
            if any(kw in msg for kw in _CONTEXT_OVERFLOW_KEYWORDS):
                return ErrorCategory.CONTEXT_OVERFLOW
            return ErrorCategory.UNKNOWN

    # 类名匹配(openai/anthropic SDK 异常)
    if "ratelimiterror" in name:
        return ErrorCategory.RATE_LIMIT
    if "timeout" in name or "requesttimeout" in name:
        return ErrorCategory.TIMEOUT
    if "authenticationerror" in name or "permissiondenied" in name or "apikey" in name:
        return ErrorCategory.AUTH
    if "internalservererror" in name or "apiconnectionerror" in name:
        # APIConnectionError 表示连接失败(网络/服务器侧),归类为 SERVER_ERROR
        return ErrorCategory.SERVER_ERROR

    return ErrorCategory.UNKNOWN


# ── 网络/厂商错误识别(用于日志降噪) ────────────────────────────
# 常见网络传输层与模型 SDK 异常的模块根名(按 __module__ 判断,避免 import SDK)
_EXTERNAL_ERROR_MODULES = frozenset(
    {
        "openai",
        "anthropic",
        "httpx",
        "httpcore",
        "aiohttp",
        "urllib3",
        "requests",
        "socket",
        "ssl",
        "http",
        "urllib",
    }
)

# 类名关键词兜底(小写子串匹配)
_EXTERNAL_ERROR_KEYWORDS = (
    "apierror",
    "apistatus",
    "apiconnection",
    "httperror",
    "httpstatus",
    "statuserror",
    "badrequest",
    "ratelimit",
    "connection",
    "connecterror",
    "timeout",
    "network",
    "socket",
    "sslerror",
    "certificate",
    "gaierror",
    "protocolerror",
)


def is_network_or_provider_error(e: Exception) -> bool:
    """判断是否为网络错误或模型厂商 API 错误。

    这类错误属于预期内的外部故障(断网、限流、厂商 5xx 等),调用方通常只需
    re-raise 交给 fallback/UI 提示,不必再记录完整 traceback 刷日志。

    判定顺序:已知错误分类 -> 内置网络异常 -> 异常类所在模块 -> 类名关键词。

    Args:
        e: 待判断的异常。

    Returns:
        bool: 是网络或模型厂商错误返回 True,否则返回 False。
    """
    if classify_error(e) is not ErrorCategory.UNKNOWN:
        return True
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    module_root = (type(e).__module__ or "").partition(".")[0]
    if module_root in _EXTERNAL_ERROR_MODULES:
        return True
    name = _class_name(e)
    return any(kw in name for kw in _EXTERNAL_ERROR_KEYWORDS)


# ── 各分类的重试参数 ──────────────────────────────────────────────
# 每类最大重试次数(0 = 不重试,直接回退下一模型)
_MAX_RETRIES: dict[ErrorCategory, int] = {
    ErrorCategory.RATE_LIMIT: 3,
    ErrorCategory.CONTEXT_OVERFLOW: 1,
    ErrorCategory.AUTH: 0,
    ErrorCategory.SERVER_ERROR: 2,
    ErrorCategory.TIMEOUT: 1,
    ErrorCategory.UNKNOWN: 0,
}

# 可重试分类集合
_RETRYABLE = frozenset(
    {
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.CONTEXT_OVERFLOW,
        ErrorCategory.SERVER_ERROR,
        ErrorCategory.TIMEOUT,
    }
)


def is_retryable(cat: ErrorCategory) -> bool:
    """该分类是否值得重试。"""
    return cat in _RETRYABLE


def get_max_retries(cat: ErrorCategory) -> int:
    """返回该分类的最大重试次数。

    Args:
        cat: 错误分类

    Returns:
        int: 最大重试次数,不可重试分类返回 0。
    """
    return _MAX_RETRIES.get(cat, 0)


def get_backoff_delay(cat: ErrorCategory, attempt: int) -> float:
    """返回第 attempt 次重试前的退避延迟(秒)。

    指数退避策略:
    - RATE_LIMIT: 2^(attempt-1) (1s, 2s, 4s)
    - SERVER_ERROR: attempt * 2 (2s, 4s)
    - 其余可重试分类: 0s (立即重试)
    - 不可重试分类: 0s

    Args:
        cat: 错误分类
        attempt: 第几次重试(从 1 开始)

    Returns:
        float: 退避延迟秒数。
    """
    if not is_retryable(cat):
        return 0.0
    if cat == ErrorCategory.RATE_LIMIT:
        return float(2 ** max(attempt - 1, 0))
    if cat == ErrorCategory.SERVER_ERROR:
        return float(2 * max(attempt, 1))
    return 0.0
