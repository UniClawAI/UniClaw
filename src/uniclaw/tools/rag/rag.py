"""RAG 核心类 — 向量存储、多路检索和重排序。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rank_bm25 import BM25Okapi

from uniclaw.console.ui import err
from uniclaw.context import Scope, get_app_dir
from uniclaw.utils.tokenize import tokenize

from .splitter import Chunk

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

# ChromaDB where 条件操作符的本地匹配实现(BM25 通道过滤用)
_WHERE_OPS = {
    "$eq": lambda a, b: a == b,
    "$ne": lambda a, b: a != b,
    "$gt": lambda a, b: a > b,
    "$gte": lambda a, b: a >= b,
    "$lt": lambda a, b: a < b,
    "$lte": lambda a, b: a <= b,
    "$in": lambda a, b: a in b,
    "$nin": lambda a, b: a not in b,
}


def _match_where(metadata: dict | None, where: dict) -> bool:
    """判断 metadata 是否满足 ChromaDB 风格的 where 条件。

    支持 $eq/$ne/$gt/$gte/$lt/$lte/$in/$nin 操作符与隐式 $eq,
    以及 $and/$or 逻辑组合。BM25 通道用它在本地过滤,与向量通道
    的 collection.query(where=...) 语义保持一致。
    """
    if not where:
        return True
    for key, cond in where.items():
        value = (metadata or {}).get(key)
        if key == "$and":  # 所有子条件都满足
            if not all(_match_where(metadata, sub) for sub in cond):
                return False
        elif key == "$or":  # 任一子条件满足
            if not cond or not any(_match_where(metadata, sub) for sub in cond):
                return False
        elif key.startswith("$"):  # 顶层裸操作符,按 $eq 处理
            op = _WHERE_OPS.get(key)
            if op is None or not op(value, cond):
                return False
        elif isinstance(cond, dict):  # 显式操作符 {key: {"$gt": 1}}
            for op_name, operand in cond.items():
                op = _WHERE_OPS.get(op_name)
                if op is None or not op(value, operand):
                    return False
        elif value != cond:  # 隐式 $eq
            return False
    return True


class RAGManager:
    """管理 ChromaDB 客户端、集合和 embedding 函数。"""

    def __init__(self, config: AppConfig, scope: Scope = Scope.PROJECT):
        self.config = config
        self.scope = scope
        self._client = None
        self._embedding_fn = None
        # BM25 索引缓存: {collection_name: (BM25Okapi, doc_ids, doc_contents, doc_metadatas)}
        self._bm25_cache: dict[
            str, tuple[BM25Okapi, list[str], list[str], list[dict]]
        ] = {}

    @property
    def _rag_dir(self) -> Path:
        """获取 RAG 数据根目录(chroma_data 和 bm25_cache 的公共父目录)。"""
        if self.scope == Scope.PROJECT and self.config.root_dir:
            resolved = self.config.root_dir
        else:
            resolved = Scope.USER
        return get_app_dir(resolved) / "rag"

    @property
    def db_path(self) -> Path:
        """获取 ChromaDB 持久化路径。"""
        return self._rag_dir / "chroma_data"

    @property
    def _bm25_cache_dir(self) -> Path:
        """获取 BM25 索引缓存目录。"""
        return self._rag_dir / "bm25_cache"

    @property
    def client(self):
        """懒初始化 ChromaDB 客户端。"""
        if self._client is None:
            import chromadb

            self.db_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.db_path))
        return self._client

    def _get_embedding_fn(self):
        """获取异步 embedding 函数。"""
        if self._embedding_fn is None:
            from uniclaw.provider.common import resolve_model_provider

            model_ref = self.config.embedding_model
            if not model_ref:
                raise ValueError(
                    "未配置 embedding_model,请在 settings.json 中设置 embedding_model 字段"
                )

            profile, model_name = resolve_model_provider(self.config, model_ref)

            import openai

            if not profile:
                # 无 provider 前缀时,使用第一个可用 provider
                if self.config.providers:
                    profile = next(iter(self.config.providers.values()))
                else:
                    raise ValueError(
                        "未找到可用的 provider 配置,请在 settings.json 中配置 providers"
                    )

            client = openai.AsyncOpenAI(
                api_key=profile.api_key,
                base_url=profile.base_url,
            )

            async def embed_fn(texts: list[str]) -> list[list[float]]:
                try:
                    response = await client.embeddings.create(
                        model=model_name,
                        input=texts,
                    )
                except Exception:
                    response = await client.embeddings.create(
                        model=model_name,
                        input=texts,
                        encoding_format="float",
                    )
                return [item.embedding for item in response.data]

            self._embedding_fn = embed_fn
        return self._embedding_fn

    def get_or_create_collection(self, name: str):
        """获取或创建集合。"""
        col = self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )
        # 如果集合刚创建,记录创建时间
        # 注意: modify 是替换语义,但 hnsw:space 是索引参数(独立于 metadata),
        # 丢失不影响距离计算。且 get_or_create_collection 总在 set_collection_desc
        # 之前调用,此时 metadata 中不会有 description 等自定义字段。
        if "created_at" not in (col.metadata or {}):
            col.modify(metadata={"created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        return col

    def list_collections(self) -> list[dict]:
        """列出所有集合及其信息。"""
        collections = self.client.list_collections()
        result = []
        for col in collections:
            # 兼容不同版本 ChromaDB: list_collections() 可能返回名称列表或对象列表
            col_name = col if isinstance(col, str) else col.name
            try:
                collection = self.client.get_collection(col_name)
                count = collection.count()
                meta = collection.metadata or {}
                result.append(
                    {
                        "name": col_name,
                        "count": count,
                        "created_at": meta.get("created_at"),
                        "description": meta.get("description"),
                    }
                )
            except Exception:
                continue
        return result

    def set_collection_desc(self, name: str, description: str) -> bool:
        """设置或更新集合的描述信息。

        Args:
            name: 集合名称
            description: 集合描述(用于系统提示词,说明集合内容和使用场景)

        Returns:
            是否设置成功
        """
        try:
            collection = self.client.get_collection(name=name)
            collection.modify(metadata={**(collection.metadata or {}), "description": description})
            return True
        except Exception:
            return False

    def delete_collection(self, name: str) -> bool:
        """删除指定集合。"""
        try:
            self.client.delete_collection(name=name)
            self._invalidate_bm25(name)
            return True
        except Exception:
            return False

    def delete_by_source(self, collection_name: str, source: str) -> int:
        """删除指定来源的文档块。

        Args:
            collection_name: 集合名称
            source: 来源路径(metadata 中的 source 字段)

        Returns:
            删除的文档块数量
        """
        try:
            collection = self.client.get_collection(name=collection_name)
        except Exception:
            return 0

        # 查找匹配 source 的文档
        results = collection.get(where={"source": source})
        if not results["ids"]:
            return 0

        collection.delete(ids=results["ids"])
        self._invalidate_bm25(collection_name)
        return len(results["ids"])

    def delete_by_hashes(
        self, collection_name: str, content_hashes: set[str]
    ) -> int:
        """删除指定内容哈希的文档块(增量导入时清除已变更的旧块)。

        Args:
            collection_name: 集合名称
            content_hashes: 要删除的 content_hash 集合

        Returns:
            删除的文档块数量
        """
        if not content_hashes:
            return 0
        try:
            collection = self.client.get_collection(name=collection_name)
        except Exception:
            return 0

        deleted = 0
        for h in content_hashes:
            results = collection.get(where={"content_hash": h})
            if results["ids"]:
                collection.delete(ids=results["ids"])
                deleted += len(results["ids"])
        if deleted:
            self._invalidate_bm25(collection_name)
        return deleted

    def get_existing_hashes(self, collection_name: str, source: str) -> set[str]:
        """获取指定来源已存入集合的全部内容哈希。

        Args:
            collection_name: 集合名称
            source: 来源路径(metadata 中的 source 字段)

        Returns:
            set[str]: 该来源所有块的 content_hash;集合不存在时为空集
        """
        try:
            collection = self.client.get_collection(name=collection_name)
        except Exception:
            return set()

        results = collection.get(where={"source": source}, include=["metadatas"])
        hashes = set()
        for meta in results["metadatas"] or []:
            if meta and meta.get("content_hash"):
                hashes.add(meta["content_hash"])
        return hashes

    def get_collection_info(self, name: str) -> dict | None:
        """获取集合统计信息。"""
        try:
            collection = self.client.get_collection(name=name)
            count = collection.count()
            return {"name": name, "count": count}
        except Exception:
            return None

    async def ingest(
        self,
        collection_name: str,
        chunks: list[Chunk],
        progress_callback: Callable[[int, int], Awaitable[None] | None] | None = None,
    ) -> int:
        """将文档块 embedding 后存入集合。

        Args:
            collection_name: 集合名称
            chunks: 文档块列表(应预先过滤掉未变更的块)
            progress_callback: 可选回调(同步或异步均可),每个批次入库后调用,
                参数为 (已完成块数, 总块数),用于显示进度条。

        Returns:
            实际存入的文档块数量
        """
        collection = self.get_or_create_collection(collection_name)
        embed_fn = self._get_embedding_fn()

        if not chunks:
            return 0

        # ChromaDB 单次最多 5461 条,分批处理
        batch_size = 256
        total = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [c.content for c in batch]
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            metadatas = [{**c.metadata, "created_at": now} for c in batch]

            # 生成 ID (UUID 避免多次导入冲突)
            ids = [str(uuid.uuid4()) for _ in range(len(batch))]

            # 生成 embedding(API 调用,天然异步)与入库(同步 HNSW 操作,
            # 放入线程池避免阻塞事件循环)
            embeddings = await embed_fn(texts)
            await asyncio.to_thread(
                collection.add,
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            total += len(batch)

            if progress_callback is not None:
                result = progress_callback(total, len(chunks))
                if inspect.isawaitable(result):
                    await result

        if total:
            self._invalidate_bm25(collection_name)
        return total

    @staticmethod
    async def compute_chunk_hashes(
        chunks: list[Chunk],
        on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
    ) -> list[str]:
        """计算每个文档块的内容哈希(用于增量导入跳过未变更块)。

        Args:
            chunks: 文档块列表
            on_progress: 进度回调,签名为 (current, total),可以是同步或异步函数。

        Returns:
            list[str]: 与 chunks 一一对应的 md5 哈希
        """
        total = len(chunks)
        result: list[str] = []
        batch_size = 50

        for start in range(0, total, batch_size):
            batch = chunks[start : start + batch_size]
            batch_hashes = await asyncio.to_thread(
                lambda bs: [hashlib.md5(b.content.encode("utf-8")).hexdigest() for b in bs],
                batch,
            )
            result.extend(batch_hashes)
            if on_progress:
                done = min(start + batch_size, total)
                ret = on_progress(done, total)
                if inspect.isawaitable(ret):
                    await ret

        return result

    # Contextual Retrieval: 单次 LLM 请求批量生成的块数上限
    CONTEXT_BATCH_SIZE = 8

    async def generate_chunk_contexts(
        self,
        chunks: list[Chunk],
        progress_callback: Callable[[int, int], Awaitable[None] | None] | None = None,
    ) -> list[str]:
        """为文档块生成情境上下文(Contextual Retrieval)。

        以块所属文档的摘要为背景,用 mini 模型为每个块生成一句
        "该块在文档中的位置与作用"说明。入库时拼接到块内容前,
        补全块内省略的主题背景,提升指代性/缩写类内容在向量与
        BM25 两路的召回率。

        Args:
            chunks: 文档块列表
            progress_callback: 可选回调(同步或异步均可),参数为 (已完成块数, 总块数)。

        Returns:
            list[str]: 与 chunks 一一对应的上下文,生成失败时为空字符串。
        """
        from uniclaw.provider.fallback import achat
        from uniclaw.tools.session.session import Session
        from uniclaw.utils.format import parse_json_from_llm

        contexts = [""] * len(chunks)
        if not chunks or not self.config.mini_model_name:
            return contexts

        # 按来源分组,同一文档的块共享文档摘要作为生成背景
        groups: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            groups.setdefault(c.metadata.get("source") or "", []).append(i)

        done = 0
        for source, indices in groups.items():
            # 文档摘要: 首块开头 + 末块结尾,近似还原文档全貌
            digest = chunks[indices[0]].content[:1500]
            if len(indices) > 1:
                digest += "\n...\n" + chunks[indices[-1]].content[-500:]

            for start in range(0, len(indices), self.CONTEXT_BATCH_SIZE):
                batch = indices[start : start + self.CONTEXT_BATCH_SIZE]
                chunk_list = "\n\n".join(
                    f"[{pos}] {chunks[i].content}" for pos, i in enumerate(batch)
                )
                system_prompt = (
                    "你是文档检索预处理助手。根据文档摘要,为每个文档块写一句简短的情境说明,"
                    "交代该块讨论的具体内容及其在文档中的位置(如所属章节/主题),"
                    "补全块内省略的文档主题背景,使块脱离原文也能被准确检索。"
                    "每条说明不超过 80 字,不要复述块内容本身。"
                    '只返回一个 JSON 对象,格式: {"contexts": [{"index": 0, "context": "说明"}, ...]},'
                    "index 必须按 0, 1, 2... 顺序递增,与输入的 [序号] 一一对应,不要返回其他内容。"
                )
                user_message = (
                    f"文档摘要:\n{digest}\n\n文档块:\n{chunk_list}\n请为每个块生成情境说明。"
                )

                session = Session()
                session.add_user_message(content=user_message)
                try:
                    resp = await achat(
                        system_prompt,
                        session,
                        model_name=self.config.mini_model_name or "",
                        enable_thinking=False,
                        thinking=False,
                        config=self.config,
                        temperature=0.3,
                        response_format={"type": "json_object"},
                    )
                    result = parse_json_from_llm(resp.content)
                    items = (result or {}).get("contexts", [])
                    for pos, i in enumerate(batch):
                        if pos >= len(items):
                            break
                        item = items[pos]
                        if not isinstance(item, dict) or item.get("index") != pos:
                            continue
                        text = item.get("context", "")
                        if isinstance(text, str) and text.strip():
                            contexts[i] = text.strip()
                except Exception as e:
                    await err(f"上下文生成批次失败(source={source}, batch={batch}): {e}")

                done += len(batch)
                if progress_callback is not None:
                    cb_result = progress_callback(done, len(chunks))
                    if inspect.isawaitable(cb_result):
                        await cb_result
        return contexts

    def _build_bm25(self, collection_name: str) -> BM25Okapi | None:
        """从磁盘缓存或集合中构建 BM25 索引。

        加载顺序: 内存缓存 → 磁盘缓存 → 从 ChromaDB 重建。

        Args:
            collection_name: 集合名称

        Returns:
            BM25Okapi 实例,集合为空时返回 None
        """
        # 1. 内存缓存
        if collection_name in self._bm25_cache:
            return self._bm25_cache[collection_name][0]

        # 2. 磁盘缓存
        loaded = self._load_bm25_from_disk(collection_name)
        if loaded is not None:
            bm25, doc_ids, doc_contents, doc_metadatas = loaded
            self._bm25_cache[collection_name] = (
                bm25,
                doc_ids,
                doc_contents,
                doc_metadatas,
            )
            return bm25

        # 3. 从 ChromaDB 重建
        try:
            collection = self.client.get_collection(name=collection_name)
        except Exception:
            return None

        count = collection.count()
        if count == 0:
            return None

        # 获取所有文档
        results = collection.get(include=["documents", "metadatas"])
        doc_ids = results["ids"]
        doc_contents = results["documents"]
        doc_metadatas = results["metadatas"] or [{}] * len(doc_ids)

        # 分词构建语料库
        corpus = [tokenize(content) for content in doc_contents]
        bm25 = BM25Okapi(corpus)

        self._bm25_cache[collection_name] = (bm25, doc_ids, doc_contents, doc_metadatas)
        # 持久化到磁盘
        self._save_bm25_to_disk(collection_name)
        return bm25

    def _bm25_search(
        self, collection_name: str, query: str, top_k: int, where: dict | None = None
    ) -> list[dict]:
        """BM25 关键词检索(同步, CPU 密集型)。

        Args:
            collection_name: 集合名称
            query: 查询文本
            top_k: 返回结果数
            where: 元数据过滤条件(ChromaDB 风格),仅保留满足条件的文档块

        Returns:
            检索结果列表,格式与向量检索一致
        """
        bm25 = self._build_bm25(collection_name)
        if bm25 is None:
            return []

        cache = self._bm25_cache[collection_name]
        _, doc_ids, doc_contents, doc_metadatas = cache

        query_tokens = tokenize(query)
        scores = bm25.get_scores(query_tokens)

        # where 过滤: 不满足条件的候选即使得分高也直接排除
        if where:
            ranked_indices = sorted(
                (
                    i
                    for i in range(len(scores))
                    if scores[i] > 0 and _match_where(doc_metadatas[i], where)
                ),
                key=lambda i: scores[i],
                reverse=True,
            )[:top_k]
        else:
            # 仅保留正分候选,避免 top_k 中混入零分/负分文档
            ranked_indices = sorted(
                (i for i in range(len(scores)) if scores[i] > 0),
                key=lambda i: scores[i],
                reverse=True,
            )[:top_k]

        results = []
        for idx in ranked_indices:
            results.append(
                {
                    "content": doc_contents[idx],
                    "metadata": doc_metadatas[idx],
                    "bm25_score": float(scores[idx]),
                    "doc_id": doc_ids[idx],
                }
            )

        # 归一化到 [0, 1]
        if results:
            max_score = results[0]["bm25_score"]
            if max_score > 0:
                for r in results:
                    r["bm25_score"] /= max_score

        return results

    @staticmethod
    def _checksum(doc_ids: list[str]) -> str:
        """计算文档 ID 列表的校验和,用于检测缓存是否过期。"""
        return hashlib.md5("|".join(doc_ids).encode()).hexdigest()

    def _save_bm25_to_disk(self, collection_name: str) -> None:
        """将内存中的 BM25 缓存持久化到磁盘。"""
        if collection_name not in self._bm25_cache:
            return

        _, doc_ids, doc_contents, doc_metadatas = self._bm25_cache[collection_name]
        cache_dir = self._bm25_cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        cache_file = cache_dir / f"{collection_name}.json"
        payload = {
            "checksum": self._checksum(doc_ids),
            "doc_count": len(doc_ids),
            "doc_ids": doc_ids,
            "doc_contents": doc_contents,
            "doc_metadatas": doc_metadatas,
        }

        try:
            tmp_file = cache_file.with_suffix(".json.tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            tmp_file.replace(cache_file)
        except Exception:
            # 写入失败不影响正常使用,下次会从 ChromaDB 重建
            pass

    def _load_bm25_from_disk(
        self, collection_name: str
    ) -> tuple[BM25Okapi, list[str], list[str], list[dict]] | None:
        """从磁盘加载 BM25 缓存(JSON 格式),校验失败返回 None。"""
        cache_file = self._bm25_cache_dir / f"{collection_name}.json"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                payload = json.load(f)

            # 校验:从 ChromaDB 获取当前文档 ID,比对 checksum
            try:
                collection = self.client.get_collection(name=collection_name)
                current_ids = collection.get(include=[])["ids"]
            except Exception:
                return None

            if self._checksum(current_ids) != payload.get("checksum"):
                # 文档已变更,缓存失效
                cache_file.unlink(missing_ok=True)
                return None

            doc_ids = payload["doc_ids"]
            doc_contents = payload["doc_contents"]
            doc_metadatas = payload["doc_metadatas"]

            # 从 corpus 重建 BM25Okapi(不序列化 BM25 对象,避免 pickle 安全风险)
            corpus = [tokenize(content) for content in doc_contents]
            bm25 = BM25Okapi(corpus)

            return (bm25, doc_ids, doc_contents, doc_metadatas)
        except Exception:
            # 缓存文件损坏,删除
            cache_file.unlink(missing_ok=True)
            return None

    def _invalidate_bm25(self, collection_name: str) -> None:
        """清除指定集合的 BM25 缓存(内存 + 磁盘)。"""
        self._bm25_cache.pop(collection_name, None)
        cache_dir = self._bm25_cache_dir
        # 清理 JSON 缓存(当前格式)
        (cache_dir / f"{collection_name}.json").unlink(missing_ok=True)
        # 清理旧 pickle 缓存(向后兼容)
        (cache_dir / f"{collection_name}.pkl").unlink(missing_ok=True)

    @staticmethod
    def _rrf_merge(
        vector_results: list[dict],
        bm25_results: list[dict],
        top_k: int,
        k: int = 60,
    ) -> list[dict]:
        """使用 Reciprocal Rank Fusion 合并多路检索结果。

        RRF 公式: score(d) = sum(1 / (k + rank_i(d)))
        优点: 无需对不同检索器的分数做归一化。
        合并后将 rrf_score 归一化到 [0, 1](双路第一名的理论最大值 2/k),
        使其可与 min_score 阈值直接比较。

        Args:
            vector_results: 向量检索结果
            bm25_results: BM25 检索结果
            top_k: 返回结果数
            k: RRF 常数,默认 60

        Returns:
            合并后的结果列表
        """
        # 用 (内容, 来源) 做 key 去重,避免同内容不同来源的块被合并
        def _dedup_key(r: dict) -> str:
            return f"{r['content']}|{r.get('metadata', {}).get('source', '')}"

        merged: dict[str, dict] = {}

        for rank, r in enumerate(vector_results):
            key = _dedup_key(r)
            if key not in merged:
                merged[key] = {**r, "rrf_score": 0.0, "retrieval_channels": []}
            merged[key]["rrf_score"] += 1.0 / (k + rank)
            merged[key]["retrieval_channels"].append("vector")
            # 保留向量检索的 distance 信息
            if "distance" in r:
                merged[key]["distance"] = r["distance"]

        for rank, r in enumerate(bm25_results):
            key = _dedup_key(r)
            if key not in merged:
                merged[key] = {**r, "rrf_score": 0.0, "retrieval_channels": []}
            merged[key]["rrf_score"] += 1.0 / (k + rank)
            merged[key]["retrieval_channels"].append("bm25")
            # 保留 BM25 分数
            if "bm25_score" in r:
                merged[key]["bm25_score"] = r["bm25_score"]

        # 按 RRF 分数降序排列
        results = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
        results = results[:top_k]

        # 归一化到 [0, 1]: 按实际最高分归一化,使 top-1 始终为 1.0
        if results:
            max_rrf = results[0]["rrf_score"]
            if max_rrf > 0:
                for r in results:
                    r["rrf_score"] /= max_rrf
        return results

    async def search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 5,
        rerank: bool = True,
        use_bm25: bool = True,
        intent: str = "",
        where: dict | None = None,
        status_callback: Any | None = None,
    ) -> list[dict]:
        """多路召回检索 + 重排序。

        同时执行向量语义检索和 BM25 关键词检索,使用 RRF 合并结果。

        Args:
            collection_name: 集合名称
            query: 查询文本
            top_k: 返回结果数
            rerank: 是否启用重排序
            use_bm25: 是否启用 BM25 多路召回。默认为 True。
            intent: 搜索意图描述,描述当前想要搜索什么样的数据,供 LLM 重排序时
                判断相关性参考。传入非空 intent 时自动启用重排序(即使 rerank=False),
                避免意图描述被忽略。可为空字符串。
            where: 元数据过滤条件(ChromaDB where 语法),如 {"source": "a.txt"} 或
                {"suffix": {"$in": [".md", ".txt"]}}。仅满足条件的文档块参与检索,
                向量与 BM25 两路同时生效。默认 None 表示不过滤。
            status_callback: 状态回调(如 ToolRuntime.stream),rerank 前推送进度提示。

        Returns:
            检索结果列表
        """
        try:
            collection = self.client.get_collection(name=collection_name)
        except Exception:
            return []

        if collection.count() == 0:
            return []

        # 候选取 3 倍,保证合并后有足够的候选
        n_candidates = min(top_k * 3, collection.count())

        async def _vector_search() -> list[dict]:
            """向量语义检索。"""
            embed_fn = self._get_embedding_fn()
            query_embedding = (await embed_fn([query]))[0]

            # ChromaDB 查询是同步 HNSW 操作,放入线程池避免阻塞事件循环
            # where 过滤由 ChromaDB 原生处理,空条件不过滤
            results = await asyncio.to_thread(
                collection.query,
                query_embeddings=[query_embedding],
                n_results=n_candidates,
                include=["documents", "metadatas", "distances"],
                **({"where": where} if where else {}),
            )

            if not results["documents"] or not results["documents"][0]:
                return []

            candidates = []
            for i in range(len(results["documents"][0])):
                candidates.append(
                    {
                        "content": results["documents"][0][i],
                        "metadata": (
                            results["metadatas"][0][i] if results["metadatas"] else {}
                        ),
                        "distance": (
                            results["distances"][0][i] if results["distances"] else 0
                        ),
                    }
                )
            return candidates

        async def _bm25_search_async() -> list[dict]:
            """BM25 关键词检索(在线程池中执行)。"""
            return await asyncio.to_thread(
                self._bm25_search, collection_name, query, n_candidates, where
            )

        # 并发执行两路检索
        if use_bm25:
            vector_results, bm25_results = await asyncio.gather(
                _vector_search(), _bm25_search_async()
            )
            # RRF 合并
            candidates = self._rrf_merge(vector_results, bm25_results, n_candidates)
        else:
            candidates = await _vector_search()
            # 仅向量检索时,补充 cosine_similarity 字段(跳过 rerank 时仍可显示)
            for c in candidates:
                c["cosine_similarity"] = 1 - c.get("distance", 0)

        if not candidates:
            return []

        # 提供 intent 时自动启用重排序,避免意图描述被静默忽略
        if intent and not rerank:
            rerank = True

        # 重排序
        if rerank:
            # 推送 rerank 进度提示,让前端知道正在做 LLM 精排
            if status_callback is not None:
                try:
                    result = status_callback(
                        f"召回 {len(candidates)} 个候选块,正在重排序..."
                    )
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass
            candidates = await self._rerank(query, candidates, top_k, intent)

        return candidates[:top_k]

    async def _rerank(
        self, query: str, candidates: list[dict], top_k: int, intent: str = ""
    ) -> list[dict]:
        """重排序:LLM 评分 + 余弦距离混合排序。

        Args:
            query: 查询文本
            candidates: 候选文档列表
            top_k: 返回结果数
            intent: 搜索意图描述,描述当前想要搜索什么样的数据,供 LLM 判断
                相关性参考。可为空字符串。

        Returns:
            重排序后的文档列表
        """
        from uniclaw.provider.fallback import achat
        from uniclaw.tools.session.session import Session

        # 构建候选文档列表
        docs_text = ""
        for i, c in enumerate(candidates):
            content = c["content"]
            chunk_idx = c.get("metadata", {}).get("chunk_index")
            idx_tag = f" (chunk_index={chunk_idx})" if chunk_idx is not None else ""
            docs_text += f"[{i}]{idx_tag} {content}\n\n"

        system_prompt = (
            "你是一个文档相关性评分助手。根据查询与每个文档的相关性给出分数。"
            "分数范围 0-100: 0 表示完全无关,100 表示完全相关。"
            "请充分利用整个分数区间,区分不同程度的相关性,避免只给 0 或 100 的极端分数。"
            '只返回一个 JSON 对象,格式: {"scores": [{"index": 0, "score": 分数}, ...]},'
            "index 必须按 0, 1, 2... 顺序递增,与候选文档的 [序号] 一一对应,不要返回其他内容。"
        )
        parts = [f"查询: {query}"]
        if intent:
            parts.append(f"搜索意图: {intent}")
        parts.append(f"候选文档:\n{docs_text}\n请为每个文档打分。")
        user_message = "\n\n".join(parts)

        session = Session()
        session.add_user_message(content=user_message)

        llm_scores = None
        try:
            from uniclaw.utils.format import parse_json_from_llm

            resp = await achat(
                system_prompt,
                session,
                model_name=self.config.mini_model_name or "",
                enable_thinking=False,
                thinking=False,
                config=self.config,
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            result = parse_json_from_llm(resp.content)
            if result:
                scores = result.get("scores", [])
                if (
                    len(scores) == len(candidates)
                    and all(isinstance(s, dict) and s.get("index") == i for i, s in enumerate(scores))
                ):
                    score_values = [s.get("score", 0) for s in scores]
                    # 归一化到 [0, 1] (分数范围 0-100,钳制到合法区间)
                    llm_scores = [max(0.0, min(1.0, float(s) / 100)) for s in score_values]
        except Exception:
            pass

        # 余弦相似度: 仅向量召回的候选才有 distance,纯 BM25 召回的用 RRF 归一化分数作代理
        cosine_sim = []
        for c in candidates:
            channels = c.get("retrieval_channels", [])
            if "vector" in channels:
                cosine_sim.append(1 - c.get("distance", 0))
            elif "rrf_score" in c:
                cosine_sim.append(c["rrf_score"])
            else:
                cosine_sim.append(0.0)

        # BM25 分数已在 _bm25_search 中归一化到 [0, 1]
        has_bm25 = any(c.get("bm25_score", 0) > 0 for c in candidates)

        # 混合排序
        combined = []
        for i, c in enumerate(candidates):
            bm25 = c.get("bm25_score", 0.0)
            if llm_scores and has_bm25:
                # LLM + 向量 + BM25,权重 0.5:0.3:0.2
                score = 0.5 * llm_scores[i] + 0.3 * cosine_sim[i] + 0.2 * bm25
            elif llm_scores:
                score = 0.6 * llm_scores[i] + 0.4 * cosine_sim[i]
            elif has_bm25:
                score = 0.6 * cosine_sim[i] + 0.4 * bm25
            else:
                score = cosine_sim[i]
            c["rerank_score"] = score
            c["cosine_similarity"] = cosine_sim[i]
            if llm_scores:
                c["llm_score"] = llm_scores[i]
            combined.append(c)

        combined.sort(key=lambda x: x["rerank_score"], reverse=True)
        return combined[:top_k]
