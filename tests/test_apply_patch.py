"""apply_patch 批量文件修改工具测试。"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from uniclaw.tools.apply_patch import (
    apply_patch,
    _apply_update,
    extract_patch_paths,
    parse_patch,
)
from uniclaw.utils.constants import TOOL_ERROR


def _cfg(tmp_path: Path, workspace=(), writable=()) -> SimpleNamespace:
    """构造最小可用 config(Py 测试无 AppConfig 依赖)。"""
    return SimpleNamespace(
        root_dir=tmp_path,
        workspace=list(workspace),
        writable_dirs=list(writable),
    )


async def _run(tmp_path: Path, patch: str, dry_run: bool = False, cfg=None) -> str:
    cfg = cfg or _cfg(tmp_path)
    return await apply_patch.func(
        patch=patch, dry_run=dry_run, tool_runtime=SimpleNamespace(config=cfg)
    )


def _write(p: Path, content: str) -> None:
    p.write_text(content, encoding="utf-8", newline="")  # 精确控制换行


# ── parse_patch ────────────────────────────────────────────


def test_parse_add():
    ops, err = parse_patch("*** Begin Patch\n*** Add File: a.txt\n+hello\n+world\n*** End Patch")
    assert err is None
    assert [(o.kind, o.rel) for o in ops] == [("add", "a.txt")]
    assert ops[0].hunks[0].ins_lines == ["hello\n", "world\n"]


def test_parse_update_multi_hunk():
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-old\n+new\n@@\n-x\n+y\n*** End Patch"
    ops, err = parse_patch(p)
    assert err is None
    assert ops[0].kind == "update"
    assert [h.del_lines for h in ops[0].hunks] == [["old\n"], ["x\n"]]
    assert [h.ins_lines for h in ops[0].hunks] == [["new\n"], ["y\n"]]


def test_parse_delete():
    ops, err = parse_patch("*** Begin Patch\n*** Delete File: a.txt\n*** End Patch")
    assert err is None
    assert [(o.kind, o.rel) for o in ops] == [("delete", "a.txt")]


def test_parse_missing_begin():
    ops, err = parse_patch("*** Update File: a.txt\n@@\n-x\n+y\n*** End Patch")
    assert ops == []
    assert "Begin Patch" in err


def test_parse_missing_end_patch_rejected():
    """Bug 修复: 缺 *** End Patch 视为截断报错,避免静默应用损坏文件。"""
    ops, err = parse_patch("*** Begin Patch\n*** Add File: a.txt\n+hello\n")
    assert ops == []
    assert "End Patch" in err


def test_parse_missing_end_patch_does_not_corrupt():
    """Bug 修复: 缺 End Patch 时整批拒绝,不会部分应用导致数据损坏。"""
    p = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "@@\n"
        "-r1\n"
        "+r1: beta\n"
        "@@\n"
        "-r6\n"
        "+r6: beta"  # 截断: 无换行, 缺 End Patch
    )
    ops, err = parse_patch(p)
    assert ops == []
    assert "End Patch" in err


def test_parse_duplicate_update_rejected():
    p = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n@@\n-x\n+y\n"
        "*** Update File: a.txt\n@@\n-b\n+c\n"
        "*** End Patch"
    )
    ops, err = parse_patch(p)
    assert ops == []
    assert "只能出现一次 Update" in err


def test_parse_empty_patch():
    ops, err = parse_patch("")
    assert ops == []
    assert "patch 为空" in err


# ── _apply_update 纯逻辑 ───────────────────────────────────


def test_apply_replace():
    old = "line1\nline2\nline3\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-line2\n+CHANGED\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "line1\nCHANGED\nline3\n"


def test_apply_multi_hunk():
    old = "a\nb\nc\nd\ne\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-b\n+B2\n@@\n-d\n+D2\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nB2\nc\nD2\ne\n"


def test_apply_context_insert():
    # 纯 context(无 del): ins 插到 context 之后,context 保留原位
    old = "a\nb\nc\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n b\n+X\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nX\nc\n"


def test_apply_eof_append():
    old = "a\nb\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n+c\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\n"


def test_apply_eof_append_no_trailing_newline():
    """Bug 修复: EOF 追加到无末尾换行文件时先补换行(不能粘到上一行)。"""
    old = "a\nb"  # 末尾行 b 无换行
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n+c\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\n"


def test_apply_eof_append_empty_file():
    """EOF 追加到空文件: 不补多余换行。"""
    old = ""
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n+c\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "c\n"


def test_apply_eof_with_context_anchors_tail():
    """Bug 修复: EOF 带 context 时从文件尾锚定,context 行保留、不重复追加。"""
    old = "a\nb\nc\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n b\n*** End of File\n+X\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nX\nc\n"


def test_apply_eof_delete_tail_only():
    """Bug 修复: EOF+del 限定文件尾部删除,不误改文件中部相同行。"""
    old = "a\nb\nc\nd\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n-d\n+DD\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\nDD\n"


def test_apply_eof_delete_non_tail_rejected():
    """Bug 修复: EOF+del 删除区必须锚定文件末尾;删除中部同名行应报错而非误删。"""
    old = "a\nb\nc\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n-a\n*** End Patch"  # a 在文件开头
    ops, _ = parse_patch(p)
    with pytest.raises(Exception):
        _apply_update(old, ops[0].hunks)
    # 文件未被误改(仅报错)
    assert old == "a\nb\nc\n"


def test_apply_eof_delete_multi_tail():
    """EOF 一次删除文件末尾多行。"""
    old = "a\nb\nc\nd\ne\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n-d\n-e\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\n"


def test_apply_eof_context_replace_tail():
    """EOF 带 context 替换文件末尾(窗口含 context+del,锚定末尾)。"""
    old = "a\nb\nc\nd\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n c\n*** End of File\n-d\n+DD\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\nDD\n"


def test_parse_eof_marker_before_hunk():
    """Bug 修复: EOF 标记出现在 '@@' 之前时,该 hunk 仍继承 EOF 语义。"""
    p = "*** Begin Patch\n*** Update File: a.txt\n*** End of File\n@@\n-old\n+new\n*** End Patch"
    ops, err = parse_patch(p)
    assert err is None
    assert ops[0].hunks[0].is_eof is True


def test_apply_eof_delete_repeated_line_only_at_tail():
    """EOF 删除重复行: 只删文件末尾那个,不误删中部同名行。"""
    old = "a\nb\nc\nb\n"  # b 出现 2 次
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n-b\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\n"


def test_apply_eof_context_repeat_anchors_last():
    """EOF context 重复出现: 锚定文件末尾那处,在其后插入。"""
    old = "x\na\nb\nx\na\nb\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n a\n b\n*** End of File\n+X\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "x\na\nb\nx\na\nb\nX\n"


def test_apply_eof_no_context_delete_tail():
    """EOF 无 context 但删除文件末尾行。"""
    old = "a\nb\nc\nb\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n-b\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\n"


def test_apply_eof_context_after_prior_hunk():
    """EOF context+del 前面有普通 hunk 消费行时,仍正确锚定文件尾。"""
    old = "a\nb\nc\nd\ne\n"
    p = (
        "*** Begin Patch\n*** Update File: a.txt\n"
        "@@\n-a\n+A\n"
        "@@\n d\n*** End of File\n-e\n+E\n"
        "*** End Patch"
    )
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "A\nb\nc\nd\nE\n"


def test_parse_eof_applies_to_following_hunks():
    """Bug 修复: EOF 后多个 hunk 全部继承 EOF 语义。"""
    p = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "@@\n-x\n+y\n"
        "*** End of File\n"
        "@@\n-a\n+b\n"
        "@@\n-c\n+d\n"
        "*** End Patch"
    )
    ops, err = parse_patch(p)
    assert err is None
    assert all(h.is_eof for h in ops[0].hunks)


def test_apply_rstrip_fuzz():
    old = "foo  \nbar\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-foo\n+FOO\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "FOO\nbar\n"


def test_apply_no_match_raises():
    old = "abc\ndef\n"
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-xyz\n+ZZZ\n*** End Patch"
    ops, _ = parse_patch(p)
    with pytest.raises(Exception):
        _apply_update(old, ops[0].hunks)


# ── 工具端到端 ─────────────────────────────────────────────


async def test_add_new_file(tmp_path):
    p = "*** Begin Patch\n*** Add File: new.txt\n+hello\n+world\n*** End Patch"
    result = await _run(tmp_path, p)
    assert "已创建" in result
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello\nworld\n"


async def test_add_target_exists(tmp_path):
    _write(tmp_path / "a.txt", "x")
    p = "*** Begin Patch\n*** Add File: a.txt\n+y\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "已存在" in result
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x"


async def test_update_single_hunk(tmp_path):
    _write(tmp_path / "a.txt", "line1\nline2\nline3\n")
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-line2\n+CHANGED\n*** End Patch"
    result = await _run(tmp_path, p)
    assert "已应用" in result
    assert "---" in result  # unified diff 报告
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "line1\nCHANGED\nline3\n"


async def test_update_file_missing(tmp_path):
    p = "*** Begin Patch\n*** Update File: nope.txt\n@@\n-x\n+y\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "文件不存在" in result


async def test_mixed_multi_file(tmp_path):
    _write(tmp_path / "old.txt", "a\nb\nc\n")
    p = (
        "*** Begin Patch\n"
        "*** Add File: new.txt\n+hello\n"
        "*** Update File: old.txt\n@@\n-b\n+B2\n"
        "*** Delete File: gone.txt\n"
        "*** End Patch"
    )
    # gone.txt 不存在 → Delete 报错,整体不执行任何写(计划期全量校验)
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "文件不存在" in result
    assert not (tmp_path / "new.txt").exists()  # 零写入
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "a\nb\nc\n"


async def test_delete_file(tmp_path):
    _write(tmp_path / "gone.txt", "x")
    p = "*** Begin Patch\n*** Delete File: gone.txt\n*** End Patch"
    result = await _run(tmp_path, p)
    assert "已删除" in result
    assert not (tmp_path / "gone.txt").exists()


async def test_absolute_path_rejected(tmp_path):
    p = "*** Begin Patch\n*** Add File: /etc/evil.txt\n+x\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "绝对路径" in result


async def test_windows_drive_path_rejected(tmp_path):
    p = "*** Begin Patch\n*** Add File: C:/evil.txt\n+x\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "绝对路径" in result


async def test_dotdot_rejected(tmp_path):
    p = "*** Begin Patch\n*** Add File: ../evil.txt\n+x\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert ".." in result or "禁止" in result


async def test_dotdot_nested_rejected(tmp_path):
    p = "*** Begin Patch\n*** Add File: a/../../b.txt\n+x\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result


async def test_symlink_escape(tmp_path):
    import os

    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    try:
        outside.mkdir(exist_ok=True)
        (tmp_path / "link.txt").symlink_to(outside / "real.txt")
        p = "*** Begin Patch\n*** Add File: link.txt\n+x\n*** End Patch"
        result = await _run(tmp_path, p)
        assert TOOL_ERROR in result
        assert "symlink" in result
    except OSError:
        pytest.skip("symlink 不可用(需管理员/开发者模式)")
    finally:
        if outside.exists():
            outside.rmdir()


async def test_symlink_middle_component(tmp_path):
    import os

    outside = tmp_path.parent / f"{tmp_path.name}_outside2"
    try:
        outside.mkdir(exist_ok=True)
        (tmp_path / "linkdir").symlink_to(outside, target_is_directory=True)
        p = "*** Begin Patch\n*** Add File: linkdir/evil.txt\n+x\n*** End Patch"
        result = await _run(tmp_path, p)
        assert TOOL_ERROR in result
        assert "symlink" in result
    except OSError:
        pytest.skip("symlink 不可用(需管理员/开发者模式)")
    finally:
        if outside.exists():
            outside.rmdir()


async def test_crlf_preserved(tmp_path):
    _write(tmp_path / "a.txt", "a\r\nb\r\nc\r\n")
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-b\n+B2\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    content = (tmp_path / "a.txt").read_bytes()
    assert content == b"a\r\nB2\r\nc\r\n"  # CRLF 保持


async def test_binary_rejected(tmp_path):
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    p = "*** Begin Patch\n*** Update File: img.png\n@@\n-old\n+new\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "二进制" in result


async def test_dry_run(tmp_path):
    _write(tmp_path / "a.txt", "x\n")
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-x\n+X2\n*** End Patch"
    result = await _run(tmp_path, p, dry_run=True)
    assert "将应用" in result
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x\n"  # 未落盘


async def test_no_tool_runtime(tmp_path):
    result = await apply_patch.func(patch="*** Begin Patch\n*** Add File: a\n+1\n*** End Patch")
    assert TOOL_ERROR in result
    assert "root_dir" in result  # 不抛 AttributeError,明确报 config 缺失


async def test_empty_patch(tmp_path):
    result = await _run(tmp_path, "")
    assert TOOL_ERROR in result


# ── Move ───────────────────────────────────────────────────


async def test_move_file(tmp_path):
    _write(tmp_path / "old.py", "x = 1\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: old.py\n"
        "*** Move to: new.py\n"
        "@@\n-x = 1\n+x = 2\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert "已移动至 new.py" in result
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x = 2\n"


async def test_move_overwrite_existing_rejected(tmp_path):
    """Bug 修复: Move 到已存在目标时应拒绝,不静默覆盖。"""
    _write(tmp_path / "old.txt", "old\n")
    _write(tmp_path / "new.txt", "EXISTING\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: old.txt\n"
        "*** Move to: new.txt\n"
        "@@\n-old\n+NEW\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "已存在" in result
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "EXISTING\n"
    assert (tmp_path / "old.txt").exists()


async def test_move_same_path_is_update(tmp_path):
    """Move 到同路径等价于普通 update,不删源。"""
    _write(tmp_path / "a.txt", "x\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "*** Move to: a.txt\n"
        "@@\n-x\n+y\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "y\n"


async def test_move_crlf_preserved(tmp_path):
    """Move 修改 CRLF 文件: 目标文件保持 CRLF 换行。"""
    _write(tmp_path / "old.py", "a = 1\r\nb = 2\r\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: old.py\n"
        "*** Move to: new.py\n"
        "@@\n-a = 1\n+a = 3\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert "已移动至 new.py" in result
    assert (tmp_path / "new.py").read_bytes() == b"a = 3\r\nb = 2\r\n"
    assert not (tmp_path / "old.py").exists()


async def test_update_directory_rejected(tmp_path):
    """Bug 修复: Update 目录应返回明确报错而非抛 PermissionError。"""
    (tmp_path / "adir").mkdir()
    p = "*** Begin Patch\n*** Update File: adir\n@@\n-x\n+y\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "目录" in result


async def test_delete_directory_rejected(tmp_path):
    """Bug 修复: Delete 目录应返回明确报错。"""
    (tmp_path / "ddir").mkdir()
    p = "*** Begin Patch\n*** Delete File: ddir\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "目录" in result


# ── extract_patch_paths ────────────────────────────────────


def test_extract_paths_basic():
    p = (
        "*** Begin Patch\n"
        "*** Add File: a.txt\n+x\n"
        "*** Update File: b.txt\n@@\n-y\n+z\n"
        "*** Delete File: c.txt\n"
        "*** End Patch"
    )
    rels = [str(x) for x in extract_patch_paths(p, None)]
    assert rels == ["a.txt", "b.txt", "c.txt"]


def test_extract_paths_move_target_included():
    p = "*** Begin Patch\n*** Update File: a.txt\n*** Move to: b.txt\n@@\n-x\n+y\n*** End Patch"
    rels = [str(x) for x in extract_patch_paths(p, None)]
    assert rels == ["a.txt", "b.txt"]


def test_extract_paths_skips_absolute_and_dotdot():
    p = (
        "*** Begin Patch\n"
        "*** Add File: /etc/x\n+x\n"
        "*** Update File: ../y.txt\n@@\n-a\n+b\n"
        "*** Delete File: ok.txt\n"
        "*** End Patch"
    )
    rels = [str(x) for x in extract_patch_paths(p, None)]
    assert rels == ["ok.txt"]


def test_extract_paths_with_root(tmp_path):
    p = "*** Begin Patch\n*** Add File: sub/a.txt\n+x\n*** End Patch"
    paths = extract_patch_paths(p, tmp_path)
    assert paths == [(tmp_path / "sub" / "a.txt").resolve()]


def test_extract_paths_ignores_content_lines():
    """Bug 修复: context/内容行的 '*** Move to' 字样不应被误捕为目标路径。"""
    p = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "@@\n"
        " *** Move to: b.txt\n"
        "-x\n"
        "+y\n"
        "*** End Patch"
    )
    rels = [str(x) for x in extract_patch_paths(p, None)]
    assert rels == ["a.txt"]
    # Add 内容行含 *** 字样也不误捕
    p2 = "*** Begin Patch\n*** Add File: a.txt\n+*** not a header\n+x\n*** End Patch"
    rels2 = [str(x) for x in extract_patch_paths(p2, None)]
    assert rels2 == ["a.txt"]


# ── 更多边缘场景 ───────────────────────────────────────────


async def test_add_deep_nested_dir(tmp_path):
    """Add 到多层不存在的目录: 自动创建父目录。"""
    p = "*** Begin Patch\n*** Add File: a/b/c/d/new.py\n+print('hi')\n*** End Patch"
    result = await _run(tmp_path, p)
    assert "已创建" in result
    f = tmp_path / "a" / "b" / "c" / "d" / "new.py"
    assert f.read_text(encoding="utf-8") == "print('hi')\n"


async def test_move_to_deep_nested_dir(tmp_path):
    """Move 到多层不存在的目录: 自动创建目标父目录。"""
    _write(tmp_path / "x.py", "a = 1\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: x.py\n"
        "*** Move to: deep/nest/x2.py\n"
        "@@\n-a = 1\n+a = 2\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert "已移动至 deep/nest/x2.py" in result
    assert (tmp_path / "deep" / "nest" / "x2.py").read_text(encoding="utf-8") == "a = 2\n"
    assert not (tmp_path / "x.py").exists()


async def test_add_unicode_filename(tmp_path):
    """中文文件名 Add 正常。"""
    p = "*** Begin Patch\n*** Add File: 测试文件.txt\n+内容\n*** End Patch"
    result = await _run(tmp_path, p)
    assert "已创建" in result
    assert (tmp_path / "测试文件.txt").read_text(encoding="utf-8") == "内容\n"


def test_parse_crlf_patch():
    """CRLF 换行的 patch 解析: 行尾归一为 LF。"""
    p = "*** Begin Patch\r\n*** Update File: a.txt\r\n@@\r\n-old\r\n+new\r\n*** End Patch\r\n"
    ops, err = parse_patch(p)
    assert err is None
    assert ops[0].hunks[0].del_lines == ["old\n"]
    assert ops[0].hunks[0].ins_lines == ["new\n"]


def test_parse_mixed_eol_patch():
    """patch 内混合 CRLF/LF 行: 各自归一为 LF。"""
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-old\r\n+new\n*** End Patch\n"
    ops, err = parse_patch(p)
    assert err is None
    assert ops[0].hunks[0].del_lines == ["old\n"]
    assert ops[0].hunks[0].ins_lines == ["new\n"]


def test_apply_content_starting_with_plus():
    """文件行以 + 开头: 通过 del 前缀正确匹配删除。"""
    old = "a\n+plus\nb\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n a\n-+plus\n+changed\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nchanged\nb\n"


def test_apply_content_starting_with_minus():
    """文件行以 - 开头: 作为 context 锚点正确匹配。"""
    old = "a\n-minus\nb\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n -minus\n-b\n+B\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\n-minus\nB\n"


def test_apply_context_hunk_progresses_cursor():
    """纯 context hunk 定位后 cursor 推进,后续 hunk 在其后匹配。"""
    old = "a\nb\nc\nd\ne\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n b\n c\n@@\n-e\n+E\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\nd\nE\n"


def test_apply_order_detection():
    """hunk 乱序: 后 hunk 上下文出现在前 hunk 之前 → 报错。"""
    old = "a\nb\nc\nd\ne\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n d\n@@\n-a\n+A\n*** End Patch"
    ops, _ = parse_patch(p)
    with pytest.raises(Exception):
        _apply_update(old, ops[0].hunks)


def test_apply_empty_hunk_list():
    """空 hunks 列表: 原内容不变。"""
    assert _apply_update("abc\n", []) == "abc\n"


def test_apply_empty_ctx_hunk():
    """空 @@ 后直接 +/- 行: 在当前游标位置插删。"""
    old = "a\nb\nc\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n-b\n+X\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nX\nc\n"


def test_apply_eof_delete_all_lines():
    """EOF 一次删除文件全部行。"""
    old = "a\nb\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n*** End of File\n-a\n-b\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == ""


def test_apply_eof_delete_unique_line():
    """EOF 删除文件唯一一行。"""
    old = "x\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n*** End of File\n-x\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == ""


def test_parse_duplicate_add_rejected_at_apply():
    """同一路径两个 Add: 计划期第二个报错,整体不写入。"""
    p = (
        "*** Begin Patch\n"
        "*** Add File: a.txt\n+x\n"
        "*** Add File: a.txt\n+y\n"
        "*** End Patch"
    )
    ops, err = parse_patch(p)
    assert err is None  # parser 不拦重复 Add
    assert len(ops) == 2


async def test_move_absolute_target_rejected(tmp_path):
    """Move 目标为绝对路径 → 拒绝。"""
    _write(tmp_path / "a.txt", "x\n")
    outside = tmp_path.parent / f"{tmp_path.name}_move_out.txt"
    p = f"*** Begin Patch\n*** Update File: a.txt\n*** Move to: {outside}\n@@\n-x\n+y\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "绝对路径" in result


async def test_move_dotdot_target_rejected(tmp_path):
    """Move 目标含 .. → 拒绝。"""
    _write(tmp_path / "a.txt", "x\n")
    p = "*** Begin Patch\n*** Update File: a.txt\n*** Move to: ../evil.txt\n@@\n-x\n+y\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert ".." in result or "禁止" in result


async def test_add_via_symlink_parent_escape(tmp_path):
    """Add 目标父目录是逃逸 symlink → 拒绝。"""
    import os

    outside = tmp_path.parent / f"{tmp_path.name}_out3"
    try:
        outside.mkdir(exist_ok=True)
        (tmp_path / "linkdir").symlink_to(outside, target_is_directory=True)
        p = "*** Begin Patch\n*** Add File: linkdir/evil.txt\n+x\n*** End Patch"
        result = await _run(tmp_path, p)
        assert TOOL_ERROR in result
    except OSError:
        pytest.skip("symlink 不可用(需管理员/开发者模式)")
    finally:
        if outside.exists():
            outside.rmdir()


async def test_delete_nonexistent_rejected(tmp_path):
    """Delete 目标不存在 → 明确报错。"""
    p = "*** Begin Patch\n*** Delete File: nofile.txt\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "文件不存在" in result


async def test_update_no_trailing_newline_preserved(tmp_path):
    """无末尾换行文件 update: 不额外补换行,保持无换行结尾。"""
    (tmp_path / "a.txt").write_bytes(b"a\nb")
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-b\n+B2\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "a.txt").read_bytes() == b"a\nB2"


async def test_eof_append_single_line_no_newline(tmp_path):
    """单行无换行文件 EOF 追加: 补换行再追加。"""
    (tmp_path / "a.txt").write_bytes(b"x")
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n*** End of File\n+y\n*** End Patch"
    result = await _run(tmp_path, p)
    assert (tmp_path / "a.txt").read_bytes() == b"x\ny\n"


# ── Bug 报告回归: 缺 End Patch 与单行无换行 diff ───────────


async def test_missing_end_patch_rejected_no_write(tmp_path):
    """Bug1: 缺 *** End Patch 时报错,不部分应用,不损坏文件。"""
    _write(tmp_path / "exp_c.txt", "".join(f"r{i}: alpha\n" for i in range(1, 8)))
    patch = (
        "*** Begin Patch\n"
        "*** Update File: exp_c.txt\n"
        "@@\n"
        "-r1: alpha\n"
        "+r1: beta\n"
        "@@\n"
        "-r6: alpha\n"
        "+r6: beta"  # 截断: 无换行 + 缺 End Patch
    )
    result = await _run(tmp_path, patch)
    assert TOOL_ERROR in result
    assert "End Patch" in result
    # 文件未被部分修改(原子拒绝)
    assert (tmp_path / "exp_c.txt").read_text(encoding="utf-8").count("\n") == 7
    assert "betar7" not in (tmp_path / "exp_c.txt").read_text(encoding="utf-8")


async def test_missing_end_patch_update_only(tmp_path):
    """Bug1 变体: 缺 End Patch 的 Update 同样报错(不只 Add)。"""
    _write(tmp_path / "a.txt", "x\ny\nz\n")
    patch = "*** Begin Patch\n*** Update File: a.txt\n@@\n-x\n+X\n@@\n-y\n+Y\n"  # 无 End Patch
    result = await _run(tmp_path, patch)
    assert TOOL_ERROR in result
    assert "End Patch" in result
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x\ny\nz\n"


async def test_single_line_no_newline_diff_readable(tmp_path):
    """Bug2: 单行无尾换行文件修改后,diff 的 -/+ 行分行显示不粘连。"""
    (tmp_path / "single.txt").write_bytes(b"single no newline")
    p = (
        "*** Begin Patch\n"
        "*** Update File: single.txt\n"
        "@@\n"
        "-single no newline\n"
        "+single no newline [CHANGED]\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    # diff 中 - 行和 + 行各自独立(不粘连成 -old+new 一行)
    assert "-single no newline+single no newline" not in result
    assert "\n-single no newline\n" in result
    assert "\n+single no newline [CHANGED]\n" in result
    # 文件内容正确: 末行替换保持无尾换行(Bug B 语义)
    assert (tmp_path / "single.txt").read_bytes() == b"single no newline [CHANGED]"


# ── Bug 报告回归: 前后 context / 非 UTF-8 ──────────────────


def test_apply_context_both_sides():
    """Bug2: 支持删除区两侧的 context(标准 diff 语义)。"""
    old = "a\nb\nc\nd\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n a\n-b\n+b2\n c\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb2\nc\nd\n"


def test_apply_standard_git_diff_order():
    """Bug2: 标准 git diff 行序(context/-del/context/+ins/context)。"""
    old = "a\nb\nc\nd\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n a\n-b\n c\n+b2\n d\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nc\nb2\nd\n"


def test_apply_context_both_sides_insert():
    """Bug2: 前 context + del + 插入 + 后 context(按文件序)。"""
    old = "a\nb\nc\nd\ne\nf\ng\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n c\n-d\n-e\n+D\n f\n g\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nc\nD\nf\ng\n"


def test_apply_multi_ctx_both_sides():
    """Bug2: 多行前后 context 同时存在。"""
    old = "a\nb\nc\nd\ne\nf\ng\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n a\n b\n-c\n-d\n+C\n e\n f\n*** End Patch"
    ops, _ = parse_patch(p)
    assert _apply_update(old, ops[0].hunks) == "a\nb\nC\ne\nf\ng\n"


def test_apply_big_hunk_context_both_sides():
    """Bug2: 大 hunk(300 行文件删 100 行)前后 context 完整保留。"""
    old = "".join(f"L{i}\n" for i in range(300))
    body_lines = ["@@", " L98", " L99"]
    for i in range(100, 200):
        body_lines.append(f"-L{i}")
    for i in range(100, 200):
        body_lines.append(f"+NEW{i}")
    body_lines += [" L200", " L201"]
    p = "*** Begin Patch\n*** Update File: x.txt\n" + "\n".join(body_lines) + "\n*** End Patch"
    ops, err = parse_patch(p)
    assert err is None
    new = _apply_update(old, ops[0].hunks)
    new_lines = new.splitlines(keepends=False)
    assert len(new_lines) == 300
    assert new_lines[:100] == [f"L{i}" for i in range(100)]  # L0..L99 保留
    assert new_lines[100:200] == [f"NEW{i}" for i in range(100, 200)]
    assert new_lines[200:] == [f"L{i}" for i in range(200, 300)]  # L200..L299 保留


def test_apply_context_disordered_rejected():
    """Bug2 对照: context 行乱序(匹配序列在文件不连续)应报错。"""
    old = "a\nb\nc\nd\ne\nf\ng\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n c\n f\n-d\n-e\n+D\n*** End Patch"
    ops, _ = parse_patch(p)
    with pytest.raises(Exception):
        _apply_update(old, ops[0].hunks)


async def test_gbk_encoding_rejected_with_clear_message(tmp_path):
    """Bug3: 非 UTF-8(GBK)文件报"编码不是 UTF-8"而非"上下文未匹配"。"""
    (tmp_path / "enc_gbk.txt").write_bytes(
        "中文第一行\n中文第二行\n中文第三行\n".encode("gbk")
    )
    p = "*** Begin Patch\n*** Update File: enc_gbk.txt\n@@\n-中文第二行\n+中文第二行 [MOD]\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "UTF-8" in result
    assert "上下文未匹配" not in result


async def test_latin1_encoding_rejected_with_clear_message(tmp_path):
    """Bug3: 非 UTF-8(latin-1)文件报"编码不是 UTF-8"。"""
    (tmp_path / "enc_latin.txt").write_bytes("caf\xe9\ntext\n".encode("latin-1"))
    p = "*** Begin Patch\n*** Update File: enc_latin.txt\n@@\n-text\n+TEXT\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "UTF-8" in result


async def test_utf8_file_still_works(tmp_path):
    """Bug3 对照: UTF-8 中文文件仍正常修改。"""
    _write(tmp_path / "u.txt", "中文第一行\n中文第二行\n")
    p = "*** Begin Patch\n*** Update File: u.txt\n@@\n-中文第二行\n+中文第二行 [MOD]\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "u.txt").read_text(encoding="utf-8") == "中文第一行\n中文第二行 [MOD]\n"


async def test_utf16_bom_file_rejected_clear_message(tmp_path):
    """Bug3: UTF-16 BOM 文件明确报"只支持 UTF-8",而非"上下文未匹配"。"""
    (tmp_path / "u16.txt").write_bytes(b"\xff\xfe" + "a\nb\n".encode("utf-16-le"))
    p = "*** Begin Patch\n*** Update File: u16.txt\n@@\n-b\n+B\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "UTF-8" in result
    assert "上下文未匹配" not in result


async def test_utf8_bom_file_preserved(tmp_path):
    """Bug3 对照: UTF-8 BOM 文件可正常修改且 BOM 保留。"""
    (tmp_path / "u8bom.txt").write_bytes(b"\xef\xbb\xbf" + "a\nb\nc\n".encode("utf-8"))
    p = "*** Begin Patch\n*** Update File: u8bom.txt\n@@\n-b\n+B\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "u8bom.txt").read_bytes() == b"\xef\xbb\xbfa\nB\nc\n"


def test_apply_context_insert_no_trailing_newline():
    """Bug4: 无尾换行文件,纯 context 插入到末行后不应粘连。"""
    old = "l1\nl2"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n l2\n+X\n*** End Patch"
    ops, err = parse_patch(p)
    assert err is None
    assert _apply_update(old, ops[0].hunks) == "l1\nl2\nX\n"


def test_apply_eof_context_insert_no_trailing_newline():
    """Bug4: EOF + context 插入到无尾换行末行后不应粘连。"""
    old = "l1\nl2"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n l2\n*** End of File\n+X\n*** End Patch"
    ops, err = parse_patch(p)
    assert err is None
    assert _apply_update(old, ops[0].hunks) == "l1\nl2\nX\n"


def test_apply_context_insert_normal_trailing_newline_unchanged():
    """Bug4 对照: 有尾换行的常规插入行为不回归。"""
    old = "l1\nl2\nl3\n"
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n l2\n+X\n*** End Patch"
    ops, err = parse_patch(p)
    assert err is None
    assert _apply_update(old, ops[0].hunks) == "l1\nl2\nX\nl3\n"


def test_parse_update_empty_hunk_rejected():
    """空 @@ 后无内容行的 Update 块应报错(需要一个有内容的 hunk)。"""
    p = "*** Begin Patch\n*** Update File: x.txt\n@@\n*** End Patch"
    ops, err = parse_patch(p)
    assert err is not None and "至少需要一个" in err

# ── Bug 报告回归: NUL 漏检 / \r-only 换行 / 设备名 ─────────


async def test_nul_after_8kb_rejected(tmp_path):
    """Bug6: 二进制 NUL 出现在 8KB 之后也应被拒绝(全文 NUL 扫描)。"""
    data = ("alpha line\n" * 820).encode("utf-8") + b"\x00\x00BINARY-TAIL"  # 9033 字节
    (tmp_path / "blob.txt").write_bytes(data)
    p = "*** Begin Patch\n*** Update File: blob.txt\n@@\n-alpha line\n+alpha line [MOD]\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "二进制" in result
    assert b"alpha line [MOD]" not in (tmp_path / "blob.txt").read_bytes()


async def test_nul_only_in_head_still_rejected(tmp_path):
    """NUL 在头部时仍被拒绝(既有行为保持)。"""
    (tmp_path / "head.txt").write_bytes(b"text\x00nul\nmore\n")
    p = "*** Begin Patch\n*** Update File: head.txt\n@@\n-text\n+TEXT\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "二进制" in result


async def test_cr_only_newline_preserved(tmp_path):
    """Bug7: 老 Mac \r-only 换行文件修改后保持 \r-only,不混入 LF。"""
    (tmp_path / "oldmac.txt").write_bytes(b"a\rb\rc\r")
    p = "*** Begin Patch\n*** Update File: oldmac.txt\n@@\n-b\n+B\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "oldmac.txt").read_bytes() == b"a\rB\rc\r"


async def test_cr_only_multi_hunk_preserved(tmp_path):
    """Bug7: \r-only 文件多 hunk 修改仍保持 \r-only。"""
    (tmp_path / "m.txt").write_bytes(b"a\rb\rc\r")
    p = "*** Begin Patch\n*** Update File: m.txt\n@@\n-a\n+A\n@@\n-c\n+C\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "m.txt").read_bytes() == b"A\rb\rC\r"


async def test_cr_only_eof_append_preserved(tmp_path):
    """Bug7: \r-only 文件 EOF 追加仍保持 \r-only。"""
    (tmp_path / "e.txt").write_bytes(b"a\rb\rc\r")
    p = "*** Begin Patch\n*** Update File: e.txt\n@@\n c\n*** End of File\n+d\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "e.txt").read_bytes() == b"a\rb\rc\rd\r"


async def test_reserved_device_name_rejected(tmp_path):
    """低危: Windows 保留设备名(con / NUL / con.txt 等)应拒绝。"""
    for name in ("con", "con.txt", "NUL", "nul.txt", "prn", "aux", "com1", "lpt3"):
        p = f"*** Begin Patch\n*** Add File: {name}\n+x\n*** End Patch"
        result = await _run(tmp_path, p)
        assert TOOL_ERROR in result, f"{name} 应被拒绝: {result[:60]}"


async def test_trailing_dot_path_rejected(tmp_path):
    """低危: 尾部点的路径应拒绝(Windows 会静默归一化)。"""
    p = "*** Begin Patch\n*** Add File: abc.\n+x\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "尾部" in result or "归一" in result


def test_parse_trailing_space_normalized():
    """尾部空格在 parser 层被剥离: 创建的实际文件名为去掉空格后的名字。"""
    ops, err = parse_patch("*** Begin Patch\n*** Add File: trapfile \n+x\n*** End Patch")
    assert err is None
    assert ops[0].rel == "trapfile"  # 尾部空格被剥离


# ── 第七轮 Bug 报告: End Patch 后内容 / Delete+Add ─────────


def test_parse_content_after_end_patch_rejected():
    """低危A: '*** End Patch' 之后的非空内容应报错(而非静默忽略)。"""
    p = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "@@\n-x\n+X\n"
        "*** End Patch\n"
        "garbage after end patch\n"
    )
    ops, err = parse_patch(p)
    assert ops == []
    assert "End Patch" in err
    assert "后" in err


async def test_trailing_blank_lines_after_end_patch_ok(tmp_path):
    """低危A 对照: End Patch 后只有空行 → 正常应用。"""
    _write(tmp_path / "a.txt", "x\n")
    p = "*** Begin Patch\n*** Update File: a.txt\n@@\n-x\n+X\n*** End Patch\n\n\n"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "X\n"


async def test_delete_then_add_same_path_replaces(tmp_path):
    """低危B: Delete + Add 同路径 = 先删后建,应放行并替换文件内容。"""
    _write(tmp_path / "f.txt", "old\n")
    p = (
        "*** Begin Patch\n"
        "*** Delete File: f.txt\n"
        "*** Add File: f.txt\n"
        "+new content\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "new content\n"


async def test_add_existing_without_prior_delete_still_rejected(tmp_path):
    """低危B 对照: 无前序 Delete 的 Add 已存在目标仍报错。"""
    _write(tmp_path / "f.txt", "old\n")
    p = "*** Begin Patch\n*** Add File: f.txt\n+new\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "已存在" in result


# ── 第十轮 Bug 报告: G/A/B/C/D/E ─────────────────────────


def test_add_block_followed_by_delete_is_valid():
    """标准协议(对齐 Codex/Aider): Add 块后跟独立 Delete 是合法多操作。"""
    p = (
        "*** Begin Patch\n"
        "*** Add File: f.txt\n+l1\n"
        "*** Delete File: hit.txt\n"
        "*** End Patch"
    )
    ops, err = parse_patch(p)
    assert err is None
    assert [(o.kind, o.rel) for o in ops] == [("add", "f.txt"), ("delete", "hit.txt")]


async def test_add_then_delete_different_files(tmp_path):
    """标准协议: 一次调用"先建新文件 + 删除旧文件"合法且生效。"""
    _write(tmp_path / "old.txt", "old\n")
    p = (
        "*** Begin Patch\n"
        "*** Add File: new.txt\n+content\n"
        "*** Delete File: old.txt\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "content\n"
    assert not (tmp_path / "old.txt").exists()


def test_add_block_bare_delete_content_via_plus():
    """BugG 对照: '+*** Delete File:' 作为合法内容行写入(+ 前缀被剥离)。"""
    p = (
        "*** Begin Patch\n"
        "*** Add File: f.txt\n+l1\n"
        "+*** Delete File: not_a_cmd\n"
        "*** End Patch"
    )
    ops, err = parse_patch(p)
    assert err is None
    assert any(c == "*** Delete File: not_a_cmd\n" for k, c in ops[0].hunks[0].body if k == "ins")


async def test_bom_first_line_update(tmp_path):
    """BugA: UTF-8 BOM 文件首行可以修改,且 BOM 保留。"""
    (tmp_path / "bom.txt").write_bytes(b"\xef\xbb\xbfhead\nbody\n")
    p = "*** Begin Patch\n*** Update File: bom.txt\n@@\n-head\n+HEAD\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "bom.txt").read_bytes() == b"\xef\xbb\xbfHEAD\nbody\n"


async def test_no_trailing_newline_replace_keeps_no_nl(tmp_path):
    """BugB: 无尾换行文件末行等量替换后保持无尾换行。"""
    (tmp_path / "n.txt").write_bytes(b"l1\nl2\nl3")
    p = "*** Begin Patch\n*** Update File: n.txt\n@@\n l1\n l2\n-l3\n+l3-MOD\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "n.txt").read_bytes() == b"l1\nl2\nl3-MOD"


async def test_no_trailing_newline_insert_adds_nl(tmp_path):
    """BugB 对照: 无尾换行文件纯插入仍补换行(非替换)。"""
    (tmp_path / "i.txt").write_bytes(b"l1\nl2")
    p = "*** Begin Patch\n*** Update File: i.txt\n@@\n l2\n+X\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "i.txt").read_bytes() == b"l1\nl2\nX\n"


def test_add_block_bare_marker_reports_prefix_error():
    """BugC: Add 块内裸 '*** xxx'(漏前缀)报缺前缀错误,不中断为标记。"""
    p = "*** Begin Patch\n*** Add File: c.txt\n+l1\n*** note line\n+l2\n*** End Patch"
    ops, err = parse_patch(p)
    assert ops == []
    assert "前缀" in err


async def test_context_line_equal_eof_not_misparsed(tmp_path):
    """BugD: context 行内容恰为 '*** End of File' 不被误判为 EOF 标记。"""
    _write(tmp_path / "d.txt", "a\n*** End of File\nz\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: d.txt\n"
        "@@\n a\n *** End of File\n-z\n+Z\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "d.txt").read_text(encoding="utf-8") == "a\n*** End of File\nZ\n"


async def test_cr_only_diff_readable(tmp_path):
    """BugE: CR-only 文件 diff 报告的 -/+ 行各自独立不粘连。"""
    (tmp_path / "cr.txt").write_bytes(b"one\r two\r three")
    p = "*** Begin Patch\n*** Update File: cr.txt\n@@\n one\n- two\n+ TWO\n three\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert "- two" in result and "+ TWO" in result
    assert "- two+ TWO" not in result and "- two\r+ TWO" not in result
    assert (tmp_path / "cr.txt").read_bytes() == b"one\r TWO\r three"


# ── 第十二轮 Bug 报告: H(裁剪过拟合) ────────────────────


async def test_delete_eof_last_line_keeps_new_marker_nl(tmp_path):
    """BugH: 删除无尾换行文件的末行时,新末行(原倒数第二行)保留自己的换行。"""
    (tmp_path / "n.txt").write_bytes(b"l1\nl2\nl3")
    p = "*** Begin Patch\n*** Update File: n.txt\n@@\n-l3\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "n.txt").read_bytes() == b"l1\nl2\n"


async def test_replace_middle_and_delete_last(tmp_path):
    """BugH: 替换中间行 + 删除无尾换行末行,新末行换行不被误裁。"""
    (tmp_path / "n2.txt").write_bytes(b"l1\nl2\nl3\nl4")
    p = (
        "*** Begin Patch\n*** Update File: n2.txt\n"
        "@@\n l1\n-l2\n+L2\n l3\n"
        "@@\n-l4\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "n2.txt").read_bytes() == b"l1\nL2\nl3\n"


async def test_replace_eof_last_line_still_no_nl(tmp_path):
    """BugB 目标场景回归: 等量替换无尾换行末行仍保持无尾换行。"""
    (tmp_path / "b.txt").write_bytes(b"l1\nl2\nl3")
    p = "*** Begin Patch\n*** Update File: b.txt\n@@\n l1\n l2\n-l3\n+l3-MOD\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "b.txt").read_bytes() == b"l1\nl2\nl3-MOD"


# ── 第十三/十四轮 Bug 报告: J ────────────────────────────


async def test_single_hunk_replace_mid_and_delete_last(tmp_path):
    """BugJ: 单 hunk 内"替换中间行 + 删除无尾换行末行"不误裁新末行换行。"""
    (tmp_path / "n.txt").write_bytes(b"l1\nl2\nl3\nl4")
    p = "*** Begin Patch\n*** Update File: n.txt\n@@\n l1\n-l2\n+L2\n l3\n-l4\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "n.txt").read_bytes() == b"l1\nL2\nl3\n"


async def test_single_hunk_replace_mid_keep_last_ctx_no_nl(tmp_path):
    """BugJ 边界: 替换中间行 + 末行 ctx 保留(无尾换行原文件)→ 末行保持无换行。"""
    (tmp_path / "n.txt").write_bytes(b"l1\nl2\nl3\nl4")
    p = "*** Begin Patch\n*** Update File: n.txt\n@@\n l1\n-l2\n+L2\n l3\n l4\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "n.txt").read_bytes() == b"l1\nL2\nl3\nl4"


# ── 第十四轮: Delete 报告可追溯性 ────────────────────────


async def test_delete_report_highlights_target(tmp_path):
    """Delete 报告醒目标注目标路径(可追溯性增强)。"""
    _write(tmp_path / "gone.txt", "x\n")
    p = "*** Begin Patch\n*** Delete File: gone.txt\n*** End Patch"
    result = await _run(tmp_path, p)
    assert "⚠️ 已删除" in result
    assert "删除目标路径(相对)" in result and "gone.txt" in result


async def test_multi_add_then_delete_legal(tmp_path):
    """标准协议: Add 后跟独立 Delete(先建后删)是合法多操作。"""
    _write(tmp_path / "old.txt", "old\n")
    p = (
        "*** Begin Patch\n"
        "*** Add File: new.txt\n+content\n"
        "*** Delete File: old.txt\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "content\n"
    assert not (tmp_path / "old.txt").exists()


async def test_multi_update_then_add_legal(tmp_path):
    """标准协议: Update 块后跟独立 Add(多文件批量)是合法多操作。"""
    _write(tmp_path / "a.txt", "x\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n@@\n-x\n+X\n"
        "*** Add File: b.txt\n+y\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "X\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "y\n"


# ── 第十六轮 Bug 报告: K(同路径多操作冲突) ──────────────


async def test_delete_then_update_same_path_conflict(tmp_path):
    """BugK: 同路径 Delete+Update 冲突,计划期报错且文件零改动。"""
    _write(tmp_path / "tmp1.txt", "aaa\nbbb\nccc\n")
    p = (
        "*** Begin Patch\n"
        "*** Delete File: tmp1.txt\n"
        "*** Update File: tmp1.txt\n@@\n-aaa\n+AAA\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "多个操作" in result
    assert (tmp_path / "tmp1.txt").read_text(encoding="utf-8") == "aaa\nbbb\nccc\n"


async def test_double_delete_same_path_conflict(tmp_path):
    """BugK: 同路径 Delete+Delete 冲突,文件保留。"""
    _write(tmp_path / "dup.txt", "x\n")
    p = "*** Begin Patch\n*** Delete File: dup.txt\n*** Delete File: dup.txt\n*** End Patch"
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "多个操作" in result
    assert (tmp_path / "dup.txt").read_text(encoding="utf-8") == "x\n"


async def test_move_then_delete_source_conflict(tmp_path):
    """BugK: Move 后 Delete 原路径冲突,文件保持原位。"""
    _write(tmp_path / "src.txt", "n1\n")
    p = (
        "*** Begin Patch\n"
        "*** Update File: src.txt\n*** Move to: dst.txt\n@@\n-n1\n+n1-X\n"
        "*** Delete File: src.txt\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR in result
    assert "多个操作" in result
    assert (tmp_path / "src.txt").exists()
    assert not (tmp_path / "dst.txt").exists()


async def test_delete_then_add_same_path_still_legal(tmp_path):
    """BugK 对照: 同路径 Delete+Add(先删后建)仍合法。"""
    _write(tmp_path / "f.txt", "old\n")
    p = (
        "*** Begin Patch\n"
        "*** Delete File: f.txt\n"
        "*** Add File: f.txt\n+new content\n"
        "*** End Patch"
    )
    result = await _run(tmp_path, p)
    assert TOOL_ERROR not in result
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "new content\n"
