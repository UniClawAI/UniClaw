"""会话标题生成测试:_build_title_transcript 摘录构建与 generate_title 取材。"""

from unittest.mock import AsyncMock, patch

from uniclaw.provider.types import Usage
from uniclaw.tools.session.session import (
    AIMessage,
    SUMMARY_ACK_CONTENT,
    SUMMARY_PREFIX,
    Session,
    ToolCallMessage,
    UserMessage,
    _build_title_transcript,
    _extract_summary_needs,
)
from uniclaw.utils.constants import SYSTEM_PREFIX


def _user(content: str) -> UserMessage:
    return UserMessage(content=content)


def _assistant(content: str = "") -> AIMessage:
    return AIMessage(content=content, model_name="gpt-4o", usage=Usage.from_dict({}))


def _tool(name: str, args: dict, content: str = "") -> ToolCallMessage:
    return ToolCallMessage(name=name, tool_call_id="t1", args=args, content=content)


# ── _build_title_transcript ────────────────────────────────────


def test_keeps_both_start_and_end():
    """摘录同时保留开头与结尾,标题才有整个对话的跨度。"""
    messages = [_user("帮我调研 UniClaw 工具系统"), _assistant("好的")]
    text = _build_title_transcript(messages)
    assert "[用户]: 帮我调研 UniClaw 工具系统" in text
    assert "[助手]: 好的" in text


def test_long_conversation_keeps_head_and_tail():
    """过长对话保留开头与结尾,中间省略并标注条数。"""
    messages = [_user(f"消息{i}") for i in range(30)]
    text = _build_title_transcript(
        messages, max_total_chars=200, max_line_chars=50, head=3, tail=2
    )
    assert "[用户]: 消息0" in text
    assert "[用户]: 消息29" in text
    assert "中间省略 25 条消息" in text
    # 中间内容不得挤掉头尾
    assert "[用户]: 消息15" not in text


def test_short_conversation_not_elided():
    """总量未超限时全量保留,不做头尾切片。"""
    messages = [_user(f"消息{i}") for i in range(5)]
    text = _build_title_transcript(messages, head=2, tail=1)
    for i in range(5):
        assert f"[用户]: 消息{i}" in text
    assert "中间省略" not in text


def test_skips_tool_messages():
    """工具结果是过程噪音,不进标题摘录。"""
    messages = [_user("改一下 README"), _tool("Read", {"path": "a.py"}, "文件内容")]
    text = _build_title_transcript(messages)
    assert "文件内容" not in text
    assert "Read" not in text


def test_skips_system_injection():
    """系统注入(时间提醒/Shell 通知)不参与定题。"""
    messages = [
        _user(f"{SYSTEM_PREFIX}(time)当前时间..."),
        _assistant(SUMMARY_ACK_CONTENT),
        _user("新需求"),
    ]
    text = _build_title_transcript(messages)
    assert text == "[用户]: 新需求"


def test_summary_contributes_needs_section():
    """压缩摘要取「## 用户需求」段参与定题,而不是整条丢弃。"""
    messages = [
        _user(f"{SUMMARY_PREFIX}\n## 用户需求\n帮我调研工具系统\n\n## 当前进度\n推进中"),
        _assistant(SUMMARY_ACK_CONTENT),
        _user("收尾"),
    ]
    text = _build_title_transcript(messages)
    assert "[用户诉求(压缩摘要)]: 帮我调研工具系统" in text
    assert "推进中" not in text  # 非需求段不进摘录
    assert "[用户]: 收尾" in text


def test_extract_summary_needs():
    """摘要需求段提取:截到下一个同级标题,缺段时退回摘要正文。"""
    assert (
        _extract_summary_needs(f"{SUMMARY_PREFIX}\n## 用户需求\nA\nB\n\n## 当前进度\nC")
        == "A\nB"
    )
    # 缺少「## 用户需求」时返回去掉前缀的摘要正文
    assert _extract_summary_needs(f"{SUMMARY_PREFIX}\n## 当前进度\nC") == "## 当前进度\nC"


def test_line_truncated():
    """单条超长内容被截断并标记。"""
    text = _build_title_transcript([_user("x" * 500)], max_line_chars=50)
    assert "x" * 50 in text
    assert "...(已省略)" in text
    assert "x" * 500 not in text


def test_total_cap():
    """摘录总长受上限约束。"""
    messages = [_user("y" * 300) for _ in range(20)]
    text = _build_title_transcript(messages, max_total_chars=500, max_line_chars=100)
    assert len(text) <= 600  # 允许小幅超出(省略标记 + 分隔符)


def test_empty_content_skipped():
    """空消息与不可识别类型跳过而不报错。"""
    text = _build_title_transcript([_user("  "), _assistant(""), "junk", _user("你好")])
    assert text == "[用户]: 你好"


# ── Session._fallback_title ────────────────────────────────────


def test_fallback_title_skips_system():
    """回退标题跳过系统注入,取第一条真实用户消息。"""
    s = Session()
    s.add_user_message(content=f"{SYSTEM_PREFIX}(time)当前时间...")
    s.add_user_message(content="帮我修登录页的 bug")
    assert s._fallback_title() == "帮我修登录页的 bug"


def test_fallback_title_prefers_summary_needs():
    """压缩后回退标题取摘要里的用户诉求,而不是末尾的只言片语。"""
    s = Session()
    s.add_user_message(content=f"{SUMMARY_PREFIX}\n## 用户需求\n帮我调研工具系统\n\n## 当前进度\n推进中")
    s.add_user_message(content="收尾")
    assert s._fallback_title() == "帮我调研工具系统"


def test_fallback_title_empty():
    """无真实用户消息时返回空串。"""
    s = Session()
    s.add_user_message(content=f"{SYSTEM_PREFIX}通知")
    assert s._fallback_title() == ""


# ── Session.generate_title ─────────────────────────────────────


async def test_generate_title_prompt_covers_whole_conversation():
    """标题提示词覆盖开头用户诉求,而非只剩最后一段。"""
    s = Session()
    s.add_user_message(content="帮我调研 UniClaw 的工具系统架构")
    s.add_assistant_message(content="这是很长的过程说明" * 200, model_name="m", usage={})
    s.add_user_message(content="继续")
    s.add_assistant_message(content="做完了", model_name="m", usage={})

    captured = {}

    async def fake_achat(system_prompt, session, **kwargs):
        captured["system"] = system_prompt
        captured["prompt"] = session._messages[0].content
        return AIMessage(content="工具系统调研", model_name="m", usage=Usage.from_dict({}))

    with patch("uniclaw.provider.fallback.achat", new=AsyncMock(side_effect=fake_achat)):
        title = await s.generate_title(config=AsyncMock(mini_model_name="mini"))

    assert title == "工具系统调研"
    assert "帮我调研 UniClaw 的工具系统架构" in captured["prompt"]
    assert "做完了" in captured["prompt"]
    assert "整个对话" in captured["system"]


async def test_generate_title_prompt_is_bounded():
    """摘录总量有界,来源再长也不会撑爆标题模型的上下文。"""
    s = Session()
    for i in range(200):
        s.add_user_message(content=f"第{i}条用户消息" + "x" * 100)
        s.add_assistant_message(content="回复" + "y" * 100, model_name="m", usage={})

    captured = {}

    async def fake_achat(system_prompt, session, **kwargs):
        captured["prompt"] = session._messages[0].content
        return AIMessage(content="标题", model_name="m", usage=Usage.from_dict({}))

    with patch("uniclaw.provider.fallback.achat", new=AsyncMock(side_effect=fake_achat)):
        await s.generate_title(config=AsyncMock(mini_model_name="mini"))

    assert len(captured["prompt"]) <= 8200


async def test_generate_title_after_compaction_keeps_original_needs():
    """压缩后以 _messages 取材,原始诉求经摘要需求段保留,不只剩末尾。"""
    s = Session()
    s.add_user_message(content="最初的关键需求")
    for i in range(10):
        s.add_assistant_message(content=f"过程{i}" * 50, model_name="m", usage={})
    # 模拟压缩后的上下文窗口:只剩摘要 + 末尾
    s._messages[:] = [
        _user(f"{SUMMARY_PREFIX}\n## 用户需求\n最初的关键需求\n\n## 当前进度\n推进中"),
        _assistant(SUMMARY_ACK_CONTENT),
        _user("收尾"),
    ]

    captured = {}

    async def fake_achat(system_prompt, session, **kwargs):
        captured["prompt"] = session._messages[0].content
        return AIMessage(content="标题", model_name="m", usage=Usage.from_dict({}))

    with patch("uniclaw.provider.fallback.achat", new=AsyncMock(side_effect=fake_achat)):
        await s.generate_title(config=AsyncMock(mini_model_name="mini"))

    assert "最初的关键需求" in captured["prompt"]
    assert "推进中" not in captured["prompt"]
    assert "[用户]: 收尾" in captured["prompt"]
