"""时间感知 (maybe_time_notice) 纯逻辑测试。

只测不依赖 LLM/IO 的部分:
- get_last_user_spoke_at 基准扫描(跳过 [system] 注入 / 时间戳异常)
- maybe_time_notice 触发条件(间隔阈值 / 跨天 / 不触发)
- _format_gap 间隔格式化
- is_time_notice 识别 / delete_messages 连带删除时间提醒
"""

from datetime import datetime, timedelta

from uniclaw.tools.session.session import (
    TIME_NOTICE_GAP,
    TIME_NOTICE_TAG,
    Session,
    _format_gap,
    get_last_user_spoke_at,
    is_time_notice,
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

    def test_aware_datetime_normalized(self):
        """带时区的 datetime 归一化为 naive 本地时间,不再与 now() 相减报错。"""
        local_tz = datetime.now().astimezone().tzinfo
        aware = datetime(2026, 9, 24, 12, 0, 0, tzinfo=local_tz)
        session = _session_with_user("hello", aware)
        assert get_last_user_spoke_at(session) == datetime(2026, 9, 24, 12, 0, 0)

    def test_aware_iso_string_normalized(self):
        """ISO 字符串带 UTC 偏移 / Z 后缀,归一化后是 naive 本地时间。"""
        for raw in ("2026-09-24T07:00:00+00:00", "2026-09-24T07:00:00Z"):
            session = _session_with_user("hello", raw)
            got = get_last_user_spoke_at(session)
            assert got is not None and got.tzinfo is None


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

    def test_aware_timestamp_no_crash(self):
        """created_at 带时区不抛 TypeError,间隔按本地时间正确计算。"""
        local_tz = datetime.now().astimezone().tzinfo
        session = _session_with_user(
            "hello", datetime(2026, 9, 24, 12, 0, 0, tzinfo=local_tz)
        )
        notice = maybe_time_notice(session, now=NOW)
        assert notice is not None
        assert "3 小时" in notice

    def test_aware_now_parameter(self):
        """测试注入的 now 带时区时同样归一化,不抛 TypeError。"""
        local_tz = datetime.now().astimezone().tzinfo
        session = _session_with_user("hello", NOW - timedelta(hours=2))
        aware_now = datetime(2026, 9, 24, 15, 0, 0, tzinfo=local_tz)
        notice = maybe_time_notice(session, now=aware_now)
        assert notice is not None
        assert "2 小时" in notice

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
            content=f"{TIME_NOTICE_TAG}当前时间:...",
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


def _session_with_notice_tail(prefix: str = TIME_NOTICE_TAG) -> Session:
    """构造末尾为 [user hello] [系统消息] [user 继续] [assistant 好的] 的会话。"""
    session = Session()
    session.add_user_message(content="hello", created_at=NOW - timedelta(hours=3))
    session.add_user_message(
        content=f"{prefix}当前时间:2026-09-24 14:00 周四;距上次用户发言已过 2 小时。",
        created_at=NOW - timedelta(hours=1),
    )
    session.add_user_message(content="继续", created_at=NOW - timedelta(hours=1))
    session.add_assistant_message(
        content="好的", model_name="test", usage={}, created_at=NOW
    )
    return session


class TestIsTimeNotice:
    def test_time_notice_recognized(self):
        session = _session_with_notice_tail()
        assert is_time_notice(session._messages[1]) is True

    def test_other_system_message_not_matched(self):
        """其他 [system] 注入(监控/Shell 命令等)不算时间提醒。"""
        session = Session()
        session.add_user_message(
            content=f"{SYSTEM_PREFIX}(monitor) 监控命中", created_at=NOW
        )
        assert is_time_notice(session._messages[0]) is False

    def test_normal_user_message_not_matched(self):
        session = _session_with_user("hello", NOW)
        assert is_time_notice(session._messages[0]) is False

    def test_non_user_message_not_matched(self):
        session = Session()
        session.add_assistant_message(
            content=f"{TIME_NOTICE_TAG}当前时间:...", model_name="t", usage={},
            created_at=NOW,
        )
        assert is_time_notice(session._messages[0]) is False


class TestDeleteMessagesWithTimeNotice:
    def test_notice_before_range_deleted_together(self):
        """被删范围上一条是时间提醒 → 一并删除,返回数量包含它。"""
        session = _session_with_notice_tail()
        deleted = session.delete_messages(2)
        assert deleted == 3
        assert [m.to_content() for m in session._messages] == ["hello"]
        assert len(session.history) == 1

    def test_normal_boundary_only_deletes_count(self):
        """上一条是普通消息 → 只删指定数量。"""
        session = _session_with_notice_tail()
        session.add_user_message(content="再问一句", created_at=NOW)
        # 删 "再问一句",边界是 assistant "好的",与时间提醒无关
        deleted = session.delete_messages(1)
        assert deleted == 1
        assert len(session._messages) == 4

    def test_other_system_boundary_not_deleted(self):
        """上一条是其他 [system] 消息 → 不连带删除。"""
        session = _session_with_notice_tail(prefix=f"{SYSTEM_PREFIX}(monitor)")
        deleted = session.delete_messages(2)
        assert deleted == 2
        assert len(session._messages) == 2

    def test_history_source_same_behavior(self):
        """source="history" 时同样连带删除时间提醒。"""
        session = _session_with_notice_tail()
        deleted = session.delete_messages(2, source="history")
        assert deleted == 3
        assert len(session.history) == 1
        assert len(session._messages) == 1

    def test_delete_all_no_extra(self):
        """全删时没有"上一条",不越界。"""
        session = _session_with_notice_tail()
        assert session.delete_messages(4) == 4
        assert len(session._messages) == 0

    def test_count_beyond_length_no_crash(self):
        session = _session_with_notice_tail()
        assert session.delete_messages(10) == 4
        assert len(session._messages) == 0

    def test_notice_inside_range_deleted_once(self):
        """时间提醒本身在被删范围内 → 不重复删,计数不含额外项。"""
        session = _session_with_notice_tail()
        # 从时间提醒开始删 3 条: 时间提醒 + "继续" + "好的"
        deleted = session.delete_messages(3)
        assert deleted == 3
        assert [m.to_content() for m in session._messages] == ["hello"]
