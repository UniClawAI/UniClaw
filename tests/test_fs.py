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


def test_edit_multiple_matches_no_replace_all(tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("aaa bbb aaa\n", encoding="utf-8")
    result = Edit.func(
        file_path=str(f), old_string="aaa", new_string="ccc", replace_all=False
    )
    assert "出现了 2 次" in result


def test_edit_multiple_matches_replace_all(tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("aaa bbb aaa\n", encoding="utf-8")
    result = Edit.func(
        file_path=str(f), old_string="aaa", new_string="ccc", replace_all=True
    )
    assert f.read_text(encoding="utf-8") == "ccc bbb ccc\n"


def test_edit_file_not_found(tmp_path):
    result = Edit.func(
        file_path=str(tmp_path / "nope.txt"), old_string="a", new_string="b"
    )
    assert "未找到" in result


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


def test_glob_sorted(tmp_path):
    (tmp_path / "c.txt").write_text("c", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    result = Glob.func(pattern="*.txt", path=str(tmp_path))
    lines = result.strip().split("\n")
    assert lines == sorted(lines)


def test_glob_empty_dir(tmp_path):
    result = Glob.func(pattern="*", path=str(tmp_path))
    assert "未找到" in result


# ── _allocate_md_output / _cleanup_placeholder (Bug #5 回归) ──


def test_allocate_md_output_simple(tmp_path):
    """正常分配:输出为 同名.md,且占位文件已创建。"""
    from uniclaw.tools.fs import _allocate_md_output

    src = tmp_path / "report.pdf"
    src.write_text("pdf", encoding="utf-8")
    out = _allocate_md_output(src)
    assert out == tmp_path / "report.md"
    assert out.exists()  # 原子占位文件已创建


def test_allocate_md_output_existing_increments(tmp_path):
    """目标已存在(如已有 report.md)时自动递增数字后缀。"""
    from uniclaw.tools.fs import _allocate_md_output

    src = tmp_path / "report.pdf"
    src.write_text("pdf", encoding="utf-8")
    (tmp_path / "report.md").write_text("existing", encoding="utf-8")

    out = _allocate_md_output(src)
    assert out == tmp_path / "report_1.md"
    # 已存在的文件不受影响
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "existing"


def test_allocate_md_output_concurrent_same_stem(tmp_path):
    """同名不同扩展名并发转换时输出路径互不覆盖(Bug #5 核心回归)。"""
    import asyncio
    from uniclaw.tools.fs import _allocate_md_output, _cleanup_placeholder

    csv_src = tmp_path / "data.csv"
    html_src = tmp_path / "data.html"
    csv_src.write_text("a,b\n1,2", encoding="utf-8")
    html_src.write_text("<html>hi</html>", encoding="utf-8")

    async def run():
        # 两个任务几乎同时分配,模拟并发转换
        t1 = asyncio.create_task(asyncio.to_thread(_allocate_md_output, csv_src))
        t2 = asyncio.create_task(asyncio.to_thread(_allocate_md_output, html_src))
        return await asyncio.gather(t1, t2)

    out1, out2 = asyncio.run(run())
    # 两个输出必须互不相同,且同目录、均为 .md
    assert out1 != out2
    assert out1.parent == out2.parent == tmp_path
    assert out1.suffix == out2.suffix == ".md"
    assert set(out1.name) | set(out2.name)  # 有实际文件名
    # 两个占位文件都应存在
    assert out1.exists() and out2.exists()

    # 清理占位
    _cleanup_placeholder(out1)
    _cleanup_placeholder(out2)
    assert not out1.exists() and not out2.exists()


def test_cleanup_placeholder_only_empty(tmp_path):
    """占位清理只删空文件,不误删已写入内容(如转换成功)的文件。"""
    from uniclaw.tools.fs import _allocate_md_output, _cleanup_placeholder

    src = tmp_path / "doc.pdf"
    src.write_text("pdf", encoding="utf-8")
    out = _allocate_md_output(src)

    # 模拟转换成功写入内容
    out.write_text("# 转换结果\n", encoding="utf-8")
    _cleanup_placeholder(out)
    assert out.exists(), "非空文件不应被清理"

    # 空占位应被清理
    out2 = _allocate_md_output(src)
    assert out2 == tmp_path / "doc_1.md"
    _cleanup_placeholder(out2)
    assert not out2.exists()
