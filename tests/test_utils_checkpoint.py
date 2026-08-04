"""tests for src/uniclaw/utils/checkpoint.py — 检查点系统。"""

import json
from pathlib import Path

from uniclaw.utils.checkpoint import (
    _file_delete_checkpoint,
    _file_diff_two_dirs,
    _file_has_diff_sync,
    _file_list_checkpoints,
    _file_pop_checkpoint,
    _file_restore_checkpoint,
    _generate_diff,
    _load_gitignore,
    _load_index,
    _read_file_content,
    _save_index,
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


# ── _load_gitignore ──────────────────────────────────────────────


class TestLoadGitignore:
    def test_no_gitignore_default_rules(self, tmp_path):
        spec = _load_gitignore(tmp_path)
        assert spec.match_file(".hidden")
        assert spec.match_file("__pycache__/")
        assert spec.match_file("node_modules/")
        assert not spec.match_file("main.py")

    def test_custom_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
        spec = _load_gitignore(tmp_path)
        assert spec.match_file("app.log")
        assert spec.match_file("build/output.js")
        assert not spec.match_file("main.py")


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
