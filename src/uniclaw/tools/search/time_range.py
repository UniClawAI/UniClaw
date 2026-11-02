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


# 时长单位 -> 单份时长, 任意 "数字+单位" 组合均按此换算
# (如 3d = 3 天); m/y 为近似换算, 与搜索过滤的粗粒度需求匹配
_UNIT_FACTOR: dict[str, timedelta] = {
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "m": timedelta(days=30),
    "y": timedelta(days=365),
}
_DURATION_RE = re.compile(r"(\d+)([hdwmy])")

# ISO 区间: 两侧均可省略, 但至少要有一个日期 (单独日期不算区间)
_DAY = r"\d{4}-\d{2}-\d{2}"
_RANGE_RE = re.compile(rf"^({_DAY})?\.\.({_DAY})?$")

_USAGE = (
    "支持的格式: 时长 (3d/12h/2w/6m/1y, 可组合如 1w2d;"
    " 单位 h=小时 d=天 w=周 m=月(30天) y=年(365天))、all"
    " 或 ISO 区间 (2024-01-01..2024-06-30, 单边可省略)"
)


def _parse_duration(v: str) -> timedelta | None:
    """解析 "3d"/"24h"/"1w2d" 这类时长串为回溯时长。

    任意正整数 + 单位的组合均接受, 不依赖预设枚举; 含无法识别的
    字符、数量为 0、数值超出 timedelta 上限时返回 None (由调用方
    继续尝试 ISO 区间解析或按非法报错)。

    Args:
        v: 已 strip + lower 的时长串, 如 "3d"、"1w2d"。

    Returns:
        timedelta | None: 解析出的总时长; 非法输入返回 None。
    """
    total = timedelta()
    pos = 0
    for m in _DURATION_RE.finditer(v):
        # 中间夹了无法识别的字符 (如 "-7d" 的负号、"3d-x" 的分隔符)
        if m.start() != pos:
            return None
        n = int(m.group(1))
        if n == 0:
            return None
        try:
            total += _UNIT_FACTOR[m.group(2)] * n
        except OverflowError:
            # 超大数值是垃圾输入 (非 bug), 按非法处理
            return None
        pos = m.end()
    if pos != len(v) or total == timedelta():
        return None
    return total


def parse_time_range(value: str, *, now: datetime | None = None) -> TimeRange:
    """解析 time_range 参数, 产物均为 tz-aware UTC datetime。

    Args:
        value: 时长 (如 "3d"/"12h"/"2w"/"1y", 单位 h/d/w/m/y,
            可组合如 "1w2d"; m 按 30 天、y 按 365 天近似,
            大小写不敏感, 前后空白容忍)、"all" 或 ISO 区间
            ("2024-01-01..2024-06-30", 单边可省略); 空值等价于 "all"
        now: 时长回溯的基准时刻, 默认当前 UTC 时间 (测试注入用)

    Returns:
        TimeRange: 解析产物, 不过滤时两侧均为 None;
            ISO 区间的 end 补至当日 23:59:59 (含端点日)

    Raises:
        ValueError: 输入不符合任何支持格式时抛出, 消息含用法说明
    """
    v = (value or "").strip().lower()
    if not v or v == "all":
        return TimeRange(None, None)

    delta = _parse_duration(v)
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
