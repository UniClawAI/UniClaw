"""用量统计模块 — 跟踪 token 消耗和 API 调用次数,持久化到磁盘。

价格信息通过 model_info 模块从 OpenRouter API 获取。
"""

import json
import logging
import threading
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uniclaw.context import get_app_dir, Scope

logger = logging.getLogger("usage")


async def _get_model_price(model: str) -> dict:
    """获取模型价格。返回 {"input": float, "output": float, "cache_read": float, "cache_write": float}。
    未找到时返回全零字典。
    价格单位: 美元/token。
    """
    try:
        from uniclaw.utils.model_info import get_model_info_provider

        provider = get_model_info_provider()
        info = await provider.get_model_info(model)
        if info and info.pricing:
            return {
                "input": info.pricing.get("prompt", 0.0),
                "output": info.pricing.get("completion", 0.0),
                "cache_read": info.pricing.get("input_cache_read", 0.0),
                "cache_write": info.pricing.get("input_cache_write", 0.0),
            }
    except Exception as e:
        logger.debug("获取模型价格失败: %s", e)

    return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0}


def _estimate_cost_from_price(
    input_tokens: int,
    output_tokens: int,
    price: dict,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_discount: float = 0.0,
) -> float:
    """用价格字典计算费用(美元)。

    计算逻辑:
    1. 基础费用 = input_tokens * input_price + output_tokens * output_price
    2. 如果有 cache_discount (来自 API 响应),直接使用
    3. 如果没有 cache_discount,但有缓存价格,手动计算:
       - 缓存读取节省 = cached_tokens * (input_price - cache_read_price)
       - 缓存写入额外 = cache_write_tokens * (cache_write_price - input_price)
       - 费用修正 = 缓存写入额外 - 缓存读取节省

    Args:
        input_tokens: 输入 token 数。
        output_tokens: 输出 token 数。
        price: 价格字典 {"input": float, "output": float, "cache_read": float, "cache_write": float}。
        cached_tokens: 缓存命中 token 数。
        cache_write_tokens: 缓存写入 token 数。
        cache_discount: 缓存折扣金额(美元,来自 API 响应,正数表示省钱)。
    """
    base = (input_tokens * price["input"]) + (output_tokens * price["output"])

    # 优先使用 API 返回的 cache_discount
    if cache_discount != 0:
        return base - cache_discount

    # 如果没有 cache_discount,但有缓存价格信息,手动计算
    cache_read_price = price.get("cache_read", 0)
    cache_write_price = price.get("cache_write", 0)

    if cached_tokens > 0 and cache_read_price > 0:
        # 缓存读取通常比输入价格低,节省的费用
        savings = cached_tokens * (price["input"] - cache_read_price)
        base -= savings

    if cache_write_tokens > 0 and cache_write_price > 0:
        # 缓存写入可能比输入价格高,额外费用
        extra = cache_write_tokens * (cache_write_price - price["input"])
        base += extra

    return base


class UsageField(StrEnum):
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    API_CALLS = "api_calls"
    TOOL_CALLS = "tool_calls"
    CACHED_TOKENS = "cached_tokens"
    CACHE_WRITE_TOKENS = "cache_write_tokens"
    CACHE_DISCOUNT = "cache_discount"


# 数据结构的顶层键
TOTAL = "total"
DAILY = "daily"

# 统计字段列表(用于生成空记录)
_STAT_FIELDS = [
    UsageField.INPUT_TOKENS,
    UsageField.OUTPUT_TOKENS,
    UsageField.API_CALLS,
    UsageField.TOOL_CALLS,
    UsageField.CACHED_TOKENS,
    UsageField.CACHE_WRITE_TOKENS,
    UsageField.CACHE_DISCOUNT,
]


_lock = threading.Lock()


def _stats_path() -> Path:
    return get_app_dir(Scope.USER) / "usage.json"


def _load() -> dict:
    p = _stats_path()
    if not p.exists():
        return {TOTAL: _new_record(), DAILY: {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {TOTAL: _new_record(), DAILY: {}}
    _migrate(data)
    return data


def _migrate(data: dict):
    """兼容旧版 usage.json:补齐后新增的统计字段(如缓存字段)。"""
    records = [data.setdefault(TOTAL, {})] + list(
        data.setdefault(DAILY, {}).values()
    ) + list(data.get("by_model", {}).values())
    for rec in records:
        for f in _STAT_FIELDS:
            rec.setdefault(f.value, 0)


def _save(data: dict):
    p = _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_record() -> dict:
    return {f.value: 0 for f in _STAT_FIELDS}


async def record_usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    tool_calls: int = 0,
    model: str = "",
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_discount: float = 0.0,
):
    """记录一次 API 调用的用量和费用。

    Args:
        input_tokens: 输入 token 数。
        output_tokens: 输出 token 数。
        tool_calls: 工具调用数。
        model: 模型名。
        cached_tokens: 缓存命中 token 数(OpenRouter/DeepSeek/Anthropic 可选)。
        cache_write_tokens: 缓存写入 token 数(同上)。
        cache_discount: 缓存折扣金额(美元,读为正写为负,非缓存提供商为 0)。
    """
    if (
        input_tokens == 0
        and output_tokens == 0
        and tool_calls == 0
        and cached_tokens == 0
        and cache_write_tokens == 0
        and cache_discount == 0
    ):
        return
    today = datetime.now().strftime("%Y-%m-%d")
    model_key = model or "unknown"

    # 查询价格并计算本次费用
    price = await _get_model_price(model_key)
    cost = _estimate_cost_from_price(
        input_tokens, output_tokens, price, cached_tokens, cache_write_tokens, cache_discount
    )

    with _lock:
        data = _load()
        # 累计总量
        data[TOTAL][UsageField.INPUT_TOKENS] += input_tokens
        data[TOTAL][UsageField.OUTPUT_TOKENS] += output_tokens
        data[TOTAL][UsageField.API_CALLS] += 1
        data[TOTAL][UsageField.TOOL_CALLS] += tool_calls
        data[TOTAL][UsageField.CACHED_TOKENS] += cached_tokens
        data[TOTAL][UsageField.CACHE_WRITE_TOKENS] += cache_write_tokens
        data[TOTAL][UsageField.CACHE_DISCOUNT] += cache_discount
        # 按模型统计
        if "by_model" not in data:
            data["by_model"] = {}
        if model_key not in data["by_model"]:
            data["by_model"][model_key] = {**_new_record(), "cost": 0.0}
        m = data["by_model"][model_key]
        m[UsageField.INPUT_TOKENS] += input_tokens
        m[UsageField.OUTPUT_TOKENS] += output_tokens
        m[UsageField.API_CALLS] += 1
        m[UsageField.TOOL_CALLS] += tool_calls
        m[UsageField.CACHED_TOKENS] += cached_tokens
        m[UsageField.CACHE_WRITE_TOKENS] += cache_write_tokens
        m[UsageField.CACHE_DISCOUNT] += cache_discount
        m["cost"] = m.get("cost", 0.0) + cost
        # 每日统计
        if today not in data[DAILY]:
            data[DAILY][today] = {**_new_record(), "cost": 0.0}
        day = data[DAILY][today]
        day[UsageField.INPUT_TOKENS] += input_tokens
        day[UsageField.OUTPUT_TOKENS] += output_tokens
        day[UsageField.API_CALLS] += 1
        day[UsageField.TOOL_CALLS] += tool_calls
        day[UsageField.CACHED_TOKENS] += cached_tokens
        day[UsageField.CACHE_WRITE_TOKENS] += cache_write_tokens
        day[UsageField.CACHE_DISCOUNT] += cache_discount
        day["cost"] = day.get("cost", 0.0) + cost
        _save(data)


def get_stats() -> dict:
    """获取用量统计"""
    return _load()


def format_stats(data: dict | None = None) -> str:
    """格式化用量统计为可读文本"""
    if data is None:
        data = _load()
    total = data.get(TOTAL, _new_record())
    daily = data.get(DAILY, {})

    lines = ["用量统计:"]
    lines.append(
        f"  总计: {total[UsageField.INPUT_TOKENS]:,} 输入 + {total[UsageField.OUTPUT_TOKENS]:,} 输出 tokens"
    )
    lines.append(
        f"  总计: {total[UsageField.API_CALLS]:,} 次 API 调用, {total[UsageField.TOOL_CALLS]:,} 次工具调用"
    )
    total_tokens = total[UsageField.INPUT_TOKENS] + total[UsageField.OUTPUT_TOKENS]
    lines.append(f"  总 tokens: {total_tokens:,}")

    # 缓存统计(OpenRouter/DeepSeek/Anthropic 等返回缓存字段时才展示)
    cached = total.get(UsageField.CACHED_TOKENS, 0)
    cache_write = total.get(UsageField.CACHE_WRITE_TOKENS, 0)
    cache_discount = total.get(UsageField.CACHE_DISCOUNT, 0)
    if cached or cache_write or cache_discount:
        cache_parts = [f"命中 {cached:,} tokens"]
        if cache_write:
            cache_parts.append(f"写入 {cache_write:,}")
        if cache_discount:
            cache_parts.append(f"折扣 ${cache_discount:.4f}")
        lines.append(f"  缓存: {', '.join(cache_parts)}")

    if daily:
        lines.append("")
        lines.append("  最近 7 天:")
        for date in sorted(daily.keys(), reverse=True)[:7]:
            day = daily[date]
            in_t = day[UsageField.INPUT_TOKENS]
            out_t = day[UsageField.OUTPUT_TOKENS]
            day_line = (
                f"    {date}: {in_t:,}+{out_t:,}={in_t + out_t:,} tokens, "
                f"{day[UsageField.API_CALLS]} 次调用"
            )
            day_cached = day.get(UsageField.CACHED_TOKENS, 0)
            if day_cached:
                day_line += f", 缓存命中 {day_cached:,}"
            lines.append(day_line)

    return "\n".join(lines)
