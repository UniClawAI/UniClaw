"""时间感知 (maybe_time_notice) 纯逻辑测试。

只测不依赖 LLM/IO 的部分:
- get_last_user_spoke_at 基准扫描(跳过 [system] 注入 / 时间戳异常)
- maybe_time_notice 触发条件(间隔阈值 / 跨天 / 不触发)
- _format_gap 间隔格式化
"""

from datetime import datetime, timedelta

from uniclaw.tools.session.session import (
    TIME_NOTICE_GAP,
    Session,
    _format_gap,
    get_last_user_spoke_at,
    maybe_time_notice,
)
from uniclaw.utils.constants import SYSTEM_PREFIX

# 固定基准时刻: 2026-09-24 15:00 周四
NOW = datetime(2026, 9, 24, 15, 0, 0)


def _session_with_user(text: str, created_at) -> Session:
    session = Session()
    session.add_user_message(content=text, created_at=created_at)
    return session


class TestGetLastUserSpokeAt:
    def test_empty_session(self):
        assert get_last_user_spoke_at(Session()) is None

    def test_returns_user_message_time(self):
        session = _session_with_user("hello", NOW - timedelta(minutes=5))
        assert get_last_user_spoke_at(session) == NOW - timedelta(minutes=5)

    def test_skips_system_notice(self):
        """[system] 注入消息不作为基准。"""
        session = _session_with_user("hello", NOW - timedelta(hours=3))
        session.add_user_message(
            content=f"{SYSTEM_PREFIX}(monitor) 监控命中",
            created_at=NOW - timedelta(minutes=1),
        )
        assert get_last_user_spoke_at(session) == NOW - timedelta(hours=3)

    def test_skips_assistant_messages(self):
        session = _session_with_user("hello", NOW - timedelta(hours=2))
        session.add_assistant_message(
            content="hi", model_name="test", usage={}, created_at=NOW
        )
        assert get_last_user_spoke_at(session) == NOW - timedelta(hours=2)

    def test_missing_timestamp_returns_none(self):
        """最近真实用户发言无时间戳 → None(无法判断间隔,不误报)。"""
        session = _session_with_user("old", NOW - timedelta(hours=5))
        session.add_user_message(content="new", created_at=None)
        assert get_last_user_spoke_at(session) is None

    def test_iso_string_timestamp_parsed(self):
        """fork 场景: created_at 可能是 ISO 字符串,需归一化。"""
        session = _session_with_user("hello", (NOW - timedelta(hours=2)).isoformat())
        assert get_last_user_spoke_at(session) == NOW - timedelta(hours=2)


class TestMaybeTimeNotice:
    def test_no_user_message(self):
        """新会话无用户发言 → 不注入。"""
        assert maybe_time_notice(Session(), now=NOW) is None

    def test_short_gap_same_day(self):
        session = _session_with_user("hello", NOW - timedelta(minutes=5))
        assert maybe_time_notice(session, now=NOW) is None

    def test_gap_exactly_one_hour(self):
        """间隔恰好 1 小时 → 注入(阈值为 >=)。"""
        session = _session_with_user("hello", NOW - TIME_NOTICE_GAP)
        notice = maybe_time_notice(session, now=NOW)
        assert notice is not None
        assert notice.startswith(SYSTEM_PREFIX)

    def test_long_gap_triggers(self):
        session = _session_with_user("hello", NOW - timedelta(hours=2, minutes=15))
        notice = maybe_time_notice(session, now=NOW)
        assert notice is not None
        assert "2026-09-24 15:00" in notice
        assert "周四" in notice
        assert "2 小时 15 分" in notice
        assert "已跨天" not in notice

    def test_cross_day_short_gap(self):
        """跨自然日但间隔不足 1 小时 → 仍注入。"""
        session = _session_with_user("hello", datetime(2026, 9, 24, 23, 50, 0))
        notice = maybe_time_notice(session, now=datetime(2026, 9, 25, 0, 10, 0))
        assert notice is not None
        assert "已跨天" in notice
        assert "20 分" in notice

    def test_system_notice_not_baseline(self):
        """[system] 消息插在中间,基准仍是更早的真实用户发言。"""
        session = _session_with_user("hello", NOW - timedelta(hours=3))
        session.add_user_message(
            content=f"{SYSTEM_PREFIX}(时间)当前时间:...",
            created_at=NOW - timedelta(minutes=10),
        )
        notice = maybe_time_notice(session, now=NOW)
        assert notice is not None
        assert "3 小时" in notice

    def test_notice_not_retriggered_by_itself(self):
        """时间告知注入后不成为新基准,也不会导致连续误触发。"""
        session = _session_with_user("hello", NOW - timedelta(hours=2))
        notice = maybe_time_notice(session, now=NOW)
        assert notice is not None
        session.add_user_message(content=notice, created_at=NOW)
        # 新基准是注入的告知? 不应发生 — 告知被跳过,基准仍是 2 小时前
        assert get_last_user_spoke_at(session) == NOW - timedelta(hours=2)
        # 用户真实发言落盘后,基准更新,短间隔不再注入
        session.add_user_message(content="继续", created_at=NOW)
        assert maybe_time_notice(session, now=NOW + timedelta(minutes=5)) is None

    def test_gap_formatting_days(self):
        session = _session_with_user(
            "hello", NOW - timedelta(days=1, hours=2, minutes=5)
        )
        notice = maybe_time_notice(session, now=NOW)
        assert notice is not None
        assert "1 天 2 小时 5 分" in notice


class TestFormatGap:
    def test_sub_minute(self):
        assert _format_gap(timedelta(seconds=30)) == "不足 1 分"

    def test_minutes_only(self):
        assert _format_gap(timedelta(minutes=45)) == "45 分"

    def test_hours_minutes(self):
        assert _format_gap(timedelta(hours=2, minutes=15)) == "2 小时 15 分"

    def test_days_hours_minutes(self):
        assert _format_gap(timedelta(days=1, hours=2, minutes=5)) == "1 天 2 小时 5 分"

    def test_exact_hours(self):
        assert _format_gap(timedelta(hours=3)) == "3 小时"

    def test_negative_clamped(self):
        assert _format_gap(timedelta(minutes=-5)) == "不足 1 分"
