import difflib
from pathlib import Path
from uniclaw.tools.base import tool
from uniclaw.utils.constants import TOOL_ERROR


def _read_preserving_newlines(p: Path, encoding: str = "utf-8") -> str:
    with p.open(encoding=encoding, errors="replace", newline="") as f:
        return f.read()


# ── Diff helpers ──────────────────────────────────────────────────────────


def generate_unified_diff(
    old: str, new: str, filename: str, context_lines: int = 3
) -> str:
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=context_lines,
    )
    return "".join(diff)


# ── Read ─────────────────────────────────────────────────────────────────
@tool
def Read(
    file_path: str, limit: int = None, offset: int = None, encoding: str = "utf-8"
) -> str:
    """
    读取文本文件内容并返回带行号的文本。只能读取文本文件,不能读取图片、音频、视频等非文本文件。

    对于大文件,应分批读取: 先读取前一部分(如 limit=200),再通过 offset 继续读取后续部分,
    避免一次读取过多内容导致输出截断或上下文溢出。

    Args:
        file_path: 要读取的文件路径
        limit: 可选,限制读取的行数。建议大文件设置为 100~300 行分批读取。如果未指定,则读取从offset开始的所有行
        offset: 可选,起始行偏移量(从0开始)。默认为0
        encoding: 可选,文件编码格式。默认为"utf-8"

    Returns:
        str: 带行号的文件内容字符串,格式为"行号\t内容"。
             如果文件不存在或出错,返回错误信息字符串
    """
    p = Path(file_path)
    if not p.exists():
        return f"{TOOL_ERROR}: 文件未找到: {file_path}"
    if p.is_dir():
        return f"{TOOL_ERROR}: {file_path} 是一个目录"

    try:
        lines = _read_preserving_newlines(p, encoding).splitlines(keepends=True)
        start = offset or 0
        chunk = lines[start : start + limit] if limit else lines[start:]
        if not chunk:
            return "(空文件)"
        return "".join(f"{start + i + 1:6}\t{l}" for i, l in enumerate(chunk))
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── Write ─────────────────────────────────────────────────────────────────
@tool
def Write(file_path: str, content: str) -> str:
    """
    写入文件内容,支持创建新文件或更新现有文件。

    该函数会将指定内容写入文件,如果文件不存在则创建新文件并返回创建信息；
    如果文件已存在则比较新旧内容的差异,并返回差异报告。函数会自动创建
    必要的父目录,并使用UTF-8编码保存文件。

    Args:
        file_path (str): 要写入的文件路径。如果父目录不存在会自动创建。
        content (str): 要写入的文件内容字符串。

    Returns:
        str: 操作结果信息。可能的返回值包括:
             - 创建新文件时:返回 "已创建 {file_path} ({lc} 行)",包含行数信息
             - 文件无变化时:返回 "{file_path} 无变化"
             - 文件更新时:返回 "文件已更新 — {file_path}:" 后跟差异报告
             - 发生错误时:返回 "错误:{e}",包含具体错误信息
    """
    p = Path(file_path)
    try:
        # 检查文件是否存在,并读取旧内容(如果存在)
        is_new = not p.exists()
        old_content = "" if is_new else _read_preserving_newlines(p)

        # 确保父目录存在,然后写入新内容
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")

        # 根据文件是否为新创建,返回不同的结果信息
        if is_new:
            lc = content.count("\n") + (
                1 if content and not content.endswith("\n") else 0
            )
            return f"已创建 {file_path} ({lc} 行)"

        # 对于已存在的文件,生成并返回差异报告
        diff = generate_unified_diff(old_content, content, p.name)
        if not diff:
            return f"{file_path} 无变化"
        return f"文件已更新 — {file_path}:\n\n{diff}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── Edit ──────────────────────────────────────────────────────────────────
@tool
def Edit(
    file_path: str, old_string: str, new_string: str, replace_all: bool = False
) -> str:
    """
    编辑文件内容,将指定的旧字符串替换为新字符串。

    该函数支持精确的字符串替换,能够自动处理不同的换行符格式(CRLF/LF),
    并生成统一的差异报告。适用于需要精确控制文件内容修改的场景。

    Args:
        file_path (str): 要编辑的文件路径。如果文件不存在,将返回错误信息。
        old_string (str): 要被替换的原始字符串。必须与文件中的内容完全匹配,
                         包括所有前导空格/缩进和尾部换行符。
        new_string (str): 用于替换的新字符串。
        replace_all (bool): 是否替换所有匹配项。默认为False,只替换第一个匹配项。
                           如果为True且存在多个匹配项,将全部替换。

    Returns:
        str: 操作结果信息。成功时返回包含文件名的变更摘要和统一差异报告；
             失败时返回错误信息,可能的错误包括:
             - 文件未找到
             - 旧字符串未在文件中找到
             - 旧字符串出现多次但未指定replace_all
             - 其他异常错误

    Note:
        - 函数会自动检测文件的换行符格式(CRLF或LF),并在写入时保持原格式
        - 比较时会先将所有内容标准化为LF格式进行匹配
        - 生成的差异报告使用统一差异格式(unified diff)
    """
    p = Path(file_path)
    if not p.exists():
        return f"{TOOL_ERROR}: 文件未找到: {file_path}"
    try:
        # 读取文件内容并保持原始换行符格式
        content = _read_preserving_newlines(p)

        # 检测文件的换行符格式,判断是否为纯CRLF格式
        crlf_count = content.count("\r\n")
        lf_count = content.count("\n")
        is_pure_crlf = crlf_count > 0 and crlf_count == lf_count

        # 将所有内容标准化为LF格式以便进行精确匹配
        content_norm = content.replace("\r\n", "\n")
        old_norm = old_string.replace("\r\n", "\n")
        new_norm = new_string.replace("\r\n", "\n")

        # 统计匹配次数并进行验证
        count = content_norm.count(old_norm)
        if count == 0:
            return (
                f"{TOOL_ERROR}: 在文件中未找到 old_string。请确保完全匹配,"
                "包括所有精确的前导空格/缩进和尾随换行符。"
            )
        if count > 1 and not replace_all:
            return (
                f"{TOOL_ERROR}: old_string 出现了 {count} 次。"
                "请提供更多上下文以使其唯一,或使用 replace_all=true。"
            )

        # 执行替换操作
        if replace_all:
            new_content_norm = content_norm.replace(old_norm, new_norm)
        else:
            new_content_norm = content_norm.replace(old_norm, new_norm, 1)

        # 根据原始文件格式恢复相应的换行符格式
        if is_pure_crlf:
            final_content = new_content_norm.replace("\n", "\r\n")
            old_content_final = content
        else:
            final_content = new_content_norm
            old_content_final = content_norm

        # 写入文件并生成差异报告
        p.write_text(final_content, encoding="utf-8", newline="")
        diff = generate_unified_diff(old_content_final, final_content, p.name)
        return f"已应用更改到 {p.name}:\n\n{diff}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── Glob ──────────────────────────────────────────────────────────────────
@tool
def Glob(pattern: str, path: str) -> str:
    """
    根据通配符模式搜索匹配的文件路径。

    Args:
        pattern (str): 文件匹配模式,支持通配符(如 *.txt, **/*.py 等)
        path (str): 搜索的起始目录路径。

    Returns:
        str: 匹配的文件路径列表(最多500个),每行一个路径:如果没有匹配则返回 "未找到匹配的文件"；发生错误时返回错误信息
    """
    # 确定搜索的基础目录路径

    base = Path(path)

    try:
        # 执行通配符匹配并排序结果
        matches = sorted(base.glob(pattern))
        if not matches:
            return "未找到匹配的文件"
        # 返回最多500个匹配结果
        return "\n".join(str(m) for m in matches[:500])
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


# ── ReadPDF ──────────────────────────────────────────────────────────────
@tool
def ReadPDF(file_path: str, pages: str = None, encoding: str = "utf-8") -> str:
    """
    读取 PDF 文件内容并返回文本。

    Args:
        file_path: 要读取的 PDF 文件路径
        pages: 可选,指定要读取的页码范围,格式如 "1-5" 或 "1,3,5"。如果未指定,则读取所有页
        encoding: 可选,文本编码格式。默认为 "utf-8"

    Returns:
        str: PDF 文件的文本内容,每页以 "--- 第 X 页 ---" 分隔。
             如果文件不存在或出错,返回错误信息字符串
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return f"{TOOL_ERROR}: pypdf 库未安装,请运行: uv sync"

    p = Path(file_path)
    if not p.exists():
        return f"{TOOL_ERROR}: 文件未找到: {file_path}"
    if p.is_dir():
        return f"{TOOL_ERROR}: {file_path} 是一个目录"
    if p.suffix.lower() != ".pdf":
        return f"{TOOL_ERROR}: {file_path} 不是 PDF 文件"

    try:
        reader = PdfReader(str(p))
        total_pages = len(reader.pages)

        # 解析页码范围
        page_numbers = []
        if pages:
            for part in pages.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-", 1)
                    start = max(1, int(start))
                    end = min(total_pages, int(end))
                    page_numbers.extend(range(start, end + 1))
                else:
                    page_num = int(part)
                    if 1 <= page_num <= total_pages:
                        page_numbers.append(page_num)
        else:
            page_numbers = list(range(1, total_pages + 1))

        # 提取文本
        result = []
        for page_num in page_numbers:
            page = reader.pages[page_num - 1]  # pypdf 使用 0-based 索引
            text = page.extract_text()
            if text:
                result.append(f"--- 第 {page_num} 页 ---\n{text}")

        if not result:
            return "(PDF 文件无文本内容或无法提取文本)"

        return "\n\n".join(result)
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


def get_tools() -> list:
    """获取文件系统工具列表"""
    return [Read, Write, Edit, Glob, ReadPDF]


def get_all_tools() -> list:
    """获取所有文件系统工具(无条件返回)"""
    return get_tools()
