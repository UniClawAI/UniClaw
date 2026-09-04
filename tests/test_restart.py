"""进程重启工具测试 — 覆盖参数校验、调度防重入、标志文件读写和唤醒分支。"""

import asyncio
import json
import time
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from uniclaw.agent import AgentStatus
from uniclaw.config import RunMode
from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.restart import get_tools, get_all_tools
from uniclaw.tools.restart.manager import find_running_other_sessions
from uniclaw.tools.restart.tools import restart_agent
from uniclaw.tools.session.session import SessionType
from uniclaw.utils.constants import TOOL_ERROR


def _fake_create_task(coro):
    """模拟 asyncio.create_task:关闭协程避免 'never awaited' 警告。"""
    coro.close()
    return MagicMock()


@pytest.fixture(autouse=True)
def _reset_restart_state():
    """每个测试前后重置模块级重启状态。"""
    from uniclaw.tools.restart import manager

    manager.restart_scheduled = False
    yield
    manager.restart_scheduled = False


def _make_config(run_mode=RunMode.WEBUI, session_type=SessionType.CONSOLE):
    """构造用于校验逻辑的 mock 配置。"""
    config = MagicMock()
    config.run_mode = run_mode
    config.is_wechat = False
    session = MagicMock()
    session.id = "sess_test"
    session.title = "测试会话"
    session.session_type = session_type
    task = MagicMock()
    task.session = session
    task.status = AgentStatus.RUNNING
    config.current_agent = task
    return config


class TestRestartRegistration:
    """工具注册测试。"""

    def test_get_tools_webui_only(self):
        """仅 WebUI 模式返回工具,其他模式返回空列表。"""
        from uniclaw.config import RunMode

        assert get_tools(config=_make_config()) == [restart_agent]
        assert get_tools(config=_make_config(run_mode=RunMode.CONSOLE)) == []
        assert get_tools(config=None) == []

    def test_get_all_tools_unconditional(self):
        """get_all_tools 无条件返回(registry BM25 索引依赖)。"""
        assert len(get_all_tools()) == 1


class TestRestartAgentValidation:
    """restart_agent 前置校验测试。"""

    @pytest.mark.asyncio
    async def test_console_mode_rejected(self):
        """Console 模式下拒绝重启。"""
        result = await restart_agent(tool_runtime=ToolRuntime(config=_make_config(run_mode=RunMode.CONSOLE)))
        assert TOOL_ERROR in result
        assert "WebUI" in result

    @pytest.mark.asyncio
    async def test_none_config_rejected(self):
        """config 缺失时拒绝重启。"""
        result = await restart_agent(tool_runtime=ToolRuntime(config=None))
        assert TOOL_ERROR in result

    @pytest.mark.asyncio
    async def test_a2a_session_rejected(self):
        """A2A 会话不支持重启(会话不持久化)。"""
        result = await restart_agent(tool_runtime=ToolRuntime(config=_make_config(session_type=SessionType.A2A)))
        assert TOOL_ERROR in result

    @pytest.mark.asyncio
    async def test_wechat_session_rejected(self):
        """WeChat 会话不支持重启。"""
        result = await restart_agent(
            tool_runtime=ToolRuntime(config=_make_config(session_type=SessionType.WECHAT))
        )
        assert TOOL_ERROR in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.restart.manager.find_running_other_sessions")
    async def test_other_sessions_running_rejected(self, mock_find):
        """其他会话有 agent 运行时拒绝重启。"""
        mock_find.return_value = ["其他会话"]
        result = await restart_agent(tool_runtime=ToolRuntime(config=_make_config()))
        assert TOOL_ERROR in result
        assert "其他会话" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.restart.tools.asyncio")
    async def test_schedule_success(self, mock_asyncio):
        """WebUI 模式下成功调度重启。"""
        mock_asyncio.create_task = _fake_create_task
        from uniclaw.tools.restart import manager

        result = await restart_agent(tool_runtime=ToolRuntime(config=_make_config()))
        assert "已调度" in result
        assert manager.restart_scheduled is True

    @pytest.mark.asyncio
    @patch("uniclaw.tools.restart.tools.asyncio")
    async def test_double_schedule_rejected(self, mock_asyncio):
        """重复调度被拒绝。"""
        mock_asyncio.create_task = _fake_create_task
        from uniclaw.tools.restart import manager

        first = await restart_agent(tool_runtime=ToolRuntime(config=_make_config()))
        second = await restart_agent(tool_runtime=ToolRuntime(config=_make_config()))
        assert "已调度" in first
        assert "勿重复调用" in second
        assert manager.restart_scheduled is True


class TestFindRunningOtherSessions:
    """其他运行中会话检测测试。"""

    def test_excludes_current_session(self):
        """当前会话不计入运行中列表,其他 RUNNING 会话计入。"""
        from uniclaw.webui.ws import session_cache

        config = _make_config()
        session_cache.clear()
        try:
            session_cache["sess_test"] = config
            assert find_running_other_sessions(config) == []

            other = _make_config()
            other.current_agent.status = AgentStatus.RUNNING
            session_cache["sess_other"] = other
            assert find_running_other_sessions(config) == ["测试会话"]

            # 空闲会话不计入
            other.current_agent.status = AgentStatus.COMPLETED
            assert find_running_other_sessions(config) == []
        finally:
            session_cache.clear()

    def test_pending_without_future_not_busy(self):
        """PENDING 且无 future(前端刚点开/磁盘加载,从未运行)不算忙,不阻止重启。"""
        from uniclaw.webui.ws import session_cache

        config = _make_config()
        config.current_agent.future = None
        session_cache.clear()
        try:
            other = _make_config()
            other.current_agent.status = AgentStatus.PENDING
            other.current_agent.future = None
            session_cache["sess_other"] = other
            assert find_running_other_sessions(config) == []
        finally:
            session_cache.clear()

    def test_pending_with_future_busy(self):
        """PENDING 且有 future(已调度、主循环未开跑)算忙,阻止重启。"""
        from uniclaw.webui.ws import session_cache

        config = _make_config()
        config.current_agent.future = None
        session_cache.clear()
        try:
            other = _make_config()
            other.current_agent.status = AgentStatus.PENDING
            other.current_agent.future = MagicMock()  # 已调度
            session_cache["sess_other"] = other
            assert find_running_other_sessions(config) == ["测试会话"]
        finally:
            session_cache.clear()


class TestRestartFlag:
    """重启标志文件读写测试。"""

    def test_write_read_clear(self, tmp_path, monkeypatch):
        """写入后可读取(仅 session_id,无 message 字段),清除后读不到。"""
        import uniclaw.utils.restart_flag as rf

        path = tmp_path / "pending_restart.json"
        monkeypatch.setattr(rf, "flag_path", lambda: path)

        assert rf.read_pending_restart() is None
        rf.write_pending_restart("sess_abc")
        data = rf.read_pending_restart()
        assert data["session_id"] == "sess_abc"
        assert "message" not in data
        rf.clear_pending_restart()
        assert not path.exists()

    def test_stale_flag_ignored(self, tmp_path, monkeypatch):
        """超过有效期的标志被视为陈旧。"""
        import uniclaw.utils.restart_flag as rf

        path = tmp_path / "pending_restart.json"
        monkeypatch.setattr(rf, "flag_path", lambda: path)
        rf.write_pending_restart("sess_old")
        # 回拨时间戳使其过期
        data = json.loads(path.read_text(encoding="utf-8"))
        data["timestamp"] = time.time() - 9999
        path.write_text(json.dumps(data), encoding="utf-8")
        assert rf.read_pending_restart() is None


class TestWakeAgentPendingTask:
    """wake_agent 对未启动任务的唤醒分支测试。"""

    @pytest.mark.asyncio
    async def test_pending_without_future_starts_agent(self):
        """PENDING 且无 future(磁盘加载的会话)应走 start_agent 分支。"""
        from uniclaw.utils.wakeup import wake_agent

        config = _make_config()
        task = config.current_agent
        task.status = AgentStatus.PENDING
        task.future = None
        task.user_queue = MagicMock()

        with (
            patch("uniclaw.agent.MultiAgent") as MockMultiAgent,
            patch("uniclaw.utils.wakeup._needs_drain", return_value=False),
            patch("uniclaw.utils.wakeup._ensure_webui_bridge", new=AsyncMock()),
        ):
            instance = MockMultiAgent.get_instance.return_value
            ok = await wake_agent("恢复工作", config)

        assert ok is True
        instance.start_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_running_with_alive_future_enqueues(self):
        """RUNNING 且 future 存活时消息注入 user_queue。"""
        from uniclaw.utils.wakeup import wake_agent

        config = _make_config()
        task = config.current_agent
        task.status = AgentStatus.RUNNING
        future = MagicMock()
        future.done.return_value = False
        task.future = future
        queue = asyncio.Queue()
        task.user_queue = queue

        ok = await wake_agent("插入消息", config)

        assert ok is True
        assert queue.qsize() == 1


class TestBuildRelaunchCommand:
    """重启命令构造测试(覆盖各种启动入口形态)。"""

    KWARGS = {"host": "127.0.0.1", "port": 8907, "ssl": False, "domain": ""}

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        import importlib.util

        from uniclaw.tools.restart import relauncher

        self.relauncher = relauncher
        # 统一模拟"包未提供 __main__"的环境(uv 安装形态)
        monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path

    def _set_argv(self, argv0: str, executable: str):
        self.monkeypatch.setattr(self.relauncher.sys, "argv", [argv0])
        self.monkeypatch.setattr(self.relauncher.sys, "executable", executable)

    def _expected_args(self):
        return ["--mode", "webui", "--host", "127.0.0.1", "--port", "8907", "--no-ssl"]

    def test_script_entrypoint_uses_absolute_path(self):
        """`python src/uniclaw/main.py` 场景:argv0 是真实 .py 脚本时重跑该脚本。"""
        script = self.tmp_path / "main.py"
        script.write_text("# entry")
        python = self.tmp_path / "python.exe"
        python.write_bytes(b"")
        self._set_argv(str(script), str(python))

        cmd = self.relauncher.build_relaunch_command(self.KWARGS)

        assert cmd == [str(python), str(script), *self._expected_args()]

    def test_trampoline_virtual_path_extracts_exe(self):
        """uv trampoline 虚拟路径(<...>uniclaw.exe\\__main__.py)取 exe 前缀。"""
        exe = self.tmp_path / "uniclaw.exe"
        exe.write_bytes(b"")
        python = self.tmp_path / "python.exe"
        python.write_bytes(b"")
        virtual = str(exe) + "\\__main__.py"  # 磁盘上不存在
        self._set_argv(virtual, str(python))

        cmd = self.relauncher.build_relaunch_command(self.KWARGS)

        assert cmd == [str(exe), *self._expected_args()]

    def test_sibling_entrypoint_fallback(self):
        """argv0 无效时兜底到解释器同级目录的入口脚本。"""
        scripts_dir = self.tmp_path / "Scripts"
        scripts_dir.mkdir()
        sibling = scripts_dir / "uniclaw.exe"
        sibling.write_bytes(b"")
        python = scripts_dir / "python.exe"
        python.write_bytes(b"")
        self._set_argv("", str(python))

        cmd = self.relauncher.build_relaunch_command(self.KWARGS)

        assert cmd == [str(sibling), *self._expected_args()]

    def test_no_entry_found_raises(self):
        """所有策略都找不到入口时报错(调用方会清标志并记录日志)。"""
        python = self.tmp_path / "python.exe"
        python.write_bytes(b"")
        self._set_argv("", str(python))

        with pytest.raises(RuntimeError):
            self.relauncher.build_relaunch_command(self.KWARGS)

    def test_ssl_flag_omitted_when_enabled(self):
        """启用 SSL 时命令不带 --no-ssl。"""
        script = self.tmp_path / "main.py"
        script.write_text("# entry")
        python = self.tmp_path / "python.exe"
        python.write_bytes(b"")
        self._set_argv(str(script), str(python))

        kwargs = dict(self.KWARGS, ssl=True)
        cmd = self.relauncher.build_relaunch_command(kwargs)

        assert "--no-ssl" not in cmd
