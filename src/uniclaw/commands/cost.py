"""费用统计命令"""

from uniclaw.config import AppConfig
from uniclaw.console.ui import info


async def cmd_cost(_args: str, config: AppConfig) -> bool:
    """显示详细的 token 消耗和费用统计(按模型计费,价格来自 OpenRouter)"""
    from uniclaw.utils.usage import get_stats, UsageField, TOTAL, DAILY

    data = get_stats()
    total = data.get(TOTAL, {})
    daily = data.get(DAILY, {})
    by_model = data.get("by_model", {})

    in_tokens = total.get(UsageField.INPUT_TOKENS, 0)
    out_tokens = total.get(UsageField.OUTPUT_TOKENS, 0)
    api_calls = total.get(UsageField.API_CALLS, 0)
    tool_calls = total.get(UsageField.TOOL_CALLS, 0)
    total_tokens = in_tokens + out_tokens

    lines = [
        f"\n费用统计\n",
        "─" * 50,
        f"  输入 tokens:   {in_tokens:>12,}",
        f"  输出 tokens:   {out_tokens:>12,}",
        f"  总 tokens:     {total_tokens:>12,}",
        f"  API 调用:      {api_calls:>12,}",
        f"  工具调用:      {tool_calls:>12,}",
    ]

    # 按模型计费
    if by_model:
        total_cost = 0.0
        lines.append(f"\n按模型计费:\n")

        # 动态计算列宽: 汇总行的数值可能比数据行宽
        w_in = max(len(f"{in_tokens:,}"),
                   max(len(f"{m.get(UsageField.INPUT_TOKENS, 0):,}") for m in by_model.values()))
        w_out = max(len(f"{out_tokens:,}"),
                    max(len(f"{m.get(UsageField.OUTPUT_TOKENS, 0):,}") for m in by_model.values()))
        w_call = max(len(f"{api_calls:,}"),
                     max(len(f"{m.get(UsageField.API_CALLS, 0):,}") for m in by_model.values()))
        w_cost = max(len(f"${total_cost:.4f}"),
                     max(len(f"${m.get('cost', 0.0):.4f}") for m in by_model.values()))

        lines.append(
            f"  {'模型':<28} {'输入':>{w_in}} {'输出':>{w_out}} "
            f"{'调用':>{w_call}} {'$费用':>{w_cost}}"
        )
        lines.append(
            f"  {'─'*28} {'─'*w_in} {'─'*w_out} {'─'*w_call} {'─'*w_cost}"
        )
        for model_name, m in sorted(by_model.items()):
            m_in = m.get(UsageField.INPUT_TOKENS, 0)
            m_out = m.get(UsageField.OUTPUT_TOKENS, 0)
            m_calls = m.get(UsageField.API_CALLS, 0)
            m_cost = m.get("cost", 0.0)
            total_cost += m_cost
            display_name = (
                model_name if len(model_name) <= 27 else model_name[:24] + "..."
            )
            lines.append(
                f"  {display_name:<28} {m_in:>{w_in},} {m_out:>{w_out},} "
                f"{m_calls:>{w_call},} {'$'+f'{m_cost:.4f}':>{w_cost}}"
            )
        lines.append(
            f"  {'─'*28} {'─'*w_in} {'─'*w_out} {'─'*w_call} {'─'*w_cost}"
        )
        lines.append(
            f"  {'合计':<28} {in_tokens:>{w_in},} {out_tokens:>{w_out},} "
            f"{api_calls:>{w_call},} {'$'+f'{total_cost:.4f}':>{w_cost}}"
        )
    else:
        # 兼容旧数据(无 by_model)
        lines.append(f"  (旧数据无费用记录,升级后的新调用将自动计费)")

    # 每日统计
    if daily:
        lines.append(f"\n最近 7 天:\n")

        # 动态计算列宽
        w_in = max(len(f"{d.get(UsageField.INPUT_TOKENS, 0):,}") for d in daily.values())
        w_out = max(len(f"{d.get(UsageField.OUTPUT_TOKENS, 0):,}") for d in daily.values())
        w_total = max(len(f"{d.get(UsageField.INPUT_TOKENS, 0) + d.get(UsageField.OUTPUT_TOKENS, 0):,}")
                      for d in daily.values())
        w_call = max(len(f"{d.get(UsageField.API_CALLS, 0):,}") for d in daily.values())
        w_cost = max(len(f"${d.get('cost', 0.0):.4f}") for d in daily.values())

        lines.append(
            f"  {'日期':<12} {'输入':>{w_in}} {'输出':>{w_out}} "
            f"{'合计':>{w_total}} {'调用':>{w_call}} {'$费用':>{w_cost}}"
        )
        lines.append(
            f"  {'─'*12} {'─'*w_in} {'─'*w_out} {'─'*w_total} {'─'*w_call} {'─'*w_cost}"
        )
        for date in sorted(daily.keys(), reverse=True)[:7]:
            day = daily[date]
            d_in = day.get(UsageField.INPUT_TOKENS, 0)
            d_out = day.get(UsageField.OUTPUT_TOKENS, 0)
            d_calls = day.get(UsageField.API_CALLS, 0)
            d_cost = day.get("cost", 0.0)
            lines.append(
                f"  {date:<12} {d_in:>{w_in},} {d_out:>{w_out},} "
                f"{d_in+d_out:>{w_total},} {d_calls:>{w_call},} {'$'+f'{d_cost:.4f}':>{w_cost}}"
            )
    lines.append("")
    await info("\n".join(lines), config)

    return True
