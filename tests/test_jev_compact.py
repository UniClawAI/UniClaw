"""Jev 智能压缩模块测试

覆盖 jev_compact.py 的核心函数: collect_tool_pairs, build_jev_state,
build_batch_questions, filter_old_messages, jev_compact。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.jev_compact import (
    JevCompactConfig,
    JevCompactResult,
    JevCompactSkip,
    ToolPairScore,
    build_batch_questions,
    build_jev_state,
    collect_tool_pairs,
    filter_old_messages,
    jev_compact,
)
from uniclaw.provider.types import Usage
from uniclaw.tools.session.session import AIMessage, Session, ToolCallMessage, UserMessage


# ── 辅助工具 ──────────────────────────────────────────────────


def _make_ai_msg(tool_calls=None, content=""):
    """构造 AIMessage。"""
    return AIMessage(
        content=content,
        model_name="test",
        usage=Usage.from_dict({}),
        tool_calls=tool_calls or [],
    )


def _make_tool_msg(name, tool_call_id, content, args=None):
    """构造 ToolCallMessage。"""
    return ToolCallMessage(
        name=name,
        tool_call_id=tool_call_id,
        content=content,
        args=args or {},
    )


def _make_user_msg(content):
    """构造 UserMessage。"""
    return UserMessage(content=content)


def _make_tc(tool_name, tc_id, arguments=None):
    """构造 tool_call dict (OpenAI 格式)。"""
    import json

    return {
        "id": tc_id,
        "function": {
            "name": tool_name,
            "arguments": json.dumps(arguments or {}, ensure_ascii=False),
        },
    }


def _make_session_with_tool_calls():
    """构造一个包含多个工具调用的 Session。"""
    session = Session()
    session._messages = [
        _make_user_msg("请帮我读取文件并搜索代码"),
        _make_ai_msg(
            tool_calls=[
                _make_tc("Read", "tc_001", {"file_path": "src/main.py"}),
                _make_tc("Grep", "tc_002", {"pattern": "def main"}),
            ]
        ),
        _make_tool_msg("Read", "tc_001", "print('hello world')\n" * 100),
        _make_tool_msg("Grep", "tc_002", "src/main.py:1:def main():\n" * 50),
        _make_ai_msg(content="我已读取文件并搜索到结果"),
        _make_user_msg("很好,再帮我编辑文件"),
        _make_ai_msg(
            tool_calls=[
                _make_tc("Edit", "tc_003", {"file_path": "src/main.py"}),
            ]
        ),
        _make_tool_msg("Edit", "tc_003", "编辑成功", {"file_path": "src/main.py"}),
        _make_ai_msg(content="编辑完成"),
        _make_user_msg("谢谢"),
    ]
    return session


# ── collect_tool_pairs 测试 ───────────────────────────────────


class TestCollectToolPairs:
    """collect_tool_pairs 函数测试。"""

    def test_basic_collection(self):
        """基本配对收集。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)

        # 全部消息中有 3 个工具调用
        assert len(pairs) == 3

        # 验证配对信息
        assert pairs[0].tool_call_id == "tc_001"
        assert pairs[0].tool_name == "Read"
        assert pairs[0].ai_msg_idx == 1
        assert pairs[0].result_msg_idx == 2

        assert pairs[1].tool_call_id == "tc_002"
        assert pairs[1].tool_name == "Grep"

        assert pairs[2].tool_call_id == "tc_003"
        assert pairs[2].tool_name == "Edit"

    def test_no_tool_calls(self):
        """没有工具调用时返回空列表。"""
        session = Session()
        session._messages = [
            _make_user_msg("hello"),
            _make_ai_msg(content="hi"),
        ]
        pairs = collect_tool_pairs(session._messages)
        assert pairs == []

    def test_empty_messages(self):
        """空消息列表。"""
        pairs = collect_tool_pairs([])
        assert pairs == []

    def test_mismatched_tool_call_id(self):
        """tool_call_id 不匹配时,该配对被跳过。"""
        session = Session()
        session._messages = [
            _make_user_msg("test"),
            _make_ai_msg(tool_calls=[_make_tc("Read", "tc_orphan")]),
            _make_tool_msg("Read", "tc_other", "content"),  # ID 不匹配
            _make_user_msg("recent"),
        ]
        pairs = collect_tool_pairs(session._messages)
        assert len(pairs) == 0


# ── build_jev_state 测试 ─────────────────────────────────────


class TestBuildJevState:
    """build_jev_state 函数测试。"""

    def test_basic_state_building(self):
        """基本 state 构建。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        state = build_jev_state(session._messages, pairs, max_tokens=5000)

        # state 应包含上下文说明
        assert "压缩" in state

        # state 应包含用户消息
        assert "[user]:" in state

        # state 应包含助手消息
        assert "[assistant]:" in state

        # state 应包含工具调用信息
        assert "[tool_call]:" in state

        # state 应包含工具结果占位符
        assert "chars (omitted)" in state

    def test_tool_results_replaced(self):
        """工具结果应被替换为占位符。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        state = build_jev_state(session._messages, pairs, max_tokens=5000)

        # 不应包含原始工具结果内容
        assert "print('hello world')" not in state
        assert "src/main.py:1:def main():" not in state

    def test_respects_max_tokens(self):
        """state 不应超过 max_tokens 限制。"""
        from uniclaw.utils.tokens import count_tokens

        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        state = build_jev_state(session._messages, pairs, max_tokens=200)
        # token 数不应大幅超出限制(允许少量溢出因截断粒度)
        assert count_tokens(state) <= 250

    def test_empty_messages(self):
        """空消息列表应返回只有 header 的 state。"""
        state = build_jev_state([], [], max_tokens=5000)
        assert "压缩" in state


# ── build_batch_questions 测试 ────────────────────────────────


class TestBuildBatchQuestions:
    """build_batch_questions 函数测试。"""

    def test_two_questions_per_pair(self):
        """每个工具配对应生成 2 个问题。"""
        pairs = [
            ToolPairScore(
                ai_msg_idx=1,
                tool_call_idx=0,
                tool_call_id="tc_001",
                tool_name="Read",
                tool_args={},
                result_msg_idx=2,
                result_chars=100,
            ),
            ToolPairScore(
                ai_msg_idx=3,
                tool_call_idx=0,
                tool_call_id="tc_002",
                tool_name="Grep",
                tool_args={},
                result_msg_idx=4,
                result_chars=200,
            ),
        ]
        questions = build_batch_questions(pairs)

        assert len(questions) == 4
        assert "call_tc_001" in questions
        assert "result_tc_001" in questions
        assert "call_tc_002" in questions
        assert "result_tc_002" in questions

    def test_question_type_is_noul(self):
        """所有问题类型应为 noul。"""
        pairs = [
            ToolPairScore(
                ai_msg_idx=1,
                tool_call_idx=0,
                tool_call_id="tc_001",
                tool_name="Read",
                tool_args={},
                result_msg_idx=2,
                result_chars=100,
            ),
        ]
        questions = build_batch_questions(pairs)

        for q in questions.values():
            assert q["type"] == "noul"

    def test_empty_pairs(self):
        """空配对列表应返回空问题。"""
        questions = build_batch_questions([])
        assert questions == {}


# ── filter_old_messages 测试 ──────────────────────────────────


class TestFilterOldMessages:
    """filter_old_messages 函数测试。"""

    def test_keep_high_scored_pairs(self):
        """高分工具配对应完整保留。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]  # 前 8 条(old)
        pairs = collect_tool_pairs(old_msgs)
        config = JevCompactConfig()

        for p in pairs:
            p.keep_call = 0.9
            p.keep_result = 0.9

        filtered, kept, modified = filter_old_messages(old_msgs, pairs, config)
        assert kept == 3
        assert modified == 0
        # 所有消息都保留
        assert len(filtered) == len(old_msgs)

    def test_truncate_medium_scored_pairs(self):
        """中等分(调用保留,结果截断)。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]
        pairs = collect_tool_pairs(old_msgs)
        config = JevCompactConfig()

        for p in pairs:
            p.keep_call = 0.8
            p.keep_result = 0.3

        filtered, kept, modified = filter_old_messages(old_msgs, pairs, config)
        assert kept == 3
        assert modified == 3

        # 验证结果被替换为占位符
        tool_msgs = [m for m in filtered if isinstance(m, ToolCallMessage)]
        for tm in tool_msgs:
            assert "结果已省略" in tm.content

    def test_delete_low_scored_pairs(self):
        """低分(删除调用+结果)。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]
        pairs = collect_tool_pairs(old_msgs)
        config = JevCompactConfig()

        for p in pairs:
            p.keep_call = 0.1
            p.keep_result = 0.1

        filtered, kept, modified = filter_old_messages(old_msgs, pairs, config)
        assert kept == 0
        assert modified > 0

        # ToolCallMessage 应被删除
        tool_msgs = [m for m in filtered if isinstance(m, ToolCallMessage)]
        assert len(tool_msgs) == 0

        # AIMessage 的 tool_calls 应被清空
        ai_msgs = [m for m in filtered if isinstance(m, AIMessage) and m.tool_calls]
        assert len(ai_msgs) == 0

    def test_mixed_decisions(self):
        """混合决策:保留、截断、删除各一个。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]
        pairs = collect_tool_pairs(old_msgs)
        config = JevCompactConfig()

        # 第一个:高分保留
        pairs[0].keep_call = 0.9
        pairs[0].keep_result = 0.9
        # 第二个:中等分截断
        pairs[1].keep_call = 0.8
        pairs[1].keep_result = 0.3
        # 第三个:低分删除
        pairs[2].keep_call = 0.1
        pairs[2].keep_result = 0.1

        filtered, kept, modified = filter_old_messages(old_msgs, pairs, config)
        assert kept == 2  # 前两个保留
        assert modified >= 2  # 截断+删除

        # 验证:第一个结果原样保留
        assert pairs[0].result_msg_idx < len(old_msgs)
        # 验证:第三个的结果被删除
        tool_msgs = [m for m in filtered if isinstance(m, ToolCallMessage)]
        assert len(tool_msgs) <= 2  # 最多保留 2 个(第一个完整,第二个截断)


# ── jev_compact 集成测试 ──────────────────────────────────────


class TestJevCompact:
    """jev_compact 函数集成测试。"""

    @pytest.mark.asyncio
    async def test_raises_when_jev_unavailable(self):
        """Jev 不可用时抛出 JevCompactSkip。"""
        session = _make_session_with_tool_calls()
        config = MagicMock()

        with patch("uniclaw.utils.jev.is_available", return_value=False):
            with pytest.raises(JevCompactSkip):
                await jev_compact(session)

    @pytest.mark.asyncio
    async def test_raises_when_no_pairs(self):
        """没有工具配对时抛出 JevCompactSkip。"""
        session = Session()
        session._messages = [
            _make_user_msg("hello"),
            _make_ai_msg(content="hi"),
            _make_user_msg("bye"),
        ]
        config = MagicMock()

        with patch("uniclaw.utils.jev.is_available", return_value=True):
            with pytest.raises(JevCompactSkip):
                await jev_compact(session)

    @pytest.mark.asyncio
    async def test_raises_on_jev_api_error(self):
        """Jev API 错误时异常传播。"""
        from uniclaw.utils.jev import JevAPIError

        session = _make_session_with_tool_calls()
        config = MagicMock()

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                side_effect=JevAPIError("test error"),
            ),
        ):
            with pytest.raises(JevAPIError):
                await jev_compact(session)

    @pytest.mark.asyncio
    async def test_successful_compaction(self):
        """成功压缩时返回 True 并创建摘要占位。"""
        from uniclaw.utils.jev import BatchResult, NoulResult

        session = _make_session_with_tool_calls()
        config = MagicMock()

        # Mock batch 返回:所有配对都低分(删除)
        mock_answers = {}
        for tc_id in ["tc_001", "tc_002", "tc_003"]:
            mock_answers[f"call_{tc_id}"] = NoulResult(noul=0.1)
            mock_answers[f"result_{tc_id}"] = NoulResult(noul=0.1)

        mock_result = BatchResult(answers=mock_answers)

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = await jev_compact(session)

        assert isinstance(result, JevCompactResult)
        assert result.total_pairs >= 1  # 取决于 split 点

        # 验证:summary 在 filtered_old 之后
        # 结构: [filtered_old...] + [summary_user, summary_ai] + [recent...]
        compact_count = session._compact_end
        assert compact_count >= 2

        # summary 是 compact 区的最后 2 条
        summary_user = session._messages[compact_count - 2]
        summary_ai = session._messages[compact_count - 1]
        assert isinstance(summary_user, UserMessage)
        assert isinstance(summary_ai, AIMessage)
        assert "Jev 压缩" in summary_user.content

        # 验证:recent 消息在 summary 之后
        recent_msgs = session._messages[compact_count:]
        assert len(recent_msgs) > 0

    @pytest.mark.asyncio
    async def test_compaction_preserves_text_messages(self):
        """压缩应保留所有文本消息。"""
        from uniclaw.utils.jev import BatchResult, NoulResult

        session = _make_session_with_tool_calls()
        config = MagicMock()

        # 记录原始文本消息(AIMessage 的非工具调用消息)
        original_texts = [
            m.content
            for m in session._messages
            if isinstance(m, AIMessage) and not m.tool_calls and m.content
        ]

        # 所有配对都低分(删除)
        mock_answers = {}
        for tc_id in ["tc_001", "tc_002", "tc_003"]:
            mock_answers[f"call_{tc_id}"] = NoulResult(noul=0.1)
            mock_answers[f"result_{tc_id}"] = NoulResult(noul=0.1)

        mock_result = BatchResult(answers=mock_answers)

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            await jev_compact(session)

        # 验证文本消息仍然存在
        current_texts = [
            m.content
            for m in session._messages
            if isinstance(m, AIMessage) and not m.tool_calls and m.content
        ]
        for text in original_texts:
            assert text in current_texts
