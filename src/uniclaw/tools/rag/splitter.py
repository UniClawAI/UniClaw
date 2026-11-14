"""文本拆分器 — 固定大小、递归、句子、Markdown、代码定义等分块策略。"""

from __future__ import annotations

import re
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

    def _take_tail(self, text: str, token_count: int) -> str:
        """取文本末尾指定 token 数的内容。"""
        tokens = self.tokenizer.encode(text)
        if len(tokens) <= token_count:
            return text
        return self.tokenizer.decode(tokens[-token_count:])

    def _linear_split(self, text: str, max_tokens: int | None = None) -> list[str]:
        """按 token 上限把文本切成字符级片段。

        用于单个超长句子/段落的最终兜底,保证每个片段不超过 max_tokens。
        切点优先落在空白处,避免把英文单词/标点从中间劈开。
        """
        limit = max_tokens if max_tokens is not None else self.chunk_size
        parts: list[str] = []
        rest = text.strip()
        while rest:
            length = self._max_prefix_chars(rest, limit)
            while True:
                # 候选切点落在空白处则用;否则收窄到最近的空白,避免劈词
                if length >= len(rest) or rest[length].isspace():
                    break
                before = rest.rfind(" ", 0, length)
                if before <= 0:
                    break  # 无空白可退,只能硬切(中文连续文本场景)
                length = before
            if length <= 0:
                length = 1  # 防退化: 单字符也超限时至少推进一个字符
            part = rest[:length].strip()
            if part:
                parts.append(part)
            rest = rest[length:]
        # 吸收孤立标点/纯空白残片(如英文句尾被单独拆出的标点),并入前一块
        if len(parts) >= 2 and not re.search(r"[^\s\W\d]", parts[-1]):
            parts[-2] += " " + parts[-1]
            parts.pop()
        return parts

    def _max_prefix_chars(self, text: str, max_tokens: int) -> int:
        """二分查找不超过 max_tokens 的最长字符前缀长度。"""
        if not text:
            return 0
        if self.count_tokens(text) <= max_tokens:
            return len(text)
        lo, hi = 1, len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.count_tokens(text[:mid]) <= max_tokens:
                lo = mid + 1
            else:
                hi = mid
        return max(1, lo - 1)


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
                        overlap_text = self._take_tail(
                            current_chunk, self.chunk_overlap
                        )
                        current_chunk = (
                            overlap_text + separator + split if overlap_text else split
                        )
                    else:
                        current_chunk = split
                    # overlap 后如果当前块仍超长,用剩余分隔符递归拆分
                    remaining_seps = separators[separators.index(separator) + 1 :]
                    if self.count_tokens(current_chunk) > self.chunk_size:
                        sub_chunks = self._recursive_split(
                            current_chunk, remaining_seps
                        )
                        chunks.extend(sub_chunks)
                        # 从递归拆分后的最后一个子块取尾部,保持跨段连续性
                        if sub_chunks and self.chunk_overlap > 0:
                            current_chunk = self._take_tail(
                                sub_chunks[-1], self.chunk_overlap
                            )
                        else:
                            current_chunk = ""
                else:
                    if self.count_tokens(split) > self.chunk_size:
                        remaining_seps = separators[separators.index(separator) + 1 :]
                        sub_chunks = self._recursive_split(split, remaining_seps)
                        chunks.extend(sub_chunks)
                        # 保留最后一个 sub_chunk 的尾部作为 overlap,与下一个块衔接
                        if sub_chunks and self.chunk_overlap > 0:
                            current_chunk = self._take_tail(
                                sub_chunks[-1], self.chunk_overlap
                            )
                    else:
                        current_chunk = split

        if current_chunk and current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks


class SentenceSplitter(TextSplitter):
    """句子分块器

    按句子边界拆分,再按 token 上限把多个句子合并成一个块。
    相比 RecursiveSplitter,句子级粒度更细,适合问答类场景:
    每个块内都是完整的句子,语义单元完整。

    分隔符覆盖中英文句号、感叹号、问号、分号,以及换行(列表、长句)。
    单个超长句子按子句/字符线性切分,保证不丢内容。相邻块之间
    保留 chunk_overlap 的文本衔接,保持上下文连贯。
    """

    # 句子结束标点(中英文句号/叹号/问号/分号)+其后空白,或换行。
    # 两组都带捕获: 前者保留标点与其后空白,后者保留换行本身 ——
    # 重拼句子时英文单词间不会粘连(如 `test!How`),换行也不会被吞掉。
    _SENTENCE_RE = re.compile(r"([。！？!?；;]+\s*)|(\n+)")

    @staticmethod
    def _slice_text(text: str) -> list[str]:
        """按句子边界切文本,每个返回片段保留原始间隙空白。

        `re.split` 有两组捕获时,返回值形如 [文本, g1, g2, 文本, g1, g2, ..., 文本]:
        标点分支只填 g1、换行分支只填 g2、另一组恒为 None。遍历时把非空捕获块
        附着到前一个文本段尾部,从而保留英文句子间的空格与换行不断字。
        """
        parts = SentenceSplitter._SENTENCE_RE.split(text)
        sentences: list[str] = []
        i = 0
        while i + 2 < len(parts):
            text_part = parts[i] or ""
            gap = (parts[i + 1] or "") + (parts[i + 2] or "")
            combined = text_part + gap
            if combined.strip():
                sentences.append(combined)
            i += 3
        tail = parts[i] or ""
        if tail.strip():
            sentences.append(tail)
        return sentences

    def split_text(self, text: str) -> list[str]:
        # 保留句子间原始空白(_slice_text 已经附着到段尾),仅最后 trim 缝隙
        sentences = [s for s in self._slice_text(text) if s.strip()]
        if not sentences:
            return []

        chunks: list[str] = []
        current = ""
        current_tokens = 0

        for sentence in sentences:
            sentence_tokens = self.count_tokens(sentence)
            if sentence_tokens > self.chunk_size:
                if current:
                    chunks.append(current)
                    current, current_tokens = "", 0
                chunks.extend(self._split_oversized(sentence))
                continue

            if current and current_tokens + sentence_tokens > self.chunk_size:
                chunks.append(current)
                tail = (
                    self._take_tail(current, self.chunk_overlap)
                    if self.chunk_overlap
                    else ""
                )
                current_tokens = self.count_tokens(tail)
                if current_tokens + sentence_tokens > self.chunk_size:
                    tail, current_tokens = "", 0
                current = tail

            current += sentence
            current_tokens += sentence_tokens

        if current:
            chunks.append(current)
        return chunks

    def _split_oversized(self, sentence: str) -> list[str]:
        """单个超长句子按子句(逗号/顿号)切割,仍超长则退字符级线性切分。"""
        pieces = [p for p in re.split(r"([，、,])", sentence) if p]
        if not pieces:
            return self._linear_split(sentence)
        merged: list[str] = []
        current = ""
        for piece in pieces:
            if current and self.count_tokens(current + piece) > self.chunk_size:
                merged.append(current)
                current = piece
            else:
                current += piece
        if current:
            merged.append(current)
        result: list[str] = []
        for m in merged:
            if self.count_tokens(m) > self.chunk_size:
                result.extend(self._linear_split(m))
            else:
                result.append(m)
        return result


class MarkdownSplitter(TextSplitter):
    """Markdown 结构化分块器

    按标题层级(#, ##, ###...)把文档切块,块边界与文档章节对齐。
    块内保留标题行,语义上下文完整;过长的章节内部按 RecursiveSplitter
    继续拆分(内部分块器按「chunk_size - 标题 token 数」收紧,保证
    拼接标题后不超过 chunk_size)。

    适合文档、博客、README 等结构化文本。块以标题开头,便于追溯章节。
    代码围栏(```)内的内容不会被误判为标题。
    """

    _HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+.+$", re.MULTILINE)
    _FENCE_RE = re.compile(r"^```[^\n]*$", re.MULTILINE)

    def split_text(self, text: str) -> list[str]:
        heading_matches = [
            m
            for m in self._HEADING_RE.finditer(text)
            if not self._in_fence(m.start(), text)
        ]
        if not heading_matches:
            return RecursiveSplitter(
                chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap
            ).split_text(text)

        chunks: list[str] = []

        # 第一个标题之前的文档引言,归为开头块
        first_start = heading_matches[0].start()
        if first_start > 0 and text[:first_start].strip():
            chunks.extend(
                RecursiveSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                ).split_text(text[:first_start])
            )

        for i, m in enumerate(heading_matches):
            end = (
                heading_matches[i + 1].start()
                if i + 1 < len(heading_matches)
                else len(text)
            )
            start = m.start()
            section = text[start:end]

            if self.count_tokens(section) <= self.chunk_size:
                chunks.append(section.strip())
                continue

            heading_match = self._HEADING_RE.match(section)
            if heading_match is None:
                continue  # 理论不可达: 章节起点必为标题
            heading = heading_match.group(0).strip()
            heading_tokens = self.count_tokens(heading)
            # 为拼接用的换行 "\n" 预留 1 token,保证「标题 + 换行 + 正文」不超 chunk_size
            inner_size = self.chunk_size - heading_tokens - 1
            if inner_size < 1:
                chunks.append(heading)
                continue
            inner_overlap = min(self.chunk_overlap, inner_size - 1)
            sub_splitter = RecursiveSplitter(
                chunk_size=inner_size, chunk_overlap=inner_overlap
            )
            sub_chunks = sub_splitter.split_text(section[heading_match.end() :])
            if not sub_chunks:
                chunks.append(heading)
                continue
            chunks.append((heading + "\n" + sub_chunks[0]).strip())
            chunks.extend((heading + "\n" + c).strip() for c in sub_chunks[1:])

        return chunks

    def _in_fence(self, pos: int, text: str) -> bool:
        """判断 pos 是否落在两两配对的 ``` 围栏之间。"""
        fence_starts = [m.start() for m in self._FENCE_RE.finditer(text)]
        for i in range(0, len(fence_starts) - 1, 2):
            if fence_starts[i] <= pos <= fence_starts[i + 1]:
                return True
        return False


# 分块策略注册表: create_splitter / rag_ingest 共用同一份可用集合
_STRATEGIES: dict[str, type[TextSplitter]] = {
    "fixed": FixedSizeSplitter,
    "markdown": MarkdownSplitter,
    "recursive": RecursiveSplitter,
    "sentence": SentenceSplitter,
}

# 供工具层展示合法取值(顺序即文档展示顺序)
SPLITTER_CHOICES: tuple[str, ...] = tuple(_STRATEGIES)


def create_splitter(
    strategy: str,
    chunk_size: int = 256,
    chunk_overlap: int = 50,
) -> TextSplitter:
    """创建指定策略的分块器。

    Args:
        strategy: 分块策略名: "recursive"(默认) | "fixed" | "sentence"
            | "markdown"。不区分大小写。
        chunk_size: 每个块的最大 token 数。
        chunk_overlap: 相邻块的重叠 token 数。

    Returns:
        对应策略的 TextSplitter 实例。

    Raises:
        ValueError: 未知策略名。
    """
    name = (strategy or "recursive").strip().lower()
    cls = _STRATEGIES.get(name)
    if cls is None:
        raise ValueError(
            f"未知分块策略: {strategy!r}, 可选: {', '.join(SPLITTER_CHOICES)}"
        )
    return cls(chunk_size=chunk_size, chunk_overlap=chunk_overlap)


def split_documents(
    documents: list[Document],
    chunk_size: int = 256,
    chunk_overlap: int = 50,
    splitter: str = "recursive",
) -> list[Chunk]:
    """将文档列表拆分为更小的块。

    Args:
        documents: 文档列表
        chunk_size: 每个块的最大 token 数
        chunk_overlap: 相邻块的重叠 token 数
        splitter: 分块策略,见 create_splitter。默认为 recursive。

    Returns:
        拆分后的 Chunk 列表
    """
    name = (splitter or "recursive").strip().lower()
    splitter_obj = create_splitter(name, chunk_size, chunk_overlap)
    return splitter_obj.split_documents(documents)
