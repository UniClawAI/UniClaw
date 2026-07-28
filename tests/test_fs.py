"""文件操作工具测试。"""

from pathlib import Path

from uniclaw.tools.fs import (
    Read,
    Write,
    Edit,
    Glob,
    _read_preserving_newlines,
    generate_unified_diff,
)

# ── _read_preserving_newlines ─────────────────────────────


def test_read_preserving_newlines(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\r\nline3\rline4", encoding="utf-8", newline="")
    content = _read_preserving_newlines(f)
    assert "line1\n" in content
    assert "line2\r\n" in content


def test_read_preserving_newlines_empty(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    assert _read_preserving_newlines(f) == ""


# ── generate_unified_diff ─────────────────────────────────


def test_generate_unified_diff():
    diff = generate_unified_diff("old\n", "new\n", "test.py")
    assert "--- a/test.py" in diff
    assert "+++ b/test.py" in diff
    assert "-old" in diff
    assert "+new" in diff


def test_generate_unified_diff_no_change():
    diff = generate_unified_diff("same\n", "same\n", "test.py")
    assert diff == ""


def test_generate_unified_diff_context_lines():
    diff = generate_unified_diff("a\nb\nc\n", "a\nb\nd\n", "f.py", context_lines=1)
    assert "@@" in diff


# ── Read tool ─────────────────────────────────────────────


def test_read_basic(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello\nworld\n", encoding="utf-8")
    result = Read.func(file_path=str(f))
    assert "hello" in result
    assert "world" in result


def test_read_with_offset_and_limit(tmp_path):
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    result = Read.func(file_path=str(f), offset=1, limit=2)
    assert "b" in result
    assert "c" in result
    assert "a" not in result
    assert "d" not in result


def test_read_nonexistent(tmp_path):
    result = Read.func(file_path=str(tmp_path / "nope.txt"))
    assert "错误" in result or "未找到" in result


def test_read_directory(tmp_path):
    result = Read.func(file_path=str(tmp_path))
    assert "目录" in result


def test_read_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    result = Read.func(file_path=str(f))
    assert "空文件" in result


# ── Write tool ────────────────────────────────────────────


def test_write_creates_new_file(tmp_path):
    f = tmp_path / "new.txt"
    result = Write.func(file_path=str(f), content="hello\n")
    assert "已创建" in result
    assert f.read_text(encoding="utf-8") == "hello\n"


def test_write_updates_existing(tmp_path):
    f = tmp_path / "existing.txt"
    f.write_text("old\n", encoding="utf-8")
    result = Write.func(file_path=str(f), content="new\n")
    assert "已更新" in result
    assert f.read_text(encoding="utf-8") == "new\n"


def test_write_no_change(tmp_path):
    f = tmp_path / "same.txt"
    f.write_text("same\n", encoding="utf-8", newline="")
    result = Write.func(file_path=str(f), content="same\n")
    assert "无变化" in result


def test_write_creates_parent_dirs(tmp_path):
    f = tmp_path / "a" / "b" / "c.txt"
    Write.func(file_path=str(f), content="deep\n")
    assert f.read_text(encoding="utf-8") == "deep\n"


# ── Edit tool ─────────────────────────────────────────────


def test_edit_basic(tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("hello world\n", encoding="utf-8")
    result = Edit.func(file_path=str(f), old_string="world", new_string="python")
    assert f.read_text(encoding="utf-8") == "hello python\n"


def test_edit_no_match(tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("hello\n", encoding="utf-8")
    result = Edit.func(file_path=str(f), old_string="xyz", new_string="abc")
    assert "未找到" in result or "错误" in result


# ── Glob tool ─────────────────────────────────────────────


def test_glob_basic(tmp_path):
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "b.py").write_text("b", encoding="utf-8")
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")
    result = Glob.func(pattern="*.py", path=str(tmp_path))
    assert "a.py" in result
    assert "b.py" in result
    assert "c.txt" not in result


def test_glob_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.py").write_text("d", encoding="utf-8")
    result = Glob.func(pattern="**/*.py", path=str(tmp_path))
    assert "deep.py" in result


def test_glob_no_match(tmp_path):
    result = Glob.func(pattern="*.xyz", path=str(tmp_path))
    assert "未找到" in result or "No files" in result or result.strip() == ""
