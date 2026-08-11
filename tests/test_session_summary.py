"""Session 摘要转录构建与 compact() 压缩逻辑测试。"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from uniclaw.provider.types import Usage
from uniclaw.tools.session.session import (
    AIMessage,
    CHECKPOINT_TEMPLATE,
    Session,
    ToolCallMessage,
    UserMessage,
    _build_summary_transcript,
)

# ── CHECKPOINT_TEMPLATE ────────────────────────────────────────


def test_checkpoint_template_has_all_continuation_sections():
    """续作摘要模板包含全部必要板块。"""
    for section in (
        "## 用户需求",
        "## 当前进度",
        "## 已完成",
        "## 待完成",
        "## 下一步动作",
        "## 涉及文件",
        "## 关键决策",
        "## 关键认知",
        "## 错误与修复",
        "## 归档信息",
    ):
        assert section in CHECKPOINT_TEMPLATE


def test_checkpoint_template_has_placeholders():
    """模板保留 __FOCUS__/__BUDGET__ 占位符,由 compact() 填充。"""
    assert "__FOCUS__" in CHECKPOINT_TEMPLATE
    assert "__BUDGET__" in CHECKPOINT_TEMPLATE


def _user(content: str) -> UserMessage:
    return UserMessage(content=content)


def _assistant(content: str = "", tool_calls: list | None = None) -> AIMessage:
    return AIMessage(
        content=content,
        model_name="gpt-4o",
        usage=Usage.from_dict({}),
        tool_calls=tool_calls or [],
    )


def _tool(name: str, args: dict, content: str = "") -> ToolCallMessage:
    return ToolCallMessage(name=name, tool_call_id="t1", args=args, content=content)


# ── _build_summary_transcript ──────────────────────────────────


def test_user_assistant_labels():
    """用户/助手消息带角色标签且内容保留。"""
    messages = [_user("帮我改 README"), _assistant("好的,我看看")]
    text = _build_summary_transcript(messages)
    assert "[用户]: 帮我改 README" in text
    assert "[助手]: 好的,我看看" in text


def test_tool_call_format():
    """工具调用显示名称和缩短后的参数。"""
    messages = [_tool("Read", {"path": "README.md", "limit": 100}, "文件内容")]
    text = _build_summary_transcript(messages)
    assert "[工具] Read(path=README.md, limit=100)" in text
    assert "文件内容" in text


def test_tool_no_args():
    """无参数的工具调用不带括号。"""
    messages = [_tool("Read", {}, "结果")]
    text = _build_summary_transcript(messages)
    assert "[工具] Read" in text
    assert "结果" in text


def test_tool_empty_result_no_blank_line():
    """空工具结果不追加空行。"""
    messages = [_tool("Read", {"path": "x"}, "")]
    text = _build_summary_transcript(messages)
    assert "[工具] Read(path=x)" in text
    assert "\n[工具]" not in text


def test_tool_result_truncated():
    """超长工具结果被截断并标记。"""
    messages = [_tool("Read", {"path": "a.py"}, "x" * 2000)]
    text = _build_summary_transcript(messages, max_result_chars=100)
    assert "x" * 100 in text
    assert "...(结果已截断)" in text
    assert "x" * 2000 not in text


def test_user_message_truncated():
    """超长用户消息被截断并标记。"""
    messages = [_user("y" * 3000)]
    text = _build_summary_transcript(messages, max_user_chars=100)
    assert "y" * 100 in text
    assert "...(已省略)" in text
    assert "y" * 3000 not in text


def test_assistant_tool_calls_note():
    """助手发起工具调用时追加调用工具名列表。"""
    messages = [
        _assistant(
            "我先读文件",
            tool_calls=[{"function": {"name": "Read"}}, {"function": {"name": "Grep"}}],
        )
    ]
    text = _build_summary_transcript(messages)
    assert "调用工具: Read, Grep" in text
    assert "我先读文件" in text


def test_overall_cap_stops():
    """转录总长超限后停止追加,尾部带省略标记。"""
    messages = [_user("z" * 500) for _ in range(50)]
    text = _build_summary_transcript(messages, max_total_chars=300)
    assert len(text) <= 400  # 允许小幅超出(省略标记 + 分隔符)
    assert text.endswith("...(已省略)")


def test_unknown_message_skipped():
    """不认识的类型被跳过而不报错。"""
    messages = [_user("你好"), "not-a-message"]
    text = _build_summary_transcript(messages)
    assert "[用户]: 你好" in text


# ── Session.compact ────────────────────────────────────────────


def _make_filled_session() -> Session:
    """构造 6 条消息(3 对 user/assistant)的 Session。"""
    s = Session()
    for _ in range(3):
        s.add_user_message(content="用户消息")
        s.add_assistant_message(content="回复内容", model_name="gpt-4o", usage={})
    return s


def _mock_config() -> SimpleNamespace:
    return SimpleNamespace(
        spinner=SimpleNamespace(start=lambda msg: "wait-id", stop=lambda wait_id: None),
        model_name=["gpt-4o", "gpt-4o-mini"],
    )


@pytest.mark.asyncio
async def test_compact_prepends_summary_pair():
    """压缩后头部为摘要 user/assistant 消息对,_compact_count=2。"""
    s = _make_filled_session()
    with (
        patch(
            "uniclaw.tools.session.session.Session._find_split_point", return_value=4
        ),
        patch("uniclaw.provider.fallback.achat", new=AsyncMock()) as mock_achat,
    ):
        mock_achat.return_value = AIMessage(
            content="## 用户需求\n...", model_name="gpt-4o", usage=Usage.from_dict({})
        )
        await s.compact(_mock_config())

    assert len(s._messages) == 4  # 摘要对(2) + recent(2)
    assert isinstance(s._messages[0], UserMessage)
    assert "[之前的对话摘要]" in s._messages[0].content
    assert isinstance(s._messages[1], AIMessage)
    assert s._compact_count == 2


@pytest.mark.asyncio
async def test_compact_calls_achat_with_summary_params():
    """achat 以续作导向参数调用: 低温度、关思考、max_tokens 预算。"""
    s = _make_filled_session()
    with (
        patch(
            "uniclaw.tools.session.session.Session._find_split_point", return_value=4
        ),
        patch("uniclaw.provider.fallback.achat", new=AsyncMock()) as mock_achat,
    ):
        mock_achat.return_value = AIMessage(
            content="摘要内容", model_name="gpt-4o", usage=Usage.from_dict({})
        )
        await s.compact(_mock_config())

    kwargs = mock_achat.call_args.kwargs
    assert kwargs["temperature"] == 0.2
    assert kwargs["enable_thinking"] is False
    assert kwargs["thinking"] is False
    assert kwargs["max_tokens"] >= 500
    assert kwargs["max_tokens"] <= 1500
    # 使用 fallback.achat 并传入模型列表,保证主模型失败时能回退备用模型
    assert kwargs["model_name"] == ["gpt-4o", "gpt-4o-mini"]


@pytest.mark.asyncio
async def test_compact_focus_in_prompt():
    """focus 参数写入提示词(替换 __FOCUS__ 占位符)。"""
    s = _make_filled_session()
    with (
        patch(
            "uniclaw.tools.session.session.Session._find_split_point", return_value=4
        ),
        patch("uniclaw.provider.fallback.achat", new=AsyncMock()) as mock_achat,
    ):
        mock_achat.return_value = AIMessage(
            content="摘要", model_name="gpt-4o", usage=Usage.from_dict({})
        )
        await s.compact(_mock_config(), focus="文件修改")

    user_msg = mock_achat.call_args.args[1]._messages[0].content
    assert "特别关注点: 文件修改" in user_msg
    assert "__FOCUS__" not in user_msg


@pytest.mark.asyncio
async def test_compact_keeps_messages_on_failure():
    """LLM 调用抛异常时保留原消息,不破坏会话。"""
    s = _make_filled_session()
    before = list(s._messages)
    with (
        patch(
            "uniclaw.tools.session.session.Session._find_split_point", return_value=4
        ),
        patch(
            "uniclaw.provider.fallback.achat",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        await s.compact(_mock_config())

    assert s._messages == before
    assert s._compact_count == 0


@pytest.mark.asyncio
async def test_compact_keeps_messages_on_empty_summary():
    """摘要为空时保留原消息,避免清空上下文。"""
    s = _make_filled_session()
    before = list(s._messages)
    with (
        patch(
            "uniclaw.tools.session.session.Session._find_split_point", return_value=4
        ),
        patch("uniclaw.provider.fallback.achat", new=AsyncMock()) as mock_achat,
    ):
        mock_achat.return_value = AIMessage(
            content="   ", model_name="gpt-4o", usage=Usage.from_dict({})
        )
        await s.compact(_mock_config())

    assert s._messages == before


@pytest.mark.asyncio
async def test_compact_split_zero_noop():
    """split <= 0 时不触发压缩,不调用 LLM。"""
    s = _make_filled_session()
    with (
        patch(
            "uniclaw.tools.session.session.Session._find_split_point", return_value=0
        ),
        patch("uniclaw.provider.fallback.achat", new=AsyncMock()) as mock_achat,
    ):
        await s.compact(_mock_config())

    mock_achat.assert_not_called()
    assert s._compact_count == 0
