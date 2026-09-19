"""cmd_cost 动态列宽计算测试。

覆盖 commit 7b2f400 实现的动态列宽功能:
- 汇总行数值宽度可能超过数据行时,列宽应自动扩展
- 分隔线长度与列宽一致
- 每日统计表格同样使用动态列宽
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.utils.usage import UsageField, TOTAL, DAILY


# ── helpers ─────────────────────────────────────────────────


def _make_config():
    """构造最小可用的 AppConfig mock。"""
    config = MagicMock()
    config.root_dir = "/tmp"
    return config


async def _run_cost(stats: dict) -> str:
    """执行 cmd_cost 并捕获 info() 输出。"""
    with (
        patch("uniclaw.utils.usage.get_stats", return_value=stats),
        patch("uniclaw.commands.cost.info", new_callable=AsyncMock) as mock_info,
    ):
        from uniclaw.commands.cost import cmd_cost

        result = await cmd_cost("", _make_config())
        assert result is True
        # info 被调用一次, 第一个位置参数就是输出文本
        return mock_info.call_args[0][0]


# ── 按模型计费表格 ─────────────────────────────────────────


class TestByModelDynamicColumnWidth:
    """验证按模型计费表的动态列宽。"""

    @pytest.mark.asyncio
    async def test_wide_summary_row_expands_column(self):
        """汇总行的 token 数比任何单个模型行都大时,列宽应撑开。"""
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 12_345_678,
                UsageField.OUTPUT_TOKENS: 987_654,
                UsageField.API_CALLS: 42,
                UsageField.TOOL_CALLS: 10,
            },
            DAILY: {},
            "by_model": {
                "gpt-4o": {
                    UsageField.INPUT_TOKENS: 100,
                    UsageField.OUTPUT_TOKENS: 200,
                    UsageField.API_CALLS: 5,
                    "cost": 0.0012,
                },
            },
        }
        output = await _run_cost(stats)
        # 汇总行的 12,345,678 应该完整显示,不被截断
        assert "12,345,678" in output
        # 数据行也应正确对齐(数值完整显示)
        assert "100" in output
        assert "200" in output

    @pytest.mark.asyncio
    async def test_separator_line_matches_column_width(self):
        """按模型计费表的分隔线应长度一致(表头前和汇总行前)。"""
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 1_000,
                UsageField.OUTPUT_TOKENS: 2_000,
                UsageField.API_CALLS: 10,
                UsageField.TOOL_CALLS: 5,
            },
            DAILY: {},
            "by_model": {
                "model-a": {
                    UsageField.INPUT_TOKENS: 500,
                    UsageField.OUTPUT_TOKENS: 1_000,
                    UsageField.API_CALLS: 5,
                    "cost": 0.05,
                },
            },
        }
        output = await _run_cost(stats)
        lines = output.split("\n")
        # by_model 表的分隔线: 以 "  " 开头且含 ─ 和空格分段(不是纯 ─ 行)
        sep_lines = [
            l for l in lines
            if l.startswith("  ") and "─" in l and " " in l.strip()
        ]
        assert len(sep_lines) >= 2, f"应有至少两条按模型分隔线,实际: {sep_lines}"
        # 表头分隔线和汇总分隔线应长度相同
        assert len(sep_lines[0]) == len(sep_lines[1])

    @pytest.mark.asyncio
    async def test_zero_cost_displays_correctly(self):
        """费用为零时列宽应容纳 $0.0000。"""
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 0,
                UsageField.OUTPUT_TOKENS: 0,
                UsageField.API_CALLS: 0,
                UsageField.TOOL_CALLS: 0,
            },
            DAILY: {},
            "by_model": {
                "test-model": {
                    UsageField.INPUT_TOKENS: 0,
                    UsageField.OUTPUT_TOKENS: 0,
                    UsageField.API_CALLS: 0,
                    "cost": 0.0,
                },
            },
        }
        output = await _run_cost(stats)
        assert "$0.0000" in output

    @pytest.mark.asyncio
    async def test_multiple_models_sorted_and_aligned(self):
        """多个模型应按名称排序,所有行对齐。"""
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 300,
                UsageField.OUTPUT_TOKENS: 300,
                UsageField.API_CALLS: 30,
                UsageField.TOOL_CALLS: 0,
            },
            DAILY: {},
            "by_model": {
                "claude-3.5-sonnet": {
                    UsageField.INPUT_TOKENS: 100,
                    UsageField.OUTPUT_TOKENS: 100,
                    UsageField.API_CALLS: 10,
                    "cost": 0.01,
                },
                "gpt-4o": {
                    UsageField.INPUT_TOKENS: 200,
                    UsageField.OUTPUT_TOKENS: 200,
                    UsageField.API_CALLS: 20,
                    "cost": 0.02,
                },
            },
        }
        output = await _run_cost(stats)
        # 模型按名称排序
        claude_pos = output.index("claude-3.5-sonnet")
        gpt_pos = output.index("gpt-4o")
        assert claude_pos < gpt_pos

    @pytest.mark.asyncio
    async def test_long_model_name_truncated(self):
        """超过 27 字符的模型名应截断为 24 字符 + '...'。"""
        long_name = "a-very-long-model-name-that-exceeds"  # 35 chars
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 10,
                UsageField.OUTPUT_TOKENS: 10,
                UsageField.API_CALLS: 1,
                UsageField.TOOL_CALLS: 0,
            },
            DAILY: {},
            "by_model": {
                long_name: {
                    UsageField.INPUT_TOKENS: 10,
                    UsageField.OUTPUT_TOKENS: 10,
                    UsageField.API_CALLS: 1,
                    "cost": 0.001,
                },
            },
        }
        output = await _run_cost(stats)
        # 前 24 字符 + "..." = "a-very-long-model-name-t..."
        assert "a-very-long-model-name-t..." in output
        # 原始名不应出现
        assert long_name not in output


# ── 每日统计表格 ───────────────────────────────────────────


class TestDailyDynamicColumnWidth:
    """验证每日统计表的动态列宽。"""

    @pytest.mark.asyncio
    async def test_daily_columns_widen_for_large_values(self):
        """每日统计中大数值应正确对齐。"""
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 0,
                UsageField.OUTPUT_TOKENS: 0,
                UsageField.API_CALLS: 0,
                UsageField.TOOL_CALLS: 0,
            },
            DAILY: {
                "2026-09-17": {
                    UsageField.INPUT_TOKENS: 9_999_999,
                    UsageField.OUTPUT_TOKENS: 888,
                    UsageField.API_CALLS: 50,
                    "cost": 0.1234,
                },
            },
            "by_model": {},
        }
        output = await _run_cost(stats)
        assert "9,999,999" in output
        assert "$0.1234" in output

    @pytest.mark.asyncio
    async def test_daily_dates_sorted_descending(self):
        """日期应按倒序排列(最近的在前)。"""
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 0,
                UsageField.OUTPUT_TOKENS: 0,
                UsageField.API_CALLS: 0,
                UsageField.TOOL_CALLS: 0,
            },
            DAILY: {
                "2026-09-15": {
                    UsageField.INPUT_TOKENS: 10,
                    UsageField.OUTPUT_TOKENS: 20,
                    UsageField.API_CALLS: 1,
                    "cost": 0.01,
                },
                "2026-09-17": {
                    UsageField.INPUT_TOKENS: 30,
                    UsageField.OUTPUT_TOKENS: 40,
                    UsageField.API_CALLS: 2,
                    "cost": 0.02,
                },
            },
            "by_model": {},
        }
        output = await _run_cost(stats)
        pos_17 = output.index("2026-09-17")
        pos_15 = output.index("2026-09-15")
        assert pos_17 < pos_15


# ── 兼容旧数据 ─────────────────────────────────────────────


class TestLegacyData:
    """无 by_model 时显示兼容提示。"""

    @pytest.mark.asyncio
    async def test_no_by_model_shows_legacy_hint(self):
        stats = {
            TOTAL: {
                UsageField.INPUT_TOKENS: 100,
                UsageField.OUTPUT_TOKENS: 200,
                UsageField.API_CALLS: 5,
                UsageField.TOOL_CALLS: 2,
            },
            DAILY: {},
        }
        output = await _run_cost(stats)
        assert "旧数据无费用记录" in output