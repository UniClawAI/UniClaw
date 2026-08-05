"""RAG 工具 — 文档导入、语义检索和集合管理。"""

from __future__ import annotations

from pathlib import Path

from uniclaw.config import AppConfig
from uniclaw.context import Scope
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR

from .loader import load_directory, load_file
from .rag import RAGManager
from .splitter import split_documents


def _validate_chunk_params(chunk_size: int, chunk_overlap: int) -> None:
    """验证文档拆分参数。

    Args:
        chunk_size: 每个文档块的最大 token 数。
        chunk_overlap: 相邻文档块的重叠 token 数。

    Raises:
        ValueError: 参数无效。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须为正整数")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap 不能为负数")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须小于 chunk_size")


_manager_cache: dict[tuple, RAGManager] = {}


def _get_manager(config: AppConfig, scope: Scope) -> RAGManager:
    """获取 RAGManager 实例(带缓存)。"""
    key = (config.current_agent.session.id, scope)
    if key not in _manager_cache:
        _manager_cache[key] = RAGManager(config, scope)
    return _manager_cache[key]


@tool
async def rag_ingest(
    path: str,
    collection: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    scope: Scope = Scope.PROJECT,
    config: AppConfig = None,
) -> str:
    """
    读取文件或目录,递归拆分为文档块,生成 embedding 后存入向量数据库。

    Args:
        path: 文件或目录路径。支持 txt/md/py/json/yaml/csv/html/pdf 等格式。
        collection: 目标集合名称(必填),用于隔离不同来源的文档。名称应与文档内容相关,便于后续检索时识别。仅允许小写字母、数字、连字符和下划线,不支持中文。
        chunk_size: 每个文档块的最大 token 数。默认为 1000。
        chunk_overlap: 相邻文档块的重叠 token 数。默认为 200。
        scope: 存储级别,Scope.PROJECT(项目级) 或 Scope.USER(用户级)。

    Returns:
        str: 处理结果摘要,包含文件数、块数和集合信息。
    """
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    try:
        # 验证参数
        _validate_chunk_params(chunk_size, chunk_overlap)

        # 验证路径
        target = Path(path)
        if not target.exists():
            return f"{TOOL_ERROR}: 路径不存在: {path}"

        # 加载文档
        if target.is_file():
            docs = load_file(target)
        else:
            docs = load_directory(target)

        if not docs:
            return f"{TOOL_ERROR}: 未在 {path} 中找到可读取的文档"

        # 拆分文档
        chunks = split_documents(docs, chunk_size, chunk_overlap)
        if not chunks:
            return f"{TOOL_ERROR}: 文档拆分后为空"

        # 存入向量数据库
        manager = _get_manager(config, scope)

        # 导入前先删除同源文档,避免重复
        sources = {
            doc.metadata.get("source") for doc in docs if doc.metadata.get("source")
        }
        deleted = 0
        for source in sources:
            deleted += manager.delete_by_source(collection, source)

        count = await manager.ingest(collection, chunks)

        info = manager.get_collection_info(collection)
        total = info["count"] if info else count

        msg = (
            f"成功导入 {len(docs)} 个文档,拆分为 {count} 个块,存入集合 '{collection}'。"
        )
        if deleted:
            msg = f"已清除 {deleted} 个旧文档块。" + msg
        msg += f"集合当前共 {total} 个文档块。"
        return msg
    except ValueError as e:
        return f"{TOOL_ERROR}: {e}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def rag_search(
    query: str,
    collection: str,
    top_k: int = 10,
    min_score: float = 0.1,
    rerank: bool = True,
    use_bm25: bool = True,
    config: AppConfig = None,
) -> str:
    """
    在向量数据库中多路召回检索与查询最相关的文档块。
    同时执行向量语义检索和 BM25 关键词检索,使用 RRF(Reciprocal Rank Fusion)合并结果,支持 LLM 重排序。
    自动搜索项目级和用户级两个层级的同名集合并合并结果。

    Args:
        query: 检索查询文本。
        collection: 要搜索的集合名称(必填)。仅允许小写字母、数字、连字符和下划线,不支持中文。
        top_k: 返回的结果数量。默认为 10。
        min_score: 最低相关性分数,低于此值的结果将被过滤。默认为 0.1。
        rerank: 是否启用 LLM 重排序。默认为 True。
        use_bm25: 是否启用 BM25 关键词检索实现多路召回。默认为 True。关闭后仅使用向量语义检索。

    Returns:
        str: 格式化的检索结果,包含相关文档块内容、来源和相似度分数。
    """
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    try:
        all_results = []
        seen_contents = set()
        errors = []

        # 搜索两个层级,search() 内部已处理集合不存在/为空的情况
        for scope in [Scope.PROJECT, Scope.USER]:
            try:
                manager = _get_manager(config, scope)
                results = await manager.search(
                    collection, query, top_k, rerank, use_bm25
                )
                for r in results:
                    # 按内容去重
                    if r["content"] not in seen_contents:
                        seen_contents.add(r["content"])
                        all_results.append(r)
            except Exception as e:
                errors.append(f"{scope.value}: {e}")
                continue

        if not all_results:
            msg = f"{TOOL_ERROR}: 在集合 '{collection}' 中未找到与查询相关的结果"
            if errors:
                msg += f"\n搜索过程中出现错误:\n" + "\n".join(
                    f"  - {e}" for e in errors
                )
            return msg

        # 按分数排序取 top_k (优先使用 rerank_score, 其次 rrf_score, 最后余弦相似度)
        def _sort_key(x: dict) -> float:
            if "rerank_score" in x:
                return x["rerank_score"]
            if "rrf_score" in x:
                return x["rrf_score"]
            if "cosine_similarity" in x:
                return x["cosine_similarity"]
            if "distance" in x:
                return 1 - x["distance"]
            return -1  # 没有分数的排在最后

        all_results.sort(key=_sort_key, reverse=True)
        all_results = [r for r in all_results if _sort_key(r) >= min_score][:top_k]

        lines = [f"在集合 '{collection}' 中找到 {len(all_results)} 个相关结果:\n"]

        for i, r in enumerate(all_results, 1):
            source = r["metadata"].get("source", "未知")
            filename = r["metadata"].get("filename", "")

            # 构建分数信息
            score_parts = []
            if "rerank_score" in r:
                score_parts.append(f"重排序: {r['rerank_score']:.3g}")
            if r.get("llm_score", 0) > 0:
                score_parts.append(f"LLM: {r['llm_score']:.3g}")
            if "cosine_similarity" in r:
                score_parts.append(f"向量相似度: {r['cosine_similarity']:.3g}")
            elif "distance" in r:
                score_parts.append(f"向量相似度: {1 - r['distance']:.3g}")
            if "bm25_score" in r:
                score_parts.append(f"BM25: {r['bm25_score']:.3g}")
            if "rrf_score" in r:
                score_parts.append(f"RRF: {r['rrf_score']:.3g}")
            channels = r.get("retrieval_channels", [])
            if channels:
                score_parts.append(f"召回: {'+'.join(channels)}")
            score_str = " | ".join(score_parts) if score_parts else ""

            lines.append(f"--- 结果 {i} ({score_str}) ---")
            lines.append(f"来源: {filename or source}")
            if "chunk_index" in r["metadata"]:
                lines.append(f"块索引: {r['metadata']['chunk_index']}")
            if "page" in r["metadata"]:
                lines.append(f"页码: {r['metadata']['page']}")
            if "created_at" in r["metadata"]:
                lines.append(f"导入时间: {r['metadata']['created_at']}")
            lines.append(f"内容:\n{r['content']}\n")

        return "\n".join(lines)
    except Exception as e:
        return f"{TOOL_ERROR}: 检索失败: {e}"


@tool
def rag_list_collections(
    config: AppConfig = None,
) -> str:
    """
    列出向量数据库中的所有集合及其文档数量。
    自动汇总项目级和用户级两个层级的集合。

    Returns:
        str: 集合列表及其统计信息。
    """
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    try:
        has_any = False
        lines = []

        for scope, label in [(Scope.PROJECT, "project"), (Scope.USER, "user")]:
            try:
                manager = _get_manager(config, scope)
                collections = manager.list_collections()
            except Exception:
                continue

            if not collections:
                continue

            has_any = True
            lines.append(f"[{label}]")
            for c in collections:
                created = f" (创建于 {c['created_at']})" if c.get("created_at") else ""
                desc = f"\n    描述: {c['description']}" if c.get("description") else ""
                lines.append(f"  - {c['name']}: {c['count']} 个文档块{created}{desc}")

        if not has_any:
            return f"{TOOL_ERROR}: 当前没有任何集合"

        return "\n".join(lines)
    except Exception as e:
        return f"{TOOL_ERROR}: 列出集合失败: {e}"


@tool
def rag_set_desc(
    collection: str,
    description: str,
    scope: Scope = Scope.PROJECT,
    config: AppConfig = None,
) -> str:
    """
    设置或更新向量数据库集合的描述信息。
    描述会显示在系统提示词中,帮助 LLM 了解集合包含什么类型的数据、什么情况下应该搜索该集合。

    Args:
        collection: 集合名称(必填)。
        description: 集合描述,简要说明集合内容和适用场景。
        scope: 存储级别,Scope.PROJECT(项目级) 或 Scope.USER(用户级)。

    Returns:
        str: 设置结果。
    """
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    try:
        manager = _get_manager(config, scope)
        if manager.set_collection_desc(collection, description):
            return f"已更新集合 '{collection}' 的描述: {description}"
        return f"{TOOL_ERROR}: 集合 '{collection}' 不存在或设置失败"
    except Exception as e:
        return f"{TOOL_ERROR}: 设置描述失败: {e}"


@tool
def rag_delete_collection(
    collection: str,
    scope: Scope = Scope.PROJECT,
    config: AppConfig = None,
) -> str:
    """
    删除指定的向量数据库集合。

    Args:
        collection: 要删除的集合名称(必填)。仅允许小写字母、数字、连字符和下划线,不支持中文。
        scope: 存储级别,Scope.PROJECT(项目级) 或 Scope.USER(用户级)。

    Returns:
        str: 删除结果。
    """
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    try:
        manager = _get_manager(config, scope)
        if manager.delete_collection(collection):
            return f"已删除集合 '{collection}'"
        return f"{TOOL_ERROR}: 集合 '{collection}' 不存在或删除失败"
    except Exception as e:
        return f"{TOOL_ERROR}: 删除集合失败: {e}"


def get_tools(config=None) -> list:
    """获取 RAG 工具列表。仅当配置了 embedding_model 时才返回工具。"""
    if not config or not config.embedding_model:
        return []
    return [
        rag_ingest,
        rag_search,
        rag_list_collections,
        rag_set_desc,
        rag_delete_collection,
    ]


def get_all_tools() -> list:
    """获取所有 RAG 工具。"""
    return [
        rag_ingest,
        rag_search,
        rag_list_collections,
        rag_set_desc,
        rag_delete_collection,
    ]
