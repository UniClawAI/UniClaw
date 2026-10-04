"""LSP 协议层 — 帧编解码与协议常量。

LSP 基于 JSON-RPC 2.0,通过 Content-Length 头帧传输:
    Content-Length: <N>\r\n\r\n<JSON 正文 N 字节>

只包含纯函数/流式编解码,不依赖子进程,便于单测。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any


class FrameError(Exception):
    """LSP 帧解析错误。"""


# ── 协议方法名常量 ──────────────────────────────────────────

# 生命周期
INITIALIZE = "initialize"
INITIALIZED = "initialized"
SHUTDOWN = "shutdown"
EXIT = "exit"

# 文档同步
DID_OPEN = "textDocument/didOpen"
DID_CHANGE = "textDocument/didChange"
DID_CLOSE = "textDocument/didClose"

# 诊断
PUBLISH_DIAGNOSTICS = "textDocument/publishDiagnostics"
DIAGNOSTIC = "textDocument/diagnostic"

# 查询能力
DEFINITION = "textDocument/definition"
HOVER = "textDocument/hover"
REFERENCES = "textDocument/references"
DOCUMENT_SYMBOL = "textDocument/documentSymbol"

# 服务器主动通知(需要静默跳过)
SERVER_NOTIFICATIONS = {
    "window/logMessage",
    "window/showMessage",
    "window/showMessageRequest",
    "window/workDoneProgress/create",
    "telemetry/event",
    "$/progress",
    "$/cancelRequest",
    "textDocument/publishDiagnostics",  # 由 client 缓存处理
}


# ── URI 规范化 ──────────────────────────────────────────

def path_to_uri(path: str) -> str:
    """本地路径 → file:// URI(标准格式,Windows 盘符小写带冒号: file:///d:/x)。

    使用 VS Code 标准的小写盘符格式:typescript-language-server 等对 uri
    大小写敏感(区分 D: 与 d:),大写盘符会导致文档查找失败、查询返回空;
    pyright 大小写不敏感,回显时可能 URL 编码盘符冒号(%3A),由 normalize_uri
    统一规范化。path 段做百分号编码(保留 / 与冒号),空格/中文/# 等特殊字符
    正确转义,普通 ASCII 路径编码后不变,不影响现有服务器兼容。
    """
    p = str(Path(path).resolve()).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        p = p[0].lower() + p[1:]  # "D:/x" → "d:/x"
    if not p.startswith("/"):
        p = "/" + p
    return f"file://{urllib.parse.quote(p, safe='/:')}"


def uri_to_path(uri: str) -> str:
    """file:// URI → 本地路径(兼容带/不带盘符冒号、%XX 编码、localhost host;
    Windows 盘符大小写不敏感)。"""
    if not uri.startswith("file://"):
        return uri
    p = uri[len("file://"):]
    if p.startswith("localhost/"):  # file://localhost/... 带 host 形式
        p = p[len("localhost/"):]
    p = urllib.parse.unquote(p)  # 全面解码 %3A/%20/中文 等
    if os.name == "nt":
        # /D:/code/x.py | /D/code/x.py | D:/code/x.py → D:\code\x.py
        if re.match(r"^[A-Za-z]:", p):
            rest = p[2:].lstrip("/")
            return f"{p[0].upper()}:\\" + rest.replace("/", "\\")
        m = re.match(r"^/([A-Za-z]):?(?=/|$)", p)
        if m:
            rest = p[len(m.group(0)):].lstrip("/")
            return f"{m.group(1).upper()}:\\" + rest.replace("/", "\\")
    return p  # POSIX: file:///x → /x


def normalize_uri(uri: str) -> str:
    """统一 uri 规范(用于缓存 key):解析回本地路径再重新生成。

    非 file:// 的 uri(如 untitled:Untitled-1)不参与本地文件缓存,原样返回。
    """
    if not uri.startswith("file://"):
        return uri
    return path_to_uri(uri_to_path(uri))


def encode_message(msg: dict) -> bytes:
    """将消息编码为 LSP 帧字节(Content-Length 头 + JSON 正文)。"""
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


async def read_message(reader: Any, timeout: float | None = None) -> dict:
    """从 asyncio StreamReader 读取一条 LSP 消息。

    LSP 头部可含多个字段(Content-Length 及可选头如 Content-Type),
    以空行结束;循环读取头部直到空行,缺失 Content-Length 视为帧错误。

    Args:
        reader: asyncio.StreamReader(子进程 stdout 或 socket 流)。
        timeout: 秒;None 表示无限等待。

    Returns:
        解析后的消息 dict。

    Raises:
        EOFError: 流已关闭(含 body 中途断开,由 IncompleteReadError 归一)。
        asyncio.TimeoutError: 超时。
        FrameError: 帧头非法。
    """
    async def read_line():
        if timeout is not None:
            return await asyncio.wait_for(reader.readline(), timeout)
        return await reader.readline()

    length: int | None = None
    # 读取头部各行,直到空行(头部结束)
    while True:
        line = await read_line()
        if not line:
            raise EOFError("LSP 流已关闭")
        if line.strip() == b"":  # 空行 → 头部结束
            break
        if line.startswith(b"Content-Length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError as e:
                raise FrameError(f"非法 Content-Length: {line!r}") from e
    if length is None:
        raise FrameError("缺少 Content-Length 头")
    if length <= 0 or length > 64 * 1024 * 1024:
        raise FrameError(f"非法消息长度: {length}")

    try:
        if timeout is not None:
            body = await asyncio.wait_for(reader.readexactly(length), timeout)
        else:
            body = await reader.readexactly(length)
    except asyncio.IncompleteReadError as e:
        raise EOFError("LSP 流在消息体中提前关闭") from e

    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise FrameError(f"JSON 解析失败: {e}") from e
