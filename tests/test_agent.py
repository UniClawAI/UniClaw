"""
agent.py 模块的单元测试

测试事件类、权限检查、消息队列等核心功能
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from uniclaw.agent import (
    ReturnEvent,
    UserEvent,
    TextChunkEvent,
    ThinkingChunkEvent,
    ThinkingStartEvent,
    AssistantEvent,
    ToolPreparingEvent,
    ToolStartEvent,
    ToolEvent,
    EndEvent,
    InterruptedEvent,
    PermissionRequestEvent,
    SlashCommandEvent,
    ShellCommandEvent,
    AgentStatus,
    _permission_desc,
    _edit_permission_diff,
)


class TestReturnEvent:
    """ReturnEvent 类的测试"""

    def test_initialization_default(self):
        """测试默认初始化"""
        event = ReturnEvent()
        assert event.content is None
        assert hasattr(event, "return_event")
        assert isinstance(event.return_event, asyncio.Event)

    def test_initialization_with_content(self):
        """测试带内容初始化"""
        event = ReturnEvent(default_content="test")
        assert event.content == "test"

    def test_return_event_is_event(self):
        """测试 return_event 是 asyncio.Event"""
        event = ReturnEvent()
        assert not event.return_event.is_set()


class TestEventClasses:
    """事件类的测试"""

    def test_user_event(self):
        """测试 UserEvent"""
        event = UserEvent(content="Hello")
        assert event.content == "Hello"

    def test_text_chunk_event(self):
        """测试 TextChunkEvent"""
        event = TextChunkEvent(content="chunk")
        assert event.content == "chunk"

    def test_thinking_chunk_event(self):
        """测试 ThinkingChunkEvent"""
        event = ThinkingChunkEvent(content="thinking")
        assert event.content == "thinking"

    def test_thinking_start_event(self):
        """测试 ThinkingStartEvent"""
        event = ThinkingStartEvent()
        assert event is not None

    def test_assistant_event(self):
        """测试 AssistantEvent"""
        event = AssistantEvent(
            content="response",
            tool_calls=[{"name": "test"}],
            in_tokens=10,
            out_tokens=20,
            model_name="gpt-4",
            cached_tokens=8,
            cache_write_tokens=2,
            cache_discount=0.4,
        )
        assert event.content == "response"
        assert event.tool_calls == [{"name": "test"}]
        assert event.in_tokens == 10
        assert event.out_tokens == 20
        assert event.model_name == "gpt-4"
        assert event.cached_tokens == 8
        assert event.cache_write_tokens == 2
        assert event.cache_discount == 0.4

    def test_assistant_event_defaults(self):
        """测试 AssistantEvent 默认值"""
        event = AssistantEvent(content="test", tool_calls=[])
        assert event.in_tokens == 0
        assert event.out_tokens == 0
        assert event.model_name == ""
        assert event.cached_tokens == 0
        assert event.cache_write_tokens == 0
        assert event.cache_discount == 0.0

    def test_tool_preparing_event(self):
        """测试 ToolPreparingEvent"""
        event = ToolPreparingEvent(name="Read", args={"file_path": "test.py"})
        assert event.name == "Read"
        assert event.args == {"file_path": "test.py"}

    def test_tool_preparing_event_default_args(self):
        """测试 ToolPreparingEvent 默认参数"""
        event = ToolPreparingEvent(name="Read")
        assert event.args == {}

    def test_tool_start_event(self):
        """测试 ToolStartEvent"""
        event = ToolStartEvent(name="Write", args={"file_path": "test.py"})
        assert event.name == "Write"
        assert event.args == {"file_path": "test.py"}

    def test_tool_event(self):
        """测试 ToolEvent"""
        event = ToolEvent(
            name="Read",
            content="file content",
            tool_call_id="call_123",
            args={"file_path": "test.py"},
        )
        assert event.name == "Read"
        assert event.content == "file content"
        assert event.tool_call_id == "call_123"
        assert event.args == {"file_path": "test.py"}

    def test_tool_event_default_args(self):
        """测试 ToolEvent 默认参数"""
        event = ToolEvent(name="Read", content="content", tool_call_id="call_1")
        assert event.args is None

    def test_end_event(self):
        """测试 EndEvent"""
        event = EndEvent(depth=3)
        assert event.depth == 3

    def test_interrupted_event(self):
        """测试 InterruptedEvent"""
        event = InterruptedEvent()
        assert event.message == "已中断,等待您的补充指令..."

    def test_interrupted_event_custom_message(self):
        """测试 InterruptedEvent 自定义消息"""
        event = InterruptedEvent(message="自定义中断消息")
        assert event.message == "自定义中断消息"


class TestPermissionRequestEvent:
    """PermissionRequestEvent 的测试"""

    def test_initialization(self):
        """测试初始化"""
        event = PermissionRequestEvent(
            description="运行命令",
            tool_call={"name": "Bash", "args": {"command": "ls"}},
            explanation="需要执行命令",
        )
        assert event.description == "运行命令"
        assert event.tool_call == {"name": "Bash", "args": {"command": "ls"}}
        assert event.explanation == "需要执行命令"
        assert event.content == "无可用的 UI 响应,自动拒绝权限请求"
        assert hasattr(event, "return_event")

    def test_default_tool_call(self):
        """测试默认 tool_call"""
        event = PermissionRequestEvent(description="测试")
        assert event.tool_call == {}

    def test_default_explanation(self):
        """测试默认 explanation"""
        event = PermissionRequestEvent(description="测试")
        assert event.explanation == ""


class TestSlashCommandEvent:
    """SlashCommandEvent 的测试"""

    def test_initialization(self):
        """测试初始化"""
        event = SlashCommandEvent(command="/help")
        assert event.command == "/help"
        assert event.content is None
        assert hasattr(event, "return_event")


class TestShellCommandEvent:
    """ShellCommandEvent 的测试"""

    def test_initialization(self):
        """测试初始化"""
        event = ShellCommandEvent(command="ls -la")
        assert event.command == "ls -la"
        assert event.content is None
        assert hasattr(event, "return_event")


class TestAgentStatus:
    """AgentStatus 枚举的测试"""

    def test_status_values(self):
        """测试状态值"""
        assert AgentStatus.PENDING == "pending"
        assert AgentStatus.RUNNING == "running"
        assert AgentStatus.COMPLETED == "completed"
        assert AgentStatus.FAILED == "failed"
        assert AgentStatus.CANCELLED == "cancelled"
        assert AgentStatus.LOST == "lost"

    def test_status_is_string(self):
        """测试状态是字符串"""
        assert isinstance(AgentStatus.PENDING, str)
        assert isinstance(AgentStatus.COMPLETED, str)


class TestPermissionDesc:
    """_permission_desc 函数的测试"""

    def test_bash_command(self):
        """测试 Bash 命令描述"""
        tc = {"name": "Bash", "args": {"command": "ls -la"}}
        desc = _permission_desc(tc)
        assert "🖥️" in desc
        assert "ls -la" in desc

    def test_write_file(self):
        """测试 Write 工具描述"""
        tc = {"name": "Write", "args": {"file_path": "/tmp/test.py"}}
        desc = _permission_desc(tc)
        assert "📝" in desc
        assert "/tmp/test.py" in desc

    def test_edit_file(self):
        """测试 Edit 工具描述"""
        tc = {
            "name": "Edit",
            "args": {
                "file_path": "/tmp/test.py",
                "old_string": "old",
                "new_string": "new",
                "replace_all": False,
            },
        }
        desc = _permission_desc(tc)
        assert "✏️" in desc
        assert "/tmp/test.py" in desc

    def test_other_tool(self):
        """测试其他工具描述"""
        tc = {"name": "Read", "args": {"file_path": "/tmp/test.py"}}
        desc = _permission_desc(tc)
        assert "🔧" in desc
        assert "Read" in desc


class TestEditPermissionDiff:
    """_edit_permission_diff 函数的测试"""

    def test_simple_diff(self):
        """测试简单差异"""
        diff = _edit_permission_diff("test.py", "old content", "new content")
        assert "拟修改 diff:" in diff
        assert "-old content" in diff
        assert "+new content" in diff

    def test_no_diff(self):
        """测试无差异"""
        diff = _edit_permission_diff("test.py", "same content", "same content")
        assert "拟修改内容无差异" in diff

    def test_multiline_diff(self):
        """测试多行差异"""
        old = "line1\nline2\nline3"
        new = "line1\nmodified\nline3"
        diff = _edit_permission_diff("test.py", old, new)
        assert "-line2" in diff
        assert "+modified" in diff

    def test_large_diff_truncation(self):
        """测试大差异截断"""
        old = "\n".join([f"old line {i}" for i in range(200)])
        new = "\n".join([f"new line {i}" for i in range(200)])
        diff = _edit_permission_diff("test.py", old, new)
        assert "more diff lines hidden" in diff


class TestExecuteSingleToolLookup:
    """_execute_single_tool 工具查找失败分支: 禁用工具要报根因而非"工具不存在"。"""

    def _make_config(self, unavailable: dict, is_sub: bool = False) -> MagicMock:
        config = MagicMock()
        config.is_sub = is_sub
        task = MagicMock()
        task.allowed_tools_set = set()
        task.extended_mgr = MagicMock()
        task.session = MagicMock()
        task.session.check_dedup = MagicMock(return_value=None)
        task.cancel_event = MagicMock()
        task.cancel_event.is_set = MagicMock(return_value=False)
        config.current_agent = task
        config.unavailable_tool_reasons = unavailable
        return config

    async def _call(self, config, name2tool, name="text_to_speech"):
        from uniclaw.agent.multi_agent import MultiAgent

        agent = MultiAgent.get_instance()
        agent.send_event_to_user = AsyncMock(return_value=None)
        tool_call = {"id": "call_1", "name": name, "args": {}}
        with patch("uniclaw.agent.multi_agent.run_hooks", new=AsyncMock()):
            return await agent._execute_single_tool(tool_call, name2tool, config)

    @pytest.mark.asyncio
    async def test_disabled_tool_reports_reason(self):
        """未启用工具直接调用 → 报"未启用: 原因", 不是"工具不存在"。"""
        config = self._make_config(
            {"text_to_speech": "未配置 tts_model 或 audio,TTS 工具不可用"}
        )
        _, content = await self._call(config, name2tool={})
        assert "未启用" in content
        assert "未配置 tts_model" in content
        assert "工具不存在" not in content

    @pytest.mark.asyncio
    async def test_unknown_tool_still_says_not_found(self):
        """真不存在的工具仍报"工具不存在"。"""
        config = self._make_config({})
        _, content = await self._call(config, name2tool={}, name="no_such_tool")
        assert "工具不存在" in content

    @pytest.mark.asyncio
    async def test_catalog_tool_not_enabled_says_disabled(self):
        """在全量清单(get_all_tools)里但当前未启用 → 报"未启用",不是"工具不存在"。

        典型场景: monitor_screen 在非 Windows/缺 pywinpty 环境被 get_tools 裁掉,
        直接调用若报"工具不存在"会误导调用方改工具名。
        """
        config = self._make_config({})
        fake_registry = MagicMock()
        fake_registry.get_all_entries.return_value = {"monitor_screen": object()}
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance",
            return_value=fake_registry,
        ):
            _, content = await self._call(config, name2tool={}, name="monitor_screen")
        assert "未启用" in content
        assert "monitor_screen" in content
        assert "工具不存在" not in content

    @pytest.mark.asyncio
    async def test_disabled_with_reason_still_beats_catalog(self):
        """有记录的根因时优先报根因,不落到目录兜底的泛化措辞。"""
        config = self._make_config({"monitor_screen": "缺少真实屏幕快照支持(需要 Windows + pywinpty)"})
        fake_registry = MagicMock()
        fake_registry.get_all_entries.return_value = {"monitor_screen": object()}
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance",
            return_value=fake_registry,
        ):
            _, content = await self._call(config, name2tool={}, name="monitor_screen")
        assert "未启用" in content
        assert "缺少真实屏幕快照支持" in content

    @pytest.mark.asyncio
    async def test_subagent_calls_parent_only_tool(self):
        """子代理直接调用主 agent 专属工具(todolist_create)→ 报"主 agent 专属",不报"未启用"。"""
        from uniclaw.tools.registry import get_parent_only_tool_names

        assert "todolist_create" in get_parent_only_tool_names()
        config = self._make_config({}, is_sub=True)
        _, content = await self._call(config, name2tool={}, name="todolist_create")
        assert "主 agent 专属工具" in content
        assert "子代理不可用" in content
        assert "未启用" not in content
        assert "工具不存在" not in content

    @pytest.mark.asyncio
    async def test_subagent_calls_unknown_still_not_found(self):
        """子代理调用不属于任何目录的真未知工具 → 仍报"工具不存在"。"""
        config = self._make_config({}, is_sub=True)
        _, content = await self._call(config, name2tool={}, name="totally_unknown_xyz")
        assert "工具不存在" in content

    @pytest.mark.asyncio
    async def test_main_agent_calls_parent_only_tool_unchanged(self):
        """主 agent 直接调用 todolist_create(未加载)→ 还走扩展未加载路径,不误报"主 agent 专属"。"""
        config = self._make_config({})
        _, content = await self._call(config, name2tool={}, name="todolist_create")
        # 主 agent 无 is_sub,不进 parent-only 分支
        assert "主 agent 专属工具" not in content

    @pytest.mark.asyncio
    async def test_whitelisted_subagent_direct_call_outside_whitelist(self):
        """带工具白名单子代理直接调用名单外工具(kg-extract 调 kg_search)→ 报"不在白名单内",不报"未启用"。"""
        config = self._make_config({}, is_sub=True)
        config.current_agent.tool_whitelist = {"Read", "Glob", "Grep"}
        _, content = await self._call(config, name2tool={}, name="kg_search")
        assert "工具不存在" not in content
        assert "未启用" not in content
        assert "白名单" in content
        assert "不在本子智能体声明的工具白名单内" in content
        assert "Read" in content and "Glob" in content and "Grep" in content

    @pytest.mark.asyncio
    async def test_whitelisted_subagent_whitelisted_ext_loaded_hint(self):
        """白名单内但未加载的扩展工具仍优先提示"先搜索加载",不误报白名单限制。"""
        config = self._make_config({}, is_sub=True)
        config.current_agent.tool_whitelist = {"kg_search", "Read"}
        task = config.current_agent
        task.allowed_tools_set = {"kg_search", "Read"}  # 白名单内
        _, content = await self._call(config, name2tool={}, name="kg_search")
        assert "扩展工具" in content
        assert "未加载" in content
        assert "白名单" not in content

    @pytest.mark.asyncio
    async def test_whitelist_reason_wins_over_whitelist_hint(self):
        """白名单子代理调用既有配置原因又名单外的工具 → 配置原因优先,不叠加白名单。"""
        config = self._make_config(
            {"kg_search": "未配置 embedding_model"}, is_sub=True
        )
        config.current_agent.tool_whitelist = {"Read", "Glob", "Grep"}
        _, content = await self._call(config, name2tool={}, name="kg_search")
        assert "未启用" in content
        assert "未配置 embedding_model" in content
        assert "白名单" not in content

    @pytest.mark.asyncio
    async def test_whitelisted_subagent_empty_whitelist(self):
        """空白名单子代理(set())调用任何工具 → 名单外提示,空清单不炸。"""
        config = self._make_config({}, is_sub=True)
        config.current_agent.tool_whitelist = set()
        _, content = await self._call(config, name2tool={}, name="kg_search")
        assert "白名单" in content
        assert "工具不存在" not in content

    @pytest.mark.asyncio
    async def test_main_agent_with_stray_whitelist_ignored(self):
        """主 agent 即使 task.tool_whitelist 有残留值也会被 config.is_sub 挡住,不受影响。"""
        # 防御: 主 agent 的 whitelist 字段理论上为 None,但即使残留也能正确忽略
        config = self._make_config({}, is_sub=False)
        config.current_agent.tool_whitelist = {"Read", "Glob", "Grep"}
        _, content = await self._call(config, name2tool={}, name="kg_search")
        assert "白名单" not in content

    @pytest.mark.asyncio
    async def test_whitelisted_subagent_in_whitelist_but_missing_from_catalog(self):
        """白名单内但不在全量目录且未加载(理论残留)→ 落"工具不存在",不误报白名单。"""
        config = self._make_config({}, is_sub=True)
        config.current_agent.tool_whitelist = {"Read", "ghost_tool"}
        task = config.current_agent
        task.allowed_tools_set = set()
        name2tool = {}
        # ghost_tool 在白名单但 name2tool 没有(核心/扩展都没加载)且不在全量目录
        _, content = await self._call(config, name2tool, name="ghost_tool")
        assert "工具不存在" in content
        assert "白名单" not in content

    @pytest.mark.asyncio
    async def test_unrestricted_subagent_catalog_fallback(self):
        """无白名单子代理调用目录内未加载工具 → "未启用"兜底(原有行为)。"""
        config = self._make_config({}, is_sub=True)
        fake_registry = MagicMock()
        fake_registry.get_all_entries.return_value = {"monitor_screen": object()}
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance",
            return_value=fake_registry,
        ):
            _, content = await self._call(config, name2tool={}, name="monitor_screen")
        assert "未启用" in content
        assert "白名单" not in content
        assert "工具不存在" not in content


class TestWhitelistHelper:
    """_whitelist_of 辅助函数: 各类型输入的统一处理。"""

    def _task(self, whitelist):
        task = MagicMock()
        task.tool_whitelist = whitelist
        return task

    def _whitelist_of(self, task):
        from uniclaw.agent.multi_agent import _whitelist_of

        return _whitelist_of(task)

    def test_none_returns_none(self):
        """None(无白名单/全集)返回 None。"""
        assert self._whitelist_of(self._task(None)) is None

    def test_set_returns_same_content(self):
        """set 原样返回副本。"""
        wl = {"Read", "Glob"}
        result = self._whitelist_of(self._task(wl))
        assert result == {"Read", "Glob"}
        # 返回的是副本,修改不影响原对象
        result.add("Write")
        assert wl == {"Read", "Glob"}

    def test_frozenset_converted(self):
        """frozenset → set。"""
        result = self._whitelist_of(self._task(frozenset({"a", "b"})))
        assert result == {"a", "b"}
        assert isinstance(result, set)

    def test_list_converted(self):
        """list → set(去重)。"""
        result = self._whitelist_of(self._task(["a", "b", "a"]))
        assert result == {"a", "b"}

    def test_tuple_converted(self):
        """tuple → set。"""
        result = self._whitelist_of(self._task(("a", "b")))
        assert result == {"a", "b"}

    def test_empty_set_returns_empty_set(self):
        """空 set() 返回空 set — 与 None 区分(空白名单仍是白名单)。"""
        result = self._whitelist_of(self._task(set()))
        assert result == set()
        assert result is not None

    def test_non_collection_returns_none(self):
        """非集合类型(如字符串)不被误认为白名单。"""
        assert self._whitelist_of(self._task("Read")) is None


class TestOutputTruncationDetect:
    """输出截断检测: finish_reason 为 length/max_tokens 时判定被截断。"""

    def test_truncated_reasons(self):
        from uniclaw.agent.multi_agent import _is_output_truncated
        from uniclaw.tools.session.session import StreamChunk

        assert _is_output_truncated(StreamChunk(finish_reason="length")) is True
        assert _is_output_truncated(StreamChunk(finish_reason="max_tokens")) is True

    def test_normal_finish_not_truncated(self):
        from uniclaw.agent.multi_agent import _is_output_truncated
        from uniclaw.tools.session.session import StreamChunk

        assert _is_output_truncated(StreamChunk(finish_reason="stop")) is False
        assert _is_output_truncated(StreamChunk(finish_reason="tool_calls")) is False
        assert _is_output_truncated(StreamChunk()) is False

    def test_resp_without_finish_reason(self):
        """无 finish_reason 字段的对象(如旧 AIMessage)不误判。"""
        from uniclaw.agent.multi_agent import _is_output_truncated

        assert _is_output_truncated(object()) is False
