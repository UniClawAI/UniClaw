import json
import logging
from dataclasses import dataclass
from typing import Any

from uniclaw.agent import AgentTask
from uniclaw.compaction import AUTOCOMPACT_THRESHOLD, get_context_limit
from uniclaw.utils.tokens import count_tokens
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, warn
from uniclaw.context import build_system_prompt

logger = logging.getLogger(__name__)

# 自动压缩预留空间比例,与 compaction.AUTOCOMPACT_THRESHOLD 对应
AUTOCOMPACT_RATIO = 1 - AUTOCOMPACT_THRESHOLD
BAR_CELLS = 50


@dataclass
class ContextItem:
    name: str
    tokens: int


@dataclass
class ContextReport:
    model: str
    limit: int
    system_prompt_tokens: int
    tool_tokens: int
    core_tool_tokens: int
    extended_tool_tokens: int
    skill_tokens: int
    message_tokens: int
    autocompact_tokens: int
    core_tools: list[ContextItem]

    @property
    def used_tokens(self) -> int:
        return (
            self.system_prompt_tokens
            + self.tool_tokens
            + self.skill_tokens
            + self.message_tokens
        )

    @property
    def free_tokens(self) -> int:
        return max(0, self.limit - self.used_tokens - self.autocompact_tokens)


def _token_count_text(text: str, model: str | None = None) -> int:
    return count_tokens(text or "", model)


def _format_tokens(tokens: int) -> str:
    sign = "-" if tokens < 0 else ""
    tokens = abs(int(tokens))
    if tokens >= 1_000_000:
        return f"{sign}{tokens / 1_000_000:.1f}m"
    if tokens >= 1_000:
        return f"{sign}{tokens / 1_000:.1f}k"
    return f"{sign}{tokens}"


def _pct(tokens: int, limit: int) -> float:
    if limit <= 0:
        return 0.0
    return tokens / limit * 100


def _serialize_tool(tool: Any) -> str:
    try:
        payload = tool.to_openai_schema()
    except Exception as e:
        logger.debug("工具 schema 序列化失败,使用 fallback: %s", e)
        payload = {
            "name": getattr(tool, "name", tool.__class__.__name__),
            "description": getattr(tool, "description", ""),
            "args": getattr(tool, "args", {}),
        }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _top_items(items: list[ContextItem], limit: int = 8) -> list[ContextItem]:
    return sorted(items, key=lambda item: item.tokens, reverse=True)[:limit]


def _build_usage_bar(report: ContextReport) -> list[str]:
    used_cells = (
        round(report.used_tokens / report.limit * BAR_CELLS) if report.limit else 0
    )
    buffer_cells = (
        round(report.autocompact_tokens / report.limit * BAR_CELLS)
        if report.limit
        else 0
    )
    used_cells = max(0, min(BAR_CELLS, used_cells))
    buffer_cells = max(0, min(BAR_CELLS - used_cells, buffer_cells))
    free_cells = max(0, BAR_CELLS - used_cells - buffer_cells)

    cells = ["⛁"] * used_cells + ["⛶"] * free_cells + ["⛝"] * buffer_cells
    if not cells:
        cells = ["⛶"] * BAR_CELLS

    lines = []
    for i in range(0, BAR_CELLS, 10):
        lines.append(" ".join(cells[i : i + 10]))
    return lines


async def analyze_context(config: AppConfig) -> ContextReport:
    task = config.current_agent
    model = config.model_name[0] if config.model_name else "unknown"
    limit = get_context_limit(model)

    system_prompt = await build_system_prompt(config)
    system_prompt_tokens = _token_count_text(system_prompt, model)
    message_tokens = task.session.estimate_tokens(model)

    from uniclaw.tools import get_core_tools

    # 只统计核心工具的 schema token —— 扩展工具通过 search_tools 延迟加载,
    # 其摘要列表已包含在 system_prompt_tokens 中,不单独计算 schema
    core_tool_items: list[ContextItem] = []
    for tool in await get_core_tools():
        tokens = _token_count_text(_serialize_tool(tool), model)
        core_tool_items.append(ContextItem(getattr(tool, "name", "unknown"), tokens))
    core_tool_tokens = sum(item.tokens for item in core_tool_items)

    # 扩展工具摘要已在 system_prompt 中,此处仅统计摘要文本的 token
    from uniclaw.tools.registry import get_registry_system_prompt

    extended_summary = await get_registry_system_prompt(config)
    extended_tool_tokens = _token_count_text(extended_summary, model)

    # 技能按需触发,不会预先加载到上下文中,token 计为 0
    skill_tokens = 0

    autocompact_tokens = int(limit * AUTOCOMPACT_RATIO)

    return ContextReport(
        model=model,
        limit=limit,
        system_prompt_tokens=system_prompt_tokens,
        tool_tokens=core_tool_tokens + extended_tool_tokens,
        core_tool_tokens=core_tool_tokens,
        extended_tool_tokens=extended_tool_tokens,
        skill_tokens=skill_tokens,
        message_tokens=message_tokens,
        autocompact_tokens=autocompact_tokens,
        core_tools=_top_items(core_tool_items, 20),
    )


def format_context_report(report: ContextReport) -> str:
    lines = ["Context Usage"]
    bar = _build_usage_bar(report)
    for idx, row in enumerate(bar):
        suffix = ""
        if idx == 0:
            suffix = f"  {report.model}"
        elif idx == 1:
            suffix = (
                f"  {_format_tokens(report.used_tokens)}/{_format_tokens(report.limit)} "
                f"tokens ({_pct(report.used_tokens, report.limit):.1f}%)"
            )
        elif idx == 3:
            suffix = "  Estimated usage by category"
        elif idx == 4:
            suffix = (
                f"  ⛁ System prompt: {_format_tokens(report.system_prompt_tokens)} "
                f"tokens ({_pct(report.system_prompt_tokens, report.limit):.1f}%)"
            )
        lines.append(f"  {row}{suffix}")

    category_lines = [
        ("Core tools (full schema)", report.core_tool_tokens, "⛁"),
        ("Extended tools (summary only)", report.extended_tool_tokens, "⛁"),
        ("Skills (on-demand, 0 until triggered)", report.skill_tokens, "⛁"),
        ("Messages", report.message_tokens, "⛁"),
        ("Free space", report.free_tokens, "⛶"),
        ("Autocompact buffer", report.autocompact_tokens, "⛝"),
    ]
    for label, tokens, marker in category_lines:
        lines.append(
            f"  {marker} {label}: {_format_tokens(tokens)} tokens "
            f"({_pct(tokens, report.limit):.1f}%)"
        )

    if report.core_tools:
        lines.append("")
        lines.append(
            f"Core tools ({len(report.core_tools)} tools, "
            f"~{_format_tokens(report.core_tool_tokens)} tokens)"
        )
        for item in report.core_tools:
            lines.append(f"├ {item.name}: ~{_format_tokens(item.tokens)} tokens")

    lines.append("")
    lines.append(
        "Skills: loaded on-demand (triggered skills inject into context at runtime)"
    )

    lines.append("")
    lines.append(
        "Note: values are local estimates; provider-side tool schema overhead may differ."
    )
    return "\n".join(lines)


async def cmd_context(_args: str, config: AppConfig) -> bool:
    """查看当前上下文 token 构成,包括系统提示、工具、技能和消息的占用情况"""
    try:
        await info("\n" + format_context_report(await analyze_context(config)), config)
    except Exception as exc:
        await warn(f"无法估算上下文: {exc}", config)
    return True
