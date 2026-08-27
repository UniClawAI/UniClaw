"""time_range 参数解析与各平台日期格式化工具。

搜索链路的 time_range 参数统一在此解析为 TimeRange(start, end),
各平台模块再用下述格式化函数转换为自己 API 接受的日期表示。
"""

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import NamedTuple


class TimeRange(NamedTuple):
    """parse_time_range 的解析产物, 缺失的一侧为 None。"""

    start: datetime | None
    end: datetime | None


# 预设别名 -> 回溯时长 (strip + lower 后查表)
_PRESETS: dict[str, timedelta] = {
    "1d": timedelta(days=1),
    "24h": timedelta(days=1),
    "7d": timedelta(weeks=1),
    "1w": timedelta(weeks=1),
    "30d": timedelta(days=30),
    "1m": timedelta(days=30),
    "90d": timedelta(days=90),
    "3m": timedelta(days=90),
    "180d": timedelta(days=180),
    "6m": timedelta(days=180),
    "365d": timedelta(days=365),
    "1y": timedelta(days=365),
}

# ISO 区间: 两侧均可省略, 但至少要有一个日期 (单独日期不算区间)
_DAY = r"\d{4}-\d{2}-\d{2}"
_RANGE_RE = re.compile(rf"^({_DAY})?\.\.({_DAY})?$")

_USAGE = (
    "支持的格式: 预设 (1d/7d/30d/90d/180d/1y/all)"
    " 或 ISO 区间 (2024-01-01..2024-06-30, 单边可省略)"
)


def parse_time_range(value: str, *, now: datetime | None = None) -> TimeRange:
    """解析 time_range 参数, 产物均为 tz-aware UTC datetime。

    Args:
        value: 预设 ("1d"/"7d"/"30d"/"90d"/"180d"/"1y"/"all", 大小写不敏感,
            前后空白容忍) 或 ISO 区间 ("2024-01-01..2024-06-30",
            单边可省略); 空值等价于 "all"
        now: 预设回溯的基准时刻, 默认当前 UTC 时间 (测试注入用)

    Returns:
        TimeRange: 解析产物, 不过滤时两侧均为 None;
            ISO 区间的 end 补至当日 23:59:59 (含端点日)

    Raises:
        ValueError: 输入不符合任何支持格式时抛出, 消息含用法说明
    """
    v = (value or "").strip().lower()
    if not v or v == "all":
        return TimeRange(None, None)

    delta = _PRESETS.get(v)
    if delta is not None:
        base = now or datetime.now(timezone.utc)
        return TimeRange(base - delta, None)

    m = _RANGE_RE.match(v)
    if not m or not (m.group(1) or m.group(2)):
        # "..." 或 ".." 这类无日期区间同样视为非法
        raise ValueError(f"无法解析 time_range={value!r}: {_USAGE}")
    try:
        start = (
            datetime.combine(
                date.fromisoformat(m.group(1)), time.min, tzinfo=timezone.utc
            )
            if m.group(1)
            else None
        )
        end = (
            datetime.combine(
                date.fromisoformat(m.group(2)), time(23, 59, 59), tzinfo=timezone.utc
            )
            if m.group(2)
            else None
        )
    except ValueError as e:
        raise ValueError(f"time_range 中存在无效日期: {_USAGE}") from e
    return TimeRange(start, end)


def epoch(dt: datetime) -> int:
    """转 Unix 秒时间戳 (Stack Overflow/Hacker News 数值过滤用)。"""
    return int(dt.timestamp())


def arxiv_stamp(dt: datetime) -> str:
    """转 arXiv submittedDate 的 12 位时间戳 (%Y%m%d%H%M)。"""
    return dt.strftime("%Y%m%d%H%M")


def iso_date(dt: datetime) -> str:
    """转 ISO 日期串 YYYY-MM-DD (github/google_news/openalex/sec_edgar 用)。"""
    return dt.strftime("%Y-%m-%d")


def exa_rfc3339(dt: datetime, *, end: bool) -> str:
    """转 Exa 的 RFC3339 日期串, 只取日期部分规范化时刻。

    Args:
        dt: 边界时刻 (只使用其日期部分)
        end: True 表示区间上界 (当日 23:59:59), 否则为下界 (当日 00:00:00)

    Returns:
        str: 形如 "2024-06-30T23:59:59Z" 的字符串
    """
    hhmmss = "23:59:59" if end else "00:00:00"
    return f"{dt.date():%Y-%m-%d}T{hhmmss}Z"


# 桶边界容差: 吸收 parse_time_range 与 reddit_t 两次取当前时刻之间的时钟差,
# 否则 "7d" 这类恰好压线的预设在真实时钟下 span 会略微超过名义上限而越档
_BUCKET_TOL = timedelta(minutes=1)


def reddit_t(tr: TimeRange, *, now: datetime | None = None) -> str:
    """把 TimeRange 映射为 Reddit 的 t 档位。

    Reddit 只接受 hour/day/week/month/year/all 固定档位而非精确区间,
    按区间跨度就近向上映射; 无下界 (只有 end 或全空) 时返回 all。
    """
    if tr.start is None:
        return "all"
    span = (tr.end or now or datetime.now(timezone.utc)) - tr.start
    if span <= timedelta(hours=1) + _BUCKET_TOL:
        return "hour"
    if span <= timedelta(days=1) + _BUCKET_TOL:
        return "day"
    if span <= timedelta(weeks=1) + _BUCKET_TOL:
        return "week"
    if span <= timedelta(days=31) + _BUCKET_TOL:
        return "month"
    if span <= timedelta(days=366) + _BUCKET_TOL:
        return "year"
    return "all"
