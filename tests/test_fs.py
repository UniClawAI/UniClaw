import pytest
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
    assert len(_DOCUMENT_FORMATS) == 8
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


# ── 任务 1: PPTX 字面转义还原 + 标题顺序 ──────────────────────


def test_restore_literal_escapes_basic():
    """字面 \\n / \\t 还原为真实换行/制表符(任务 1)。"""
    from uniclaw.tools.fs import _restore_literal_escapes

    assert _restore_literal_escapes("a\\nb") == "a\nb"
    assert _restore_literal_escapes("a\\tb") == "a\tb"
    assert _restore_literal_escapes("no escapes here") == "no escapes here"


def test_restore_literal_escapes_keeps_code_and_paths():
    """引号内代码展示与 Windows 路径不能被误伤(任务 1)。"""
    from uniclaw.tools.fs import _restore_literal_escapes

    # 代码里展示的 "\n" 必须原样保留
    assert _restore_literal_escapes('"\\n"') == '"\\n"'
    assert _restore_literal_escapes("'\\n'") == "'\\n'"
    assert _restore_literal_escapes('"print(\\n)"') == '"print(\\n)"'
    # 反斜杠 + n 本身就是两个要显示的字符
    assert _restore_literal_escapes("\\\\n") == "\\\\n"
    # Windows 盘符路径
    assert _restore_literal_escapes("C:\\new") == "C:\\new"
    assert _restore_literal_escapes("C:\\temp") == "C:\\temp"
    # 缩写撇号不当成引号开始
    assert _restore_literal_escapes("don't \\nx") == "don't \nx"


def test_normalize_pptx_text_softbreak_and_ooxml():
    """软换行 \\v 与 OOXML _xHHHH_ 转义还原为真实字符(任务 1)。"""
    from uniclaw.tools.fs import _normalize_pptx_text

    assert _normalize_pptx_text("x\x0by") == "x\ny"
    assert _normalize_pptx_text("a_x000A_b") == "a\nb"
    assert _normalize_pptx_text("a_x000B_b") == "a\nb"
    assert _normalize_pptx_text("") == ""


def test_pptx_output_no_literal_escape_and_title_first(tmp_path):
    """PPTX 产物无字面 \\n,且标题在表格前(任务 1 回归)。"""
    from pptx import Presentation
    from pptx.util import Inches

    from uniclaw.tools.fs import _convert_document

    prs = Presentation()
    # 页 1: 文本框含字面 \\n(拼接泄漏形态)
    s1 = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s1.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(5), Inches(1))
    tb.text_frame.text = "line1" + chr(92) + "nline2"
    # 页 2: 表格坐标在标题上方 —— 旧实现按 (top,left) 排序会把标题排到表格后
    s2 = prs.slides.add_slide(prs.slide_layouts[6])
    tbl = s2.shapes.add_table(2, 2, Inches(0.5), Inches(0.5), Inches(4), Inches(1.2)).table
    tbl.cell(0, 0).text = "H1"
    tbl.cell(0, 1).text = "H2"
    tbl.cell(1, 0).text = "a"
    tbl.cell(1, 1).text = "b"
    tbox = s2.shapes.add_textbox(Inches(0.5), Inches(3.0), Inches(5), Inches(0.8))
    tbox.text_frame.text = "Slide Two Title"
    # 页 3: 真正的标题占位符 + 表格
    s3 = prs.slides.add_slide(prs.slide_layouts[1])
    s3.shapes.title.text = "Page Three Title"
    s3.placeholders[1].text_frame.text = "body text"
    tbl3 = s3.shapes.add_table(2, 2, Inches(0.5), Inches(3.0), Inches(5), Inches(1.2)).table
    tbl3.cell(0, 0).text = "C1"
    tbl3.cell(0, 1).text = "C2"
    tbl3.cell(1, 0).text = "x"
    tbl3.cell(1, 1).text = "y"

    src = tmp_path / "sample.pptx"
    prs.save(src)
    md = _convert_document(src)

    # 字面 \n 已还原为真实换行
    assert "line1\nline2" in md
    assert chr(92) + "n" not in md
    # 标题顺序: 标题在表格前
    assert 0 < md.find("Slide Two Title") < md.find("| H1 | H2 |")
    assert 0 < md.find("# Page Three Title") < md.find("| C1 | C2 |")


def test_pptx_keeps_code_literal_and_windows_path(tmp_path):
    """PPTX 里的代码展示与 Windows 路径不能被转义还原误伤(任务 1)。"""
    from pptx import Presentation
    from pptx.util import Inches

    from uniclaw.tools.fs import _convert_document

    BSN = chr(92) + "n"
    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s1.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(5), Inches(1))
    tb.text_frame.text = 'code: "print(' + BSN + ')" and C:' + chr(92) + "new"
    src = tmp_path / "code.pptx"
    prs.save(src)
    md = _convert_document(src)
    assert '"print(' + BSN + ')"' in md
    assert "C:" + chr(92) + "new" in md


# ── 任务 2: 损坏文件统一报错 ─────────────────────────────────


def test_corrupt_docx_xlsx_pptx_all_error(tmp_path):
    """垃圾内容改 docx/xlsx/pptx 后缀,三个都报错且含"损坏"或"无效"(任务 2)。"""
    import asyncio
    from unittest.mock import MagicMock

    from uniclaw.tools.base import ToolRuntime
    from uniclaw.tools.fs import ConvertToMarkdown, _validate_document_content

    config = MagicMock()
    config.multimodal_model_name = []
    rt = ToolRuntime(config=config)

    for ext in (".docx", ".xlsx", ".pptx"):
        g = tmp_path / f"garbage{ext}"
        g.write_text("this is not a real office file\njust plain text\n", encoding="utf-8")

        # 内容校验层立即报错
        err = _validate_document_content(g)
        assert err is not None, f"{ext} 应未通过内容校验"
        assert ("损坏" in err) or ("无效" in err), f"{ext} 报错缺少损坏/无效: {err}"
        assert "TOOL_ERROR" in err

        # 工具入口同样报错,不静默成功
        async def run(g=g):
            return await ConvertToMarkdown(file_path=str(g), tool_runtime=rt)

        result = asyncio.run(run())
        assert "TOOL_ERROR" in result, f"{ext} 不应静默成功: {result}"
        assert ("损坏" in result) or ("无效" in result), f"{ext} 报错缺少损坏/无效: {result}"


def test_validate_accepts_real_docx(tmp_path):
    """合法 OOXML 不能被误判为损坏(任务 2,防止误伤)。"""
    from docx import Document

    from uniclaw.tools.fs import _validate_document_content

    doc = Document()
    doc.add_heading("Hello", 0)
    doc.add_paragraph("world")
    p = tmp_path / "ok.docx"
    doc.save(p)
    assert _validate_document_content(p) is None


def test_validate_rejects_zip_without_ooxml_part(tmp_path):
    """是 zip 但不是 Office 包(缺 OOXML 部件)也必须报错(任务 2)。"""
    import zipfile

    from uniclaw.tools.fs import _validate_document_content

    p = tmp_path / "fake.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("hello.txt", "hi")
    err = _validate_document_content(p)
    assert err is not None
    assert ("损坏" in err) or ("无效" in err)


# ── 任务 3: utf-8-sig CSV 首格无 BOM ─────────────────────────


def test_csv_utf8_sig_first_cell_no_bom(tmp_path):
    """utf-8-sig 编码 CSV,markdown 表格首格不能残留 U+FEFF(任务 3)。"""
    from uniclaw.tools.fs import _convert_document

    p = tmp_path / "bom.csv"
    # encode("utf-8-sig") 会写入前导 BOM
    p.write_bytes("名字,年龄\n张三,20\n".encode("utf-8-sig"))
    md = _convert_document(p)

    assert "\ufeff" not in md
    first_cell = md.split("|")[1]
    assert first_cell.startswith(" 名字") or first_cell.strip() == "名字"
    assert "名字" in first_cell




# ── EPUB 编码正确性(第 26 轮 #5) ─────────────────────────


def _build_epub(path, chapter_bytes: bytes, opf_encoding: str = "utf-8"):
    """构造最小 EPUB(单章节),章节内容字节可指定编码。"""
    import zipfile as zf

    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        f'<?xml version="1.0" encoding="{opf_encoding}"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
        'unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:title>中文书名</dc:title></metadata>'
        '<manifest><item id="ch1" href="chapter.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="ch1"/></spine></package>'
    ).encode(opf_encoding)
    with zf.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zf.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/chapter.xhtml", chapter_bytes)


def test_epub_utf8_chapter_keeps_chinese(tmp_path):
    """UTF-8 章节转换后中文应原样保留。"""
    from uniclaw.tools.fs import _convert_document

    chapter = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        + "<h1>测试中文段落</h1><p>正文甲乙丙</p>".encode("utf-8")
        + b"</body></html>"
    )
    p = tmp_path / "zh.epub"
    _build_epub(p, chapter)
    md = _convert_document(p)

    assert "测试中文段落" in md
    assert "正文甲乙丙" in md


def test_epub_gbk_chapter_not_mojibake(tmp_path):
    """GBK 编码章节(常见于国产电子书)不能整篇变乱码。"""
    from uniclaw.tools.fs import _convert_document

    chapter = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        + "<h1>测试中文段落</h1><p>正文甲乙丙</p>".encode("gbk")
        + b"</body></html>"
    )
    p = tmp_path / "gbk.epub"
    _build_epub(p, chapter)
    md = _convert_document(p)

    assert "测试中文段落" in md
    assert "正文甲乙丙" in md
    # 乱码特征: 韩文区字符(错误解码的典型产物)
    assert not any("가" <= c <= "힣" for c in md)


def test_epub_metadata_title_present(tmp_path):
    """OPF 元数据里的标题应出现在输出开头。"""
    from uniclaw.tools.fs import _convert_document

    chapter = b"<html><body><p>hi</p></body></html>"
    p = tmp_path / "meta.epub"
    _build_epub(p, chapter)
    md = _convert_document(p)

    assert "中文书名" in md


# ── JSON/XML 结构化转换(第 26 轮 #10) ─────────────────


def test_text_native_formats_are_not_converted(tmp_path):
    """JSON/CSV/XML 是纯文本 —— 提示直接 Read,不做 Markdown 转换。"""
    import asyncio as aio

    from uniclaw.tools.base import ToolRuntime
    from uniclaw.tools.fs import ConvertToMarkdown
    from unittest.mock import MagicMock

    config = MagicMock()
    config.multimodal_model_name = []
    rt = ToolRuntime(config=config)

    async def run(suffix):
        f = tmp_path / f"t{suffix}"
        f.write_text("plain text", encoding="utf-8")
        return await ConvertToMarkdown(file_path=str(f), tool_runtime=rt)

    for suffix in (".json", ".csv", ".xml", ".html", ".htm"):
        result = aio.run(run(suffix))
        assert "纯文本" in result or "Read" in result, f"{suffix}: {result}"


def test_unsupported_format_not_reported_as_corrupt(tmp_path):
    """markitdown 报 UnsupportedFormat 时不能说"文件已损坏"。"""
    from unittest.mock import MagicMock, patch

    import pytest

    from uniclaw.tools.fs import _DocumentConvertError, _convert_with_markitdown

    class UnsupportedFormatException(Exception):
        pass

    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)

    mock_md = MagicMock()
    mock_md.convert.side_effect = UnsupportedFormatException(
        "File extension .doc is not supported"
    )
    with patch("markitdown.MarkItDown", return_value=mock_md):
        with pytest.raises(_DocumentConvertError) as ei:
            _convert_with_markitdown(p, None)

    msg = str(ei.value)
    assert "格式不受支持" in msg
    assert "已损坏" not in msg
    assert "doc" in msg.lower()


# ── 缺依赖与文件损坏分口径(第 27 轮 #6) ───────────────────


def test_missing_dependency_not_reported_as_corrupt(tmp_path):
    """markitdown 报 MissingDependency 时不能说"文件已损坏"。"""
    from unittest.mock import MagicMock, patch

    import pytest

    from uniclaw.tools.fs import _DocumentConvertError, _convert_with_markitdown

    class MissingDependencyException(Exception):
        pass

    p = tmp_path / "real.xls"
    # 真实 OLE 魔数(文件结构完好),失败原因只可能是缺 xlrd 依赖
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)

    mock_md = MagicMock()
    mock_md.convert.side_effect = MissingDependencyException(
        "XlsConverter requires missing dependency xlrd, please pip install xlrd"
    )
    with patch("markitdown.MarkItDown", return_value=mock_md):
        with pytest.raises(_DocumentConvertError) as ei:
            _convert_with_markitdown(p, None)

    msg = str(ei.value)
    assert "缺少转换依赖" in msg
    assert "已损坏" not in msg
    assert "xlrd" in msg

# ── PDF 截断拒绝(汇总 #9) ─────────────────────────────────


def test_truncated_pdf_rejected(tmp_path):
    """截断 PDF(无 %%EOF)必须在内容校验层拦下,不能静默转出残缺正文。"""
    from uniclaw.tools.fs import _validate_document_content

    p = tmp_path / "cut.pdf"
    p.write_bytes(b"%PDF-1.4" + bytes([10]) + b"1 0 obj" + bytes([10]) + b"%%no eof here")
    err = _validate_document_content(p)
    assert err is not None
    assert "截断" in err or "EOF" in err


def test_intact_pdf_header_and_eof_pass_validation(tmp_path):
    """带 %%EOF 的 PDF 通过结构校验(进入转换阶段)。"""
    from uniclaw.tools.fs import _validate_document_content

    p = tmp_path / "ok.pdf"
    p.write_bytes(b"%PDF-1.4\ncontent\n%%EOF\n")
    assert _validate_document_content(p) is None

class TestConvertTableAndHeadings:
    def test_pptx_merged_header_separator_columns(self, tmp_path):
        """PPTX 表头行有合并单元格时分隔行仍按真实列数。"""
        from pptx import Presentation
        from pptx.util import Inches

        from uniclaw.tools.fs import _convert_document

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        shape = slide.shapes.add_table(2, 3, Inches(1), Inches(1), Inches(5), Inches(2))
        tbl = shape.table
        tbl.cell(0, 0).merge(tbl.cell(0, 1))  # 表头合并两格
        tbl.cell(0, 0).text = "合并表头"
        tbl.cell(0, 2).text = "列三"
        tbl.cell(1, 0).text = "a"
        tbl.cell(1, 1).text = "b"
        tbl.cell(1, 2).text = "c"
        p = tmp_path / "t.pptx"
        prs.save(str(p))
        md = _convert_document(p)
        sep = [ln for ln in md.splitlines() if "---" in ln][0]
        assert sep.count("---") == 3, f"合并表头时分隔行须 3 列: {sep}"

    def test_pptx_broken_tblgrid_separator_matches_rows(self, tmp_path):
        """tblGrid 少声明列(WPS 等导出)时分隔行按行内真实单元格数。

        分隔行不能信 tblGrid 声明 —— 声明 1 列而每行 3 格时,
        信声明会让分隔行塌缩成单列(GFM 渲染错乱)。
        """
        from pptx import Presentation
        from pptx.util import Inches

        from uniclaw.tools.fs import _convert_document

        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        shape = slide.shapes.add_table(3, 3, Inches(1), Inches(1), Inches(5), Inches(2))
        tbl = shape.table
        data = [["姓名", "年龄", "城市"], ["a", "1", "x"], ["b", "2", "y"]]
        for r in range(3):
            for c in range(3):
                tbl.cell(r, c).text = data[r][c]
        # 破坏 tblGrid: 只留 1 个 gridCol(行内单元格仍是 3 个)
        grid = tbl._tbl.tblGrid
        for grid_col in list(grid)[1:]:
            grid.remove(grid_col)
        p = tmp_path / "g.pptx"
        prs.save(str(p))

        md = _convert_document(p)
        sep = [ln for ln in md.splitlines() if "---" in ln][0]
        assert sep.count("---") == 3, f"tblGrid 异常时分隔行须按行内列数: {sep}"

    def test_separator_avoids_list_multiply_construct(self):
        """分隔行禁用 ["---"] * n 构造 —— Cython 3.2.4 折叠 bug 的防回归守卫。

        该构造源码运行正确,但 Cython 编译后会把"列表/元组字面量×变量"
        包进字符串拼接链错误折叠成单元素 —— 发布包分隔行塌缩成 1 列,
        源码测试却全绿(第 37-40 轮连错 7 次的根因)。必须用 listcomp。
        打包侧由 .package/check_dist.py 的编译产物行为冒烟兜底。
        """
        import inspect

        from uniclaw.tools import fs

        src = inspect.getsource(fs._pptx_table_to_markdown)
        assert '["---"] *' not in src, "分隔行禁止列表字面量乘法,须用 listcomp"
        assert "['---'] *" not in src

    def test_docx_chinese_heading_styles(self, tmp_path):
        """中文 Word 样式名(标题 1/2)也要转成 # 层级。"""
        from docx import Document

        from uniclaw.tools.fs import _convert_document

        doc = Document()
        para = doc.add_paragraph("中文一级标题")
        try:
            para.style = doc.styles["Heading 1"]
        except KeyError:
            para.style = "Heading 1"
        doc.add_paragraph("正文")
        p = tmp_path / "cn.docx"
        doc.save(str(p))
        md = _convert_document(p)
        assert "中文一级标题" in md
        assert md.splitlines()[0].startswith("#"), f"标题未转 #: {md[:80]!r}"

def test_pptx_merged_cell_note(tmp_path):
    """合并单元格不再语义丢失: 表后注明跨列。"""
    from pptx import Presentation
    from pptx.util import Inches

    from uniclaw.tools.fs import _convert_document

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    shape = slide.shapes.add_table(2, 3, Inches(1), Inches(1), Inches(5), Inches(2))
    tbl = shape.table
    tbl.cell(0, 0).merge(tbl.cell(0, 1))
    tbl.cell(0, 0).text = "Merged Header"
    tbl.cell(0, 2).text = "City"
    tbl.cell(1, 0).text = "a"
    tbl.cell(1, 1).text = "b"
    tbl.cell(1, 2).text = "c"
    p = tmp_path / "m.pptx"
    prs.save(str(p))

    md = _convert_document(p)

    sep = [ln for ln in md.splitlines() if "---" in ln][0]
    assert sep.count("---") == 3
    assert "跨 2 列的合并单元格" in md
    assert "Merged Header" in md

@pytest.mark.asyncio
async def test_glob_empty_pattern_rejected(tmp_path):
    """pattern 为空给参数提示,不裸抛 pathlib ValueError。"""
    from uniclaw.tools.fs import Glob

    result = await Glob(pattern="", path=str(tmp_path))
    assert "TOOL_ERROR" in result
    assert "pattern 不能为空" in result
