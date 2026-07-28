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
    input_tokens: int, output_tokens: int, price: dict
) -> float:
    """用价格字典计算费用(美元)。"""
    return (input_tokens * price["input"]) + (output_tokens * price["output"])


class UsageField(StrEnum):
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    API_CALLS = "api_calls"
    TOOL_CALLS = "tool_calls"


# 数据结构的顶层键
TOTAL = "total"
DAILY = "daily"

# 统计字段列表(用于生成空记录)
_STAT_FIELDS = [
    UsageField.INPUT_TOKENS,
    UsageField.OUTPUT_TOKENS,
    UsageField.API_CALLS,
    UsageField.TOOL_CALLS,
]


_lock = threading.Lock()


def _stats_path() -> Path:
    return get_app_dir(Scope.USER) / "usage.json"


def _load() -> dict:
    p = _stats_path()
    if not p.exists():
        return {TOTAL: _new_record(), DAILY: {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {TOTAL: _new_record(), DAILY: {}}


def _save(data: dict):
    p = _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _new_record() -> dict:
    return {f.value: 0 for f in _STAT_FIELDS}


async def record_usage(
    input_tokens: int = 0, output_tokens: int = 0, tool_calls: int = 0, model: str = ""
):
    """记录一次 API 调用的用量和费用"""
    if input_tokens == 0 and output_tokens == 0 and tool_calls == 0:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    model_key = model or "unknown"

    # 查询价格并计算本次费用
    price = await _get_model_price(model_key)
    cost = _estimate_cost_from_price(input_tokens, output_tokens, price)

    with _lock:
        data = _load()
        # 累计总量
        data[TOTAL][UsageField.INPUT_TOKENS] += input_tokens
        data[TOTAL][UsageField.OUTPUT_TOKENS] += output_tokens
        data[TOTAL][UsageField.API_CALLS] += 1
        data[TOTAL][UsageField.TOOL_CALLS] += tool_calls
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
        m["cost"] = m.get("cost", 0.0) + cost
        # 每日统计
        if today not in data[DAILY]:
            data[DAILY][today] = {**_new_record(), "cost": 0.0}
        day = data[DAILY][today]
        day[UsageField.INPUT_TOKENS] += input_tokens
        day[UsageField.OUTPUT_TOKENS] += output_tokens
        day[UsageField.API_CALLS] += 1
        day[UsageField.TOOL_CALLS] += tool_calls
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

    if daily:
        lines.append("")
        lines.append("  最近 7 天:")
        for date in sorted(daily.keys(), reverse=True)[:7]:
            day = daily[date]
            in_t = day[UsageField.INPUT_TOKENS]
            out_t = day[UsageField.OUTPUT_TOKENS]
            lines.append(
                f"    {date}: {in_t:,}+{out_t:,}={in_t + out_t:,} tokens, "
                f"{day[UsageField.API_CALLS]} 次调用"
            )

    return "\n".join(lines)
