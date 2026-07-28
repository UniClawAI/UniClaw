"""
format.py 模块的单元测试

测试格式化工具函数
"""

import pytest
from uniclaw.utils.format import format_args_for_display, parse_json_from_llm


class TestFormatArgsForDisplay:
    """format_args_for_display 函数测试"""

    def test_empty_args(self):
        """测试空参数"""
        result = format_args_for_display({})
        assert result == ""

    def test_none_args(self):
        """测试 None 参数"""
        result = format_args_for_display(None)
        assert result == ""

    def test_single_arg(self):
        """测试单个参数"""
        result = format_args_for_display({"key": "value"})
        assert result == "key=value"

    def test_multiple_args(self):
        """测试多个参数"""
        result = format_args_for_display({"k1": "v1", "k2": "v2"})
        assert "k1=v1" in result
        assert "k2=v2" in result
        assert ", " in result

    def test_custom_separator(self):
        """测试自定义分隔符"""
        result = format_args_for_display({"k1": "v1", "k2": "v2"}, separator=" | ")
        assert "k1=v1" in result
        assert "k2=v2" in result
        assert " | " in result

    def test_long_value_truncation(self):
        """测试长值截断"""
        long_value = "a" * 200
        result = format_args_for_display({"key": long_value}, max_length=50)
        assert "..." in result
        assert "省略" in result
        assert len(result) < len(long_value) + 20

    def test_multiline_value(self):
        """测试多行值"""
        multiline = "line1\nline2\nline3"
        result = format_args_for_display({"key": multiline})
        assert "line1" in result
        assert "..." in result
        assert "省略" in result

    def test_value_with_newline_at_end(self):
        """测试末尾换行的值"""
        value = "content\n"
        result = format_args_for_display({"key": value})
        assert "content" in result
        assert "..." in result

    def test_short_value_no_truncation(self):
        """测试短值不截断"""
        result = format_args_for_display({"key": "short"}, max_length=100)
        assert result == "key=short"

    def test_exact_max_length(self):
        """测试正好最大长度"""
        value = "a" * 50
        result = format_args_for_display({"key": value}, max_length=50)
        assert result == f"key={value}"
        assert "..." not in result

    def test_one_over_max_length(self):
        """测试超过最大长度一个字符"""
        value = "a" * 51
        result = format_args_for_display({"key": value}, max_length=50)
        assert "..." in result

    def test_numeric_values(self):
        """测试数值参数"""
        result = format_args_for_display({"int": 42, "float": 3.14})
        assert "int=42" in result
        assert "float=3.14" in result

    def test_none_value(self):
        """测试 None 值"""
        result = format_args_for_display({"key": None})
        assert result == "key=None"

    def test_boolean_values(self):
        """测试布尔值"""
        result = format_args_for_display({"true": True, "false": False})
        assert "true=True" in result
        assert "false=False" in result

    def test_list_value(self):
        """测试列表值"""
        result = format_args_for_display({"key": [1, 2, 3]})
        assert "key=[1, 2, 3]" in result

    def test_dict_value(self):
        """测试字典值"""
        result = format_args_for_display({"key": {"a": 1}})
        assert "key=" in result
        assert "a" in result

    def test_mixed_multiline_and_long(self):
        """测试同时有多行和超长"""
        value = "first line\n" + "a" * 200
        result = format_args_for_display({"key": value}, max_length=50)
        assert "first line" in result
        assert "..." in result

    def test_custom_max_length(self):
        """测试自定义最大长度"""
        value = "a" * 30
        result = format_args_for_display({"key": value}, max_length=20)
        assert "..." in result
        assert "省略10字符" in result

    def test_multiple_args_with_truncation(self):
        """测试多个参数部分截断"""
        args = {
            "short": "ok",
            "long": "a" * 200,
            "multiline": "line1\nline2",
        }
        result = format_args_for_display(args, max_length=50)
        assert "short=ok" in result
        assert "long=" in result
        assert "multiline=" in result
        assert result.count("...") == 2  # long 和 multiline 被截断


class TestParseJsonFromLlm:
    """parse_json_from_llm 函数测试"""

    def test_pure_json(self):
        """测试纯 JSON 字符串"""
        text = '{"is_safe": true, "explanation": "ok"}'
        result = parse_json_from_llm(text)
        assert result == {"is_safe": True, "explanation": "ok"}

    def test_markdown_json_block(self):
        """测试 markdown json 代码块"""
        text = """这是分析结果：
```json
{"is_safe": false, "explanation": "dangerous"}
```
请小心处理。"""
        result = parse_json_from_llm(text)
        assert result == {"is_safe": False, "explanation": "dangerous"}

    def test_markdown_plain_block(self):
        """测试普通 markdown 代码块"""
        text = """结果如下：
```
{"is_safe": true}
```"""
        result = parse_json_from_llm(text)
        assert result == {"is_safe": True}

    def test_json_with_surrounding_text(self):
        """测试带周围文本的 JSON"""
        text = '根据分析，{"is_safe": true, "explanation": "ok"} 是结论。'
        result = parse_json_from_llm(text)
        assert result == {"is_safe": True, "explanation": "ok"}

    def test_invalid_json(self):
        """测试无效 JSON"""
        assert parse_json_from_llm("not json") is None
        assert parse_json_from_llm("") is None
        assert parse_json_from_llm(None) is None

    def test_non_dict_json(self):
        """测试非字典 JSON"""
        assert parse_json_from_llm("[1, 2, 3]") is None
        assert parse_json_from_llm('"string"') is None

    def test_nested_json(self):
        """测试嵌套 JSON"""
        text = '{"data": {"nested": true}, "status": "ok"}'
        result = parse_json_from_llm(text)
        assert result == {"data": {"nested": True}, "status": "ok"}

    def test_json_with_newlines(self):
        """测试带换行的 JSON"""
        text = """
{
  "is_safe": true,
  "explanation": "ok"
}
"""
        result = parse_json_from_llm(text)
        assert result == {"is_safe": True, "explanation": "ok"}
