"""git 工具测试 — 覆盖 git 命令封装、worktree 和 stash 检查点。"""

import subprocess
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.utils import git as git_mod
from uniclaw.utils.git import (
    _get_stash_untracked_tree,
    _run_git,
    create_worktree,
    get_git_root,
    git_apply_checkpoint,
    git_create_checkpoint,
    git_delete_checkpoint,
    git_diff_between,
    git_diff_checkpoint,
    git_diff_current,
    git_generate_commit_message,
    git_list_checkpoints,
    git_pop_checkpoint,
    has_git_commit,
    is_git_installed,
    is_git_repo,
    remove_worktree,
)


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=(), returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestRunGit:
    """_run_git 测试。"""

    def _proc(self, returncode=0, out=b"", err=b""):
        proc = AsyncMock()
        proc.returncode = returncode
        proc.communicate.return_value = (out, err)
        return proc

    @pytest.mark.asyncio
    async def test_success(self):
        """成功执行。"""
        proc = self._proc(out=b"stdout data", err=b"stderr data")
        with patch("uniclaw.utils.git.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            result = await _run_git("git", "status", cwd="/repo")
        assert result.returncode == 0
        assert result.stdout == "stdout data"
        assert result.stderr == "stderr data"
        assert result.args == ("git", "status")

    @pytest.mark.asyncio
    async def test_failure_no_check(self):
        """失败但不 check。"""
        proc = self._proc(returncode=1, err=b"boom")
        with patch("uniclaw.utils.git.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            result = await _run_git("git", "status")
        assert result.returncode == 1
        assert result.stderr == "boom"

    @pytest.mark.asyncio
    async def test_check_raises(self):
        """check=True 时失败抛出。"""
        proc = self._proc(returncode=2, err=b"fatal")
        with patch("uniclaw.utils.git.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc):
            with pytest.raises(subprocess.CalledProcessError):
                await _run_git("git", "status", check=True)


class TestGetGitRoot:
    """get_git_root 测试。"""

    @pytest.mark.asyncio
    async def test_success(self):
        """成功返回根目录。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="/repo\n")):
            assert await get_git_root(Path("/x")) == "/repo"

    @pytest.mark.asyncio
    async def test_not_a_repo(self):
        """非仓库返回 None。"""
        with patch(
            "uniclaw.utils.git._run_git",
            new_callable=AsyncMock,
            side_effect=subprocess.CalledProcessError(128, "git"),
        ):
            assert await get_git_root(Path("/x")) is None


class TestIsGitInstalled:
    """is_git_installed 测试。"""

    @pytest.mark.asyncio
    async def test_installed(self):
        """已安装。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()):
            assert await is_git_installed() is True

    @pytest.mark.asyncio
    async def test_not_installed(self):
        """未安装。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=FileNotFoundError):
            assert await is_git_installed() is False


class TestIsGitRepo:
    """is_git_repo 测试。"""

    @pytest.mark.asyncio
    async def test_in_repo(self):
        """在仓库中。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"):
            assert await is_git_repo(Path("/x")) is True

    @pytest.mark.asyncio
    async def test_not_in_repo(self):
        """不在仓库中。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value=None):
            assert await is_git_repo(Path("/x")) is False

    @pytest.mark.asyncio
    async def test_no_root_dir(self):
        """无 root_dir 返回 True。"""
        assert await is_git_repo(None) is True


class TestHasGitCommit:
    """has_git_commit 测试。"""

    @pytest.mark.asyncio
    async def test_has_commit(self):
        """有 commit。"""
        with patch("uniclaw.utils.git.is_git_repo", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ):
            assert await has_git_commit(Path("/x")) is True

    @pytest.mark.asyncio
    async def test_no_commit(self):
        """无 commit。"""
        with patch("uniclaw.utils.git.is_git_repo", new_callable=AsyncMock, return_value=True), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(returncode=1)
        ):
            assert await has_git_commit(Path("/x")) is False

    @pytest.mark.asyncio
    async def test_not_repo(self):
        """非仓库。"""
        with patch("uniclaw.utils.git.is_git_repo", new_callable=AsyncMock, return_value=False):
            assert await has_git_commit(Path("/x")) is False


class TestWorktree:
    """worktree 创建与移除测试。"""

    @pytest.mark.asyncio
    async def test_create_worktree(self):
        """创建 worktree。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock) as m, patch(
            "uniclaw.utils.git.tempfile.mkdtemp", return_value="C:/tmp/wt-xyz"
        ), patch("uniclaw.utils.git.os.rmdir"):
            wt, branch = await create_worktree(Path("/base"))
        assert wt == "C:/tmp/wt-xyz"
        assert branch.startswith("nano-agent-")
        assert len(branch) > len("nano-agent-")
        args = m.await_args.args
        assert args[0] == "git"
        assert "worktree" in args
        assert branch in args

    @pytest.mark.asyncio
    async def test_remove_worktree(self):
        """移除 worktree。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock) as m:
            await remove_worktree(Path("C:/tmp/wt"), "branch1", Path("/base"))
        assert m.await_count == 2

    @pytest.mark.asyncio
    async def test_remove_worktree_errors(self):
        """移除失败不抛出。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=Exception("boom")):
            await remove_worktree(Path("C:/tmp/wt"), "branch1", Path("/base"))


class TestGitCreateCheckpoint:
    """git_create_checkpoint 测试。"""

    @pytest.mark.asyncio
    async def test_not_repo(self):
        """非仓库返回 False。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value=None):
            assert await git_create_checkpoint(Path("/x")) is False

    @pytest.mark.asyncio
    async def test_stash_push_fails(self):
        """stash push 失败。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git",
            new_callable=AsyncMock,
            return_value=_result(returncode=1, stderr="no changes"),
        ):
            assert await git_create_checkpoint(Path("/x")) is False

    @pytest.mark.asyncio
    async def test_success(self):
        """成功创建。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ) as m:
            assert await git_create_checkpoint(Path("/x")) is True
        assert m.await_count == 2  # stash push + stash apply

    @pytest.mark.asyncio
    async def test_message_used(self):
        """消息传入 stash。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ) as m:
            await git_create_checkpoint(Path("/x"), message="检查点描述")
        args = m.await_args_list[0].args
        assert args[0] == "git"
        assert args[1] == "stash"
        assert args[2] == "push"
        assert "-m" in args

    @pytest.mark.asyncio
    async def test_message_truncated(self):
        """消息截取前 50 字符。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ) as m:
            await git_create_checkpoint(Path("/x"), message="x" * 80)
        args = m.await_args_list[0].args
        msg_idx = args.index("-m") + 1
        assert len(args[msg_idx]) == 50


class TestRestoreHelper:
    """stash 恢复测试。"""

    def _rev_result(self):
        return _result(stdout="/repo\n")

    @pytest.mark.asyncio
    async def test_not_repo(self):
        """非仓库。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value=None):
            ok, msg = await git_pop_checkpoint(Path("/x"))
        assert ok is False
        assert "不在 git 仓库" in msg

    @pytest.mark.asyncio
    async def test_pop_success(self):
        """pop 成功。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=[self._rev_result(), _result()]):
            ok, msg = await git_pop_checkpoint(Path("/x"))
        assert ok is True
        assert "已恢复并删除" in msg

    @pytest.mark.asyncio
    async def test_apply_success(self):
        """apply 成功。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=[self._rev_result(), _result()]):
            ok, msg = await git_apply_checkpoint(Path("/x"))
        assert ok is True
        assert "已恢复" in msg
        assert "删除" not in msg

    @pytest.mark.asyncio
    async def test_conflict_recovered(self):
        """冲突后强制恢复成功。"""
        results = [
            self._rev_result(),
            _result(returncode=1, stderr="error: your local changes would be overwritten"),
            _result(),  # checkout
            _result(),  # clean
            _result(),  # 第二次 stash
        ]
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=results):
            ok, msg = await git_pop_checkpoint(Path("/x"))
        assert ok is True
        assert "已恢复并删除" in msg

    @pytest.mark.asyncio
    async def test_conflict_recovery_fails(self):
        """冲突后强制恢复失败。"""
        results = [
            self._rev_result(),
            _result(returncode=1, stderr="error: would be overwritten"),
            _result(),
            _result(),
            _result(returncode=1, stderr="conflict persists"),
        ]
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=results):
            ok, msg = await git_pop_checkpoint(Path("/x"))
        assert ok is False

    @pytest.mark.asyncio
    async def test_other_error(self):
        """其他错误。"""
        with patch("uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=[self._rev_result(), _result(returncode=1, stderr="nothing to pop")]):
            ok, msg = await git_pop_checkpoint(Path("/x"))
        assert ok is False
        assert "nothing to pop" in msg


class TestGitDeleteCheckpoint:
    """git_delete_checkpoint 测试。"""

    @pytest.mark.asyncio
    async def test_not_repo(self):
        """非仓库。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value=None):
            ok, msg = await git_delete_checkpoint(Path("/x"))
        assert ok is False
        assert "不在 git 仓库" in msg

    @pytest.mark.asyncio
    async def test_index_out_of_range(self):
        """序号越界。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="")
        ):
            ok, msg = await git_delete_checkpoint(Path("/x"), index=0)
        assert ok is False
        assert "不存在" in msg

    @pytest.mark.asyncio
    async def test_success(self):
        """删除成功。"""
        stash_line = "stash@{0}: WIP on master: 12345 消息"
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git",
            new_callable=AsyncMock,
            side_effect=[_result(stdout=stash_line), _result()],
        ):
            ok, msg = await git_delete_checkpoint(Path("/x"))
        assert ok is True
        assert "已删除检查点" in msg


class TestGitDiff:
    """diff 与 list 测试。"""

    @pytest.mark.asyncio
    async def test_diff_checkpoint_not_repo(self):
        """非仓库。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value=None):
            assert await git_diff_checkpoint(Path("/x")) == "不在 git 仓库中"

    @pytest.mark.asyncio
    async def test_diff_checkpoint(self):
        """有变更。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="+change")
        ), patch("uniclaw.utils.git._diff_stash_untracked_vs_workdir", new_callable=AsyncMock, return_value=""):
            result = await git_diff_checkpoint(Path("/x"))
        assert "+change" in result

    @pytest.mark.asyncio
    async def test_diff_checkpoint_no_changes(self):
        """无变更。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ), patch("uniclaw.utils.git._diff_stash_untracked_vs_workdir", new_callable=AsyncMock, return_value=""):
            result = await git_diff_checkpoint(Path("/x"))
        assert result == "检查点与当前文件没有差异"

    @pytest.mark.asyncio
    async def test_diff_current(self):
        """当前变更。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="diff --git")
        ):
            result = await git_diff_current(Path("/x"))
        assert "diff --git" in result

    @pytest.mark.asyncio
    async def test_diff_current_no_changes(self):
        """无当前变更。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ):
            result = await git_diff_current(Path("/x"))
        assert result == "当前没有未提交的变更"

    @pytest.mark.asyncio
    async def test_diff_between(self):
        """比较两个检查点。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="between")
        ), patch("uniclaw.utils.git._diff_stash_untracked_between", new_callable=AsyncMock, return_value=""):
            result = await git_diff_between(Path("/x"), 0, 1)
        assert "between" in result

    @pytest.mark.asyncio
    async def test_list_checkpoints(self):
        """列出检查点。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="stash@{0}: WIP")
        ):
            result = await git_list_checkpoints(Path("/x"))
        assert "stash@{0}" in result

    @pytest.mark.asyncio
    async def test_list_checkpoints_empty(self):
        """无检查点。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ):
            result = await git_list_checkpoints(Path("/x"))
        assert result == "没有检查点"


class TestGetStashUntrackedTree:
    """_get_stash_untracked_tree 测试。"""

    @pytest.mark.asyncio
    async def test_no_parent3(self):
        """无第三个 parent。"""
        with patch(
            "uniclaw.utils.git._run_git",
            new_callable=AsyncMock,
            side_effect=[_result(returncode=1), _result(stdout="tree abc")],
        ):
            assert await _get_stash_untracked_tree(Path("/x"), "stash@{0}") is None

    @pytest.mark.asyncio
    async def test_tree_found(self):
        """找到 tree。"""
        with patch(
            "uniclaw.utils.git._run_git",
            new_callable=AsyncMock,
            side_effect=[_result(stdout="abc123"), _result(stdout="tree 4b825d\nmode 100644 blob x file")],
        ):
            result = await _get_stash_untracked_tree(Path("/x"), "stash@{0}")
        assert result == "4b825d"

    @pytest.mark.asyncio
    async def test_cat_file_fails(self):
        """cat-file 失败。"""
        with patch(
            "uniclaw.utils.git._run_git",
            new_callable=AsyncMock,
            side_effect=[_result(stdout="abc123"), _result(returncode=1)],
        ):
            assert await _get_stash_untracked_tree(Path("/x"), "stash@{0}") is None


class TestGenerateCommitMessage:
    """git_generate_commit_message 测试。"""

    @pytest.mark.asyncio
    async def test_not_repo(self):
        """非仓库。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value=None):
            result = await git_generate_commit_message(Path("/x"), config=MagicMock())
        assert result == {"error": "当前目录不是 git 仓库"}

    @pytest.mark.asyncio
    async def test_no_changes(self):
        """无变更。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result()
        ):
            result = await git_generate_commit_message(Path("/x"), config=MagicMock())
        assert result == {"error": "未检测到变化"}

    @pytest.mark.asyncio
    async def test_success_cached(self):
        """优先暂存区 diff。"""
        resp = MagicMock()
        resp.content = "feat(agent): 添加功能"
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="+cached change")
        ) as m, patch("uniclaw.provider.fallback.achat", new_callable=AsyncMock, return_value=resp), patch(
            "uniclaw.tools.session.session.Session"
        ):
            result = await git_generate_commit_message(Path("/x"), config=MagicMock())
        assert result == {"message": "feat(agent): 添加功能"}
        # 只调用一次 diff --cached
        assert m.await_count == 1

    @pytest.mark.asyncio
    async def test_fallback_to_working_tree(self):
        """fallback 到未暂存 diff。"""
        resp = MagicMock()
        resp.content = "fix: 修复 bug"
        results = [_result(stdout=""), _result(stdout="+working change")]
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, side_effect=results
        ) as m, patch("uniclaw.provider.fallback.achat", new_callable=AsyncMock, return_value=resp), patch(
            "uniclaw.tools.session.session.Session"
        ):
            result = await git_generate_commit_message(Path("/x"), config=MagicMock())
        assert result == {"message": "fix: 修复 bug"}
        assert m.await_count == 2

    @pytest.mark.asyncio
    async def test_diff_truncated(self):
        """超长 diff 截断。"""
        resp = MagicMock()
        resp.content = "chore: 调整"
        long_diff = "+" * 40000
        session = MagicMock()
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout=long_diff)
        ), patch("uniclaw.provider.fallback.achat", new_callable=AsyncMock, return_value=resp), patch(
            "uniclaw.tools.session.session.Session", return_value=session
        ):
            result = await git_generate_commit_message(Path("/x"), config=MagicMock())
        assert result == {"message": "chore: 调整"}
        content = session.add_user_message.call_args[1]["content"]
        assert "... (diff 过长" in content

    @pytest.mark.asyncio
    async def test_ai_failure(self):
        """AI 生成失败。"""
        with patch("uniclaw.utils.git.get_git_root", new_callable=AsyncMock, return_value="/repo"), patch(
            "uniclaw.utils.git._run_git", new_callable=AsyncMock, return_value=_result(stdout="+change")
        ), patch("uniclaw.provider.fallback.achat", new_callable=AsyncMock, side_effect=RuntimeError("boom")), patch(
            "uniclaw.tools.session.session.Session"
        ):
            result = await git_generate_commit_message(Path("/x"), config=MagicMock())
        assert "error" in result
        assert "AI 生成失败" in result["error"]
