"""工具注册表测试 — 覆盖核心/扩展工具、BM25 搜索和能量管理。

注意:BM25 在 corpus 过小(<3 文档)时 idf 为 0 或负会导致搜索失败,
因此搜索类测试至少注册 3 个工具,且目标关键词为唯一 token。
"""

from pathlib import Path

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.tools.base import Tool, ToolRuntime
from uniclaw.tools.registry import (
    CORE_TOOL_NAMES,
    CORE_TOOLS,
    EXTENDED_TOOL_ENERGY_MAX,
    EXTENDED_TOOL_RECENT_PROTECT,
    MAX_LOADED_EXTENDED,
    ExtendedToolManager,
    ToolRegistry,
    _build_extended_keywords,
    _build_tool_categories,
    _name_match_tier,
    _norm_name,
    _keyword_match_tier,
    _category_match_tier,
    _entry_match_tier,
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
        """CORE_TOOL_NAMES = CORE_TOOLS 名字 + search_tools + 子代理专属核心工具。"""
        from uniclaw.tools.multi_agent.tools import subagent_send_to_parent

        assert CORE_TOOL_NAMES == (
            {t.name for t in CORE_TOOLS}
            | {"search_tools"}
            | {subagent_send_to_parent.name}
        )

    def test_search_tools_in_core(self):
        """search_tools 元工具在核心工具中。"""
        assert "search_tools" in CORE_TOOL_NAMES

    def test_known_core_tools(self):
        """包含关键核心工具。"""
        for name in ["Read", "Write", "Bash", "Grep", "webFetch"]:
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


# text_to_speech 查询下曾混入的无关工具(BM25 把 text_to_speech 拆成 text/to/speech
# 后,凡文档里带 "text"/"to" 的工具都能蹭分)
_TTS_NOISE_TOOLS = {
    "browser_get_text",
    "browser_insert_text",
    "wechat_send_text",
    "subagent_send_to_child",
    "ConvertToMarkdown",
    "ReadMedia",
    "cu_keyboard_type",
    "browser_navigate",
    "browser_type",
    "get_command_help",
    "exa_web_search_exa",
    "tavily_tavily_extract",
}


def _make_realistic_registry() -> ToolRegistry:
    """用真实扩展工具的关键词表构造注册表,复现 BM25 通用词噪声场景。"""
    r = ToolRegistry()
    for name, kws in _build_extended_keywords().items():
        r.register(_make_tool(name, f"desc for {name}"), kws, "cat")
    return r


class TestNameMatchRanking:
    """精确/前缀工具名匹配优先 — 回归 DockerSearch 命中 kg_search、text_to_speech 混入噪声。"""

    def test_norm_name_ignores_style(self):
        """归一化忽略大小写/下划线/空格/连字符。"""
        assert _norm_name("DockerSearch") == _norm_name("docker_search")
        assert _norm_name("DockerSearch") == _norm_name("docker search")
        assert _norm_name("DockerSearch") == _norm_name("docker-search")
        assert _norm_name("DockerSearch") == "dockersearch"

    def test_name_match_tier_levels(self):
        """名称匹配分层: 精确 > 前缀 > 子串。"""
        assert _name_match_tier("DockerSearch", "DockerSearch") == 3
        assert _name_match_tier("docker_search", "DockerSearch") == 3
        assert _name_match_tier("docker search", "DockerSearch") == 3
        assert _name_match_tier("hook", "hook_add") == 2
        assert _name_match_tier("screenshot", "cu_screenshot") == 1
        assert _name_match_tier("zzzz", "DockerSearch") == 0

    def test_name_match_tier_short_query(self):
        """过短查询词不做前缀/子串匹配,避免噪声。"""
        assert _name_match_tier("to", "text_to_speech") == 0
        assert _name_match_tier("a", "tool_alpha") == 0

    def test_dockersearch_exact_ranks_first(self):
        """精确工具名 DockerSearch 必须排第一,不能被 kg_search 等抢位。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("DockerSearch")]
        assert names, "精确工具名查询不应为空"
        assert names[0] == "DockerSearch"
        # kg_search 曾因分词出 "search" 蹭进结果,必须被剔除
        assert "kg_search" not in names

    def test_dockersearch_normalized_queries(self):
        """DockerSearch / docker_search / docker search / docker-search 同一查询。"""
        r = _make_realistic_registry()
        for q in ("DockerSearch", "docker_search", "docker search", "docker-search"):
            names = [e.tool.name for e in r.search(q)]
            assert names and names[0] == "DockerSearch", f"query={q!r} -> {names}"

    def test_dockersearch_not_only_kg_search(self):
        """DockerSearch 查询不能只给 kg_search。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("DockerSearch")]
        assert "kg_search" not in names or names[0] == "DockerSearch"
        assert "DockerSearch" in names

    def test_text_to_speech_no_noise(self):
        """text_to_speech 结果不应混入 8 个无关工具。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("text_to_speech")]
        assert names[0] == "text_to_speech"
        noise = set(names) & _TTS_NOISE_TOOLS
        assert not noise, f"text_to_speech 查询混入无关工具: {noise}"

    def test_text_to_speech_normalized(self):
        """text to speech 等写法同样只命中 text_to_speech。"""
        r = _make_realistic_registry()
        for q in ("text_to_speech", "text to speech", "TextToSpeech"):
            names = [e.tool.name for e in r.search(q)]
            assert names == ["text_to_speech"], f"query={q!r} -> {names}"

    def test_embedded_tool_name_in_sentence(self):
        """长句中嵌入工具名也能命中该工具,且不混入噪声。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("帮我用 DockerSearch 搜镜像")]
        assert names[0] == "DockerSearch"
        assert "kg_search" not in names

    def test_synonym_search_still_works(self):
        """中文同义词检索能力保留: 截图 → cu_screenshot。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("截图")]
        assert "cu_screenshot" in names

    def test_synonym_search_chinese_phrase(self):
        """中文功能描述检索能力保留: 定时任务 → schedule 系列。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("定时任务")]
        assert any(n.startswith("schedule_") for n in names)

    def test_prefix_family_grouping(self):
        """前缀查询聚合同族工具: hook → hook_* 系列。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("hook")]
        assert set(names) == {"hook_docs", "hook_read", "hook_add", "hook_remove"}

    def test_exact_match_wins_over_prefix(self):
        """精确匹配排在前缀/子串匹配之前。"""
        r = ToolRegistry()
        r.register(_make_tool("docker_search"), ["x"], "c")
        r.register(_make_tool("docker_search_extra"), ["y"], "c")
        r.register(_make_tool("my_docker_search"), ["z"], "c")
        r.register(_make_tool("other_tool"), ["w"], "c")
        names = [e.tool.name for e in r.search("docker_search")]
        assert names[0] == "docker_search"

    def test_keyword_exact_match_tier(self):
        """检索词精确命中给 3;前缀/子串不算(避免 "text" 蹭进 text_to_speech)。"""
        assert _keyword_match_tier("computer_use", ["computer_use", "截图"]) == 3
        assert _keyword_match_tier("computer use", ["computer_use"]) == 3
        assert _keyword_match_tier("text_to_speech", ["text"]) == 0
        assert _keyword_match_tier("text_to_speech", ["text to speech"]) == 3
        assert _keyword_match_tier("to", ["text to speech"]) == 0

    def test_category_alias_match_tier(self):
        """类别英文别名参与匹配: computer_use 命中"计算机操作"类。"""
        assert _category_match_tier("computer_use", "计算机操作") == 3
        assert _category_match_tier("desktop automation", "计算机操作") == 3
        assert _category_match_tier("text_to_speech", "TTS") == 0

    def test_entry_match_tier_takes_max(self):
        """名称/检索词/类别三层取最高。"""
        assert _entry_match_tier("cu_screenshot", "cu_screenshot", [], "x") == 3
        assert _entry_match_tier("computer_use", "cu_screenshot", ["computer_use"], "x") == 3
        assert _entry_match_tier("computer_use", "cu_screenshot", [], "计算机操作") == 3
        assert _entry_match_tier("zzzz", "cu_screenshot", [], "x") == 0

    def test_computer_use_hits_cu_family(self):
        """computer_use 必须命中 cu_* 家族,不能只给 exa/tavily/rag 等无关工具。"""
        r = _make_realistic_registry()
        names = [e.tool.name for e in r.search("computer_use", top_k=20)]
        cu_hits = [n for n in names if n.startswith("cu_")]
        assert cu_hits, f"computer_use 未命中任何 cu_* 工具: {names}"
        # 命中里应包含常用入口 cu_screenshot
        assert "cu_screenshot" in cu_hits

    def test_computer_use_no_noise(self):
        """computer_use 结果不应混入 get_command_help / rag_* 等蹭 "use" 的噪声。"""
        r = _make_realistic_registry()
        names = set(e.tool.name for e in r.search("computer_use", top_k=20))
        noise = {
            "get_command_help",
            "rag_evaluate",
            "rag_search",
            "rag_ingest",
            "exa_web_search_exa",
            "tavily_tavily_extract",
        } & names
        assert not noise, f"computer_use 查询混入无关工具: {noise}"

    def test_computer_use_normalized_queries(self):
        """computer use / computer-use 同样命中 cu_*。"""
        r = _make_realistic_registry()
        for q in ("computer_use", "computer use", "computer-use", "ComputerUse"):
            names = [e.tool.name for e in r.search(q, top_k=20)]
            assert any(n.startswith("cu_") for n in names), f"query={q!r} -> {names}"

    def test_name_hit_without_content_keeps_bm25(self):
        """名称蹭边但内容无关(BM25=0)时,不能丢弃内容相关的 BM25 结果。"""
        r = ToolRegistry()
        # 名字含查询子串(tier>0)但文档词元与查询无交集(BM25=0),只是蹭边
        r.register(_make_tool("computeruse_helper", "unrelated"), ["zzz"], "c")
        # 名称不匹配,但描述/检索词与查询词元有交集(BM25>0)
        r.register(_make_tool("content_match", "has computer use terms"), ["desktop"], "c")
        r.register(_make_tool("filler_one", "filler"), ["aaa"], "c")
        r.register(_make_tool("filler_two", "filler"), ["bbb"], "c")
        names = [e.tool.name for e in r.search("computer_use")]
        assert "content_match" in names


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
        """能量耗尽自动淘汰(已滑出最近调用窗口)。"""
        m = self._manager()
        m.touch("a")
        m.energy["a"] = 1
        # 滑出最近调用窗口,否则会被"刚用过"豁免
        m.recent_uses.clear()
        exhausted = m.drain_energy()
        assert "a" in exhausted
        assert "a" not in m.loaded

    def test_drain_energy_spares_recently_used(self):
        """最近调用过的工具即使能量耗尽也不淘汰 — 刚用过就被淘汰的回归。"""
        m = self._manager()
        m.touch("a")
        m.energy["a"] = 1
        exhausted = m.drain_energy()
        assert "a" not in exhausted
        assert "a" in m.loaded
        assert m.energy["a"] == 1  # 保底能量,下一轮再判定

    def test_touch_cancels_pending_evict(self):
        """touch 取消待淘汰 — 否则 apply() 会把刚用过的工具从 name2tool 卸载。"""
        m = self._manager()
        t = _make_tool("tool1")
        m.touch(t)
        m.evict(["tool1"])
        assert "tool1" in m.pending_evicted
        m.touch("tool1")  # 模拟工具调用后的 touch
        assert "tool1" not in m.pending_evicted
        assert "tool1" in m.loaded
        tools, name2tool = [t], {"tool1": t}
        m.apply(tools, name2tool)
        assert "tool1" in name2tool  # 未被误删

    def test_pick_eviction_skips_recently_used(self):
        """容量淘汰跳过最近调用窗口内的工具。"""
        m = self._manager()
        for i in range(MAX_LOADED_EXTENDED):
            m.touch(f"old_{i}")
        # recent_uses 只保留最近 N 个(old_{N}..old_{24}),老的可淘汰
        cands = m.pick_eviction_candidates(3)
        assert "old_0" in cands  # 最久未用
        assert not any(n in m.recent_uses for n in cands)

    def test_pick_eviction_respects_window(self):
        """全部在最近调用窗口内时不淘汰任何工具。"""
        m = self._manager()
        for i in range(EXTENDED_TOOL_RECENT_PROTECT):
            m.touch(f"t{i}")
        assert m.pick_eviction_candidates(5) == []

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
        assert categories["DockerCreate"] == "Docker"
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
    config.unavailable_tool_reasons = {}
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
            result = await search_tools("nonexistentxyz", tool_runtime=ToolRuntime(config=config))
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
            result = await search_tools("kgquery", tool_runtime=ToolRuntime(config=config))
        assert "kg_search" in result
        assert "kg_search" in config.current_agent.extended_mgr.loaded

    @pytest.mark.asyncio
    async def test_all_blocked(self):
        """工具存在但不可用 — 首行要说"命中 N 个但均不可用",不能说"未找到匹配"。"""
        registry = _make_search_registry()
        registry.register(_make_tool("kg_search", "desc"), ["kgquery"], "知识图谱")
        config, p = _make_search_config(
            registry, {"tool_alpha", "tool_beta", "tool_gamma"}
        )
        with p:
            result = await search_tools("kgquery", tool_runtime=ToolRuntime(config=config))
        first_line = result.splitlines()[0]
        # 误导回归: 旧行为首行写"未找到匹配 ... 的可用工具",agent 会误判为查询没命中
        assert "未找到匹配" not in first_line
        assert "命中" in first_line
        assert "不可用" in first_line
        assert "kg_search" in result

    @pytest.mark.asyncio
    async def test_no_hit_still_says_not_found(self):
        """真未命中(注册表里没有)时,首行才是"未找到匹配"。"""
        config, p = _make_search_config(ToolRegistry(), {"anything"})
        with p:
            result = await search_tools("zzz_no_such_tool", tool_runtime=ToolRuntime(config=config))
        first_line = result.splitlines()[0]
        assert "未找到匹配" in first_line

    @pytest.mark.asyncio
    async def test_recently_used_survives_capacity_eviction(self):
        """最近调用过的工具豁免容量淘汰 — 刚用过就被淘汰的回归。"""
        registry = _make_search_registry()
        registry.register(_make_tool("new_tool", "desc new"), ["newword"], "类别")
        config, p = _make_search_config(
            registry, {"new_tool", "tool_alpha", "tool_beta", "tool_gamma"}
        )
        mgr = config.current_agent.extended_mgr
        # 占满所有槽位,最后 touch 的 keep_me 在最近调用窗口内
        for i in range(MAX_LOADED_EXTENDED - 1):
            mgr.touch(f"old_{i}")
        mgr.touch("keep_me")
        with p:
            result = await search_tools("newword", tool_runtime=ToolRuntime(config=config))
        assert "已淘汰" in result
        assert "keep_me" in mgr.loaded  # 刚用过的不能被淘汰
        assert "keep_me" not in result.split("已淘汰")[-1]

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
            result = await search_tools("kgquery", tool_runtime=ToolRuntime(config=config))
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
            result = await search_tools("newword", tool_runtime=ToolRuntime(config=config))
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


class TestDockerUnavailableReason:
    """Docker 域禁用工具必须登记根因,与 advisor/GenerateImage 一致。

    直接调用 DockerCreate 时 _execute_single_tool 要报
    "工具 DockerCreate 未启用: Docker 不可用: <根因>",而非"工具不存在"。
    关键回归点: 缓存命中也要把原因写进当前 config(否则新会话拿到空原因)。
    """

    def _reset_cache(self):
        from uniclaw.tools.docker import tools as docker_tools

        docker_tools._docker_cache["result"] = None
        docker_tools._docker_cache["time"] = 0
        docker_tools._docker_cache["error"] = None
        return docker_tools

    @pytest.mark.asyncio
    async def test_unavailable_records_reason(self):
        """Docker 不可用 → 记录根因,返回空工具列表。"""
        docker_tools = self._reset_cache()
        config = MagicMock()
        config.unavailable_tool_reasons = {}
        config.record_unavailable_tools = lambda names, reason: [
            config.unavailable_tool_reasons.__setitem__(n, reason) for n in names
        ]
        config.clear_unavailable_tools = lambda names: [
            config.unavailable_tool_reasons.pop(n, None) for n in names
        ]
        with patch(
            "uniclaw.tools.docker.manager.DockerManager.check_docker",
            new=AsyncMock(return_value="[WinError 2] 系统找不到指定的文件"),
        ):
            result = await docker_tools.get_tools(config)
        assert result == []
        reason = config.unavailable_tool_reasons.get("DockerCreate")
        assert reason is not None
        assert "Docker 不可用" in reason
        assert "WinError 2" in reason
        # 所有 Docker 工具都要登记同一根因
        for t in docker_tools._ALL_TOOLS:
            assert t.name in config.unavailable_tool_reasons

    @pytest.mark.asyncio
    async def test_cache_hit_still_records_reason(self):
        """缓存命中也必须把根因写进新 config — 否则直接调用报"工具不存在"。"""
        docker_tools = self._reset_cache()
        cfg1 = MagicMock()
        cfg1.unavailable_tool_reasons = {}
        cfg1.record_unavailable_tools = lambda names, reason: [
            cfg1.unavailable_tool_reasons.__setitem__(n, reason) for n in names
        ]
        cfg1.clear_unavailable_tools = lambda names: [
            cfg1.unavailable_tool_reasons.pop(n, None) for n in names
        ]
        cfg2 = MagicMock()
        cfg2.unavailable_tool_reasons = {}
        cfg2.record_unavailable_tools = lambda names, reason: [
            cfg2.unavailable_tool_reasons.__setitem__(n, reason) for n in names
        ]
        cfg2.clear_unavailable_tools = lambda names: [
            cfg2.unavailable_tool_reasons.pop(n, None) for n in names
        ]
        mock_check = AsyncMock(return_value="Docker 服务未运行")
        with patch(
            "uniclaw.tools.docker.manager.DockerManager.check_docker", new=mock_check
        ):
            await docker_tools.get_tools(cfg1)
            assert mock_check.call_count == 1
            # 第二次调用命中缓存, 不应重新探测, 但仍要写原因
            result2 = await docker_tools.get_tools(cfg2)
            assert mock_check.call_count == 1
        assert result2 == []
        assert "Docker 服务未运行" in cfg2.unavailable_tool_reasons.get(
            "DockerCreate", ""
        )

    @pytest.mark.asyncio
    async def test_available_clears_reason(self):
        """Docker 可用 → 清除原因并返回全部工具。"""
        docker_tools = self._reset_cache()
        config = MagicMock()
        config.unavailable_tool_reasons = {"DockerCreate": "旧原因"}
        config.record_unavailable_tools = lambda names, reason: [
            config.unavailable_tool_reasons.__setitem__(n, reason) for n in names
        ]
        config.clear_unavailable_tools = lambda names: [
            config.unavailable_tool_reasons.pop(n, None) for n in names
        ]
        with patch(
            "uniclaw.tools.docker.manager.DockerManager.check_docker",
            new=AsyncMock(return_value=None),
        ):
            result = await docker_tools.get_tools(config)
        assert len(result) == len(docker_tools._ALL_TOOLS)
        assert "DockerCreate" not in config.unavailable_tool_reasons

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


class TestSearchToolsDocstring:
    """search_tools docstring 必须写明加载机制,否则 LLM 会把"搜不到"误判为工具缺失。

    断言 description(LLM 实际看到的 schema 描述,由 @tool 从 docstring 生成)
    与 func.__doc__(原始 docstring)都含关键信息。
    """

    @staticmethod
    def _doc() -> str:
        return (search_tools.description or "") + "\n" + (search_tools.func.__doc__ or "")

    def test_docstring_mentions_search_result_cap(self):
        """单次搜索约 10 条上限 — 大组工具需拆多次搜索。"""
        doc = self._doc()
        assert "10" in doc

    def test_docstring_mentions_lru_eviction(self):
        """扩展工具池 LRU 淘汰 — 跨轮可能要重搜。"""
        doc = self._doc()
        assert "LRU" in doc
        assert "淘汰" in doc

    def test_docstring_mentions_not_found_is_not_missing(self):
        """搜不到 ≠ 不存在,大组工具未加载时直接调用会报未启用/未加载。"""
        doc = self._doc()
        assert "搜不到" in doc
        assert "不存在" in doc
