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


def test_read_binary_png_rejected(tmp_path):
    """PNG 等二进制文件不能按文本读出乱码。"""
    f = tmp_path / "img.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 32)
    result = Read.func(file_path=str(f))
    assert "二进制文件" in result
    assert "PNG" in result
    assert "ReadMedia" in result


def test_read_binary_with_nul_rejected(tmp_path):
    """含 NUL 字节的未知二进制也拒绝。"""
    f = tmp_path / "data.bin"
    f.write_bytes(b"hello\x00world\x00")
    result = Read.func(file_path=str(f))
    assert "二进制文件" in result


def test_read_utf16_text_allowed(tmp_path):
    """UTF-16 文本天然含 NUL, 不应误判为二进制。"""
    f = tmp_path / "u16.txt"
    f.write_text("你好\nworld\n", encoding="utf-16")
    result = Read.func(file_path=str(f), encoding="utf-16")
    assert "二进制" not in result
    assert "你好" in result or "world" in result


def test_read_offset_out_of_range(tmp_path):
    """offset 超界必须报明确信息,不能伪装成空文件(任务 4 回归)。"""
    f = tmp_path / "big.txt"
    f.write_text("".join(f"line{i}\n" for i in range(1, 60001)), encoding="utf-8")
    result = Read.func(file_path=str(f), offset=200000)
    assert "(空文件)" not in result
    assert "offset=200000" in result
    assert "超出文件范围" in result
    assert "60000" in result  # 写明真实行数


def test_read_offset_at_exact_end(tmp_path):
    """offset 正好等于行数也算超界。"""
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    result = Read.func(file_path=str(f), offset=3)
    assert "offset=3" in result
    assert "共 3 行" in result


def test_read_empty_file_still_empty(tmp_path):
    """真真空文件仍返回 (空文件),不被超界逻辑误伤。"""
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    result = Read.func(file_path=str(f))
    assert result == "(空文件)"


def test_read_negative_offset_rejected(tmp_path):
    """负 offset 拒绝(否则会从文件尾往前切,语义混乱)。"""
    f = tmp_path / "a.txt"
    f.write_text("a\nb\n", encoding="utf-8")
    result = Read.func(file_path=str(f), offset=-1)
    assert "offset 不能为负数" in result


def test_read_limit_exceeds_remaining_notes_eof(tmp_path):
    """limit 超过剩余行数时注明已达末尾(任务 5 回归)。"""
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    result = Read.func(file_path=str(f), offset=3, limit=100)
    assert "d" in result and "e" in result
    assert "已达文件末尾" in result
    assert "仅剩 2 行" in result


def test_read_limit_exact_remaining_no_eof_note(tmp_path):
    """limit 恰好等于剩余行数时不加多余提示。"""
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    result = Read.func(file_path=str(f), offset=1, limit=2)
    assert "已达文件末尾" not in result
    assert "b" in result and "c" in result


def test_read_limit_fits_no_eof_note(tmp_path):
    """limit 小于剩余行数时不加提示。"""
    f = tmp_path / "lines.txt"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    result = Read.func(file_path=str(f), offset=0, limit=2)
    assert "已达文件末尾" not in result


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


def test_glob_truncation_not_silent(tmp_path):
    """超过 500 条必须标注截断(任务 2 回归): 不能静默只给 500 条。"""
    from uniclaw.tools.fs import GLOB_MAX_RESULTS

    for i in range(GLOB_MAX_RESULTS + 30):
        (tmp_path / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
    result = Glob.func(pattern="*.txt", path=str(tmp_path))
    lines = result.strip().split("\n")
    body_lines = [l for l in lines if l.startswith(str(tmp_path))]
    assert len(body_lines) == GLOB_MAX_RESULTS
    assert "已截断" in result
    assert f"共匹配 {GLOB_MAX_RESULTS + 30} 条" in result
    assert f"仅显示前 {GLOB_MAX_RESULTS} 条" in result
    assert "收窄 pattern" in result


def test_glob_exactly_max_no_truncation_note(tmp_path):
    """恰好 500 条时不加截断提示。"""
    from uniclaw.tools.fs import GLOB_MAX_RESULTS

    for i in range(GLOB_MAX_RESULTS):
        (tmp_path / f"f{i:04d}.txt").write_text("x", encoding="utf-8")
    result = Glob.func(pattern="*.txt", path=str(tmp_path))
    assert "已截断" not in result
    assert len(result.strip().split("\n")) == GLOB_MAX_RESULTS


# ── ConvertToMarkdown 格式清单一致性(任务 6) ──


def test_convert_docstring_lists_all_supported_formats():
    """docstring(LM schema)必须列出全部 13 种格式,与 _DOCUMENT_FORMATS 一致。

    漏列 .doc/.xls/.ppt 会让 LLM 误判"不支持"而放弃转换。
    """
    import re

    from uniclaw.tools.fs import ConvertToMarkdown, _DOCUMENT_FORMATS

    doc = ConvertToMarkdown.func.__doc__
    assert doc
    assert len(_DOCUMENT_FORMATS) == 13
    for ext in _DOCUMENT_FORMATS:
        name = ext.lstrip(".").upper()
        # 边界匹配: DOC 不能只靠 DOCX 命中
        assert re.search(rf"(?<![A-Z]){name}(?![A-Z])", doc), f"docstring 缺少 {name}"


def test_convert_error_message_shares_format_list(tmp_path):
    """报错文案与 _DOCUMENT_FORMATS 共用,不再两处各写一份。"""
    import asyncio as aio

    from uniclaw.tools.fs import ConvertToMarkdown, _DOCUMENT_FORMATS
    from uniclaw.tools.base import ToolRuntime
    from unittest.mock import MagicMock

    config = MagicMock()
    config.multimodal_model_name = []
    rt = ToolRuntime(config=config)
    bad = tmp_path / "x.unsupported"
    bad.write_text("x", encoding="utf-8")

    async def run():
        return await ConvertToMarkdown(
            file_path=str(bad), output_path="", tool_runtime=rt
        )

    result = aio.run(run())
    assert "不支持的格式" in result
    for ext in _DOCUMENT_FORMATS:
        assert ext in result, f"报错文案缺少 {ext}"


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
