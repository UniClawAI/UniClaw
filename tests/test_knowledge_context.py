"""知识图谱上下文注入测试 — knowledge/context.py(原本 0% 覆盖)。

get_knowledge_system_prompt 构建图谱系统提示词:无数据返回空串,
有数据列出所有可用工具。_resolve_db_path 解析用户级/项目级 DB 路径。
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from uniclaw.tools.knowledge.context import (
    get_knowledge_system_prompt,
    _resolve_db_path,
)
from uniclaw.context import Scope

EXPECTED_TOOL_NAMES = [
    "kg_add_entity",
    "kg_add_relation",
    "kg_add_alias",
    "kg_get_entity",
    "kg_search",
    "kg_neighbors",
    "kg_path",
    "kg_extract",
    "kg_export",
]


class TestResolveDbPath:
    """DB 路径解析。"""

    def test_user_scope(self):
        """用户级图谱路径在 get_app_dir(Scope.USER)/knowledge.db。"""
        with patch(
            "uniclaw.context.get_app_dir",
            return_value=Path("/mock/appdir"),
        ) as mock_ga:
            got = _resolve_db_path(Scope.USER)
        mock_ga.assert_called_once_with(Scope.USER)
        assert got == Path("/mock/appdir") / "knowledge.db"

    def test_project_root(self):
        """项目级(Path)图谱路径在对应 root/knowledge.db。"""
        with patch(
            "uniclaw.context.get_app_dir",
            return_value=Path("/mock/projdir"),
        ) as mock_ga:
            got = _resolve_db_path(Path("/some/root"))
        assert got == Path("/mock/projdir") / "knowledge.db"


class TestGetKnowledgeSystemPrompt:
    """系统提示词构建。"""

    def _mock_db_path(self, exists: bool) -> Path:
        db = MagicMock(spec=Path)
        db.exists.return_value = exists
        return db

    def test_no_data_returns_empty(self):
        """用户级图谱文件不存在时返回空串。"""
        with patch(
            "uniclaw.tools.knowledge.context._resolve_db_path",
            return_value=self._mock_db_path(False),
        ):
            assert get_knowledge_system_prompt(None) == ""

    def test_empty_graph_returns_empty(self):
        """图谱存在但无实体时返回空串,并关闭连接。"""
        graph = MagicMock()
        graph.get_stats.return_value = {"entities": 0}
        with (
            patch(
                "uniclaw.tools.knowledge.context._resolve_db_path",
                return_value=self._mock_db_path(True),
            ),
            patch(
                "uniclaw.tools.knowledge.graph.KnowledgeGraph",
                return_value=graph,
            ) as mock_kg,
        ):
            assert get_knowledge_system_prompt(None) == ""
        mock_kg.assert_called_once()
        graph.close.assert_called_once()  # 无数据也要关闭

    def test_has_data_lists_tools(self):
        """图谱有实体时返回包含全部图谱工具名的提示词。"""
        graph = MagicMock()
        graph.get_stats.return_value = {"entities": 3}
        with (
            patch(
                "uniclaw.tools.knowledge.context._resolve_db_path",
                return_value=self._mock_db_path(True),
            ),
            patch(
                "uniclaw.tools.knowledge.graph.KnowledgeGraph",
                return_value=graph,
            ) as mock_kg,
        ):
            got = get_knowledge_system_prompt(None)

        assert got.startswith("# 知识图谱")
        for n in EXPECTED_TOOL_NAMES:
            assert n in got, f"提示词缺少工具 {n}"
        mock_kg.assert_called_once()
        graph.close.assert_called_once()

    def test_project_scope_included(self):
        """root_dir 非 None 时,项目级图谱也会被检查。"""
        graph_user = MagicMock()
        graph_user.get_stats.return_value = {"entities": 0}
        graph_proj = MagicMock()
        graph_proj.get_stats.return_value = {"entities": 2}
        db_user = self._mock_db_path(True)
        db_proj = self._mock_db_path(True)
        with (
            patch(
                "uniclaw.tools.knowledge.context._resolve_db_path",
                side_effect=[db_user, db_proj],
            ),
            patch(
                "uniclaw.tools.knowledge.graph.KnowledgeGraph",
                side_effect=[graph_user, graph_proj],
            ) as mock_kg,
        ):
            got = get_knowledge_system_prompt(Path("/proj"))

        assert got != ""
        assert mock_kg.call_count == 2  # 用户级 + 项目级
        graph_user.close.assert_called_once()
        graph_proj.close.assert_called_once()