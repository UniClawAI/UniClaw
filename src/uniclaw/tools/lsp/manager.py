"""LSPManager — 语言服务器会话管理。

- 按语言懒启动服务器(首次查询时),会话内复用
- 文档同步:AI 直接读写磁盘,查询前比较内容 hash,变化则全量 didOpen
- 崩溃自动重启(每会话最多 MAX_RESTARTS 次)
- 空闲惰性回收 + 显式 lsp_stop
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from uniclaw.console.ui import warn

from .client import LspClient, LspRpcError, resolve_launch
from .protocol import DIAGNOSTIC, DID_OPEN, LspBizError, normalize_uri, path_to_uri
from .servers import (
    BUILTIN_SERVERS,
    detect_tsserver_path,
    get_server_def,
    language_id_for,
    probe_command,
    server_install_hint,
)

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

# 空闲回收阈值(秒)
IDLE_RECYCLE_SECONDS = 30 * 60
# 单会话最大崩溃重启次数
MAX_RESTARTS = 3
# 诊断等待上限(didOpen 后服务器索引需要时间)
DIAG_WAIT_TIMEOUT = 10.0
# 诊断轮询间隔
DIAG_POLL_INTERVAL = 0.2
# 无推送时提前 pull 的等待秒数:pyright 在分析完成前收到 pull/definition
# 会返回空并挂死,必须先等到"分析完成"信号(push 到达)才能安全 pull
PULL_EARLY_SECONDS = 1.5


@dataclass
class DocState:
    version: int
    content_hash: str


class LspSession:
    """一个语言服务器的会话(客户端 + 文档同步状态)。

    会话身份 = (language, root_dir):workspace 根在创建时固定,决定服务器进程;
    config(输出通道)是动态引用,由 LSPManager 在每次查询时更新为最近调用者的
    AppConfig —— 服务器跨会话共享,但 warn/err 要发给实际使用者。
    """

    def __init__(self, language: str, root_dir: Path | None):
        self.language = language
        self.root_dir = root_dir
        self.config: AppConfig | None = None  # 输出通道,由 manager 动态设置
        self.server_def = get_server_def(language)
        self.client: LspClient | None = None
        self._docs: dict[str, DocState] = {}
        self._lock = asyncio.Lock()
        self._restarts = 0
        self._last_used = time.monotonic()
        self._start_error: Optional[str] = None

    # ── 状态 ──────────────────────────────────────────

    @property
    def is_ready(self) -> bool:
        return self.client is not None and self.client.is_alive

    @property
    def queued_documents(self) -> int:
        return len(self._docs)

    # ── 生命周期 ──────────────────────────────────────────

    async def ensure_started(self) -> None:
        """懒启动(或崩溃后自动重启)。并发安全。"""
        if self.is_ready:
            self._last_used = time.monotonic()
            return
        async with self._lock:
            if self.is_ready:
                return
            # 连续失败达上限才停止重试;未达上限继续尝试(临时性故障)
            if self._start_error and self._restarts >= MAX_RESTARTS:
                raise LspBizError(self._start_error)

            if self.client is not None and not self.client.is_alive:
                # 运行时崩溃(读循环退出/进程死亡):计入连续崩溃次数,
                # 与启动失败同口径,达上限才停止自动重启
                self._restarts += 1
                # 读循环异常退出(如帧错误)时子进程可能仍存活:
                # 替换前先 stop 释放残留进程/管道,避免句柄泄漏
                old = self.client
                self.client = None
                try:
                    await old.stop()
                except Exception:
                    pass
            if self._restarts >= MAX_RESTARTS:
                from .tools import lsp_stop  # 延迟导入,避免与 tools.py 循环导入

                raise LspBizError(
                    f"语言服务器 {self.server_def['name']} 已连续崩溃 "
                    f"{MAX_RESTARTS} 次,停止自动重启。"
                    f"可用 {lsp_stop.name} 后重试。"
                )
            try:
                launch = resolve_launch(self.server_def["command"][0])
                # 部分服务器需要 initializationOptions(如 TS 服务器必须指定
                # tsserver.path,否则找不到全局 typescript 编译器)
                init_options: dict = {}
                tsserver_missing = False
                if self.server_def.get("needs_tsserver"):
                    tsserver = await detect_tsserver_path(self.root_dir)
                    if tsserver:
                        init_options["tsserver"] = {"path": tsserver}
                    else:
                        tsserver_missing = True
                client = LspClient(
                    launch + list(self.server_def.get("args", [])),
                    str(self.root_dir),
                    name=self.server_def["name"],
                    config=self.config,
                    initialization_options=init_options,
                    ready_notification=self.server_def.get("ready_notification"),
                    # html/css(vscode-languageserver 系)完整 capabilities 会注册
                    # 假 pull diagnosticProvider 导致诊断丢失,使用最小声明走 push
                    capabilities=(
                        {} if self.server_def.get("minimal_capabilities") else None
                    ),
                )
                await client.start()
                self.client = client
                # 注意:不再在成功启动时清零 _restarts —— 运行时崩溃会被计入,
                # 只有显式 close()(lsp_stop)才重置连续崩溃计数
                self._start_error = None
                self._last_used = time.monotonic()
                # 重启后服务器内存态已丢失,重新打开所有同步过的文档
                for path in list(self._docs.keys()):
                    try:
                        await self._do_open(path, force=True)
                    except Exception as e:
                        try:
                            await warn(
                                f"[LSP {self.language}] 重启后重开文档失败: {path}: {e}",
                                self.config,
                            )
                        except Exception:
                            pass
            except Exception as e:
                self._start_error = str(e)
                self._restarts += 1
                msg = f"启动语言服务器失败: {e}"
                if tsserver_missing:
                    msg += (
                        "。检测不到 typescript 编译器(tsserver),"
                        "请安装: npm i -g typescript@5"
                    )
                raise LspBizError(msg) from e

    async def close(self) -> None:
        """停止服务器(释放子进程),并重置会话状态供下次启动。"""
        async with self._lock:
            if self.client is not None:
                await self.client.stop()
                self.client = None
            self._docs.clear()
            self._restarts = 0
            self._start_error = None  # 清掉失败记录,允许重新启动

    # ── 文档同步 ──────────────────────────────────────────

    async def sync_document(self, path: str) -> str:
        """同步文件内容到服务器(didOpen 全量,内容未变则跳过),并顺带刷新所有已跟踪文档。

        AI 直接改磁盘文件,pyright 无文件监听,不会感知变化;若目标文件(如
        import 的依赖)曾被打开过且已被修改,查询前必须重新 didOpen 才能拿到
        最新定义/诊断。首次查询的指定文件在此同步,其余已跟踪文件由
        _resync_all 刷新(未跟踪的文件靠 pyright 磁盘解析,见 _do_open 注释)。

        Returns:
            file:// uri。
        """
        await self.ensure_started()
        resolved = str(Path(path).resolve())
        async with self._lock:
            await self._resync_all()
            await self._do_open(resolved)
            self._last_used = time.monotonic()
            return path_to_uri(resolved)

    async def _resync_all(self) -> None:
        """刷新所有已跟踪文档:hash 变化则重新 didOpen(覆盖"打开过的文件被改")。"""
        for path in list(self._docs.keys()):
            try:
                await self._do_open(path)
            except FileNotFoundError:
                self._docs.pop(path, None)  # 文件已删除:清除失效跟踪
            except OSError:
                pass  # 其他 IO 错误(权限等)暂不处理,保留跟踪

    async def _do_open(self, resolved_path: str, force: bool = False) -> None:
        text = Path(resolved_path).read_text(encoding="utf-8", errors="replace")
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
        prev = self._docs.get(resolved_path)
        if not force and prev and prev.content_hash == h:
            return
        client = self.client
        if client is None:
            return
        version = prev.version + 1 if prev else 1
        uri = path_to_uri(resolved_path)
        await client.notify(
            DID_OPEN,
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id_for(resolved_path),
                    "version": version,
                    "text": text,
                }
            },
        )
        # 已推送新版本,旧诊断作废;等服务器推送新诊断
        client.invalidate_diagnostics(uri)
        self._docs[resolved_path] = DocState(version, h)

    # ── 诊断 ──────────────────────────────────────────

    async def get_diagnostics(self, path: str) -> Optional[list]:
        """同步文档并获取诊断。

        目标文件诊断缓存无条件失效:AI 可能修改了未跟踪的依赖文件
        (目标文件 hash 未变、didOpen 被跳过),失效后拉取才能拿到
        服务器最新诊断而非我们缓存的旧结果。

        获取顺序:先轮询 push(publishDiagnostics,分析完成即推)→
        无推送等待 PULL_EARLY_SECONDS 后提前 pull(内容未变服务器不重推,
        直接取当前快照;pyright 分析未完成时 pull 会挂死,故必须先等
        push 信号)→ 10s 兜底再 pull。三路全无返回 None。
        """
        await self.ensure_started()
        uri = await self.sync_document(path)
        client = self.client
        if client is None:
            return None
        client.invalidate_diagnostics(uri)
        deadline = time.monotonic() + DIAG_WAIT_TIMEOUT
        pull_attempted = False
        while time.monotonic() < deadline:
            diags = client.get_diagnostics(uri)
            if diags is not None:
                self._last_used = time.monotonic()
                return diags
            # 内容未变的文档服务器不会重推:等待 PULL_EARLY_SECONDS 后
            # 提前 pull 当前分析快照(此时分析应已完成,pull 安全)
            if (
                not pull_attempted
                and time.monotonic() - deadline + DIAG_WAIT_TIMEOUT
                >= PULL_EARLY_SECONDS
            ):
                pull_attempted = True
                diags = await self._pull_diagnostics(client, uri)
                if diags is not None:
                    self._last_used = time.monotonic()
                    return diags
            await asyncio.sleep(DIAG_POLL_INTERVAL)
        # 10s 上限兜底(服务器可能刚刚完成分析)
        diags = await self._pull_diagnostics(client, uri)
        if diags is not None:
            self._last_used = time.monotonic()
            return diags
        return client.get_diagnostics(uri)

    async def _pull_diagnostics(self, client, uri: str) -> Optional[list]:
        """pull 模式拉取诊断;成功(含空 items)写缓存;不支持/失败返回 None。"""
        try:
            result = await client.request(
                DIAGNOSTIC,
                {"textDocument": {"uri": uri}, "previousResultId": None},
            )
            if isinstance(result, dict) and "items" in result:
                items = result["items"] or []
                # pull 结果写入诊断缓存,避免下次查询重走等待+pull
                client._diag_cache[normalize_uri(uri)] = (
                    list(items),
                    time.monotonic(),
                )
                return list(items)
        except Exception:
            pass  # 服务器不支持 pull 或请求失败,返回 None 走推送轮询
        return None

    # ── 查询转发 ──────────────────────────────────────────

    async def request(self, method: str, params: dict) -> object:
        """发送查询请求,返回原始 result(可能是 dict/list/text)。"""
        await self.ensure_started()
        self._last_used = time.monotonic()
        assert self.client is not None
        return await self.client.request(method, params)


class LSPManager:
    """管理多个 LspSession 的单例,按 (language, root_dir) 键控。

    每个项目的每种语言一个独立服务器:不同 root_dir 的项目 workspace 根不同,
    pyright 的项目索引/import 解析依赖 workspace,不能跨项目共享;同一 root_dir
    内的所有会话(主代理、子代理、同项目多会话)共享同一服务器,避免重复进程。
    """

    _instance: Optional["LSPManager"] = None

    def __init__(self):
        self._sessions: dict[tuple[str, str], LspSession] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "LSPManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _key(language: str, root_dir: Path | str | None) -> tuple[str, str]:
        return (language, str(root_dir) if root_dir is not None else "")

    async def get_session(self, language: str, config: AppConfig) -> LspSession:
        """获取(或创建)语言+项目的会话;服务器缺失时抛出带安装提示的错误。

        查询入口顺带惰性回收空闲会话(30 分钟未使用自动释放服务器子进程)。
        """
        try:
            await self.close_idle()  # 锁外调用,避免与下方 _lock 嵌套
        except Exception:
            pass
        # config.root_dir 可为 None(无项目上下文):回退当前工作目录,
        # 避免 cwd="None" 导致子进程启动失败且掩盖真实原因
        root = config.root_dir if config.root_dir is not None else Path.cwd()
        key = self._key(language, root)
        async with self._lock:
            session = self._sessions.get(key)
            if session is None:
                server_def = get_server_def(language)
                if server_def is None:
                    raise LspBizError(
                        f"不支持的语言 '{language}',当前注册: "
                        f"{', '.join(sorted(_SERVER_NAMES))}"
                    )
                if not probe_command(server_def):
                    hint = server_install_hint(language)
                    raise LspBizError(
                        f"未找到语言服务器 {server_def['name']} "
                        f"(探测命令: {server_def['command'][0]})。"
                        f"请先安装: {hint}。"
                    )
                session = LspSession(language, root)
                self._sessions[key] = session
            # 输出通道跟随最近调用者:服务器跨会话共享,但 warn/err 要发给
            # 实际使用者(而非创建者);命中已有会话时同样刷新。
            session.config = config
            if session.client is not None:
                session.client._config = config
            return session

    def list_sessions(self) -> list[tuple[str, str, LspSession]]:
        """返回 [(language, root_dir, session)] 快照。"""
        return [(lang, root, s) for (lang, root), s in self._sessions.items()]

    async def close_session(self, language: str, root_dir: Path | str = "") -> bool:
        """停止指定语言(可限定 root_dir)的会话;返回是否命中。

        root_dir 为空 → 停止该语言在所有项目的会话(lsp_stop 默认语义)。
        计算目标与移除在锁内一次性完成,避免并发 get_session 重建后误杀。
        """
        async with self._lock:
            targets = [
                (lang, root)
                for (lang, root), s in self._sessions.items()
                if lang == language and (not root_dir or root == str(root_dir))
            ]
            sessions = [self._sessions.pop(k) for k in targets]
        for session in sessions:
            try:
                await session.close()
            except Exception:
                pass
        return bool(sessions)

    async def close_idle(self, idle_seconds: float = IDLE_RECYCLE_SECONDS) -> int:
        """回收空闲超时的会话(惰性回收,由查询入口触发)。返回回收数。

        - 已启动且空闲超时(> idle_seconds 未使用)→ 关闭并释放语言服务器子进程
        - 崩溃/未启动(client 为空或已死)→ 无子进程可释放,直接从表移除
        """
        async with self._lock:
            now = time.monotonic()
            idle_keys = []
            crash_sessions = []
            for key, s in list(self._sessions.items()):
                if s.client is not None and not s.client.is_alive:
                    # 曾启动但已崩溃:读循环异常退出(如帧错误)时子进程可能
                    # 仍存活,锁内 pop 后再锁外 close 释放进程/管道句柄
                    # (直接 pop 而非加入 close_session,避免与并发
                    # get_session 重启该会话后误杀新会话)
                    crash_sessions.append(self._sessions.pop(key))
                elif s.client is None and now - s._last_used > idle_seconds:
                    # 创建了但从未启动且空闲超时:同样无进程,清掉
                    self._sessions.pop(key, None)
                elif now - s._last_used > idle_seconds:
                    idle_keys.append(key)  # 启动中且空闲超时 → 关闭释放进程
        for session in crash_sessions:
            try:
                await session.close()
            except Exception:
                pass
        for lang, root in idle_keys:
            await self.close_session(lang, root)  # 锁外关闭,避免与 session._lock 嵌套
        return len(idle_keys) + len(crash_sessions)

    async def close_all(self) -> None:
        """停止所有会话(应用退出/会话重置时调用)。"""
        for lang in set(lang for lang, _ in self._sessions.keys()):
            await self.close_session(lang)


# 供 get_session 错误提示列出已注册语言
_SERVER_NAMES = set(BUILTIN_SERVERS.keys())
