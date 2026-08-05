"""RAG 上下文注入 — 将 RAG 集合信息注入系统 prompt。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from uniclaw.context import Scope

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


def get_rag_system_prompt(config: AppConfig | None = None) -> str:
    """构建 RAG 的系统提示词。分层级列出可用 collection 列表。

    Args:
        config: 应用配置实例。为 None 时跳过 RAG。

    Returns:
        RAG 系统提示词,无数据或未配置 embedding_model 时返回空字符串。
    """
    if config is None:
        return ""

    # 未配置 embedding_model 时直接跳过
    if not config.embedding_model:
        return ""

    from .rag import RAGManager

    user_collections: list[dict] = []
    project_collections: list[dict] = []

    # 用户级
    try:
        manager = RAGManager(config, Scope.USER)
        for c in manager.list_collections():
            if c["count"] > 0:
                user_collections.append(c)
    except Exception:
        pass

    # 项目级
    root_dir = config.root_dir
    if root_dir is not None:
        try:
            manager = RAGManager(config, Scope.PROJECT)
            for c in manager.list_collections():
                if c["count"] > 0:
                    project_collections.append(c)
        except Exception:
            pass

    if not user_collections and not project_collections:
        return ""

    lines = ["# RAG 文档检索"]

    if project_collections:
        lines.append("项目级集合:")
        for c in project_collections:
            desc = f" — {c['description']}" if c.get("description") else ""
            lines.append(f"  - {c['name']} ({c['count']} 块){desc}")
    if user_collections:
        lines.append("用户级集合:")
        for c in user_collections:
            desc = f" — {c['description']}" if c.get("description") else ""
            lines.append(f"  - {c['name']} ({c['count']} 块){desc}")

    lines.append("使用 rag_search 时必须指定 collection 参数,会自动搜索两个层级。")

    return "\n".join(lines)
