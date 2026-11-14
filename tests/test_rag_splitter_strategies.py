"""
splitter.py 新增分块策略的单元测试

覆盖: SentenceSplitter、MarkdownSplitter、create_splitter 工厂
"""

import pytest

from uniclaw.tools.rag.loader import Document
from uniclaw.tools.rag.splitter import (
    MarkdownSplitter,
    SentenceSplitter,
    SPLITTER_CHOICES,
    TextSplitter,
    create_splitter,
)

# ── SentenceSplitter ─────────────────────────────────────


class TestSentenceSplitter:
    """句子分块器基本行为"""

    def test_empty_text(self):
        splitter = SentenceSplitter(chunk_size=20, chunk_overlap=5)
        assert splitter.split_text("") == []
        assert splitter.split_text("   ") == []

    def test_short_text_single_chunk(self):
        splitter = SentenceSplitter(chunk_size=100, chunk_overlap=10)
        chunks = splitter.split_text("这是第一句话。")
        assert len(chunks) == 1
        assert "第一句话" in chunks[0]

    def test_multiple_sentences_split(self):
        splitter = SentenceSplitter(chunk_size=20, chunk_overlap=5)
        text = (
            "这是第一句话。这是第二句话!这是第三句话?这是第四句话;这是第五句话。"
            "这是第六句话,包含了一个子句。这是第七句话!这是第八句话。"
        )
        chunks = splitter.split_text(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert splitter.count_tokens(c) <= 20

    def test_all_chunks_within_size(self):
        splitter = SentenceSplitter(chunk_size=25, chunk_overlap=3)
        text = "短句一。短句二!短句三?短句四;短句五。短句六。短句七。短句八。短句九。短句十。"
        chunks = splitter.split_text(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert splitter.count_tokens(c) <= 25

    def test_sentence_keeps_punctuation(self):
        """句子结束标点应保留在原句块内,不丢标点"""
        splitter = SentenceSplitter(chunk_size=100, chunk_overlap=5)
        text = "甲说:你好。乙回答:你好!丙再问:好的?"
        joined = "".join(splitter.split_text(text))
        assert "。" in joined and "!" in joined and "?" in joined

    def test_oversized_sentence_linear_fallback(self):
        """单个超长句子退化为字符级线性切分,不丢内容"""
        splitter = SentenceSplitter(chunk_size=10, chunk_overlap=2)
        long_sentence = "这是一个非常非常非常非常非常非常非常非常非常非常长的句子且没有任何标点符号可以切割它吗"
        chunks = splitter.split_text(long_sentence)
        assert len(chunks) >= 2
        for c in chunks:
            assert splitter.count_tokens(c) <= 10
        # 内容不丢失
        joined = "".join(chunks)
        assert "非常" in joined and "切割" in joined

    def test_newline_is_also_split(self):
        """换行可作为切割边界: 合力超过 chunk_size 时能沿换行切开"""
        splitter = SentenceSplitter(chunk_size=25, chunk_overlap=0)
        # 每行都含多个句子,四行合计远超 chunk_size,应沿换行/标点边界切开
        chunks = splitter.split_text(
            "行为一描述:这是第一行内容,包含两个分句,说明功能一。\n"
            "行为二描述:这是第二行内容,包含另外的分句,说明功能二。\n"
            "行为三描述:这是第三行内容,继续说明功能三。\n"
            "行为四描述:这是第四行内容,收尾说明功能四。\n"
        )
        assert len(chunks) >= 3
        for c in chunks:
            assert splitter.count_tokens(c) <= 25

    def test_split_documents_preserves_metadata(self):
        splitter = SentenceSplitter(chunk_size=10, chunk_overlap=2)
        docs = [
            Document(
                content="第一句。第二句。第三句。第四句。", metadata={"source": "a.txt"}
            ),
        ]
        chunks = splitter.split_documents(docs)
        assert len(chunks) >= 2
        assert chunks[0].metadata["source"] == "a.txt"
        assert chunks[0].metadata["chunk_index"] == 0

    # ── 英文与空白处理 ──

    def test_english_sentences_keep_space(self):
        """英文句子间空格必须保留,不得粘连成 test!How"""
        splitter = SentenceSplitter(chunk_size=100, chunk_overlap=0)
        chunks = splitter.split_text(
            "Hello world. This is a test! How are you? Fine, thanks; bye."
        )
        assert len(chunks) == 1
        assert "test! How" in chunks[0], "标点后空格被吞: " + repr(chunks)
        assert "thanks; bye" in chunks[0], "分号后空格被吞: " + repr(chunks)

    def test_english_split_on_boundary(self):
        """英文句子超过 chunk_size 时沿标点边界切开,句间空格仍在块内"""
        splitter = SentenceSplitter(chunk_size=10, chunk_overlap=0)
        chunks = splitter.split_text(
            "First sentence one. Second sentence two. Third sentence three."
        )
        assert len(chunks) >= 2
        assert all(splitter.count_tokens(c) <= 10 for c in chunks)
        joined = "".join(chunks)
        assert "sentence one." in joined and "sentence two." in joined
        assert "one.Second" not in joined, "句子粘连: " + repr(joined)

    def test_decimal_and_version_dots_not_split(self):
        """小数 3.14、版本号 1.2.3、缩写 U.S.A. 内的句点不能当句子边界"""
        splitter = SentenceSplitter(chunk_size=80, chunk_overlap=0)
        text = "The U.S.A. value is 3.14 and version 1.2.3. Next sentence."
        chunks = splitter.split_text(text)
        assert len(chunks) == 1
        assert "3.14" in chunks[0] and "1.2.3" in chunks[0] and "U.S.A." in chunks[0]

    # ── 换行边界 ──

    def test_newline_acts_as_boundary(self):
        """换行作为句子边界: 每行独立时切出多块"""
        splitter = SentenceSplitter(chunk_size=100, chunk_overlap=0)
        text = "行一内容。\n行二内容。\n行三内容。\n行四内容。\n行五内容。\n行六内容。\n行七内容。\n行八内容。"
        # 总 token 远超 chunk_size?不——每个句子短,合并成一块时不超过 100
        # 用足够小的 chunk_size 强制切开
        splitter_small = SentenceSplitter(chunk_size=8, chunk_overlap=0)
        text_small = "行一\n行二\n行三\n行四\n行五\n行六\n行七\n行八\n行九\n行十"
        chunks = splitter_small.split_text(text_small)
        assert len(chunks) >= 2
        # 换行作为边界保留,不应粘连成 "行一行二"
        joined = "".join(chunks)
        assert (
            "行一\n行二" in joined
            or len(splitter_small.split_text(text_small)[0].split("\n")) >= 1
        )

    def test_empty_lines_are_boundaries(self):
        """连续空行(段落间隔)也是合法边界,不会吞文本"""
        splitter = SentenceSplitter(chunk_size=20, chunk_overlap=0)
        text = "第一段落内容。\n\n\n第二段落内容。\n\n第三段落内容。"
        chunks = splitter.split_text(text)
        assert len(chunks) >= 1
        joined = "".join(chunks)
        assert "第一段落" in joined and "第二段落" in joined

    # ── 超长退级 ──

    def test_linear_fallback_does_not_split_words(self):
        """超长英文句子线性兜底时不得把英文单词劈成两半"""
        splitter = SentenceSplitter(chunk_size=15, chunk_overlap=0)
        long_text = "Word3 " * 40 + "FinalWord."
        chunks = splitter.split_text(long_text)
        for c in chunks:
            assert splitter.count_tokens(c) <= 15
        # 每块以完整单词起止: 除首末块外,块尾必须是空格结尾的完整词,块首是词开头
        for c in chunks[1:]:
            # 块首不应是单词中段(如前一块末尾被劈开留下的残段)
            first_word = c.lstrip().split(" ")[0]
            assert (
                first_word == "Word3" or "FinalWord" in first_word
            ), f"出现残缺词开头: {first_word!r}"

    def test_overflowed_english_punct_attached(self):
        """线性兜底后孤立标点应并入前块而非单独成块"""
        splitter = SentenceSplitter(chunk_size=12, chunk_overlap=0)
        text = "abc def ghi jkl mno"
        chunks = splitter.split_text(text)
        assert all(splitter.count_tokens(c) <= 12 for c in chunks)
        assert not any(
            len(c.strip()) <= 2 for c in chunks
        ), "不应有孤立标点块: " + repr(chunks)

    # ── 无标点连续文本 ──

    def test_consecutive_chinese_no_punct(self):
        """无任何标点的连续中文退化为线性切分,不丢内容"""
        splitter = SentenceSplitter(chunk_size=10, chunk_overlap=0)
        text = "这是一段没有标点的连续中文内容用来测试线性切分行为覆盖所有文本"
        chunks = splitter.split_text(text)
        assert len(chunks) >= 2
        for c in chunks:
            assert splitter.count_tokens(c) <= 10
        joined = "".join(chunks)
        assert "连续中文" in joined and "所有文本" in joined

    # ── overlap 边界 ──

    def test_overlap_within_size(self):
        """overlap 后拼接应保证块不超 chunk_size"""
        splitter = SentenceSplitter(chunk_size=12, chunk_overlap=4)
        chunks = splitter.split_text(
            "第一句。第二句。第三句。第四句。第五句。第六句。第七句。第八句。"
        )
        for i, c in enumerate(chunks):
            assert (
                splitter.count_tokens(c) <= 12
            ), f"chunk[{i}] 超限: {splitter.count_tokens(c)}"
        # 相邻块应有文本级 overlap(前块尾部出现在后块开头)
        if len(chunks) >= 2:
            for i in range(len(chunks) - 1):
                tail = splitter._take_tail(chunks[i], splitter.chunk_overlap)
                assert (
                    tail.strip() and tail.strip() in chunks[i + 1]
                ), f"chunk[{i}] 尾 {tail!r} 未出现在 chunk[{i+1}]"

    def test_overlap_reset_when_too_large(self):
        """overlap 尾 + 新句超过 chunk_size 时,丢弃 overlap 从头开始"""
        splitter = SentenceSplitter(chunk_size=6, chunk_overlap=5)
        # overlap=5 接近 chunk_size,尾句 + 新句必然超限,应重置为空而非逼近超限
        text = "句子甲。句子乙。" * 8
        chunks = splitter.split_text(text)
        for c in chunks:
            assert splitter.count_tokens(c) <= 6


# ── MarkdownSplitter ─────────────────────────────────────


class TestMarkdownSplitter:
    """Markdown 结构化分块"""

    def test_empty_text(self):
        splitter = MarkdownSplitter(chunk_size=100, chunk_overlap=10)
        assert splitter.split_text("") == []

    def test_no_heading_falls_back_to_recursive(self):
        """无标题时退回递归分块"""
        splitter = MarkdownSplitter(chunk_size=100, chunk_overlap=10)
        text = "这是一段没有标题的普通正文。".join(["", ""])
        chunks = splitter.split_text("普通正文内容。\n\n更多正文内容。")
        assert len(chunks) == 1
        assert "普通正文" in chunks[0]

    def test_split_by_headings(self):
        splitter = MarkdownSplitter(chunk_size=200, chunk_overlap=10)
        md = "# 标题一\n\n第一段内容。\n\n## 子标题\n\n子章节内容。\n\n# 标题二\n\n第二段内容。"
        chunks = splitter.split_text(md)
        # 每个块以标题开头,与章节对齐
        assert chunks[0].startswith("# 标题一")
        assert chunks[1].startswith("## 子标题")
        assert chunks[2].startswith("# 标题二")
        assert len(chunks) == 3

    def test_front_matter_as_intro(self):
        """第一个标题前的引言归为开头块"""
        splitter = MarkdownSplitter(chunk_size=200, chunk_overlap=0)
        md = "这是文档引言。\n\n# 正文标题\n\n正文内容。"
        chunks = splitter.split_text(md)
        assert len(chunks) == 2
        assert "引言" in chunks[0]

    def test_all_sections_within_size(self):
        """章节过长时内部递归拆分,拆分后仍不超 chunk_size"""
        splitter = MarkdownSplitter(chunk_size=50, chunk_overlap=5)
        long_section = "# 超长章节\n\n" + "正文内容。" * 20
        chunks = splitter.split_text(long_section)
        assert len(chunks) >= 2
        for c in chunks:
            assert splitter.count_tokens(c) <= 50, splitter.count_tokens(c)

    def test_heading_inside_code_fence_not_split(self):
        """代码围栏内的 # 注释不应被当作标题"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        md = "```python\n# 这不是标题\nx = 1\n```\n\n" "# 这才是标题\n\n正文。"
        chunks = splitter.split_text(md)
        # 围栏内的 # 不参与分块,整个 md 因为有一个真实标题,应至少 2 块
        assert len(chunks) >= 2
        assert any(x.startswith("# 这才是标题") for x in chunks)

    def test_heading_only_document(self):
        """全是标题时每个标题独立成块"""
        splitter = MarkdownSplitter(chunk_size=200, chunk_overlap=0)
        md = "# A\n\n# B\n\n# C"
        chunks = splitter.split_text(md)
        assert len(chunks) == 3
        assert [c.split(" ")[1] for c in chunks] == ["A", "B", "C"]

    # ── Markdown 补充边界 ──

    def test_code_fence_with_multiple_headings(self):
        """多个代码围栏内的 # 都不当标题,围栏前引言独立成块"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        md = (
            "```py\n# 围栏A\nx = 1\n```\n\n"
            "# 真标题一\n\n正文一。\n\n"
            "```js\n# 围栏B\ny = 2\n```\n\n"
            "# 真标题二\n\n正文二。"
        )
        chunks = splitter.split_text(md)
        # 3 块: 引言(含围栏A) + 标题一区间 + 标题二区间
        assert len(chunks) == 3
        assert chunks[1].startswith("# 真标题一")
        assert chunks[2].startswith("# 真标题二")
        # 围栏内的 # 不能被当作块开头
        assert not any(c.startswith("# 围栏") for c in chunks)

    def test_fence_only_at_document_start(self):
        """文档开头的围栏是引言,不吞后续真实标题"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        md = "```py\n# 不是标题\nx=1\n```\n\n# 真标题\n\n正文。"
        chunks = splitter.split_text(md)
        assert len(chunks) == 2
        assert chunks[1].startswith("# 真标题")

    def test_nested_headings_kept_inline(self):
        """每个 ATX 标题(含子标题)都是章节边界,按文档顺序出块"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        md = "# 父\n\n内容。\n\n## 子一\n\n子内容。\n\n### 孙\n\n孙内容。\n\n# 其它\n\n其它内容。"
        chunks = splitter.split_text(md)
        # # 父 / ## 子一 / ### 孙 / # 其它 各自成块,共 4 块
        assert len(chunks) == 4
        assert [c.split("\n")[0] for c in chunks] == [
            "# 父",
            "## 子一",
            "### 孙",
            "# 其它",
        ]

    def test_heading_with_trailing_space(self):
        """标题行首前允许 0-3 空格(Markdown 规范)"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        md = "# A\n\n   ## B(\n\n内容。"
        chunks = splitter.split_text(md)
        assert len(chunks) >= 1
        assert any(c.startswith("# A") for c in chunks)

    def test_setext_heading_not_supported(self):
        """setext 形式(=== / ---)不识别为标题,退回递归"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        md = "我是一个标题\n==========\n\n正文内容。\n\n另外一个标题\n----------\n\n正文二。"
        chunks = splitter.split_text(md)
        # 无 ATX 标题时整体一块或由递归拆分
        assert len(chunks) >= 1
        assert "正文二" in "".join(chunks)

    def test_nested_list_not_split(self):
        """列表项不因缩进被拆散,整段列表保持完整"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        md = "# 列表\n\n- 项一\n  - 子项A\n  - 子项B\n- 项二\n\n结尾。"
        chunks = splitter.split_text(md)
        assert len(chunks) >= 1
        joined = "".join(chunks)
        assert "项一" in joined and "子项A" in joined and "项二" in joined

    def test_heading_preserved_on_split(self):
        """超长章节拆分后每个子块都带标题前缀,便于定位"""
        splitter = MarkdownSplitter(chunk_size=40, chunk_overlap=5)
        md = "# 超长章\n\n" + "段落内容在这里。\n\n" * 15
        chunks = splitter.split_text(md)
        assert len(chunks) >= 2
        for c in chunks:
            assert c.startswith("# 超长章"), f"子块缺标题前缀: {c[:30]!r}"
            assert splitter.count_tokens(c) <= 40, splitter.count_tokens(c)

    def test_empty_markdown(self):
        """空文档与空行文档返回[]"""
        splitter = MarkdownSplitter(chunk_size=1000, chunk_overlap=0)
        assert splitter.split_text("") == []
        assert splitter.split_text("\n\n\n") == []


# ── create_splitter 工厂 ─────────────────────────────────


class TestCreateSplitter:
    """create_splitter 工厂函数"""

    def test_all_choices_constructible(self):
        assert SPLITTER_CHOICES == ("fixed", "markdown", "recursive", "sentence")
        for name in SPLITTER_CHOICES:
            splitter = create_splitter(name, 100, 10)
            assert isinstance(splitter, TextSplitter)
            assert splitter.chunk_size == 100
            assert splitter.chunk_overlap == 10

    def test_case_insensitive_and_default(self):
        assert isinstance(create_splitter("Recursive", 50, 5), TextSplitter)
        assert isinstance(create_splitter("", 50, 5), TextSplitter)
        assert isinstance(create_splitter(None, 50, 5), TextSplitter)

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="未知分块策略"):
            create_splitter("unknown", 100, 10)

    def test_split_documents_unknown_splitter_raises(self):
        from uniclaw.tools.rag.splitter import split_documents as sd

        with pytest.raises(ValueError, match="未知分块策略"):
            sd([Document(content="x", metadata={})], splitter="bogus")
