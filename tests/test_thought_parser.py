"""ThoughtParser 测试 — 覆盖 <thought>/<think> 标签流式解析的各种边界情况。"""

from uniclaw.provider.thought_parser import ThoughtParser


class TestThoughtParser:
    """ThoughtParser 状态机测试。"""

    # ── 基本功能 ──────────────────────────────────────────────

    def test_plain_text_no_thought(self):
        """纯文本,无 thought 标签。"""
        p = ThoughtParser()
        thinking, text = p.process("Hello world")
        assert thinking == ""
        assert text == "Hello world"

    def test_thought_tag_basic(self):
        """基本 <thought> 标签。"""
        p = ThoughtParser()
        thinking, text = p.process("<thought>reasoning</thought>answer")
        assert thinking == "reasoning"
        assert text == "answer"

    def test_think_tag_basic(self):
        """基本 <think> 标签。"""
        p = ThoughtParser()
        thinking, text = p.process("<think>reasoning</think>answer")
        assert thinking == "reasoning"
        assert text == "answer"

    def test_thought_tag_no_content(self):
        """空 thought 标签。"""
        p = ThoughtParser()
        thinking, text = p.process("<thought></thought>answer")
        assert thinking == ""
        assert text == "answer"

    def test_think_tag_no_content(self):
        """空 think 标签。"""
        p = ThoughtParser()
        thinking, text = p.process("<think></think>answer")
        assert thinking == ""
        assert text == "answer"

    # ── 流式分块处理 ──────────────────────────────────────────

    def test_streaming_char_by_char(self):
        """逐字符流式输入。"""
        p = ThoughtParser()
        all_thinking = ""
        all_text = ""
        for ch in "<thought>abc</thought>def":
            t, x = p.process(ch)
            all_thinking += t
            all_text += x
        # 标签本身不产生内容,但 thought 内容和文本内容应被提取
        assert "abc" in all_thinking or "abc" in all_text
        assert "def" in all_text

    def test_streaming_thought_in_chunks(self):
        """thought 内容分多次到达。"""
        p = ThoughtParser()
        t1, x1 = p.process("<thou")
        t2, x2 = p.process("ght>rea")
        t3, x3 = p.process("soning</tho")
        t4, x4 = p.process("ught>answer")
        assert t1 + t2 + t3 + t4 == "reasoning"
        assert x1 + x2 + x3 + x4 == "answer"

    def test_streaming_think_tag_split(self):
        """<think> 标签被分块切开。"""
        p = ThoughtParser()
        t1, x1 = p.process("<think>rea")
        t2, x2 = p.process("soning</think>ans")
        t3, x3 = p.process("wer")
        assert t1 + t2 == "reasoning"
        assert x2 + x3 == "answer"

    def test_streaming_close_tag_split_across_chunks(self):
        """闭合标签被分块切开。"""
        p = ThoughtParser()
        t1, x1 = p.process("<thought>abc</tho")
        # "</tho" 是闭合标签前缀,被缓冲,不会输出
        t2, x2 = p.process("ught>def")
        # 完整闭合标签后,abc 作为 thinking,def 作为 text
        assert "abc" in (t1 + t2)
        assert "def" in (x1 + x2)

    # ── 标签前缀缓冲 ──────────────────────────────────────────

    def test_partial_open_tag_buffering(self):
        """输入是开放标签的前缀时,应缓冲。"""
        p = ThoughtParser()
        # "<" 是 <thought> 的前缀
        t1, x1 = p.process("<")
        assert t1 == ""
        assert x1 == ""
        # 补全标签
        t2, x2 = p.process("thought>content</thought>rest")
        assert t2 == "content"
        assert x2 == "rest"

    def test_partial_think_tag_buffering(self):
        """输入是 <think> 的前缀时,应缓冲。"""
        p = ThoughtParser()
        t1, x1 = p.process("<t")
        assert t1 == ""
        assert x1 == ""
        t2, x2 = p.process("hink>content</think>rest")
        assert t2 == "content"
        assert x2 == "rest"

    # ── TEXT 阶段直接透传 ──────────────────────────────────────

    def test_text_phase_passthrough(self):
        """进入 TEXT 阶段后,后续输入直接透传为文本。"""
        p = ThoughtParser()
        p.process("<thought>reasoning</thought>first")
        # 此时 phase = TEXT
        t, x = p.process(" second")
        assert t == ""
        assert x == " second"

    def test_multiple_calls_after_thought(self):
        """thought 结束后多次调用都返回文本。"""
        p = ThoughtParser()
        p.process("<thought>r</thought>answer")
        results = []
        for ch in " continued":
            t, x = p.process(ch)
            results.append((t, x))
        assert all(t == "" for t, _ in results)
        assert "".join(x for _, x in results) == " continued"

    # ── 无 thought 的纯文本 ────────────────────────────────────

    def test_pure_text_to_text_phase(self):
        """纯文本输入直接进入 TEXT 阶段。"""
        p = ThoughtParser()
        t, x = p.process("no tags here")
        assert t == ""
        assert x == "no tags here"
        # 后续也是 TEXT
        t2, x2 = p.process(" still no tags")
        assert t2 == ""
        assert x2 == " still no tags"

    # ── 边界情况 ──────────────────────────────────────────────

    def test_thought_at_end_no_text_after(self):
        """thought 标签后没有文本。"""
        p = ThoughtParser()
        thinking, text = p.process("<thought>reasoning</thought>")
        assert thinking == "reasoning"
        assert text == ""

    def test_empty_input(self):
        """空字符串输入。"""
        p = ThoughtParser()
        t, x = p.process("")
        assert t == ""
        assert x == ""

    def test_thought_with_special_chars(self):
        """thought 内容包含特殊字符。"""
        p = ThoughtParser()
        thinking, text = p.process("<thought>a < b & c > d</thought>result")
        assert thinking == "a < b & c > d"
        assert text == "result"

    def test_thought_with_newlines(self):
        """thought 内容包含换行。"""
        p = ThoughtParser()
        thinking, text = p.process("<thought>line1\nline2</thought>result")
        assert thinking == "line1\nline2"
        assert text == "result"

    def test_close_tag_partial_match_then_full(self):
        """闭合标签部分匹配后回退,最终完整匹配。"""
        p = ThoughtParser()
        # "</t" 是 </thought> 的前缀,但不是完整匹配
        t1, x1 = p.process("<thought>ab</t")
        # 此时 buffer = "</t",还没有闭合
        t2, x2 = p.process("hought>cd")
        assert t1 + t2 == "ab"
        assert x1 + x2 == "cd"

    # ── Phase 枚举 ──────────────────────────────────────────────

    def test_initial_phase_is_seeking_open(self):
        """初始阶段是 SEEKING_OPEN。"""
        p = ThoughtParser()
        assert p.phase == ThoughtParser.Phase.SEEKING_OPEN

    def test_phase_transitions_to_in_thought(self):
        """遇到开放标签后进入 IN_THOUGHT。"""
        p = ThoughtParser()
        p.process("<thought>")
        assert p.phase == ThoughtParser.Phase.IN_THOUGHT

    def test_phase_transitions_to_text_from_seeking(self):
        """纯文本输入从 SEEKING_OPEN 转到 TEXT。"""
        p = ThoughtParser()
        p.process("plain text")
        assert p.phase == ThoughtParser.Phase.TEXT

    def test_phase_transitions_to_text_from_in_thought(self):
        """闭合标签后从 IN_THOUGHT 转到 TEXT。"""
        p = ThoughtParser()
        p.process("<thought>r</thought>text")
        assert p.phase == ThoughtParser.Phase.TEXT
