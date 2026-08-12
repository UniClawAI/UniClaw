"""用量统计模块 — 跟踪 token 消耗和 API 调用次数,持久化到磁盘"""

import asyncio
import json
import logging
import threading
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uniclaw.context import get_app_dir, Scope

logger = logging.getLogger("usage")

# ── 价格缓存,内存级(重启失效)─────────────────────────────
# 结构: {model_name: {"input": float, "output": float}}
# 全量缓存,一次 API 请求拿到所有模型价格
_price_cache: dict[str, dict] = {}
_PRICE_CACHE_DATE: str = ""


async def _fetch_all_prices() -> dict[str, dict]:
    """从 OpenRouter API 一次性获取所有模型价格。
    返回 {model_id: {"input": float, "output": float}}。
    价格单位: 美元/token。
    同时建立短名称索引(如 gpt-4o -> openai/gpt-4o)。
    """
    import httpx

    result: dict[str, dict] = {}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://openrouter.ai/api/v1/models",
                timeout=15,
            )
        resp.raise_for_status()
        for m in resp.json().get("data", []):
            mid = m.get("id", "")
            pricing = m.get("pricing", {})
            price = {
                "input": float(pricing.get("prompt", 0)),
                "output": float(pricing.get("completion", 0)),
            }
            # 完整 ID(如 openai/gpt-4o)
            result[mid] = price
            # 短名称索引(如 gpt-4o)
            if "/" in mid:
                short = mid.split("/", 1)[1]
                if short not in result:
                    result[short] = price
    except Exception as e:
        logger.debug("获取 OpenRouter 价格失败: %s", e)
    return result


async def _ensure_price_cache():
    """确保价格缓存有效。当天有效,重启失效。"""
    global _PRICE_CACHE_DATE, _price_cache
    today = datetime.now().strftime("%Y-%m-%d")
    if _PRICE_CACHE_DATE != today or not _price_cache:
        _price_cache = await _fetch_all_prices()
        _PRICE_CACHE_DATE = today


async def _get_model_price(model: str) -> dict:
    """获取模型价格。返回 {"input": float, "output": float}。
    未找到时返回 {"input": 0, "output": 0}。
    """
    await _ensure_price_cache()
    model_lower = (model or "").lower()
    # 精确匹配
    if model_lower in _price_cache:
        return _price_cache[model_lower]
    # 模糊匹配:遍历缓存查找后缀
    for mid, price in _price_cache.items():
        if mid.endswith("/" + model_lower):
            return price
    return {"input": 0, "output": 0}


def _estimate_cost_from_price(
    input_tokens: int, output_tokens: int, price: dict, cache_discount: float = 0.0
) -> float:
    """用价格字典计算费用(美元)。

    cache_discount 来自响应体:缓存命中为正(折扣,省钱),缓存写入为负(溢价)。
    基础费用按全价输入计算后减去此折扣,得到实际费用。
    无缓存字段的提供商传入 0,不影响计算。
    """
    base = (input_tokens * price["input"]) + (output_tokens * price["output"])
    return base - cache_discount


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
    cost = _estimate_cost_from_price(input_tokens, output_tokens, price, cache_discount)

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
