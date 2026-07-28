"""
frontmatter 模块的单元测试
"""

import pytest
from uniclaw.utils.frontmatter import parse_frontmatter, write_frontmatter


class TestParseFrontmatter:
    """parse_frontmatter 函数的测试类"""

    # ==================== 边界条件测试 ====================

    def test_empty_string(self):
        """测试空字符串"""
        metadata, body = parse_frontmatter("")
        assert metadata == {}
        assert body == ""

    def test_none_input(self):
        """测试None输入"""
        metadata, body = parse_frontmatter(None)
        assert metadata == {}
        assert body is None

    def test_no_frontmatter(self):
        """测试没有 frontmatter 的文本"""
        text = "This is just plain text without frontmatter."
        metadata, body = parse_frontmatter(text)
        assert metadata == {}
        assert body == text

    def test_only_whitespace(self):
        """测试只有空白字符的文本"""
        text = "   \n\t\n  "
        metadata, body = parse_frontmatter(text)
        assert metadata == {}
        assert body == text

    # ==================== 基本解析测试 ====================

    def test_simple_key_value(self):
        """测试简单的键值对"""
        content = "---\ntitle: Hello World\n---\nBody text"
        metadata, body = parse_frontmatter(content)

        assert metadata == {"title": "Hello World"}
        assert body == "Body text"

    def test_multiple_key_values(self):
        """测试多个键值对"""
        content = """---
title: My Post
date: 2024-01-01
author: John Doe
---
Content here"""
        metadata, body = parse_frontmatter(content)

        assert metadata["title"] == "My Post"
        # PyYAML 会将日期解析为 datetime.date 对象
        assert str(metadata["date"]) == "2024-01-01"
        assert metadata["author"] == "John Doe"
        assert body == "Content here"

    def test_numeric_values(self):
        """测试数字类型的值"""
        content = """---
count: 42
price: 19.99
---
Body"""
        metadata, body = parse_frontmatter(content)

        assert metadata["count"] == 42
        assert isinstance(metadata["count"], int)
        assert metadata["price"] == 19.99
        assert isinstance(metadata["price"], float)

    def test_boolean_values(self):
        """测试布尔值"""
        content = """---
published: true
draft: false
---
Body"""
        metadata, body = parse_frontmatter(content)

        assert metadata["published"] is True
        assert metadata["draft"] is False

    def test_null_values(self):
        """测试空值"""
        content = """---
empty: null
tilde: ~
---
Body"""
        metadata, body = parse_frontmatter(content)

        assert metadata["empty"] is None
        assert metadata["tilde"] is None

    # ==================== 列表测试 ====================

    def test_simple_list(self):
        """测试简单列表"""
        content = """---
tags:
  - python
  - tutorial
  - beginner
---
Body"""
        metadata, body = parse_frontmatter(content)

        assert metadata["tags"] == ["python", "tutorial", "beginner"]
        assert isinstance(metadata["tags"], list)

    def test_mixed_content_with_list(self):
        """测试包含列表和其他键值对的混合内容"""
        content = """---
title: My Article
tags:
  - python
  - programming
author: Jane
---
Article content"""
        metadata, body = parse_frontmatter(content)

        assert metadata["title"] == "My Article"
        assert metadata["tags"] == ["python", "programming"]
        assert metadata["author"] == "Jane"
        assert body == "Article content"

    def test_empty_list(self):
        """测试空列表"""
        content = """---
items:
---
Body"""
        metadata, body = parse_frontmatter(content)

        # PyYAML 将空值解析为 None，这是 YAML 标准行为
        assert metadata["items"] is None

    # ==================== 字符串处理测试 ====================

    def test_quoted_strings(self):
        """测试带引号的字符串"""
        content = """---
message: "Hello, World!"
greeting: 'Hi there'
---
Body"""
        metadata, body = parse_frontmatter(content)

        assert metadata["message"] == "Hello, World!"
        assert metadata["greeting"] == "Hi there"

    def test_special_characters_in_value(self):
        """测试包含特殊字符的值"""
        content = """---
url: https://example.com/path?query=value
description: A:B:C
---
Body"""
        metadata, body = parse_frontmatter(content)

        assert metadata["url"] == "https://example.com/path?query=value"
        assert metadata["description"] == "A:B:C"

    # ==================== 复杂场景测试 ====================

    def test_multiline_body(self):
        """测试多行正文"""
        content = """---
title: Test
---
Line 1
Line 2
Line 3"""
        metadata, body = parse_frontmatter(content)

        assert metadata["title"] == "Test"
        assert body == "Line 1\nLine 2\nLine 3"

    def test_empty_body(self):
        """测试空正文"""
        content = """---
title: Only Frontmatter
---
"""
        metadata, body = parse_frontmatter(content)

        assert metadata["title"] == "Only Frontmatter"
        assert body == ""

    def test_complex_metadata(self):
        """测试复杂的元数据结构"""
        content = """---
title: Complete Guide
date: 2024-01-15
published: true
views: 1234
rating: 4.5
tags:
  - guide
  - advanced
categories:
  - programming
  - tutorials
author: John Doe
summary: null
---
This is the article body."""
        metadata, body = parse_frontmatter(content)

        assert metadata["title"] == "Complete Guide"
        # PyYAML 会将日期解析为 datetime.date 对象
        assert str(metadata["date"]) == "2024-01-15"
        assert metadata["published"] is True
        assert metadata["views"] == 1234
        assert metadata["rating"] == 4.5
        assert metadata["tags"] == ["guide", "advanced"]
        assert metadata["categories"] == ["programming", "tutorials"]
        assert metadata["author"] == "John Doe"
        assert metadata["summary"] is None
        assert body == "This is the article body."

    def test_yes_no_as_boolean(self):
        """测试yes/no作为布尔值"""
        content = """---
enabled: yes
disabled: no
---
Body"""
        metadata, body = parse_frontmatter(content)

        assert metadata["enabled"] is True
        assert metadata["disabled"] is False


class TestWriteFrontmatter:
    """write_frontmatter 函数的测试类"""

    # ==================== 边界条件测试 ====================

    def test_empty_metadata(self):
        """测试空元数据"""
        result = write_frontmatter({})
        assert result == ""

    def test_empty_metadata_with_body(self):
        """测试空元数据但有正文"""
        result = write_frontmatter({}, "Body text")
        assert result == "Body text"

    def test_none_metadata(self):
        """测试None元数据"""
        result = write_frontmatter(None)
        assert result == ""

    # ==================== 基本写入测试 ====================

    def test_simple_key_value(self):
        """测试简单的键值对"""
        metadata = {"title": "Hello World"}
        result = write_frontmatter(metadata)

        assert result.startswith("---\n")
        assert "title: Hello World" in result
        assert result.endswith("---\n")

    def test_multiple_key_values(self):
        """测试多个键值对"""
        metadata = {"title": "My Post", "author": "John Doe", "date": "2024-01-01"}
        result = write_frontmatter(metadata)

        assert "title: My Post" in result
        assert "author: John Doe" in result
        # PyYAML 会对包含连字符的字符串添加单引号，这是合法的 YAML 格式
        assert "date:" in result
        assert "2024-01-01" in result

    def test_with_body(self):
        """测试带正文的输出"""
        metadata = {"title": "Test"}
        body = "This is the content."
        result = write_frontmatter(metadata, body)

        assert result == "---\ntitle: Test\n---\nThis is the content."

    # ==================== 数据类型测试 ====================

    def test_integer_value(self):
        """测试整数类型"""
        metadata = {"count": 42}
        result = write_frontmatter(metadata)

        assert "count: 42" in result

    def test_float_value(self):
        """测试浮点数类型"""
        metadata = {"price": 19.99}
        result = write_frontmatter(metadata)

        assert "price: 19.99" in result

    def test_boolean_values(self):
        """测试布尔值"""
        metadata = {"published": True, "draft": False}
        result = write_frontmatter(metadata)

        assert "published: true" in result
        assert "draft: false" in result

    def test_none_value(self):
        """测试None值"""
        metadata = {"empty": None}
        result = write_frontmatter(metadata)

        assert "empty: null" in result

    def test_list_value(self):
        """测试列表类型"""
        metadata = {"tags": ["python", "tutorial"]}
        result = write_frontmatter(metadata)

        assert "tags:" in result

    def test_empty_list(self):
        """测试空列表"""
        metadata = {"items": []}
        result = write_frontmatter(metadata)

        assert "items: []" in result

    # ==================== 特殊字符测试 ====================

    def test_string_with_special_chars(self):
        """测试包含特殊字符的字符串"""
        metadata = {"url": "https://example.com"}
        result = write_frontmatter(metadata)

        # PyYAML 会自动处理 URL，可能不加引号（如果不需要）
        assert "url:" in result
        assert "https://example.com" in result

    def test_complex_metadata(self):
        """测试复杂的元数据"""
        metadata = {
            "title": "Complete Guide",
            "date": "2024-01-15",
            "published": True,
            "views": 1234,
            "rating": 4.5,
            "tags": ["guide", "advanced"],
            "author": "John Doe",
            "summary": None,
        }
        result = write_frontmatter(metadata)

        assert "title: Complete Guide" in result
        # PyYAML 可能会添加引号，但内容应该存在
        assert "2024-01-15" in result
        assert "published: true" in result
        assert "views: 1234" in result
        assert "rating: 4.5" in result
        assert "guide" in result
        assert "advanced" in result
        assert "author: John Doe" in result
        assert "summary: null" in result


class TestRoundTrip:
    """测试解析和写入的往返一致性"""

    def test_simple_round_trip(self):
        """测试简单的往返转换"""
        original = """---
title: Hello World
author: John
---
Body content"""

        metadata, body = parse_frontmatter(original)
        reconstructed = write_frontmatter(metadata, body)

        # 再次解析应该得到相同结果
        metadata2, body2 = parse_frontmatter(reconstructed)

        assert metadata == metadata2
        assert body == body2

    def test_complex_round_trip(self):
        """测试复杂的往返转换"""
        original = """---
title: Complex Document
date: 2024-01-15
published: true
views: 100
tags:
  - python
  - testing
---
Document body here."""

        metadata, body = parse_frontmatter(original)
        reconstructed = write_frontmatter(metadata, body)

        metadata2, body2 = parse_frontmatter(reconstructed)

        assert metadata == metadata2
        assert body == body2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
