"""tests for src/uniclaw/utils/format.py"""

import json

from uniclaw.utils.format import (
    format_args_for_display,
    parse_json_from_llm,
    sanitize_progress_line,
)


# ── parse_json_from_llm ──────────────────────────────────────────


class TestParseJsonFromLlm:
    def test_plain_json(self):
        data = {"key": "value", "num": 42}
        assert parse_json_from_llm(json.dumps(data)) == data

    def test_json_markdown_block(self):
        text = '```json\n{"a": 1}\n```'
        assert parse_json_from_llm(text) == {"a": 1}

    def test_plain_markdown_block(self):
        text = '```\n{"b": 2}\n```'
        assert parse_json_from_llm(text) == {"b": 2}

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n```json\n{"x": 10}\n```\nDone.'
        assert parse_json_from_llm(text) == {"x": 10}

    def test_bare_braces(self):
        text = 'The answer is {"name": "test"} ok?'
        assert parse_json_from_llm(text) == {"name": "test"}

    def test_empty_string(self):
        assert parse_json_from_llm("") is None

    def test_none_input(self):
        assert parse_json_from_llm(None) is None

    def test_non_string_input(self):
        assert parse_json_from_llm(123) is None

    def test_invalid_json(self):
        assert parse_json_from_llm("{broken") is None

    def test_json_array_returns_none(self):
        # parse_json_from_llm only returns dict, not list
        assert parse_json_from_llm("[1, 2, 3]") is None

    def test_nested_json(self):
        data = {"outer": {"inner": [1, 2]}}
        assert parse_json_from_llm(json.dumps(data)) == data

    def test_json_in_text_without_code_block(self):
        text = 'Result: {"status": "ok"} from the tool'
        assert parse_json_from_llm(text) == {"status": "ok"}


# ── sanitize_progress_line ───────────────────────────────────────


class TestSanitizeProgressLine:
    def test_no_carriage_return(self):
        assert sanitize_progress_line("hello") == "hello"

    def test_carriage_return_takes_last_frame(self):
        line = "frame1\rframe2\rframe3"
        assert sanitize_progress_line(line) == "frame3"

    def test_crlf_treated_as_newline(self):
        line = "line1\r\nline2"
        assert sanitize_progress_line(line) == "line1\nline2"

    def test_multiple_lines_with_cr(self):
        line = "a1\ra2\nb1\rb2"
        assert sanitize_progress_line(line) == "a2\nb2"

    def test_empty_string(self):
        assert sanitize_progress_line("") == ""

    def test_only_carriage_returns(self):
        line = "\r\r"
        assert sanitize_progress_line(line) == ""


# ── format_args_for_display ──────────────────────────────────────


class TestFormatArgsForDisplay:
    def test_empty_dict(self):
        assert format_args_for_display({}) == ""

    def test_single_arg(self):
        assert format_args_for_display({"key": "val"}) == "key=val"

    def test_multiple_args(self):
        result = format_args_for_display({"a": 1, "b": 2})
        assert result == "a=1, b=2"

    def test_custom_separator(self):
        result = format_args_for_display({"a": 1, "b": 2}, separator=" | ")
        assert result == "a=1 | b=2"

    def test_long_value_truncated(self):
        long_val = "x" * 150
        result = format_args_for_display({"k": long_val}, max_length=100)
        assert "..." in result
        assert "省略50字符" in result

    def test_multiline_value_truncated(self):
        val = "line1\nline2\nline3"
        result = format_args_for_display({"k": val})
        assert "line1" in result
        assert "省略" in result

    def test_value_at_max_length_no_truncation(self):
        val = "a" * 100
        result = format_args_for_display({"k": val}, max_length=100)
        assert result == f"k={val}"
        assert "..." not in result

    def test_non_string_values(self):
        result = format_args_for_display({"count": 42, "flag": True})
        assert "count=42" in result
        assert "flag=True" in result
