"""LSP 语言智能工具测试 — 纯逻辑层(协议帧、uri 规范化、注册表、解析/格式化)。

不启动真实语言服务器子进程,保证测试快速、稳定、可在任意环境运行。
覆盖:帧编解码、uri 规范化、resolve_launch、诊断缓存、服务器注册表、
位置解析、诊断/位置/悬停/符号格式化、会话键控与空闲回收。
"""

import asyncio
import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniclaw.tools.lsp import protocol as proto
from uniclaw.tools.lsp.client import LspClient, resolve_launch
from uniclaw.tools.lsp.manager import LSPManager, LspSession
from uniclaw.tools.lsp.servers import (
    BUILTIN_SERVERS,
    detect_language,
    get_server_def,
    language_id_for,
    server_install_hint,
)
from uniclaw.tools.lsp.tools import (
    _format_diagnostics,
    _format_hover,
    _format_locations,
    _format_symbols,
    _parse_position,
)


# ── 帧编解码 ──────────────────────────────────────────


class _FakeReader:
    """可替换 asyncio.StreamReader 的最小 reader(供 read_message 测试)。"""

    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)

    async def readline(self):
        return self._buf.readline()

    async def readexactly(self, n):
        return self._buf.read(n)


def test_encode_message():
    """encode_message 输出 Content-Length 头 + JSON 正文。"""
    body = json.dumps({"a": 1}, ensure_ascii=False).encode("utf-8")
    frame = proto.encode_message({"a": 1})
    assert frame.startswith(b"Content-Length: ")
    assert frame == f"Content-Length: {len(body)}\r\n\r\n".encode() + body


@pytest.mark.asyncio
async def test_read_message_ok():
    """read_message 正确解析完整帧。"""
    body = b'{"jsonrpc": "2.0", "id": 1}'
    reader = _FakeReader(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    msg = await proto.read_message(reader)
    assert msg == {"jsonrpc": "2.0", "id": 1}


@pytest.mark.asyncio
async def test_read_message_eof():
    """流关闭时抛 EOFError。"""
    reader = _FakeReader(b"")
    with pytest.raises(EOFError):
        await proto.read_message(reader)


@pytest.mark.asyncio
async def test_read_message_bad_header():
    """非法帧头抛 FrameError。"""
    reader = _FakeReader(b"Bogus: 1\r\n\r\n{}")
    with pytest.raises(proto.FrameError):
        await proto.read_message(reader)


# ── uri 规范化 ──────────────────────────────────────────


def test_path_to_uri_windows_lowercase_drive():
    """Windows 路径 → 小写盘符 + 冒号(file:///d:/...)。"""
    assert proto.path_to_uri("D:\\code\\a.py") == "file:///d:/code/a.py"


def test_path_to_uri_posix():
    """POSIX 路径 → file:///x(Windows 上解析到当前盘符根)。"""
    if sys.platform == "win32":
        # Windows:Path("/home/x") 解析到当前盘符的根目录
        out = proto.path_to_uri("/home/user/a.py")
        assert out.startswith("file:///[a-z]:/home/user/a.py") or (
            out.startswith("file:///") and "home/user" in out
        )
    else:
        assert proto.path_to_uri("/home/user/a.py") == "file:///home/user/a.py"


@pytest.mark.parametrize(
    "uri,expected",
    [
        # 标准小写盘符 → 统一返回大写盘符本地路径
        ("file:///d:/code/a.py", "D:\\code\\a.py"),
        # 大写盘符(兼容)
        ("file:///D:/code/a.py", "D:\\code\\a.py"),
        # %3A 编码盘符冒号(服务器回显常见形态)
        ("file:///d%3A/code/a.py", "D:\\code\\a.py"),
        # 无盘符冒号(pyright 无冒号形态)
        ("file:///D/code/a.py", "D:\\code\\a.py"),
        # POSIX(仅非 Windows 生效)
        ("file:///home/user/a.py", "/home/user/a.py"),
    ],
)
def test_uri_to_path(uri, expected):
    if expected.startswith("/home") and sys.platform == "win32":
        pytest.skip("POSIX 形态在 Windows 无意义")
    assert proto.uri_to_path(uri) == expected


def test_normalize_uri_roundtrip():
    """normalize_uri 把各形态回显统一为同一 key。"""
    variants = [
        "file:///d:/code/a.py",      # 发送形态(小写)
        "file:///D:/code/a.py",      # 大写
        "file:///d%3A/code/a.py",    # pyright/TS 回显(%3A)
        "file:///D/code/a.py",       # 无冒号
    ]
    keys = {proto.normalize_uri(v) for v in variants}
    assert len(keys) == 1, f"归一化后 key 应一致: {keys}"
    assert keys.pop() == "file:///d:/code/a.py"


# ── resolve_launch ──────────────────────────────────────────


def test_resolve_launch_plain_exe(monkeypatch):
    """普通可执行文件直接返回(不经 .cmd 解析)。"""
    monkeypatch.setattr("uniclaw.tools.lsp.client.shutil.which", lambda c: None)
    assert resolve_launch("pylsp") == ["pylsp"]


def test_resolve_launch_cmd_mjs(tmp_path, monkeypatch):
    """npm .cmd 包装解析 node 入口(含 .mjs,如 typescript-language-server)。"""
    cmd = tmp_path / "tsls.cmd"
    cmd.write_text(
        '@ECHO off\n"%_prog%"  "%dp0%\\node_modules\\typescript-language-server'
        "\\lib\\cli.mjs\" %*\n",
        encoding="utf-8",
    )
    real_which = __import__("shutil").which

    def fake_which(c):
        if c == "tsls":
            return str(cmd)
        return real_which(c)  # node 探测走真实查找

    monkeypatch.setattr("uniclaw.tools.lsp.client.shutil.which", fake_which)
    launch = resolve_launch("tsls")
    assert launch[0].lower().endswith(("node", "node.exe"))
    assert launch[-1].endswith("cli.mjs")


# ── 诊断缓存 ──────────────────────────────────────────


def _client() -> LspClient:
    return LspClient(["fake"], "/tmp", name="test")


def test_diagnostics_cache_normalize_uri():
    """publishDiagnostics 回显 uri 与发送形态不一致时缓存 key 仍匹配。"""
    c = _client()
    sent = "file:///d:/code/a.py"
    echoed = "file:///d%3A/code/a.py"  # 服务器回显形态
    c._dispatch(
        {"method": "textDocument/publishDiagnostics",
         "params": {"uri": echoed, "diagnostics": [{"severity": 1, "message": "x"}]}}
    )
    diags = c.get_diagnostics(sent)
    assert diags is not None and len(diags) == 1


def test_invalidate_diagnostics():
    """invalidate_diagnostics 清除指定 uri 的缓存(didOpen 新版本后)。"""
    c = _client()
    uri = "file:///d:/code/a.py"
    c._dispatch(
        {"method": "textDocument/publishDiagnostics",
         "params": {"uri": uri, "diagnostics": [{"message": "x"}]}}
    )
    assert c.get_diagnostics(uri) is not None
    c.invalidate_diagnostics(uri)
    assert c.get_diagnostics(uri) is None


def test_dispatch_ready_notification():
    """ready_notification(如 $/typescriptVersion)触发 ready 事件。"""
    c = LspClient(["fake"], "/tmp", name="test", ready_notification="$/typescriptVersion")
    assert not c._ready.is_set()
    c._dispatch({"method": "$/typescriptVersion", "params": {"version": "1.0.0"}})
    assert c._ready.is_set()


# ── 服务器注册表 ──────────────────────────────────────────


@pytest.mark.parametrize(
    "path,lang",
    [
        ("a.py", "python"),
        ("a.ts", "javascript"),
        ("a.js", "javascript"),
        ("a.html", "html"),
        ("a.css", "css"),
        ("a.md", None),
    ],
)
def test_detect_language(path, lang):
    assert detect_language(path) == lang


@pytest.mark.parametrize(
    "path,lid",
    [
        ("a.py", "python"),
        ("a.ts", "typescript"),
        ("a.js", "javascript"),
        ("a.html", "html"),
        ("a.css", "css"),
        ("a.unknown", "plaintext"),
    ],
)
def test_language_id_for(path, lid):
    assert language_id_for(path) == lid


def test_get_server_def():
    """内置注册表定义完整(命令/参数/扩展名)。"""
    d = get_server_def("python")
    assert d["command"] == ["pyright-langserver"]
    assert d["args"] == ["--stdio"]
    assert get_server_def("nolang") is None


@pytest.mark.parametrize("lang", ["python", "javascript", "html", "css"])
def test_server_install_hint(lang):
    assert server_install_hint(lang)  # 非空提示


# ── 位置解析 ──────────────────────────────────────────


def test_parse_position_plain():
    path, line, col = _parse_position("src/main.py")
    assert path.name == "main.py" and line == 0 and col == 0


def test_parse_position_inline_line():
    path, line, col = _parse_position("src/main.py:12")
    assert line == 11 and col == 0  # 1-based → 0-based


def test_parse_position_inline_line_col():
    path, line, col = _parse_position("src/main.py:12:5")
    assert line == 11 and col == 4


def test_parse_position_windows_drive():
    """Windows 盘符路径不被误拆(盘符冒号与行号后缀共存)。"""
    path, line, col = _parse_position("D:\\code\\x.py:10:3")
    assert str(path).startswith("D:\\") and line == 9 and col == 2


def test_parse_position_explicit_overrides():
    """显式 line/column 参数优先于内嵌后缀。"""
    path, line, col = _parse_position("src/main.py:5", line=20, column=7)
    assert line == 19 and col == 6


# ── 结果格式化 ──────────────────────────────────────────


def test_format_diagnostics_sorted_and_summary():
    """诊断按 severity 排序、带汇总、无问题时有提示。"""
    diags = [
        {"severity": 2, "range": {"start": {"line": 1, "character": 0}}, "message": "w"},
        {"severity": 1, "range": {"start": {"line": 0, "character": 5}}, "message": "e"},
    ]
    out = _format_diagnostics(Path("/x/a.py"), diags)
    assert "共 2 条诊断" in out and "(1 错误 / 1 警告)" in out
    # 错误在警告之前
    assert out.index("[错误]") < out.index("[警告]")
    empty = _format_diagnostics(Path("/x/a.py"), [])
    assert "未发现诊断" in empty


def test_format_locations_location_and_locationlink():
    """兼容 Location(uri/range)与 LocationLink(targetUri/targetRange)。"""
    loc = {"uri": "file:///d:/a.py", "range": {"start": {"line": 0, "character": 0},
                                              "end": {"line": 0, "character": 1}}}
    link = {"targetUri": "file:///d:/b.py", "targetRange": {
        "start": {"line": 2, "character": 4}, "end": {"line": 2, "character": 8}}}
    out = _format_locations([loc, link])
    assert "a.py:1:1" in out and "b.py:3:5" in out
    assert _format_locations([]) == "未找到匹配位置。"


def test_format_locations_snippet_from_disk(tmp_path):
    """目标文件在磁盘时附带代码摘录。"""
    f = tmp_path / "t.py"
    f.write_text("def target_fn(x):\n    pass\n", encoding="utf-8")
    items = [{"uri": proto.path_to_uri(str(f)),
              "range": {"start": {"line": 0, "character": 4},
                        "end": {"line": 0, "character": 13}}}]
    out = _format_locations(items)
    assert "target_fn" in out  # 摘录包含函数名


def test_format_hover_variants():
    """悬停内容兼容字符串/MarkupContent/标记列表。"""
    assert "fn(a: int)" in _format_hover({"contents": {"kind": "markdown",
                                                       "value": "fn(a: int)"}})
    assert "text" in _format_hover("text")
    assert "v2" in _format_hover({"contents": [{"language": "python", "value": "v2"}]})
    assert "无悬停信息" in _format_hover(None)


def test_format_symbols_tree():
    """documentSymbol 递归渲染为缩进树。"""
    symbols = [{"name": "Foo", "kind": 5,
                "selectionRange": {"start": {"line": 0, "character": 0}},
                "children": [{"name": "bar", "kind": 6,
                              "selectionRange": {"start": {"line": 1, "character": 4}}}]}]
    out = _format_symbols(symbols)
    assert any("L1 [类] Foo" in ln for ln in out)
    assert any("L2 [方法] bar" in ln for ln in out)


# ── 会话键控与生命周期 ──────────────────────────────────────────


def test_manager_key_language_rootdir():
    """会话键控 = (language, root_dir):不同项目不共享、同项目共享。"""
    mgr = LSPManager()
    assert mgr._key("python", "/proj/a") == ("python", "/proj/a")
    assert mgr._key("python", "/proj/b") == ("python", "/proj/b")
    # 同 root_dir 同 language → 同 key
    assert mgr._key("python", "/proj/a") == mgr._key("python", "/proj/a")


def test_lsp_session_init_identity_only():
    """LspSession 初始化只收 (language, root_dir);config 是动态字段。"""
    s = LspSession("python", Path("/proj"))
    assert s.root_dir == Path("/proj") and s.config is None and s.client is None


@pytest.mark.asyncio
async def test_close_idle_recycles_stale_and_cleans_dead(tmp_path):
    """close_idle:空闲超时回收;崩溃/未启动记录被清理;活跃会话保留。"""
    mgr = LSPManager()
    # 模拟三个会话:活跃、空闲超时、崩溃(未启动)
    live = LspSession("python", tmp_path)
    stale = LspSession("python", tmp_path)
    dead = LspSession("python", tmp_path)
    stale._last_used -= 9999  # 模拟长期空闲
    dead._last_used -= 9999  # 从未启动且空闲超时 → 应被清理

    live_client = MagicMock(is_alive=True, stop=AsyncMock())
    stale_client = MagicMock(is_alive=True, stop=AsyncMock())
    live.client = live_client
    stale.client = stale_client
    # dead 保持 client=None(从未启动)

    mgr._sessions[("python", str(tmp_path / "live"))] = live
    mgr._sessions[("python", str(tmp_path / "stale"))] = stale
    mgr._sessions[("python", str(tmp_path / "dead"))] = dead

    n = await mgr.close_idle(idle_seconds=60)
    remaining = mgr.list_sessions()
    # dead(未启动)被直接清掉,stale 被回收,live 保留
    assert n == 1  # stale 是唯一需要 close 的
    assert len(remaining) == 1
    assert remaining[0][1].endswith("live")
