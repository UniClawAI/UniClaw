"""
用量统计模块的单元测试
"""

import json
import pytest
from unittest.mock import patch
from pathlib import Path
from uniclaw.utils.usage import record_usage, get_stats, format_stats, _new_record


@pytest.fixture(autouse=True)
def tmp_stats(tmp_path):
    """每个测试使用独立的临时文件"""
    fake_dir = tmp_path / ".UniClaw"
    fake_dir.mkdir()
    stats_file = fake_dir / "usage.json"
    with patch("uniclaw.utils.usage._stats_path", return_value=stats_file):
        yield stats_file


class TestRecordUsage:
    """记录用量测试"""

    @pytest.mark.asyncio
    async def test_record_single(self):
        await record_usage(input_tokens=100, output_tokens=50, tool_calls=2)
        data = get_stats()
        assert data["total"]["input_tokens"] == 100
        assert data["total"]["output_tokens"] == 50
        assert data["total"]["api_calls"] == 1
        assert data["total"]["tool_calls"] == 2

    @pytest.mark.asyncio
    async def test_record_accumulates(self):
        await record_usage(input_tokens=100, output_tokens=50, tool_calls=1)
        await record_usage(input_tokens=200, output_tokens=80, tool_calls=3)
        data = get_stats()
        assert data["total"]["input_tokens"] == 300
        assert data["total"]["output_tokens"] == 130
        assert data["total"]["api_calls"] == 2
        assert data["total"]["tool_calls"] == 4

    @pytest.mark.asyncio
    async def test_record_skip_all_zero(self):
        await record_usage(0, 0, 0)
        data = get_stats()
        assert data["total"]["api_calls"] == 0

    @pytest.mark.asyncio
    async def test_daily_stats(self):
        await record_usage(input_tokens=100, output_tokens=50, tool_calls=1)
        data = get_stats()
        assert len(data["daily"]) == 1
        day = list(data["daily"].values())[0]
        assert day["input_tokens"] == 100
        assert day["output_tokens"] == 50

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_stats):
        await record_usage(input_tokens=100, output_tokens=50, tool_calls=1)
        # 直接读文件验证持久化
        data = json.loads(tmp_stats.read_text(encoding="utf-8"))
        assert data["total"]["input_tokens"] == 100


class TestGetStats:
    """获取统计测试"""

    def test_empty_stats(self):
        data = get_stats()
        assert data["total"]["input_tokens"] == 0
        assert data["daily"] == {}

    def test_corrupted_file(self, tmp_stats):
        tmp_stats.write_text("not json", encoding="utf-8")
        data = get_stats()
        assert data["total"]["input_tokens"] == 0


class TestRecordUsageCache:
    """缓存字段落库测试"""

    @pytest.mark.asyncio
    async def test_record_cache_fields(self):
        await record_usage(
            input_tokens=5000,
            output_tokens=200,
            cached_tokens=4000,
            cache_write_tokens=1000,
            cache_discount=0.012,
        )
        data = get_stats()
        assert data["total"]["cached_tokens"] == 4000
        assert data["total"]["cache_write_tokens"] == 1000
        assert data["total"]["cache_discount"] == pytest.approx(0.012)
        day = list(data["daily"].values())[0]
        assert day["cached_tokens"] == 4000
        assert day["cache_write_tokens"] == 1000
        assert day["cache_discount"] == pytest.approx(0.012)

    @pytest.mark.asyncio
    async def test_record_cache_accumulates(self):
        await record_usage(input_tokens=5000, cached_tokens=4000)
        await record_usage(input_tokens=5000, cached_tokens=3000, cache_discount=-0.001)
        data = get_stats()
        assert data["total"]["cached_tokens"] == 7000
        assert data["total"]["cache_discount"] == pytest.approx(-0.001)

    @pytest.mark.asyncio
    async def test_record_skip_cache_only_zero(self):
        # 只有缓存字段时才需要落库
        await record_usage(cached_tokens=100)
        data = get_stats()
        assert data["total"]["cached_tokens"] == 100
        assert data["total"]["api_calls"] == 1

    @pytest.mark.asyncio
    async def test_cache_discount_affects_cost(self, tmp_path):
        """cache_discount 修正费用:基础费用 - 折扣 = 实际费用"""
        from uniclaw.utils.usage import _estimate_cost_from_price

        price = {"input": 2.5e-6, "output": 1e-5, "cache_read": 0.0, "cache_write": 0.0}
        # 10000 input + 500 output,无折扣
        base = _estimate_cost_from_price(10000, 500, price, 0, 0, 0)
        assert base == pytest.approx(0.03)

        # 缓存命中 8000 tokens,cache_discount 为正(省钱)
        discounted = _estimate_cost_from_price(10000, 500, price, 8000, 0, 0.012)
        assert discounted == pytest.approx(0.018)  # 0.03 - 0.012

        # 缓存写入,cache_discount 为负(多花钱)
        expensive = _estimate_cost_from_price(10000, 500, price, 0, 1000, -0.005)
        assert expensive == pytest.approx(0.035)  # 0.03 + 0.005

    @pytest.mark.asyncio
    async def test_cache_prices_manual_calculation(self, tmp_path):
        """当没有 cache_discount 时,使用缓存价格手动计算"""
        from uniclaw.utils.usage import _estimate_cost_from_price

        # 价格: input=$2.5/M, output=$10/M, cache_read=$0.25/M, cache_write=$3.125/M
        price = {
            "input": 2.5e-6,
            "output": 1e-5,
            "cache_read": 0.25e-6,
            "cache_write": 3.125e-6,
        }

        # 无缓存: 10000 * 2.5e-6 + 500 * 1e-5 = 0.025 + 0.005 = 0.03
        cost_no_cache = _estimate_cost_from_price(10000, 500, price, 0, 0, 0)
        assert cost_no_cache == pytest.approx(0.03)

        # 缓存读取 8000 tokens:
        # 节省 = 8000 * (2.5e-6 - 0.25e-6) = 8000 * 2.25e-6 = 0.018
        # 费用 = 0.03 - 0.018 = 0.012
        cost_cache_read = _estimate_cost_from_price(10000, 500, price, 8000, 0, 0)
        assert cost_cache_read == pytest.approx(0.012)

        # 缓存写入 2000 tokens:
        # 额外 = 2000 * (3.125e-6 - 2.5e-6) = 2000 * 0.625e-6 = 0.00125
        # 费用 = 0.03 + 0.00125 = 0.03125
        cost_cache_write = _estimate_cost_from_price(10000, 500, price, 0, 2000, 0)
        assert cost_cache_write == pytest.approx(0.03125)

        # 同时有缓存读取和写入:
        # 节省 = 8000 * (2.5e-6 - 0.25e-6) = 0.018
        # 额外 = 2000 * (3.125e-6 - 2.5e-6) = 0.00125
        # 费用 = 0.03 - 0.018 + 0.00125 = 0.01325
        cost_both = _estimate_cost_from_price(10000, 500, price, 8000, 2000, 0)
        assert cost_both == pytest.approx(0.01325)

    @pytest.mark.asyncio
    async def test_migrate_old_file(self, tmp_stats):
        # 旧版 usage.json 缺少缓存字段,迁移后补 0,record_usage 不应 KeyError
        tmp_stats.write_text(
            json.dumps(
                {
                    "total": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "api_calls": 1,
                        "tool_calls": 0,
                    },
                    "daily": {
                        "2026-01-01": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "api_calls": 1,
                            "tool_calls": 0,
                            "cost": 0.0,
                        }
                    },
                    "by_model": {
                        "gpt-4o": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "api_calls": 1,
                            "tool_calls": 0,
                            "cost": 0.0,
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        await record_usage(input_tokens=100, cached_tokens=50)
        data = get_stats()
        assert data["total"]["cached_tokens"] == 50
        assert data["total"]["input_tokens"] == 200  # 旧字段保留并累计
        assert data["daily"]["2026-01-01"]["cache_write_tokens"] == 0
        assert data["by_model"]["gpt-4o"]["cached_tokens"] == 0


class TestFormatStats:
    """格式化测试"""

    def test_empty(self):
        text = format_stats()
        assert "0 输入" in text
        assert "0 次 API 调用" in text

    @pytest.mark.asyncio
    async def test_with_data(self):
        await record_usage(input_tokens=1000, output_tokens=500, tool_calls=3)
        text = format_stats()
        assert "1,000 输入" in text
        assert "500 输出" in text
        assert "1,500" in text  # total tokens
        assert "1 次 API 调用" in text
        assert "3 次工具调用" in text

    @pytest.mark.asyncio
    async def test_daily_section(self):
        await record_usage(input_tokens=100, output_tokens=50, tool_calls=1)
        text = format_stats()
        assert "最近 7 天" in text
        assert "次调用" in text

    @pytest.mark.asyncio
    async def test_cache_line(self):
        await record_usage(
            input_tokens=5000,
            output_tokens=200,
            cached_tokens=4000,
            cache_write_tokens=1000,
            cache_discount=0.012,
        )
        text = format_stats()
        assert "缓存" in text
        assert "命中 4,000 tokens" in text
        assert "写入 1,000" in text
        assert "$0.0120" in text

    @pytest.mark.asyncio
    async def test_cache_line_hidden_when_zero(self):
        await record_usage(input_tokens=100, output_tokens=50)
        text = format_stats()
        assert "缓存" not in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
