from __future__ import annotations
import asyncio
import json
import re
import httpx
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from uniclaw.context import get_app_dir, Scope
from uniclaw.console.ui import err, ok
from uniclaw.tools.base import Tool
from uniclaw.utils.constants import TOOL_ERROR

if TYPE_CHECKING:
    from uniclaw.config import AppConfig
    from uniclaw.tools.base import ToolRuntime

# 连接/握手/工具发现的默认超时(秒)。mcp.exa.ai 首次握手需数秒,10s 太紧。
DEFAULT_CONNECT_TIMEOUT = 15.0
# 工具调用读超时默认(秒)。exa_agent_run 这类长任务即使 effort=minimal 也常
# 超过 10s;旧默认 10s 导致持续 ReadTimeout 且拿不到 runId,无法续跑。
# 对齐 MCP SDK 推荐的 read 超时(300s)。
DEFAULT_CALL_TIMEOUT = 300.0


def _flat_error(e: BaseException) -> str:
    """把异常压平成一行可读文本。

    anyio 的 TaskGroup 失败会包装成 ExceptionGroup,str() 只显示
    "unhandled errors in a TaskGroup (1 sub-exception)",真实原因藏在
    子异常里 — 递归展开后拼接,避免报错无法定位。
    """
    if isinstance(e, BaseExceptionGroup) and e.exceptions:
        return "; ".join(_flat_error(sub) for sub in e.exceptions)
    return str(e) or type(e).__name__


def _is_timeout_error(e: BaseException) -> bool:
    """判断异常是否为超时(含 ExceptionGroup 里的超时叶子)。

    覆盖 asyncio/anyio 的 TimeoutError 与 httpx 的 ReadTimeout/ConnectTimeout
    等(按类名匹配,不强制 import httpx 异常层次,避免版本差异)。
    """
    if isinstance(e, TimeoutError):  # asyncio.TimeoutError 是其别名(Python 3.11+)
        return True
    if isinstance(e, BaseExceptionGroup) and e.exceptions:
        return any(_is_timeout_error(sub) for sub in e.exceptions)
    name = type(e).__name__
    if "Timeout" in name or "timeout" in name:
        return True
    msg = str(e).lower()
    return "timeout" in msg or "timed out" in msg or "deadline" in msg


def _resolve_call_timeout(connection: dict, override: float | None) -> float:
    """解析工具调用读超时(秒):调用参数 > read_timeout > 默认。

    connection["timeout"] 保持旧语义(连接/发现超时),不直接当作调用读超时;
    只有显式配置了 read_timeout 时才覆盖默认值。
    """
    if override is not None:
        try:
            return float(override)
        except (TypeError, ValueError):
            pass
    if "read_timeout" in connection:
        try:
            return float(connection["read_timeout"])
        except (TypeError, ValueError):
            pass
    return DEFAULT_CALL_TIMEOUT


def _build_httpx_timeout(connection: dict) -> httpx.Timeout:
    """构造 httpx 超时:连接/写短超时,读长超时(工具调用可能很久)。

    connection["timeout"] 兼容旧配置(连接超时);read 超时用 read_timeout
    或 DEFAULT_CALL_TIMEOUT,避免长任务被 10s 读超时掐断且拿不到 runId。
    """
    try:
        connect_t = float(connection.get("timeout", DEFAULT_CONNECT_TIMEOUT))
    except (TypeError, ValueError):
        connect_t = DEFAULT_CONNECT_TIMEOUT
    read_t = _resolve_call_timeout(connection, None)
    # 连接阶段不应拖太久;读阶段给足长任务时间
    connect_t = max(1.0, min(connect_t, 60.0))
    return httpx.Timeout(connect_t, read=read_t, write=connect_t, pool=connect_t)


# runId 提取:优先 runId/run_id 字段;id 仅在 status=running 附近出现时才认
_RUN_ID_RE = re.compile(r'"(?:runId|run_id)"\s*:\s*"([^"]+)"')
_RUNNING_ID_RE = re.compile(
    r'"status"\s*:\s*"running"[^}]*?"(?:id|runId|run_id)"\s*:\s*"([^"]+)"'
    r'|"(?:id|runId|run_id)"\s*:\s*"([^"]+)"[^}]*?"status"\s*:\s*"running"',
    re.DOTALL,
)


def _extract_run_ids(text: str) -> list[str]:
    """从工具结果文本中提取 runId(长任务续跑句柄)。"""
    if not text:
        return []
    ids: list[str] = []
    for m in _RUN_ID_RE.finditer(text):
        rid = m.group(1)
        if rid not in ids:
            ids.append(rid)
    if not ids:
        for m in _RUNNING_ID_RE.finditer(text):
            rid = m.group(1) or m.group(2)
            if rid and rid not in ids:
                ids.append(rid)
    return ids


def _annotate_run_id(content, server_name: str, tool_name: str):
    """结果里带 runId 时追加续跑提示,避免调用方整单重来。

    上游(Exa agent_run)文档:超时/未完成时返回 status=running + id,
    再次调用同一工具并只传 runId 即可续跑。上游没有独立的 agent_get 工具,
    续跑入口就是同一个 agent_run(以 runId 为句柄),不要硬造其它工具名。
    """
    if not isinstance(content, str):
        return content
    run_ids = _extract_run_ids(content)
    if not run_ids:
        return content
    rid = run_ids[0]
    resume_tool = f"{server_name}_{tool_name}"
    return (
        f"{content}\n\n"
        f"[续跑提示] 检测到 runId={rid}。"
        f"请再次调用 {resume_tool} 并只传 runId=\"{rid}\" 轮询进度/取结果"
        f"(不要重复传 query 重新发起)。"
    )


def _format_call_error(
    server_name: str,
    tool_name: str,
    e: BaseException,
    phase: str,
    call_timeout: float,
) -> str:
    """按阶段分类包装工具调用失败,超时时区分「未发起」与「可能已创建」。

    - connect/initialize 阶段超时: 请求未发出,任务未创建,需重新发起
    - call 阶段超时: 请求已发出,服务端可能已创建 run 但未返回 runId,
      无法续跑;给出加大 timeout 的建议
    非超时异常只压平原因,不编造 runId。
    """
    reason = _flat_error(e)
    if not _is_timeout_error(e):
        return f"{TOOL_ERROR}: {reason}"

    resume_tool = f"{server_name}_{tool_name}"
    is_agent_run = "agent_run" in tool_name or tool_name.endswith("_run")
    timeout_hint = (
        f"当前读超时约 {call_timeout:.0f}s,"
        f"可传 timeout 参数加大(如 timeout={max(int(call_timeout), 120) * 2})后重试"
    )

    if phase in ("connect", "initialize"):
        return (
            f"{TOOL_ERROR}: 连接/握手超时(未发起任务,需重新发起): {reason}\n"
            f"{timeout_hint};请确认网络可访问该 MCP 服务器后重新调用。"
        )

    if is_agent_run:
        return (
            f"{TOOL_ERROR}: 工具调用超时"
            f"(run 可能已在服务端创建,但本次未返回 runId,无法续跑): {reason}\n"
            f"本次请求已发出。若之前某次调用曾返回过 runId,"
            f"可再次调用 {resume_tool} 并只传 runId=... 续跑;"
            f"真没拿到过 runId 则本次 run 无法续跑,需重新发起。\n"
            f"{timeout_hint}。"
        )
    return (
        f"{TOOL_ERROR}: 工具调用超时(请求已发出,响应未在超时前返回): {reason}\n"
        f"{timeout_hint}。"
    )


@asynccontextmanager
async def _connect_mcp(connection: dict):
    """根据 transport 类型建立 MCP 连接,统一返回 (read, write) 流。

    基于 mcp 2.x:支持 stdio / sse / streamable_http 三种传输。
    mcp 2.x 已移除 websocket 客户端,websocket 配置会直接报错。
    """
    transport = connection.get("transport", "stdio")

    if transport == "stdio":
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_params = StdioServerParameters(
            command=connection.get("command", ""),
            args=connection.get("args", []),
            env=connection.get("env"),
            cwd=connection.get("cwd"),  # 修复:传递cwd参数
        )
        async with stdio_client(server_params) as (read, write):
            yield read, write

    elif transport == "sse":
        from mcp.client.sse import sse_client

        # SSE 的 timeout 是连接建立超时;长读由 httpx 侧 read 超时兜底
        try:
            sse_timeout = float(connection.get("timeout", DEFAULT_CONNECT_TIMEOUT))
        except (TypeError, ValueError):
            sse_timeout = DEFAULT_CONNECT_TIMEOUT
        async with sse_client(
            url=connection["url"],
            headers=connection.get("headers"),
            timeout=sse_timeout,
        ) as (read, write):
            yield read, write

    elif transport == "streamable_http":
        from mcp.client.streamable_http import streamable_http_client

        headers = connection.get("headers")
        proxy = connection.get("proxy")

        # 读超时单独放宽:exa_agent_run 等长任务需要收到 status=running + runId
        client_kwargs = {"timeout": _build_httpx_timeout(connection)}
        if headers:
            client_kwargs["headers"] = headers
        if proxy:
            client_kwargs["proxy"] = proxy
        async with httpx.AsyncClient(**client_kwargs) as http_client:
            try:
                async with streamable_http_client(
                    url=connection["url"],
                    http_client=http_client,
                ) as (read, write):
                    yield read, write
            except RuntimeError as e:
                # asyncio.run() 关闭期间, 存活的 async generator 会被强制 aclose,
                # streamable_http_client 内部的 anyio cancel scope 跨任务退出会抛该异常;
                # 此时进程即将结束, 连接由 OS 回收, 静默返回即可。
                if "cancel scope in a different task" in str(e):
                    return
                raise

    elif transport == "websocket":
        raise RuntimeError(
            "websocket 传输不可用: mcp 2.x 已移除 websocket 客户端,"
            "请改用 sse 或 streamable_http"
        )

    else:
        raise ValueError(f"不支持的传输类型: {transport}")


# 持久会话闲置回收阈值(秒):超过该时长无调用即回收连接。
# 只按闲置判定,不查磁盘 — 会话可能尚未落盘(首轮未保存/A2A 内存态),
# 按磁盘存在性判定会误杀活跃连接、丢失有状态 server 的登录态。
# 会话显式删除由 SessionManager.delete_session 走 close_session_persistent_sessions。
PERSISTENT_SESSION_IDLE_TIMEOUT = 1800


class _PersistentSession:
    """持久化 MCP 会话:连接由独立后台任务持有,跨工具调用复用。

    仅当 mcp.json 中该 server 配置了 "persistent": true 时使用。
    连接的建立/销毁都在同一个后台任务内完成,规避 anyio cancel scope 跨任务问题。
    """

    def __init__(self, server_name: str, connection: dict):
        self._server_name = server_name
        self._connection = connection
        self._session = None
        self._init_error: Exception | None = None
        self._task: asyncio.Task | None = None
        self._close_signal: asyncio.Event | None = None
        self._ready: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._last_used: float = time.monotonic()
        self._in_flight: int = 0

    def _is_alive(self) -> bool:
        """连接已建立且持有任务仍在运行。"""
        return (
            self._session is not None and self._task is not None and not self._task.done()
        )

    @property
    def is_busy(self) -> bool:
        """是否有调用正在进行中。"""
        return self._in_flight > 0

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self._last_used

    async def get_session(self):
        """获取活跃的 ClientSession,必要时建立连接。"""
        if self._is_alive():
            self._last_used = time.monotonic()
            return self._session

        async with self._lock:
            if self._is_alive():
                self._last_used = time.monotonic()
                return self._session

            if self._task is not None and not self._task.done() and self._ready is not None:
                # 建连进行中:复用同一 ready 等待,避免并发冷启动互相拆台
                ready = self._ready
            else:
                await self._cleanup()
                self._init_error = None
                ready = asyncio.Event()
                self._ready = ready
                self._close_signal = asyncio.Event()
                self._task = asyncio.create_task(
                    self._runner(ready),
                    name=f"mcp-persistent-{self._server_name}",
                )

        await ready.wait()
        self._last_used = time.monotonic()
        if self._session is None:
            raise self._init_error or RuntimeError(
                f"MCP 持久会话 '{self._server_name}' 启动失败"
            )
        return self._session

    async def _runner(self, ready: asyncio.Event):
        """持有连接的后台任务:建连 -> 等待关闭信号 -> 在同一任务内销毁。"""
        from mcp import ClientSession

        close_signal = self._close_signal
        try:
            async with _connect_mcp(self._connection) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    ready.set()
                    await close_signal.wait()
        except Exception as e:
            self._init_error = e
        finally:
            self._session = None
            ready.set()  # 确保等待者不被挂起

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        read_timeout_seconds: float | None = None,
    ):
        self._in_flight += 1
        self._last_used = time.monotonic()
        try:
            session = await self.get_session()
            try:
                return await session.call_tool(
                    tool_name,
                    arguments=arguments,
                    read_timeout_seconds=read_timeout_seconds,
                )
            except Exception:
                # 连接可能已失效,废弃会话让下次重建;不自动重试,避免副作用工具重复执行
                await self.close()
                raise
        finally:
            self._in_flight -= 1
            self._last_used = time.monotonic()

    async def close(self):
        async with self._lock:
            await self._cleanup()

    async def _cleanup(self):
        if self._close_signal is not None:
            self._close_signal.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, Exception):
                    pass
            except Exception:
                pass
            self._task = None
        self._session = None
        self._close_signal = None
        self._ready = None


# MCP 媒体块的 base64 尺寸上限(按原始字节估算),对齐 tools/media.py 的 SIZE_LIMITS
_MM_SIZE_LIMITS = {
    "image": 20 * 1024 * 1024,
    "audio": 25 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
}


def _mcp_media_block(mime_type: str, b64: str) -> dict:
    """把 MCP 的 base64 媒体数据转成 OpenAI 风格的多模态块。

    与 tools/media.py 的输出形状保持一致(图片走 image_url 的 data URI,
    音频走 input_audio 的 data URI,视频走 video_url),这样 multi_agent 的
    多模态分支和 MultimodalBlock.from_dict 都能直接识别。

    超出尺寸上限或 MIME 前缀无法识别时降级为文本占位,避免撑爆上下文。
    """
    kind = (mime_type or "").split("/", 1)[0]
    if not b64 or kind not in _MM_SIZE_LIMITS:
        return {"type": "text", "text": f"[{mime_type or 'unknown'}: 不支持的媒体内容]"}

    raw_bytes = (len(b64) * 3) // 4
    if raw_bytes > _MM_SIZE_LIMITS[kind]:
        return {
            "type": "text",
            "text": f"[{mime_type}: 内容过大({raw_bytes // 1024} KB),已省略]",
        }

    data_uri = f"data:{mime_type};base64,{b64}"
    if kind == "image":
        return {"type": "image_url", "image_url": {"url": data_uri}}
    if kind == "video":
        return {
            "type": "video_url",
            "video_url": {"url": data_uri},
            "fps": 2,
            "media_resolution": "default",
        }
    return {"type": "input_audio", "input_audio": {"data": data_uri}}


def _mcp_blocks_to_content(blocks) -> str | list[dict]:
    """把 MCP 的 content 块列表转成 UniClaw 工具结果。

    纯文本结果返回 str(保持既有调用方行为);含图片/音频/视频时返回多模态块
    列表,由 multi_agent 的多模态分支拆成 TOOL 文本 + USER 媒体消息。
    """
    parts: list[dict] = []
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append({"type": "text", "text": getattr(block, "text", "")})
        elif btype in ("image", "audio"):
            parts.append(
                _mcp_media_block(
                    getattr(block, "mime_type", ""),
                    getattr(block, "data", "") or "",
                )
            )
        elif btype == "resource":
            res = getattr(block, "resource", None)
            text = getattr(res, "text", None)
            if text is not None:
                parts.append({"type": "text", "text": text})
            else:
                parts.append(
                    _mcp_media_block(
                        getattr(res, "mime_type", ""),
                        getattr(res, "blob", "") or "",
                    )
                )
        elif btype == "resource_link":
            name = getattr(block, "name", "") or getattr(block, "uri", "")
            mime = getattr(block, "mime_type", "") or "unknown"
            parts.append({"type": "text", "text": f"[resource_link: {name} ({mime})]"})
        else:
            # 未知块类型只留类型占位,避免 pydantic repr 把 base64 原样倾泻进上下文
            parts.append({"type": "text", "text": f"[{btype or 'unknown'}]"})

    if not any(p.get("type") != "text" for p in parts):
        return "\n".join(p["text"] for p in parts) if parts else "(无输出)"
    return parts


def _get_session_key(tool_runtime: ToolRuntime | None) -> str:
    """从 tool_runtime 提取会话级 key:沿 parent_config 链取根会话 ID。

    同一对话的主/子代理共享同一 key,跨对话隔离。
    """
    if tool_runtime is None or tool_runtime.config is None:
        return "_default"
    cfg = tool_runtime.config
    while cfg.parent_config is not None:
        cfg = cfg.parent_config
    agent = cfg.current_agent
    if agent is not None and agent.session.id:
        return agent.session.id
    return f"_cfg_{id(cfg)}"


def _inject_timeout_param(schema: dict) -> tuple[dict, bool]:
    """在 MCP 工具 schema 注入调用侧 timeout 参数(不传给上游)。

    上游自身已声明 timeout 时不动(避免遮蔽),返回 (schema, 是否注入)。
    长任务(exa_agent_run 等)需要调用方能加大读超时才能拿到 runId。
    """
    schema = dict(schema or {"type": "object", "properties": {}})
    properties = dict(schema.get("properties") or {})
    if "timeout" in properties:
        return schema, False
    properties["timeout"] = {
        "type": "number",
        "description": (
            "本次调用的读超时(秒)。长任务(如 exa_agent_run)建议 120-600。"
            "不传则用服务器默认值。该参数不会传给上游工具。"
        ),
    }
    schema["properties"] = properties
    return schema, True


def _make_mcp_caller(
    server_name: str,
    tool_name: str,
    connection: dict,
    owns_timeout_param: bool = False,
):
    """创建 MCP 工具的异步调用闭包。

    默认每次调用时建立连接、执行、断开。
    当 connection 配置了 "persistent": true 时,复用持久会话(按会话隔离)。

    Args:
        owns_timeout_param: schema 是否由我们注入了 timeout 参数。
            为 True 时调用方传入的 timeout 被用作读超时,不会转发给上游。
    """
    is_persistent = connection.get("persistent", False)

    async def _call(tool_runtime: ToolRuntime | None = None, **kwargs) -> str | list:
        from mcp import ClientSession

        call_timeout_override = None
        if owns_timeout_param:
            call_timeout_override = kwargs.pop("timeout", None)
        call_timeout = _resolve_call_timeout(connection, call_timeout_override)
        phase = "connect"
        try:
            if is_persistent:
                phase = "initialize"
                session_key = _get_session_key(tool_runtime)
                ps = MCPManager.get_instance().get_persistent_session(
                    session_key, server_name, connection
                )
                phase = "call"
                result = await ps.call_tool(
                    tool_name,
                    arguments=kwargs,
                    read_timeout_seconds=call_timeout,
                )
            else:
                async with _connect_mcp(connection) as (read, write):
                    phase = "initialize"
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        phase = "call"
                        result = await session.call_tool(
                            tool_name,
                            arguments=kwargs,
                            read_timeout_seconds=call_timeout,
                        )
            content = _mcp_blocks_to_content(result.content)
            return _annotate_run_id(content, server_name, tool_name)
        except Exception as e:
            return _format_call_error(
                server_name, tool_name, e, phase, call_timeout
            )

    _call.__name__ = f"{server_name}_{tool_name}"
    _call.__qualname__ = _call.__name__
    return _call


def _ensure_exa_api_key(mcp_config: dict, config: AppConfig | None) -> None:
    """确保 mcp 配置中 exa 服务器的 URL 带上最新的 EXA_API_KEY。

    mcp.json 可能是早期创建的(或用户手动编辑过), URL 上不一定有
    exaApiKey 参数; 而 load_config 的首次创建拼接只在文件不存在时执行。
    此函数在每次加载后调用, 幂等补齐, 不重复追加。
    """
    if not config or not config.EXA_API_KEY:
        return
    exa = mcp_config.get("servers", {}).get("exa")
    if exa and "exaApiKey=" not in exa.get("url", ""):
        sep = "&" if "?" in exa["url"] else "?"
        exa["url"] = f"{exa['url']}{sep}exaApiKey={config.EXA_API_KEY}"


async def _discover_tools_async(server_name: str, connection: dict) -> list[Tool]:
    """异步连接 MCP 服务器,发现工具并转换为 Tool 对象。"""
    from mcp import ClientSession

    tools = []
    async with _connect_mcp(connection) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            for mcp_tool in tools_result.tools:
                full_name = f"{server_name}_{mcp_tool.name}"
                schema = getattr(mcp_tool, "input_schema", None) or {
                    "type": "object",
                    "properties": {},
                }
                schema, owns_timeout = _inject_timeout_param(schema)
                caller = _make_mcp_caller(
                    server_name, mcp_tool.name, connection, owns_timeout_param=owns_timeout
                )
                tools.append(
                    Tool(
                        name=full_name,
                        description=mcp_tool.description or "",
                        func=caller,
                        parameters=schema,
                    )
                )
    return tools


class MCPManager:
    """MCP 服务器管理器(单例)"""

    _instance: "MCPManager | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._config_path: Path = get_app_dir(Scope.USER) / "mcp.json"
        self._config: dict = {"servers": {}}
        self._client = None
        self.server2tools: dict[str, list] = {}
        self._initialized = False
        self._registered_mcp_names: set[str] = (
            set()
        )  # 已注册到 ToolRegistry 的 MCP 工具名
        self._persistent_sessions: dict[tuple[str, str], _PersistentSession] = {}
        self._last_orphan_sweep: float = 0.0

    @classmethod
    def get_instance(cls) -> "MCPManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def load_config(self, config: AppConfig | None = None) -> dict:
        if not self._config_path.exists():
            # 首次使用,写入内置默认配置
            from .builtin import BUILTIN_MCP_SERVERS

            servers = {name: {**srv} for name, srv in BUILTIN_MCP_SERVERS.items()}
            self._config = {"servers": servers}
            await self.save_config()
        else:
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                if "servers" not in self._config:
                    self._config["servers"] = {}
            except (json.JSONDecodeError, IOError) as e:
                await err(f"加载 MCP 配置失败: {e}", config, e=e)
                self._config = {"servers": {}}
        # exa: 无论文件是首次创建还是已存在, 都确保 URL 带上最新的 API Key
        _ensure_exa_api_key(self._config, config)
        return self._config

    async def save_config(self):
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    async def list_servers(self, config: AppConfig | None = None) -> list[dict]:
        await self.load_config(config)
        servers = []
        for name, conn in self._config.get("servers", {}).items():
            entry = {"name": name, **conn}
            servers.append(entry)
        return servers

    async def get_server(
        self, name: str, config: AppConfig | None = None
    ) -> dict | None:
        await self.load_config(config)
        conn = self._config.get("servers", {}).get(name)
        if conn is None:
            return None
        return {"name": name, **conn}

    async def add_server(
        self,
        name: str,
        connection: dict,
        enabled: bool = True,
        skip_validation: bool = False,
        config: AppConfig | None = None,
    ):
        await self.load_config(config)
        if name in self._config["servers"]:
            raise ValueError(f"服务器 '{name}' 已存在")
        if not skip_validation:
            reason = await self.test_connection(connection, config)
            if reason:
                # 把底层原因一起带上 —— 只报"连接验证失败"无法定位到
                # 超时/DNS/协议握手的哪一环
                raise ValueError(
                    f"连接验证失败(name={name}, "
                    f"transport={connection.get('transport')}, "
                    f"url={connection.get('url') or connection.get('command')}): "
                    f"{reason}"
                )
        connection["enabled"] = enabled
        self._config["servers"][name] = connection
        await self.save_config()
        await self.refresh(config)

    async def remove_server(self, name: str, config: AppConfig | None = None) -> bool:
        await self.load_config(config)
        if name not in self._config["servers"]:
            return False
        del self._config["servers"][name]
        await self.save_config()
        await self.close_server_persistent_sessions(name)
        await self.refresh(config)
        return True

    async def update_server(
        self, name: str, connection: dict, config: AppConfig | None = None
    ) -> bool:
        await self.load_config(config)
        if name not in self._config["servers"]:
            return False
        old = self._config["servers"][name]
        connection["enabled"] = old.get("enabled", True)
        self._config["servers"][name] = connection
        await self.save_config()
        await self.close_server_persistent_sessions(name)  # 连接配置可能已变,强制重建
        await self.refresh(config)
        return True

    async def toggle_server(
        self, name: str, enabled: bool, config: AppConfig | None = None
    ) -> bool:
        await self.load_config(config)
        if name not in self._config["servers"]:
            return False
        self._config["servers"][name]["enabled"] = enabled
        await self.save_config()
        if not enabled:
            await self.close_server_persistent_sessions(name)
        await self.refresh(config)
        return True

    async def _build_connections(self, config: AppConfig | None = None) -> dict:
        await self.load_config(config)
        connections = {}
        for name, conn in self._config.get("servers", {}).items():
            if not conn.get("enabled", True):
                continue
            clean = {k: v for k, v in conn.items() if k != "enabled"}
            connections[name] = clean
        return connections

    async def init_client(self, config: AppConfig | None = None):
        """异步并发连接所有 MCP 服务器,单个失败不影响其他。"""
        connections = await self._build_connections(config)
        if not connections:
            self._client = None
            self.server2tools = {}
            return None
        self.server2tools = {k: list() for k in connections.keys()}

        async def _try_discover(server_name: str, conn: dict):
            try:
                # 发现阶段只做握手+list_tools,不该按长任务读超时等
                discover_timeout = min(
                    float(conn.get("timeout", DEFAULT_CONNECT_TIMEOUT)), 60.0
                )
                tools = await asyncio.wait_for(
                    _discover_tools_async(server_name, conn),
                    timeout=discover_timeout,
                )
                self.server2tools[server_name] = tools
                await ok(
                    f"MCP [{server_name}] 连接成功,发现 {len(tools)} 个工具", config
                )
            except asyncio.TimeoutError as e:
                # 超时/网络这类外部失败不传 e —— 堆栈只是 httpx/anyio 的调用链,
                # 对定位"服务不通"没有帮助,写进日志文件纯属噪音
                await err(f"MCP [{server_name}] 连接超时", config)
                self.server2tools[server_name] = []
            except Exception as e:
                # 连接外部服务失败同理: _flat_error 已压平出原因, 不留堆栈
                await err(f"MCP [{server_name}] 连接失败: {_flat_error(e)}", config)
                self.server2tools[server_name] = []

        await asyncio.gather(
            *[_try_discover(name, conn) for name, conn in connections.items()]
        )
        self._client = True
        return self._client

    def get_persistent_session(
        self, session_key: str, server_name: str, connection: dict
    ) -> _PersistentSession:
        """获取(或创建)指定 (会话, server) 的持久会话包装器。"""
        self._schedule_orphan_sweep()
        key = (session_key, server_name)
        if key not in self._persistent_sessions:
            self._persistent_sessions[key] = _PersistentSession(server_name, connection)
        return self._persistent_sessions[key]

    def _schedule_orphan_sweep(self):
        """节流调度闲置持久连接回收,不阻塞调用方。"""
        now = time.monotonic()
        if now - self._last_orphan_sweep < 60:
            return
        self._last_orphan_sweep = now
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.sweep_orphaned_persistent_sessions())
        except RuntimeError:
            pass

    async def sweep_orphaned_persistent_sessions(self):
        """回收长期闲置的持久连接(兜底;会话显式删除由 delete_session 清理)。

        只按闲置时间判定,不查磁盘:首轮会话尚未落盘、A2A 会话永不落盘,
        用 load_session 判存活会把仍在使用的连接当孤儿杀掉。
        """
        if not self._persistent_sessions:
            return
        for key, ps in list(self._persistent_sessions.items()):
            try:
                if ps.is_busy or ps.idle_seconds < PERSISTENT_SESSION_IDLE_TIMEOUT:
                    continue
                if self._persistent_sessions.get(key) is not ps:
                    continue
                self._persistent_sessions.pop(key, None)
                await ps.close()
            except Exception:
                continue

    async def close_server_persistent_sessions(self, server_name: str):
        """关闭指定 server 在所有会话中的持久连接。"""
        keys = [k for k in self._persistent_sessions if k[1] == server_name]
        for key in keys:
            ps = self._persistent_sessions.pop(key)
            await ps.close()

    async def close_session_persistent_sessions(self, session_key: str):
        """关闭指定会话的所有持久连接。"""
        keys = [k for k in self._persistent_sessions if k[0] == session_key]
        for key in keys:
            ps = self._persistent_sessions.pop(key)
            await ps.close()

    async def close_all_persistent_sessions(self):
        """关闭所有持久会话。"""
        for key in list(self._persistent_sessions.keys()):
            ps = self._persistent_sessions.pop(key)
            await ps.close()

    async def get_mcp_tools(self) -> list:
        if not self._initialized:
            await self.refresh()
        return [tool for tools in self.server2tools.values() for tool in tools]

    async def test_connection(
        self, connection: dict, config: AppConfig | None = None
    ) -> str:
        """测试单个 MCP 连接是否可用。

        Returns:
            str: 成功返回空串;失败返回具体原因(超时/DNS/TLS/HTTP 状态码等)。
                原因要带回给调用方 —— 只报"连接验证失败"会让模型无从判断是
                超时、域名解析失败还是协议不匹配,无法据此调整参数重试。
        """
        timeout = min(
            float(connection.get("timeout", DEFAULT_CONNECT_TIMEOUT)), 60.0
        )
        target = (
            f"transport={connection.get('transport', '?')}, "
            f"url={connection.get('url') or connection.get('command') or '?'}"
        )
        try:
            tools = await asyncio.wait_for(
                _discover_tools_async("test", connection),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # 超时/网络这类外部失败不传 e —— 堆栈对定位"服务不通"没帮助,
            # 写进日志文件纯属噪音。原因已压平在 reason 里返回给调用方
            reason = f"连接超时({timeout}秒未响应)"
            await err(f"连接验证失败({target}): {reason}", config)
            return reason
        except Exception as e:
            reason = _flat_error(e)
            await err(f"连接验证失败({target}): {reason}", config)
            return reason
        await ok(f"连接验证成功,发现 {len(tools)} 个工具", config)
        return ""

    async def refresh(self, config: AppConfig | None = None):
        """重新初始化客户端以加载最新配置"""
        await self.close_all_persistent_sessions()  # 配置可能已变,旧会话作废
        await self.init_client(config)
        # MCP 工具变更后,重新注册到 ToolRegistry 以更新 BM25 索引
        self._reregister_mcp_tools()
        self._initialized = True

    def _reregister_mcp_tools(self):
        """将 MCP 工具重新注册到 ToolRegistry。"""
        from uniclaw.tools.registry import ToolRegistry

        registry = ToolRegistry.get_instance()

        # 批量删除旧的 MCP 工具
        registry.unregister(*self._registered_mcp_names)

        # 注册当前 MCP 工具
        self._registered_mcp_names = set()
        for tools in self.server2tools.values():
            for tool in tools:
                registry.register(tool, [], "MCP工具", is_core=False)
                self._registered_mcp_names.add(tool.name)

    def get_tools_info(self, server_name: str | None = None) -> list[dict]:
        info = []
        if server_name is not None:
            servers = {server_name: self.server2tools.get(server_name, [])}
        else:
            servers = self.server2tools
        for srv_name, tools in servers.items():
            for tool in tools:
                info.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "server": srv_name,
                    }
                )
        return info


# 导出工具函数
from .tools import get_tools, get_all_tools
