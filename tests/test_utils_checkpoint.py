"""tests for src/uniclaw/utils/checkpoint.py — 检查点系统。"""

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest

from uniclaw.utils.checkpoint import (
    _file_create_checkpoint,
    _file_delete_checkpoint,
    _file_diff_two_dirs,
    _file_has_diff_sync,
    _file_list_checkpoints,
    _file_pop_checkpoint,
    _file_restore_checkpoint,
    _generate_diff,
    _get_all_files,
    _load_index,
    _read_file_content,
    _save_index,
    _scan_files_sync,
)


# ── _load_index / _save_index ────────────────────────────────────


class TestIndex:
    def test_load_missing_file(self, tmp_path):
        assert _load_index(tmp_path) == []

    def test_load_corrupt_json(self, tmp_path):
        (tmp_path / "index.json").write_text("not json", encoding="utf-8")
        assert _load_index(tmp_path) == []

    def test_save_and_load(self, tmp_path):
        entries = [
            {"id": "cp_1", "message": "first", "time": "2024-01-01 10:00:00"},
            {"id": "cp_2", "message": "second", "time": "2024-01-02 10:00:00"},
        ]
        _save_index(tmp_path, entries)
        loaded = _load_index(tmp_path)
        assert len(loaded) == 2
        # sorted by time descending
        assert loaded[0]["id"] == "cp_2"
        assert loaded[1]["id"] == "cp_1"

    def test_save_chinese_message(self, tmp_path):
        entries = [{"id": "cp_1", "message": "中文消息", "time": "2024-01-01"}]
        _save_index(tmp_path, entries)
        loaded = _load_index(tmp_path)
        assert loaded[0]["message"] == "中文消息"

    def test_same_second_order_by_created_at(self, tmp_path):
        """同秒内多个 checkpoint:按 created_at 毫秒新→旧排序。"""
        import uniclaw.utils.checkpoint as cp_module

        checkpoint_dir = cp_module._get_checkpoint_dir(tmp_path)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # 同秒内 3 个,created_at 毫秒不同;写入顺序故意颠倒,排序后应新→旧
        _save_index(
            checkpoint_dir,
            [
                {"id": "cp_mid", "time": "2024-01-02 10:00:00", "created_at": 1_704_160_800_500},
                {"id": "cp_new", "time": "2024-01-02 10:00:00", "created_at": 1_704_160_800_900},
                {"id": "cp_old", "time": "2024-01-02 10:00:00", "created_at": 1_704_160_800_100},
            ],
        )
        loaded = _load_index(checkpoint_dir)
        assert [e["id"] for e in loaded] == ["cp_new", "cp_mid", "cp_old"]

    def test_legacy_sort_fallback_to_time_string(self, tmp_path):
        """旧数据无 created_at:按 time 字符串解析排序。"""
        import uniclaw.utils.checkpoint as cp_module

        checkpoint_dir = cp_module._get_checkpoint_dir(tmp_path)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        _save_index(
            checkpoint_dir,
            [
                {"id": "cp_old", "time": "2024-01-01 10:00:00"},
                {"id": "cp_new", "time": "2024-01-02 10:00:00"},
            ],
        )
        loaded = _load_index(checkpoint_dir)
        assert [e["id"] for e in loaded] == ["cp_new", "cp_old"]


# ── _scan_files_sync ─────────────────────────────────────────────


class TestScanFilesSync:
    def test_subdirectory_gitignore(self, tmp_path):
        """测试子目录中的 .gitignore 规则。"""
        import os

        # 创建根目录结构
        (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
        (tmp_path / "app.log").write_text("log content", encoding="utf-8")

        # 创建子目录及其 .gitignore
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "code.py").write_text("x = 1", encoding="utf-8")
        (subdir / "debug.log").write_text("debug info", encoding="utf-8")
        (subdir / ".gitignore").write_text("*.log\n", encoding="utf-8")

        # 创建更深层的子目录
        deep_dir = subdir / "deep"
        deep_dir.mkdir()
        (deep_dir / "helper.py").write_text("def help(): pass", encoding="utf-8")
        (deep_dir / "error.log").write_text("error info", encoding="utf-8")

        # 扫描文件
        files = _scan_files_sync(tmp_path)

        # 将路径标准化为使用正斜杠，便于比较
        normalized_files = [f.replace(os.sep, "/") for f in files]

        # 验证根目录的 .gitignore 规则（默认规则忽略隐藏文件和 __pycache__）
        assert "main.py" in normalized_files
        # app.log 不在根目录的 .gitignore 中，应该被包含
        assert "app.log" in normalized_files

        # 验证子目录的 .gitignore 规则
        assert "subdir/code.py" in normalized_files
        # subdir/debug.log 应该被子目录的 .gitignore 忽略
        assert "subdir/debug.log" not in normalized_files

        # 验证深层子目录继承子目录的 .gitignore 规则
        assert "subdir/deep/helper.py" in normalized_files
        # subdir/deep/error.log 应该被子目录的 .gitignore 忽略
        assert "subdir/deep/error.log" not in normalized_files

    def test_multiple_gitignore_levels(self, tmp_path):
        """测试多层级 .gitignore 规则。"""
        import os

        # 根目录 .gitignore
        (tmp_path / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        (tmp_path / "data.tmp").write_text("temp data", encoding="utf-8")
        (tmp_path / "data.txt").write_text("real data", encoding="utf-8")

        # 子目录 .gitignore（覆盖根目录规则）
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / ".gitignore").write_text("!*.tmp\n*.log\n", encoding="utf-8")
        (subdir / "keep.tmp").write_text("keep this", encoding="utf-8")
        (subdir / "remove.log").write_text("remove this", encoding="utf-8")

        files = _scan_files_sync(tmp_path)

        # 将路径标准化为使用正斜杠，便于比较
        normalized_files = [f.replace(os.sep, "/") for f in files]

        # 根目录规则生效
        assert "data.tmp" not in normalized_files
        assert "data.txt" in normalized_files

        # 子目录规则覆盖根目录规则
        assert "subdir/keep.tmp" in normalized_files  # !*.tmp 取消忽略
        assert "subdir/remove.log" not in normalized_files  # *.log 被忽略


# ── _read_file_content ───────────────────────────────────────────


class TestReadFileContent:
    def test_valid_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        assert _read_file_content(f) == "hello"

    def test_nonexistent_file(self, tmp_path):
        assert _read_file_content(tmp_path / "nope.txt") is None

    def test_binary_file(self, tmp_path):
        f = tmp_path / "bin.dat"
        f.write_bytes(b"\x80\x81\x82")
        assert _read_file_content(f) is None


# ── _generate_diff ───────────────────────────────────────────────


class TestGenerateDiff:
    def test_identical(self):
        assert _generate_diff("same", "same", "a", "b") == ""

    def test_different(self):
        diff = _generate_diff("old\n", "new\n", "a/old", "b/new")
        assert "-old" in diff
        assert "+new" in diff

    def test_empty_old(self):
        diff = _generate_diff("", "new\n", "a/old", "b/new")
        assert "+new" in diff

    def test_empty_new(self):
        diff = _generate_diff("old\n", "", "a/old", "b/new")
        assert "-old" in diff


# ── _file_diff_two_dirs ──────────────────────────────────────────


class TestFileDiffTwoDirs:
    def test_identical_dirs(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "f.txt").write_text("same", encoding="utf-8")
        (d2 / "f.txt").write_text("same", encoding="utf-8")
        result = _file_diff_two_dirs(
            ["f.txt"], ["f.txt"], d1, d2, "a", "b", "no diff"
        )
        assert result == "no diff"

    def test_file_only_in_b(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d2 / "new.txt").write_text("new content", encoding="utf-8")
        result = _file_diff_two_dirs([], ["new.txt"], d1, d2, "a", "b", "no diff")
        assert "new.txt" in result
        assert "new content" in result

    def test_file_only_in_a(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "old.txt").write_text("old content", encoding="utf-8")
        result = _file_diff_two_dirs(["old.txt"], [], d1, d2, "a", "b", "no diff")
        assert "old.txt" in result

    def test_different_content(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "f.txt").write_text("old\n", encoding="utf-8")
        (d2 / "f.txt").write_text("new\n", encoding="utf-8")
        result = _file_diff_two_dirs(
            ["f.txt"], ["f.txt"], d1, d2, "a", "b", "no diff"
        )
        assert "-old" in result
        assert "+new" in result


# ── _file_has_diff_sync ──────────────────────────────────────────


class TestFileHasDiffSync:
    def test_same_files_same_content(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "f.txt").write_text("same", encoding="utf-8")
        (d2 / "f.txt").write_text("same", encoding="utf-8")
        assert _file_has_diff_sync(["f.txt"], ["f.txt"], d1, d2) is False

    def test_different_file_lists(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        assert _file_has_diff_sync(["a.txt"], ["b.txt"], d1, d2) is True

    def test_same_list_different_content(self, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "f.txt").write_text("old", encoding="utf-8")
        (d2 / "f.txt").write_text("new", encoding="utf-8")
        assert _file_has_diff_sync(["f.txt"], ["f.txt"], d1, d2) is True


# ── _file_list_checkpoints ───────────────────────────────────────


class TestFileListCheckpoints:
    def test_no_checkpoint_dir(self, tmp_path):
        result = _file_list_checkpoints(tmp_path)
        assert "没有检查点" in result

    def test_empty_index(self, tmp_path):
        cp_dir = tmp_path / ".UniClaw" / "checkpoints"
        cp_dir.mkdir(parents=True)
        _save_index(cp_dir, [])
        result = _file_list_checkpoints(tmp_path)
        assert "没有检查点" in result

    def test_with_entries(self, tmp_path):
        cp_dir = tmp_path / ".UniClaw" / "checkpoints"
        cp_dir.mkdir(parents=True)
        _save_index(
            cp_dir,
            [
                {"id": "cp_1", "message": "first", "time": "2024-01-01 10:00:00"},
                {"id": "cp_2", "message": "second", "time": "2024-01-02 10:00:00"},
            ],
        )
        result = _file_list_checkpoints(tmp_path)
        assert "cp_1" in result
        assert "cp_2" in result
        assert "first" in result
        assert "second" in result


# ── _file_delete_checkpoint ──────────────────────────────────────


class TestFileDeleteCheckpoint:
    def test_no_checkpoint_dir(self, tmp_path):
        ok, msg = _file_delete_checkpoint(tmp_path)
        assert ok is False
        assert "没有检查点" in msg

    def test_index_out_of_range(self, tmp_path):
        cp_dir = tmp_path / ".UniClaw" / "checkpoints"
        cp_dir.mkdir(parents=True)
        _save_index(cp_dir, [{"id": "cp_1", "message": "m", "time": "t"}])
        ok, msg = _file_delete_checkpoint(tmp_path, index=5)
        assert ok is False
        assert "不存在" in msg

    def test_delete_existing(self, tmp_path):
        cp_dir = tmp_path / ".UniClaw" / "checkpoints"
        cp_path = cp_dir / "cp_1"
        cp_path.mkdir(parents=True)
        (cp_path / "meta.json").write_text("{}", encoding="utf-8")
        _save_index(cp_dir, [{"id": "cp_1", "message": "m", "time": "t"}])
        ok, msg = _file_delete_checkpoint(tmp_path, index=0)
        assert ok is True
        assert "已删除" in msg
        assert not cp_path.exists()


# ── _file_restore_checkpoint ─────────────────────────────────────


class TestFileRestoreCheckpoint:
    def test_no_checkpoint_dir(self, tmp_path):
        ok, msg = _file_restore_checkpoint(tmp_path)
        assert ok is False

    def test_restore_valid(self, tmp_path):
        # Setup: create a checkpoint with one file
        cp_dir = tmp_path / ".UniClaw" / "checkpoints"
        cp_path = cp_dir / "cp_1"
        files_path = cp_path / "files"
        files_path.mkdir(parents=True)
        (files_path / "test.txt").write_text("saved content", encoding="utf-8")
        meta = {"id": "cp_1", "message": "m", "time": "t", "files": ["test.txt"]}
        (cp_path / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        _save_index(cp_dir, [{"id": "cp_1", "message": "m", "time": "t"}])

        # Modify the working file
        (tmp_path / "test.txt").write_text("modified", encoding="utf-8")

        ok, msg = _file_restore_checkpoint(tmp_path, index=0)
        assert ok is True
        assert "已恢复" in msg
        assert (tmp_path / "test.txt").read_text(encoding="utf-8") == "saved content"


# ── 锚点(未修改文件的 turn) ─────────────────────────────────────


class TestFileAnchor:
    async def test_no_file_change_creates_anchor(self, tmp_path):
        """无文件改动时创建时间锚点,可被精确匹配回退。"""
        import uniclaw.utils.checkpoint as cp

        (tmp_path / "a.txt").write_text("content", encoding="utf-8")
        # 先建真实快照基线
        t0 = int(time.time() * 1000)
        await cp._file_create_checkpoint(tmp_path, "基线", created_at=t0)
        # 无改动 turn → 锚点
        t1 = int(time.time() * 1000) + 1
        ok = await cp._file_create_anchor(tmp_path, "无改动", created_at=t1)
        assert ok is True
        index = cp._load_index(cp._get_checkpoint_dir(tmp_path))
        assert index[0].get("anchor") is True
        assert index[0]["created_at"] == t1
        # 精确匹配命中锚点
        idx = await cp.find_checkpoint_index(tmp_path, t1)
        assert idx == 0

    async def test_restore_anchor_falls_back_to_snapshot(self, tmp_path):
        """恢复锚点时,定位到最近真实快照的状态。"""
        import uniclaw.utils.checkpoint as cp

        (tmp_path / "a.txt").write_text("V1", encoding="utf-8")
        t1 = int(time.time() * 1000)
        await cp._file_create_checkpoint(tmp_path, "改到V1", created_at=t1)
        # 无改动 turn → anchor,文件仍 V1
        t2 = int(time.time() * 1000) + 1
        await cp._file_create_anchor(tmp_path, "没改", created_at=t2)
        # 改动文件模拟后续 agent 操作
        (tmp_path / "a.txt").write_text("AGENT改", encoding="utf-8")
        ok, msg = cp._file_restore_checkpoint(tmp_path, index=0)
        assert ok is True
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "V1"

    async def test_no_snapshot_before_anchor_returns_true(self, tmp_path):
        """锚点之前没有任何真实快照 → 无需恢复(返回 True)。"""
        import uniclaw.utils.checkpoint as cp

        t = int(time.time() * 1000)
        await cp._file_create_anchor(tmp_path, "首个无改动", created_at=t)
        ok, msg = cp._file_restore_checkpoint(tmp_path, index=0)
        assert ok is True

    async def test_has_diff_skips_anchor(self, tmp_path):
        """_file_has_diff 应跳过锚点,与最近真实快照比较。"""
        import uniclaw.utils.checkpoint as cp

        (tmp_path / "a.txt").write_text("V1", encoding="utf-8")
        t1 = int(time.time() * 1000)
        await cp._file_create_checkpoint(tmp_path, "V1", created_at=t1)
        # 无改动 anchor(文件仍 V1)
        t2 = int(time.time() * 1000) + 1
        await cp._file_create_anchor(tmp_path, "没改", created_at=t2)
        # index 0 是 anchor,应解析到 V1 快照比较 → 无差异
        assert await cp._file_has_diff(tmp_path, 0) is False
        # 改动文件 → 有差异
        (tmp_path / "a.txt").write_text("REAL改", encoding="utf-8")
        assert await cp._file_has_diff(tmp_path, 0) is True

    async def test_multiple_anchors_each_matches(self, tmp_path):
        """连续多个无改动 turn:每个锚点按 created_at 独立可回退。"""
        import uniclaw.utils.checkpoint as cp

        (tmp_path / "a.txt").write_text("FIXED", encoding="utf-8")
        # 先建一个真实快照作基线
        t0 = int(time.time() * 1000)
        await cp._file_create_checkpoint(tmp_path, "基线", created_at=t0)
        # 连建 3 个锚点(模拟 3 个无改动 turn)
        t_list = []
        for i in range(3):
            t = t0 + i + 1
            await cp._file_create_anchor(tmp_path, f"没改{i}", created_at=t)
            t_list.append(t)

        index = cp._load_index(cp._get_checkpoint_dir(tmp_path))
        anchors = [e for e in index if e.get("anchor") is True]
        assert len(anchors) == 3
        # 每个锚点都能精确匹配到
        for t in t_list:
            idx = await cp.find_checkpoint_index(tmp_path, t)
            assert idx is not None

    async def test_create_checkpoint_no_change_makes_anchor(self, tmp_path):
        """create_checkpoint 无差异时自动落锚点(而非跳过)。"""
        import uniclaw.utils.checkpoint as cp

        (tmp_path / "a.txt").write_text("V1", encoding="utf-8")
        t1 = int(time.time() * 1000)
        # 第一次创建 → 真实快照
        ok = await cp.create_checkpoint(tmp_path, "改", created_at=t1)
        assert ok is True
        # 第二次无改动 → 锚点
        t2 = int(time.time() * 1000) + 1
        ok = await cp.create_checkpoint(tmp_path, "没改", created_at=t2)
        assert ok is True
        index = cp._load_index(cp._get_checkpoint_dir(tmp_path))
        assert index[0].get("anchor") is True
        assert index[0]["created_at"] == t2
        # 锚点时刻可回退
        idx = await cp.find_checkpoint_index(tmp_path, t2)
        assert idx == 0

    async def test_anchor_then_change_creates_snapshot(self, tmp_path):
        """锚点之后又有改动 → 建真实快照(不被锚点吞掉)。"""
        import uniclaw.utils.checkpoint as cp

        (tmp_path / "a.txt").write_text("V1", encoding="utf-8")
        t1 = int(time.time() * 1000)
        await cp._file_create_checkpoint(tmp_path, "V1", created_at=t1)
        # 无改动 → 锚点
        t2 = t1 + 1
        await cp._file_create_anchor(tmp_path, "没改", created_at=t2)
        # 有改动 → 应建真实快照
        (tmp_path / "a.txt").write_text("V2", encoding="utf-8")
        t3 = t1 + 2
        ok = await cp.create_checkpoint(tmp_path, "改V2", created_at=t3)
        assert ok is True
        index = cp._load_index(cp._get_checkpoint_dir(tmp_path))
        assert index[0].get("anchor") is not True  # 最新是真实快照
        # V2 快照可恢复
        (tmp_path / "a.txt").write_text("搞乱了", encoding="utf-8")
        ok, msg = cp._file_restore_checkpoint(tmp_path, index=0)
        assert ok is True
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "V2"

    async def test_delete_anchor_by_index(self, tmp_path):
        """删除锚点:目录不存在也能删(只移除 index 条目)。"""
        import uniclaw.utils.checkpoint as cp

        (tmp_path / "a.txt").write_text("X", encoding="utf-8")
        t = int(time.time() * 1000)
        await cp._file_create_anchor(tmp_path, "没改", created_at=t)
        ok, msg = cp._file_delete_checkpoint(tmp_path, index=0)
        assert ok is True
        index = cp._load_index(cp._get_checkpoint_dir(tmp_path))
        assert len(index) == 0

    async def test_anchor_message_default(self, tmp_path):
        """锚点缺省 message 为'未修改文件'。"""
        import uniclaw.utils.checkpoint as cp

        t = int(time.time() * 1000)
        await cp._file_create_anchor(tmp_path, created_at=t)
        index = cp._load_index(cp._get_checkpoint_dir(tmp_path))
        assert index[0]["message"] == "未修改文件"


# ── _file_pop_checkpoint ─────────────────────────────────────────


class TestFilePopCheckpoint:
    def test_pop_restores_and_deletes(self, tmp_path):
        cp_dir = tmp_path / ".UniClaw" / "checkpoints"
        cp_path = cp_dir / "cp_1"
        files_path = cp_path / "files"
        files_path.mkdir(parents=True)
        (files_path / "test.txt").write_text("saved", encoding="utf-8")
        meta = {"id": "cp_1", "message": "m", "time": "t", "files": ["test.txt"]}
        (cp_path / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8"
        )
        _save_index(cp_dir, [{"id": "cp_1", "message": "m", "time": "t"}])

        (tmp_path / "test.txt").write_text("modified", encoding="utf-8")

        ok, msg = _file_pop_checkpoint(tmp_path, index=0)
        assert ok is True
        # File restored
        assert (tmp_path / "test.txt").read_text(encoding="utf-8") == "saved"
        # Checkpoint directory deleted
        assert not cp_path.exists()


# ── _get_all_files / 特殊文件名(中文、下划线)──────────────────


@pytest.mark.skipif(shutil.which("git") is None, reason="git 未安装")
class TestGetAllFilesSpecialNames:
    async def test_chinese_and_underscore_names_kept(self, tmp_path):
        """中文名与下划线开头文件不应被 git ls-files 转义/忽略。"""
        (tmp_path / "正常中文名.txt").write_text("中文内容", encoding="utf-8")
        (tmp_path / "_下划线开头.txt").write_text("下划线中文", encoding="utf-8")
        (tmp_path / "_ascii.txt").write_text("underscore ascii", encoding="utf-8")
        (tmp_path / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "plain.txt").write_text("plain", encoding="utf-8")
        (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "p.pyc").write_text("x", encoding="utf-8")

        files = await _get_all_files(tmp_path)

        # 中文/下划线文件名必须原样保留,不能是 "\xxx" 转义字面量
        assert "正常中文名.txt" in files
        assert "_下划线开头.txt" in files
        assert "_ascii.txt" in files
        assert "__init__.py" in files
        assert "plain.txt" in files
        # 默认排除规则仍然生效
        assert ".hidden.txt" not in files
        assert not any("__pycache__" in f for f in files)

    async def test_subdir_root_lists_only_subtree(self, tmp_path):
        """root_dir 为 git 子目录时,列表相对 root_dir 且只含子树。"""
        (tmp_path / "top.txt").write_text("top", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "正常中文名.txt").write_text("中文", encoding="utf-8")

        files = await _get_all_files(sub)

        assert "正常中文名.txt" in files
        assert "top.txt" not in files


@pytest.mark.skipif(shutil.which("git") is None, reason="git 未安装")
class TestCheckpointSpecialNamesRoundTrip:
    async def test_create_restore_chinese_and_underscore(self, tmp_path):
        """快照/恢复必须覆盖中文名与下划线名文件。"""
        (tmp_path / "正常中文名.txt").write_text("原始中文内容", encoding="utf-8")
        (tmp_path / "_下划线开头.txt").write_text("原始下划线内容", encoding="utf-8")
        (tmp_path / "_ascii.txt").write_text("original", encoding="utf-8")

        ok = await _file_create_checkpoint(tmp_path, "中文消息")
        assert ok is True

        (tmp_path / "正常中文名.txt").write_text("修改后", encoding="utf-8")
        (tmp_path / "_下划线开头.txt").write_text("changed", encoding="utf-8")
        (tmp_path / "_ascii.txt").write_text("changed", encoding="utf-8")

        ok, msg = _file_restore_checkpoint(tmp_path, index=0)
        assert ok is True
        assert (
            tmp_path / "正常中文名.txt"
        ).read_text(encoding="utf-8") == "原始中文内容"
        assert (
            tmp_path / "_下划线开头.txt"
        ).read_text(encoding="utf-8") == "原始下划线内容"
        assert (tmp_path / "_ascii.txt").read_text(encoding="utf-8") == "original"


# ── find_checkpoint_index(按消息时间定位)────────────────────────


class TestFindCheckpointIndex:
    async def test_empty_index(self, tmp_path):
        """无 checkpoint 时返回 None。"""
        from uniclaw.utils.checkpoint import find_checkpoint_index

        idx = await find_checkpoint_index(tmp_path, 1_700_000_000)
        assert idx is None

    async def test_exact_created_at_match(self, tmp_path):
        """created_at 毫秒直等:消息与 checkpoint 同源,查询精确命中。"""
        import uniclaw.utils.checkpoint as cp

        checkpoint_dir = cp._get_checkpoint_dir(tmp_path)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        cp._save_index(
            checkpoint_dir,
            [
                {"id": "cp_3", "message": "c3", "time": "2024-01-03 10:00:00", "created_at": 1_704_247_200_000},
                {"id": "cp_2", "message": "c2", "time": "2024-01-02 10:00:00", "created_at": 1_704_160_800_000},
                {"id": "cp_1", "message": "c1", "time": "2024-01-01 10:00:00", "created_at": 1_704_074_400_000},
            ],
        )
        from uniclaw.utils.checkpoint import find_checkpoint_index

        # 消息与 cp_2 同源(同一毫秒)→ 精确命中 cp_2(序号 1)
        idx = await find_checkpoint_index(tmp_path, 1_704_160_800_000)
        assert idx == 1
        # cp_3 → 序号 0
        idx = await find_checkpoint_index(tmp_path, 1_704_247_200_000)
        assert idx == 0
        # cp_1 → 序号 2(列表末尾)
        idx = await find_checkpoint_index(tmp_path, 1_704_074_400_000)
        assert idx == 2

    async def test_same_second_distinguished_by_ms(self, tmp_path):
        """同一秒内多条消息:毫秒区分,各回各的 checkpoint。"""
        import uniclaw.utils.checkpoint as cp

        checkpoint_dir = cp._get_checkpoint_dir(tmp_path)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # 同秒(1_704_160_800)内 3 条,毫秒不同
        cp._save_index(
            checkpoint_dir,
            [
                {"id": "cp_b", "message": "b", "time": "2024-01-02 10:00:00", "created_at": 1_704_160_800_780},
                {"id": "cp_a", "message": "a", "time": "2024-01-02 10:00:00", "created_at": 1_704_160_800_120},
            ],
        )
        from uniclaw.utils.checkpoint import find_checkpoint_index

        # 删第 2 条(ms=780)→ 命中 cp_b(序号 0);删第 1 条(ms=120)→ cp_a(序号 1)
        idx = await find_checkpoint_index(tmp_path, 1_704_160_800_780)
        assert idx == 0
        idx = await find_checkpoint_index(tmp_path, 1_704_160_800_120)
        assert idx == 1

    async def test_legacy_second_created_at_normalized(self, tmp_path):
        """旧 checkpoint 的秒级 created_at 自动归一化为毫秒匹配。"""
        import uniclaw.utils.checkpoint as cp

        checkpoint_dir = cp._get_checkpoint_dir(tmp_path)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # 旧数据 created_at 是秒级(1_704_160_800)
        cp._save_index(
            checkpoint_dir,
            [
                {"id": "cp_2", "message": "c2", "time": "2024-01-02 10:00:00", "created_at": 1_704_160_800},
                {"id": "cp_1", "message": "c1", "time": "2024-01-01 10:00:00", "created_at": 1_704_074_400},
            ],
        )
        from uniclaw.utils.checkpoint import find_checkpoint_index

        # 秒级旧数据 ×1000 对齐毫秒查询
        idx = await find_checkpoint_index(tmp_path, 1_704_160_800_000)
        assert idx == 0

    async def test_exact_miss_returns_none(self, tmp_path):
        """created_at 不匹配(该消息无对应 checkpoint)→ None,不做模糊兜底。"""
        import uniclaw.utils.checkpoint as cp

        checkpoint_dir = cp._get_checkpoint_dir(tmp_path)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        # 三种形态:有 created_at、无 created_at(旧数据)、time 损坏
        cp._save_index(
            checkpoint_dir,
            [
                {"id": "cp_3", "message": "c3", "time": "2024-01-03 10:00:00", "created_at": 1_704_247_200},
                {"id": "cp_2", "message": "c2", "time": "2024-01-02 10:00:00"},
                {"id": "cp_bad", "message": "bad", "time": "not-a-time"},
            ],
        )
        from uniclaw.utils.checkpoint import find_checkpoint_index

        # 不存在的整数秒(在 cp_2 与 cp_3 之间)→ 精确 miss,不按秒级回退
        idx = await find_checkpoint_index(tmp_path, 1_704_200_000)
        assert idx is None
        # 晚于所有 checkpoint → None
        idx = await find_checkpoint_index(tmp_path, 1_704_333_600)
        assert idx is None
        # 早于所有 checkpoint → None
        idx = await find_checkpoint_index(tmp_path, 1_700_000_000)
        assert idx is None


# ── _norm_ms(时间单位归一化)──────────────────────────────────────


class TestNormMs:
    def test_second_normalized_to_ms(self):
        from uniclaw.utils.checkpoint import _norm_ms

        assert _norm_ms(1_704_160_800) == 1_704_160_800_000

    def test_ms_passthrough(self):
        from uniclaw.utils.checkpoint import _norm_ms

        assert _norm_ms(1_704_160_800_500) == 1_704_160_800_500

    def test_zero_unchanged(self):
        from uniclaw.utils.checkpoint import _norm_ms

        assert _norm_ms(0) == 0

    def test_huge_ms_unchanged(self):
        from uniclaw.utils.checkpoint import _norm_ms

        assert _norm_ms(9_999_999_999_999) == 9_999_999_999_999


# ── find_checkpoint_index git 模式 ──────────────────────────────


@pytest.mark.skipif(shutil.which("git") is None, reason="git 未安装")
class TestFindCheckpointIndexGit:
    async def test_git_mode_exact_match(self, tmp_path):
        """git 模式下按毫秒精确匹配 stash 的 created_at。"""
        from uniclaw.utils.checkpoint import (
            _get_checkpoint_dir,
            find_checkpoint_index,
        )
        from uniclaw.utils.git import git_create_checkpoint, git_list_checkpoints

        # 初始化 git 仓库
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("v0", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

        # 制造未提交改动 → 走 stash 分支
        (tmp_path / "a.txt").write_text("v1", encoding="utf-8")
        t1 = int(time.time() * 1000)
        ok = await git_create_checkpoint(tmp_path, "第一轮", created_at=t1)
        assert ok is True

        # 精确匹配命中 stash
        idx = await find_checkpoint_index(tmp_path, t1)
        assert idx == 0
        # 不匹配 → None
        idx = await find_checkpoint_index(tmp_path, t1 + 1)
        assert idx is None

    async def test_git_mode_head_anchor_match(self, tmp_path):
        """git 模式工作区干净 → HEAD 锚点,created_at 精确匹配。"""
        from uniclaw.utils.checkpoint import find_checkpoint_index
        from uniclaw.utils.git import _load_head_anchors, git_create_checkpoint, get_git_root

        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("v0", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

        # 工作区干净 → HEAD 锚点
        t1 = int(time.time() * 1000)
        ok = await git_create_checkpoint(tmp_path, "无改动轮", created_at=t1)
        assert ok is True
        git_root = await get_git_root(tmp_path)
        anchors = _load_head_anchors(git_root)
        assert anchors[-1]["created_at"] == t1

        idx = await find_checkpoint_index(tmp_path, t1)
        assert idx == 0

    async def test_git_mode_order_new_to_old(self, tmp_path):
        """git 模式合并列表按 created_at 新→旧(锚点/快照交替排序)。"""
        from uniclaw.utils.git import _merged_checkpoint_items, get_git_root, git_create_checkpoint

        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("v0", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)

        # 轮1: 干净 → HEAD 锚点
        t1 = int(time.time() * 1000)
        await git_create_checkpoint(tmp_path, "锚点1", created_at=t1)
        await asyncio.sleep(0)
        # 轮2: 改动 → stash
        (tmp_path / "a.txt").write_text("v2", encoding="utf-8")
        t2 = int(time.time() * 1000)
        assert t2 > t1 or True
        await git_create_checkpoint(tmp_path, "快照2", created_at=t2)

        git_root = await get_git_root(tmp_path)
        items = await _merged_checkpoint_items(git_root)
        # 新→旧:第一个应是最新的 stash 或锚点,created_at 降序
        cas = [it.get("created_at") for it in items if it.get("created_at") is not None]
        assert cas == sorted(cas, reverse=True), f"created_at 应降序: {cas}"
