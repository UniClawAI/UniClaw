"""知识图谱上下文注入 — 将图谱摘要注入系统 prompt。"""

from __future__ import annotations

from pathlib import Path

from uniclaw.context import Scope


def get_knowledge_system_prompt(root_dir: Path | None) -> str:
    """构建知识图谱的系统提示词。合并用户级和项目级图谱,只在有数据时返回。"""
    from .tools import (
        kg_add_entity, kg_add_relation, kg_add_alias,
        kg_get_entity, kg_search, kg_neighbors, kg_path, kg_extract, kg_export,
    )

    # 检查两个层级是否有非空图谱
    scopes: list[Scope | Path] = [Scope.USER]
    if root_dir is not None:
        scopes.append(root_dir)

    has_data = False
    for scope in scopes:
        db_path = _resolve_db_path(scope)
        if not db_path.exists():
            continue
        from .graph import KnowledgeGraph

        graph = KnowledgeGraph(db_path)
        try:
            if graph.get_stats()["entities"] > 0:
                has_data = True
                break
        finally:
            graph.close()

    if not has_data:
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
        f"- {kg_export.name}: 导出到文件(根据后缀选格式: .json/.md/.html)",
    ])
    return f"# 知识图谱\n你有知识图谱可用。\n{tools}"


def _resolve_db_path(scope: Scope | Path) -> Path:
    """解析知识图谱 DB 路径。"""
    from uniclaw.context import get_app_dir

    return get_app_dir(scope) / "knowledge.db"
