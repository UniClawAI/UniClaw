"""知识图谱工具层测试 — 覆盖 kg_* 工具对 KnowledgeGraph 的包装。"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from uniclaw.context import Scope
from uniclaw.tools.knowledge.tools import (
    _get_graph,
    get_all_tools,
    get_tools,
    kg_add_alias,
    kg_add_entity,
    kg_add_relation,
    kg_clear,
    kg_delete_entity,
    kg_delete_relation,
    kg_export,
    kg_extract,
    kg_get_entity,
    kg_list,
    kg_merge_entities,
    kg_neighbors,
    kg_path,
    kg_search,
    kg_stats,
    kg_update_entity,
)


def _make_config(root_dir: Path) -> MagicMock:
    """构造带 root_dir 的 mock config。"""
    config = MagicMock()
    config.root_dir = root_dir
    return config


class TestToolRegistration:
    """工具注册测试。"""

    def test_get_tools_returns_sixteen(self):
        """get_tools 返回 16 个工具。"""
        assert len(get_tools()) == 16

    def test_tool_names(self):
        """工具名称齐全。"""
        names = [t.name for t in get_tools()]
        for expected in [
            "kg_add_entity",
            "kg_add_relation",
            "kg_add_alias",
            "kg_update_entity",
            "kg_delete_entity",
            "kg_delete_relation",
            "kg_merge_entities",
            "kg_get_entity",
            "kg_search",
            "kg_neighbors",
            "kg_path",
            "kg_stats",
            "kg_export",
            "kg_list",
            "kg_extract",
            "kg_clear",
        ]:
            assert expected in names

    def test_get_all_tools_same(self):
        """get_all_tools 与 get_tools 相同。"""
        assert len(get_all_tools()) == len(get_tools())


class TestGetGraph:
    """_get_graph scope 解析测试。"""

    @patch("uniclaw.tools.knowledge.tools.get_app_dir")
    @patch("uniclaw.tools.knowledge.tools.KnowledgeGraph")
    def test_project_scope_uses_root_dir(self, mock_kg, mock_app_dir):
        """project scope 且 root_dir 存在时使用 root_dir。"""
        config = MagicMock()
        config.root_dir = Path("/proj")
        mock_app_dir.return_value = Path("/app")
        _get_graph(config, Scope.PROJECT)
        mock_app_dir.assert_called_once_with(Path("/proj"))
        mock_kg.assert_called_once_with(Path("/app") / "knowledge.db")

    @patch("uniclaw.tools.knowledge.tools.get_app_dir")
    @patch("uniclaw.tools.knowledge.tools.KnowledgeGraph")
    def test_user_scope_ignores_root_dir(self, mock_kg, mock_app_dir):
        """user scope 不使用 root_dir。"""
        config = MagicMock()
        config.root_dir = Path("/proj")
        _get_graph(config, Scope.USER)
        mock_app_dir.assert_called_once_with(Scope.USER)

    @patch("uniclaw.tools.knowledge.tools.get_app_dir")
    @patch("uniclaw.tools.knowledge.tools.KnowledgeGraph")
    def test_no_root_dir_falls_back_user(self, mock_kg, mock_app_dir):
        """无 root_dir 时回退到 user scope。"""
        config = MagicMock()
        config.root_dir = None
        _get_graph(config, Scope.PROJECT)
        mock_app_dir.assert_called_once_with(Scope.USER)


class TestAddEntity:
    """kg_add_entity 测试。"""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        """添加实体成功。"""
        config = _make_config(tmp_path)
        result = await kg_add_entity(
            "Python", type="technology", description="编程语言", config=config
        )
        assert "已添加" in result
        assert "Python" in result
        assert "technology" in result
        assert "ID=" in result
        # 验证持久化
        detail = await kg_get_entity("Python", config=config)
        assert "实体: Python" in detail

    @pytest.mark.asyncio
    async def test_duplicate_warning(self, tmp_path):
        """重复添加返回警告。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        result = await kg_add_entity("Python", type="technology", config=config)
        assert "⚠" in result
        assert "已存在" in result

    @pytest.mark.asyncio
    async def test_with_properties(self, tmp_path):
        """带属性添加。"""
        config = _make_config(tmp_path)
        await kg_add_entity(
            "Python",
            type="technology",
            properties={"version": "3.14"},
            config=config,
        )
        detail = await kg_get_entity("Python", config=config)
        assert "version=3.14" in detail

    @pytest.mark.asyncio
    async def test_default_type_concept(self, tmp_path):
        """默认类型为 concept。"""
        config = _make_config(tmp_path)
        await kg_add_entity("UniClaw", config=config)
        detail = await kg_get_entity("UniClaw", config=config)
        assert "type=concept" in detail


class TestAddRelation:
    """kg_add_relation 测试。"""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        """添加关系成功。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Guido", type="person", config=config)
        await kg_add_entity("Python", type="technology", config=config)
        result = await kg_add_relation(
            "Guido", "Python", "created_by", config=config
        )
        assert "关系已添加" in result
        assert "Guido" in result
        assert "created_by" in result
        assert "Python" in result

    @pytest.mark.asyncio
    async def test_missing_source(self, tmp_path):
        """源实体不存在返回错误。"""
        config = _make_config(tmp_path)
        result = await kg_add_relation(
            "Nonexistent", "Python", "related_to", config=config
        )
        assert "错误" in result
        assert "源实体" in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_missing_target(self, tmp_path):
        """目标实体不存在返回错误。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Guido", type="person", config=config)
        result = await kg_add_relation(
            "Guido", "Nonexistent", "related_to", config=config
        )
        assert "错误" in result
        assert "目标实体" in result


class TestAddAlias:
    """kg_add_alias 测试。"""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        """添加别名成功。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        result = await kg_add_alias("Python", "Py", config=config)
        assert "别名已添加" in result
        assert "Python" in result
        assert "Py" in result

    @pytest.mark.asyncio
    async def test_entity_missing(self, tmp_path):
        """实体不存在返回错误。"""
        config = _make_config(tmp_path)
        result = await kg_add_alias("Nonexistent", "X", config=config)
        assert "错误" in result
        assert "不存在" in result


class TestUpdateEntity:
    """kg_update_entity 测试。"""

    @pytest.mark.asyncio
    async def test_update_description(self, tmp_path):
        """更新描述。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        result = await kg_update_entity("Python", description="新描述", config=config)
        assert "已更新" in result
        assert "description" in result
        detail = await kg_get_entity("Python", config=config)
        assert "新描述" in detail

    @pytest.mark.asyncio
    async def test_entity_missing(self, tmp_path):
        """实体不存在返回错误。"""
        config = _make_config(tmp_path)
        result = await kg_update_entity("Nonexistent", description="x", config=config)
        assert "错误" in result
        assert "不存在" in result

    @pytest.mark.asyncio
    async def test_no_fields(self, tmp_path):
        """无有效更新字段返回错误。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        result = await kg_update_entity("Python", config=config)
        assert "错误" in result


class TestDeleteEntity:
    """kg_delete_entity 测试。"""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        """删除实体成功。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        result = await kg_delete_entity("Python", config=config)
        assert "实体 'Python' 及其关系已删除" in result
        # 验证已删除
        detail = await kg_get_entity("Python", config=config)
        assert "未找到实体" in detail

    @pytest.mark.asyncio
    async def test_missing(self, tmp_path):
        """实体不存在返回错误。"""
        config = _make_config(tmp_path)
        result = await kg_delete_entity("Nonexistent", config=config)
        assert "错误" in result
        assert "不存在" in result


class TestDeleteRelation:
    """kg_delete_relation 测试。"""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        """删除关系成功。"""
        config = _make_config(tmp_path)
        await kg_add_entity("A", type="concept", config=config)
        await kg_add_entity("B", type="concept", config=config)
        await kg_add_relation("A", "B", "related_to", config=config)
        result = await kg_delete_relation("A", "B", "related_to", config=config)
        assert "关系已删除" in result
        assert "A" in result
        assert "B" in result

    @pytest.mark.asyncio
    async def test_missing(self, tmp_path):
        """关系不存在返回错误。"""
        config = _make_config(tmp_path)
        await kg_add_entity("A", type="concept", config=config)
        await kg_add_entity("B", type="concept", config=config)
        result = await kg_delete_relation("A", "B", "nonexistent", config=config)
        assert "错误" in result
        assert "关系不存在" in result


class TestMergeEntities:
    """kg_merge_entities 测试。"""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path):
        """合并实体成功。"""
        config = _make_config(tmp_path)
        await kg_add_entity("A", type="concept", config=config)
        await kg_add_entity("B", type="concept", config=config)
        await kg_add_entity("C", type="concept", config=config)
        await kg_add_relation("A", "C", "related_to", config=config)
        result = await kg_merge_entities("A", "B", config=config)
        assert "实体合并完成" in result
        assert "'A'" in result
        assert "'B'" in result
        assert "转移关系: 1 条" in result
        assert "转移别名" in result

    @pytest.mark.asyncio
    async def test_source_missing(self, tmp_path):
        """源实体不存在返回错误。"""
        config = _make_config(tmp_path)
        await kg_add_entity("B", type="concept", config=config)
        result = await kg_merge_entities("Nonexistent", "B", config=config)
        assert "错误" in result
        assert "不存在" in result


class TestGetEntity:
    """kg_get_entity 测试。"""

    @pytest.mark.asyncio
    async def test_found(self, tmp_path):
        """获取实体详情。"""
        config = _make_config(tmp_path)
        await kg_add_entity(
            "Python",
            type="technology",
            description="编程语言",
            properties={"version": "3.14"},
            config=config,
        )
        await kg_add_entity("Guido", type="person", config=config)
        await kg_add_relation("Guido", "Python", "created_by", config=config)
        await kg_add_alias("Python", "Py", config=config)

        result = await kg_get_entity("Python", config=config)
        assert "实体: Python" in result
        assert "type=technology" in result
        assert "描述: 编程语言" in result
        assert "别名: Py" in result
        assert "version=3.14" in result
        assert "置信度: 100%" in result
        assert "来源: manual" in result
        assert "created_by" in result

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path):
        """实体不存在。"""
        config = _make_config(tmp_path)
        result = await kg_get_entity("Nonexistent", config=config)
        assert "未找到实体 'Nonexistent'" in result


class TestSearch:
    """kg_search 测试。"""

    @pytest.mark.asyncio
    async def test_found(self, tmp_path):
        """搜索命中。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        await kg_add_entity("Java", type="technology", config=config)
        result = await kg_search("Python", config=config)
        assert "找到 1 个匹配实体" in result
        assert "[technology] Python" in result

    @pytest.mark.asyncio
    async def test_empty(self, tmp_path):
        """搜索无结果。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        result = await kg_search("zzzznotfound", config=config)
        assert "未找到匹配 'zzzznotfound' 的实体" in result

    @pytest.mark.asyncio
    async def test_type_filter(self, tmp_path):
        """按类型过滤。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        await kg_add_entity("Python", type="concept", config=config)
        result = await kg_search("Python", type="concept", config=config)
        assert "找到 1 个匹配实体" in result
        assert "[concept] Python" in result
        assert "[technology] Python" not in result


class TestNeighbors:
    """kg_neighbors 测试。"""

    @pytest.mark.asyncio
    async def test_found(self, tmp_path):
        """查询邻居。"""
        config = _make_config(tmp_path)
        await kg_add_entity("A", type="concept", config=config)
        await kg_add_entity("B", type="concept", config=config)
        await kg_add_entity("C", type="concept", config=config)
        await kg_add_relation("A", "B", "related_to", config=config)
        await kg_add_relation("A", "C", "related_to", config=config)
        result = await kg_neighbors("A", config=config)
        assert "实体 'A' 的邻居" in result
        assert "B" in result
        assert "C" in result
        assert "related_to" in result

    @pytest.mark.asyncio
    async def test_empty(self, tmp_path):
        """无邻居。"""
        config = _make_config(tmp_path)
        await kg_add_entity("A", type="concept", config=config)
        result = await kg_neighbors("A", config=config)
        assert "实体 'A' 没有邻居" in result

    @pytest.mark.asyncio
    async def test_entity_missing(self, tmp_path):
        """实体不存在视为无邻居。"""
        config = _make_config(tmp_path)
        result = await kg_neighbors("Nonexistent", config=config)
        assert "没有邻居" in result


class TestPath:
    """kg_path 测试。"""

    @pytest.mark.asyncio
    async def test_found(self, tmp_path):
        """找到路径。"""
        config = _make_config(tmp_path)
        await kg_add_entity("A", type="concept", config=config)
        await kg_add_entity("B", type="concept", config=config)
        await kg_add_relation("A", "B", "related_to", config=config)
        result = await kg_path("A", "B", config=config)
        assert "找到 1 条路径" in result
        assert "路径 1" in result
        assert "--[related_to]--> B" in result

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path):
        """无路径。"""
        config = _make_config(tmp_path)
        await kg_add_entity("A", type="concept", config=config)
        await kg_add_entity("B", type="concept", config=config)
        result = await kg_path("A", "B", config=config)
        assert "未找到 'A' 到 'B' 的路径" in result


class TestStats:
    """kg_stats 测试。"""

    @pytest.mark.asyncio
    async def test_empty(self, tmp_path):
        """空图谱。"""
        config = _make_config(tmp_path)
        result = await kg_stats(config=config)
        assert "知识图谱统计" in result
        assert "实体: 0" in result
        assert "关系: 0" in result
        assert "别名: 0" in result

    @pytest.mark.asyncio
    async def test_with_data(self, tmp_path):
        """有数据时显示类型分布。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        await kg_add_entity("Java", type="technology", config=config)
        await kg_add_entity("Guido", type="person", config=config)
        await kg_add_relation("Guido", "Python", "created_by", config=config)
        await kg_add_alias("Python", "Py", config=config)
        result = await kg_stats(config=config)
        assert "实体: 3" in result
        assert "关系: 1" in result
        assert "别名: 1" in result
        assert "实体类型分布" in result
        assert "technology: 2" in result
        assert "person: 1" in result
        assert "关系类型分布" in result
        assert "created_by: 1" in result


class TestExport:
    """kg_export 测试。"""

    @pytest.mark.asyncio
    async def test_json(self, tmp_path):
        """导出 JSON。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        out = tmp_path / "kg.json"
        result = await kg_export(str(out), config=config)
        assert "知识图谱已导出" in result
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "entities" in data
        assert data["entities"][0]["name"] == "Python"

    @pytest.mark.asyncio
    async def test_markdown(self, tmp_path):
        """导出 Markdown。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        out = tmp_path / "kg.md"
        result = await kg_export(str(out), config=config)
        assert "知识图谱已导出" in result
        content = out.read_text(encoding="utf-8")
        assert "Python" in content
        assert "technology" in content

    @pytest.mark.asyncio
    async def test_html(self, tmp_path):
        """导出 HTML 可视化。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        out = tmp_path / "kg.html"
        result = await kg_export(str(out), config=config)
        assert "HTML 可视化已生成" in result
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "DOCTYPE html" in content

    @pytest.mark.asyncio
    async def test_unsupported_ext(self, tmp_path):
        """不支持的后缀。"""
        config = _make_config(tmp_path)
        out = tmp_path / "kg.txt"
        result = await kg_export(str(out), config=config)
        assert "不支持的文件后缀" in result
        assert not out.exists()


class TestList:
    """kg_list 测试。"""

    @pytest.mark.asyncio
    async def test_empty(self, tmp_path):
        """空图谱。"""
        config = _make_config(tmp_path)
        result = await kg_list(config=config)
        assert "知识图谱为空" in result

    @pytest.mark.asyncio
    async def test_with_items(self, tmp_path):
        """列出实体。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", description="编程语言", config=config)
        await kg_add_entity("Java", type="technology", config=config)
        result = await kg_list(config=config)
        assert "共 2 个实体" in result
        assert "[technology] Python" in result
        assert "编程语言" in result

    @pytest.mark.asyncio
    async def test_type_filter(self, tmp_path):
        """按类型过滤。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        await kg_add_entity("Guido", type="person", config=config)
        result = await kg_list(type="person", config=config)
        assert "共 1 个实体" in result
        assert "[person] Guido" in result
        assert "Python" not in result


class TestClear:
    """kg_clear 测试。"""

    @pytest.mark.asyncio
    async def test_clear(self, tmp_path):
        """清空图谱。"""
        config = _make_config(tmp_path)
        await kg_add_entity("Python", type="technology", config=config)
        await kg_add_entity("Guido", type="person", config=config)
        await kg_add_relation("Guido", "Python", "created_by", config=config)
        result = await kg_clear(config=config)
        assert "知识图谱已清空" in result
        assert "删除了 2 个实体" in result
        assert "1 条关系" in result
        # 验证已清空
        listed = await kg_list(config=config)
        assert "知识图谱为空" in listed


class TestExtract:
    """kg_extract 测试。"""

    @pytest.mark.asyncio
    async def test_no_input(self, tmp_path):
        """未提供 text 和 path。"""
        config = _make_config(tmp_path)
        result = await kg_extract(config=config)
        assert "请提供 text 或 path" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions", return_value={})
    async def test_agent_def_missing(self, mock_defs, tmp_path):
        """未找到 kg-extract 定义。"""
        config = _make_config(tmp_path)
        result = await kg_extract(text="hello", config=config)
        assert "未找到 'kg-extract' 子智能体定义" in result

    @pytest.mark.asyncio
    @patch("uniclaw.utils.format.parse_json_from_llm")
    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions")
    @patch("uniclaw.agent.MultiAgent.get_instance")
    async def test_success(self, mock_get, mock_defs, mock_parse, tmp_path):
        """完整提取流程。"""
        config = _make_config(tmp_path)
        # 预先真实添加 Python 实体,供关系引用
        await kg_add_entity("Python", type="technology", config=config)

        mock_defs.return_value = {"kg-extract": MagicMock(name="kg-extract-def")}
        mgr = MagicMock()
        task = MagicMock()
        task.status = "ok"
        task.result = "subagent output"
        mgr.start_sub_agent = AsyncMock(return_value=task)
        mgr.wait = AsyncMock()
        mock_get.return_value = mgr
        mock_parse.return_value = {
            "entities": [
                {
                    "name": "Numpy",
                    "type": "technology",
                    "description": "数组计算库",
                }
            ],
            "relations": [
                {"source": "Numpy", "target": "Python", "relation": "based_on"}
            ],
        }

        result = await kg_extract(text="Numpy 是一个数组库", config=config)

        assert "AI 提取结果" in result
        assert "实体: 1 个" in result
        assert "关系: 1 个" in result
        assert "[technology] Numpy: 已添加" in result
        assert "Numpy --[based_on]--> Python: 已添加" in result
        mgr.start_sub_agent.assert_called_once()
        mgr.wait.assert_called_once()

    @pytest.mark.asyncio
    @patch("uniclaw.utils.format.parse_json_from_llm", return_value=None)
    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions")
    @patch("uniclaw.agent.MultiAgent.get_instance")
    async def test_parse_failure(self, mock_get, mock_defs, mock_parse, tmp_path):
        """返回内容无法解析为 JSON。"""
        config = _make_config(tmp_path)
        mock_defs.return_value = {"kg-extract": MagicMock()}
        mgr = MagicMock()
        task = MagicMock()
        task.status = "ok"
        task.result = "not json"
        mgr.start_sub_agent = AsyncMock(return_value=task)
        mgr.wait = AsyncMock()
        mock_get.return_value = mgr

        result = await kg_extract(text="hello", config=config)
        assert "无法解析为 JSON" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions")
    @patch("uniclaw.agent.MultiAgent.get_instance")
    async def test_start_exception(self, mock_get, mock_defs, tmp_path):
        """subagent 启动抛出异常。"""
        config = _make_config(tmp_path)
        mock_defs.return_value = {"kg-extract": MagicMock()}
        mgr = MagicMock()
        mgr.start_sub_agent = AsyncMock(side_effect=Exception("boom"))
        mock_get.return_value = mgr

        result = await kg_extract(text="hello", config=config)
        assert "subagent 启动失败: boom" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.multi_agent.sub_agent.load_agent_definitions")
    @patch("uniclaw.agent.MultiAgent.get_instance")
    async def test_start_failed_status(self, mock_get, mock_defs, tmp_path):
        """subagent 状态为 FAILED。"""
        from uniclaw.agent import AgentStatus

        config = _make_config(tmp_path)
        mock_defs.return_value = {"kg-extract": MagicMock()}
        mgr = MagicMock()
        task = MagicMock()
        task.status = AgentStatus.FAILED
        task.result = "初始化错误"
        mgr.start_sub_agent = AsyncMock(return_value=task)
        mock_get.return_value = mgr

        result = await kg_extract(text="hello", config=config)
        assert "subagent 启动失败: 初始化错误" in result
