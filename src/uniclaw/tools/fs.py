import asyncio
import codecs
import csv
import difflib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import SYSTEM_PREFIX, TOOL_ERROR


def _read_preserving_newlines(p: Path, encoding: str = "utf-8") -> str:
    with p.open(encoding=encoding, errors="replace", newline="") as f:
        return f.read()


def _binary_file_kind(head: bytes) -> str | None:
    """检测文件头是否为二进制,返回类型描述;可作文本读取时返回 None。

    启发式: 含 NUL 字节即视为二进制(UTF-16/32 文本除外,由调用方按
    encoding/BOM 豁免),再按常见魔数给出更具体类型。
    """
    # 常见二进制魔数 → 类型说明(便于报错里直接告诉调用方该用什么工具)
    magic_map = (
        (b"\x89PNG\r\n\x1a\n", "PNG 图片"),
        (b"\xff\xd8\xff", "JPEG 图片"),
        (b"GIF87a", "GIF 图片"),
        (b"GIF89a", "GIF 图片"),
        (b"%PDF", "PDF 文档"),
        (b"PK\x03\x04", "ZIP 压缩包"),
        (b"\x1f\x8b", "GZIP 压缩包"),
        (b"RIFF", "RIFF 多媒体(WAV/AVI 等)"),
        (b"\x7fELF", "ELF 可执行文件"),
        (b"MZ", "Windows 可执行文件"),
    )
    for magic, kind in magic_map:
        if head.startswith(magic):
            return kind
    if b"\x00" in head:
        return "二进制数据(含 NUL 字节)"
    return None


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
             offset 超出文件范围时报错说明总行数(不伪装成空文件);
             limit 超过剩余行数时注明已达文件末尾。
             如果文件不存在或出错,返回错误信息字符串
    """
    p = Path(file_path)
    if not p.exists():
        return f"{TOOL_ERROR}: 文件未找到: {file_path}"
    if p.is_dir():
        return f"{TOOL_ERROR}: {file_path} 是一个目录"

    try:
        # 二进制防护: 先看文件头,避免 PNG 等被按文本解码输出乱码
        # (UTF-16/32 文本天然含 NUL,按编码豁免;BOM 检测同 read_text)
        with p.open("rb") as f:
            head = f.read(8192)
        enc_norm = (encoding or "").lower().replace("_", "-")
        is_nul_safe = enc_norm in ("utf-16", "utf-32") or (
            len(head) >= 2
            and (
                head.startswith((b"\xff\xfe", b"\xfe\xff", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"))
            )
        )
        if not is_nul_safe:
            kind = _binary_file_kind(head)
            if kind:
                # 函数内导入避免循环依赖(fs ↔ media),此处仅取工具规范名
                from uniclaw.tools.media import ReadMedia

                return (
                    f"{TOOL_ERROR}: {file_path} 是二进制文件({kind}),"
                    f"不适合文本读取。图片请用 {ReadMedia.name},其他请用专用工具"
                )

        lines = _read_preserving_newlines(p, encoding).splitlines(keepends=True)
        total = len(lines)
        # 真空文件才返回 "(空文件)";offset 超界必须明确报错,
        # 否则 60k 行的文件传 offset=200000 会伪装成空文件,误导调用方
        if total == 0:
            return "(空文件)"
        start = offset or 0
        if start < 0:
            return f"{TOOL_ERROR}: offset 不能为负数: {offset}"
        if start >= total:
            return (
                f"{TOOL_ERROR}: offset={start} 超出文件范围(共 {total} 行),"
                f"有效范围 0..{total - 1}"
            )
        chunk = lines[start : start + limit] if limit else lines[start:]
        result = "".join(f"{start + i + 1:6}\t{l}" for i, l in enumerate(chunk))
        # limit 超过剩余行数时明确说明已到末尾,避免"只返回了 N 行"被误读为文件就这么多
        remaining = total - start
        if limit and remaining < limit:
            result += f"\n(已达文件末尾,仅剩 {remaining} 行)"
        return result
    except (OSError, LookupError) as e:
        return (
            f"{TOOL_ERROR}: 读取失败 {file_path} (encoding={encoding}): "
            f"{type(e).__name__}: {e}"
        )


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
    except OSError as e:
        return f"{TOOL_ERROR}: 写入失败 {file_path}: {type(e).__name__}: {e}"


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
    except OSError as e:
        return f"{TOOL_ERROR}: 编辑失败 {file_path}: {type(e).__name__}: {e}"


# ── Glob ──────────────────────────────────────────────────────────────────

# Glob 单次返回上限: 超出时截断并在末尾标注总匹配数,禁止静默截断
GLOB_MAX_RESULTS = 500


@tool
def Glob(pattern: str, path: str) -> str:
    """
    根据通配符模式搜索匹配的文件路径。

    Args:
        pattern (str): 文件匹配模式,支持通配符(如 *.txt, **/*.py 等)
        path (str): 搜索的起始目录路径。

    Returns:
        str: 匹配的文件路径列表,每行一个路径。超过 500 条时只返回前 500 条,
             并在末尾标注「已截断,共匹配 N 条」。如果没有匹配则返回 "未找到匹配的文件";
             发生错误时返回错误信息
    """
    # 确定搜索的基础目录路径

    base = Path(path)

    if not isinstance(pattern, str) or not pattern.strip():
        return (
            f"{TOOL_ERROR}: pattern 不能为空 —— "
            f"如需列出目录下全部文件请传 '*'"
        )

    try:
        # 执行通配符匹配并排序结果
        matches = sorted(base.glob(pattern))
        if not matches:
            return "未找到匹配的文件"
        if len(matches) > GLOB_MAX_RESULTS:
            head = "\n".join(str(m) for m in matches[:GLOB_MAX_RESULTS])
            return (
                f"{head}\n\n"
                f"已截断,共匹配 {len(matches)} 条,仅显示前 {GLOB_MAX_RESULTS} 条;"
                f"请收窄 pattern 或分批查询"
            )
        return "\n".join(str(m) for m in matches)
    except ValueError as e:
        # pathlib 拒绝非法 pattern(空串/怪异语法)—— 属参数错误,给可用示例
        return (
            f"{TOOL_ERROR}: 无效的 pattern {pattern!r}: {e};"
            f"示例: '*.md', '**/*.py', 'src/**/*.txt'"
        )
    except OSError as e:
        return (
            f"{TOOL_ERROR}: 搜索失败 (pattern={pattern!r}, path={path}): "
            f"{type(e).__name__}: {e}"
        )


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


# ── 任务 2: 内容与扩展名一致性校验 ──────────────────────────────────────
# markitdown 对识别失败的文件会兜底落到 PlainText/Zip converter,把改名的
# 纯文本"转换成功"。这里先做魔数/结构校验,再对二进制格式禁止跨格式兜底,
# 保证各格式失败口径一致: 一律报"已损坏或不是有效的 X 文件"。

# OOXML(docx/xlsx/pptx)是 zip 容器,必须包含对应部件才算有效文件
_OOXML_ZIP_PARTS: dict[str, str] = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
}
# 旧版 Office 复合文档(doc/xls/ppt)的 OLE 魔数
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE_SUFFIXES = frozenset({".doc", ".xls", ".ppt"})
# 二进制/结构化格式: 转换失败时必须报错,不允许文本兜底冒充成功
_BINARY_FORMATS = frozenset(
    {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".pdf", ".epub"}
)


class _DocumentConvertError(Exception):
    """转换失败(非 bug,如文件损坏/格式不匹配),携带给用户/LLM 的报错文案。"""


def _invalid_document_error(p: Path, detail: str = "") -> str:
    """统一的"文件已损坏或不是有效的 X 文件"报错文案(任务 2)。

    Args:
        p: 源文件路径。
        detail: 可选的原始异常信息,便于定位,不只丢内部异常类名。

    Returns:
        str: 带 TOOL_ERROR 前缀的报错文案。
    """
    fmt = p.suffix.lstrip(".").upper() or "文档"
    msg = f"{TOOL_ERROR}: 文件已损坏或不是有效的 {fmt} 文件: {p}"
    if detail:
        msg = f"{msg}: {detail}"
    return msg


def _validate_document_content(p: Path) -> str | None:
    """检查文件内容与扩展名是否匹配(任务 2)。

    二进制文档格式(docx/xlsx/pptx/doc/xls/ppt/pdf/epub)先做魔数/结构校验,
    避免 markitdown 把纯文本等无关内容兜底转出来当成成功。文本格式
    (csv/html/json/xml)本身就是文本,不做硬校验。

    Args:
        p: 待检查的文档路径。

    Returns:
        str | None: 不匹配时返回带 TOOL_ERROR 前缀的报错文案;匹配返回 None。
    """
    suffix = p.suffix.lower()
    try:
        if suffix in _OOXML_ZIP_PARTS:
            with zipfile.ZipFile(p) as z:
                if _OOXML_ZIP_PARTS[suffix] not in z.namelist():
                    return _invalid_document_error(
                        p, f"缺少 OOXML 部件 {_OOXML_ZIP_PARTS[suffix]}"
                    )
        elif suffix in _OLE_SUFFIXES:
            with p.open("rb") as f:
                head = f.read(8)
            if not head.startswith(_OLE_MAGIC):
                return _invalid_document_error(p, "缺少 OLE 复合文档头")
        elif suffix == ".pdf":
            with p.open("rb") as f:
                head = f.read(5)
                # 尾部检查: 截断 PDF 没有 %%EOF,markitdown 会"照常"转出
                # 残缺正文(OCR/解析垃圾混入)且毫无警告
                try:
                    f.seek(max(p.stat().st_size - 1024, 0))
                except OSError:
                    pass
                tail = f.read()
            if head != b"%PDF-":
                return _invalid_document_error(p, "缺少 %PDF 文件头")
            if b"%%EOF" not in tail:
                return _invalid_document_error(
                    p, "文件已截断(尾部缺少 %%EOF 结束标记),内容不完整"
                )
        elif suffix == ".epub":
            with zipfile.ZipFile(p) as z:
                if "mimetype" not in z.namelist():
                    return _invalid_document_error(p, "缺少 mimetype 部件")
    except zipfile.BadZipFile as e:
        # 非 bug: 文件不是合法 zip(把 txt 改名成 docx 就会走到这)
        return _invalid_document_error(p, f"{type(e).__name__}: {e}")
    except OSError as e:
        return f"{TOOL_ERROR}: 读取失败 {p}: {type(e).__name__}: {e}"
    return None


def _looks_like_fallback_text(p: Path, text: str) -> bool:
    """判断产物是否是 markitdown 的 PlainText/Zip 兜底,而非真正的格式解析。

    二进制格式解析失败时,markitdown 会退到 ZipConverter(输出 zip 目录)或
    PlainTextConverter(把原始字节当文本),看起来像"成功"实为垃圾产物。

    Args:
        p: 源文件路径。
        text: 转换产物文本。

    Returns:
        bool: 疑似兜底产物返回 True。
    """
    if text.startswith("Content from the zip file"):
        return True
    try:
        raw = p.read_bytes()
    except OSError:
        return False
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            if raw.decode(enc).strip() == text.strip():
                return True
        except UnicodeDecodeError:
            continue
    return False


# ── 任务 1: 转义还原(PPTX 文本规范化) ──────────────────────────────────
# python-pptx 写入侧会把控制字符转义成 _xHHHH_(OOXML 约定),读取侧不解码;
# 软换行 <a:br/> 读出来是 \\v。另外拼接泄漏会在产物里留下字面 \\n / \\t。
# 只处理这类"我们自己拼接/库转义产生的"序列,引号内代码展示保持原样。

_OOXML_CTRL_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")

_QUOTE_CHARS = ('"', "'", "`")


def _restore_literal_escapes(text: str) -> str:
    """把字面 \\n / \\t 转义还原为真实换行/制表符(任务 1)。

    只处理结构换行类转义。引号内的代码展示(如 "print(\\n)"、'\\n')与
    Windows 盘符路径(C:\\new / C:\\temp)保持原样,避免误伤本来就要
    显示这些字符的内容。缩写撇号(don't / it's)不当成引号开始。

    Args:
        text: 待还原的文本。

    Returns:
        str: 还原后的文本。
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_quote: str | None = None
    while i < n:
        ch = text[i]
        if in_quote:
            # 引号内原样保留;反斜杠转义连同后一个字符一起透传
            if ch == "\\" and i + 1 < n:
                out.append(text[i : i + 2])
                i += 2
                continue
            if ch == in_quote:
                in_quote = None
            out.append(ch)
            i += 1
            continue
        if ch in _QUOTE_CHARS:
            # don't / it's: 字母+撇号+字母是缩写撇号,不是引号开始
            if (
                ch == "'"
                and i > 0
                and i + 1 < n
                and text[i - 1].isalnum()
                and text[i + 1].isalnum()
            ):
                out.append(ch)
                i += 1
                continue
            in_quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            # \\ 是转义反斜杠(要显示反斜杠+n 的两个字符),整体透传
            if text[i + 1] == "\\":
                out.append(text[i : i + 2])
                i += 2
                continue
            if text[i + 1] in ("n", "t"):
                # Windows 盘符路径 C:\new / C:\temp 不还原
                if (
                    i >= 2
                    and text[i - 1] == ":"
                    and text[i - 2].isalpha()
                ):
                    out.append(text[i : i + 2])
                    i += 2
                    continue
                out.append("\n" if text[i + 1] == "n" else "\t")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _normalize_pptx_text(text: str) -> str:
    """规范化从 PPTX 提取出的文本,把各种转义还原成真实字符(任务 1)。

    - 软换行(\\v)与 CR/LF 变体 → 真实换行
    - OOXML 控制字符转义 _xHHHH_ → 对应字符(python-pptx 写入侧产物)
    - 字面 \\n / \\t → 真实换行/制表符(拼接泄漏)

    Args:
        text: python-pptx 提取的原始文本。

    Returns:
        str: 规范化后的文本。
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\v", "\n").replace("\x85", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    text = _OOXML_CTRL_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    # _x000B_ \u7b49\u89e3\u7801\u540e\u53ef\u80fd\u4ea7\u751f\u8f6f\u6362\u884c/\u56de\u8f66,\u518d\u8d70\u4e00\u904d\u6362\u884c\u5f52\u4e00
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\v", "\n").replace("\x85", "\n")
    return _restore_literal_escapes(text)


def _pptx_table_to_markdown(table) -> str:
    """PPTX 表格 → Markdown 表格。

    表格单元格内不能放真实换行,换行压成空格;管道符转义避免撑破表格。
    合并单元格(Markdown 表格不支持列合并)在首格放内容、其余列留空,
    并在表后注明跨列情况,避免语义无声丢失。

    Args:
        table: python-pptx 的 Table 对象。

    Returns:
        str: Markdown 表格文本。
    """
    parsed_rows: list[list[str]] = []
    merge_notes: list[tuple[str, int]] = []
    for row in table.rows:
        cells: list[str] = []
        # 合并单元格: is_spanned 标记被合并覆盖的重复位置(留空占位),
        # 跨列数取合并源格的 span_width,语义在表后注明
        for cell in row.cells:
            if getattr(cell, "is_spanned", False):
                cells.append("")
                continue
            raw = cell.text if cell.text is not None else ""
            text = _normalize_pptx_text(raw)
            text = re.sub(r"\s*\n\s*", " ", text).strip()
            text = text.replace("|", "\\|")
            span = int(getattr(cell, "span_width", 1) or 1)
            if span > 1:
                merge_notes.append((text, span))
            cells.append(text)
        parsed_rows.append(cells)

    # 列数取"最宽行"与 tblGrid 声明的较大者,谁都不能让分隔行塌缩:
    # - 部分工具(WPS 等)导出的表格 tblGrid 少声明列,行内单元格数才是真实宽度
    # - 表头合并行的单元格数会少于列数,统一按最宽行补齐
    grid_cols = len(table.columns) if hasattr(table, "columns") else 0
    ncols = max([grid_cols, *(len(r) for r in parsed_rows)], default=1)

    rows_md: list[str] = []
    for i, cells in enumerate(parsed_rows):
        cells = cells + ["" for _ in range(ncols - len(cells))]
        rows_md.append("| " + " | ".join(cells) + " |")
        if i == 0:
            # 分隔行用 listcomp 构建,不用"列表/元组字面量 × 变量"的写法 ——
            # Cython 3.2.4 会把这类乘法包进字符串拼接链后错误折叠成单元素:
            # 源码运行 3 列正确,编译产物塌缩成单列分隔行。listcomp 实测编译安全。
            rows_md.append(
                "| " + " | ".join(["---" for _ in range(ncols)]) + " |"
            )
    if merge_notes:
        rows_md.append("")
        for text, span in merge_notes:
            shown = text[:40] or "(空)"
            rows_md.append(
                f'注: "{shown}" 为跨 {span} 列的合并单元格'
                f"(Markdown 表格不支持列合并,内容置于该行首列)"
            )
    return "\n".join(rows_md)


def _pptx_image_to_markdown(shape) -> str:
    """图片 → 占位 Markdown 图片(保留 alt 文本,供后续替换真实文件名)。

    Args:
        shape: python-pptx 的图片形状。

    Returns:
        str: `![alt](name.jpg)` 形式的占位文本。
    """
    alt = ""
    try:
        alt = shape._element._nvXxPr.cNvPr.attrib.get("descr", "") or ""
    except Exception:
        alt = ""
    alt = alt or getattr(shape, "name", "") or "image"
    alt = re.sub(r"[\r\n\[\]]", " ", alt)
    alt = re.sub(r"\s+", " ", alt).strip() or "image"
    name = getattr(shape, "name", "") or "image"
    filename = re.sub(r"\W", "", name) + ".jpg"
    return f"![{alt}]({filename})"


def _pptx_to_markdown(p: Path) -> str:
    """PPTX → Markdown,按文档流顺序输出标题/正文/表格/图片(任务 1)。

    markitdown 的 PptxConverter 按 (top, left) 坐标排序形状,标题占位符
    若在页面上位于表格下方就会被排到表格后面。这里每页固定按
    标题 → 正文 → 表格 → 图片 输出,保证标题永远在本页最前,文本
    走 _normalize_pptx_text 消除字面转义泄漏。

    Args:
        p: .pptx 文件路径。

    Returns:
        str: Markdown 文本。

    Raises:
        _DocumentConvertError: python-pptx 缺失或文件解析失败(非 bug)。
    """
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except ImportError as e:
        raise _DocumentConvertError(
            f"{TOOL_ERROR}: python-pptx 未安装,请运行: uv sync: {type(e).__name__}: {e}"
        )

    try:
        prs = Presentation(str(p))
    except Exception as e:
        # 非 bug: 文件损坏/不是有效 pptx
        raise _DocumentConvertError(_invalid_document_error(p, f"{type(e).__name__}: {e}"))

    def is_picture(shape) -> bool:
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            return True
        if shape.shape_type == MSO_SHAPE_TYPE.PLACEHOLDER and hasattr(shape, "image"):
            return True
        return False

    def iter_leaves(shapes):
        """按文档流顺序展平形状,组形状递归展开。"""
        for shape in shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                yield from iter_leaves(shape.shapes)
            else:
                yield shape

    slides_md: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        title_text = ""
        bodies: list[str] = []
        tables: list[str] = []
        images: list[str] = []

        for shape in iter_leaves(slide.shapes):
            if title_shape is not None and shape == title_shape:
                raw = shape.text if getattr(shape, "has_text_frame", False) else ""
                title_text = _normalize_pptx_text(raw)
                continue
            if getattr(shape, "has_table", False):
                md = _pptx_table_to_markdown(shape.table)
                if md.strip():
                    tables.append(md)
                continue
            if is_picture(shape):
                images.append(_pptx_image_to_markdown(shape))
                continue
            if getattr(shape, "has_chart", False):
                chart_md = "### Chart"
                try:
                    if shape.chart.has_title:
                        chart_md += f": {_normalize_pptx_text(shape.chart.chart_title.text_frame.text)}"
                except Exception:
                    pass
                bodies.append(chart_md)
                continue
            if getattr(shape, "has_text_frame", False):
                text = _normalize_pptx_text(shape.text)
                if text.strip():
                    bodies.append(text.strip())

        # 固定类别顺序: 标题 / 正文 / 表格 / 图片 —— 标题永远在本页最前
        parts: list[str] = [f"<!-- Slide number: {i} -->"]
        if title_text.strip():
            parts.append("# " + title_text.strip())
        if bodies:
            parts.append("\n\n".join(bodies))
        if tables:
            parts.append("\n\n".join(tables))
        if images:
            parts.append("\n\n".join(images))

        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            if notes_frame is not None:
                notes = _normalize_pptx_text(notes_frame.text).strip()
                if notes:
                    parts.append("### Notes:\n" + notes)

        slides_md.append("\n\n".join(parts))

    return "\n\n".join(slides_md)


def _decode_markup(data: bytes) -> str:
    """解码标记文档字节流,避免错误编码把正文变成乱码。

    识别顺序: BOM → 文档声明的 encoding/charset → 猜测链(UTF-8 →
    GB18030 → Big5 → EUC-KR → Shift_JIS),全失败时 UTF-8 replace 兜底。
    markitdown 的 EPUB/HTML 转换会把章节字节硬按 UTF-8 解码,GBK 章节
    因此整篇乱码 —— 自有转换路径与 webFetch 统一走这里。

    Args:
        data: 文档原始字节。

    Returns:
        str: 解码后的文本。
    """
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig")
    # UTF-32 BOM 以 UTF-16 BOM 为前缀,必须先判 UTF-32
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode("utf-32")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")

    # 声明的编码(XML prolog / meta charset)优先 —— 声明本身是 ASCII
    head = data[:512].decode("ascii", errors="ignore")
    m = re.search(r"charset\s*=\s*[\"']?([\w.:-]+)", head)
    if not m:
        m = re.search(r"encoding\s*=\s*[\"']([\w.:-]+)", head)

    candidates: list[str] = []
    if m:
        candidates.append(m.group(1))
    candidates += ["utf-8", "gb18030", "big5", "euc-kr", "shift_jis"]

    seen: set[str] = set()
    for enc in candidates:
        key = enc.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _xml_local_name(tag: str) -> tuple[str, str]:
    """拆分 ElementTree 的 {ns}local 形式标签。

    Args:
        tag: 元素标签(可能带命名空间)。

    Returns:
        tuple[str, str]: (本地名, 命名空间),无命名空间时 ns 为空串。
    """
    if tag.startswith("{"):
        ns, _, local = tag[1:].partition("}")
        return local, ns
    return tag, ""


def _epub_to_markdown(p: Path) -> str:
    """EPUB 专用转换: 解析 OPF/spine,按阅读顺序转换各章节为 Markdown。

    不走 markitdown 的 EpubConverter —— 它把章节字节硬按 UTF-8 解码,
    非 UTF-8(如 GBK)章节会整篇变乱码;这里逐章识别编码后转换。

    Args:
        p: EPUB 文件路径。

    Returns:
        str: Markdown 文本(元数据 + 各章节)。

    Raises:
        _DocumentConvertError: 结构损坏或缺关键部件(非 bug)。
    """
    from markitdown import MarkItDown, StreamInfo

    try:
        z = zipfile.ZipFile(p)
    except zipfile.BadZipFile as e:
        raise _DocumentConvertError(_invalid_document_error(p, f"{type(e).__name__}: {e}"))

    with z:
        try:
            container_xml = z.read("META-INF/container.xml")
        except KeyError as e:
            raise _DocumentConvertError(
                _invalid_document_error(p, "缺少 META-INF/container.xml")
            ) from e
        try:
            container = ElementTree.fromstring(container_xml)
        except ElementTree.ParseError as e:
            raise _DocumentConvertError(
                _invalid_document_error(p, f"container.xml 解析失败: {e}")
            ) from e

        opf_path = ""
        for el in container.iter():
            local, _ = _xml_local_name(el.tag)
            if local == "rootfile":
                opf_path = el.get("full-path", "")
                break
        if not opf_path or opf_path not in z.namelist():
            raise _DocumentConvertError(
                _invalid_document_error(p, f"container.xml 未指向有效 OPF({opf_path!r})")
            )

        try:
            opf = ElementTree.fromstring(z.read(opf_path))
        except ElementTree.ParseError as e:
            raise _DocumentConvertError(
                _invalid_document_error(p, f"OPF 解析失败: {e}")
            ) from e

        # manifest: id → href;spine: 阅读顺序的 idref 列表
        manifest: dict[str, str] = {}
        spine_ids: list[str] = []
        for el in opf.iter():
            local, _ = _xml_local_name(el.tag)
            if local == "item" and el.get("id"):
                manifest[el.get("id")] = el.get("href", "")
            elif local == "itemref" and el.get("idref"):
                spine_ids.append(el.get("idref"))

        meta_parts: list[str] = []
        for el in opf.iter():
            local, _ = _xml_local_name(el.tag)
            if local in ("title", "creator", "language", "publisher", "date"):
                text = (el.text or "").strip()
                if text:
                    meta_parts.append(f"**{local.capitalize()}:** {text}")
        if not spine_ids:
            raise _DocumentConvertError(
                _invalid_document_error(p, "OPF spine 中没有任何章节条目")
            )

        base_path = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
        md = MarkItDown(enable_plugins=False)
        chapters: list[str] = []
        for idref in spine_ids:
            href = manifest.get(idref)
            if not href:
                continue
            entry = f"{base_path}/{href}" if base_path else href
            # zip 内路径统一用 / 分隔;解压可能带 ./ 前缀
            entry = entry.lstrip("./")
            if entry not in z.namelist():
                continue
            raw = z.read(entry)
            html = _decode_markup(raw)
            result = md.convert_stream(
                io.BytesIO(html.encode("utf-8")),
                stream_info=StreamInfo(
                    mimetype="text/html", extension=".html", charset="utf-8"
                ),
            )
            text = (result.markdown or "").strip()
            if text:
                chapters.append(text)

    parts = ["\n".join(meta_parts)] if meta_parts else []
    parts.extend(chapters)
    if not chapters:
        raise _DocumentConvertError(
            _invalid_document_error(p, "spine 章节均无法提取出文本")
        )
    return "\n\n".join(parts)


# mammoth 按样式名映射标题;中文 Word 的 "标题 1/2/3" 不在默认映射里,
# 会把标题转成普通段落(标题层级丢失)。补上中文样式名映射
_DOCX_STYLE_MAP = """
p.Heading 1 => h1:fresh
p.Heading 2 => h2:fresh
p.Heading 3 => h3:fresh
p.Heading 4 => h4:fresh
p[style-name='标题 1'] => h1:fresh
p[style-name='标题 2'] => h2:fresh
p[style-name='标题 3'] => h3:fresh
p[style-name='标题 4'] => h4:fresh
p[style-name='Title'] => h1:fresh
"""


def _convert_with_markitdown(p: Path, config=None) -> str:
    """用 markitdown 转换非 PPTX 格式,失败时报统一文案(任务 2)。

    Args:
        p: 文档路径。
        config: 可选 AppConfig,用于图片 LLM 描述。

    Returns:
        str: Markdown 文本(可能为空)。

    Raises:
        _DocumentConvertError: 解析失败,或二进制格式落到 PlainText/Zip 兜底。
    """
    from markitdown import MarkItDown

    llm_client, vision_model = (None, None)
    if config is not None and getattr(config, "multimodal_model_name", None):
        llm_client, vision_model = _build_llm_client(config)

    if llm_client and vision_model:
        md = MarkItDown(
            llm_client=llm_client,
            llm_model=vision_model,
            enable_plugins=True,
        )
    else:
        md = MarkItDown(enable_plugins=False)

    try:
        if p.suffix.lower() == ".docx":
            result = md.convert(str(p), style_map=_DOCX_STYLE_MAP)
        else:
            result = md.convert(str(p))
    except Exception as e:
        # 非 bug: 文件损坏/格式不匹配/远端解析失败。
        # "缺依赖"/"格式不支持"与"文件损坏"是三回事 —— 真实 .xls 结构完好,
        # 只是 XlsConverter 缺 xlrd(MissingDependencyException);旧版 .doc/.ppt
        # 是没有对应转换器(UnsupportedFormatException)。说成"已损坏"会误导
        # 用户去修一个没坏的文件
        name = type(e).__name__
        msg = str(e)
        if "missingdependency" in name.lower() or re.search(
            r"missing dependency|pip install|uv add|uv sync|缺少依赖|未安装",
            msg,
            re.IGNORECASE,
        ):
            raise _DocumentConvertError(
                f"{TOOL_ERROR}: 缺少转换依赖,无法解析 {p.suffix} 文件: {p}"
                f"({name}: {msg})"
                " —— 这不是文件损坏;请安装缺少的依赖(如 .xls 需要 xlrd: "
                "uv add xlrd)后重试"
            ) from e
        if "unsupported" in name.lower() or re.search(
            r"unsupported|not supported|无法找到|不支持", msg, re.IGNORECASE
        ):
            raise _DocumentConvertError(
                f"{TOOL_ERROR}: 格式不受支持,无法转换: {p}"
                f"({name}: {msg})"
                " —— 这不是文件损坏;旧版二进制格式(如 .doc/.ppt/.xls)需要"
                " LibreOffice 等专用工具转换为 .docx/.pptx/.xlsx 后再试"
            ) from e
        raise _DocumentConvertError(_invalid_document_error(p, f"{name}: {msg}"))

    text = result.text_content.strip() if result.text_content else ""
    text = text.replace("﻿", "")  # 兜底剥掉 BOM,首格不残留

    # 二进制格式禁止文本/zip 兜底冒充成功
    if p.suffix.lower() in _BINARY_FORMATS and text and _looks_like_fallback_text(p, text):
        raise _DocumentConvertError(
            _invalid_document_error(p, "markitdown 未能按该格式解析,拒绝输出兜底文本")
        )
    return text


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


def _convert_document(p: Path, config=None) -> str:
    """统一转换入口: 按扩展名分派到格式专属转换器(任务 1/2/3)。

    Args:
        p: 文档路径。
        config: 可选 AppConfig。

    Returns:
        str: Markdown 文本(可能为空)。

    Raises:
        _DocumentConvertError: 转换失败(非 bug)。
    """
    suffix = p.suffix.lower()
    if suffix == ".pptx":
        return _pptx_to_markdown(p)
    if suffix == ".epub":
        return _epub_to_markdown(p)
    return _convert_with_markitdown(p, config)


# 支持的文档格式清单 — docstring 与报错文案共用,避免两处不一致
# (docstring 是 LLM 的 schema,漏列格式会让 LLM 误判"不支持";
#  改动格式时先改这里,docstring 由测试校验与本清单一致)

# 转换目标是"文本模型读不了的二进制/富文本文档" —— JSON/CSV/XML/HTML 等
# 文本文件没有转换价值,直接 Read 读取即可
_TEXT_NATIVE_SUFFIXES = frozenset({".json", ".csv", ".xml", ".html", ".htm"})

_DOCUMENT_FORMATS: tuple[str, ...] = (
    ".pdf",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".epub",
)
_DOCUMENT_EXTENSIONS = frozenset(_DOCUMENT_FORMATS)


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
    将文档转换为 Markdown 格式并保存为 .md 文件。
    转换目标是文本模型无法直接阅读的二进制文档,支持 8 种格式:
    PDF、DOCX、DOC、PPTX、PPT、XLSX、XLS、EPUB。
    JSON/CSV/XML/HTML 等纯文本格式无需转换,请直接用 Read 读取。

    旧版二进制格式(Word/PPT 97-2003 的 .doc/.ppt)转换链不支持,
    会返回"格式不受支持"并提示先经 LibreOffice 转为 .docx/.pptx;
    .xls 需要 xlrd 依赖(uv add xlrd),缺依赖时报"缺少转换依赖"
    —— 都不是文件损坏,无需修复源文件。

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
    if suffix in _TEXT_NATIVE_SUFFIXES:
        return (
            f"{suffix} 是文本格式,无需转换为 Markdown —— "
            f"文本模型可直接读取,请用 Read 工具查看: {p.name}"
        )
    if suffix not in _DOCUMENT_EXTENSIONS:
        return (
            f"{TOOL_ERROR}: 不支持的格式 '{suffix}',"
            f"支持的格式: {', '.join(_DOCUMENT_FORMATS)}"
        )

    # 内容/扩展名一致性校验(任务 2): 垃圾内容改后缀立即报错,
    # 不给 markitdown 兜底冒充成功的机会,各格式失败口径一致
    content_err = _validate_document_content(p)
    if content_err:
        return content_err

    # 确定输出路径(提前计算,避免异步竞争)
    if output_path:
        out = Path(output_path)
    else:
        # 原子分配:并发转换同名不同扩展名的文件(如 a.csv + a.html)时,
        # 以 O_CREAT|O_EXCL 占位,后者自动获得 _1/_2... 后缀,互不覆盖
        try:
            out = _allocate_md_output(p)
        except OSError as e:
            # mkdir/os.open 的非 FileExistsError 失败:无权限、路径过长、磁盘满等
            return (
                f"{TOOL_ERROR}: 分配输出路径失败 (file_path={file_path}, "
                f"output_path={p.with_suffix('.md')}): {type(e).__name__}: {e}"
            )

    def _do_convert() -> tuple[str, float]:
        """在线程池中执行同步转换(避免阻塞事件循环)"""
        start = time.monotonic()
        text = _convert_document(p, config)
        elapsed = time.monotonic() - start
        text = text.strip() if text else ""

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
        except _DocumentConvertError as e:
            # 非 bug: 文件损坏/格式不匹配,文案已带 TOOL_ERROR 与上下文
            _cleanup_placeholder(out)
            await _wake(str(e))
        except Exception as e:
            # bug 级兜底: 清掉占位,避免留下空 .md 冒充成功
            _cleanup_placeholder(out)
            await _wake(f"转换失败 (file_path={p}, output_path={out}): {type(e).__name__}: {e}")

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
