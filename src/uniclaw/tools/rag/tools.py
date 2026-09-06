"""RAG 工具 — 文档导入、语义检索和集合管理。"""

from __future__ import annotations

import re
from pathlib import Path

from uniclaw.config import AppConfig
from uniclaw.context import Scope
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR

from .loader import load_directory, load_file
from .rag import RAGManager
from .splitter import split_documents

# collection 名称规则: 仅小写字母、数字、连字符和下划线
_COLLECTION_NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def validate_collection_name(collection: str) -> str | None:
    """校验 collection 名称,合法返回 None,非法返回错误信息。

    Args:
        collection: 集合名称

    Returns:
        str | None: 错误描述,名称合法时为 None
    """
    if not collection:
        return "collection 名称不能为空"
    if len(collection) > 64:
        return "collection 名称过长(最多 64 字符)"
    if not _COLLECTION_NAME_RE.match(collection):
        return (
            f"collection 名称 '{collection}' 非法,仅允许小写字母、数字、"
            "连字符和下划线,不支持中文"
        )
    return None


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
    if chunk_size > 32000:
        raise ValueError(
            f"chunk_size 不能超过 32000,当前值: {chunk_size}。"
            "过大的 chunk_size 会导致生成的块超过 embedding 模型最大输入长度"
        )
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap 不能为负数")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须小于 chunk_size")


_manager_cache: dict[tuple, RAGManager] = {}
_MANAGER_CACHE_MAX = 32  # 缓存上限,防止长时间运行时内存泄漏


def _get_manager(config: AppConfig, scope: Scope) -> RAGManager:
    """获取 RAGManager 实例(带缓存,LRU 淘汰)。"""
    key = (config.current_agent.session.id, scope)
    if key not in _manager_cache:
        # 超出上限时淘汰最早的条目
        if len(_manager_cache) >= _MANAGER_CACHE_MAX:
            evict_key = next(iter(_manager_cache))
            _manager_cache.pop(evict_key)
        _manager_cache[key] = RAGManager(config, scope)
    return _manager_cache[key]


def _sort_key(x: dict) -> float:
    """检索结果排序键:优先 rerank_score,其次 rrf_score,再次余弦相似度。"""
    if "rerank_score" in x:
        return x["rerank_score"]
    if "rrf_score" in x:
        return x["rrf_score"]
    if "cosine_similarity" in x:
        return x["cosine_similarity"]
    if "distance" in x:
        return 1 - x["distance"]
    return -1  # 没有分数的排在最后


@tool
async def rag_ingest(
    path: str,
    collection: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200, 
    scope: Scope = Scope.PROJECT,
    contextual: bool = False,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    读取文件或目录,递归拆分为文档块,生成 embedding 后存入向量数据库。

    Args:
        path: 文件或目录路径。支持 txt/md/py/json/yaml/csv/html/pdf 等格式。
        collection: 目标集合名称(必填),用于隔离不同来源的文档。名称应与文档内容相关,便于后续检索时识别。仅允许小写字母、数字、连字符和下划线,不支持中文。
        chunk_size: 每个文档块的最大 token 数。默认为 1000。
        chunk_overlap: 相邻文档块的重叠 token 数。默认为 200。
        scope: 存储级别,Scope.PROJECT(项目级) 或 Scope.USER(用户级)。
        contextual: 是否启用 Contextual Retrieval — 用 mini 模型为每个块生成情境上下文,
            拼接到块内容前再入库。能显著提升指代性/缩写类内容的召回率,但会增加导入耗时。
            默认为 False。

    Returns:
        str: 处理结果摘要,包含文件数、块数和集合信息。
    """
    config = tool_runtime.config
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    try:
        # 校验 collection 名称
        err = validate_collection_name(collection)
        if err:
            return f"{TOOL_ERROR}: {err}"

        # 验证参数
        _validate_chunk_params(chunk_size, chunk_overlap)

        # 验证路径
        target = Path(path)
        if not target.exists():
            return f"{TOOL_ERROR}: 路径不存在: {path}"

        # 加载文档
        await tool_runtime.stream(f"📂 正在加载: {path}\n")
        skipped = 0
        if target.is_file():
            docs = load_file(target)
        else:
            docs, skipped = load_directory(target)

        if not docs:
            msg = f"{TOOL_ERROR}: 未在 {path} 中找到可读取的文档"
            if skipped:
                msg += f"({skipped} 个文件读取失败已跳过)"
            return msg
        await tool_runtime.stream(f"✅ 加载完成,共 {len(docs)} 个文档\n")
        if skipped:
            await tool_runtime.stream(f"⚠️  {skipped} 个文件读取失败已跳过\n")

        # 拆分文档
        await tool_runtime.stream("✂️  正在拆分文档...\n")
        chunks = split_documents(docs, chunk_size, chunk_overlap)
        if not chunks:
            return f"{TOOL_ERROR}: 文档拆分后为空"
        await tool_runtime.stream(f"✅ 拆分完成,共 {len(chunks)} 个文档块\n")

        # 存入向量数据库
        manager = _get_manager(config, scope)

        # 增量导入: 按内容哈希跳过未变更的块,只重灌变更的部分
        sources = {
            doc.metadata.get("source") for doc in docs if doc.metadata.get("source")
        }
        existing_hashes: set[str] = set()
        for source in sources:
            existing_hashes |= manager.get_existing_hashes(collection, source)

        hashes = await manager.compute_chunk_hashes(
            chunks,
            lambda cur, tot: tool_runtime.stream(f"\r🔢 计算哈希: {cur}/{tot}"),
        )
        new_chunks = []
        new_hashes = []
        for chunk, h in zip(chunks, hashes):
            if h in existing_hashes:
                continue  # 内容未变更,跳过
            new_chunks.append(chunk)
            new_hashes.append(h)

        # 已存在于集合但本次不再出现的旧块需要清除(文件被修改或删除的场景)
        stale_hashes = existing_hashes - set(hashes)
        # 仅当存在同源旧块且内容有变化时才需要清理;全新文档无需删除
        deleted = 0
        if stale_hashes:
            deleted = manager.delete_by_hashes(collection, stale_hashes)

        skipped_count = len(chunks) - len(new_chunks)
        if skipped_count:
            await tool_runtime.stream(
                f"⏭️  {skipped_count} 个块内容未变更,跳过\n"
            )
        if not new_chunks:
            info = manager.get_collection_info(collection)
            total = info["count"] if info else "原"
            msg = (
                f"全部 {len(chunks)} 个块内容未变更,无需重新导入。"
                f"集合 '{collection}' 保持 {total} 个文档块。"
            )
            await tool_runtime.stream(f"✅ {msg}\n")
            return msg
        if deleted:
            await tool_runtime.stream(f"🧹 已清除 {deleted} 个旧文档块\n")

        # Contextual Retrieval: 为新块生成情境上下文(哈希已在原始内容上计算,不影响增量判断)
        if contextual and new_chunks:
            await tool_runtime.stream(
                f"🧠 正在为 {len(new_chunks)} 个新块生成情境上下文...\n"
            )
            try:

                async def _ctx_progress(done: int, total: int) -> None:
                    await tool_runtime.stream(f"\r⏳ 上下文生成: {done}/{total}")

                contexts = await manager.generate_chunk_contexts(
                    new_chunks, progress_callback=_ctx_progress
                )
                for chunk, ctx in zip(new_chunks, contexts):
                    if ctx:
                        chunk.content = f"{ctx}\n\n{chunk.content}"
                await tool_runtime.stream("\n")
            except Exception as e:
                await tool_runtime.stream(f"\n⚠️ 上下文生成失败,按原始内容入库: {e}\n")

        await tool_runtime.stream(
            f"⚙️  正在生成 embedding 并入库(共 {len(new_chunks)} 个新块)...\n"
        )

        async def _progress(done: int, total: int) -> None:
            pct = done * 100 // total if total else 0
            await tool_runtime.stream(f"\r⏳ 进度: {done}/{total} ({pct}%)")

        # 将 content_hash 写入 metadata,供下次增量导入比对
        for chunk, h in zip(new_chunks, new_hashes):
            chunk.metadata["content_hash"] = h

        count = await manager.ingest(collection, new_chunks, progress_callback=_progress)
        await tool_runtime.stream("\n")

        info = manager.get_collection_info(collection)
        total = info["count"] if info else count

        msg = f"成功导入 {len(docs)} 个文档,新增 {count} 个块,存入集合 '{collection}'。"
        if skipped_count:
            msg = f"{skipped_count} 个未变更块已跳过。" + msg
        if deleted:
            msg = f"已清除 {deleted} 个旧文档块。" + msg
        msg += f"集合当前共 {total} 个文档块。"
        await tool_runtime.stream(f"✅ {msg}\n")
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
    intent: str = "",
    where: dict | None = None,
    tool_runtime: ToolRuntime = None,
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
        intent: 搜索意图描述,描述当前想要搜索什么样的数据,供 LLM 重排序时判断相关性参考。
            传入非空 intent 时自动启用重排序(即使 rerank=False),避免意图描述被忽略。
            可为空字符串。
        where: ChromaDB where 条件,用于元数据过滤。
            可用字段: source(完整路径), filename(文件名), suffix(扩展名), chunk_index(块索引), created_at(导入时间)。
            示例: {"filename": "a.txt"}、{"suffix": {"$in": [".md", ".txt"]}}、
            {"$and": [{"chunk_index": {"$gt": 100}}, {"suffix": ".md"}]}。
            注意: source 是文件完整路径,按文件名过滤请用 filename 字段。
            向量与 BM25 两路同时生效。默认 None 表示不过滤。

    Returns:
        str: 格式化的检索结果,包含相关文档块内容、来源和相似度分数。
    """
    config = tool_runtime.config
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    if not query or not query.strip():
        return f"{TOOL_ERROR}: 查询文本不能为空"
    if top_k < 1:
        return f"{TOOL_ERROR}: top_k 必须大于等于 1,当前值: {top_k}"
    if top_k > 100:
        return f"{TOOL_ERROR}: top_k 不能超过 100,当前值: {top_k}"

    try:
        err = validate_collection_name(collection)
        if err:
            return f"{TOOL_ERROR}: {err}"

        all_results = []
        seen_keys: set[str] = set()
        errors = []
        collection_exists = False

        await tool_runtime.stream(f"🔍 正在搜索集合 '{collection}'...\n")

        # 搜索两个层级,search() 内部已处理集合不存在/为空的情况
        for scope in [Scope.PROJECT, Scope.USER]:
            try:
                manager = _get_manager(config, scope)
                # 检查集合是否存在
                info = manager.get_collection_info(collection)
                if info:
                    collection_exists = True
                results = await manager.search(
                    collection,
                    query,
                    top_k,
                    rerank,
                    use_bm25,
                    intent,
                    where=where,
                )
                for r in results:
                    # 按 (内容, 来源) 去重,避免同内容不同来源的块被合并
                    dedup_key = f"{r['content']}|{r.get('metadata', {}).get('source', '')}"
                    if dedup_key not in seen_keys:
                        seen_keys.add(dedup_key)
                        all_results.append(r)
            except Exception as e:
                errors.append(f"{scope.value}: {e}")
                continue

        if not all_results:
            if not collection_exists:
                return f"{TOOL_ERROR}: 集合 '{collection}' 不存在。请先使用 {rag_ingest.name} 导入文档创建集合。"
            msg = f"{TOOL_ERROR}: 在集合 '{collection}' 中未找到与查询相关的结果"
            if errors:
                msg += f"\n搜索过程中出现错误:\n" + "\n".join(
                    f"  - {e}" for e in errors
                )
            return msg

        # 按分数排序取 top_k
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
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    列出向量数据库中的所有集合及其文档数量。
    自动汇总项目级和用户级两个层级的集合。

    Returns:
        str: 集合列表及其统计信息。
    """
    config = tool_runtime.config
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
    tool_runtime: ToolRuntime = None,
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
    config = tool_runtime.config
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    if not description or not description.strip():
        return f"{TOOL_ERROR}: 描述内容不能为空"

    try:
        err = validate_collection_name(collection)
        if err:
            return f"{TOOL_ERROR}: {err}"

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
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    删除指定的向量数据库集合。

    Args:
        collection: 要删除的集合名称(必填)。仅允许小写字母、数字、连字符和下划线,不支持中文。
        scope: 存储级别,Scope.PROJECT(项目级) 或 Scope.USER(用户级)。

    Returns:
        str: 删除结果。
    """
    config = tool_runtime.config
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"

    try:
        err = validate_collection_name(collection)
        if err:
            return f"{TOOL_ERROR}: {err}"

        manager = _get_manager(config, scope)
        if manager.delete_collection(collection):
            return f"已删除集合 '{collection}'"
        return f"{TOOL_ERROR}: 集合 '{collection}' 不存在或删除失败"
    except Exception as e:
        return f"{TOOL_ERROR}: 删除集合失败: {e}"


async def _judge_relevance(
    query: str, results: list[dict], config: AppConfig
) -> list[float]:
    """用 LLM 判断检索结果与查询的相关性(LLM judge,无需人工标注)。

    对应 RAGAS 的 Context Relevancy 思路:批量判断每个 chunk 能否回答查询。

    Args:
        query: 查询文本。
        results: 检索结果列表。
        config: 应用配置。

    Returns:
        list[float]: 每个结果的 LLM 相关度分数(0-1),调用失败时全为 0。
    """
    if not results:
        return []

    from uniclaw.provider.fallback import achat
    from uniclaw.tools.session.session import Session

    docs_text = ""
    for i, c in enumerate(results):
        content = c["content"]
        chunk_idx = c.get("metadata", {}).get("chunk_index")
        idx_tag = f" (chunk_index={chunk_idx})" if chunk_idx is not None else ""
        docs_text += f"[{i}]{idx_tag} {content}\n\n"

    system_prompt = (
        "你是一个文档相关性判断助手。根据查询与每个文档的相关性给出分数。"
        "分数范围 0-100: 0 表示完全无关,100 表示完全相关且能直接回答查询问题。"
        "请充分利用整个分数区间,避免只给 0 或 100 的极端分数。"
        '只返回一个 JSON 对象,格式: {"judgments": [{"index": 序号, "score": 分数}, ...]},'
        "不要返回其他内容。"
    )
    user_message = f"查询: {query}\n\n候选文档:\n{docs_text}\n请为每个文档打分。"

    session = Session()
    session.add_user_message(content=user_message)

    try:
        from uniclaw.utils.format import parse_json_from_llm

        resp = await achat(
            system_prompt,
            session,
            model_name=config.mini_model_name,
            enable_thinking=False,
            thinking=False,
            config=config,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        result = parse_json_from_llm(resp.content)
        judgments = result.get("judgments", []) if result else []
        scores = [0.0] * len(results)
        # 只接受 {"index": 序号, "score": 分数} 字典格式,其余视为错误
        for j in judgments:
            if not isinstance(j, dict):
                return [0.0] * len(results)
            try:
                idx = int(j.get("index"))
            except (TypeError, ValueError):
                return [0.0] * len(results)
            if 0 <= idx < len(scores):
                scores[idx] = max(0.0, min(1.0, float(j.get("score", 0)) / 100))
        return scores
    except Exception:
        return [0.0] * len(results)


@tool
async def rag_evaluate(
    collection: str,
    queries: list[str],
    top_k: int = 5,
    rerank: bool = True,
    use_bm25: bool = True,
    use_llm_judge: bool = False,
    where: dict | None = None,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    评估 RAG 检索效果:对一组测试问题执行检索,输出 LLM Judge 相关性指标。

    设置 use_llm_judge=True 时,会用 LLM 评估每个检索结果与查询的相关性,
    无需人工标注。输出 LLM Judge 平均分和 Context Precision@k。

    Args:
        collection: 要评估的集合名称(必填)。
        queries: 测试问题列表,每个问题应能对应集合中的某个文档块。
        top_k: 每个问题返回的结果数。默认为 5。
        rerank: 是否启用 LLM 重排序。默认为 True。
        use_bm25: 是否启用 BM25 关键词检索实现多路召回。默认为 True。
        use_llm_judge: 是否启用 LLM judge 评估检索相关性。默认为 False。
            启用后计算平均相关性分数和 Context Precision@k,无需人工标注。
        where: ChromaDB where 条件,用于元数据过滤。可用字段: filename, suffix, chunk_index 等。
            示例: {"filename": "a.txt"}、{"suffix": {"$in": [".md", ".txt"]}}。默认 None 表示不过滤。

    Returns:
        str: 评估报告,包含总指标和每个问题的检索明细。
    """
    config = tool_runtime.config
    if not config:
        return f"{TOOL_ERROR}: 无法获取配置"
    if not queries:
        return f"{TOOL_ERROR}: queries 不能为空"
    if top_k <= 0:
        return f"{TOOL_ERROR}: top_k 必须为正整数"

    try:
        err = validate_collection_name(collection)
        if err:
            return f"{TOOL_ERROR}: {err}"

        # 为每个问题执行多层级检索,按内容去重
        all_retrieved: dict[str, list[dict]] = {}
        errors = []
        for qi, query in enumerate(queries):
            retrieved = []
            seen_keys: set[str] = set()
            for s in [Scope.PROJECT, Scope.USER]:
                try:
                    manager = _get_manager(config, s)
                    results = await manager.search(
                        collection, query, top_k, rerank, use_bm25, where=where
                    )
                    for r in results:
                        dedup_key = f"{r['content']}|{r.get('metadata', {}).get('source', '')}"
                        if dedup_key not in seen_keys:
                            seen_keys.add(dedup_key)
                            retrieved.append(r)
                except Exception as e:
                    errors.append(f"问题 {qi + 1} ({s.value}): {e}")
            # 按分数排序并截断到 top_k
            retrieved.sort(key=_sort_key, reverse=True)
            retrieved = retrieved[:top_k]
            all_retrieved[query] = retrieved

        # ── LLM judge 相关性评估 ──
        judge_scores: dict[str, list[float]] = {}
        if use_llm_judge:
            for qi, query in enumerate(queries):
                retrieved = all_retrieved[query]
                if retrieved:
                    scores = await _judge_relevance(query, retrieved, config)
                else:
                    scores = []
                judge_scores[query] = scores

        # ── 汇总指标 ──
        result_counts = []
        detail_lines = []

        for qi, query in enumerate(queries):
            retrieved = all_retrieved[query]
            result_counts.append(len(retrieved))

            # 构建每个问题的明细
            parts = [f"Q{qi + 1}. {query}"]

            if use_llm_judge and judge_scores.get(query):
                scores = judge_scores[query]
                avg_score = sum(scores) / len(scores) if scores else 0.0
                relevant = sum(1 for s in scores if s >= 0.5)
                context_precision = relevant / len(scores) if scores else 0.0
                parts.append(
                    f"    LLM Judge: 平均分 {avg_score:.3f}, "
                    f"Context Precision@{top_k}: {relevant}/{len(scores)} = {context_precision * 100:.0f}%"
                )
                # 显示每个结果的分数
                score_details = []
                for i, s in enumerate(scores):
                    tag = "✓" if s >= 0.5 else "✗"
                    score_details.append(f"[{i}]{tag}{s:.2f}")
                parts.append(f"    Chunk 分数: {' '.join(score_details)}")

            parts.append(f"    返回 {len(retrieved)} 个结果")
            detail_lines.append("\n".join(parts))

        n = len(queries)
        avg_results = sum(result_counts) / n

        # 构建报告
        lines = [
            f"评估完成:集合 '{collection}', 共 {n} 个问题, "
            f"top_k={top_k}, rerank={rerank}, BM25={use_bm25}",
            "=" * 40,
            "总指标:",
            f"  平均返回结果数:      {avg_results:.1f}",
        ]

        # 全局 LLM judge 指标
        if use_llm_judge and judge_scores:
            all_scores = [s for scores in judge_scores.values() for s in scores]
            all_precisions = []
            for query in queries:
                scores = judge_scores.get(query, [])
                if scores:
                    all_precisions.append(sum(1 for s in scores if s >= 0.5) / len(scores))
            avg_judge = sum(all_scores) / len(all_scores) if all_scores else 0.0
            avg_precision = sum(all_precisions) / len(all_precisions) if all_precisions else 0.0
            lines.append(f"  LLM Judge 平均相关度:  {avg_judge:.3f}")
            lines.append(f"  LLM Context Precision: {avg_precision * 100:.1f}%")

        if errors:
            lines.append(f"  搜索错误: {len(errors)} 个")
        if not use_llm_judge:
            lines.append("  (未启用 LLM Judge,仅统计检索结果数量)")
        if detail_lines:
            lines.append("-" * 40)
            lines.extend(detail_lines)
        if errors:
            lines.append("-" * 40)
            lines.append("错误明细:")
            lines.extend(f"  - {e}" for e in errors[:10])

        return "\n".join(lines)
    except Exception as e:
        return f"{TOOL_ERROR}: 评估失败: {e}"


def get_tools(config=None) -> list:
    """获取 RAG 工具列表。仅当配置了 embedding_model 时才返回工具。"""
    if not config or not config.embedding_model:
        if config:
            config.record_unavailable_tools(
                [rag_ingest.name, rag_search.name, rag_list_collections.name,
                 rag_set_desc.name, rag_delete_collection.name, rag_evaluate.name],
                "未配置 embedding_model,在 settings.json 中配置后可启用 RAG 工具",
            )
        return []
    if config:
        config.clear_unavailable_tools(
            [rag_ingest.name, rag_search.name, rag_list_collections.name,
             rag_set_desc.name, rag_delete_collection.name, rag_evaluate.name],
        )
    return [
        rag_ingest,
        rag_search,
        rag_list_collections,
        rag_set_desc,
        rag_delete_collection,
        rag_evaluate,
    ]


def get_all_tools() -> list:
    """获取所有 RAG 工具。"""
    return [
        rag_ingest,
        rag_search,
        rag_list_collections,
        rag_set_desc,
        rag_delete_collection,
        rag_evaluate,
    ]
