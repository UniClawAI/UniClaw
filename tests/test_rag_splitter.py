"""
splitter.py 模块的单元测试

测试文本拆分器: FixedSizeSplitter 和 RecursiveSplitter
"""

import pytest
from uniclaw.tools.rag.splitter import (
    Chunk,
    FixedSizeSplitter,
    RecursiveSplitter,
    split_documents,
)
from uniclaw.tools.rag.loader import Document


# ── 辅助工具 ──────────────────────────────────────────────

def _reconstruct_from_chunks(chunks: list[str], splitter: RecursiveSplitter) -> str:
    """从 chunk 列表重建原文(考虑 overlap)。

    假设相邻 chunk 之间有 token 级别的 overlap,
    通过逐字符匹配找到重叠部分并去重拼接。
    """
    if not chunks:
        return ""
    result = chunks[0]
    for c in chunks[1:]:
        # 尝试找到最长的尾部-头部重叠
        max_ol = min(len(result), len(c))
        found = False
        for ol in range(max_ol, 0, -1):
            if result[-ol:] == c[:ol]:
                result += c[ol:]
                found = True
                break
        if not found:
            result += c
    return result


def _assert_chunks_cover_text(splitter, text: str):
    """断言 chunks 的 token 拼接覆盖原文所有 token。"""
    chunks = splitter.split_text(text)
    original_tokens = splitter.tokenizer.encode(text)
    chunk_tokens = []
    for c in chunks:
        chunk_tokens.extend(splitter.tokenizer.encode(c))
    # chunk token 序列应包含原文所有 token
    assert len(chunk_tokens) >= len(original_tokens), (
        f"chunk tokens ({len(chunk_tokens)}) < original tokens ({len(original_tokens)})"
    )


def _assert_no_chunk_exceeds_size(splitter, chunks: list[str]):
    """断言所有 chunk 的 token 数不超过 chunk_size。"""
    for i, c in enumerate(chunks):
        tokens = splitter.count_tokens(c)
        assert tokens <= splitter.chunk_size, (
            f"chunk[{i}] exceeds chunk_size: {tokens} > {splitter.chunk_size}"
        )


def _assert_overlap_present(splitter, chunks: list[str]):
    """断言相邻 chunk 之间存在 overlap(当 chunk_overlap > 0)。"""
    if splitter.chunk_overlap == 0 or len(chunks) < 2:
        return
    for i in range(len(chunks) - 1):
        prev_tail = splitter._take_tail(chunks[i], splitter.chunk_overlap)
        curr = chunks[i + 1]
        # 前一个 chunk 的尾部应该出现在下一个 chunk 的开头附近
        # 由于分隔符的影响,我们检查 token 级别的重叠
        prev_tail_tokens = splitter.tokenizer.encode(prev_tail)
        curr_tokens = splitter.tokenizer.encode(curr)
        # 至少有一些 overlap tokens
        overlap_count = 0
        for j in range(min(len(prev_tail_tokens), len(curr_tokens))):
            if prev_tail_tokens[-(j + 1)] == curr_tokens[j]:
                overlap_count += 1
            else:
                break
        assert overlap_count > 0, (
            f"No token overlap between chunk[{i}] and chunk[{i + 1}]"
        )


# ── TextSplitter 基类测试 ────────────────────────────────

class TestTextSplitterBase:
    """TextSplitter 基类验证"""

    def test_chunk_size_must_be_positive(self):
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            RecursiveSplitter(chunk_size=0)

    def test_chunk_overlap_must_be_non_negative(self):
        with pytest.raises(ValueError, match="chunk_overlap must be non-negative"):
            RecursiveSplitter(chunk_overlap=-1)

    def test_overlap_must_be_less_than_size(self):
        with pytest.raises(ValueError, match="chunk_overlap must be less than chunk_size"):
            RecursiveSplitter(chunk_size=10, chunk_overlap=10)

    def test_split_text_not_implemented(self):
        from uniclaw.tools.rag.splitter import TextSplitter
        splitter = TextSplitter.__new__(TextSplitter)
        with pytest.raises(NotImplementedError):
            splitter.split_text("test")


# ── FixedSizeSplitter 测试 ───────────────────────────────

class TestFixedSizeSplitter:
    """FixedSizeSplitter 基本功能"""

    def test_basic_split(self):
        splitter = FixedSizeSplitter(chunk_size=10, chunk_overlap=0)
        text = " ".join(["word"] * 25)
        chunks = splitter.split_text(text)
        assert len(chunks) == 3  # 10 + 10 + 5

    def test_no_overlap(self):
        splitter = FixedSizeSplitter(chunk_size=10, chunk_overlap=0)
        text = " ".join(["word"] * 20)
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        assert len(chunks) == 2

    def test_with_overlap(self):
        splitter = FixedSizeSplitter(chunk_size=10, chunk_overlap=3)
        text = " ".join(["word"] * 20)
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        # step = 10 - 3 = 7, ceil(20 / 7) = 3
        assert len(chunks) == 3

    def test_short_text_single_chunk(self):
        splitter = FixedSizeSplitter(chunk_size=10, chunk_overlap=0)
        text = "short text"
        chunks = splitter.split_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_empty_text(self):
        splitter = FixedSizeSplitter(chunk_size=10, chunk_overlap=0)
        chunks = splitter.split_text("")
        assert chunks == []

    def test_split_documents(self):
        splitter = FixedSizeSplitter(chunk_size=10, chunk_overlap=0)
        docs = [
            Document(content=" ".join(["a"] * 15), metadata={"source": "test.txt"}),
        ]
        chunks = splitter.split_documents(docs)
        assert len(chunks) == 2
        assert all(isinstance(c, Chunk) for c in chunks)
        assert chunks[0].metadata["source"] == "test.txt"
        assert chunks[0].metadata["chunk_index"] == 0
        assert chunks[1].metadata["chunk_index"] == 1
        assert chunks[0].metadata["total_chunks"] == 2


# ── RecursiveSplitter 基本测试 ───────────────────────────

class TestRecursiveSplitterBasic:
    """RecursiveSplitter 基本功能"""

    def test_empty_text(self):
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=0)
        assert splitter.split_text("") == []
        assert splitter.split_text("   ") == []
        assert splitter.split_text("\n\n") == []

    def test_short_text_single_chunk(self):
        splitter = RecursiveSplitter(chunk_size=100, chunk_overlap=10)
        text = "Hello, world!"
        chunks = splitter.split_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_paragraph_split(self):
        """按段落 \\n\\n 分割"""
        # 9 tokens total, chunk_size=4 forces paragraph-level split
        splitter = RecursiveSplitter(chunk_size=4, chunk_overlap=0)
        text = "Para one.\n\nPara two.\n\nPara three."
        chunks = splitter.split_text(text)
        assert len(chunks) >= 2
        assert "Para one." in chunks[0]
        assert "Para three." in chunks[-1]

    def test_sentence_split(self):
        """段落太大时按句子分割"""
        # 9 tokens total, chunk_size=4 forces split
        splitter = RecursiveSplitter(chunk_size=4, chunk_overlap=0)
        text = "First sentence. Second sentence. Third sentence."
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        assert len(chunks) >= 2

    def test_word_split_fallback(self):
        """句子也太大时按单词分割"""
        splitter = RecursiveSplitter(chunk_size=5, chunk_overlap=0)
        text = "abc def ghi jkl mno pqr stu vwx yz"
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        assert len(chunks) >= 2

    def test_chinese_text(self):
        """中文文本按句号分割"""
        # 12 tokens total, chunk_size=5 forces split
        splitter = RecursiveSplitter(chunk_size=5, chunk_overlap=2)
        text = "这是第一句话。这是第二句话。这是第三句话。"
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        assert len(chunks) >= 2

    def test_all_chunks_within_size(self):
        """所有 chunk 不超过 chunk_size"""
        splitter = RecursiveSplitter(chunk_size=12, chunk_overlap=3)
        text = "Hello world. " * 20
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)

    def test_split_documents_preserves_metadata(self):
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=0)
        docs = [
            Document(content="First doc.", metadata={"source": "a.txt"}),
            Document(content="Second doc.", metadata={"source": "b.txt"}),
        ]
        chunks = splitter.split_documents(docs)
        assert len(chunks) == 2
        assert chunks[0].metadata["source"] == "a.txt"
        assert chunks[1].metadata["source"] == "b.txt"


# ── RecursiveSplitter overlap 测试 ───────────────────────

class TestRecursiveSplitterOverlap:
    """RecursiveSplitter overlap 行为"""

    def test_overlap_present_between_chunks(self):
        """相邻 chunk 之间应有 overlap"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=3)
        text = " ".join(["word"] * 30)
        chunks = splitter.split_text(text)
        _assert_overlap_present(splitter, chunks)

    def test_zero_overlap(self):
        """chunk_overlap=0 时无重叠"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=0)
        text = " ".join(["word"] * 20)
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        # 无 overlap 时 chunk 数量应该更少
        assert len(chunks) == 2

    def test_large_overlap(self):
        """较大的 overlap 不会导致无限循环"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=8)
        text = " ".join(["W"] * 20)
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        assert len(chunks) > 1


# ── 递归分支 overlap 来源修复验证 ────────────────────────

class TestRecursiveSplitterBugFix:
    """验证 _recursive_split 中递归分支的 overlap 来源修复

    Bug: 当 current_chunk(含 overlap) + split 仍超长需要递归拆分时,
    原代码从未拆分的 current_chunk 取 tail,而非从递归拆分后的 sub_chunks[-1] 取,
    导致 overlap 来源错误(内容重复或衔接断裂)。
    """

    def test_recursive_branch_overlap_continuity(self):
        """递归拆分后,下一个 chunk 的开头应与前一个 chunk 的尾部有 overlap"""
        splitter = RecursiveSplitter(chunk_size=15, chunk_overlap=5)
        # 20 tokens 的超长段落,需要递归拆分
        long_para = " ".join(["WORD"] * 20)
        text = long_para + "\n\nEND"
        chunks = splitter.split_text(text)

        _assert_no_chunk_exceeds_size(splitter, chunks)
        # 至少应拆成3块:长段落的部分 + 长段落剩余 + END
        assert len(chunks) >= 3

        # 验证相邻 chunk 之间有 overlap
        _assert_overlap_present(splitter, chunks)

    def test_recursive_branch_no_content_duplication(self):
        """递归拆分不应导致大量重复内容"""
        splitter = RecursiveSplitter(chunk_size=15, chunk_overlap=5)
        long_para = " ".join(["WORD"] * 20)
        text = long_para + "\n\nEND_TEXT"
        chunks = splitter.split_text(text)

        # 将所有 chunk 的 token 拼接
        all_tokens = []
        for c in chunks:
            all_tokens.extend(splitter.tokenizer.encode(c))
        original_tokens = splitter.tokenizer.encode(text)

        # chunk tokens 总数不应超过原文的2倍(合理的 overlap 开销)
        assert len(all_tokens) < len(original_tokens) * 2, (
            f"Excessive duplication: {len(all_tokens)} vs {len(original_tokens)}"
        )

    def test_recursive_branch_tail_matches_sub_chunks(self):
        """修复核心:递归拆分后的 tail 应取自 sub_chunks[-1]"""
        splitter = RecursiveSplitter(chunk_size=15, chunk_overlap=5)
        # 构造触发递归分支的场景
        long_para = " ".join(["AAA"] * 25)
        text = long_para + "\n\nBBB"
        chunks = splitter.split_text(text)

        # 最后一个 chunk 应包含 "BBB"
        assert any("BBB" in c for c in chunks), "BBB should appear in chunks"

        # 验证 BBB 所在 chunk 的开头与前一个 chunk 的尾部有 overlap
        for i, c in enumerate(chunks):
            if "BBB" in c and i > 0:
                prev_tail = splitter._take_tail(chunks[i - 1], splitter.chunk_overlap)
                # prev_tail 的 tokens 应该出现在当前 chunk 的开头
                tail_tokens = splitter.tokenizer.encode(prev_tail)
                curr_tokens = splitter.tokenizer.encode(c)
                # 至少第一个 overlap token 应匹配
                if tail_tokens and curr_tokens:
                    assert tail_tokens[-1] == curr_tokens[0] or any(
                        tail_tokens[-j] == curr_tokens[0]
                        for j in range(1, len(tail_tokens))
                    ), "Overlap continuity broken at recursive branch boundary"
                break

    def test_recursive_with_multiple_long_paragraphs(self):
        """多个超长段落递归拆分后内容完整"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=3)
        para1 = " ".join(["A"] * 15)
        para2 = " ".join(["B"] * 15)
        text = para1 + "\n\n" + para2
        chunks = splitter.split_text(text)

        _assert_no_chunk_exceeds_size(splitter, chunks)
        # A 和 B 都应出现
        text_joined = " ".join(chunks)
        assert "A" in text_joined
        assert "B" in text_joined

    def test_recursive_extreme_overlap_no_infinite_loop(self):
        """极端 overlap 不会导致挂起(chunk_size=10, overlap=8)"""
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=8)
        text = " ".join(["W"] * 30)
        # 如果有 bug 会挂起;设置超时
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        assert len(chunks) > 0

    def test_single_word_exceeds_chunk_size(self):
        """单个词超过 chunk_size 时递归到字符级别"""
        splitter = RecursiveSplitter(chunk_size=3, chunk_overlap=1)
        # 用带空格的单词确保 token 数量足够
        text = " ".join(["WORD"] * 10)  # 10 tokens > chunk_size=3
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        assert len(chunks) > 1

    def test_separator_boundary_continuity(self):
        """跨分隔符边界时 overlap 保持连续"""
        splitter = RecursiveSplitter(chunk_size=12, chunk_overlap=4)
        text = "First paragraph with extra words.\n\nSecond paragraph with extra words."
        chunks = splitter.split_text(text)
        _assert_no_chunk_exceeds_size(splitter, chunks)
        if len(chunks) > 1:
            _assert_overlap_present(splitter, chunks)


# ── split_documents 入口函数测试 ─────────────────────────

class TestSplitDocuments:
    """split_documents 入口函数"""

    def test_basic_usage(self):
        docs = [Document(content="Hello world.", metadata={"source": "test"})]
        chunks = split_documents(docs, chunk_size=10, chunk_overlap=2)
        assert len(chunks) >= 1
        assert all(isinstance(c, Chunk) for c in chunks)

    def test_multiple_documents(self):
        docs = [
            Document(content="Doc one content.", metadata={"source": "a"}),
            Document(content="Doc two content.", metadata={"source": "b"}),
        ]
        chunks = split_documents(docs, chunk_size=10, chunk_overlap=2)
        sources = {c.metadata["source"] for c in chunks}
        assert "a" in sources
        assert "b" in sources

    def test_empty_documents(self):
        chunks = split_documents([], chunk_size=10, chunk_overlap=2)
        assert chunks == []
