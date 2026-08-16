"""工具注册表测试 — 覆盖核心/扩展工具、BM25 搜索和能量管理。

注意:BM25 在 corpus 过小(<3 文档)时 idf 为 0 或负会导致搜索失败,
因此搜索类测试至少注册 3 个工具,且目标关键词为唯一 token。
"""

from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from uniclaw.tools.base import Tool
from uniclaw.tools.registry import (
    CORE_TOOL_NAMES,
    CORE_TOOLS,
    EXTENDED_TOOL_ENERGY_MAX,
    MAX_LOADED_EXTENDED,
    ExtendedToolManager,
    ToolRegistry,
    _build_extended_keywords,
    _build_tool_categories,
    get_registry_system_prompt,
    init_registry,
    search_tools,
)


def _make_tool(name: str, description: str = "desc") -> Tool:
    tool = MagicMock()
    tool.name = name
    tool.description = description
    return tool


def _make_search_registry() -> ToolRegistry:
    """构造 5 个工具的注册表。

    共享词 "文件" 只出现在 2 个工具中(freq < corpus/2),
    保证 BM25 idf 为正,避免小 corpus 下 idf 归零或为负。
    """
    r = ToolRegistry()
    r.register(_make_tool("tool_alpha", "handles alpha"), ["alpha"], "测试")
    r.register(_make_tool("tool_beta", "handles beta"), ["beta", "文件"], "测试")
    r.register(_make_tool("tool_gamma", "handles gamma"), ["gamma", "文件"], "测试")
    r.register(_make_tool("tool_delta", "handles delta"), ["delta"], "测试")
    r.register(_make_tool("tool_epsilon", "handles epsilon"), ["epsilon"], "测试")
    return r


class TestCoreTools:
    """核心工具列表测试。"""

    def test_core_tools_nonempty(self):
        """核心工具非空。"""
        assert len(CORE_TOOLS) >= 10

    def test_all_are_tools(self):
        """核心工具都是 Tool 对象。"""
        for t in CORE_TOOLS:
            assert isinstance(t, Tool)

    def test_core_names_match(self):
        """CORE_TOOL_NAMES 与工具名一致(search_tools 在模块加载时加入)。"""
        assert CORE_TOOL_NAMES == {t.name for t in CORE_TOOLS} | {"search_tools"}

    def test_search_tools_in_core(self):
        """search_tools 元工具在核心工具中。"""
        assert "search_tools" in CORE_TOOL_NAMES

    def test_known_core_tools(self):
        """包含关键核心工具。"""
        for name in ["Read", "Write", "Bash", "Grep", "webFetch", "webSearch"]:
            assert name in CORE_TOOL_NAMES


class TestToolRegistry:
    """ToolRegistry 基础功能测试。"""

    def test_get_instance_singleton(self):
        """单例模式。"""
        with patch.object(ToolRegistry, "_instance", None):
            r1 = ToolRegistry.get_instance()
            r2 = ToolRegistry.get_instance()
        assert r1 is r2

    def test_register(self):
        """注册工具。"""
        r = ToolRegistry()
        t = _make_tool("test_tool", "desc here")
        r.register(t, ["kw"], "测试", is_core=False)
        entries = r.get_all_entries()
        assert "test_tool" in entries
        assert entries["test_tool"].tool is t
        assert entries["test_tool"].keywords == ["kw"]
        assert entries["test_tool"].category == "测试"

    def test_register_core(self):
        """注册核心工具。"""
        r = ToolRegistry()
        r.register(_make_tool("core1"), [], "c", is_core=True)
        assert "core1" in r.get_core_names()

    def test_register_invalidates_bm25(self):
        """注册后 BM25 索引标记失效。"""
        r = ToolRegistry()
        r.register(_make_tool("t1", "alpha"), ["alpha"], "c")
        r._build_bm25()
        assert r._bm25 is not None
        r.register(_make_tool("t2", "beta"), ["beta"], "c")
        assert r._bm25 is None

    def test_unregister(self):
        """注销工具。"""
        r = ToolRegistry()
        r.register(_make_tool("t1"), [], "c")
        r.register(_make_tool("t2"), [], "c")
        r.unregister("t1", "t2")
        assert r.get_all_entries() == {}

    def test_unregister_missing(self):
        """注销不存在的工具不报错。"""
        r = ToolRegistry()
        r.unregister("nope")

    def test_clear_bm25(self):
        """清除 BM25 索引。"""
        r = ToolRegistry()
        r.register(_make_tool("t1", "alpha keyword"), ["alpha"], "c")
        r._build_bm25()
        assert r._bm25 is not None
        r.clear_bm25()
        assert r._bm25 is None

    def test_get_core_names(self):
        """核心工具名集合。"""
        r = ToolRegistry()
        r.register(_make_tool("c1"), [], "c", is_core=True)
        assert r.get_core_names() == {"c1"}


class TestSearch:
    """BM25 搜索测试。"""

    def test_search_unique_token(self):
        """按唯一关键词命中。"""
        r = _make_search_registry()
        results = r.search("alpha")
        names = [e.tool.name for e in results]
        assert "tool_alpha" in names
        assert "tool_beta" not in names
        assert "tool_gamma" not in names

    def test_search_multiple_matches(self):
        """共享关键词返回多个。"""
        r = _make_search_registry()
        results = r.search("文件")
        assert len(results) >= 2

    def test_search_no_match(self):
        """无匹配返回空。"""
        r = _make_search_registry()
        assert r.search("zzzznope") == []

    def test_search_empty_registry(self):
        """空注册表返回空。"""
        r = ToolRegistry()
        assert r.search("anything") == []

    def test_search_top_k(self):
        """top_k 限制。"""
        r = _make_search_registry()
        results = r.search("文件", top_k=2)
        assert len(results) == 2

    def test_search_core_excluded(self):
        """核心工具不参与搜索。"""
        r = _make_search_registry()
        r.register(_make_tool("core_alpha", "core alpha"), ["alpha"], "c", is_core=True)
        results = r.search("alpha")
        names = [e.tool.name for e in results]
        assert "core_alpha" not in names


class TestResolveTools:
    """resolve_tools 测试。"""

    def test_resolve_names(self):
        """按名字解析。"""
        r = ToolRegistry()
        t1 = _make_tool("t1")
        r.register(t1, [], "c")
        resolved = r.resolve_tools(["t1"])
        assert resolved == [t1]

    def test_resolve_mixed(self):
        """混合名字和对象。"""
        r = ToolRegistry()
        t1 = _make_tool("t1")
        r.register(t1, [], "c")
        t2 = _make_tool("t2")
        resolved = r.resolve_tools(["t1", t2])
        assert resolved == [t1, t2]

    @patch("uniclaw.utils.logger.get_logger")
    def test_resolve_unknown(self, mock_logger):
        """未知名字被忽略并记录警告。"""
        r = ToolRegistry()
        resolved = r.resolve_tools(["ghost"])
        assert resolved == []
        mock_logger.assert_called_once_with("registry")


class TestExtendedToolManager:
    """扩展工具能量管理测试。"""

    def _manager(self):
        return ExtendedToolManager()

    def test_initial_state(self):
        """初始状态。"""
        m = self._manager()
        assert m.loaded == []
        assert m.energy == {}
        assert m.loaded_names == set()
        assert m.slot_count == 0

    def test_touch_string(self):
        """按名字 touch。"""
        m = self._manager()
        m.touch("tool1")
        assert m.loaded == ["tool1"]
        assert m.energy["tool1"] == EXTENDED_TOOL_ENERGY_MAX

    def test_touch_tool_object(self):
        """按对象 touch 加入待加载。"""
        m = self._manager()
        t = _make_tool("tool1")
        m.touch(t)
        assert t in m.pending_tools
        assert "tool1" in m.loaded

    @patch("uniclaw.tools.registry.CORE_TOOL_NAMES", {"Read"})
    def test_touch_core_ignored(self):
        """核心工具不参与能量管理。"""
        m = self._manager()
        t = _make_tool("Read")
        m.touch(t)
        assert m.loaded == []
        assert m.pending_tools == []

    def test_touch_moves_to_front(self):
        """重复 touch 移到 MRU 端。"""
        m = self._manager()
        m.touch("a")
        m.touch("b")
        m.touch("a")
        assert m.loaded == ["a", "b"]

    def test_evict(self):
        """淘汰工具。"""
        m = self._manager()
        m.touch("a")
        m.touch("b")
        m.evict(["a"])
        assert m.loaded == ["b"]
        assert "a" not in m.energy
        assert "a" in m.pending_evicted

    def test_drain_energy_decays(self):
        """能量逐轮递减。"""
        m = self._manager()
        m.touch("a")
        m.drain_energy()
        assert m.energy["a"] == EXTENDED_TOOL_ENERGY_MAX - 1

    def test_drain_energy_exhausts(self):
        """能量耗尽自动淘汰。"""
        m = self._manager()
        m.touch("a")
        m.energy["a"] = 1
        exhausted = m.drain_energy()
        assert "a" in exhausted
        assert "a" not in m.loaded

    def test_drain_energy_missing_entry(self):
        """无能量记录的工具按满值递减。"""
        m = self._manager()
        m.loaded = ["a"]
        m.drain_energy()
        assert m.energy["a"] == EXTENDED_TOOL_ENERGY_MAX - 1

    def test_apply_loads_pending(self):
        """apply 加载待发现工具。"""
        m = self._manager()
        t = _make_tool("tool1")
        m.pending_tools = [t]
        tools = []
        name2tool = {}
        m.apply(tools, name2tool)
        assert tools == [t]
        assert name2tool["tool1"] is t

    def test_apply_skips_existing(self):
        """已存在的工具不重复添加。"""
        m = self._manager()
        t = _make_tool("tool1")
        m.pending_tools = [t]
        name2tool = {"tool1": t}
        tools = [t]
        m.apply(tools, name2tool)
        assert tools == [t]

    def test_apply_evicts(self):
        """apply 清理被淘汰工具。"""
        m = self._manager()
        t = _make_tool("t1")
        tools = [t]
        name2tool = {"t1": t}
        m.pending_evicted = {"t1"}
        m.apply(tools, name2tool)
        assert tools == []
        assert name2tool == {}

    def test_restore_session(self):
        """会话恢复。"""
        m = self._manager()
        t = _make_tool("tool1")
        entries = {"tool1": MagicMock(tool=t)}
        name2tool = {}
        tools = []
        m.restore_session(["tool1"], entries, name2tool, tools, allowed={"tool1"})
        assert tools == [t]
        assert name2tool["tool1"] is t

    def test_restore_session_not_allowed(self):
        """未授权工具不恢复。"""
        m = self._manager()
        t = _make_tool("tool1")
        entries = {"tool1": MagicMock(tool=t)}
        tools = []
        m.restore_session(["tool1"], entries, {}, tools, allowed=set())
        assert tools == []


class TestInitRegistry:
    """init_registry 测试。"""

    @patch("uniclaw.tools.registry._build_tool_categories")
    @patch("uniclaw.tools.registry._build_extended_keywords")
    def test_init(self, mock_kw, mock_cat):
        """初始化注册表。"""
        mock_kw.return_value = {"t1": ["kw1"]}
        mock_cat.return_value = {"t1": "类别"}
        registry = ToolRegistry()
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance", return_value=registry
        ):
            tools = [_make_tool("t1")]
            init_registry(tools)
        entry = registry.get_all_entries()["t1"]
        assert entry.keywords == ["kw1"]
        assert entry.category == "类别"

    @patch("uniclaw.tools.registry._build_tool_categories")
    @patch("uniclaw.tools.registry._build_extended_keywords")
    def test_init_core(self, mock_kw, mock_cat):
        """核心工具标记。"""
        mock_kw.return_value = {}
        mock_cat.return_value = {}
        registry = ToolRegistry()
        with (
            patch(
                "uniclaw.tools.registry.ToolRegistry.get_instance",
                return_value=registry,
            ),
            patch("uniclaw.tools.registry.CORE_TOOL_NAMES", {"Read"}),
        ):
            init_registry([_make_tool("Read")])
        assert "Read" in registry.get_core_names()

    @patch("uniclaw.tools.registry._build_tool_categories")
    @patch("uniclaw.tools.registry._build_extended_keywords")
    def test_init_default_category(self, mock_kw, mock_cat):
        """无类别工具默认 mcp。"""
        mock_kw.return_value = {}
        mock_cat.return_value = {}
        registry = ToolRegistry()
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance", return_value=registry
        ):
            init_registry([_make_tool("t1")])
        assert registry.get_all_entries()["t1"].category == "mcp"


class TestKeywordsCategories:
    """关键词与类别映射一致性测试。"""

    def test_keywords_all_tools(self):
        """每个有类别的工具都有关键词。"""
        keywords = _build_extended_keywords()
        categories = _build_tool_categories()
        for name in categories:
            assert name in keywords, f"工具 {name} 缺少关键词"

    def test_known_categories(self):
        """已知类别。"""
        categories = _build_tool_categories()
        assert categories["kg_search"] == "知识图谱"
        assert categories["DockerCreate"] == "沙箱"
        assert categories["browser_start"] == "浏览器"
        assert categories["ipython_execute"] == "IPython"


def _make_search_config(
    registry: ToolRegistry, allowed: set, loaded: list[str] | None = None
):
    """构造 search_tools 需要的 config。"""
    from uniclaw.tools.registry import ExtendedToolManager

    agent = MagicMock()
    agent.allowed_tools_set = allowed
    agent.extended_mgr = ExtendedToolManager()
    for name in loaded or []:
        agent.extended_mgr.touch(name)
    config = MagicMock()
    config.current_agent = agent
    patch_get = patch(
        "uniclaw.tools.registry.ToolRegistry.get_instance", return_value=registry
    )
    return config, patch_get


class TestSearchToolsTool:
    """search_tools 元工具测试。"""

    @pytest.mark.asyncio
    async def test_no_results(self):
        """无结果返回提示。"""
        config, p = _make_search_config(ToolRegistry(), {"anything"})
        with p:
            result = await search_tools("nonexistentxyz", config=config)
        assert "未找到匹配" in result

    @pytest.mark.asyncio
    async def test_loads_new_tool(self):
        """搜索到新工具并加载。"""
        registry = _make_search_registry()
        registry.register(
            _make_tool("kg_search", "搜索知识图谱"), ["kgquery"], "知识图谱"
        )
        config, p = _make_search_config(
            registry, {"kg_search", "tool_alpha", "tool_beta", "tool_gamma"}
        )
        with p:
            result = await search_tools("kgquery", config=config)
        assert "kg_search" in result
        assert "kg_search" in config.current_agent.extended_mgr.loaded

    @pytest.mark.asyncio
    async def test_all_blocked(self):
        """工具存在但不可用。"""
        registry = _make_search_registry()
        registry.register(_make_tool("kg_search", "desc"), ["kgquery"], "知识图谱")
        config, p = _make_search_config(
            registry, {"tool_alpha", "tool_beta", "tool_gamma"}
        )
        with p:
            result = await search_tools("kgquery", config=config)
        assert "当前不可用" in result
        assert "kg_search" in result

    @pytest.mark.asyncio
    async def test_already_loaded(self):
        """已加载的工具直接返回。"""
        registry = _make_search_registry()
        registry.register(_make_tool("kg_search", "desc"), ["kgquery"], "知识图谱")
        config, p = _make_search_config(
            registry,
            {"kg_search", "tool_alpha", "tool_beta", "tool_gamma"},
            loaded=["kg_search"],
        )
        with p:
            result = await search_tools("kgquery", config=config)
        assert "均已加载" in result

    @pytest.mark.asyncio
    async def test_capacity_eviction(self):
        """超上限时淘汰旧工具。"""
        registry = _make_search_registry()
        registry.register(_make_tool("new_tool", "desc new"), ["newword"], "类别")
        config, p = _make_search_config(
            registry, {"new_tool", "tool_alpha", "tool_beta", "tool_gamma"}
        )
        # 占满所有槽位
        for i in range(MAX_LOADED_EXTENDED):
            config.current_agent.extended_mgr.touch(f"old_{i}")
        with p:
            result = await search_tools("newword", config=config)
        assert "已淘汰" in result


class TestRegistrySystemPrompt:
    """get_registry_system_prompt 测试。"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools._ensure_registry")
    async def test_prompt_generated(self, mock_ensure):
        """生成系统提示词。"""
        registry = _make_search_registry()
        registry.register(
            _make_tool("kg_search", "搜索知识图谱。更多说明"), ["kgquery"], "知识图谱"
        )
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance", return_value=registry
        ):
            result = await get_registry_system_prompt()
        assert "# 扩展工具" in result
        assert "kg_search" in result
        assert "知识图谱" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools._ensure_registry")
    async def test_subagent_filter(self, mock_ensure):
        """子代理只显示允许的工具。"""
        registry = _make_search_registry()
        registry.register(_make_tool("allowed_tool", "允许的"), ["x"], "类别")
        registry.register(_make_tool("blocked_tool", "禁止的"), ["x"], "类别")
        config = MagicMock()
        config.is_sub = True
        agent = MagicMock()
        agent.allowed_tools_set = {"allowed_tool"}
        config.current_agent = agent
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance", return_value=registry
        ):
            result = await get_registry_system_prompt(config)
        assert "allowed_tool" in result
        assert "blocked_tool" not in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools._ensure_registry")
    async def test_empty_registry(self, mock_ensure, tmp_path, monkeypatch):
        """空注册表返回空。"""
        # 隔离真实用户主目录,避免 ~/.UniClaw/plugins/ 下的插件污染测试
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance",
            return_value=ToolRegistry(),
        ):
            result = await get_registry_system_prompt()
        assert result == ""

    @pytest.mark.asyncio
    @patch("uniclaw.tools._ensure_registry")
    async def test_plugin_tools_in_prompt(self, mock_ensure, tmp_path, monkeypatch):
        """用户级插件工具应通过注册表出现在系统提示词中(分类"插件")。"""
        plugin_dir = tmp_path / "home" / ".UniClaw" / "plugins" / "tools"
        plugin_dir.mkdir(parents=True)
        plugin_dir.joinpath("note.py").write_text(
            "from uniclaw.tools.base import tool\n"
            "\n"
            "@tool\n"
            "def note() -> str:\n"
            '    """写笔记"""\n'
            '    return "ok"\n'
            "\n"
            "def get_tools():\n"
            "    return [note]\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

        registry = ToolRegistry()
        with patch(
            "uniclaw.tools.registry.ToolRegistry.get_instance", return_value=registry
        ):
            result = await get_registry_system_prompt()
        assert "note" in result
        assert "插件" in result
        assert "note" in registry.get_all_entries()
