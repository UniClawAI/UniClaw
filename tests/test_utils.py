"""
truncation 模块的单元测试
"""

import re

import pytest
from uniclaw.utils.tokens import count_tokens, slice_by_tokens
from uniclaw.utils.truncation import (
    truncate_text,
    truncate_text_by_lines,
    truncate_text_by_tokens,
)


class TestTruncateText:
    """truncate_text 函数的测试类"""

    # ==================== 边界条件测试 ====================

    def test_empty_string(self):
        """测试空字符串"""
        assert truncate_text("") == ""

    def test_none_input(self):
        """测试 None 输入"""
        assert truncate_text(None) is None

    def test_short_text_within_limit(self):
        """测试短文本,未超过 token 限制"""
        text = "Hello, World!"
        result = truncate_text(text, max_tokens=1000)
        assert result == text

    def test_one_token_over_limit(self):
        """测试文本 token 数刚好超过限制"""
        text = "abcde " * 600  # ~600 tokens
        result = truncate_text(text, max_tokens=500)
        assert "[截断了" in result
        assert "个tokens]" in result

    # ==================== 正常截断测试 ====================

    def test_basic_truncation(self):
        """测试基本截断功能"""
        text = "hello world " * 1000  # ~1000 tokens
        result = truncate_text(text, max_tokens=500, keep_ratio=0.4)

        assert "[截断了" in result
        assert "个tokens]" in result
        # 前面部分被保留
        assert result.startswith("hello world")
        # 后面部分被保留
        assert result.endswith("hello world ")

    def test_truncation_info_format(self):
        """测试截断信息的格式"""
        text = "abcde " * 1000
        result = truncate_text(text, max_tokens=500, keep_ratio=0.4)

        assert "...[截断了" in result
        assert "个tokens]..." in result

    def test_correct_truncated_count(self):
        """测试截断 token 数计算正确"""
        text = "hello world " * 500  # ~500 tokens
        result = truncate_text(text, max_tokens=200, keep_ratio=0.4)

        assert "[截断了" in result
        assert "个tokens]" in result

    # ==================== 参数测试 ====================

    def test_custom_max_tokens(self):
        """测试自定义 max_tokens 参数"""
        text = "abcde " * 2000
        result = truncate_text(text, max_tokens=800, keep_ratio=0.4)
        assert "[截断了" in result

    def test_custom_keep_ratio(self):
        """测试自定义 keep_ratio 参数"""
        text = "hello world " * 500
        result = truncate_text(text, max_tokens=200, keep_ratio=0.5)
        assert "[截断了" in result

    def test_small_keep_ratio(self):
        """测试较小的 keep_ratio"""
        text = "hello world " * 500
        result = truncate_text(text, max_tokens=200, keep_ratio=0.2)
        assert "[截断了" in result

    def test_large_keep_ratio(self):
        """测试较大的 keep_ratio"""
        text = "hello world " * 500
        result = truncate_text(text, max_tokens=200, keep_ratio=0.8)
        assert "[截断了" in result


class TestTruncateTextByLines:
    """truncate_text_by_lines 函数的测试类"""

    # ==================== 边界条件测试 ====================

    def test_empty_string(self):
        """测试空字符串"""
        assert truncate_text_by_lines("") == ""

    def test_none_input(self):
        """测试 None 输入"""
        assert truncate_text_by_lines(None) is None

    def test_short_text_within_limit(self):
        """测试短文本,未超过 token 限制"""
        text = "Hello, World!"
        result = truncate_text_by_lines(text, max_tokens=1000)
        assert result == text

    def test_exactly_at_limit(self):
        """测试文本 token 数正好等于 max_tokens"""
        text = "\n".join([f"Line {i:03d}" for i in range(100)])
        # 设置一个足够大的限制,确保不触发截断
        result = truncate_text_by_lines(text, max_tokens=999999)
        assert result == text

    def test_one_token_over_limit(self):
        """测试文本 token 数刚好超过限制"""
        text = "abcde " * 600  # ~600 tokens
        result = truncate_text_by_lines(text, max_tokens=500)

        assert "[截断了" in result
        assert "个tokens]" in result

    # ==================== 正常截断测试 ====================

    def test_basic_truncation(self):
        """测试基本截断功能"""
        lines = [f"Line {i:03d} content\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        assert "[截断了" in result
        assert "个tokens]" in result
        # 前面部分被保留
        assert "Line 000" in result
        # 后面部分被保留
        assert "Line 199" in result

    def test_truncation_info_format(self):
        """测试截断信息的格式"""
        lines = [f"Line {i:03d} content\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        assert "\n...[截断了" in result
        assert "行," in result
        assert "个tokens]...\n" in result

    def test_correct_truncated_count(self):
        """测试截断行数和 token 数计算正确"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        assert "行," in result
        assert "个tokens]" in result

    # ==================== 参数测试 ====================

    def test_custom_max_tokens(self):
        """测试自定义 max_tokens 参数"""
        lines = [f"Line {i:03d} content here\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        assert "[截断了" in result
        assert "个tokens]" in result

    def test_custom_keep_ratio(self):
        """测试自定义 keep_ratio 参数"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.5)

        assert "[截断了" in result
        assert "个tokens]" in result

    def test_small_keep_ratio(self):
        """测试较小的 keep_ratio"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.2)
        assert "[截断了" in result

    def test_large_keep_ratio(self):
        """测试较大的 keep_ratio"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.8)
        assert "[截断了" in result

    # ==================== 行结尾处理测试 ====================

    def test_preserve_line_endings(self):
        """测试保留行结尾符"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        # 前面部分应该保留换行符
        assert "Line 000\n" in result

    def test_mixed_line_endings(self):
        """测试混合行结尾符(\n 和 \r\n)"""
        text = "Line 000\nLine 001\r\nLine 002\nLine 003\r\n" + "\n".join(
            [f"Line {i:03d}" for i in range(4, 200)]
        )

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        assert "[截断了" in result

    def test_no_trailing_newline(self):
        """测试最后一行没有换行符的情况"""
        lines = [f"Line {i:03d}\n" for i in range(199)]
        lines.append("Line 199")  # 最后一行没有换行符
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        # 验证最后一行被保留
        assert "Line 199" in result

    # ==================== 特殊场景测试 ====================

    def test_very_long_text(self):
        """测试非常长的文本"""
        lines = [
            f"This is a very long line number {i:04d} with some additional text\n"
            for i in range(1000)
        ]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=500, keep_ratio=0.4)

        assert "[截断了" in result
        assert "line number 0000" in result
        assert "line number 0999" in result

    def test_short_text_not_truncated(self):
        """测试短文本不被截断"""
        text = "Short text\nwith few lines"
        result = truncate_text_by_lines(text, max_tokens=1000)
        assert result == text

    def test_zero_max_tokens(self):
        """测试 max_tokens 为 0 的情况"""
        lines = [f"Line {i:03d}\n" for i in range(10)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=0, keep_ratio=0.4)

        # keep_tokens_per_part = 0, 应该显示截断信息
        assert "[截断了" in result

    def test_result_structure(self):
        """测试返回结果的结构"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        # 结果应该包含三个部分:前面部分、截断信息、后面部分
        parts = result.split("\n...[截断了")
        assert len(parts) == 2

        assert "行," in parts[1]
        assert "个tokens]...\n" in parts[1]

    def test_front_and_back_content(self):
        """测试前后部分内容正确"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        assert "Line 000\n" in result
        assert "Line 199\n" in result

    def test_whitespace_only_lines(self):
        """测试只包含空白字符的行"""
        lines = ["   \n", "\t\n", "  \t  \n"] * 100
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=100, keep_ratio=0.4)

        assert "[截断了" in result

    def test_empty_lines(self):
        """测试空行"""
        lines = ["\n"] * 2000
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=50, keep_ratio=0.4)

        assert "[截断了" in result

    def test_line_boundary_truncation(self):
        """测试截断点在行边界处"""
        lines = [
            f"This is line number {i:03d} with enough content\n" for i in range(100)
        ]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=0.4)

        assert "[截断了" in result

        # 验证所有出现的行号都是完整的(不会出现半行)
        import re

        line_pattern = r"This is line number \d{3} with enough content"
        matches = re.findall(line_pattern, result)
        for match in matches:
            assert len(match) == len("This is line number 000 with enough content")

    # ==================== 回归测试(历史 bug) ====================

    def test_single_long_line_truncated_count_not_negative(self):
        """测试单行超长文本:被截断行数不应为负"""
        text = "x" * 5000

        result = truncate_text_by_lines(text, max_tokens=100)

        m = re.search(r"\[截断了(-?\d+)行,", result)
        assert m is not None
        assert int(m.group(1)) == 1

    def test_tail_not_lost_when_slice_is_single_partial_line(self):
        """测试尾部切片仅含半行+行尾时不应整体丢失"""
        text = "F" * 200 + "\n" + "M" * 3000 + "\n" + "Y" * 300 + "\n"

        result = truncate_text_by_lines(text, max_tokens=200, keep_ratio=0.4)

        marker_end = result.find("个tokens]...\n")
        assert marker_end > 0
        tail = result[marker_end + len("个tokens]...\n") :]
        assert "Y" * 10 in tail

    def test_crlf_line_endings_preserved(self):
        """测试 CRLF 行尾不被 rstrip 破坏"""
        text = "abc\r\ndef\r\n" + "M" * 5000 + "\r\ntail\r\n"

        result = truncate_text_by_lines(text, max_tokens=200, keep_ratio=0.4)

        assert "abc\r\ndef\r\n" in result

    def test_front_trailing_spaces_preserved(self):
        """测试前部行尾空白不被剥离"""
        text = "keep me   \n" + "M" * 5000 + "\n" + "tail\n"

        result = truncate_text_by_lines(text, max_tokens=200, keep_ratio=0.4)

        assert "keep me   " in result

    def test_zero_max_tokens_does_not_keep_content(self):
        """测试 max_tokens=0 时不应保留任何正文(历史 bug:尾部全文保留)"""
        text = "\n".join([f"Line {i:03d}" for i in range(10)])

        result = truncate_text_by_lines(text, max_tokens=0, keep_ratio=0.4)

        assert "[截断了" in result
        assert "Line 000" not in result
        assert "Line 009" not in result

    def test_huge_keep_ratio_no_overlapping_content(self):
        """测试 keep_ratio 过大时前后段不重叠(历史 bug:内容重复)"""
        lines = [f"Line {i:03d}\n" for i in range(200)]
        text = "".join(lines)

        result = truncate_text_by_lines(text, max_tokens=300, keep_ratio=8.0)

        found = re.findall(r"Line \d{3}", result)
        assert len(found) == len(set(found))


class TestSliceByTokens:
    """slice_by_tokens 边界行为的测试类"""

    def test_zero_tokens_from_end_returns_empty(self):
        """测试 max_tokens=0 时取尾部应返回空串(历史 bug: tokens[-0:] 返回全文)"""
        assert slice_by_tokens("AAA\nBBB\nCCC", 0, from_end=True) == ""

    def test_zero_tokens_from_start_returns_empty(self):
        """测试 max_tokens=0 时取头部应返回空串"""
        assert slice_by_tokens("AAA\nBBB\nCCC", 0, from_end=False) == ""

    def test_negative_tokens_returns_empty(self):
        """测试负数 max_tokens 应返回空串"""
        assert slice_by_tokens("AAA\nBBB\nCCC", -5, from_end=True) == ""
        assert slice_by_tokens("AAA\nBBB\nCCC", -5, from_end=False) == ""


class TestTruncateTextByTokens:
    """truncate_text_by_tokens 函数的测试类"""

    def test_empty_text(self):
        assert truncate_text_by_tokens("") == ""

    def test_none_text(self):
        assert truncate_text_by_tokens(None) is None

    def test_short_text_no_truncation(self):
        text = "short text"
        result = truncate_text_by_tokens(text, max_tokens=1000)
        assert result == text
        assert "已截断" not in result

    def test_exact_limit_no_truncation(self):
        text = "a" * 10  # token 数远小于 10,不应截断
        result = truncate_text_by_tokens(text, max_tokens=10)
        assert result == text
        assert "已截断" not in result

    def test_basic_truncation(self):
        text = "hello world " * 200  # 约 200+ tokens
        result = truncate_text_by_tokens(text, max_tokens=50)
        # 保留开头部分 + 截断提示
        assert result.startswith("hello world")
        assert "[已截断 " in result
        assert " 个tokens]" in result

    def test_truncation_info_reports_correct_count(self):
        text = "hello world " * 500  # 约 500+ tokens
        result = truncate_text_by_tokens(text, max_tokens=100)
        m = re.search(r"\[已截断 (\d+) 个tokens\]", result)
        assert m, f"缺少截断 token 数提示,实际输出: {result[:200]}"
        assert int(m.group(1)) > 0

    def test_truncation_count_consistent(self):
        """报告的截断数应与 count_tokens 计算结果一致"""
        text = "hello world " * 500
        result = truncate_text_by_tokens(text, max_tokens=100)
        m = re.search(r"\[已截断 (\d+) 个tokens\]", result)
        assert m
        reported = int(m.group(1))
        # 正文保留部分 = 截断提示之前的内容(分隔符为 "\n\n...[")
        body = result[: result.find("\n\n...[")].rstrip("\n")
        assert reported == count_tokens(text) - count_tokens(body)

    def test_zero_max_tokens(self):
        text = "hello"
        result = truncate_text_by_tokens(text, max_tokens=0)
        assert "[已截断 " in result
        assert " 个tokens]" in result

    def test_negative_max_tokens(self):
        text = "hello"
        result = truncate_text_by_tokens(text, max_tokens=-10)
        assert "[已截断 " in result
        assert " 个tokens]" in result

    def test_multiline_text(self):
        text = "line1\nline2\nline3"
        result = truncate_text_by_tokens(text, max_tokens=2)
        # 保留开头部分
        assert result.startswith("line")
        assert "[已截断 " in result
        assert " 个tokens]" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
