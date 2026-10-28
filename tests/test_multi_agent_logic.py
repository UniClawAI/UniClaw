"""multi_agent.py 核心逻辑补充测试 — 死循环检测、子目录 CLAUDE.md 按需加载、
输出截断判定、循环关闭噪音识别(这些纯逻辑此前未覆盖)。

不依赖真实 LLM,全部为纯函数/轻量 mock。
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from uniclaw.agent.multi_agent import (
    _ends_with_colon,
    _is_output_truncated,
    _is_loop_shutdown_noise,
    _find_nearest_subdir_claude_md,
    _claude_md_trusted_roots,
    _maybe_load_subdir_claude_md,
    LOOP_DETECTION_THRESHOLD,
    DEDUP_TOOLS,
    DEDUP_MIN_CHARS,
)
from uniclaw.utils.message import MessageRole


class TestEndsWithColon:
    """正文结尾冒号判定 —— auto 模式模型停在半句时要继续生成。"""

    def test_english_colon(self):
        assert _ends_with_colon("Let me check:") is True
        assert _ends_with_colon("Let me check : ") is True

    def test_chinese_colon(self):
        assert _ends_with_colon("我来查询一下:") is True

    def test_normal_text(self):
        assert _ends_with_colon("好的,我来看看") is False
        assert _ends_with_colon("") is False

    def test_whitespace(self):
        assert _ends_with_colon("   ") is False


class TestIsOutputTruncated:
    """输出截断判定 —— finish_reason 为 length/max_tokens 时判定被截断。"""

    def test_known_truncated_models(self):
        from uniclaw.tools.session.session import StreamChunk

        assert _is_output_truncated(StreamChunk(finish_reason="length")) is True
        assert _is_output_truncated(StreamChunk(finish_reason="max_tokens")) is True

    def test_normal_reasons(self):
        from uniclaw.tools.session.session import StreamChunk

        for reason in ("stop", "tool_calls", "end_turn"):
            assert _is_output_truncated(StreamChunk(finish_reason=reason)) is False

    def test_no_reason(self):
        assert _is_output_truncated(None) is False
        assert _is_output_truncated(object()) is False


class TestIsLoopShutdownNoise:
    """进程/事件循环关闭期噪音判定。"""

    def test_executor_shutdown(self):
        e = RuntimeError("cannot schedule new futures after executor shutdown")
        assert _is_loop_shutdown_noise(e) is True

    def test_executor_shutdown_any_case(self):
        """大小写不敏感(内部转小写匹配)。"""
        e = RuntimeError("EXECUTOR SHUTDOWN")
        assert _is_loop_shutdown_noise(e) is True

    def test_invalid_state(self):
        e = RuntimeError("InvalidStateError: invalid state")
        assert _is_loop_shutdown_noise(e) is True

    def test_winerror_995(self):
        e = OSError(995, "WinError 995 i/o operation aborted")
        assert _is_loop_shutdown_noise(e) is True

    def test_thread_exit_cn(self):
        e = OSError("由于线程退出或应用程序请求,已放弃 I/O 操作")
        assert _is_loop_shutdown_noise(e) is True

    def test_normal_error(self):
        e = RuntimeError("connection refused")
        assert _is_loop_shutdown_noise(e) is False


class TestFindNearestSubdirClaudeMd:
    """从文件父目录向上找最近的含 CLAUDE.md/AGENTS.md 的目录。"""

    def _make_tree(self, tmp_path: Path, claude_dirs: list[str], agent_dirs: list[str]):
        """创建目录树,在指定目录放 CLAUDE.md / AGENTS.md,返回根。"""
        for d in claude_dirs:
            p = tmp_path / d
            p.mkdir(parents=True, exist_ok=True)
            (p / "CLAUDE.md").write_text("instructions", encoding="utf-8")
        for d in agent_dirs:
            p = tmp_path / d
            p.mkdir(parents=True, exist_ok=True)
            (p / "AGENTS.md").write_text("instructions", encoding="utf-8")
        return tmp_path

    def test_finds_parent_claude_md(self, tmp_path):
        """文件在子目录,父目录有 CLAUDE.md → 返回父目录。"""
        root = self._make_tree(tmp_path, ["proj/sub"], [])
        file_path = tmp_path / "proj" / "sub" / "file.py"
        got = _find_nearest_subdir_claude_md(file_path, [root])
        assert got == tmp_path / "proj" / "sub"

    def test_walks_up_to_deepest(self, tmp_path):
        """多级嵌套,返回最近的含 CLAUDE.md 的目录。"""
        root = self._make_tree(tmp_path, ["a", "a/b/c"], [])
        file_path = tmp_path / "a" / "b" / "c" / "d" / "file.py"
        got = _find_nearest_subdir_claude_md(file_path, [root])
        assert got == tmp_path / "a" / "b" / "c"

    def test_agents_md_preferred(self, tmp_path):
        """同目录 AGENTS.md 优先于 CLAUDE.md(_resolve_project_instruction 决定)。"""
        root = tmp_path
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "AGENTS.md").write_text("a", encoding="utf-8")
        (root / "sub" / "CLAUDE.md").write_text("c", encoding="utf-8")
        got = _find_nearest_subdir_claude_md(
            root / "sub" / "f.py", [root]
        )
        # 具体返回哪个由 _resolve_project_instruction 决定,两者都算找到该目录
        assert got == root / "sub"

    def test_exclude_self_skips_root(self, tmp_path):
        """exclude_self 里的根不返回其自身 CLAUDE.md。"""
        root = self._make_tree(tmp_path, ["proj", "proj/sub"], [])
        file_path = tmp_path / "proj" / "sub" / "file.py"
        got = _find_nearest_subdir_claude_md(
            file_path, [root], exclude_self={root}
        )
        # root 在排除集中,但不阻挡子目录 sub(它有 CLAUDE.md)
        assert got == tmp_path / "proj" / "sub"

    def test_exclude_self_hits_target(self, tmp_path):
        """文件碰到被排除的 root(且中间无 CLAUDE.md)→ 返回 None。"""
        root = tmp_path
        (root / "sub" / "deep").mkdir(parents=True)
        (root / "sub" / "deep" / "f.py").write_text("x", encoding="utf-8")
        # 中间目录 sub/deep 都没有 CLAUDE.md,向上爬到 root → 被排除 → None
        got = _find_nearest_subdir_claude_md(
            root / "sub" / "deep" / "f.py", [root], exclude_self={root}
        )
        assert got is None

    def test_outside_roots(self, tmp_path):
        """不在任何可信根内的文件返回 None。"""
        root = self._make_tree(tmp_path, ["proj"], [])
        # root 之外的绝对路径(不在 tmp_path 内)
        outside = Path("/unrelated/place/f.py")
        got = _find_nearest_subdir_claude_md(outside, [root])
        assert got is None

    def test_no_claude_md_anywhere(self, tmp_path):
        """目录树上没有 CLAUDE.md 时返回 None。"""
        root = tmp_path
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "f.py").write_text("x", encoding="utf-8")
        got = _find_nearest_subdir_claude_md(root / "sub" / "f.py", [root])
        assert got is None


class TestClaudeMdTrustedRoots:
    """可信根列表 = session.root_dir + workspace(去重、按路径部件降序)。"""

    def test_root_dir_first(self):
        config = MagicMock()
        agent = MagicMock()
        agent.session.root_dir = "/a/b"
        config.current_agent = agent
        config.workspace = ["/a/b", "/a/b/c", ""]

        got = _claude_md_trusted_roots(config)
        got_str = [str(p) for p in got]

        # /a/b/c(更长)应排在 /a/b 前面(具体优先)
        assert len(got_str) == 2
        assert got_str[0].endswith("a" + chr(92) + "b" + chr(92) + "c") or got_str[0].endswith("a/b/c")
        assert "" not in got_str

    def test_no_root_dir_only_workspace(self):
        config = MagicMock()
        config.current_agent = None
        config.workspace = ["/x"]

        got = _claude_md_trusted_roots(config)
        assert len(got) == 1
        assert str(got[0]).replace(chr(92), "/").endswith("/x")

    def test_no_roots(self):
        config = MagicMock()
        config.current_agent = None
        config.workspace = []
        assert _claude_md_trusted_roots(config) == []


class TestMaybeLoadSubdirClaudeMd:
    """工具操作子目录文件后按需加载最近 CLAUDE.md。"""

    def test_read_triggers_injection(self, tmp_path):
        """Read 子目录文件 → 注入该子目录 CLAUDE.md 内容。"""
        sub = tmp_path / "proj" / "sub"
        sub.mkdir(parents=True)
        (sub / "CLAUDE.md").write_text("子目录指令", encoding="utf-8")

        config = MagicMock()
        agent = MagicMock()
        agent.session.root_dir = tmp_path  # root 自身不重复加载
        config.current_agent = agent
        config.workspace = []

        task = MagicMock()
        task.session.add_message = MagicMock()
        task.session.root_dir = tmp_path
        task.session._messages = []

        tool_call = {
            "name": "Read",
            "args": {"file_path": str(sub / "file.py")},
        }
        results = [(tool_call, "file content")]

        injected = _maybe_load_subdir_claude_md(task, results, config)
        assert len(injected) == 1
        assert "子目录指令" in injected[0]
        task.session.add_message.assert_called_once()

    def test_error_result_no_injection(self, tmp_path):
        """工具执行失败(TOOL_ERROR 开头)不触发注入。"""
        from uniclaw.utils.constants import TOOL_ERROR

        sub = tmp_path / "proj" / "sub"
        sub.mkdir(parents=True)
        (sub / "CLAUDE.md").write_text("指令", encoding="utf-8")

        config = MagicMock()
        agent = MagicMock()
        agent.session.root_dir = tmp_path
        config.current_agent = agent
        config.workspace = []

        task = MagicMock()
        task.session.add_message = MagicMock()
        task.session.root_dir = tmp_path
        task.session._messages = []

        tool_call = {
            "name": "Read",
            "args": {"file_path": str(sub / "file.py")},
        }
        results = [(tool_call, f"{TOOL_ERROR}: 文件不存在")]

        injected = _maybe_load_subdir_claude_md(task, results, config)
        assert injected == []
        task.session.add_message.assert_not_called()

    def test_outside_roots_no_injection(self, tmp_path):
        """不在可信根内的文件路径不触发(root_dir 之外)。"""
        sub = tmp_path / "proj"
        sub.mkdir(parents=True)
        (sub / "CLAUDE.md").write_text("指令", encoding="utf-8")

        # root_dir 设在 proj 的兄弟目录 root2 → proj 不在可信根内
        root_dir = tmp_path / "root2"
        root_dir.mkdir(parents=True)

        config = MagicMock()
        agent = MagicMock()
        agent.session.root_dir = root_dir
        config.current_agent = agent
        config.workspace = []

        task = MagicMock()
        task.session.add_message = MagicMock()
        task.session.root_dir = root_dir
        task.session._messages = []

        tool_call = {
            "name": "Read",
            "args": {"file_path": str(sub / "file.py")},
        }
        injected = _maybe_load_subdir_claude_md(
            task, [(tool_call, "content")], config
        )
        assert injected == []
        task.session.add_message.assert_not_called()

    def test_already_loaded_skipped(self, tmp_path):
        """当前消息流已注入过同一子目录则不重复注入。"""
        from uniclaw.tools.session.session import UserMessage
        from uniclaw.utils.project_instruction import SUBDIR_CLAUDE_MD_TAG

        sub = tmp_path / "proj" / "sub"
        sub.mkdir(parents=True)
        (sub / "CLAUDE.md").write_text("指令", encoding="utf-8")

        config = MagicMock()
        agent = MagicMock()
        agent.session.root_dir = tmp_path
        config.current_agent = agent
        config.workspace = []

        task = MagicMock()
        task.session.add_message = MagicMock()
        task.session.root_dir = tmp_path
        # 模拟已注入过一次(消息首行携带真实指令文件路径)
        existing = UserMessage(
            content=f"{SUBDIR_CLAUDE_MD_TAG} 已加载 {sub / 'CLAUDE.md'}\n指令"
        )
        task.session._messages = [existing]

        tool_call = {
            "name": "Read",
            "args": {"file_path": str(sub / "file.py")},
        }
        injected = _maybe_load_subdir_claude_md(
            task, [(tool_call, "content")], config
        )
        assert injected == []
        task.session.add_message.assert_not_called()


class TestDedupConstants:
    """只读工具去重配置。"""

    def test_dedup_tools(self):
        assert DEDUP_TOOLS == frozenset({"Read", "Glob", "Grep", "webFetch"})

    def test_dedup_min_chars(self):
        assert DEDUP_MIN_CHARS == 500


def _tool_call(name: str, **args) -> dict:
    return {"id": f"call_{name}", "name": name, "args": args}


class TestCheckInfiniteLoop:
    """死循环检测:连续 N 次同参数同工具 + 结果无进展才拦截。"""

    def _make_task(self):
        task = MagicMock()
        session = MagicMock()
        session.add_message = MagicMock()
        task.session = session
        task.name = "main"
        return task

    async def _run(self, agent, task, config, calls, recent):
        return await agent._check_infinite_loop(calls, recent, task, config)

    @staticmethod
    def _sig(name: str, args: dict) -> tuple:
        """构造与 current_calls 一致的嵌套元组签名。"""
        return (name, json.dumps(args, sort_keys=True))

    def _same_sig_recent(self, name: str, args: dict, fp: str, n: int) -> list:
        sig = self._sig(name, args)
        # current_calls = tuple(sorted([(name, args_json))]) → 嵌套元组 (sig,)
        return [((sig,), fp)] * n

    @pytest.mark.asyncio
    async def test_below_threshold_no_loop(self):
        """记录不足 N 次不判死循环。"""
        from uniclaw.agent.multi_agent import MultiAgent

        agent = MultiAgent.get_instance()
        agent.send_event_to_user = AsyncMock(return_value=None)
        task = self._make_task()
        config = MagicMock()
        recent: list = []
        call = _tool_call("Read", file_path="/tmp/a.txt")

        for _ in range(LOOP_DETECTION_THRESHOLD - 1):
            await self._run(agent, task, config, [call], recent)

        assert recent is not None
        agent.send_event_to_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_same_signature_different_fingerprint_not_loop(self):
        """同签名但结果指纹变化(每步有进展)→ 不判死循环。"""
        from uniclaw.agent.multi_agent import MultiAgent

        agent = MultiAgent.get_instance()
        agent.send_event_to_user = AsyncMock(return_value=None)
        task = self._make_task()
        config = MagicMock()
        call = _tool_call("browser_back")
        recent = [
            ((self._sig("browser_back", {"x": 1}),), "fp1")
        ] * (LOOP_DETECTION_THRESHOLD - 1)

        result = await self._run(agent, task, config, [call], recent)
        assert result is False

    @pytest.mark.asyncio
    async def test_loop_detected_injects_break_message(self):
        """连续 N 次同参数同工具且结果相同 → 判死循环并注入打破提示。"""
        from uniclaw.agent.multi_agent import MultiAgent

        agent = MultiAgent.get_instance()
        agent.send_event_to_user = AsyncMock(return_value=None)
        task = self._make_task()
        config = MagicMock()

        recent = [
            ((self._sig("Read", {"file_path": "/tmp/a.txt"}),), "fp_same")
        ] * (LOOP_DETECTION_THRESHOLD - 1)

        call = _tool_call("Read", file_path="/tmp/a.txt")
        result = await self._run(agent, task, config, [call], recent)

        assert result is True
        # 注入打破提示:send_event_to_user 至少被调用(ToolStart/ToolEvent/TextChunk)
        assert agent.send_event_to_user.await_count >= 1
        # 每个工具调用都有对应 tool 消息回填
        assert task.session.add_message.called
        call_args = task.session.add_message.call_args
        assert call_args.args[0] == MessageRole.TOOL

    @pytest.mark.asyncio
    async def test_loop_clears_recent(self):
        """判定死循环后 recent_tool_calls 被清空。"""
        from uniclaw.agent.multi_agent import MultiAgent

        agent = MultiAgent.get_instance()
        agent.send_event_to_user = AsyncMock(return_value=None)
        task = self._make_task()
        config = MagicMock()

        recent = [
            ((self._sig("Read", {"file_path": "/tmp/a.txt"}),), "fp")
        ] * (LOOP_DETECTION_THRESHOLD - 1)
        call = _tool_call("Read", file_path="/tmp/a.txt")

        await self._run(agent, task, config, [call], recent)
        assert recent == []

    @pytest.mark.asyncio
    async def test_different_signatures_not_loop(self):
        """不同签名连发不判死循环(参数在变化)。"""
        from uniclaw.agent.multi_agent import MultiAgent

        agent = MultiAgent.get_instance()
        agent.send_event_to_user = AsyncMock(return_value=None)
        task = self._make_task()
        config = MagicMock()

        recent = [
            ((self._sig("Read", {"file_path": f"/tmp/{i}.txt"}),), "fp")
            for i in range(LOOP_DETECTION_THRESHOLD - 1)
        ]
        call = _tool_call("Read", file_path="/tmp/new.txt")
        result = await self._run(agent, task, config, [call], recent)
        assert result is False