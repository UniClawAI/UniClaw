"""tokenize 分词工具测试 — 覆盖中英文分词的各种情况。"""

from uniclaw.utils.tokenize import tokenize


class TestTokenize:
    """中英文分词测试。"""

    def test_english_words(self):
        """纯英文按单词分词。"""
        result = tokenize("hello world")
        assert "hello" in result
        assert "world" in result

    def test_english_lowercase(self):
        """英文统一转小写。"""
        result = tokenize("Python JavaScript")
        assert "python" in result
        assert "javascript" in result

    def test_chinese_words(self):
        """中文按 jieba 分词。"""
        result = tokenize("代码风格")
        # jieba 会将中文分词
        assert len(result) > 0
        # 应包含中文 token
        cn_tokens = [t for t in result if any("一" <= c <= "鿿" for c in t)]
        assert len(cn_tokens) > 0

    def test_mixed_chinese_english(self):
        """中英文混合。"""
        result = tokenize("Python 代码风格")
        assert "python" in result
        # 应包含中文 token
        cn_tokens = [t for t in result if any("一" <= c <= "鿿" for c in t)]
        assert len(cn_tokens) > 0

    def test_underscore_in_english(self):
        """英文下划线保持为单词的一部分。"""
        result = tokenize("hello_world test_case")
        assert "hello_world" in result
        assert "test_case" in result

    def test_empty_string(self):
        """空字符串返回空列表。"""
        result = tokenize("")
        assert result == []

    def test_punctuation_only(self):
        """纯标点返回空列表。"""
        result = tokenize("!@#$%")
        assert result == []

    def test_numbers_ignored(self):
        """数字不被包含在结果中(正则只匹配字母和下划线)。"""
        result = tokenize("test 123 abc")
        assert "test" in result
        assert "abc" in result
        assert "123" not in result

    def test_single_chinese_char(self):
        """单个中文字符。"""
        result = tokenize("好")
        assert len(result) > 0

    def test_long_english_sentence(self):
        """长英文句子。"""
        result = tokenize("The quick brown fox jumps over the lazy dog")
        assert "the" in result
        assert "quick" in result
        assert "fox" in result

    def test_docstring_example(self):
        """验证 docstring 中的示例。"""
        result = tokenize("Python 代码风格")
        assert "python" in result
        # jieba 分词结果应包含 "代码" 和 "风格"
        assert "代码" in result
        assert "风格" in result
