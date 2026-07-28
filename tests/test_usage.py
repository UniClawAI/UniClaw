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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
