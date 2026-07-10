"""知识图谱上下文注入 — 将图谱摘要注入系统 prompt。"""

from __future__ import annotations

from pathlib import Path


def get_knowledge_system_prompt(root_dir: Path | None) -> str:
    """构建知识图谱的系统提示词。只在图谱非空时返回静态提示。"""
    if root_dir is None:
        return ""

    from uniclaw.context import get_app_dir
    from .graph import KnowledgeGraph
    from .tools import (
        kg_add_entity, kg_add_relation, kg_add_alias,
        kg_get_entity, kg_search, kg_neighbors, kg_path, kg_extract, kg_export,
    )

    db_path = get_app_dir(root_dir) / "knowledge.db"
    if not db_path.exists():
        return ""

    graph = KnowledgeGraph(db_path)
    try:
        if graph.get_stats()["entities"] == 0:
            return ""
        tools = "\n".join([
            f"- {kg_add_entity.name}: 添加实体",
            f"- {kg_add_relation.name}: 添加关系",
            f"- {kg_add_alias.name}: 添加别名(实体消歧)",
            f"- {kg_get_entity.name}: 查看实体详情",
            f"- {kg_search.name}: 搜索实体",
            f"- {kg_neighbors.name}: 查看关联实体",
            f"- {kg_path.name}: 查找实体间路径",
            f"- {kg_extract.name}: 从文本自动提取实体和关系",
            f"- {kg_export.name}: 导出(JSON/Markdown/HTML可视化)",
        ])
        return f"# 知识图谱\n你有知识图谱可用。\n{tools}"
    finally:
        graph.close()
