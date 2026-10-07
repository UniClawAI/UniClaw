"""LSP 语言智能工具 — AI agent 查询语言服务器的入口。

工具:
- lsp_status / lsp_start / lsp_stop: 服务器生命周期
- lsp_diagnostics: 查询文件诊断(类型错误、警告)—— 主力工具
- lsp_definition / lsp_hover / lsp_references / lsp_symbols: 语义查询
"""

from __future__ import annotations

from pathlib import Path

from uniclaw.tools.base import tool, ToolRuntime, ToolUserError
from uniclaw.utils.constants import TOOL_ERROR

from .client import LspRpcError
from .manager import LSPManager
from .protocol import (
    DEFINITION,
    DOCUMENT_SYMBOL,
    HOVER,
    LspBizError,
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

def _split_inline_suffix(path_str: str) -> tuple[str, int, int]:
    """拆分 "路径:行[:列]" 内嵌后缀,返回 (路径, 行, 列)。

    行/列以 1-based 原样返回,0 表示未给。Windows 盘符(如 D:)不参与
    拆分:只有纯数字结尾的后缀才被当作行/列。
    """
    inline_line = inline_col = 0

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

    return path_str, inline_line, inline_col


def _root_dir_of(tool_runtime) -> Path | None:
    """取会话工作目录(tool_runtime.config.root_dir)。"""
    try:
        return tool_runtime.config.root_dir
    except AttributeError:
        return None


def _to_abs(path_str: str, root_dir: Path | None) -> Path:
    """把 file 参数解析成绝对路径。

    相对路径必须按**会话工作目录**(config.root_dir)解析 —— `Path.cwd()`
    是**进程**工作目录(启动时固定,常为安装目录),两者不能混用
    (见 CLAUDE.md「root_dir ≠ Path.cwd()」)。
    """
    p = Path(path_str).expanduser()
    if p.is_absolute():
        return p.resolve()
    if root_dir is None:
        raise ToolUserError(
            f"相对路径 {path_str!r} 需要会话工作目录才能解析,但当前没有 root_dir。"
            f"请改用绝对路径"
        )
    return (Path(root_dir) / p).resolve()


def _resolve_file(file: str, root_dir: Path | None = None) -> Path:
    """把 file 参数解析成路径,剥离 ":行[:列]" 内嵌后缀但不使用行列。

    用于 lsp_diagnostics / lsp_symbols 这类全文件查询 —— 与 lsp_definition
    一致接受 "路径:行[:列]" 内嵌格式,避免同一 file 参数三工具行为分裂;
    剥离后的行列被忽略(全文件查询用不上)。
    """
    if not isinstance(file, str):
        file = str(file)
    path_str, _, _ = _split_inline_suffix(file.strip())
    return _to_abs(path_str, root_dir)


def _parse_position(
    file: str, line: int = 0, column: int = 0, root_dir: Path | None = None
) -> tuple[Path, int, int]:
    """解析文件位置参数,返回 (绝对路径, 0-based line, 0-based column)。

    支持三种形态:
    - "path/to/file.py" + line/column 参数
    - "path/to/file.py:12"  (行号内嵌, 1-based)
    - "path/to/file.py:12:5" (行+列内嵌, 1-based)
    显式 line / column 参数优先于内嵌值。

    只用于需要坐标的定位查询(definition/hover/references);全文件查询
    (diagnostics/symbols)请用 _resolve_file(同样支持内嵌格式,但忽略行列)。
    """
    # 兼容 Path 对象输入(工具直接调用时可能传入 WindowsPath)
    if not isinstance(file, str):
        file = str(file)
    path_str, inline_line, inline_col = _split_inline_suffix(file.strip())

    if line > 0:
        inline_line = line
    if column > 0:
        inline_col = column

    # line 全未指定时必须报参数错误 —— 否则会拿 (0,0) 去查, 返回
    # "未找到匹配位置",被误读成"符号无定义"。line 是 1-based, 0 只表示"未传"
    if inline_line <= 0:
        raise ToolUserError(
            "缺少 line 参数(1-based)。请传 line/column,"
            '或用 "文件路径:行[:列]" 内嵌格式,如 "src/main.py:10:5"'
        )

    path = _to_abs(path_str, root_dir)
    return path, max(inline_line - 1, 0), max(inline_col - 1, 0)


def _require_file(path: str | Path) -> None:
    path = Path(path)  # 兼容 str/Path 输入,内部统一 Path
    if not path.is_file():
        raise LspBizError(f"文件不存在: {path}")


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

# 查询命中文件、LSP 正常响应但结果为空时追加的提示。
# 空结果多数不是工具坏了,而是符号本身绑定不了(如实例方法缺 self)。
_EMPTY_RESULT_HINT = (
    "文件已同步且查询成功,但语言服务器未返回结果。"
    "可能原因:光标处不是符号,或源码存在语法/符号绑定问题"
    "(如 class 内实例方法缺少 self),导致语言服务器无法绑定该符号;"
    "请检查源码本身,不要重复调用本工具。"
)


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
        return f"未找到匹配位置。{_EMPTY_RESULT_HINT}"
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("uri") and item.get("range"):  # Location
            locs.append(item)
        elif item.get("targetUri") and item.get("targetRange"):  # LocationLink
            locs.append({"uri": item["targetUri"], "range": item["targetRange"]})
    if not locs:
        return f"未找到匹配位置。{_EMPTY_RESULT_HINT}"
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
        return f"无悬停信息。{_EMPTY_RESULT_HINT}"
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
    return f"悬停信息:\n{text}" if text else f"无悬停信息。{_EMPTY_RESULT_HINT}"


def _symbol_sort_key(s: dict) -> tuple:
    """符号排序键:行号 → 名称 → 列号。

    语言服务器返回的子节点顺序不稳定(如属性插在方法后面),构树时按源码
    位置稳定排序;同行号时按名称/列号定序,保证输出可复现。
    """
    if "selectionRange" in s:
        rng = s.get("selectionRange") or s.get("range") or {}
    elif "location" in s:
        rng = (s.get("location") or {}).get("range", {})
    else:
        rng = {}
    start = (rng or {}).get("start", {})
    return (start.get("line", 0), s.get("name") or "", start.get("character", 0))


def _format_symbols(symbols, indent: int = 0) -> list[str]:
    """递归渲染 documentSymbol 树(DocumentSymbol[] 或 SymbolInformation[])。

    每层子节点按行号稳定排序(同行号按名称/列号),避免服务器乱序导致
    展示错乱(如 L11 排在 L13 后)。
    """
    out = []
    if not symbols:
        return out
    pad = "  " * indent
    ordered = sorted(
        (s for s in symbols if isinstance(s, dict)), key=_symbol_sort_key
    )
    for s in ordered:
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
    except LspBizError as e:
        # 服务器未装/不支持语言/启动失败/连续崩溃 → 返回可操作提示,不污染错误日志
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

# 语义查询的预期失败:
# - LspBizError(RuntimeError 子类): 文件不存在/服务器未装/请求超时/管道断开
# - LspRpcError: 服务器返回 error 响应(远端拒绝,等价远端 4xx/5xx)
# - RuntimeError: client.stop() 对 pending 请求注入的"语言服务器已关闭"
_QUERY_ERRORS = (LspBizError, LspRpcError, RuntimeError)


def _query_error(method: str, path: Path, e: BaseException) -> str:
    """语义查询失败的统一 TOOL_ERROR(带 method 与文件路径,便于定位)。

    不外泄内部异常类名(LspBizError/LspRpcError 等):LspBizError 是业务
    错误,只回显其消息(与 lsp_diagnostics / lsp_start 一致,如"文件不存在: xxx");
    其余异常保留原始错误文本,同样不带类名。
    """
    if isinstance(e, LspBizError):
        return f"{TOOL_ERROR}: {e}"
    return f"{TOOL_ERROR}: LSP {method} 查询失败(file={path}): {e}"

@tool
async def lsp_diagnostics(file: str, tool_runtime: ToolRuntime = None) -> str:
    """查询文件的类型诊断(错误/警告/信息),由语言服务器实时分析。

    最常用于:修改代码后自检、定位类型不匹配、查找未定义符号。
    输出按严重程度排序(错误在前),包含行/列与消息(超长消息截断到 200 字符)。
    诊断始终针对整个文件。

    本工具只接受 file,不接受 line/column 参数;需要按行定位请用
    lsp_definition / lsp_hover / lsp_references。file 写成 "路径:行[:列]"
    内嵌格式时行列会被忽略。

    Args:
        file: 文件路径。不收 line/column 参数;内嵌 "路径:行[:列]" 时行列会被忽略,如 "src/main.py"。

    Returns:
        str: 格式化后的诊断列表;无问题时提示通过。
    """
    path = _resolve_file(file, root_dir=_root_dir_of(tool_runtime))
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
    except LspBizError as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def lsp_definition(
    file: str,
    line: int = 0,
    column: int = 0,
    tool_runtime: ToolRuntime = None,
) -> str:
    """跳转到符号定义位置(精确解析,支持跨文件、继承与 re-export)。

    定位坐标:file 内嵌 "路径:行[:列]"(1-based),或单独传 line/column,
    显式参数优先。必须提供行号(内嵌或 line 参数),否则返回参数错误。

    Args:
        file: 文件路径,支持 "路径:行[:列]" 内嵌格式(1-based),如 "src/main.py:10:5"。
        line: 行号(1-based),优先于路径内嵌行号;必须提供 line 或内嵌行号。
        column: 列号(1-based),优先于路径内嵌列号。

    Returns:
        str: 定义位置列表(目标文件:行:列 + 代码摘录)。
    """
    path, line0, col0 = _parse_position(
        file, line, column, root_dir=_root_dir_of(tool_runtime)
    )
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
                "textDocument": {"uri": path_to_uri(str(path))},
                "position": {"line": line0, "character": col0},
            },
        )
        items = result if isinstance(result, list) else [result]
        return _format_locations([i for i in items if isinstance(i, dict)])
    except _QUERY_ERRORS as e:
        return _query_error(DEFINITION, path, e)


@tool
async def lsp_hover(
    file: str,
    line: int = 0,
    column: int = 0,
    tool_runtime: ToolRuntime = None,
) -> str:
    """获取符号悬停信息(类型签名、文档),用于确认类型或 API 用法。

    定位坐标:file 内嵌 "路径:行[:列]"(1-based),或单独传 line/column,
    显式参数优先。必须提供行号(内嵌或 line 参数),否则返回参数错误。

    Args:
        file: 文件路径,支持 "路径:行[:列]" 内嵌格式(1-based),如 "src/main.py:10:5"。
        line: 行号(1-based),优先于路径内嵌行号;必须提供 line 或内嵌行号。
        column: 列号(1-based),优先于路径内嵌列号。

    Returns:
        str: 类型/文档信息。
    """
    path, line0, col0 = _parse_position(
        file, line, column, root_dir=_root_dir_of(tool_runtime)
    )
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
                "textDocument": {"uri": path_to_uri(str(path))},
                "position": {"line": line0, "character": col0},
            },
        )
        return _format_hover(result)
    except _QUERY_ERRORS as e:
        return _query_error(HOVER, path, e)


@tool
async def lsp_references(
    file: str,
    line: int = 0,
    column: int = 0,
    tool_runtime: ToolRuntime = None,
) -> str:
    """查找符号的所有引用位置(含声明处),用于评估重构影响面。

    定位坐标:file 内嵌 "路径:行[:列]"(1-based),或单独传 line/column,
    显式参数优先。必须提供行号(内嵌或 line 参数),否则返回参数错误。

    Args:
        file: 文件路径,支持 "路径:行[:列]" 内嵌格式(1-based),如 "src/main.py:10:5"。
        line: 行号(1-based),优先于路径内嵌行号;必须提供 line 或内嵌行号。
        column: 列号(1-based),优先于路径内嵌列号。

    Returns:
        str: 引用位置列表(最多 100 处,超出截断并提示)。
    """
    path, line0, col0 = _parse_position(
        file, line, column, root_dir=_root_dir_of(tool_runtime)
    )
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
                "textDocument": {"uri": path_to_uri(str(path))},
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
        return f"共找到 {n} 处引用:\n{locs}" if n else f"未找到引用。{_EMPTY_RESULT_HINT}"
    except _QUERY_ERRORS as e:
        return _query_error(REFERENCES, path, e)


@tool
async def lsp_symbols(file: str, tool_runtime: ToolRuntime = None) -> str:
    """列出文档的符号大纲(类/函数/变量等及其行号),快速了解文件结构。

    符号提取针对整个文件。

    本工具只接受 file,不接受 line/column 参数;需要按行定位请用
    lsp_definition / lsp_hover / lsp_references。file 写成 "路径:行[:列]"
    内嵌格式时行列会被忽略。

    Args:
        file: 文件路径。不收 line/column 参数;内嵌 "路径:行[:列]" 时行列会被忽略,如 "src/main.py"。

    Returns:
        str: 符号树(缩进表示嵌套层级)。
    """
    path = _resolve_file(file, root_dir=_root_dir_of(tool_runtime))
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
                "textDocument": {"uri": path_to_uri(str(path))},
            },
        )
        if not result:
            return f"{path} — 未提取到符号。"
        symbols = result if isinstance(result, list) else [result]
        lines = _format_symbols(symbols)
        if not lines:
            return f"{path} — 未提取到符号。"
        pre = f"{path} — 符号大纲({_symbol_count(symbols)} 个):"
        return pre + "\n" + "\n".join(lines)
    except _QUERY_ERRORS as e:
        return _query_error(DOCUMENT_SYMBOL, path, e)


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