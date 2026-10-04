"""LspClient — 单个语言服务器子进程的 JSON-RPC 客户端。

职责:
- 启动/销毁语言服务器子进程(Windows 下自动解析 .cmd/.bat 真实入口)
- initialize / initialized 握手
- 请求-响应按 id 分发 + 通知处理(诊断缓存等)
- 崩溃检测(读循环退出 → is_alive 为 False)
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from uniclaw.console.ui import err, warn

from .protocol import (
    EXIT,
    INITIALIZE,
    INITIALIZED,
    LspBizError,
    PUBLISH_DIAGNOSTICS,
    SHUTDOWN,
    encode_message,
    normalize_uri,
    path_to_uri,
    read_message,
)

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

# 单请求默认超时
REQUEST_TIMEOUT = 15.0

# 客户端能力完整声明(默认)。部分服务器据此决定功能开关:
# - typescript-language-server: publishDiagnostics 声明 → diagnosticsSupport
# - vscode-css/html-language-server: 完整声明会注册 pull diagnosticProvider
#   但 pull 请求未实现(-32601),改用最小声明(见 servers.py minimal_capabilities)
_FULL_CLIENT_CAPABILITIES: dict = {
    "textDocument": {
        "synchronization": {
            "dynamicRegistration": True,
            "willSave": False,
            "willSaveWaitUntil": False,
            "didSave": False,
        },
        "publishDiagnostics": {
            "relatedInformation": True,
            "tagSupport": {"valueSet": [1, 2]},
            "versionSupport": True,
            "codeDescriptionSupport": True,
            "dataSupport": True,
        },
        "completion": {
            "dynamicRegistration": True,
            "completionItem": {
                "snippetSupport": False,
                "commitCharactersSupport": False,
            },
        },
        "definition": {"linkSupport": True},
        "hover": {"dynamicRegistration": True, "contentFormat": ["markdown", "plaintext"]},
        "references": {"dynamicRegistration": True},
        "documentSymbol": {
            "dynamicRegistration": True,
            "hierarchicalDocumentSymbolSupport": True,
        },
    },
    "workspace": {
        "didChangeWatchedFiles": {"dynamicRegistration": True},
        "workspaceFolders": True,
        "configuration": True,
    },
}


class LspRpcError(Exception):
    """LSP 请求返回 error 响应。"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"LSP 错误 {code}: {message}")


def resolve_launch(command: str) -> list[str]:
    """把 LSP 命令解析为可直接 CreateProcess 的 argv。

    Windows 上 npm 全局安装的服务器是 .cmd/.bat 包装器(asyncio 无法直接
    CreateProcess 批处理文件),此函数读取包装脚本内容,解析出真实入口
    (如 ``node <npm_prefix>\\node_modules\\pyright\\langserver.index.js``)。
    解析失败时回退为原命令(asyncio 会按 PATHEXT 在 PATH 中查找可执行文件,
    例如 pip 安装的 pyright 是真实 .exe,可正常启动)。

    Args:
        command: 服务器命令(可含路径)。

    Returns:
        可直接 create_subprocess_exec 拼接的 argv 列表(不含启动参数,
        如 --stdio 由调用方追加)。
    """
    # 与 servers.probe_command 口径一致:优先当前解释器同目录(venv Scripts),
    # pip/uv 安装的服务器(pyright 等)在 venv 未激活、不在 PATH 时也能命中
    venv_bin = Path(sys.executable).resolve().parent
    for name in (command, f"{command}.exe", f"{command}.cmd"):
        candidate = venv_bin / name
        if candidate.is_file():
            cmd_path = str(candidate)
            break
    else:
        cmd_path = shutil.which(command) or command
    if cmd_path.lower().endswith((".cmd", ".bat")):
        try:
            text = Path(cmd_path).read_text(encoding="utf-8", errors="ignore")
            # 匹配 "%dp0%\node_modules\xxx\yyy.(js|mjs|cjs)" 形式(npm 全局 .cmd 包装)
            m = re.search(r'"([^"]*?%dp0%[^"]*\.(?:js|mjs|cjs))"', text) or re.search(
                r'"([^"]*node_modules[^"]*\.(?:js|mjs|cjs))"', text
            )
            if m:
                js = m.group(1).replace("%dp0%", str(Path(cmd_path).parent))
                node = shutil.which("node") or "node"
                return [node, js]
        except OSError:
            pass
    return [cmd_path]


class LspClient:
    """一个语言服务器子进程客户端(单连接,非并发安全,调用需外部加锁)。"""

    def __init__(
        self,
        launch: list[str],
        root_dir: str,
        name: str = "lsp",
        config: AppConfig | None = None,
        initialization_options: dict | None = None,
        ready_notification: str | None = None,
        capabilities: dict | None = None,
    ):
        self._launch = launch
        self._root = root_dir
        self.name = name
        self._config = config  # 仅用于 console.ui 输出通道
        self._init_options = initialization_options or {}
        # 客户端能力声明:由调用方按服务器定制(完整声明会令部分服务器如
        # vscode-css 切到 pull 模式但 pull 未实现,导致诊断丢失)
        self._client_caps = capabilities
        # 部分服务器(typescript-language-server)initialize 响应后仍需时间
        # 启动语言内核(tsserver),以某条通知为 ready 信号;收到前 didOpen 会被拒
        self._ready_notification = ready_notification
        self._ready = asyncio.Event()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._initialized = False
        self._last_used = time.monotonic()
        self._capabilities: dict = {}  # 服务器返回的能力,start() 时填充
        # uri → (diagnostics, 时间戳)
        self._diag_cache: dict[str, tuple[list, float]] = {}

    # ── 生命周期 ──────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        """子进程存活且读循环正常。"""
        if self._proc is None:
            return False
        if self._proc.returncode is not None:
            return False
        return not (self._read_task and self._read_task.done())

    async def start(self) -> None:
        """启动子进程并完成 initialize 握手。失败时清理并抛出异常。"""
        if self.is_alive:
            return
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._launch,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._root,
                close_fds=True,
            )
        except (OSError, FileNotFoundError) as e:
            raise LspBizError(f"无法启动语言服务器 {self.name}: {e}") from e

        self._read_task = asyncio.create_task(
            self._read_loop(), name=f"lsp-read-{self.name}"
        )
        try:
            capabilities = await self.request(
                INITIALIZE,
                {
                    "processId": None,
                    "rootUri": path_to_uri(self._root),
                    "capabilities": (
                        self._client_caps
                        if self._client_caps is not None
                        else _FULL_CLIENT_CAPABILITIES
                    ),
                    "clientInfo": {"name": "uniclaw-lsp", "version": "0.1"},
                    "initializationOptions": self._init_options,
                    "workspaceFolders": [
                        {"uri": path_to_uri(self._root), "name": self.name}
                    ],
                },
                timeout=30.0,
            )
            self._capabilities: dict = capabilities or {}
            await self.notify(INITIALIZED, {})
            self._initialized = True
            if self._ready_notification:
                # 等待服务器 ready 通知(如 $/typescriptVersion),避免 didOpen 过早被拒
                try:
                    await asyncio.wait_for(self._ready.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger_msg = f"等待 ready 通知 {self._ready_notification} 超时"
                    await err(f"[LSP {self.name}] {logger_msg}", self._config)
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        """优雅关闭(shutdown → exit),然后强制回收。"""
        if self._proc is None:
            return
        try:
            proc = self._proc
            if proc.returncode is None:
                # shutdown 是请求,响应依赖读循环的 _dispatch:
                # 必须先完成握手再取消读循环,否则响应永远收不到
                try:
                    await self.request(SHUTDOWN, {}, timeout=3.0)
                except Exception:
                    pass
                try:
                    await self.notify(EXIT)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except (asyncio.TimeoutError, ProcessLookupError):
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                    # terminate 后仍有界等待,仍不退则强杀
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                            await asyncio.wait_for(proc.wait(), timeout=2.0)
                        except (asyncio.TimeoutError, ProcessLookupError):
                            pass
            else:
                # 进程已退出,等待收割避免僵尸
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        except Exception:
            pass  # 关闭阶段的异常静默处理(进程即将退出,不打扰用户)
        finally:
            # 握手完成/超时后才取消读循环,随后统一回收资源
            if self._read_task and not self._read_task.done():
                self._read_task.cancel()
            self._cleanup_transport()
            self._proc = None
            self._initialized = False
            self._ready.clear()  # 重置 ready 门,restart 后重新等待
            self._diag_cache.clear()  # 跨实例重启后旧诊断作废
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError("语言服务器已关闭"))
            self._pending.clear()

    async def restart(self) -> None:
        """强制重启(崩溃恢复用)。"""
        await self.stop()
        await self.start()

    # ── 消息收发 ──────────────────────────────────────────

    async def _send(self, msg: dict) -> None:
        if self._proc is None or self._proc.returncode is not None:
            raise LspBizError("语言服务器未运行")
        try:
            self._proc.stdin.write(encode_message(msg))
            await self._proc.stdin.drain()
            self._last_used = time.monotonic()
        except (BrokenPipeError, ConnectionResetError) as e:
            raise LspBizError(f"语言服务器管道已断开: {e}") from e

    async def request(self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT) -> Any:
        """发送请求并等待响应(按 id 匹配)。"""
        self._next_id += 1
        msg_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
            )
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise LspBizError(f"LSP 请求超时: {method}(>{timeout}s)")
        except BaseException:
            self._pending.pop(msg_id, None)
            raise

    async def notify(self, method: str, params: dict) -> None:
        """发送通知(无 id,不等响应)。"""
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # ── 读循环 ──────────────────────────────────────────

    async def _read_loop(self) -> None:
        try:
            while True:
                try:
                    msg = await read_message(self._proc.stdout)
                except EOFError:
                    break
                except Exception as e:
                    await err(f"[LSP {self.name}] 读循环异常: {e}", self._config)
                    break
                # 服务器→客户端的请求(如 client/registerCapability):必须响应,
                # 否则服务器可能等待注册完成,后续诊断/分析不推进
                if (
                    "id" in msg
                    and "method" in msg
                    and msg["id"] not in self._pending
                ):
                    await self._respond(msg["id"], msg.get("method", ""))
                    continue
                try:
                    self._dispatch(msg)
                except Exception as e:
                    await warn(f"[LSP {self.name}] 消息处理异常: {e}", self._config)
        finally:
            self._cleanup_transport()

    async def _respond(self, msg_id: int, method: str = "") -> None:
        """响应服务器→客户端请求,按 method 返回合理的最小结果。

        - workspace/applyEdit → {applied: True}(应用了编辑)
        - workspace/configuration → [](无配置项)
        - 其余(registerCapability 等)→ null(接受注册)
        """
        if method == "workspace/applyEdit":
            result: Any = {"applied": True}
        elif method == "workspace/configuration":
            result = []
        else:
            result = None
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})
        except Exception:
            pass

    def _cleanup_transport(self) -> None:
        """关闭子进程管道 transport,避免崩溃/退出后句柄泄漏(Windows Proactor)。"""
        proc = self._proc
        if proc is None:
            return
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            try:
                if not transport.is_closing():
                    transport.close()
            except Exception:
                pass

    def _dispatch(self, msg: dict) -> None:
        if "id" in msg:
            fut = self._pending.pop(msg.get("id"), None)
            if fut is None or fut.done():
                return
            if "error" in msg and msg["error"]:
                fut.set_exception(
                    LspRpcError(
                        msg["error"].get("code", -1),
                        msg["error"].get("message", "未知错误"),
                    )
                )
            else:
                fut.set_result(msg.get("result"))
            return
        # 通知
        method = msg.get("method")
        if method == PUBLISH_DIAGNOSTICS:
            try:
                params = msg.get("params", {})
                # 服务器回显的 uri 可能与发送时大小写/斜杠不同,统一规范为缓存 key
                uri = normalize_uri(params.get("uri", ""))
                self._diag_cache[uri] = (
                    params.get("diagnostics", []) or [],
                    time.monotonic(),
                )
            except Exception:
                pass  # 个别诊断解析失败不影响整体,静默跳过
        elif self._ready_notification and method == self._ready_notification:
            self._ready.set()

    # ── 诊断缓存 ──────────────────────────────────────────

    def get_diagnostics(self, uri: str) -> list | None:
        """读取缓存的诊断;None 表示尚无(服务器未推送)。uri 自动规范化。

        缓存只随 didOpen/失效显式清除,不做时间老化——文件未变时旧缓存
        就是当前状态的准确结果,应直接返回(时间老化会误杀未变文件的诊断)。
        """
        item = self._diag_cache.get(normalize_uri(uri))
        if not item:
            return None
        return item[0]

    def invalidate_diagnostics(self, uri: str) -> None:
        """使某 uri 的诊断缓存失效(didOpen 新版本后旧诊断作废,等服务器重推)。"""
        self._diag_cache.pop(normalize_uri(uri), None)

    @property
    def capabilities(self) -> dict:
        return self._capabilities

    @property
    def last_used(self) -> float:
        return self._last_used