"""Jev 智能压缩模块测试

覆盖 jev_compact.py 的核心函数: collect_tool_pairs, build_jev_state,
build_batch_questions, filter_old_messages, jev_compact,
以及 Session 侧的分割点配对对齐与 snip_old_tool_results 复制改写。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.jev_compact import (
    JevCompactConfig,
    JevCompactResult,
    JevCompactSkip,
    ToolPairScore,
    _result_metrics,
    _truncate,
    build_batch_questions,
    build_jev_state,
    collect_tool_pairs,
    filter_old_messages,
    jev_compact,
)
from uniclaw.provider.types import Usage
from uniclaw.tools.session.session import (
    AIMessage,
    SUMMARY_PREFIX,
    MultimodalBlock,
    MultimodalType,
    Session,
    SessionNote,
    ToolCallMessage,
    UserMessage,
)


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

    def test_result_irreplaceable_flag(self):
        """可再生工具(Read/Grep)标记 False,不可重跑取回的 Edit 标记 True。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        by_id = {p.tool_call_id: p for p in pairs}

        assert by_id["tc_001"].result_irreplaceable is False  # Read 可重跑
        assert by_id["tc_002"].result_irreplaceable is False  # Grep 可重跑
        assert by_id["tc_003"].result_irreplaceable is True  # Edit 无法取回原文

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
        state, visible_nums = build_jev_state(session._messages, pairs, max_tokens=5000)

        # state 应包含上下文说明
        assert "压缩" in state

        # state 应包含用户消息
        assert "[user]:" in state

        # state 应包含助手消息
        assert "[assistant]:" in state

        # state 应包含带编号的工具调用信息
        assert "[tool_call #1]:" in state

        # state 应包含带编号的工具结果占位符
        assert "[tool_result #1]:" in state
        assert "chars (omitted)" in state

        # 预算充足时全部配对可见
        assert visible_nums == frozenset({1, 2, 3})

    def test_tool_call_numbers_follow_pairs_order(self):
        """工具调用/结果编号应按 pairs 列表顺序标注。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        state, visible_nums = build_jev_state(session._messages, pairs, max_tokens=5000)

        # _make_session_with_tool_calls 生成 tc_001/002/003 -> #1/#2/#3
        for n in (1, 2, 3):
            assert f"[tool_call #{n}]:" in state
            assert f"[tool_result #{n}]:" in state

    def test_numbers_align_with_questions(self):
        """state 的 #N 标签应与 build_batch_questions 的 #N 一一对应。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        state, visible_nums = build_jev_state(session._messages, pairs, max_tokens=5000)
        questions = build_batch_questions(pairs, only_nums=visible_nums)

        for n, pair in enumerate(pairs, 1):
            assert f"[tool_call #{n}]:" in state
            assert f"[tool_call #{n}]" in questions[f"call_{pair.tool_call_id}"]["instructions"]
            assert f"[tool_result #{n}]" in questions[f"result_{pair.tool_call_id}"]["instructions"]

    def test_tool_results_replaced(self):
        """工具结果应被替换为占位符。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        state, _ = build_jev_state(session._messages, pairs, max_tokens=5000)

        # 不应包含原始工具结果内容
        assert "print('hello world')" not in state
        assert "src/main.py:1:def main():" not in state

    def test_respects_max_tokens(self):
        """state 不应超过 max_tokens 限制。"""
        from uniclaw.utils.tokens import count_tokens

        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        state, _ = build_jev_state(session._messages, pairs, max_tokens=200)
        # token 数不应大幅超出限制(允许少量溢出因截断粒度)
        assert count_tokens(state) <= 250

    def test_empty_messages(self):
        """空消息列表应返回只有 header 的 state。"""
        state, visible_nums = build_jev_state([], [], max_tokens=5000)
        assert "压缩" in state
        assert visible_nums == frozenset()

    def test_budget_drops_oldest_first(self):
        """预算不足时先丢最旧内容,较新消息优先保留。"""
        msgs = [_make_user_msg(f"消息编号 {i:02d} " + "内容" * 30) for i in range(20)]
        state, _ = build_jev_state(msgs, [], max_tokens=300)

        assert "消息编号 19" in state
        assert "消息编号 00" not in state

    def test_budget_selection_is_contiguous_suffix(self):
        """预算不足时选取必须是从最新往旧的连续后缀,不允许中间挖洞。

        judge 的 keepCall 判断依赖后续上下文(是否被后续调用取代、是否影响后续决策)。
        单元大小不均时,贪心"装不下就跳过继续装更旧的"会留下
        "旧的在场、中间缺席"的时间空洞 — 此用例专门钉死该行为。
        """
        msgs = [
            _make_user_msg("旧的小消息 " + "x" * 20),
            _make_user_msg("中间的大消息 " + "y" * 400),
            _make_user_msg("新的中消息 " + "z" * 80),
        ]
        # 预算约容纳两条小/中消息:贪心挖洞时会出现 old=在 mid=不在
        state, _ = build_jev_state(msgs, [], max_tokens=220, max_tool_input=500)

        in_old = "旧的小消息" in state
        in_mid = "中间的大消息" in state
        in_new = "新的中消息" in state

        assert in_new, "最新内容必须优先入选"
        assert not (in_old and not in_mid), (
            f"时间空洞: 旧的在场而中间缺席 (old={in_old}, mid={in_mid}, new={in_new})"
        )
        assert not (in_mid and not in_new), (
            f"时间空洞: 中间在场而最新缺席 (old={in_old}, mid={in_mid}, new={in_new})"
        )

    def test_pair_labels_are_atomic(self):
        """[tool_call #N] 与 [tool_result #N] 同进同出,不出现半截配对。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)

        for budget in (150, 250, 400, 800, 5000):
            state, visible_nums = build_jev_state(
                session._messages, pairs, max_tokens=budget
            )
            for n in (1, 2, 3):
                has_call = f"[tool_call #{n}]:" in state
                has_result = f"[tool_result #{n}]:" in state
                assert has_call == has_result, (
                    f"预算 {budget} 下配对 #{n} 的 call/result 标签不一致"
                )
                assert (n in visible_nums) == has_call

    def test_visible_nums_restricts_questions(self):
        """only_nums 只为 state 中可见的配对生成问题,编号仍按全量顺序。"""
        session = _make_session_with_tool_calls()
        pairs = collect_tool_pairs(session._messages)
        questions = build_batch_questions(pairs, only_nums=frozenset({2}))

        assert len(questions) == 2
        assert "call_tc_002" in questions
        assert "result_tc_002" in questions
        assert "call_tc_001" not in questions
        # 编号仍是全量顺序的 #2,不因过滤而重新编号
        assert "[tool_call #2]" in questions["call_tc_002"]["instructions"]

    def test_user_text_truncated_keeps_head_and_tail(self):
        """超长用户文本截断保留头尾,任务目标与最新补充都可见。"""
        long_text = "目标:修复登录bug。" + "中间过程" * 500 + "补充:还要兼容移动端。"
        session = Session()
        session._messages = [_make_user_msg(long_text)]
        state, _ = build_jev_state(session._messages, [], max_tokens=5000)

        assert "目标:修复登录bug。" in state
        assert "补充:还要兼容移动端。" in state
        assert "省略" in state

    def test_truncate_head_tail_unit(self):
        """_truncate 超长时保留头尾并在中段标注省略字数。"""
        text = "A" * 100 + "B" * 100
        out = _truncate(text, 50)
        assert out.startswith("A")
        assert out.endswith("B")
        assert "省略" in out


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

    def test_focus_hint_included(self):
        """focus 非空时,问题指令应包含聚焦提示。"""
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
        questions = build_batch_questions(pairs, focus="网络错误")

        assert len(questions) == 2
        for q in questions.values():
            assert "网络错误" in q["instructions"]

    def test_no_focus_hint_by_default(self):
        """focus 为空时,问题指令不含聚焦提示。"""
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
            assert "更应保留" not in q["instructions"]

    def test_result_instruction_covers_keep_categories(self):
        """result 问题指令必须覆盖应保留的关键类别,防止改回封闭三例清单。

        旧提示词把「代码片段、错误信息、具体数据值」写成准必要条件,
        会导致用户答复、状态清单、路径 URL、散文事实等被误删。
        """
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
        result_instr = questions["result_tc_001"]["instructions"]

        for category in ("代码", "报错", "数据值", "路径", "URL", "用户给出的答复", "状态或清单", "配置语义"):
            assert category in result_instr, f"result 指令缺少关键类别: {category}"
        # 明确「否」的后果是占位降级而非删除调用
        assert "占位" in result_instr
        # 承认 judge 看不到结果原文,避免自相矛盾的内容属性提问
        assert "不可见" in result_instr

    def test_call_instruction_has_keep_and_drop_branches(self):
        """call 问题指令必须同时给出保留与丢弃分支,避免默认偏向保留。"""
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
        call_instr = questions["call_tc_001"]["instructions"]

        assert "则回答是" in call_instr
        assert "则回答否" in call_instr

    def test_same_name_tools_disambiguated_by_number(self):
        """同名工具多次调用必须靠 #N 编号区分,并带上参数预览。"""
        pairs = [
            ToolPairScore(
                ai_msg_idx=1,
                tool_call_idx=0,
                tool_call_id="tc_a",
                tool_name="Read",
                tool_args={"file_path": "src/a.py"},
                result_msg_idx=2,
                result_chars=100,
            ),
            ToolPairScore(
                ai_msg_idx=3,
                tool_call_idx=0,
                tool_call_id="tc_b",
                tool_name="Read",
                tool_args={"file_path": "src/b.py"},
                result_msg_idx=4,
                result_chars=200,
            ),
        ]
        questions = build_batch_questions(pairs)

        assert "[tool_call #1]" in questions["call_tc_a"]["instructions"]
        assert "[tool_call #2]" in questions["call_tc_b"]["instructions"]
        assert "src/a.py" in questions["call_tc_a"]["instructions"]
        assert "src/b.py" in questions["call_tc_b"]["instructions"]


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
            # 低于两个 keep_result 阈值(可再生 0.7 / 不可再生 0.3),三个配对都降级
            p.keep_result = 0.2

        filtered, kept, modified = filter_old_messages(old_msgs, pairs, config)
        # kept/modified 互斥: 截断只计入 modified
        assert kept == 0
        assert modified == 3
        assert kept + modified == len(pairs)

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
        # kept/modified 互斥: 仅完整保留的配对计入 kept
        assert kept == 1
        assert modified == 2
        assert kept + modified == len(pairs)

        # 验证:第一个结果原样保留
        assert pairs[0].result_msg_idx < len(old_msgs)
        # 验证:第三个的结果被删除
        tool_msgs = [m for m in filtered if isinstance(m, ToolCallMessage)]
        assert len(tool_msgs) <= 2  # 最多保留 2 个(第一个完整,第二个截断)

    def test_filter_does_not_mutate_original_messages(self):
        """改写必须复制消息 — 原对象(history 共享)不能被就地修改。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]
        # _messages 与 history 共享消息对象
        history = list(old_msgs)
        orig_tool_contents = [
            m.content for m in history if isinstance(m, ToolCallMessage)
        ]
        orig_tool_calls = [
            list(m.tool_calls) for m in history if isinstance(m, AIMessage) and m.tool_calls
        ]

        pairs = collect_tool_pairs(old_msgs)
        config = JevCompactConfig()
        pairs[0].keep_call, pairs[0].keep_result = 0.9, 0.9  # keep
        pairs[1].keep_call, pairs[1].keep_result = 0.8, 0.3  # truncate
        pairs[2].keep_call, pairs[2].keep_result = 0.1, 0.1  # delete

        filter_old_messages(old_msgs, pairs, config)

        after_tool_contents = [
            m.content for m in history if isinstance(m, ToolCallMessage)
        ]
        after_tool_calls = [
            list(m.tool_calls) for m in history if isinstance(m, AIMessage) and m.tool_calls
        ]
        assert after_tool_contents == orig_tool_contents
        assert after_tool_calls == orig_tool_calls

    def test_default_scores_fail_safe_keep(self):
        """未评分(默认 1.0)的配对应完整保留,绝不静默删除。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]
        pairs = collect_tool_pairs(old_msgs)
        config = JevCompactConfig()

        filtered, kept, modified = filter_old_messages(old_msgs, pairs, config)
        assert kept == len(pairs)
        assert modified == 0
        assert len(filtered) == len(old_msgs)


# ── 阈值策略 ──────────────────────────────────────────────────


class TestThresholdPolicy:
    """阈值按误留/误删代价不对称设定的回归测试。"""

    def test_config_defaults_reflect_cost_asymmetry(self):
        """调用便宜删了疼 → 低阈值;可再生结果可重跑 → 高阈值;不可再生 → 显著更低。"""
        config = JevCompactConfig()
        assert config.keep_call_threshold == 0.3
        assert config.keep_result_threshold == 0.7
        assert config.keep_result_threshold_irreplaceable == 0.5
        assert config.keep_result_threshold_irreplaceable < config.keep_result_threshold

    def test_ambiguous_call_is_kept_not_deleted(self):
        """keep_call 落在模糊带 [0.3, 0.5) 时保留调用(stub 结果),不再整对删除。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]
        pairs = collect_tool_pairs(old_msgs)
        for p in pairs:
            p.keep_call = 0.4  # 旧阈值 0.5 下会被整对删除
            p.keep_result = 0.1

        filtered, kept, modified = filter_old_messages(old_msgs, pairs, JevCompactConfig())
        assert kept == 0
        assert modified == 3
        # 三个调用全部保留为 stub,没有整对删除
        tool_msgs = [m for m in filtered if isinstance(m, ToolCallMessage)]
        assert len(tool_msgs) == 3
        for tm in tool_msgs:
            assert "结果已省略" in tm.content

    def test_irreplaceable_result_kept_at_lower_threshold(self):
        """不可再生结果 keep_result=0.5(不确定带 [0.5, 0.7),低于 0.7)必须保全文。

        阈值若高于 0.5,此处会被 stub — 多媒体/不可重跑结果即永久丢失。
        """
        msgs = [
            _make_user_msg("看下截图"),
            _make_ai_msg(tool_calls=[_make_tc("ReadMedia", "tc_m")]),
            _make_tool_msg(
                "ReadMedia",
                "tc_m",
                [
                    MultimodalBlock(
                        type=MultimodalType.image_url,
                        image_url={"url": "data:image/png;base64,xxxx"},
                    )
                ],
            ),
        ]
        pairs = collect_tool_pairs(msgs)
        assert pairs[0].result_irreplaceable is True
        pairs[0].keep_call = 0.5
        pairs[0].keep_result = 0.5

        filtered, kept, modified = filter_old_messages(msgs, pairs, JevCompactConfig())
        assert kept == 1
        assert modified == 0
        assert len(filtered) == len(msgs)

    def test_replaceable_result_stubbed_at_same_score(self):
        """可再生结果同样 0.5 分(<0.7)降为占位 — 两个阈值的差异必须生效。"""
        session = _make_session_with_tool_calls()
        old_msgs = session._messages[:8]
        pairs = collect_tool_pairs(old_msgs)
        for p in pairs:
            p.keep_call, p.keep_result = 0.9, 0.9
        pairs[0].keep_result = 0.5  # Read,可重跑取回

        assert pairs[0].result_irreplaceable is False
        filtered, kept, modified = filter_old_messages(old_msgs, pairs, JevCompactConfig())
        assert kept == 2
        assert modified == 1

        read_stub = [
            m for m in filtered if isinstance(m, ToolCallMessage) and m.name == "Read"
        ][0]
        assert "结果已省略" in read_stub.content


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
            patch.object(Session, "_find_split_point", return_value=8),
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
            patch.object(Session, "_find_split_point", return_value=8),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = await jev_compact(session)

        assert isinstance(result, JevCompactResult)
        assert result.total_pairs == 3
        assert result.kept == 0
        assert result.modified == 3
        assert result.tokens_saved > 0

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
            patch.object(Session, "_find_split_point", return_value=8),
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

    @pytest.mark.asyncio
    async def test_missing_answers_fail_safe_keep(self):
        """Jev 缺答/未返回评分时 fail-safe 保留,绝不静默删除。"""
        from uniclaw.utils.jev import BatchResult

        session = _make_session_with_tool_calls()
        mock_result = BatchResult(answers={})

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch.object(Session, "_find_split_point", return_value=8),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            result = await jev_compact(session)

        assert result.total_pairs == 3
        assert result.kept == 3
        assert result.modified == 0

    @pytest.mark.asyncio
    async def test_summary_uses_summary_prefix_and_recall_hint(self):
        """摘要消息必须用 SUMMARY_PREFIX 并携带历史检索提示,否则 recall 失效。"""
        from uniclaw.utils.jev import BatchResult, NoulResult

        session = _make_session_with_tool_calls()
        # 模拟 add_* 行为: history 与 _messages 共享对象
        session.history = list(session._messages)

        mock_result = BatchResult(
            answers={
                "call_tc_001": NoulResult(noul=0.1),
                "result_tc_001": NoulResult(noul=0.1),
                "call_tc_002": NoulResult(noul=0.1),
                "result_tc_002": NoulResult(noul=0.1),
                "call_tc_003": NoulResult(noul=0.1),
                "result_tc_003": NoulResult(noul=0.1),
            }
        )

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch.object(Session, "_find_split_point", return_value=8),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            await jev_compact(session)

        summary_user = session._messages[session._compact_end - 2]
        assert summary_user.content.startswith(SUMMARY_PREFIX)
        # 历史检索提示(archive > 0 时注入)
        assert "历史上下文" in summary_user.content
        assert "recall_history" in summary_user.content

    @pytest.mark.asyncio
    async def test_summary_includes_session_notes_snapshot(self):
        """会话笔记快照应注入摘要,关键事实跨压缩存活。"""
        from uniclaw.utils.jev import BatchResult, NoulResult

        session = _make_session_with_tool_calls()
        session.history = list(session._messages)
        session.session_notes = [
            SessionNote(name="关键约束", description="禁止修改 config.root_dir 语义", content="...")
        ]

        mock_result = BatchResult(answers={})

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch.object(Session, "_find_split_point", return_value=8),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            await jev_compact(session)

        summary_user = session._messages[session._compact_end - 2]
        assert "会话笔记" in summary_user.content
        assert "关键约束" in summary_user.content

    @pytest.mark.asyncio
    async def test_jev_does_not_mutate_history(self):
        """Jev 压缩后 history 中的原消息对象必须原样保留。"""
        from uniclaw.utils.jev import BatchResult, NoulResult

        session = _make_session_with_tool_calls()
        session.history = list(session._messages)
        orig_tool_contents = [
            m.content for m in session.history if isinstance(m, ToolCallMessage)
        ]

        mock_result = BatchResult(
            answers={
                "call_tc_001": NoulResult(noul=0.1),
                "result_tc_001": NoulResult(noul=0.1),
                "call_tc_002": NoulResult(noul=0.8),
                "result_tc_002": NoulResult(noul=0.3),
                "call_tc_003": NoulResult(noul=0.9),
                "result_tc_003": NoulResult(noul=0.9),
            }
        )

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch.object(Session, "_find_split_point", return_value=8),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            await jev_compact(session)

        after_tool_contents = [
            m.content for m in session.history if isinstance(m, ToolCallMessage)
        ]
        assert after_tool_contents == orig_tool_contents


# ── 多媒体结果 (问题5) ─────────────────────────────────────────


class TestMultimodalResults:
    """多媒体工具结果的度量、state 占位、问题描述与 stub 文案。"""

    def test_result_metrics_str(self):
        """纯文本结果。"""
        chars, has_media = _result_metrics("x" * 100)
        assert chars == 100
        assert has_media is False

    def test_result_metrics_text_blocks(self):
        """文本块列表只计文本字符。"""
        chars, has_media = _result_metrics(
            [{"type": "text", "text": "abc"}, {"type": "text", "text": "de"}]
        )
        assert chars == 5
        assert has_media is False

    def test_result_metrics_multimodal_block(self):
        """MultimodalBlock 图片块计入 has_media,不计入文本字符。"""
        content = [
            MultimodalBlock(type=MultimodalType.text, text="截图如下"),
            MultimodalBlock(
                type=MultimodalType.image_url,
                image_url={"url": "data:image/png;base64,xxxx"},
            ),
        ]
        chars, has_media = _result_metrics(content)
        assert chars == len("截图如下")
        assert has_media is True

    def test_result_metrics_dict_media_block(self):
        """原始 dict 图片块同样计入 has_media。"""
        content = [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
        ]
        chars, has_media = _result_metrics(content)
        assert chars == len("看图")
        assert has_media is True

    def test_state_placeholder_marks_media(self):
        """state 中多媒体结果占位符标注含多媒体。"""
        session = Session()
        session._messages = [
            _make_user_msg("看下截图"),
            _make_ai_msg(tool_calls=[_make_tc("ReadMedia", "tc_m")]),
            _make_tool_msg(
                "ReadMedia",
                "tc_m",
                [
                    MultimodalBlock(
                        type=MultimodalType.image_url,
                        image_url={"url": "data:image/png;base64,xxxx"},
                    )
                ],
            ),
        ]
        pairs = collect_tool_pairs(session._messages)
        assert pairs[0].result_has_media is True

        state, visible_nums = build_jev_state(session._messages, pairs, max_tokens=5000)
        assert "含多媒体" in state
        assert visible_nums == frozenset({1})

    def test_question_says_media_not_refetchable(self):
        """多媒体结果的问题描述应说明无法重新获取。"""
        pair = ToolPairScore(
            ai_msg_idx=1,
            tool_call_idx=0,
            tool_call_id="tc_m",
            tool_name="ReadMedia",
            tool_args={},
            result_msg_idx=2,
            result_chars=4,
            result_has_media=True,
        )
        questions = build_batch_questions([pair])
        result_instr = questions["result_tc_m"]["instructions"]
        assert "多媒体" in result_instr
        assert "无法重新获取" in result_instr

    def test_filter_stub_marks_media(self):
        """多媒体结果降级 stub 标注原含多媒体内容。"""
        session = Session()
        msgs = [
            _make_user_msg("看下截图"),
            _make_ai_msg(tool_calls=[_make_tc("ReadMedia", "tc_m")]),
            _make_tool_msg(
                "ReadMedia",
                "tc_m",
                [
                    MultimodalBlock(type=MultimodalType.text, text="识别文字"),
                    MultimodalBlock(
                        type=MultimodalType.image_url,
                        image_url={"url": "data:image/png;base64,xxxx"},
                    ),
                ],
            ),
        ]
        pairs = collect_tool_pairs(msgs)
        pairs[0].keep_call = 0.9
        pairs[0].keep_result = 0.1

        filtered, kept, modified = filter_old_messages(msgs, pairs, JevCompactConfig())
        tool_msgs = [m for m in filtered if isinstance(m, ToolCallMessage)]
        assert len(tool_msgs) == 1
        assert "原含多媒体内容" in tool_msgs[0].content


# ── 分割点配对对齐 (问题2) ─────────────────────────────────────


class TestSplitPointAlignment:
    """_align_split_to_tool_pairs / _find_split_point 不得切断工具配对。"""

    def _make_parallel_session(self):
        """AI@1 并行两个工具调用,结果在 @2/@3 — 切 @2 或 @3 都会断配对。"""
        session = Session()
        session._messages = [
            _make_user_msg("并行读取两个文件"),
            _make_ai_msg(
                tool_calls=[
                    _make_tc("Read", "tc_a", {"file_path": "a.py"}),
                    _make_tc("Read", "tc_b", {"file_path": "b.py"}),
                ]
            ),
            _make_tool_msg("Read", "tc_a", "content-a"),
            _make_tool_msg("Read", "tc_b", "content-b"),
            _make_ai_msg(content="读完了"),
        ]
        return session

    def test_align_moves_back_past_parallel_calls(self):
        """切在并行工具结果中间时,分割点回退到 AIMessage 之前。"""
        session = self._make_parallel_session()
        assert session._align_split_to_tool_pairs(3) == 1
        assert session._align_split_to_tool_pairs(2) == 1

    def test_align_keeps_complete_pairs(self):
        """配对完整落在 old 侧时分割点不动。"""
        session = self._make_parallel_session()
        assert session._align_split_to_tool_pairs(4) == 4
        assert session._align_split_to_tool_pairs(5) == 5

    def test_align_cascades_across_pairs(self):
        """前移分割点可能连锁切断更早配对,需迭代收敛。

        构造重叠配对: tc_1 的结果(index 3)排在 tc_2 的调用(index 2)之后。
        合法协议下不会出现这种交错,但对齐算法必须能收敛。
        """
        session = Session()
        session._messages = [
            _make_user_msg("任务"),
            _make_ai_msg(tool_calls=[_make_tc("Read", "tc_1")]),
            _make_ai_msg(tool_calls=[_make_tc("Read", "tc_2")]),
            _make_tool_msg("Read", "tc_1", "r1"),
            _make_tool_msg("Read", "tc_2", "r2"),
            _make_user_msg("继续"),
        ]
        # 切 4 切断 tc_2 → 回退到 2; 切 2 又切断 tc_1 → 回退到 1
        assert session._align_split_to_tool_pairs(4) == 1

    def test_align_without_tool_calls_is_noop(self):
        """纯文本会话分割点不变。"""
        session = Session()
        session._messages = [
            _make_user_msg("a"),
            _make_ai_msg(content="b"),
            _make_user_msg("c"),
        ]
        assert session._align_split_to_tool_pairs(2) == 2

    def test_find_split_point_returns_aligned_index(self):
        """_find_split_point 的返回值必须落在工具配对边界上。"""
        session = self._make_parallel_session()
        for ratio in (0.1, 0.3, 0.5, 0.7, 0.9):
            split = session._find_split_point(keep_ratio=ratio)
            assert split in (0, 1, 4, 5), f"keep_ratio={ratio} 切断了配对: split={split}"

    @pytest.mark.asyncio
    async def test_jev_on_unaligned_session_does_not_produce_dangling_results(self):
        """即便候选分割点会切断配对,jev 重建后的消息序列也不得出现孤儿 tool_result。"""
        from uniclaw.utils.jev import BatchResult, NoulResult

        session = self._make_parallel_session()
        session.history = list(session._messages)

        mock_result = BatchResult(
            answers={
                "call_tc_a": NoulResult(noul=0.1),
                "result_tc_a": NoulResult(noul=0.1),
                "call_tc_b": NoulResult(noul=0.1),
                "result_tc_b": NoulResult(noul=0.1),
            }
        )

        with (
            patch("uniclaw.utils.jev.is_available", return_value=True),
            patch(
                "uniclaw.utils.jev.batch",
                new_callable=AsyncMock,
                return_value=mock_result,
            ),
        ):
            try:
                await jev_compact(session, keep_ratio=0.3)
            except JevCompactSkip:
                # 分割点对齐后 old 无配对 → 跳过也是安全结果
                return

        # 校验协议合法性: 每个 tool_call_id 的调用与结果同侧,且调用在结果之前
        call_ids: set[str] = set()
        for msg in session._messages:
            if isinstance(msg, AIMessage):
                for tc in msg.tool_calls or []:
                    call_ids.add(tc.get("id", ""))
            elif isinstance(msg, ToolCallMessage):
                assert msg.tool_call_id in call_ids, (
                    f"孤儿 tool_result: {msg.tool_call_id}"
                )


# ── snip_old_tool_results 复制改写 (问题1 同族) ─────────────────


class TestSnipOldToolResults:
    """snip_old_tool_results 不得就地修改 history 共享的消息对象。"""

    def test_snip_does_not_mutate_history_objects(self):
        session = Session()
        tool_msg = _make_tool_msg("Read", "tc_1", "x" * 5000)
        session._messages = [
            _make_user_msg("hi"),
            _make_ai_msg(tool_calls=[_make_tc("Read", "tc_1")]),
            tool_msg,
            _make_ai_msg(content="ok"),
            _make_user_msg("1"),
            _make_ai_msg(content="2"),
            _make_user_msg("3"),
            _make_ai_msg(content="4"),
        ]
        session.history = list(session._messages)
        orig_content = tool_msg.content

        session.snip_old_tool_results(max_chars=200, preserve_last_n_turns=2)

        # 原对象(history 共享)不受影响
        assert tool_msg.content == orig_content
        # _messages 中的副本被改写
        new_msg = session._messages[2]
        assert new_msg is not tool_msg
        assert new_msg.content != orig_content
        assert "已省略" in new_msg.content or "已清除" in new_msg.content

    def test_snip_compactable_tool_uses_clear_stub(self):
        session = Session()
        tool_msg = _make_tool_msg("Grep", "tc_1", "y" * 5000)
        session._messages = [_make_user_msg("x"), tool_msg] + [
            _make_ai_msg(content="ok") for _ in range(6)
        ]

        session.snip_old_tool_results()

        assert "已清除" in session._messages[1].content
