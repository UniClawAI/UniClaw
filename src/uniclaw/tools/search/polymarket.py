"""Polymarket 搜索实现(公开 Gamma API 全库搜索, 无需认证)。"""

import json

from uniclaw.config import AppConfig

from .base import DEFAULT_UA, http_get, safe_search

NAME = "polymarket"
LABEL = "Polymarket"

# 官方全库搜索接口: 按 q 匹配标题/描述, 覆盖全部活跃市场。
# 勿改回 /markets Top200 + 客户端子串过滤 —— 该方式搜不到热度榜外的市场
# (实测 'tesla' 在 24h 成交量 Top200 中命中 0 条, 而 public-search 可命中多个相关事件)
_ENDPOINT = "https://gamma-api.polymarket.com/public-search"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Polymarket 预测市场搜索 (公开 Gamma API, 无需认证)。"""
    r = await http_get(
        _ENDPOINT,
        params={"q": query, "limit_per_type": max(limit * 3, 10)},
        headers={"User-Agent": DEFAULT_UA},
        config=config,
        use_proxy=True,
    )
    data = r.json()
    events = data.get("events") or []

    # public-search 不支持服务端 active/closed 过滤, 客户端排序:
    # 活跃市场优先; 已收盘的仅在前者不足 limit 时补位展示 (标注已收盘)
    def _is_open(e: dict) -> bool:
        return bool(e.get("active")) and not e.get("closed")

    ordered = [e for e in events if _is_open(e)] + [
        e for e in events if not _is_open(e)
    ]

    lines = []
    count = 0
    for e in ordered:
        closed = not _is_open(e)
        title = (e.get("title") or "").strip()
        slug = e.get("slug") or ""
        if not title or not slug:
            continue
        volume = float(e.get("volume") or 0.0)
        liquidity = float(e.get("liquidity") or 0.0)
        odds_str = ""
        desc = (e.get("description") or "").strip()
        # 解析 YES/NO 概率: 优先未收盘市场; 已收盘 event 回退到首个市场
        # 描述过短(<20字符, 常为占位文本)时借用市场描述
        markets = [m for m in (e.get("markets") or []) if not m.get("closed")] or (
            e.get("markets") or []
        )
        for m in markets:
            odds_str = _extract_odds(m)
            if len(desc) < 20:
                m_desc = (m.get("description") or "").strip()
                if m_desc:
                    desc = m_desc
            if odds_str:
                break
        lines.append(f"**{title}{' (已收盘)' if closed else ''}**")
        lines.append(f"  💰 ${volume:,.0f} 成交量 | ${liquidity:,.0f} 流动性")
        if odds_str:
            lines.append(f"  概率: {odds_str}")
        if desc:
            lines.append(f"  {desc[:400]}")
        lines.append(f"  https://polymarket.com/event/{slug}\n")
        count += 1
        if count >= limit:
            break

    if not lines:
        return "Polymarket: 无搜索结果"
    return f"**Polymarket 搜索结果** ({count} 条):\n\n" + "\n".join(lines)


def _extract_odds(m: dict) -> str:
    """解析 outcomes/outcomePrices 得到 YES/NO 概率。"""
    outcomes = m.get("outcomes")
    outcome_prices = m.get("outcomePrices")
    if not outcomes or not outcome_prices:
        return ""
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if isinstance(outcome_prices, str):
        outcome_prices = json.loads(outcome_prices)
    if not isinstance(outcomes, list) or not isinstance(outcome_prices, list):
        return ""
    if len(outcomes) != len(outcome_prices):
        return ""
    parts = []
    for name, price in zip(outcomes, outcome_prices):
        try:
            parts.append(f"{name} {float(price) * 100:.0f}%")
        except (TypeError, ValueError):
            continue
    return " · ".join(parts)
