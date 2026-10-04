"""LSP 语言智能工具 — AI agent 查询语言服务器的入口。

工具:
- lsp_status / lsp_start / lsp_stop: 服务器生命周期
- lsp_diagnostics: 查询文件诊断(类型错误、警告)—— 主力工具
- lsp_definition / lsp_hover / lsp_references / lsp_symbols: 语义查询
"""

from __future__ import annotations

from pathlib import Path

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR

from .manager import LSPManager
from .protocol import (
    DEFINITION,
    DOCUMENT_SYMBOL,
    HOVER,
    REFERENCES,
    path_to_uri,
    uri_to_path,
)
from .servers import BUILTIN_SERVERS, detect_language

DEFAULT_LANGUAGE = "python"

# 诊断 severity: LSP DiagnosticSeverity
_SEVERITY_NAMES = {1: "错误", 2: "警告", 3: "信息", 4: "提示"}
_SEVERITY_ORDER = {1: 0, 2: 1, 3: 2, 4: 3}

# SymbolKind → 中文名
_SYMBOL_KINDS = {
    1: "文件", 2: "模块", 3: "命名空间", 4: "包", 5: "类", 6: "方法",
    7: "属性", 8: "字段", 9: "构造函数", 10: "枚举", 11: "接口",
    12: "函数", 13: "变量", 14: "常量", 15: "字符串", 16: "数字",
    17: "布尔", 18: "数组", 19: "对象", 20: "键", 21: "空值",
    22: "枚举成员", 23: "结构体", 24: "事件", 25: "操作符", 26: "类型参数",
}


# ── 参数解析 ──────────────────────────────────────────

def _parse_position(
    file: str, line: int = 0, column: int = 0
) -> tuple[Path, int, int]:
    """解析文件位置参数,返回 (绝对路径, 0-based line, 0-based column)。

    支持三种形态:
    - "path/to/file.py" + line/column 参数
    - "path/to/file.py:12"  (行号内嵌, 1-based)
    - "path/to/file.py:12:5" (行+列内嵌, 1-based)
    显式 line / column 参数优先于内嵌值。
    """
    path_str = file.strip()
    inline_line = inline_col = 0

    # 拆分行内嵌的 ":line[:col]" 后缀(Windows 盘符如 D:\ 不参与拆分:只拆数字结尾的后缀)
    parts = path_str.rsplit(":", 2)
    if len(parts) >= 2 and parts[-1].isdigit():
        inline_col = int(parts[-1])
        remainder = ":".join(parts[:-1])
        parts2 = remainder.rsplit(":", 1)
        if len(parts2) == 2 and parts2[-1].isdigit():
            inline_line = int(parts2[-1])
            path_str = parts2[0]
        else:
            inline_line = inline_col
            inline_col = 0
            path_str = remainder

    if line > 0:
        inline_line = line
    if column > 0:
        inline_col = column

    path = Path(path_str).expanduser().resolve()
    return path, max(inline_line - 1, 0), max(inline_col - 1, 0)


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")


def _read_line(path: Path, zero_based_line: int) -> str:
    """读取指定行内容(摘录用),失败返回提示。"""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        if 0 <= zero_based_line < len(lines):
            return lines[zero_based_line].strip()
    except OSError:
        pass
    return ""


def _p1(line0: int, col0: int) -> tuple[int, int]:
    return line0 + 1, col0 + 1


# ── 结果格式化 ──────────────────────────────────────────

def _format_diagnostics(path: Path, diags: list) -> str:
    if not diags:
        return f"{path} — 未发现诊断(类型检查通过)。"
    diags = sorted(diags, key=lambda d: _SEVERITY_ORDER.get(d.get("severity"), 9))
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    # 全量统计(标题数字不被截断低估);渲染再截断
    for d in diags:
        sev = d.get("severity", 3)
        counts[sev] = counts.get(sev, 0) + 1
    lines = []
    MAX_ITEMS = 100
    for d in diags[:MAX_ITEMS]:
        sev = d.get("severity", 3)
        rng = d.get("range", {})
        start = rng.get("start", {})
        line1, col1 = _p1(start.get("line", 0), start.get("character", 0))
        msg = (d.get("message") or "").strip().replace("\n", " ")
        if len(msg) > 200:
            msg = msg[:197] + "..."
        lines.append(f"  [{_SEVERITY_NAMES.get(sev, str(sev))}] L{line1}:{col1} {msg}")
    title = f"{path} — 共 {len(diags)} 条诊断"
    stat = " / ".join(
        f"{counts[s]} {name}" for s, name in _SEVERITY_NAMES.items() if counts.get(s)
    )
    if stat:
        title += f" ({stat})"
    if len(diags) > MAX_ITEMS:
        lines.append(f"  … 另有 {len(diags) - MAX_ITEMS} 条已省略")
    return title + "\n" + "\n".join(lines)


def _format_locations(items: list, limit: int = 100) -> str:
    """格式化 definition/references 返回的位置列表。

    兼容两种结构:LSP 规范允许 definition 返回
    - Location(uri + range):pyright 等
    - LocationLink(targetUri + targetRange):typescript-language-server 等
    """
    locs = []
    if items is None:
        return "未找到匹配位置。"
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("uri") and item.get("range"):  # Location
            locs.append(item)
        elif item.get("targetUri") and item.get("targetRange"):  # LocationLink
            locs.append({"uri": item["targetUri"], "range": item["targetRange"]})
    if not locs:
        return "未找到匹配位置。"
    out = []
    for loc in locs[:limit]:
        uri = loc["uri"]
        start = loc["range"].get("start", {})
        line1, col1 = _p1(start.get("line", 0), start.get("character", 0))
        target = uri_to_path(uri)
        snippet = ""
        if target:
            snippet = _read_line(Path(target), start.get("line", 0))
            if snippet:
                snippet = f"      {snippet[:100]}"
        out.append(f"  {target or uri}:{line1}:{col1}  {snippet}".rstrip())
    if len(locs) > limit:
        out.append(f"  … 另有 {len(locs) - limit} 处已省略")
    return "\n".join(out)


def _format_hover(hover_result: object) -> str:
    if hover_result is None:
        return "无悬停信息。"
    if isinstance(hover_result, dict):
        contents = hover_result.get("contents")
    else:
        contents = hover_result

    pieces: list[str] = []
    if isinstance(contents, str):
        pieces.append(contents)
    elif isinstance(contents, list):
        for c in contents:
            if isinstance(c, str):
                pieces.append(c)
            elif isinstance(c, dict):
                pieces.append(c.get("value") or "")
    elif isinstance(contents, dict):
        pieces.append(contents.get("value") or "")
    text = "\n".join(x.strip() for x in pieces if x and x.strip())
    return f"悬停信息:\n{text}" if text else "无悬停信息。"


def _format_symbols(symbols, indent: int = 0) -> list[str]:
    """递归渲染 documentSymbol 树(DocumentSymbol[] 或 SymbolInformation[])。"""
    out = []
    if not symbols:
        return out
    pad = "  " * indent
    for s in symbols:
        if not isinstance(s, dict):
            continue
        name = s.get("name", "")
        kind = _SYMBOL_KINDS.get(s.get("kind"), "符号")
        # DocumentSymbol 用 selectionRange/range;SymbolInformation 用 location.range
        if "selectionRange" in s:
            rng = s.get("selectionRange") or s.get("range") or {}
            children = s.get("children", [])
        elif "location" in s:
            rng = (s.get("location") or {}).get("range", {})
            children = []
        else:
            rng = {}
            children = []
        start = (rng or {}).get("start", {})
        line1, _ = _p1(start.get("line", 0), start.get("character", 0))
        detail = s.get("detail") or ""
        line = f"{pad}L{line1} [{kind}] {name}"
        if detail:
            line += f" — {detail[:80]}"
        out.append(line)
        out.extend(_format_symbols(children, indent + 1))
    return out


# ── 生命周期工具 ──────────────────────────────────────────

@tool
async def lsp_status(language: str = "") -> str:
    """查看语言服务器状态:已注册语言、各语言服务器是否已启动、已同步文档数。

    Args:
        language: 只查看指定语言(如 python)。为空列出全部。

    Returns:
        str: 服务器状态列表。
    """
    mgr = LSPManager.get_instance()
    lines = []
    registered = sorted(BUILTIN_SERVERS.keys())
    lines.append(f"已注册语言: {', '.join(registered) if registered else '无'}")
    sessions = mgr.list_sessions()
    if language:
        sessions = [s for s in sessions if s[0] == language]
    if not sessions:
        lines.append("当前没有已启动的服务器(首次查询时自动启动)。")
    for lang, root, session in sessions:
        name = session.server_def["name"]
        docs = f", 已同步文档 {session.queued_documents} 个"
        if session.is_ready:
            lines.append(f"  {lang} @ {root}: 已启动({name}){docs}")
        elif session._start_error:
            lines.append(
                f"  {lang} @ {root}: 启动失败({name}) — {session._start_error}"
            )
        elif session.client is not None and not session.client.is_alive:
            lines.append(
                f"  {lang} @ {root}: 已崩溃({name}),将在下次查询时自动重启"
            )
        else:
            lines.append(f"  {lang} @ {root}: 待启动({name}){docs}")
    return "\n".join(lines)


@tool
async def lsp_start(language: str = DEFAULT_LANGUAGE, tool_runtime: ToolRuntime = None) -> str:
    """启动指定语言的 LSP 服务器(懒启动,通常无需手动调用)。

    Args:
        language: 语言 id(如 python)。若服务器未安装,返回安装指引。

    Returns:
        str: 启动结果。
    """
    try:
        config = tool_runtime.config
        mgr = LSPManager.get_instance()
        session = await mgr.get_session(language, config)
        await session.ensure_started()
        name = session.server_def["name"]
        return f"语言服务器 {name}({language}) 已启动。"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def lsp_stop(language: str = "") -> str:
    """停止 LSP 服务器,释放子进程资源。

    Args:
        language: 停止指定语言;空则停止全部。

    Returns:
        str: 停止结果。
    """
    mgr = LSPManager.get_instance()
    if language:
        ok = await mgr.close_session(language)
        return f"已停止 {language} 服务器。" if ok else f"没有正在运行的 {language} 服务器。"
    await mgr.close_all()
    return "已停止全部 LSP 服务器。"


def _line_count(path: Path) -> int:
    """文件实际行数(用于行号越界校验);读取失败返回 0。"""
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def _check_line_bounds(path: Path, line0: int) -> str | None:
    """校验 0-based 行号不越界;越界返回可操作错误文案,否则 None。"""
    if line0 < 0:
        return f"{TOOL_ERROR}: 行号不能为负"
    n = _line_count(path)
    if n > 0 and line0 >= n:
        return f"{TOOL_ERROR}: 行号 {line0 + 1} 超出文件行数({n} 行)"
    return None


# ── 语义查询工具 ──────────────────────────────────────────

@tool
async def lsp_diagnostics(file: str, tool_runtime: ToolRuntime = None) -> str:
    """查询文件的类型诊断(错误/警告/信息),由语言服务器实时分析。

    最常用于:修改代码后自检、定位类型不匹配、查找未定义符号。
    输出按严重程度排序(错误在前),包含行/列与消息(超长消息截断到 200 字符)。

    Args:
        file: 文件路径(可带行号后缀,仅用于路径解析;诊断始终针对整个文件)。

    Returns:
        str: 格式化后的诊断列表;无问题时提示通过。
    """
    path, _, _ = _parse_position(file)
    try:
        _require_file(path)
        lang = detect_language(str(path))
        if not lang:
            return f"{TOOL_ERROR}: 无法识别文件的语言(LSP 未注册 {path.suffix} 扩展名)"
        mgr = LSPManager.get_instance()
        session = await mgr.get_session(lang, tool_runtime.config)
        diags = await session.get_diagnostics(str(path))
        if diags is None:
            return f"{path} — 服务器暂未返回诊断(可能仍在索引,稍后重试)。"
        return _format_diagnostics(path, diags)
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def lsp_definition(
    file: str,
    line: int = 0,
    column: int = 0,
    tool_runtime: ToolRuntime = None,
) -> str:
    """跳转到符号定义位置(精确解析,支持跨文件、继承与 re-export)。

    参数 file 支持 "路径:行[:列]" 内嵌格式;line/column 也可单独传入(1-based)。

    Args:
        file: 文件路径,可带行号后缀,如 "src/main.py:10:5"。
        line: 行号(1-based),优先于路径内嵌。
        column: 列号(1-based),优先于路径内嵌。

    Returns:
        str: 定义位置列表(目标文件:行:列 + 代码摘录)。
    """
    path, line0, col0 = _parse_position(file, line, column)
    try:
        _require_file(path)
        bounds_err = _check_line_bounds(path, line0)
        if bounds_err:
            return bounds_err
        lang = detect_language(str(path))
        if not lang:
            return f"{TOOL_ERROR}: 无法识别文件语言: {path.suffix}"
        mgr = LSPManager.get_instance()
        session = await mgr.get_session(lang, tool_runtime.config)
        await session.sync_document(str(path))
        # pyright 在文档分析完成前收到 definition 会返回空并可能挂起不再
        # 响应后续请求;诊断 pull 返回 = 分析完成信号(有缓存时极快)
        await session.get_diagnostics(str(path))
        result = await session.request(
            DEFINITION,
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": {"line": line0, "character": col0},
            },
        )
        items = result if isinstance(result, list) else [result]
        return _format_locations([i for i in items if isinstance(i, dict)])
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def lsp_hover(
    file: str,
    line: int = 0,
    column: int = 0,
    tool_runtime: ToolRuntime = None,
) -> str:
    """获取符号悬停信息(类型签名、文档),用于确认类型或 API 用法。

    Args:
        file: 文件路径,可带行号后缀,如 "src/main.py:10:5"。
        line: 行号(1-based),优先于路径内嵌。
        column: 列号(1-based),优先于路径内嵌。

    Returns:
        str: 类型/文档信息。
    """
    path, line0, col0 = _parse_position(file, line, column)
    try:
        _require_file(path)
        bounds_err = _check_line_bounds(path, line0)
        if bounds_err:
            return bounds_err
        lang = detect_language(str(path))
        if not lang:
            return f"{TOOL_ERROR}: 无法识别文件语言: {path.suffix}"
        mgr = LSPManager.get_instance()
        session = await mgr.get_session(lang, tool_runtime.config)
        await session.sync_document(str(path))
        # 同 lsp_definition:确保分析完成后查询,避免 pyright 挂起
        await session.get_diagnostics(str(path))
        result = await session.request(
            HOVER,
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": {"line": line0, "character": col0},
            },
        )
        return _format_hover(result)
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def lsp_references(
    file: str,
    line: int = 0,
    column: int = 0,
    tool_runtime: ToolRuntime = None,
) -> str:
    """查找符号的所有引用位置(含声明处),用于评估重构影响面。

    Args:
        file: 文件路径,可带行号后缀,如 "src/main.py:10:5"。
        line: 行号(1-based),优先于路径内嵌。
        column: 列号(1-based),优先于路径内嵌。

    Returns:
        str: 引用位置列表(最多 100 处,超出截断并提示)。
    """
    path, line0, col0 = _parse_position(file, line, column)
    try:
        _require_file(path)
        bounds_err = _check_line_bounds(path, line0)
        if bounds_err:
            return bounds_err
        lang = detect_language(str(path))
        if not lang:
            return f"{TOOL_ERROR}: 无法识别文件语言: {path.suffix}"
        mgr = LSPManager.get_instance()
        session = await mgr.get_session(lang, tool_runtime.config)
        await session.sync_document(str(path))
        # 同 lsp_definition:确保分析完成后查询,避免 pyright 挂起
        await session.get_diagnostics(str(path))
        result = await session.request(
            REFERENCES,
            {
                "textDocument": {"uri": path_to_uri(path)},
                "position": {"line": line0, "character": col0},
                "context": {"includeDeclaration": True},
            },
        )
        locs = _format_locations(result if isinstance(result, list) else [])
        # 标题统计实际渲染的引用数(与 _format_locations 同口径:仅含
        # uri+range 或 targetUri+targetRange 的项;截断到 100 条)
        rendered = sum(
            1
            for item in (result if isinstance(result, list) else [])
            if isinstance(item, dict)
            and (
                (item.get("uri") and item.get("range"))
                or (item.get("targetUri") and item.get("targetRange"))
            )
        )
        n = min(rendered, 100)
        return f"共找到 {n} 处引用:\n{locs}" if n else "未找到引用。"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def lsp_symbols(file: str, tool_runtime: ToolRuntime = None) -> str:
    """列出文档的符号大纲(类/函数/变量等及其行号),快速了解文件结构。

    Args:
        file: 文件路径。

    Returns:
        str: 符号树(缩进表示嵌套层级)。
    """
    path, _, _ = _parse_position(file)
    try:
        _require_file(path)
        lang = detect_language(str(path))
        if not lang:
            return f"{TOOL_ERROR}: 无法识别文件语言: {path.suffix}"
        mgr = LSPManager.get_instance()
        session = await mgr.get_session(lang, tool_runtime.config)
        await session.sync_document(str(path))
        # 同 lsp_definition:确保分析完成后查询,避免 pyright 挂起
        await session.get_diagnostics(str(path))
        result = await session.request(
            DOCUMENT_SYMBOL,
            {
                "textDocument": {"uri": path_to_uri(path)},
            },
        )
        if not result:
            return f"{path} — 未提取到符号。"
        symbols = result if isinstance(result, list) else [result]
        lines = _format_symbols(symbols)
        if not lines:
            return f"{path} — 未提取到符号。"
        pre = f"{path} — 符号大纲(L{_symbol_count(symbols)} 个):"
        return pre + "\n" + "\n".join(lines)
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


def _symbol_count(symbols) -> int:
    n = 0
    for s in symbols:
        if isinstance(s, dict):
            n += 1
            n += _symbol_count(s.get("children", []))
    return n


def get_tools() -> list:
    """获取 LSP 工具列表(扩展工具注册入口)。"""
    return [
        lsp_status,
        lsp_start,
        lsp_stop,
        lsp_diagnostics,
        lsp_definition,
        lsp_hover,
        lsp_references,
        lsp_symbols,
    ]


def get_all_tools() -> list:
    """获取全部 LSP 工具(注册表初始化入口,与 get_tools 一致)。"""
    return get_tools()