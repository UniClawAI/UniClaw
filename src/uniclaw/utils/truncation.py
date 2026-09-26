def truncate_text(text: str, max_tokens: int = 10000, keep_ratio: float = 0.8) -> str:
    """
    对过长的文本内容进行截断操作

    保留文本的前面和后面部分,在中间显示被截断的 token 数信息。

    Args:
        text (str): 需要截断的原始文本内容
        max_tokens (int): 最大允许 token 数,默认为 10000 tokens
        keep_ratio (float): 保留比例,前后部分各占此值的一半。默认为0.8(即前面40%,后面40%,总共80%)

    Returns:
        str: 截断后的文本,格式为"前面部分...[截断了X个tokens]...后面部分"
             如果文本未超过 max_tokens,则返回原文本
    """
    from .tokens import count_tokens, slice_by_tokens

    if not text:
        return text

    total_tokens = count_tokens(text)

    # 如果文本 token 数未超过限制,直接返回原文本
    if total_tokens <= max_tokens:
        return text

    # 按 token 精确切片(钳制在总 token 一半以内,避免 keep_ratio 过大时前后段重叠)
    keep_tokens_per_part = max(
        0, min(int(max_tokens * keep_ratio / 2), total_tokens // 2)
    )
    front_text = slice_by_tokens(text, keep_tokens_per_part, from_end=False)
    back_text = slice_by_tokens(text, keep_tokens_per_part, from_end=True)

    # 被截断的区域即前后段之间,直接对它计数(分段计数再相减会因分词边界产生负数)
    middle = text[len(front_text) : len(text) - len(back_text)]
    truncated_tokens = count_tokens(middle)

    # 构建截断提示信息
    truncation_info = f"...[截断了{truncated_tokens}个tokens]..."

    # 组合最终结果
    result = f"{front_text}{truncation_info}{back_text}"

    return result


def truncate_text_by_lines(
    text: str, max_tokens: int = 10000, keep_ratio: float = 0.8
) -> str:
    """
    对过长的文本内容按 token 数进行截断操作

    当文本超过最大 token 数限制时,保留前面和后面的部分,在中间显示被截断的行数和 token 数信息。
    截断点尽量对齐到行边界;若切片内没有行边界(如整段只有一行超长文本),
    或对齐会导致该片段的实质内容被全部丢弃,则退化为保留半行,保证前后两段仍有内容。

    Args:
        text (str): 需要截断的原始文本内容
        max_tokens (int): 最大允许 token 数,默认为 10000 tokens
        keep_ratio (float): 保留比例,前后部分各占此值的一半。默认为0.8(即前面40%,后面40%,总共80%)

    Returns:
        str: 截断后的文本,格式为"前面部分行\\n...[截断了X行,Y个tokens]...\\n后面部分行"。
             X 为内容有缺失的行数(被截掉一半的行也计入),Y 为被截掉的 token 数。
             如果文本 token 数未超过 max_tokens,则返回原文本
    """
    from .tokens import count_tokens, slice_by_tokens

    if not text:
        return text

    total_tokens = count_tokens(text)

    # 如果文本 token 数未超过限制,直接返回原文本
    if total_tokens <= max_tokens:
        return text

    # 按 token 精确切片前后部分(钳制在总 token 一半以内,避免 keep_ratio 过大时前后段重叠)
    keep_tokens_per_part = max(
        0, min(int(max_tokens * keep_ratio / 2), total_tokens // 2)
    )

    # 前部分:精确截取 N 个 token,再对齐到行尾(丢掉尾部半行)
    front_text = slice_by_tokens(text, keep_tokens_per_part, from_end=False)
    last_newline = front_text.rfind("\n")
    if last_newline >= 0:
        aligned = front_text[: last_newline + 1]
        # 对齐后只剩空白行而实质内容在半行里时保留半行,避免前部预算被浪费
        if aligned.strip() or not front_text.strip():
            front_text = aligned

    # 后部分:精确截取 N 个 token,再对齐到行首(丢掉开头半行)
    back_text = slice_by_tokens(text, keep_tokens_per_part, from_end=True)
    if (
        back_text
        and len(back_text) < len(text)
        and text[len(text) - len(back_text) - 1] != "\n"
    ):
        # 切片起点落在行中间才需要对齐;恰好从行首开始时首行是完整的,直接保留
        first_newline = back_text.find("\n")
        # 对齐后仍有内容才丢半行,否则整体保留,避免尾部整段丢失
        if 0 <= first_newline < len(back_text) - 1:
            back_text = back_text[first_newline + 1 :]

    # 中间被截掉的区域即前后段之间(前后段分别是原文的前缀/后缀)
    middle = text[len(front_text) : len(text) - len(back_text)]
    if middle:
        # 行数按"内容有缺失的行"统计:每个换行符对应一行,末尾不足一行再补一行
        truncated_lines = middle.count("\n") + (0 if middle.endswith("\n") else 1)
        truncated_tokens = count_tokens(middle)
    else:
        truncated_lines = 0
        truncated_tokens = 0

    # 构建截断提示信息
    truncation_info = f"...[截断了{truncated_lines}行,{truncated_tokens}个tokens]..."

    # 组合最终结果(保留前部行尾空白;前部以换行结尾时不再额外补换行)
    head_sep = "" if front_text.endswith("\n") else "\n"
    result = f"{front_text}{head_sep}{truncation_info}\n{back_text}"

    return result


def truncate_text_by_tokens(text: str, max_tokens: int = 12000) -> str:
    """
    按 token 数截断文本,若发生截断则在末尾标注被截断的 token 数。

    与 truncate_text 不同,本函数只保留开头部分,并在末尾追加
    "...[已截断 N 个tokens]..." 提示,让调用方知道还有多少内容没读到。
    适合工具返回内容受上下文窗口约束的场景。

    Args:
        text (str): 需要截断的原始文本内容
        max_tokens (int): 允许保留的最大 token 数,默认为 12000

    Returns:
        str: 截断后的文本,末尾追加 "...[已截断 N 个tokens]..." 提示。
             若文本为空返回原文本;若未超过 max_tokens 返回原文本。
    """
    from .tokens import count_tokens, slice_by_tokens

    if not text:
        return text
    if max_tokens <= 0:
        return f"[已截断 {count_tokens(text)} 个tokens]"
    if count_tokens(text) <= max_tokens:
        return text
    body = slice_by_tokens(text, max_tokens, from_end=False)
    cut = count_tokens(text) - count_tokens(body)
    return f"{body}\n\n...[已截断 {cut} 个tokens]..."
