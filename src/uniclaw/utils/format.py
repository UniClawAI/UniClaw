"""格式化工具函数模块

提供统一的参数格式化、文本显示等工具函数。
"""

import json
import re
from uniclaw.utils.message import MessageRole, extract_text


def parse_json_from_llm(text: str) -> dict | None:
    """从 LLM 返回的文本中解析 JSON。

    支持以下格式:
    1. 纯 JSON 字符串
    2. markdown 代码块中的 JSON (```json ... ```)
    3. 普通代码块中的 JSON (``` ... ```)

    Args:
        text: LLM 返回的文本

    Returns:
        解析后的 dict,失败返回 None
    """
    if not text or not isinstance(text, str):
        return None

    text = text.strip()

    # 1. 尝试直接解析纯 JSON
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. 尝试从 markdown 代码块中提取 JSON
    # 匹配 ```json ... ``` 或 ``` ... ```
    patterns = [
        r"```json\s*\n?(.*?)\n?\s*```",  # ```json ... ```
        r"```\s*\n?(.*?)\n?\s*```",  # ``` ... ```
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
            try:
                result = json.loads(json_str)
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, ValueError):
                continue

    # 3. 尝试提取第一个 { } 包围的 JSON
    brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group())
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def sanitize_progress_line(line: str) -> str:
    """处理包含回车符的进度条行。

    进度条输出常用 \\r 覆盖同一行来刷新,捕获后会拼成一长串。
    按 \\n 拆分后,对每段取最后一个 \\r 之后的内容(即最终帧),
    再重新拼接。

    Args:
        line: 原始输出行

    Returns:
        清理后的文本
    """
    # \r\n 视为普通换行,先统一为 \n
    line = line.replace("\r\n", "\n")
    parts = line.split("\n")
    cleaned = []
    for part in parts:
        # 剩余的独立 \r 是进度条回车,取最后一帧
        if "\r" in part:
            part = part.rsplit("\r", maxsplit=1)[-1]
        cleaned.append(part.strip())
    return "\n".join(cleaned)


def format_args_for_display(
    args: dict, max_length: int = 100, separator: str = ", "
) -> str:
    """格式化参数字典为显示字符串,处理多行和超长情况。

    Args:
        args: 参数字典
        max_length: 单个参数值的最大显示长度,默认 100 字符
        separator: 参数之间的分隔符,默认 ", "

    Returns:
        格式化后的参数字符串,单个参数值超过 max_length 字符时截断并添加"..."
    """
    if not args:
        return ""

    # 生成参数列表,对每个参数值进行处理
    formatted_args = []
    for k, v in args.items():
        # 将值转换为字符串
        v_str = str(v)

        # 标记是否需要添加省略号
        needs_ellipsis = False
        omitted_chars = 0

        # 如果值包含换行符(多行),只取第一行并标记需要省略号
        if "\n" in v_str:
            lines = v_str.split("\n")
            omitted_chars = len(v_str) - len(lines[0])
            v_str = lines[0]
            needs_ellipsis = True

        # 检查长度是否超过指定最大长度
        if len(v_str) > max_length:
            omitted_chars += len(v_str) - max_length
            v_str = v_str[:max_length]
            needs_ellipsis = True

        # 如果需要,添加省略号及省略的字符数
        if needs_ellipsis:
            v_str += f"...(省略{omitted_chars}字符)"

        formatted_args.append(f"{k}={v_str}")

    return separator.join(formatted_args)
