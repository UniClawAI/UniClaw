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

    # 按 token 精确切片
    keep_tokens_per_part = int(max_tokens * keep_ratio / 2)
    front_text = slice_by_tokens(text, keep_tokens_per_part, from_end=False)
    back_text = slice_by_tokens(text, keep_tokens_per_part, from_end=True)

    # 计算被截断的 token 数
    truncated_tokens = total_tokens - count_tokens(front_text) - count_tokens(back_text)

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

    当文本超过最大 token 数限制时,保留前面和后面的完整行,在中间显示被截断的行数和 token 数信息。
    截断点始终在行边界处,不会截断到一行的中间。

    Args:
        text (str): 需要截断的原始文本内容
        max_tokens (int): 最大允许 token 数,默认为 10000 tokens
        keep_ratio (float): 保留比例,前后部分各占此值的一半。默认为0.8(即前面40%,后面40%,总共80%)

    Returns:
        str: 截断后的文本,格式为"前面部分行...[截断了X行,Y个tokens]...后面部分行"
             如果文本 token 数未超过 max_tokens,则返回原文本
    """
    from .tokens import count_tokens, slice_by_tokens

    if not text:
        return text

    total_tokens = count_tokens(text)

    # 如果文本 token 数未超过限制,直接返回原文本
    if total_tokens <= max_tokens:
        return text

    # 按行分割文本
    lines = text.splitlines(keepends=True)
    total_lines = len(lines)

    # 按 token 精确切片前后部分,再对齐到行边界
    keep_tokens_per_part = int(max_tokens * keep_ratio / 2)

    # 前部分:精确截取 N 个 token,然后向上对齐到行尾
    front_text = slice_by_tokens(text, keep_tokens_per_part, from_end=False)
    # 找到 front_text 中最后一个换行符,确保不截断行
    last_newline = front_text.rfind("\n")
    if last_newline > 0:
        front_text = front_text[: last_newline + 1]

    # 后部分:精确截取 N 个 token,然后向下对齐到行首
    back_text = slice_by_tokens(text, keep_tokens_per_part, from_end=True)
    first_newline = back_text.find("\n")
    if first_newline >= 0:
        back_text = back_text[first_newline + 1 :]

    # 计算被截断的行数和 token 数
    front_lines = front_text.splitlines(keepends=True)
    back_lines = back_text.splitlines(keepends=True)
    truncated_lines = total_lines - len(front_lines) - len(back_lines)
    truncated_tokens = total_tokens - count_tokens(front_text) - count_tokens(back_text)

    # 构建截断提示信息
    truncation_info = (
        f"\n...[截断了{truncated_lines}行,{truncated_tokens}个tokens]...\n"
    )

    # 组合最终结果
    result = front_text.rstrip() + truncation_info + back_text

    return result


def truncate_text_by_tokens(text: str, max_tokens: int = 12000) -> str:
    """
    按 token 数截断文本,若发生截断则在末尾标注被截断的 token 数。

    与 truncate_text 不同,本函数只保留开头部分,并在末尾追加
    "...[已截断 N 个tokens]..." 提示,让调用方知道还有多少内容没读到。
    适合工具返回内容受上下文窗口约束的场景(如 webFetch 的 max_tokens)。

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
