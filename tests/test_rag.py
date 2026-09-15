"""
RAG 模块综合测试

覆盖 loader.py, rag.py, tools.py, context.py 的主要功能
"""

import hashlib
import json
import pickle
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.base import ToolRuntime
from uniclaw.context import Scope
from uniclaw.tools.rag.loader import (
    TEXT_EXTENSIONS,
    Document,
    _load_pdf,
    _load_text,
    load_directory,
    load_file,
)
from uniclaw.tools.rag.rag import RAGManager, _match_where
from uniclaw.tools.rag.splitter import (
    Chunk,
    FixedSizeSplitter,
    RecursiveSplitter,
    split_documents,
)
from uniclaw.tools.rag.tools import (
    _get_manager,
    _judge_relevance,
    _manager_cache,
    rag_delete_collection,
    rag_evaluate,
    rag_ingest,
    rag_list_collections,
    rag_search,
    rag_set_desc,
)


# ── 辅助工具 ──────────────────────────────────────────────


def _create_mock_config(
    root_dir: Path | None = None,
    embedding_model: str = "text-embedding-3-small",
    mini_model_name: str = "gpt-4o-mini",
) -> SimpleNamespace:
    """创建模拟的 AppConfig。"""
    return SimpleNamespace(
        root_dir=root_dir,
        embedding_model=embedding_model,
        mini_model_name=mini_model_name,
        providers={
            "openai": SimpleNamespace(
                api_key="test-key",
                base_url="https://api.openai.com/v1",
            )
        },
        current_agent=SimpleNamespace(
            session=SimpleNamespace(id="test-session-id")
        ),
    )


def _create_temp_file(tmp_path: Path, name: str, content: str) -> Path:
    """创建临时文件。

    newline="" 禁止 Windows 上 write_text 把 \\n 翻译为 \\r\\n,
    保证 load_file 读回的内容与写入的逐字节一致。
    """
    file_path = tmp_path / name
    file_path.write_text(content, encoding="utf-8", newline="")
    return file_path


def _create_temp_directory(tmp_path: Path, files: dict[str, str]) -> Path:
    """创建临时目录及文件。"""
    dir_path = tmp_path / "test_dir"
    dir_path.mkdir(exist_ok=True)
    for name, content in files.items():
        file_path = dir_path / name
        # 确保父目录存在（支持子目录路径）
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    return dir_path


# ── loader.py 测试 ────────────────────────────────────────


class TestDocument:
    """Document 数据类测试"""

    def test_default_metadata(self):
        doc = Document(content="test content")
        assert doc.content == "test content"
        assert doc.metadata == {}

    def test_custom_metadata(self):
        metadata = {"source": "test.txt", "page": 1}
        doc = Document(content="test content", metadata=metadata)
        assert doc.metadata == metadata


class TestLoadFile:
    """load_file 函数测试"""

    def test_load_text_file(self, tmp_path):
        """测试加载文本文件"""
        file_path = _create_temp_file(tmp_path, "test.txt", "Hello, world!")
        docs = load_file(file_path)
        assert len(docs) == 1
        assert docs[0].content == "Hello, world!"
        assert docs[0].metadata["source"] == str(file_path)
        assert docs[0].metadata["filename"] == "test.txt"
        assert docs[0].metadata["suffix"] == ".txt"

    def test_load_markdown_file(self, tmp_path):
        """测试加载 Markdown 文件"""
        content = "# Title\n\nSome content"
        file_path = _create_temp_file(tmp_path, "test.md", content)
        docs = load_file(file_path)
        assert len(docs) == 1
        assert docs[0].content == content

    def test_load_python_file(self, tmp_path):
        """测试加载 Python 文件"""
        content = "def hello():\n    return 'world'"
        file_path = _create_temp_file(tmp_path, "test.py", content)
        docs = load_file(file_path)
        assert len(docs) == 1
        assert docs[0].content == content

    def test_unsupported_format(self, tmp_path):
        """测试不支持的文件格式"""
        file_path = tmp_path / "test.xyz"
        file_path.write_bytes(b"binary content")
        with pytest.raises(ValueError, match="不支持的文件格式"):
            load_file(file_path)

    def test_load_json_file(self, tmp_path):
        """测试加载 JSON 文件"""
        content = '{"key": "value"}'
        file_path = _create_temp_file(tmp_path, "test.json", content)
        docs = load_file(file_path)
        assert len(docs) == 1
        assert docs[0].content == content

    def test_load_yaml_file(self, tmp_path):
        """测试加载 YAML 文件"""
        content = "key: value\nlist:\n  - item1\n  - item2"
        file_path = _create_temp_file(tmp_path, "test.yaml", content)
        docs = load_file(file_path)
        assert len(docs) == 1
        assert docs[0].content == content

    def test_load_csv_file(self, tmp_path):
        """测试加载 CSV 文件"""
        content = "name,age\nAlice,30\nBob,25"
        file_path = _create_temp_file(tmp_path, "test.csv", content)
        docs = load_file(file_path)
        assert len(docs) == 1
        assert docs[0].content == content

    def test_load_html_file(self, tmp_path):
        """测试加载 HTML 文件"""
        content = "<html><body><h1>Hello</h1></body></html>"
        file_path = _create_temp_file(tmp_path, "test.html", content)
        docs = load_file(file_path)
        assert len(docs) == 1
        assert docs[0].content == content

    def test_encoding_detection_utf8(self, tmp_path):
        """测试 UTF-8 编码检测"""
        content = "你好世界"
        file_path = _create_temp_file(tmp_path, "test.txt", content)
        docs = load_file(file_path)
        assert docs[0].content == content

    @patch("uniclaw.tools.rag.loader._load_pdf")
    def test_load_pdf_calls_helper(self, mock_load_pdf, tmp_path):
        """测试 PDF 文件调用辅助函数"""
        file_path = tmp_path / "test.pdf"
        file_path.write_bytes(b"fake pdf")
        mock_load_pdf.return_value = [Document(content="pdf content")]
        docs = load_file(file_path)
        mock_load_pdf.assert_called_once_with(file_path)
        assert len(docs) == 1


class TestLoadText:
    """_load_text 函数测试"""

    def test_utf8_encoding(self, tmp_path):
        """测试 UTF-8 编码文件"""
        content = "Hello 你好"
        file_path = _create_temp_file(tmp_path, "test.txt", content)
        docs = _load_text(file_path)
        assert docs[0].content == content

    def test_metadata_fields(self, tmp_path):
        """测试元数据字段完整性"""
        file_path = _create_temp_file(tmp_path, "test.py", "print('hello')")
        docs = _load_text(file_path)
        metadata = docs[0].metadata
        assert metadata["source"] == str(file_path)
        assert metadata["filename"] == "test.py"
        assert metadata["suffix"] == ".py"


class TestLoadDirectory:
    """load_directory 函数测试"""

    def test_load_recursive(self, tmp_path):
        """测试递归加载目录"""
        files = {
            "file1.txt": "content1",
            "file2.py": "content2",
            "subdir/file3.md": "content3",
        }
        dir_path = _create_temp_directory(tmp_path, files)
        # 创建子目录
        (dir_path / "subdir").mkdir(exist_ok=True)
        ((dir_path / "subdir") / "file3.md").write_text("content3")

        docs, skipped = load_directory(dir_path, recursive=True)
        assert len(docs) == 3
        assert skipped == 0

    def test_load_non_recursive(self, tmp_path):
        """测试非递归加载目录"""
        files = {
            "file1.txt": "content1",
            "file2.py": "content2",
        }
        dir_path = _create_temp_directory(tmp_path, files)
        # 创建子目录（不应被加载）
        (dir_path / "subdir").mkdir(exist_ok=True)
        ((dir_path / "subdir") / "file3.md").write_text("content3")

        docs, skipped = load_directory(dir_path, recursive=False)
        assert len(docs) == 2

    def test_skip_hidden_files(self, tmp_path):
        """测试跳过隐藏文件"""
        files = {
            "file1.txt": "content1",
            ".hidden.txt": "hidden content",
        }
        dir_path = _create_temp_directory(tmp_path, files)
        docs, _ = load_directory(dir_path)
        assert len(docs) == 1
        assert docs[0].metadata["filename"] == "file1.txt"

    def test_skip_hidden_directories(self, tmp_path):
        """测试跳过隐藏目录"""
        dir_path = tmp_path / "test_dir"
        dir_path.mkdir()
        (dir_path / "file1.txt").write_text("content1")

        # 创建隐藏目录
        hidden_dir = dir_path / ".hidden"
        hidden_dir.mkdir()
        (hidden_dir / "file2.txt").write_text("hidden content")

        docs, _ = load_directory(dir_path, recursive=True)
        assert len(docs) == 1

    def test_skip_node_modules(self, tmp_path):
        """测试兜底规则跳过 node_modules/__pycache__ 等依赖目录"""
        dir_path = tmp_path / "test_dir"
        dir_path.mkdir()
        (dir_path / "file1.txt").write_text("content1")

        for junk in ("node_modules", "__pycache__"):
            junk_dir = dir_path / junk
            junk_dir.mkdir()
            (junk_dir / f"{junk}.txt").write_text("junk content")

        docs, _ = load_directory(dir_path, recursive=True)
        assert len(docs) == 1
        assert docs[0].metadata["filename"] == "file1.txt"

    def test_gitignore_rules_respected(self, tmp_path):
        """测试 .gitignore 规则过滤"""
        dir_path = _create_temp_directory(tmp_path, {
            "file1.txt": "content1",
            "build/out.txt": "build output",
            "secret.txt": "secret",
        })
        (dir_path / ".gitignore").write_text("build/\nsecret.txt\n", encoding="utf-8")

        docs, _ = load_directory(dir_path, recursive=True)
        names = {d.metadata["filename"] for d in docs}
        # .gitignore 本身无后缀(Path('.gitignore').suffix == ''),不会进入文档
        assert names == {"file1.txt"}

    def test_unsupported_files_skipped(self, tmp_path):
        """测试跳过不支持的文件格式"""
        files = {
            "file1.txt": "content1",
            "file2.xyz": "unsupported content",
        }
        dir_path = _create_temp_directory(tmp_path, files)
        docs, _ = load_directory(dir_path)
        assert len(docs) == 1

    def test_unreadable_files_counted(self, tmp_path):
        """测试无法读取的文件计数返回"""
        files = {
            "file1.txt": "content1",
            "file2.md": "content2",
        }
        dir_path = _create_temp_directory(tmp_path, files)
        with patch(
            "uniclaw.tools.rag.loader.load_file",
            side_effect=[load_file(dir_path / "file1.txt"), OSError("disk error")],
        ):
            docs, skipped = load_directory(dir_path)
        assert len(docs) == 1
        assert skipped == 1

    def test_empty_directory(self, tmp_path):
        """测试空目录"""
        dir_path = tmp_path / "empty"
        dir_path.mkdir()
        docs, _ = load_directory(dir_path)
        assert len(docs) == 0


# ── splitter.py 补充测试 ─────────────────────────────────


class TestTextSplitterEdgeCases:
    """TextSplitter 边界情况测试"""

    def test_count_tokens(self):
        """测试 token 计数"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=0)
        tokens = splitter.count_tokens("Hello, world!")
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_split_documents_multiple_docs(self):
        """测试多文档拆分"""
        splitter = FixedSizeSplitter(chunk_size=5, chunk_overlap=0)
        # 使用多个单词确保产生足够的 token
        docs = [
            Document(content="word " * 10, metadata={"source": "a.txt"}),
            Document(content="text " * 10, metadata={"source": "b.txt"}),
        ]
        chunks = splitter.split_documents(docs)
        assert len(chunks) >= 4  # 每个文档至少拆分为 2 个块
        sources = {c.metadata["source"] for c in chunks}
        assert sources == {"a.txt", "b.txt"}

    def test_chunk_metadata_fields(self):
        """测试 Chunk 元数据字段"""
        splitter = FixedSizeSplitter(chunk_size=5, chunk_overlap=0)
        docs = [Document(content="word " * 10, metadata={"source": "test.txt"})]
        chunks = splitter.split_documents(docs)
        assert chunks[0].metadata["chunk_index"] == 0
        assert chunks[0].metadata["total_chunks"] >= 2
        assert chunks[1].metadata["chunk_index"] == 1

    def test_recursive_splitter_separators(self):
        """测试递归分块器的分隔符优先级"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=0)
        text = "Paragraph one with more words.\n\nParagraph two with more words.\n\nParagraph three with more words."
        chunks = splitter.split_text(text)
        # 应该按段落分割
        assert len(chunks) >= 2

    def test_recursive_splitter_chinese_punctuation(self):
        """测试中文标点符号分割"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=0)
        text = "这是第一句话有更多的内容。这是第二句话有更多的内容。这是第三句话有更多的内容。这是第四句话有更多的内容。"
        chunks = splitter.split_text(text)
        assert len(chunks) >= 2

    def test_recursive_splitter_fallback_to_chars(self):
        """测试回退到字符级别分割"""
        splitter = RecursiveSplitter(chunk_size=5, chunk_overlap=0)
        # 使用带空格的单词确保产生足够的 token
        text = "abc def ghi jkl mno pqr stu vwx yz"
        chunks = splitter.split_text(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert splitter.count_tokens(chunk) <= 5

    def test_take_tail(self):
        """测试 _take_tail 方法"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=3)
        text = "Hello, this is a test text for tail extraction."
        tail = splitter._take_tail(text, 5)
        assert splitter.count_tokens(tail) <= 5

    def test_take_tail_short_text(self):
        """测试短文本的 _take_tail"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=3)
        text = "Short"
        tail = splitter._take_tail(text, 10)
        assert tail == text


class TestSplitDocuments:
    """split_documents 入口函数补充测试"""

    def test_default_parameters(self):
        """测试默认参数"""
        docs = [Document(content="Hello world.", metadata={})]
        chunks = split_documents(docs)
        assert len(chunks) >= 1

    def test_custom_parameters(self):
        """测试自定义参数"""
        # 使用多个单词确保产生足够的 token
        docs = [Document(content="word " * 50, metadata={})]
        chunks = split_documents(docs, chunk_size=10, chunk_overlap=2)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.content) > 0


# ── rag.py 测试 ───────────────────────────────────────────


class TestRAGManager:
    """RAGManager 类测试"""

    def test_init_with_project_scope(self, tmp_path):
        """测试项目级初始化"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        assert manager.scope == Scope.PROJECT
        assert manager.config == config

    def test_init_with_user_scope(self, tmp_path):
        """测试用户级初始化"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.USER)
        assert manager.scope == Scope.USER

    def test_rag_dir_project_scope(self, tmp_path):
        """测试项目级 RAG 目录"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        assert "rag" in str(manager._rag_dir)
        assert str(tmp_path) in str(manager._rag_dir)

    def test_rag_dir_user_scope(self, tmp_path):
        """测试用户级 RAG 目录"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.USER)
        assert "rag" in str(manager._rag_dir)

    def test_db_path(self, tmp_path):
        """测试 ChromaDB 路径"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        assert "chroma_data" in str(manager.db_path)

    def test_bm25_cache_dir(self, tmp_path):
        """测试 BM25 缓存目录"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        assert "bm25_cache" in str(manager._bm25_cache_dir)

    def test_checksum(self):
        """测试校验和计算"""
        ids = ["id1", "id2", "id3"]
        checksum = RAGManager._checksum(ids)
        expected = hashlib.md5("|".join(ids).encode()).hexdigest()
        assert checksum == expected

    def test_checksum_consistency(self):
        """测试校验和一致性"""
        ids = ["id1", "id2", "id3"]
        checksum1 = RAGManager._checksum(ids)
        checksum2 = RAGManager._checksum(ids)
        assert checksum1 == checksum2

    def test_checksum_different_for_different_ids(self):
        """测试不同 ID 产生不同校验和"""
        ids1 = ["id1", "id2"]
        ids2 = ["id3", "id4"]
        checksum1 = RAGManager._checksum(ids1)
        checksum2 = RAGManager._checksum(ids2)
        assert checksum1 != checksum2


class TestRAGManagerBM25:
    """RAGManager BM25 相关测试"""

    def test_bm25_cache_initially_empty(self, tmp_path):
        """测试 BM25 缓存初始为空"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        assert manager._bm25_cache == {}

    def test_invalidate_bm25(self, tmp_path):
        """测试清除 BM25 缓存"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        manager._bm25_cache["test_collection"] = (None, [], [], [])
        manager._invalidate_bm25("test_collection")
        assert "test_collection" not in manager._bm25_cache

    def test_invalidate_bm25_nonexistent(self, tmp_path):
        """测试清除不存在的集合缓存"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        # 不应抛出异常
        manager._invalidate_bm25("nonexistent")

    def test_save_bm25_to_disk_empty_cache(self, tmp_path):
        """测试空缓存时保存到磁盘"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        # 不应抛出异常
        manager._save_bm25_to_disk("test_collection")

    def test_load_bm25_from_disk_no_file(self, tmp_path):
        """测试从磁盘加载不存在的缓存"""
        config = _create_mock_config(root_dir=tmp_path)
        manager = RAGManager(config, Scope.PROJECT)
        result = manager._load_bm25_from_disk("nonexistent")
        assert result is None


class TestRAGManagerRRFMerge:
    """RAGManager RRF 合并测试"""

    def test_rrf_merge_basic(self):
        """测试基本 RRF 合并"""
        vector_results = [
            {"content": "doc1", "distance": 0.1},
            {"content": "doc2", "distance": 0.2},
        ]
        bm25_results = [
            {"content": "doc2", "bm25_score": 0.8},
            {"content": "doc3", "bm25_score": 0.6},
        ]
        merged = RAGManager._rrf_merge(vector_results, bm25_results, top_k=10)
        assert len(merged) == 3
        # doc2 应该排名最高（出现在两路结果中）
        assert merged[0]["content"] == "doc2"
        assert "vector" in merged[0]["retrieval_channels"]
        assert "bm25" in merged[0]["retrieval_channels"]

    def test_rrf_merge_top_k(self):
        """测试 RRF 合并 top_k 限制"""
        vector_results = [{"content": f"doc{i}", "distance": 0.1} for i in range(10)]
        bm25_results = []
        merged = RAGManager._rrf_merge(vector_results, bm25_results, top_k=5)
        assert len(merged) == 5

    def test_rrf_merge_empty_results(self):
        """测试空结果的 RRF 合并"""
        merged = RAGManager._rrf_merge([], [], top_k=10)
        assert len(merged) == 0

    def test_rrf_merge_deduplication(self):
        """测试 RRF 合并去重"""
        vector_results = [
            {"content": "doc1", "distance": 0.1},
            {"content": "doc1", "distance": 0.2},  # 重复
        ]
        bm25_results = []
        merged = RAGManager._rrf_merge(vector_results, bm25_results, top_k=10)
        assert len(merged) == 1

    def test_rrf_merge_preserves_distance(self):
        """测试 RRF 合并保留 distance 信息"""
        vector_results = [{"content": "doc1", "distance": 0.5}]
        bm25_results = []
        merged = RAGManager._rrf_merge(vector_results, bm25_results, top_k=10)
        assert merged[0]["distance"] == 0.5

    def test_rrf_merge_preserves_bm25_score(self):
        """测试 RRF 合并保留 BM25 分数"""
        vector_results = []
        bm25_results = [{"content": "doc1", "bm25_score": 0.9}]
        merged = RAGManager._rrf_merge(vector_results, bm25_results, top_k=10)
        assert merged[0]["bm25_score"] == 0.9

    def test_rrf_merge_custom_k(self):
        """测试自定义 k 参数"""
        vector_results = [{"content": "doc1", "distance": 0.1}]
        bm25_results = [{"content": "doc1", "bm25_score": 0.8}]
        merged = RAGManager._rrf_merge(vector_results, bm25_results, top_k=10, k=30)
        assert len(merged) == 1
        # RRF 分数应使用 k=30 计算并归一化到 [0, 1](理论最大值 2/k)
        expected_score = 1.0  # 双路 rank 0,恰好达到归一化最大值
        assert abs(merged[0]["rrf_score"] - expected_score) < 0.001

    def test_rrf_merge_scores_normalized(self):
        """测试 RRF 分数归一化: 双路命中的分数高于单路命中"""
        vector_results = [
            {"content": "both", "distance": 0.1},
            {"content": "vector-only", "distance": 0.3},
        ]
        bm25_results = [{"content": "both", "bm25_score": 0.8}]
        merged = RAGManager._rrf_merge(vector_results, bm25_results, top_k=10)
        by_content = {r["content"]: r["rrf_score"] for r in merged}
        # 双路第一名归一化后为 1.0,单路第二名为 1/(60+1) / (2/60) ≈ 0.49
        assert by_content["both"] == pytest.approx(1.0)
        assert by_content["vector-only"] < 0.5


# ── tools.py 测试 ─────────────────────────────────────────


class TestRAGIngest:
    """rag_ingest 工具测试"""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """清除 _manager_cache 避免 mock 跨测试泄漏。"""
        _manager_cache.clear()
        yield
        _manager_cache.clear()

    @pytest.mark.asyncio
    async def test_no_config(self):
        """测试无配置时返回错误"""
        result = await rag_ingest.func(
            path="/tmp/test",
            collection="test",
            tool_runtime=ToolRuntime(config=None),
        )
        assert "无法获取配置" in result

    @pytest.mark.asyncio
    async def test_path_not_exists(self, tmp_path):
        """测试路径不存在"""
        config = _create_mock_config(root_dir=tmp_path)
        result = await rag_ingest.func(
            path=str(tmp_path / "nonexistent"),
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "路径不存在" in result

    @pytest.mark.asyncio
    async def test_no_readable_documents(self, tmp_path):
        """测试无可读取文档"""
        config = _create_mock_config(root_dir=tmp_path)
        # 创建一个不支持的文件
        (tmp_path / "test.xyz").write_bytes(b"binary content")
        result = await rag_ingest.func(
            path=str(tmp_path / "test.xyz"),
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "不支持的文件格式" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools.RAGManager")
    async def test_ingest_single_file(self, mock_manager_class, tmp_path):
        """测试导入单个文件"""
        config = _create_mock_config(root_dir=tmp_path)
        file_path = _create_temp_file(tmp_path, "test.txt", "Hello, world!")

        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager
        mock_manager.get_existing_hashes.return_value = set()
        mock_manager.compute_chunk_hashes = AsyncMock(return_value=["h1"])
        mock_manager.delete_by_hashes.return_value = 0
        mock_manager.ingest = AsyncMock(return_value=1)
        mock_manager.get_collection_info.return_value = {"count": 1}

        result = await rag_ingest.func(
            path=str(file_path),
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "成功导入" in result
        assert "1 个文档" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools.RAGManager")
    async def test_ingest_directory(self, mock_manager_class, tmp_path):
        """测试导入目录"""
        config = _create_mock_config(root_dir=tmp_path)
        dir_path = _create_temp_directory(tmp_path, {
            "file1.txt": "content1",
            "file2.txt": "content2",
        })

        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager
        mock_manager.get_existing_hashes.return_value = set()
        mock_manager.compute_chunk_hashes = AsyncMock(return_value=["h1", "h2"])
        mock_manager.delete_by_hashes.return_value = 0
        mock_manager.ingest = AsyncMock(return_value=2)
        mock_manager.get_collection_info.return_value = {"count": 2}

        result = await rag_ingest.func(
            path=str(dir_path),
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "成功导入" in result
        assert "2 个文档" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools.RAGManager")
    async def test_incremental_skip_unchanged(self, mock_manager_class, tmp_path):
        """增量导入: 所有块哈希未变更时跳过"""
        config = _create_mock_config(root_dir=tmp_path)
        file_path = _create_temp_file(tmp_path, "test.txt", "Hello, world!")

        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager
        # 所有块哈希已存在 → 全部跳过
        mock_manager.get_existing_hashes.return_value = {"h1"}
        mock_manager.compute_chunk_hashes = AsyncMock(return_value=["h1"])
        mock_manager.get_collection_info.return_value = {"count": 1}

        result = await rag_ingest.func(
            path=str(file_path),
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "未变更" in result
        # 不应调用 ingest
        mock_manager.ingest.assert_not_called()

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools.RAGManager")
    async def test_incremental_delete_stale(self, mock_manager_class, tmp_path):
        """增量导入: 文件内容变更后删除旧块"""
        config = _create_mock_config(root_dir=tmp_path)
        file_path = _create_temp_file(tmp_path, "test.txt", "New content!")

        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager
        # 旧哈希 old_h1 不在新哈希中 → 应被删除
        mock_manager.get_existing_hashes.return_value = {"old_h1"}
        mock_manager.compute_chunk_hashes = AsyncMock(return_value=["new_h1"])
        mock_manager.delete_by_hashes.return_value = 1
        mock_manager.ingest = AsyncMock(return_value=1)
        mock_manager.get_collection_info.return_value = {"count": 1}

        result = await rag_ingest.func(
            path=str(file_path),
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "成功导入" in result
        mock_manager.delete_by_hashes.assert_called_once_with("test", {"old_h1"})

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools.RAGManager")
    async def test_contextual_enabled(self, mock_manager_class, tmp_path):
        """测试启用 Contextual Retrieval"""
        config = _create_mock_config(root_dir=tmp_path)
        file_path = _create_temp_file(tmp_path, "test.txt", "Hello, world!")

        mock_manager = MagicMock()
        mock_manager_class.return_value = mock_manager
        mock_manager.get_existing_hashes.return_value = set()
        mock_manager.compute_chunk_hashes = AsyncMock(return_value=["h1"])
        mock_manager.delete_by_hashes.return_value = 0
        mock_manager.generate_chunk_contexts = AsyncMock(
            return_value=["这是一段测试文档"]
        )
        mock_manager.ingest = AsyncMock(return_value=1)
        mock_manager.get_collection_info.return_value = {"count": 1}

        result = await rag_ingest.func(
            path=str(file_path),
            collection="test",
            contextual=True,
            tool_runtime=ToolRuntime(config=config),
        )
        assert "成功导入" in result
        mock_manager.generate_chunk_contexts.assert_called_once()


class TestRAGSearch:
    """rag_search 工具测试"""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        """清除 _manager_cache 避免 mock 跨测试泄漏。"""
        _manager_cache.clear()
        yield
        _manager_cache.clear()

    @pytest.mark.asyncio
    async def test_no_config(self):
        """测试无配置时返回错误"""
        result = await rag_search.func(
            query="test query",
            collection="test",
            tool_runtime=ToolRuntime(config=None),
        )
        assert "无法获取配置" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_search_no_results(self, mock_get_manager, tmp_path):
        """测试无搜索结果"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.search = AsyncMock(return_value=[])

        result = await rag_search.func(
            query="test query",
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "未找到" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_search_with_results(self, mock_get_manager, tmp_path):
        """测试有搜索结果"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.search = AsyncMock(return_value=[
            {
                "content": "test content",
                "metadata": {"source": "test.txt", "filename": "test.txt"},
                "rerank_score": 0.9,
                "cosine_similarity": 0.85,
            }
        ])

        result = await rag_search.func(
            query="test query",
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "找到 1 个相关结果" in result
        assert "test content" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_search_deduplication(self, mock_get_manager, tmp_path):
        """测试搜索结果去重:同内容同来源去重,同内容不同来源保留"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        # 返回同内容不同来源的两条结果 → 保留两条
        mock_manager.search = AsyncMock(return_value=[
            {
                "content": "duplicate content",
                "metadata": {"source": "test.txt"},
                "rerank_score": 0.9,
            },
            {
                "content": "duplicate content",
                "metadata": {"source": "test2.txt"},
                "rerank_score": 0.8,
            },
        ])

        result = await rag_search.func(
            query="test query",
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        # 不同来源的同内容块应保留两条
        assert "找到 2 个相关结果" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_search_deduplication_same_source(self, mock_get_manager, tmp_path):
        """测试同内容同来源的跨层级去重"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        # 返回同内容同来源的两条结果(模拟跨层级返回相同文档) → 去重为一条
        mock_manager.search = AsyncMock(return_value=[
            {
                "content": "duplicate content",
                "metadata": {"source": "test.txt"},
                "rerank_score": 0.9,
            },
            {
                "content": "duplicate content",
                "metadata": {"source": "test.txt"},
                "rerank_score": 0.8,
            },
        ])

        result = await rag_search.func(
            query="test query",
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "找到 1 个相关结果" in result


class TestRAGListCollections:
    """rag_list_collections 工具测试"""

    def test_no_config(self):
        """测试无配置时返回错误"""
        result = rag_list_collections.func(tool_runtime=ToolRuntime(config=None))
        assert "无法获取配置" in result

    @patch("uniclaw.tools.rag.tools._get_manager")
    def test_no_collections(self, mock_get_manager, tmp_path):
        """测试无集合"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.list_collections.return_value = []

        result = rag_list_collections.func(tool_runtime=ToolRuntime(config=config))
        assert "没有任何集合" in result

    @patch("uniclaw.tools.rag.tools._get_manager")
    def test_with_collections(self, mock_get_manager, tmp_path):
        """测试有集合"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.list_collections.return_value = [
            {
                "name": "test-collection",
                "count": 10,
                "created_at": "2024-01-01 00:00:00",
                "description": "Test collection",
            }
        ]

        result = rag_list_collections.func(tool_runtime=ToolRuntime(config=config))
        assert "test-collection" in result
        assert "10 个文档块" in result
        assert "Test collection" in result


class TestRAGSetDesc:
    """rag_set_desc 工具测试"""

    def test_no_config(self):
        """测试无配置时返回错误"""
        result = rag_set_desc.func(
            collection="test",
            description="Test description",
            tool_runtime=ToolRuntime(config=None),
        )
        assert "无法获取配置" in result

    @patch("uniclaw.tools.rag.tools._get_manager")
    def test_set_desc_success(self, mock_get_manager, tmp_path):
        """测试设置描述成功"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.set_collection_desc.return_value = True

        result = rag_set_desc.func(
            collection="test",
            description="New description",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "已更新" in result
        assert "New description" in result

    @patch("uniclaw.tools.rag.tools._get_manager")
    def test_set_desc_failure(self, mock_get_manager, tmp_path):
        """测试设置描述失败"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.set_collection_desc.return_value = False

        result = rag_set_desc.func(
            collection="nonexistent",
            description="New description",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "不存在或设置失败" in result


class TestRAGDeleteCollection:
    """rag_delete_collection 工具测试"""

    def test_no_config(self):
        """测试无配置时返回错误"""
        result = rag_delete_collection.func(
            collection="test",
            tool_runtime=ToolRuntime(config=None),
        )
        assert "无法获取配置" in result

    @patch("uniclaw.tools.rag.tools._get_manager")
    def test_delete_success(self, mock_get_manager, tmp_path):
        """测试删除成功"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.delete_collection.return_value = True

        result = rag_delete_collection.func(
            collection="test",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "已删除集合" in result

    @patch("uniclaw.tools.rag.tools._get_manager")
    def test_delete_failure(self, mock_get_manager, tmp_path):
        """测试删除失败"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.delete_collection.return_value = False

        result = rag_delete_collection.func(
            collection="nonexistent",
            tool_runtime=ToolRuntime(config=config),
        )
        assert "不存在或删除失败" in result


# ── _match_where 测试 ─────────────────────────────────────


class TestMatchWhere:
    """_match_where 本地 ChromaDB where 过滤测试"""

    def test_empty_where_matches_anything(self):
        """空条件匹配任何 metadata"""
        assert _match_where({"source": "a.txt"}, {}) is True
        assert _match_where(None, {}) is True

    def test_implicit_eq(self):
        """隐式 $eq: {key: value}"""
        assert _match_where({"source": "a.txt"}, {"source": "a.txt"}) is True
        assert _match_where({"source": "a.txt"}, {"source": "b.txt"}) is False

    def test_explicit_operators(self):
        """显式操作符 $ne/$gt/$gte/$lt/$lte"""
        meta = {"size": 100}
        assert _match_where(meta, {"size": {"$ne": 50}}) is True
        assert _match_where(meta, {"size": {"$ne": 100}}) is False
        assert _match_where(meta, {"size": {"$gt": 50}}) is True
        assert _match_where(meta, {"size": {"$gte": 100}}) is True
        assert _match_where(meta, {"size": {"$lt": 200}}) is True
        assert _match_where(meta, {"size": {"$lte": 100}}) is True

    def test_in_nin(self):
        """$in / $nin 操作符"""
        meta = {"suffix": ".md"}
        assert _match_where(meta, {"suffix": {"$in": [".md", ".txt"]}}) is True
        assert _match_where(meta, {"suffix": {"$in": [".txt", ".py"]}}) is False
        assert _match_where(meta, {"suffix": {"$nin": [".txt", ".py"]}}) is True

    def test_and_logic(self):
        """$and 组合条件"""
        meta = {"size": 200, "suffix": ".md"}
        where = {"$and": [{"size": {"$gt": 100}}, {"suffix": ".md"}]}
        assert _match_where(meta, where) is True
        where_fail = {"$and": [{"size": {"$gt": 300}}, {"suffix": ".md"}]}
        assert _match_where(meta, where_fail) is False

    def test_or_logic(self):
        """$or 组合条件"""
        meta = {"suffix": ".md"}
        where = {"$or": [{"suffix": ".md"}, {"suffix": ".txt"}]}
        assert _match_where(meta, where) is True
        where_fail = {"$or": [{"suffix": ".txt"}, {"suffix": ".py"}]}
        assert _match_where(meta, where_fail) is False

    def test_none_metadata(self):
        """metadata 为 None 时,隐式 $eq 应返回 False"""
        assert _match_where(None, {"source": "a.txt"}) is False

    def test_missing_key(self):
        """metadata 中不存在的 key 应不匹配"""
        assert _match_where({"other": 1}, {"source": "a.txt"}) is False


class TestRAGSearchWhere:
    """rag_search where 参数透传测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_where_passed_to_manager(self, mock_get_manager, tmp_path):
        """where 参数应透传给 manager.search"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.search = AsyncMock(return_value=[])

        where_cond = {"suffix": {"$in": [".md", ".txt"]}}
        await rag_search.func(
            query="test",
            collection="test",
            where=where_cond,
            tool_runtime=ToolRuntime(config=config),
        )

        # 验证 where 被传给 search
        call_kwargs = mock_manager.search.call_args
        assert call_kwargs.kwargs.get("where") == where_cond or (
            len(call_kwargs.args) >= 8 and call_kwargs.args[7] == where_cond
        )


# ── context.py 测试 ───────────────────────────────────────


class TestRAGContext:
    """RAG 上下文注入测试"""

    def test_no_collections(self, tmp_path):
        """测试无集合时返回空字符串"""
        import sys
        from unittest.mock import patch
        from uniclaw.tools.rag.context import get_rag_system_prompt

        mock_config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_manager.list_collections.return_value = []

        with patch("uniclaw.tools.rag.rag.RAGManager", return_value=mock_manager), \
             patch.dict(sys.modules, {"uniclaw.config": MagicMock(tool_runtime=ToolRuntime(config=mock_config))}):
            result = get_rag_system_prompt(mock_config)
        assert result == ""

    def test_with_collections(self, tmp_path):
        """测试有集合时生成提示词"""
        import sys
        from unittest.mock import patch
        from uniclaw.tools.rag.context import get_rag_system_prompt

        mock_config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_manager.list_collections.return_value = [
            {
                "name": "test-collection",
                "count": 10,
                "description": "Test collection",
            }
        ]

        with patch("uniclaw.tools.rag.rag.RAGManager", return_value=mock_manager), \
             patch.dict(sys.modules, {"uniclaw.config": MagicMock(tool_runtime=ToolRuntime(config=mock_config))}):
            result = get_rag_system_prompt(mock_config)
        assert "RAG 文档检索" in result
        assert "test-collection" in result
        assert "10 块" in result
        assert "Test collection" in result

    def test_empty_collections_filtered(self, tmp_path):
        """测试过滤空集合"""
        import sys
        from unittest.mock import patch
        from uniclaw.tools.rag.context import get_rag_system_prompt

        mock_config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_manager.list_collections.return_value = [
            {
                "name": "empty-collection",
                "count": 0,
                "description": "Empty collection",
            },
            {
                "name": "populated-collection",
                "count": 5,
                "description": "Populated collection",
            }
        ]

        with patch("uniclaw.tools.rag.rag.RAGManager", return_value=mock_manager), \
             patch.dict(sys.modules, {"uniclaw.config": MagicMock(tool_runtime=ToolRuntime(config=mock_config))}):
            result = get_rag_system_prompt(mock_config)
        # empty-collection 应该被过滤掉（count=0）
        assert "- empty-collection" not in result
        assert "populated-collection" in result

    def test_project_and_user_collections(self, tmp_path):
        """测试项目级和用户级集合"""
        import sys
        from unittest.mock import patch
        from uniclaw.tools.rag.context import get_rag_system_prompt

        mock_config = _create_mock_config(root_dir=tmp_path)

        # 模拟两个不同 scope 的 manager
        def side_effect(config, scope):
            mock = MagicMock()
            if scope == Scope.PROJECT:
                mock.list_collections.return_value = [
                    {"name": "project-col", "count": 5}
                ]
            else:
                mock.list_collections.return_value = [
                    {"name": "user-col", "count": 3}
                ]
            return mock

        with patch("uniclaw.tools.rag.rag.RAGManager", side_effect=side_effect), \
             patch.dict(sys.modules, {"uniclaw.config": MagicMock(tool_runtime=ToolRuntime(config=mock_config))}):
            result = get_rag_system_prompt(mock_config)
        assert "项目级集合" in result
        assert "project-col" in result
        assert "用户级集合" in result
        assert "user-col" in result

    def test_none_root_dir(self):
        """测试 root_dir 为 None"""
        import sys
        from unittest.mock import patch
        from uniclaw.tools.rag.context import get_rag_system_prompt

        mock_config = _create_mock_config(root_dir=None)
        mock_manager = MagicMock()
        mock_manager.list_collections.return_value = [
            {"name": "user-col", "count": 5}
        ]

        with patch("uniclaw.tools.rag.rag.RAGManager", return_value=mock_manager), \
             patch.dict(sys.modules, {"uniclaw.config": MagicMock(tool_runtime=ToolRuntime(config=mock_config))}):
            result = get_rag_system_prompt(mock_config)
        # 应该只有用户级集合
        assert "user-col" in result
        assert "项目级集合" not in result


# ── TEXT_EXTENSIONS 测试 ──────────────────────────────────


class TestTextExtensions:
    """TEXT_EXTENSIONS 常量测试"""

    def test_common_extensions_present(self):
        """测试常见扩展名存在"""
        assert ".txt" in TEXT_EXTENSIONS
        assert ".md" in TEXT_EXTENSIONS
        assert ".py" in TEXT_EXTENSIONS
        assert ".json" in TEXT_EXTENSIONS
        assert ".yaml" in TEXT_EXTENSIONS
        assert ".csv" in TEXT_EXTENSIONS
        assert ".html" in TEXT_EXTENSIONS
        assert ".js" in TEXT_EXTENSIONS
        assert ".ts" in TEXT_EXTENSIONS

    def test_programming_languages(self):
        """测试编程语言扩展名"""
        assert ".java" in TEXT_EXTENSIONS
        assert ".c" in TEXT_EXTENSIONS
        assert ".cpp" in TEXT_EXTENSIONS
        assert ".go" in TEXT_EXTENSIONS
        assert ".rs" in TEXT_EXTENSIONS
        assert ".rb" in TEXT_EXTENSIONS
        assert ".swift" in TEXT_EXTENSIONS
        assert ".kt" in TEXT_EXTENSIONS

    def test_config_files(self):
        """测试配置文件扩展名"""
        assert ".toml" in TEXT_EXTENSIONS
        assert ".ini" in TEXT_EXTENSIONS
        assert ".cfg" in TEXT_EXTENSIONS
        assert ".conf" in TEXT_EXTENSIONS
        assert ".env" in TEXT_EXTENSIONS

    def test_web_files(self):
        """测试 Web 文件扩展名"""
        assert ".vue" in TEXT_EXTENSIONS
        assert ".svelte" in TEXT_EXTENSIONS
        assert ".astro" in TEXT_EXTENSIONS
        assert ".jsx" in TEXT_EXTENSIONS
        assert ".tsx" in TEXT_EXTENSIONS

    def test_shell_scripts(self):
        """测试 Shell 脚本扩展名"""
        assert ".sh" in TEXT_EXTENSIONS
        assert ".bash" in TEXT_EXTENSIONS
        assert ".zsh" in TEXT_EXTENSIONS
        assert ".fish" in TEXT_EXTENSIONS
        assert ".bat" in TEXT_EXTENSIONS
        assert ".cmd" in TEXT_EXTENSIONS
        assert ".ps1" in TEXT_EXTENSIONS

    def test_pdf_not_in_text_extensions(self):
        """测试 PDF 不在文本扩展名中"""
        assert ".pdf" not in TEXT_EXTENSIONS


# ── 集成测试 ──────────────────────────────────────────────


class TestRAGIntegration:
    """RAG 模块集成测试"""

    def test_document_to_chunk_flow(self):
        """测试文档到 Chunk 的完整流程"""
        docs = [
            Document(
                content="This is a test document with some content.",
                metadata={"source": "test.txt", "filename": "test.txt"},
            )
        ]
        chunks = split_documents(docs, chunk_size=10, chunk_overlap=2)
        assert len(chunks) > 0
        for chunk in chunks:
            assert isinstance(chunk, Chunk)
            assert chunk.metadata["source"] == "test.txt"
            assert "chunk_index" in chunk.metadata
            assert "total_chunks" in chunk.metadata

    def test_multiple_documents_to_chunks(self):
        """测试多文档到 Chunk 的流程"""
        docs = [
            Document(content="First document.", metadata={"source": "doc1.txt"}),
            Document(content="Second document.", metadata={"source": "doc2.txt"}),
            Document(content="Third document.", metadata={"source": "doc3.txt"}),
        ]
        chunks = split_documents(docs, chunk_size=10, chunk_overlap=2)
        sources = {c.metadata["source"] for c in chunks}
        assert sources == {"doc1.txt", "doc2.txt", "doc3.txt"}

    def test_load_and_split_integration(self, tmp_path):
        """测试加载和拆分集成"""
        # 创建测试文件
        content = "This is a test document. " * 20
        file_path = _create_temp_file(tmp_path, "test.txt", content)

        # 加载文档
        docs = load_file(file_path)
        assert len(docs) == 1

        # 拆分文档
        chunks = split_documents(docs, chunk_size=20, chunk_overlap=5)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.content) > 0
            assert chunk.metadata["source"] == str(file_path)

    def test_load_directory_and_split_integration(self, tmp_path):
        """测试加载目录和拆分集成"""
        # 创建测试目录和文件
        files = {
            "file1.txt": "Content of file 1. " * 10,
            "file2.txt": "Content of file 2. " * 10,
        }
        dir_path = _create_temp_directory(tmp_path, files)

        # 加载目录
        docs, _ = load_directory(dir_path)
        assert len(docs) == 2

        # 拆分文档
        chunks = split_documents(docs, chunk_size=20, chunk_overlap=5)
        assert len(chunks) > 2
        sources = {c.metadata["source"] for c in chunks}
        assert len(sources) == 2


# ── 边界情况测试 ──────────────────────────────────────────


class TestEdgeCases:
    """边界情况测试"""

    def test_empty_content_document(self):
        """测试空内容文档"""
        docs = [Document(content="", metadata={})]
        chunks = split_documents(docs, chunk_size=10, chunk_overlap=0)
        # 空内容应该被跳过
        assert len(chunks) == 0

    def test_whitespace_only_document(self):
        """测试纯空白字符文档"""
        docs = [Document(content="   \n\n  ", metadata={})]
        chunks = split_documents(docs, chunk_size=10, chunk_overlap=0)
        assert len(chunks) == 0

    def test_very_long_document(self):
        """测试超长文档"""
        content = "word " * 10000
        docs = [Document(content=content, metadata={})]
        chunks = split_documents(docs, chunk_size=100, chunk_overlap=20)
        assert len(chunks) > 100
        for chunk in chunks:
            assert len(chunk.content) > 0

    def test_special_characters_in_content(self):
        """测试特殊字符内容"""
        content = "Hello! @#$%^&*() 你好世界 🌍"
        docs = [Document(content=content, metadata={})]
        chunks = split_documents(docs, chunk_size=50, chunk_overlap=10)
        assert len(chunks) >= 1
        # 特殊字符应该被保留
        assert any("🌍" in c.content for c in chunks)

    def test_metadata_preserved_through_splitting(self):
        """测试元数据在拆分过程中保留"""
        metadata = {
            "source": "test.txt",
            "filename": "test.txt",
            "custom_field": "custom_value",
        }
        docs = [Document(content="A" * 100, metadata=metadata)]
        chunks = split_documents(docs, chunk_size=20, chunk_overlap=5)
        for chunk in chunks:
            assert chunk.metadata["source"] == "test.txt"
            assert chunk.metadata["filename"] == "test.txt"
            assert chunk.metadata["custom_field"] == "custom_value"


# ── rag_evaluate 工具测试 ─────────────────────────────────


def _make_result(
    content: str, source: str, chunk_index: int | None = None, **score_fields
) -> dict:
    """构造一个检索结果字典。"""
    metadata = {"source": source, "filename": source.split("/")[-1]}
    if chunk_index is not None:
        metadata["chunk_index"] = chunk_index
    result = {"content": content, "metadata": metadata}
    result.update(score_fields)
    return result


class TestRAGEvaluate:
    """rag_evaluate 工具测试"""

    @pytest.mark.asyncio
    async def test_no_config(self):
        """测试无配置时返回错误"""
        result = await rag_evaluate.func(
            collection="test",
            queries=["query1"],
            tool_runtime=ToolRuntime(config=None),
        )
        assert "无法获取配置" in result

    @pytest.mark.asyncio
    async def test_empty_queries(self, tmp_path):
        """测试 queries 为空"""
        config = _create_mock_config(root_dir=tmp_path)
        result = await rag_evaluate.func(
            collection="test",
            queries=[],
            tool_runtime=ToolRuntime(config=config),
        )
        assert "queries 不能为空" in result

    @pytest.mark.asyncio
    async def test_invalid_top_k(self, tmp_path):
        """测试 top_k 无效"""
        config = _create_mock_config(root_dir=tmp_path)
        result = await rag_evaluate.func(
            collection="test",
            queries=["query1"],
            top_k=0,
            tool_runtime=ToolRuntime(config=config),
        )
        assert "top_k 必须为正整数" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_evaluate_no_llm_judge(self, mock_get_manager, tmp_path):
        """测试不启用 LLM judge 时仅统计检索结果数量"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.search = AsyncMock(
            return_value=[_make_result("doc1", "a.txt", rerank_score=0.9)]
        )

        result = await rag_evaluate.func(
            collection="test",
            queries=["query1"],
            tool_runtime=ToolRuntime(config=config),
        )
        assert "未启用 LLM Judge" in result
        assert "评估完成" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_evaluate_errors_collected(self, mock_get_manager, tmp_path):
        """测试搜索错误被收集但不中断评估"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_manager.search = AsyncMock(
            return_value=[_make_result("doc1", "a.txt", rerank_score=0.9)]
        )

        # 项目级正常,用户级抛异常
        def side_effect(config, scope):
            if scope == Scope.PROJECT:
                return mock_manager
            raise RuntimeError("boom")

        mock_get_manager.side_effect = side_effect

        result = await rag_evaluate.func(
            collection="test",
            queries=["query1"],
            tool_runtime=ToolRuntime(config=config),
        )
        assert "搜索错误" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_evaluate_deduplication(self, mock_get_manager, tmp_path):
        """测试跨层级结果去重"""
        config = _create_mock_config(root_dir=tmp_path)

        # 项目级和用户级返回相同内容
        def scope_manager(config, scope):
            m = MagicMock()
            m.search = AsyncMock(
                return_value=[_make_result("doc1", "a.txt", rerank_score=0.9)]
            )
            return m

        mock_get_manager.side_effect = scope_manager

        result = await rag_evaluate.func(
            collection="test",
            queries=["query1"],
            tool_runtime=ToolRuntime(config=config),
        )
        assert "返回 1 个结果" in result


class TestJudgeRelevance:
    """_judge_relevance LLM judge 测试"""

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.achat")
    async def test_returns_normalized_scores(self, mock_achat, tmp_path):
        """测试返回归一化的分数"""
        from uniclaw.tools.session.session import AIMessage

        config = _create_mock_config(root_dir=tmp_path)
        mock_achat.return_value = AIMessage(
            content='{"judgments": [{"index": 0, "score": 80}, {"index": 1, "score": 30}]}'
        )

        results = [
            _make_result("doc1 content", "a.txt", rerank_score=0.9),
            _make_result("doc2 content", "b.txt", rerank_score=0.5),
        ]
        scores = await _judge_relevance("query", results, config)
        assert scores == [0.8, 0.3]

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.achat")
    async def test_invalid_format_returns_zeros(self, mock_achat, tmp_path):
        """测试非法格式(纯数字列表)返回全 0,不兼容"""
        from uniclaw.tools.session.session import AIMessage

        config = _create_mock_config(root_dir=tmp_path)
        mock_achat.return_value = AIMessage(content='{"judgments": [100, 0]}')

        results = [
            _make_result("doc1 content", "a.txt", rerank_score=0.9),
            _make_result("doc2 content", "b.txt", rerank_score=0.5),
        ]
        scores = await _judge_relevance("query", results, config)
        assert scores == [0.0, 0.0]

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.achat")
    async def test_missing_index_returns_zeros(self, mock_achat, tmp_path):
        """测试缺失 index 字段视为格式错误,返回全 0"""
        from uniclaw.tools.session.session import AIMessage

        config = _create_mock_config(root_dir=tmp_path)
        mock_achat.return_value = AIMessage(
            content='{"judgments": [{"score": 90}]}'
        )

        results = [_make_result("doc1 content", "a.txt", rerank_score=0.9)]
        scores = await _judge_relevance("query", results, config)
        assert scores == [0.0]

    @pytest.mark.asyncio
    @patch("uniclaw.provider.fallback.achat")
    async def test_failure_returns_zeros(self, mock_achat, tmp_path):
        """测试 LLM 调用失败时返回全 0"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_achat.side_effect = RuntimeError("LLM down")

        results = [_make_result("doc1 content", "a.txt", rerank_score=0.9)]
        scores = await _judge_relevance("query", results, config)
        assert scores == [0.0]

    @pytest.mark.asyncio
    async def test_empty_results(self, tmp_path):
        """测试空结果"""
        config = _create_mock_config(root_dir=tmp_path)
        scores = await _judge_relevance("query", [], config)
        assert scores == []


class TestRAGEvaluateExtended:
    """rag_evaluate 扩展功能测试(LLM judge)"""

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    @patch("uniclaw.tools.rag.tools._judge_relevance")
    async def test_llm_judge_report(self, mock_judge, mock_get_manager, tmp_path):
        """测试启用 LLM judge 时输出相关度指标"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.search = AsyncMock(
            return_value=[
                _make_result("doc1 content", "a.txt", rerank_score=0.9),
                _make_result("doc2 content", "b.txt", rerank_score=0.5),
            ]
        )
        mock_judge.return_value = [0.9, 0.2]

        result = await rag_evaluate.func(
            collection="test",
            queries=["query1"],
            use_llm_judge=True,
            tool_runtime=ToolRuntime(config=config),
        )
        assert "LLM Judge" in result
        assert "Context Precision" in result
        assert "0.9" in result  # judge 平均分
        # 0.9 ≥ 0.5 算相关,0.2 < 0.5 不算 → precision 1/2 = 50%
        assert "50%" in result

    @pytest.mark.asyncio
    @patch("uniclaw.tools.rag.tools._get_manager")
    async def test_llm_judge_empty_results(self, mock_get_manager, tmp_path):
        """测试 LLM judge 且某问题无检索结果时不崩溃"""
        config = _create_mock_config(root_dir=tmp_path)
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.search = AsyncMock(return_value=[])

        result = await rag_evaluate.func(
            collection="test",
            queries=["query1"],
            use_llm_judge=True,
            tool_runtime=ToolRuntime(config=config),
        )
        assert "评估完成" in result
        assert "返回 0 个结果" in result

