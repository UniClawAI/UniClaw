"""time_range 解析模块与格式化函数的单元测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from uniclaw.tools.search.time_range import (
    TimeRange,
    arxiv_stamp,
    epoch,
    exa_rfc3339,
    iso_date,
    parse_time_range,
    reddit_t,
)

# 固定基准时刻, 预设回溯的断言全部以此为参照
NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)


class TestParsePresets:
    """预设解析: 空值/all/各时长别名。"""

    def test_empty_string_no_filter(self):
        tr = parse_time_range("", now=NOW)
        assert tr == TimeRange(None, None)

    def test_all_keyword(self):
        tr = parse_time_range("all", now=NOW)
        assert tr == TimeRange(None, None)

    def test_whitespace_and_case_tolerant(self):
        assert parse_time_range(" ALL ", now=NOW) == TimeRange(None, None)
        tr = parse_time_range(" 7D ", now=NOW)
        assert tr.start == NOW - timedelta(weeks=1)

    @pytest.mark.parametrize(
        "value,expected_start",
        [
            ("1d", NOW - timedelta(days=1)),
            ("24h", NOW - timedelta(days=1)),
            ("7d", NOW - timedelta(weeks=1)),
            ("1w", NOW - timedelta(weeks=1)),
            ("30d", NOW - timedelta(days=30)),
            ("90d", NOW - timedelta(days=90)),
            ("180d", NOW - timedelta(days=180)),
            ("365d", NOW - timedelta(days=365)),
        ],
    )
    def test_preset_backtrack(self, value, expected_start):
        tr = parse_time_range(value, now=NOW)
        # 预设只有下界, 上界开放
        assert tr.start == expected_start
        assert tr.end is None


class TestParseIsoRange:
    """ISO 区间解析: 完整/单边/end 补端点。"""

    def test_full_range(self):
        tr = parse_time_range("2024-01-01..2024-06-30", now=NOW)
        assert tr.start == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        # end 补到当日末秒, 含端点日
        assert tr.end == datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)

    def test_open_left(self):
        tr = parse_time_range("..2024-06-30", now=NOW)
        assert tr.start is None
        assert tr.end == datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)

    def test_open_right(self):
        tr = parse_time_range("2024-01-01..", now=NOW)
        assert tr.start == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert tr.end is None

    def test_single_day_range(self):
        tr = parse_time_range("2024-03-15..2024-03-15", now=NOW)
        assert tr.start.date().isoformat() == "2024-03-15"
        assert tr.end.date().isoformat() == "2024-03-15"
        # 同一天区间仍覆盖完整一天
        assert tr.end > tr.start

    def test_utc_aware(self):
        tr = parse_time_range("2024-01-01..", now=NOW)
        assert tr.start.tzinfo is not None


class TestParseInvalid:
    """非法输入必须抛 ValueError 且消息含用法说明。"""

    @pytest.mark.parametrize(
        "value",
        [
            "abc",
            "..",
            "2024-01-01",  # 单独日期不是区间
            "2024-13-01..",  # 越界月份
            "2024-01-01..2024-02-30",  # 越界日期
            "1w2d",
            "-7d",
        ],
    )
    def test_invalid_valueerror(self, value):
        with pytest.raises(ValueError) as exc:
            parse_time_range(value, now=NOW)
        assert "time_range" in str(exc.value)


class TestFormatters:
    """各平台日期格式化纯函数。"""

    def test_epoch(self):
        dt = datetime(1970, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        assert epoch(dt) == 86400
        assert isinstance(epoch(dt), int)

    def test_arxiv_stamp(self):
        dt = datetime(2024, 6, 30, 23, 59, 59, tzinfo=timezone.utc)
        assert arxiv_stamp(dt) == "202406302359"
        assert len(arxiv_stamp(dt)) == 12

    def test_iso_date(self):
        dt = datetime(2024, 6, 5, 10, 0, 0, tzinfo=timezone.utc)
        assert iso_date(dt) == "2024-06-05"

    def test_exa_rfc3339_bounds(self):
        dt = datetime(2024, 6, 5, 15, 30, 0, tzinfo=timezone.utc)
        # 只取日期部分, start 下界取零点 / end 上界取末秒
        assert exa_rfc3339(dt, end=False) == "2024-06-05T00:00:00Z"
        assert exa_rfc3339(dt, end=True) == "2024-06-05T23:59:59Z"


class TestRedditT:
    """reddit_t 桶映射: 按跨度就近向上映射。"""

    def make(self, hours):
        return TimeRange(NOW - timedelta(hours=hours), None)

    def test_no_start_is_all(self):
        assert reddit_t(TimeRange(None, None)) == "all"
        assert reddit_t(TimeRange(None, NOW)) == "all"

    def test_buckets(self):
        # 边界值: 恰好落在档位上限内归该档
        assert reddit_t(self.make(1), now=NOW) == "hour"
        assert reddit_t(self.make(24), now=NOW) == "day"
        assert reddit_t(self.make(24 * 7), now=NOW) == "week"
        assert reddit_t(self.make(24 * 31), now=NOW) == "month"
        assert reddit_t(self.make(24 * 366), now=NOW) == "year"

    def test_between_buckets_rounds_up(self):
        # 跨度介于两档之间时向上取整档位
        assert reddit_t(self.make(2), now=NOW) == "day"
        assert reddit_t(self.make(48), now=NOW) == "week"
        assert reddit_t(self.make(24 * 14), now=NOW) == "month"
        assert reddit_t(self.make(24 * 60), now=NOW) == "year"

    def test_over_a_year_is_all(self):
        assert reddit_t(self.make(24 * 400), now=NOW) == "all"

    def test_explicit_end_span(self):
        # 有显式 end 时按 (end - start) 判档而非当前时刻
        tr = TimeRange(NOW - timedelta(hours=1), NOW - timedelta(minutes=30))
        assert reddit_t(tr, now=NOW + timedelta(days=100)) == "hour"

    def test_default_now_used_when_end_missing(self):
        # 不传 now 时内部取当前时间 (跨度远超 1y -> all)
        tr = TimeRange(datetime(2000, 1, 1, tzinfo=timezone.utc), None)
        assert reddit_t(tr) == "all"
