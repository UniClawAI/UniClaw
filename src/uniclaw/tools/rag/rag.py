"""RAG 核心类 — 向量存储、多路检索和重排序。"""

from __future__ import annotations

import asyncio
import hashlib
import pickle
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rank_bm25 import BM25Okapi

from uniclaw.context import Scope, get_app_dir
from uniclaw.utils.tokenize import tokenize

from .splitter import Chunk

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


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

    def get_collection_info(self, name: str) -> dict | None:
        """获取集合统计信息。"""
        try:
            collection = self.client.get_collection(name=name)
            count = collection.count()
            return {"name": name, "count": count}
        except Exception:
            return None

    async def ingest(self, collection_name: str, chunks: list[Chunk]) -> int:
        """将文档块 embedding 后存入集合。

        Args:
            collection_name: 集合名称
            chunks: 文档块列表

        Returns:
            存入的文档块数量
        """
        collection = self.get_or_create_collection(collection_name)
        embed_fn = self._get_embedding_fn()

        # ChromaDB 单次最多 5461 条,分批处理
        batch_size = 256
        total = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [c.content for c in batch]
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            metadatas = [{**c.metadata, "created_at": now} for c in batch]

            # 生成 embedding
            embeddings = await embed_fn(texts)

            # 生成 ID (UUID 避免多次导入冲突)
            ids = [str(uuid.uuid4()) for _ in range(len(batch))]

            collection.add(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
            )
            total += len(batch)

        self._invalidate_bm25(collection_name)
        return total

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

    def _bm25_search(self, collection_name: str, query: str, top_k: int) -> list[dict]:
        """BM25 关键词检索(同步, CPU 密集型)。

        Args:
            collection_name: 集合名称
            query: 查询文本
            top_k: 返回结果数

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

        # 按分数降序排列取 top_k
        ranked_indices = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[:top_k]

        results = []
        for idx in ranked_indices:
            if scores[idx] <= 0:
                break
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

        bm25, doc_ids, doc_contents, doc_metadatas = self._bm25_cache[collection_name]
        cache_dir = self._bm25_cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

        cache_file = cache_dir / f"{collection_name}.pkl"
        payload = {
            "checksum": self._checksum(doc_ids),
            "doc_count": len(doc_ids),
            "bm25": bm25,
            "doc_ids": doc_ids,
            "doc_contents": doc_contents,
            "doc_metadatas": doc_metadatas,
        }

        try:
            tmp_file = cache_file.with_suffix(".pkl.tmp")
            with open(tmp_file, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            tmp_file.replace(cache_file)
        except Exception:
            # 写入失败不影响正常使用,下次会从 ChromaDB 重建
            pass

    def _load_bm25_from_disk(
        self, collection_name: str
    ) -> tuple[BM25Okapi, list[str], list[str], list[dict]] | None:
        """从磁盘加载 BM25 缓存,校验失败返回 None。"""
        cache_file = self._bm25_cache_dir / f"{collection_name}.pkl"
        if not cache_file.exists():
            return None

        try:
            with open(cache_file, "rb") as f:
                payload = pickle.load(f)

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

            return (
                payload["bm25"],
                payload["doc_ids"],
                payload["doc_contents"],
                payload["doc_metadatas"],
            )
        except Exception:
            # 缓存文件损坏,删除
            cache_file.unlink(missing_ok=True)
            return None

    def _invalidate_bm25(self, collection_name: str) -> None:
        """清除指定集合的 BM25 缓存(内存 + 磁盘)。"""
        self._bm25_cache.pop(collection_name, None)
        cache_file = self._bm25_cache_dir / f"{collection_name}.pkl"
        cache_file.unlink(missing_ok=True)

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

        Args:
            vector_results: 向量检索结果
            bm25_results: BM25 检索结果
            top_k: 返回结果数
            k: RRF 常数,默认 60

        Returns:
            合并后的结果列表
        """
        # 用内容做 key 去重,记录各路排名
        merged: dict[str, dict] = {}

        for rank, r in enumerate(vector_results):
            key = r["content"]
            if key not in merged:
                merged[key] = {**r, "rrf_score": 0.0, "retrieval_channels": []}
            merged[key]["rrf_score"] += 1.0 / (k + rank)
            merged[key]["retrieval_channels"].append("vector")
            # 保留向量检索的 distance 信息
            if "distance" in r:
                merged[key]["distance"] = r["distance"]

        for rank, r in enumerate(bm25_results):
            key = r["content"]
            if key not in merged:
                merged[key] = {**r, "rrf_score": 0.0, "retrieval_channels": []}
            merged[key]["rrf_score"] += 1.0 / (k + rank)
            merged[key]["retrieval_channels"].append("bm25")
            # 保留 BM25 分数
            if "bm25_score" in r:
                merged[key]["bm25_score"] = r["bm25_score"]

        # 按 RRF 分数降序排列
        results = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
        return results[:top_k]

    async def search(
        self,
        collection_name: str,
        query: str,
        top_k: int = 5,
        rerank: bool = True,
        use_bm25: bool = True,
        intent: str = "",
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

            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_candidates,
                include=["documents", "metadatas", "distances"],
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
                self._bm25_search, collection_name, query, n_candidates
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

        if not candidates:
            return []

        # 提供 intent 时自动启用重排序,避免意图描述被静默忽略
        if intent and not rerank:
            rerank = True

        # 重排序
        if rerank:
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
            content = c["content"][:500]  # 截断避免过长
            chunk_idx = c.get("metadata", {}).get("chunk_index")
            idx_tag = f" (chunk_index={chunk_idx})" if chunk_idx is not None else ""
            docs_text += f"[{i}]{idx_tag} {content}\n\n"

        system_prompt = (
            "你是一个文档相关性评分助手。根据查询与每个文档的相关性给出分数。"
            "分数范围 0-100: 0 表示完全无关,100 表示完全相关。"
            "请充分利用整个分数区间,区分不同程度的相关性,避免只给 0 或 100 的极端分数。"
            '只返回一个 JSON 对象,格式: {"scores": [{"index": 序号, "chunk_index": 块索引, "score": 分数}, ...]},'
            "不要返回其他内容。如果没有 chunk_index 则填 null。"
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
            )
            result = parse_json_from_llm(resp.content)
            if result:
                scores = result.get("scores", [])
                # 兼容两种格式: [{"score": 8, ...}, ...] 或 [8, 5, ...]
                if scores and isinstance(scores[0], dict):
                    # 按 index 排序确保顺序正确
                    scores.sort(key=lambda x: x.get("index", 0))
                    score_values = [s.get("score", 0) for s in scores]
                else:
                    score_values = scores
                if len(score_values) == len(candidates):
                    # 归一化到 [0, 1] (分数范围 0-100,钳制到合法区间)
                    llm_scores = [max(0.0, min(1.0, s / 100)) for s in score_values]
        except Exception:
            pass

        # 余弦相似度: 仅向量召回的候选才有 distance,纯 BM25 召回的置为 0
        cosine_sim = []
        for c in candidates:
            channels = c.get("retrieval_channels", [])
            if "vector" in channels:
                cosine_sim.append(1 - c.get("distance", 0))
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
