import asyncio
import difflib
import re
import time
from pathlib import Path
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR


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
    file_path: str,
    limit: int | None = None,
    offset: int | None = None,
    encoding: str = "utf-8",
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


# ── ConvertToMarkdown ───────────────────────────────────────────────────


def _allocate_md_output(p: Path) -> Path:
    """原子分配输出 .md 文件路径,避免并发转换同名文件互相覆盖。

    以 O_CREAT|O_EXCL 创建空占位文件:若目标已存在(含并发任务的占位),
    则自动递增数字后缀,直到创建成功。返回实际分配到的路径。

    Args:
        p: 源文件路径,输出基于其同目录、同名(替换扩展名为 .md)。

    Returns:
        Path: 已成功占位的输出路径。
    """
    import os

    p.parent.mkdir(parents=True, exist_ok=True)
    out = p.with_suffix(".md")
    stem = out.stem
    counter = 1
    while True:
        try:
            fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return out
        except FileExistsError:
            out = p.parent / f"{stem}_{counter}.md"
            counter += 1


def _cleanup_placeholder(out: Path) -> None:
    """转换失败或无内容时,删除转换前创建的占位空文件。

    仅删除空文件:若占位已被真实内容覆盖(非空),说明转换成功,不误删。
    """
    try:
        if out.exists() and out.stat().st_size == 0:
            out.unlink()
    except OSError:
        pass


_DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".csv",
    ".json",
    ".xml",
    ".epub",
}


def _build_llm_client(config):
    """根据 config 构建 OpenAI 客户端供 markitdown 使用。遍历 multimodal_model_name 找到第一个 OpenAI 兼容的模型。

    Returns:
        tuple[OpenAI, str] | tuple[None, None]: (客户端, 模型名) 或 (None, None)
    """
    from uniclaw.provider.common import resolve_params
    from uniclaw.provider.openai_provider import _build_openai_client

    for model in config.multimodal_model_name:
        p = resolve_params(config, model_name=model)
        if "anthropic_api_key" in p:
            continue
        api_key = p.get("openai_api_key", "")
        api_base = p.get("openai_api_base", "")
        client = _build_openai_client(api_base, api_key, p.get("proxy_url", ""))
        return (client, p.get("model_name", model))

    return (None, None)


@tool
async def ConvertToMarkdown(file_path: str, output_path: str = "", tool_runtime: ToolRuntime = None) -> str:
    """
    将文档转换为 Markdown 格式并保存为 .md 文件。支持 PDF、DOCX、PPTX、XLSX、HTML、CSV、JSON、XML、EPUB。

    函数立即返回,转换在后台异步执行,完成后自动唤醒 AI 并返回结果。
    转换过程保留文档结构(标题、列表、表格等),输出对 LLM 友好的 Markdown 文本。
    当配置了 multimodal_model_name 时,自动提取文档中的图片并通过视觉模型生成文字描述。

    Args:
        file_path: 文档文件路径,必须是绝对路径
        output_path: 可选,.md 文件的保存路径,必须是绝对路径。为空时保存在源文件同目录下,文件名为原文件名替换为 .md 后缀(如 report.pdf → report.md)。若文件已存在则自动添加数字后缀(如 report_1.md)

    Returns:
        str: 立即返回转换任务已启动的确认信息。实际转换结果将通过唤醒机制异步返回
    """
    config = tool_runtime.config
    # -- 参数校验(立即完成) --
    try:
        from markitdown import MarkItDown  # noqa: F401 — 验证已安装
    except ImportError:
        return f"{TOOL_ERROR}: markitdown 库未安装,请运行: uv sync"

    p = Path(file_path)
    if not p.is_absolute():
        return f"{TOOL_ERROR}: file_path 必须是绝对路径: {file_path}"
    if output_path and not Path(output_path).is_absolute():
        return f"{TOOL_ERROR}: output_path 必须是绝对路径: {output_path}"
    if not p.exists():
        return f"{TOOL_ERROR}: 文件未找到: {file_path}"
    if p.is_dir():
        return f"{TOOL_ERROR}: {file_path} 是一个目录"

    suffix = p.suffix.lower()
    if suffix not in _DOCUMENT_EXTENSIONS:
        return (
            f"{TOOL_ERROR}: 不支持的格式 '{suffix}',"
            f"支持的格式: {', '.join(sorted(_DOCUMENT_EXTENSIONS))}"
        )

    # 确定输出路径(提前计算,避免异步竞争)
    if output_path:
        out = Path(output_path)
    else:
        # 原子分配:并发转换同名不同扩展名的文件(如 a.csv + a.html)时,
        # 以 O_CREAT|O_EXCL 占位,后者自动获得 _1/_2... 后缀,互不覆盖
        out = _allocate_md_output(p)

    def _do_convert() -> tuple[str, float]:
        """在线程池中执行同步转换(避免阻塞事件循环)"""
        from markitdown import MarkItDown

        llm_client, vision_model = (None, None)
        if config and config.multimodal_model_name:
            llm_client, vision_model = _build_llm_client(config)

        if llm_client and vision_model:
            md = MarkItDown(
                llm_client=llm_client,
                llm_model=vision_model,
                enable_plugins=True,
            )
        else:
            md = MarkItDown(enable_plugins=False)

        start = time.monotonic()
        result = md.convert(str(p))
        elapsed = time.monotonic() - start
        text = result.text_content.strip() if result.text_content else ""

        if not text:
            _cleanup_placeholder(out)
            return "", elapsed

        out.write_text(text, encoding="utf-8")
        return text, elapsed

    async def _convert():
        """后台执行转换,完成后唤醒 AI"""
        try:
            text, elapsed = await asyncio.to_thread(_do_convert)

            if not text:
                await _wake(f"文档 {p.name} 无文本内容或无法提取文本")
                return

            lines = text.count('\n') + 1
            chars = len(text)
            size_kb = len(text.encode('utf-8')) / 1024

            headings = []
            for m in re.finditer(r'^(#{1,6})\s+(.+)$', text, re.MULTILINE):
                offset_val = text[:m.start()].count('\n')
                headings.append((len(m.group(1)), m.group(2), offset_val))
            h1_headings = [(title, off) for level, title, off in headings if level == 1]
            h2_headings = [(title, off) for level, title, off in headings if level == 2]

            heading_info = ""
            if h1_headings:
                items = [f"{title}(offset={off})" for title, off in h1_headings]
                heading_info += f"\n一级标题({len(h1_headings)}个): {', '.join(items)}"
            if h2_headings:
                items = [f"{title}(offset={off})" for title, off in h2_headings]
                heading_info += f"\n二级标题({len(h2_headings)}个): {', '.join(items)}"

            summary = (
                f"已将 {p.name} 转换为 Markdown 格式(耗时 {elapsed:.1f}s)\n"
                f"输出文件: {out}\n"
                f"统计: {lines} 行, {chars} 字符, {size_kb:.1f} KB"
                f"{heading_info}"
            )
            await _wake(summary)
        except Exception as e:
            await _wake(f"转换 {p.name} 失败: {e}")

    async def _wake(message: str):
        from uniclaw.utils.wakeup import wake_agent
        await wake_agent(f"{SYSTEM_PREFIX}(ConvertToMarkdown) {message}", config)

    asyncio.create_task(_convert())

    return (
        f"文档 {p.name} 转换任务已启动,正在后台异步执行...\n"
        f"输出文件: {out}\n"
        f"转换完成后将自动通知,请继续处理其他任务。"
    )


def get_tools() -> list:
    """获取文件系统工具列表"""
    return [Read, Write, Edit, Glob, ConvertToMarkdown]


def get_all_tools() -> list:
    """获取所有文件系统工具(无条件返回)"""
    return [Read, Write, Edit, Glob, ConvertToMarkdown]
