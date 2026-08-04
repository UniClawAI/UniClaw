"""tests for src/uniclaw/utils/frontmatter.py"""

from uniclaw.utils.frontmatter import (
    _fix_yaml,
    parse_frontmatter,
    write_frontmatter,
)


# ── parse_frontmatter ────────────────────────────────────────────


class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\ntitle: Hello\ncount: 42\n---\nBody text"
        metadata, body = parse_frontmatter(content)
        assert metadata == {"title": "Hello", "count": 42}
        assert body == "Body text"

    def test_no_frontmatter(self):
        content = "Just some text without frontmatter"
        metadata, body = parse_frontmatter(content)
        assert metadata == {}
        assert body == content

    def test_empty_string(self):
        metadata, body = parse_frontmatter("")
        assert metadata == {}
        assert body == ""

    def test_whitespace_only(self):
        metadata, body = parse_frontmatter("   \n  ")
        assert metadata == {}
        assert body == "   \n  "

    def test_empty_frontmatter(self):
        # Note: empty frontmatter (no content between --- markers) doesn't match
        # the regex, so it's treated as no frontmatter
        content = "---\n---\nBody"
        metadata, body = parse_frontmatter(content)
        assert metadata == {}
        assert body == content

    def test_frontmatter_with_list(self):
        content = "---\ntags:\n  - python\n  - tutorial\n---\nBody"
        metadata, body = parse_frontmatter(content)
        assert metadata == {"tags": ["python", "tutorial"]}
        assert body == "Body"

    def test_frontmatter_with_nested_dict(self):
        content = "---\nmeta:\n  author: test\n  version: 1\n---\nBody"
        metadata, body = parse_frontmatter(content)
        assert metadata == {"meta": {"author": "test", "version": 1}}

    def test_malformed_yaml_with_colon_in_value(self):
        content = '---\ndescription: text with: colon\n---\nBody'
        metadata, body = parse_frontmatter(content)
        # _fix_yaml should handle this
        assert "description" in metadata
        assert body == "Body"

    def test_frontmatter_with_unicode(self):
        content = "---\ntitle: 中文标题\n---\n正文内容"
        metadata, body = parse_frontmatter(content)
        assert metadata == {"title": "中文标题"}
        assert body == "正文内容"


# ── _fix_yaml ────────────────────────────────────────────────────


class TestFixYaml:
    def test_colon_in_value(self):
        line = "description: text with: colon"
        fixed = _fix_yaml(line)
        assert '"text with: colon"' in fixed

    def test_already_quoted_value(self):
        line = 'description: "already quoted"'
        fixed = _fix_yaml(line)
        assert fixed == line

    def test_single_quoted_value(self):
        line = "description: 'already quoted'"
        fixed = _fix_yaml(line)
        assert fixed == line

    def test_list_item_skipped(self):
        line = "  - item with: colon"
        fixed = _fix_yaml(line)
        assert fixed == line

    def test_empty_line(self):
        fixed = _fix_yaml("")
        assert fixed == ""

    def test_no_colon_in_value(self):
        line = "title: simple value"
        fixed = _fix_yaml(line)
        assert fixed == line

    def test_multiple_lines(self):
        content = "title: ok\ndescription: has: colon\nother: fine"
        fixed = _fix_yaml(content)
        lines = fixed.split("\n")
        assert lines[0] == "title: ok"
        assert '"has: colon"' in lines[1]
        assert lines[2] == "other: fine"


# ── write_frontmatter ────────────────────────────────────────────


class TestWriteFrontmatter:
    def test_basic(self):
        result = write_frontmatter({"title": "Hello"}, "Body")
        assert result.startswith("---\n")
        assert "title: Hello" in result
        assert result.endswith("---\nBody")

    def test_empty_metadata(self):
        result = write_frontmatter({}, "Body")
        assert result == "Body"

    def test_no_body(self):
        result = write_frontmatter({"key": "val"})
        assert result.startswith("---\n")
        assert result.endswith("---\n")

    def test_unicode_metadata(self):
        result = write_frontmatter({"title": "中文"}, "正文")
        assert "中文" in result
        assert "正文" in result

    def test_list_metadata(self):
        result = write_frontmatter({"tags": ["a", "b"]})
        assert "a" in result
        assert "b" in result

    def test_nested_metadata(self):
        result = write_frontmatter({"meta": {"x": 1}})
        assert "x: 1" in result


# ── round-trip ───────────────────────────────────────────────────


class TestRoundTrip:
    def test_write_then_parse(self):
        original_meta = {"title": "Test", "tags": ["a", "b"]}
        original_body = "Hello world"
        written = write_frontmatter(original_meta, original_body)
        meta, body = parse_frontmatter(written)
        assert meta["title"] == original_meta["title"]
        assert set(meta["tags"]) == set(original_meta["tags"])
        assert body == original_body

    def test_write_then_parse_unicode(self):
        original_meta = {"title": "中文标题", "desc": "描述"}
        written = write_frontmatter(original_meta, "正文")
        meta, body = parse_frontmatter(written)
        assert meta == original_meta
        assert body == "正文"
