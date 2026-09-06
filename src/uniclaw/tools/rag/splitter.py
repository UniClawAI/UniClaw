"""文本拆分器 — 支持固定大小和递归分块策略。"""

from __future__ import annotations

from dataclasses import dataclass, field

import tiktoken

from .loader import Document


@dataclass
class Chunk:
    """拆分后的文档块。"""
    content: str
    metadata: dict = field(default_factory=dict)


class TextSplitter:
    """文本分块器基类"""

    def __init__(self, chunk_size: int = 256, chunk_overlap: int = 50):
        """初始化分块器

        Args:
            chunk_size: 分块大小(token 数)
            chunk_overlap: 分块重叠大小
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be non-negative")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.tokenizer = tiktoken.get_encoding("o200k_base")

    def count_tokens(self, text: str) -> int:
        """计算文本的 token 数量"""
        return len(self.tokenizer.encode(text))

    def split_text(self, text: str) -> list[str]:
        """分割文本,子类实现"""
        raise NotImplementedError

    def split_documents(self, documents: list[Document]) -> list[Chunk]:
        """将文档分割成块

        Args:
            documents: 文档列表

        Returns:
            Chunk 列表
        """
        chunks = []
        for doc in documents:
            texts = self.split_text(doc.content)
            for i, text in enumerate(texts):
                if not text.strip():
                    continue
                chunk = Chunk(
                    content=text,
                    metadata={
                        **doc.metadata,
                        "chunk_index": i,
                        "total_chunks": len(texts),
                    },
                )
                chunks.append(chunk)
        return chunks


class FixedSizeSplitter(TextSplitter):
    """固定大小分块器

    按照固定的 token 数量分割文本,简单高效。
    缺点:可能在句子中间断开,破坏语义完整性。
    """

    def split_text(self, text: str) -> list[str]:
        """按固定大小分割文本

        使用滑动窗口方式,确保每个块的 token 数不超过 chunk_size
        """
        tokens = self.tokenizer.encode(text)
        chunks = []

        start = 0
        while start < len(tokens):
            end = min(start + self.chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]
            chunk_text = self.tokenizer.decode(chunk_tokens)
            chunks.append(chunk_text)
            start += self.chunk_size - self.chunk_overlap

        return chunks


class RecursiveSplitter(TextSplitter):
    """递归分块器(推荐)

    递归地使用不同级别的分隔符分割文本:
    1. 首先尝试按段落分割
    2. 如果块太大,按句子分割
    3. 如果还太大,按单词分割

    这种方法能更好地保持语义完整性。
    """

    SEPARATORS = [
        "\n\n",  # 段落
        "\n",  # 换行
        "。",  # 中文句号
        ".",  # 英文句号
        "！",  # 中文感叹号
        "!",  # 英文感叹号
        "？",  # 中文问号
        "?",  # 英文问号
        "；",  # 中文分号
        ";",  # 英文分号
        " ",  # 空格
        "",  # 字符级别(兜底)
    ]

    def split_text(self, text: str) -> list[str]:
        """递归分割文本"""
        return self._recursive_split(text, self.SEPARATORS)

    def _recursive_split(self, text: str, separators: list[str]) -> list[str]:
        """递归分割的核心逻辑

        Args:
            text: 待分割的文本
            separators: 分隔符列表(按优先级排序)

        Returns:
            分割后的文本块列表
        """
        if not text.strip():
            return []

        if self.count_tokens(text) <= self.chunk_size:
            return [text.strip()]

        # 找到文本中存在的最高优先级分隔符
        # "" 始终匹配,作为最终兜底
        separator = separators[-1]
        for sep in separators:
            if sep == "" or sep in text:
                separator = sep
                break

        # "" 分隔符:逐字符拆分(因为 text.split("") 会报错)
        if separator == "":
            splits = list(text)
        else:
            splits = text.split(separator)

        chunks = []
        current_chunk = ""

        for split in splits:
            if separator != "" and not split.strip():
                continue

            test_chunk = current_chunk + separator + split if current_chunk else split

            if self.count_tokens(test_chunk) <= self.chunk_size:
                current_chunk = test_chunk
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    # 保留尾部 overlap 部分作为下一个块的开头
                    if self.chunk_overlap > 0:
                        overlap_text = self._take_tail(current_chunk, self.chunk_overlap)
                        current_chunk = overlap_text + separator + split if overlap_text else split
                    else:
                        current_chunk = split
                    # overlap 后如果当前块仍超长,用剩余分隔符递归拆分
                    remaining_seps = separators[separators.index(separator) + 1:]
                    if self.count_tokens(current_chunk) > self.chunk_size:
                        sub_chunks = self._recursive_split(current_chunk, remaining_seps)
                        chunks.extend(sub_chunks)
                        # 从递归拆分后的最后一个子块取尾部,保持跨段连续性
                        if sub_chunks and self.chunk_overlap > 0:
                            current_chunk = self._take_tail(sub_chunks[-1], self.chunk_overlap)
                        else:
                            current_chunk = ""
                else:
                    if self.count_tokens(split) > self.chunk_size:
                        remaining_seps = separators[separators.index(separator) + 1:]
                        sub_chunks = self._recursive_split(split, remaining_seps)
                        chunks.extend(sub_chunks)
                        # 保留最后一个 sub_chunk 的尾部作为 overlap,与下一个块衔接
                        if sub_chunks and self.chunk_overlap > 0:
                            current_chunk = self._take_tail(sub_chunks[-1], self.chunk_overlap)
                    else:
                        current_chunk = split

        if current_chunk and current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _take_tail(self, text: str, token_count: int) -> str:
        """取文本末尾指定 token 数的内容。"""
        tokens = self.tokenizer.encode(text)
        if len(tokens) <= token_count:
            return text
        return self.tokenizer.decode(tokens[-token_count:])


def split_documents(
    documents: list[Document],
    chunk_size: int = 256,
    chunk_overlap: int = 50,
) -> list[Chunk]:
    """将文档列表拆分为更小的块。

    Args:
        documents: 文档列表
        chunk_size: 每个块的最大 token 数
        chunk_overlap: 相邻块的重叠 token 数

    Returns:
        拆分后的 Chunk 列表
    """
    splitter = RecursiveSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_documents(documents)
