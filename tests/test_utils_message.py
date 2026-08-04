"""tests for src/uniclaw/utils/message.py"""

from uniclaw.utils.message import MessageRole, build_context_summary, extract_text


# ── MessageRole ──────────────────────────────────────────────────


class TestMessageRole:
    def test_values(self):
        assert MessageRole.SYSTEM == "system"
        assert MessageRole.USER == "user"
        assert MessageRole.ASSISTANT == "assistant"
        assert MessageRole.TOOL == "tool"

    def test_is_string(self):
        assert isinstance(MessageRole.USER, str)


# ── extract_text ─────────────────────────────────────────────────


class TestExtractText:
    def test_plain_string(self):
        assert extract_text("hello") == "hello"

    def test_multimodal_list(self):
        content = [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "http://img"}},
            {"type": "text", "text": "world"},
        ]
        assert extract_text(content) == "hello world"

    def test_multimodal_custom_separator(self):
        content = [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]
        assert extract_text(content, separator=",") == "a,b"

    def test_empty_list(self):
        assert extract_text([]) == ""

    def test_no_text_items(self):
        content = [{"type": "image_url", "image_url": {"url": "x"}}]
        assert extract_text(content) == ""

    def test_non_string_non_list(self):
        assert extract_text(42) == "42"

    def test_none_input(self):
        assert extract_text(None) == "None"

    def test_list_with_non_dict_items(self):
        content = [{"type": "text", "text": "a"}, "not a dict", {"type": "text", "text": "b"}]
        assert extract_text(content) == "a b"


# ── build_context_summary ────────────────────────────────────────


class TestBuildContextSummary:
    def test_empty_messages(self):
        assert build_context_summary([]) == ""

    def test_basic_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = build_context_summary(messages)
        assert "[user]: hello" in result
        assert "[assistant]: hi there" in result

    def test_filters_by_role(self):
        messages = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        result = build_context_summary(messages)
        assert "system" not in result
        assert "[user]: hello" in result

    def test_custom_roles(self):
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
            {"role": "tool", "content": "tool result"},
        ]
        result = build_context_summary(messages, roles=(MessageRole.TOOL,))
        assert "tool result" in result
        assert "user" not in result

    def test_max_messages(self):
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "msg2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "msg4"},
        ]
        result = build_context_summary(messages, max_messages=2)
        assert "msg1" not in result
        assert "msg2" not in result
        assert "msg3" in result
        assert "msg4" in result

    def test_max_chars(self):
        messages = [
            {"role": "user", "content": "a" * 100},
            {"role": "assistant", "content": "b" * 100},
        ]
        result = build_context_summary(messages, max_chars=120)
        # first message (100 chars) fits, second should be truncated or skipped
        assert "[user]: " + "a" * 100 in result
        # second message exceeds remaining budget
        assert "b" * 100 not in result

    def test_max_chars_small_remaining_skips(self):
        """When remaining chars < 50, the message is skipped entirely."""
        messages = [
            {"role": "user", "content": "x" * 80},
            {"role": "assistant", "content": "y" * 80},
        ]
        result = build_context_summary(messages, max_chars=100)
        # first message uses 80, remaining 20 < 50, so second is skipped
        assert "y" not in result

    def test_multimodal_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "http://img"}},
                ],
            },
        ]
        result = build_context_summary(messages)
        assert "describe this" in result

    def test_empty_content_skipped(self):
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "  "},
            {"role": "user", "content": "real message"},
        ]
        result = build_context_summary(messages)
        # empty/whitespace-only messages should be filtered out
        lines = result.strip().split("\n")
        assert len(lines) == 1
        assert "real message" in lines[0]
