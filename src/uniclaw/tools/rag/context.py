"""RAG 上下文注入 — 将 RAG 集合信息注入系统 prompt。"""

from __future__ import annotations

from pathlib import Path

from uniclaw.context import Scope


def get_rag_system_prompt(root_dir: Path | None) -> str:
    """构建 RAG 的系统提示词。分层级列出可用 collection 列表。

    Args:
        root_dir: 项目根目录,None 表示仅用户级。

    Returns:
        RAG 系统提示词,无数据或未配置 embedding_model 时返回空字符串。
    """
    from uniclaw.config import config as app_config

    # 未配置 embedding_model 时直接跳过
    if not app_config.embedding_model:
        return ""

    from .rag import RAGManager

    user_collections: list[dict] = []
    project_collections: list[dict] = []

    # 用户级
    try:
        manager = RAGManager(app_config, Scope.USER)
        for c in manager.list_collections():
            if c["count"] > 0:
                user_collections.append(c)
    except Exception:
        pass

    # 项目级
    if root_dir is not None:
        try:
            manager = RAGManager(app_config, Scope.PROJECT)
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
