"""知识图谱测试 — 覆盖 KnowledgeGraph 的实体/关系 CRUD、搜索、路径查找等。"""

import pytest
from pathlib import Path

from uniclaw.tools.knowledge.graph import KnowledgeGraph


@pytest.fixture
def kg(tmp_path):
    """创建临时知识图谱实例。"""
    db_path = tmp_path / "test_knowledge.db"
    graph = KnowledgeGraph(db_path)
    yield graph
    graph.close()


class TestEntityCRUD:
    """实体增删改查测试。"""

    def test_add_entity(self, kg):
        """添加实体。"""
        result = kg.add_entity("Python", "technology", description="编程语言")
        assert "id" in result
        assert result["id"] > 0
        assert result.get("duplicate_warning") is None

    def test_add_entity_duplicate(self, kg):
        """重复添加同名同类型实体返回已有 ID。"""
        r1 = kg.add_entity("Python", "technology")
        r2 = kg.add_entity("Python", "technology")
        assert r1["id"] == r2["id"]
        assert "已存在" in r2.get("duplicate_warning", "")

    def test_get_entity(self, kg):
        """获取实体详情。"""
        kg.add_entity("Python", "technology", description="编程语言")
        entity = kg.get_entity("Python")
        assert entity is not None
        assert entity["name"] == "Python"
        assert entity["type"] == "technology"
        assert entity["description"] == "编程语言"

    def test_get_entity_not_found(self, kg):
        """获取不存在的实体返回 None。"""
        entity = kg.get_entity("Nonexistent")
        assert entity is None

    def test_update_entity(self, kg):
        """更新实体属性。"""
        kg.add_entity("Python", "technology", description="旧描述")
        result = kg.update_entity("Python", description="新描述")
        assert result.get("ok") is True
        entity = kg.get_entity("Python")
        assert entity["description"] == "新描述"

    def test_delete_entity(self, kg):
        """删除实体。"""
        kg.add_entity("Python", "technology")
        result = kg.delete_entity("Python")
        assert result.get("ok") is True
        assert kg.get_entity("Python") is None

    def test_delete_entity_not_found(self, kg):
        """删除不存在的实体返回错误。"""
        result = kg.delete_entity("Nonexistent")
        assert "error" in result

    def test_add_entity_with_properties(self, kg):
        """添加带属性的实体。"""
        kg.add_entity(
            "Python",
            "technology",
            properties={"version": "3.14", "paradigm": "multi"},
        )
        entity = kg.get_entity("Python")
        assert entity is not None
        assert entity["properties"]["version"] == "3.14"


class TestRelationCRUD:
    """关系增删改查测试。"""

    def test_add_relation(self, kg):
        """添加关系。"""
        kg.add_entity("Guido", "person")
        kg.add_entity("Python", "technology")
        result = kg.add_relation("Guido", "Python", "created_by")
        assert "id" in result
        assert result["relation"] == "created_by"

    def test_add_relation_missing_entity(self, kg):
        """引用不存在的实体时返回错误。"""
        result = kg.add_relation("Nonexistent1", "Nonexistent2", "related_to")
        assert "error" in result

    def test_delete_relation(self, kg):
        """删除关系。"""
        kg.add_entity("A", "concept")
        kg.add_entity("B", "concept")
        kg.add_relation("A", "B", "related_to")
        result = kg.delete_relation("A", "B", "related_to")
        assert result.get("ok") is True

    def test_delete_relation_not_found(self, kg):
        """删除不存在的关系返回错误。"""
        kg.add_entity("A", "concept")
        kg.add_entity("B", "concept")
        result = kg.delete_relation("A", "B", "nonexistent")
        assert "error" in result

    def test_get_entity_with_relations(self, kg):
        """获取实体时包含关系信息。"""
        kg.add_entity("A", "concept")
        kg.add_entity("B", "concept")
        kg.add_relation("A", "B", "related_to")
        entity = kg.get_entity("A")
        assert entity is not None
        assert len(entity["relations"]) > 0


class TestSearch:
    """搜索测试。"""

    def test_search_by_name(self, kg):
        """按名称搜索。"""
        kg.add_entity("Python", "technology", description="编程语言")
        kg.add_entity("Java", "technology", description="另一种语言")
        results = kg.search_entities("Python")
        assert len(results) > 0
        names = [r["name"] for r in results]
        assert "Python" in names

    def test_search_by_description(self, kg):
        """按描述搜索。"""
        kg.add_entity("Python", "technology", description="interpreted programming language")
        results = kg.search_entities("interpreted")
        assert len(results) > 0

    def test_search_empty(self, kg):
        """搜索无结果时返回空列表。"""
        results = kg.search_entities("nonexistent_keyword_xyz")
        assert results == []

    def test_search_multiple_results(self, kg):
        """搜索返回多个结果(同名不同类型)。"""
        kg.add_entity("Python", "technology")
        kg.add_entity("Python", "concept")
        results = kg.search_entities("Python")
        assert len(results) >= 2


class TestAliases:
    """别名测试。"""

    def test_add_alias(self, kg):
        """添加别名。"""
        kg.add_entity("Python", "technology")
        result = kg.add_alias("Python", "technology", "Py")
        assert result.get("ok") is True

    def test_duplicate_alias(self, kg):
        """重复添加别名返回错误。"""
        kg.add_entity("Python", "technology")
        kg.add_alias("Python", "technology", "Py")
        result = kg.add_alias("Python", "technology", "Py")
        assert "error" in result


class TestNeighbors:
    """邻居查询测试。"""

    def test_neighbors_depth_1(self, kg):
        """一跳邻居。"""
        kg.add_entity("A", "concept")
        kg.add_entity("B", "concept")
        kg.add_entity("C", "concept")
        kg.add_relation("A", "B", "related_to")
        kg.add_relation("A", "C", "related_to")
        neighbors = kg.get_neighbors("A", depth=1)
        names = [n["name"] for n in neighbors]
        assert "B" in names
        assert "C" in names

    def test_neighbors_empty(self, kg):
        """无邻居时返回空列表。"""
        kg.add_entity("A", "concept")
        neighbors = kg.get_neighbors("A", depth=1)
        assert neighbors == []


class TestPath:
    """路径查找测试。"""

    def test_path_direct(self, kg):
        """直接关系的路径。"""
        kg.add_entity("A", "concept")
        kg.add_entity("B", "concept")
        kg.add_relation("A", "B", "related_to")
        path = kg.find_path("A", "B")
        assert len(path) > 0

    def test_path_not_found(self, kg):
        """无路径时返回空列表。"""
        kg.add_entity("A", "concept")
        kg.add_entity("B", "concept")
        path = kg.find_path("A", "B")
        assert path == []


class TestExport:
    """导出测试。"""

    def test_export_json(self, kg):
        """JSON 导出。"""
        kg.add_entity("Python", "technology")
        data = kg.export_json()
        assert "entities" in data
        assert "relations" in data
        assert len(data["entities"]) == 1

    def test_export_markdown(self, kg):
        """Markdown 导出。"""
        kg.add_entity("Python", "technology", description="编程语言")
        md = kg.export_markdown()
        assert "Python" in md
        assert "technology" in md

    def test_export_empty(self, kg):
        """空图谱导出不报错。"""
        data = kg.export_json()
        assert data["entities"] == []


class TestCheckDuplicate:
    """重复检查测试。"""

    def test_no_duplicates(self, kg):
        """无重复时返回空列表。"""
        dups = kg.check_duplicate("Python", "technology")
        assert dups == []

    def test_exact_duplicate(self, kg):
        """精确重复。"""
        kg.add_entity("Python", "technology")
        dups = kg.check_duplicate("Python", "technology")
        assert len(dups) > 0
