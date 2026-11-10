from __future__ import annotations
import asyncio
import json
import re
import httpx
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Callable

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


def _make_httpx_factory(
    default_timeout: httpx.Timeout, proxy: str | None = None
) -> Callable[..., httpx.AsyncClient]:
    """构造给 fastmcp transport 的 httpx.AsyncClient 工厂。

    sse / streamable_http 都通过 httpx_client_factory 自定义底层 HTTP 客户端,
    这里统一注入长读超时(长任务不被 15s 掐断)与代理。
    """

    def _factory(
        headers: dict | None = None,
        timeout: httpx.Timeout | None = None,
        auth=None,
        **kwargs,
    ) -> httpx.AsyncClient:
        client_kwargs = dict(kwargs or {})
        client_kwargs["timeout"] = timeout or default_timeout
        client_kwargs["follow_redirects"] = True
        if headers:
            client_kwargs["headers"] = headers
        if proxy:
            client_kwargs["proxy"] = proxy
        if auth is not None:
            client_kwargs["auth"] = auth
        return httpx.AsyncClient(**client_kwargs)

    return _factory


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

    _ConnectError 标记建连失败(任务从未发出): 解包出原始建连异常,
    并把 phase 强制还原为 "connect" —— 所有工具调用一律经 _McpConnection
    后, 冷启动建连失败与调用失败从同一调用点抛出, 不还原会把"未发起"
    误报成"run 可能已创建"。
    """
    if isinstance(e, _ConnectError):
        if e.__cause__ is not None:
            e = e.__cause__
        phase = "connect"
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
    """根据 transport 类型建立 MCP 连接,统一 yield 已初始化的 ClientSession。

    基于 fastmcp(底层 mcp 2.x):支持 stdio / sse / streamable_http 三种传输,
    websocket 客户端在 mcp 2.x 已移除,直接报错。
    stdio 用 fastmcp 的 StdioTransport(自带 keep_alive 子进程保活);
    sse / streamable_http 用 fastmcp transport,并通过 httpx_client_factory
    注入超时与代理(与旧实现语义一致)。
    """
    from fastmcp import Client
    from fastmcp.client.transports import (
        SSETransport,
        StdioTransport,
        StreamableHttpTransport,
    )

    transport = connection.get("transport", "stdio")

    if transport == "stdio":
        client_transport = StdioTransport(
            command=connection.get("command", ""),
            args=connection.get("args", []),
            env=connection.get("env"),
            cwd=connection.get("cwd"),
            # 连接生命周期由 _McpConnection 持有:async with 区间即连接
            # 存活区间,退出时才断开。不启用 fastmcp 自带 keep_alive,避免
            # 两次回收之间子进程残留。
            keep_alive=False,
        )
    elif transport == "sse":
        # SSE 读超时与 streamable_http 对齐:连接短超时,读超时用 read_timeout
        # 或默认值(长任务不被 15s 掐断)。sse_read_timeout 控制 SSE 事件流读取。
        httpx_timeout = _build_httpx_timeout(connection)
        client_transport = SSETransport(
            url=connection["url"],
            headers=connection.get("headers"),
            sse_read_timeout=httpx_timeout.read,
            httpx_client_factory=_make_httpx_factory(httpx_timeout),
        )
    elif transport == "streamable_http":
        headers = connection.get("headers")
        proxy = connection.get("proxy")

        # 读超时单独放宽:exa_agent_run 等长任务需要收到 status=running + runId
        httpx_timeout = _build_httpx_timeout(connection)
        client_transport = StreamableHttpTransport(
            url=connection["url"],
            headers=headers,
            httpx_client_factory=_make_httpx_factory(httpx_timeout, proxy),
        )
    elif transport == "websocket":
        raise RuntimeError(
            "websocket 传输不可用: mcp 2.x 已移除 websocket 客户端,"
            "请改用 sse 或 streamable_http"
        )
    else:
        raise ValueError(f"不支持的传输类型: {transport}")

    # 连接/握手超时:对齐 connection["timeout"](或默认值)。
    # fastmcp Client 的 init_timeout 控制握手(内部 normalize 成秒);
    # timeout 参数期望的是 timedelta 语义, 传 float 秒会触发内部加法失败,
    # 故只传 init_timeout —— 它才是"连接/握手多久算超时"的正确开关。
    connect_timeout = _build_httpx_timeout(connection).connect

    try:
        async with Client(
            client_transport,
            init_timeout=connect_timeout,
        ) as client:
            yield client.session
    except RuntimeError as e:
        # asyncio.run() 关闭期间, 存活的 async generator 会被强制 aclose,
        # Client 内部的 anyio cancel scope 跨任务退出会抛该异常;
        # 此时进程即将结束, 连接由 OS 回收, 静默返回即可。
        if "cancel scope in a different task" in str(e):
            return
        raise


# 持久会话闲置回收阈值(秒):超过该时长无调用即回收连接。
# 只按闲置判定,不查磁盘 — 会话可能尚未落盘(首轮未保存/A2A 内存态),
# 按磁盘存在性判定会误杀活跃连接、丢失有状态 server 的登录态。
# 连接按 server 名全局共享, 删除/切换会话不影响其他使用者的连接,
# 因此会话删除无需联动关闭 MCP 连接(由闲置回收兜底)。
CONNECTION_IDLE_TIMEOUT = 1800


class _ConnectError(RuntimeError):
    """建连阶段失败标记:用于区分「连接未建立」与「调用阶段失败」。

    所有工具调用一律经 _McpConnection 后, 冷启动建连失败(如超时)与
    工具调用失败会从同一调用点(call_tool)抛出。__cause__ 携带原始建连异常,
    调用方据此把 phase 还原为 "connect", 避免把"任务从未发出"误报成
    "run 可能已创建"。
    """


class _McpConnection:
    """按 server 名共享的 MCP 持久连接:由独立后台任务持有,跨调用/跨会话复用。

    所有 MCP 连接都经它管理 —— 连接的建立/销毁都在同一个后台任务内完成,
    规避 anyio cancel scope 跨任务问题。
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
                    name=f"mcp-conn-{self._server_name}",
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
        close_signal = self._close_signal
        try:
            async with _connect_mcp(self._connection) as session:
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
            try:
                session = await self.get_session()
            except Exception as e:
                # 建连阶段失败: 用 _ConnectError 标记, 调用方据此把 phase
                # 还原为 "connect"(任务从未发出, 需重新发起而非续跑)。
                raise _ConnectError(
                    f"MCP 连接 '{self._server_name}' 建立失败: {_flat_error(e)}"
                ) from e
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

    所有工具调用统一经按 server 名全局共享的持久连接(_McpConnection),
    不做每次调用独立建连 —— 有状态服务(登录后查询)与 stdio 冷启动服务
    都能跨调用、跨会话复用连接。闲置 30 分钟由 sweep 回收。

    Args:
        owns_timeout_param: schema 是否由我们注入了 timeout 参数。
            为 True 时调用方传入的 timeout 被用作读超时,不会转发给上游。
    """

    async def _call(tool_runtime: ToolRuntime | None = None, **kwargs) -> str | list:
        call_timeout_override = None
        if owns_timeout_param:
            call_timeout_override = kwargs.pop("timeout", None)
        call_timeout = _resolve_call_timeout(connection, call_timeout_override)
        try:
            # 连接由 _McpConnection 持有:首次冷启动, 之后复用同一连接。
            # 建连失败由 _ConnectError 标记, _format_call_error 还原为 connect 阶段。
            ps = MCPManager.get_instance().get_connection(server_name, connection)
            result = await ps.call_tool(
                tool_name,
                arguments=kwargs,
                read_timeout_seconds=call_timeout,
            )
            content = _mcp_blocks_to_content(result.content)
            return _annotate_run_id(content, server_name, tool_name)
        except Exception as e:
            return _format_call_error(
                server_name, tool_name, e, "call", call_timeout
            )

    _call.__name__ = f"{server_name}_{tool_name}"
    _call.__qualname__ = _call.__name__
    return _call


# ── resources / prompts 桥接(mcp 2.x) ───────────────────────
#
# MCP 服务器除了 tools 还能暴露 resources(数据资源)与 prompts(提示词模板)。
# UniClaw 将两者各桥接为 2 个通用工具,让 LLM 能像调普通工具一样访问:
#   resources → {server}_list_resources / {server}_read_resource
#   prompts   → {server}_list_prompts   / {server}_get_prompt
# 只在服务器 capabilities 声明了对应能力时生成,避免为不支持的服务器添噪音工具。

_BRIDGE_DESCRIPTIONS: dict[str, str] = {
    "list_resources": (
        "列出该 MCP 服务器的所有资源(uri、名称、描述、mime 类型)与资源模板。"
        "资源可按 uri 用 read_resource 读取;资源模板需先填充 {variable} 占位符生成 uri。"
    ),
    "read_resource": (
        "按 uri 读取该 MCP 服务器的资源实际内容。"
        "uri 来自 list_resources 的结果(可为资源模板填充后的 uri)。"
    ),
    "list_prompts": (
        "列出该 MCP 服务器所有可用提示词模板(名称、描述、参数及是否必填)。"
    ),
    "get_prompt": (
        "按名称获取该 MCP 服务器的提示词模板内容。"
        "arguments 按 list_prompts 列出的参数填写,必填参数必须提供。"
    ),
}

_BRIDGE_SCHEMAS: dict[str, dict] = {
    "list_resources": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "read_resource": {
        "type": "object",
        "properties": {
            "uri": {
                "type": "string",
                "description": "要读取的资源 URI,来自 list_resources 的结果"
                "(资源模板填充 {variable} 后生成的 uri 也可)。",
            }
        },
        "required": ["uri"],
        "additionalProperties": False,
    },
    "list_prompts": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "get_prompt": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "提示词名称,来自 list_prompts 的结果。",
            },
            "arguments": {
                "type": "object",
                "description": "提示词参数(参数名 -> 字符串值),按 list_prompts 列出的参数填写。"
                "没有参数时可省略。",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    },
}

# 桥接工具在 ToolRegistry 中的检索关键词(按名字后缀匹配)
_BRIDGE_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "_list_resources",
        ["资源", "资源列表", "列出资源", "resource", "list resources", "resources"],
    ),
    (
        "_read_resource",
        ["读取资源", "读资源", "资源内容", "resource", "read resource", "read_resource"],
    ),
    (
        "_list_prompts",
        ["提示词", "提示词列表", "列出提示词", "prompt", "list prompts", "prompts"],
    ),
    (
        "_get_prompt",
        [
            "获取提示词",
            "提示词模板",
            "提示词内容",
            "prompt",
            "get prompt",
            "prompt template",
        ],
    ),
]


def _bridge_keywords(tool_name: str) -> list[str]:
    """按桥接工具名后缀返回注册表检索关键词(非桥接工具返回空列表)。"""
    for suffix, kws in _BRIDGE_KEYWORDS:
        if tool_name.endswith(suffix):
            return kws
    return []


def _format_resources_list(result, server_name: str) -> str:
    """格式化 ListResourcesResult:资源 + 资源模板。"""
    resources = list(getattr(result, "resources", None) or [])
    # mcp 2.x 字段是 resource_templates(snake_case), 旧版/部分实现用
    # resourceTemplates(camelCase) — 两种都兼容。
    templates = list(
        getattr(result, "resource_templates", None)
        or getattr(result, "resourceTemplates", None)
        or []
    )
    lines = [f"服务器 '{server_name}' 资源(共 {len(resources)} 个):"]
    if not resources:
        lines.append("  (无)")
    for r in resources:
        name = getattr(r, "name", "") or ""
        uri = getattr(r, "uri", "")
        desc = getattr(r, "description", None) or ""
        mime = getattr(r, "mime_type", None) or ""
        line = f"  - {uri}"
        if name:
            line += f" [{name}]"
        if desc:
            line += f": {desc}"
        if mime:
            line += f" (mime: {mime})"
        size = getattr(r, "size", None)
        if size:
            line += f" ({size} bytes)"
        lines.append(line)
    lines.append("资源模板(共 %d 个,填充 {variable} 后可作为 uri 读取):" % len(templates))
    if not templates:
        lines.append("  (无)")
    for t in templates:
        name = getattr(t, "name", "") or ""
        template = getattr(t, "uri_template", "")
        desc = getattr(t, "description", None) or ""
        line = f"  - {template}"
        if name:
            line += f" [{name}]"
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines)


class _ResourceListResult:
    """list_resources + list_resource_templates 的聚合结果容器。

    mcp 协议把静态资源与资源模板分成两个方法返回, ListResourcesResult 是
    pydantic 冻结模型无法扩展 —— 桥接层把它聚合成这个轻量容器交给
    _format_resources_list 渲染(该函数只读 resources / resource_templates)。
    """

    def __init__(
        self,
        resources: list,
        resource_templates: list | None = None,
    ):
        self.resources = resources or []
        self.resource_templates = resource_templates or []


def _resource_contents_to_content(contents, prefix: str = "") -> str | list[dict]:
    """把 ReadResourceResult.contents 转成文本或多模态块。

    TextResourceContents 直出文本;BlobResourceContents(图片/音频/视频)
    走 _mcp_media_block 转多模态块(超限/不支持则降级为文本占位)。
    """
    texts: list[str] = []
    media: list[dict] = []
    for c in contents or ():
        mime = getattr(c, "mime_type", "") or ""
        text = getattr(c, "text", None)
        if text is not None:
            texts.append(text)
            continue
        blob = getattr(c, "blob", None)
        if not blob:
            texts.append("(空资源内容)")
            continue
        block = _mcp_media_block(mime, blob)
        if block["type"] == "text":
            texts.append(block["text"])
        else:
            media.append(block)
    if media:
        if prefix:
            media.insert(0, {"type": "text", "text": prefix})
        return media
    body = "\n".join(t for t in texts if t and t != "(空资源内容)") or "(空资源内容)"
    return f"{prefix}{body}"


def _format_read_result(result, server_name: str) -> str | list[dict]:
    """格式化 ReadResourceResult。"""
    contents = list(getattr(result, "contents", None) or [])
    return _resource_contents_to_content(contents, prefix=f"资源读取成功({server_name}):\n")


def _prompt_msg_to_text(content) -> str:
    """PromptMessage.content → 可读文本(媒体降级为占位,不展开 base64)。

    当前模型非多模态,图片/音频渲染成占位说明;资源链接/内嵌资源只留
    指向性信息,避免把 base64 或大段二进制倾泻进上下文。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    ctype = getattr(content, "type", None)
    if ctype == "text":
        return getattr(content, "text", "")
    if ctype == "image":
        return f"[图片内容({getattr(content, 'mime_type', None) or 'unknown'}),需多模态模型查看]"
    if ctype == "audio":
        return f"[音频内容({getattr(content, 'mime_type', None) or 'unknown'}),需多模态模型播放]"
    if ctype == "resource_link":
        return f"[资源引用: {getattr(content, 'uri', '')}]"
    if ctype == "resource":
        res = getattr(content, "resource", None)
        text = getattr(res, "text", None)
        if text is not None:
            return text
        blob = getattr(res, "blob", None)
        if blob:
            return f"[资源内容(base64 {len(blob)} 字符,已省略)]"
        return "[资源内容(空)]"
    return f"[{ctype or 'unknown'}]"


def _prompt_result_to_text(result) -> str:
    """GetPromptResult.messages → 带 role 标注的对话文本。"""
    lines = []
    desc = getattr(result, "description", None) or ""
    if desc:
        lines.append(desc)
    for msg in getattr(result, "messages", None) or ():
        role = getattr(msg, "role", "user")
        lines.append(f"[{role}]")
        lines.append(_prompt_msg_to_text(getattr(msg, "content", None)))
        lines.append(f"[/{role}]")
    return "\n".join(lines)


def _format_prompts_list(result, server_name: str) -> str:
    """格式化 ListPromptsResult:名称、描述与参数。"""
    prompts = list(getattr(result, "prompts", None) or [])
    lines = [f"服务器 '{server_name}' 提示词(共 {len(prompts)} 个):"]
    if not prompts:
        lines.append("  (无)")
    for p in prompts:
        name = getattr(p, "name", "")
        title = getattr(p, "title", None) or ""
        desc = getattr(p, "description", None) or ""
        head = f"  - {name}"
        if title:
            head += f" [{title}]"
        if desc:
            head += f": {desc}"
        lines.append(head)
        args = list(getattr(p, "arguments", None) or [])
        if args:
            # PromptArgument 是 pydantic 对象(mcp 2.x),不是 dict ——
            # 用 getattr 同时兼容 dict 与对象两种形态。
            arg_str = ", ".join(
                f"{getattr(a, 'name', '') or (a.get('name') if isinstance(a, dict) else '')}"
                f"{'*' if (getattr(a, 'required', False) or (a.get('required') if isinstance(a, dict) else False)) else ''}"
                for a in args
            )
            lines.append(f"    参数: {arg_str}(* 为必填)")
    return "\n".join(lines)


def _format_prompt_result(result, server_name: str) -> str:
    """格式化 GetPromptResult。"""
    return f"提示词获取成功({server_name}):\n{_prompt_result_to_text(result)}"


async def _run_bridge_op(session, op: str, kwargs: dict):
    """把桥接工具的参数映射到 ClientSession 的 resources/prompts 方法。"""
    if op == "list_resources":
        # mcp 协议里静态资源(list_resources)与资源模板(list_resource_templates)
        # 是两个独立方法 —— 模板资源(如 city://{city})只出现在后者。
        # 合并两者返回, 让 list_resources 能同时看到静态资源与模板。
        result = await session.list_resources()
        resources = list(getattr(result, "resources", None) or [])
        templates: list = []
        try:
            tpl = await session.list_resource_templates()
            templates = list(
                getattr(tpl, "resource_templates", None)
                or getattr(tpl, "resourceTemplates", None)
                or []
            )
        except Exception:
            templates = []  # 服务器不支持模板接口,静默忽略
        # ListResourcesResult 是 pydantic 冻结模型,不能 setattr ——
        # 用轻量容器聚合, _format_resources_list 只读这两个属性。
        return _ResourceListResult(resources=resources, resource_templates=templates)
    if op == "list_prompts":
        return await session.list_prompts()
    if op == "read_resource":
        return await session.read_resource(kwargs["uri"])
    if op == "get_prompt":
        return await session.get_prompt(
            kwargs["name"], arguments=kwargs.get("arguments")
        )
    raise ValueError(f"未知 MCP 桥接操作: {op}")  # bug 级,兜底日志记录


def _make_bridge_caller(
    server_name: str, op: str, connection: dict
):
    """创建 resources/prompts 桥接工具调用闭包。

    与普通 MCP 工具一致,统一经按 server 名全局共享的持久连接调用。
    超时由连接层(httpx read 超时)兜底 —— ClientSession.read_resource /
    get_prompt 没有 read_timeout_seconds 参数,注入调用侧 timeout 只会误导,
    故桥接工具不注入 timeout。
    """

    async def _call(tool_runtime: ToolRuntime | None = None, **kwargs) -> str | list:
        try:
            ps = MCPManager.get_instance().get_connection(server_name, connection)
            session = await ps.get_session()
            result = await _run_bridge_op(session, op, kwargs)
            if op == "list_resources":
                return _format_resources_list(result, server_name)
            if op == "read_resource":
                return _format_read_result(result, server_name)
            if op == "list_prompts":
                return _format_prompts_list(result, server_name)
            if op == "get_prompt":
                return _format_prompt_result(result, server_name)
            return str(result)  # 未知 op 已在 _run_bridge_op 拦截
        except Exception as e:
            # 桥接工具没有调用侧 timeout, 调用读超时只来自 read_timeout 配置
            # (或默认值)。报错提示要反映真实值, 不能硬编码 DEFAULT_CALL_TIMEOUT
            # —— 否则配置 60s 的用户会收到"当前读超时约 300s"的误导。
            call_timeout = _resolve_call_timeout(connection, None)
            return _format_call_error(server_name, op, e, "call", call_timeout)

    _call.__name__ = f"{server_name}_{op}"
    _call.__qualname__ = _call.__name__
    return _call


def _make_bridge_tools(server_name: str, connection: dict, kind: str) -> list[Tool]:
    """为 resources / prompts 生成桥接工具(list + read/get)。"""
    ops = (
        ("list_resources", "read_resource")
        if kind == "resources"
        else ("list_prompts", "get_prompt")
    )
    return [
        Tool(
            name=f"{server_name}_{op}",
            description=_BRIDGE_DESCRIPTIONS[op],
            func=_make_bridge_caller(server_name, op, connection),
            parameters=_BRIDGE_SCHEMAS[op],
        )
        for op in ops
    ]


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
    """异步连接 MCP 服务器,发现工具并转换为 Tool 对象。

    除 tools 外,还检查服务器 capabilities:
    - 声明了 resources: 生成 {server}_list_resources / {server}_read_resource
    - 声明了 prompts:   生成 {server}_list_prompts   / {server}_get_prompt
    capabilities 里没有显式字段时仍尝试发现(兼容宽松实现),失败则跳过。
    """
    tools = []
    caps_provides = set()
    async with _connect_mcp(connection) as session:
        capabilities = getattr(session, "server_capabilities", None)
        if capabilities is not None:
            if capabilities.resources is not None:
                caps_provides.add("resources")
            if capabilities.prompts is not None:
                caps_provides.add("prompts")

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

        # resources / prompts 桥接:capabilities 未显式声明时在主连接上
        # 轻量探测(list_resources/list_prompts),失败静默跳过 —— 宽松实现
        # 可能不填 capabilities, 但资源/提示词接口实际可用。
        for kind in ("resources", "prompts"):
            if kind in caps_provides or await _session_probes_capability(session, kind):
                tools.extend(_make_bridge_tools(server_name, connection, kind))
                caps_provides.add(kind)
    return tools


async def _session_probes_capability(session, kind: str) -> bool:
    """在主连接上探测 resources/prompts 接口是否可用。

    仅当 capabilities 未显式声明时调用。探测失败(未实现/协议错误)返回
    False, 由调用方静默跳过 —— 不产生抛错噪音, 也不注册无用的桥接工具。
    token 采样的副作用可忽略; 探测成功与否都会让主连接继续复用。
    """
    meth = "list_resources" if kind == "resources" else "list_prompts"
    try:
        result = await getattr(session, meth)()
        return result is not None
    except Exception:
        return False


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
        self._conn_pool: dict[str, _McpConnection] = {}
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
        await self.close_server_connections(name)
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
        await self.close_server_connections(name)  # 连接配置可能已变,强制重建
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
            await self.close_server_connections(name)
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

    def get_connection(
        self, server_name: str, connection: dict
    ) -> _McpConnection:
        """获取(或创建)指定 server 的持久会话包装器。

        所有会话(主代理/子代理/A2A)按 server 名共享同一连接 —— 不做会话隔离。
        """
        self._schedule_orphan_sweep()
        if server_name not in self._conn_pool:
            self._conn_pool[server_name] = _McpConnection(
                server_name, connection
            )
        return self._conn_pool[server_name]

    def _schedule_orphan_sweep(self):
        """节流调度闲置持久连接回收,不阻塞调用方。"""
        now = time.monotonic()
        if now - self._last_orphan_sweep < 60:
            return
        self._last_orphan_sweep = now
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.sweep_orphaned_connections())
        except RuntimeError:
            pass

    async def sweep_orphaned_connections(self):
        """回收长期闲置的持久连接(兜底)。

        只按闲置时间判定,不查磁盘:首轮会话尚未落盘、A2A 会话永不落盘,
        用 load_session 判存活会把仍在使用的连接当孤儿杀掉。
        所有会话共享同一连接(按 server 名),因此闲置回收不会误伤其他会话 ——
        只要还有任何调用在用它,idle_seconds 就会更新。
        """
        if not self._conn_pool:
            return
        for name, ps in list(self._conn_pool.items()):
            try:
                if ps.is_busy or ps.idle_seconds < CONNECTION_IDLE_TIMEOUT:
                    continue
                if self._conn_pool.get(name) is not ps:
                    continue
                self._conn_pool.pop(name, None)
                await ps.close()
            except Exception:
                continue

    async def close_server_connections(self, server_name: str):
        """关闭指定 server 的持久连接(配置变更/删除 server 时调用)。"""
        ps = self._conn_pool.pop(server_name, None)
        if ps is not None:
            await ps.close()

    async def close_all_connections(self):
        """关闭所有持久会话。"""
        for key in list(self._conn_pool.keys()):
            ps = self._conn_pool.pop(key)
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
        await self.close_all_connections()  # 配置可能已变,旧会话作废
        await self.init_client(config)
        # 重新注册前记录上一轮的 MCP 工具名(供同步允许集时移除被删/禁用的)
        prev_registered = set(self._registered_mcp_names)
        # MCP 工具变更后,重新注册到 ToolRegistry 以更新 BM25 索引
        self._reregister_mcp_tools()
        self._initialized = True
        # 同步会话允许集: 让会话中途新增的 MCP 工具立即可被 search_tools 加载,
        # 被删除/禁用的工具从允许集移除。修复"添加后搜得到但不可用"。
        self._sync_allowed_tools(config, prev_registered)

    def _sync_allowed_tools(
        self, config: AppConfig | None = None, prev_registered: set[str] | None = None
    ):
        """将当前 MCP 工具名同步进当前会话的 allowed_tools_set。

        allowed_tools_set 在会话启动时由 get_tools() 一次性算出并冻结,
        会话中途添加/删除 MCP 服务器不会自动更新,导致 search_tools
        命中新工具却判为"不可用"。
        """
        if config is None:
            return
        task = getattr(config, "current_agent", None)
        if task is None:
            return
        allowed = task.allowed_tools_set
        # 尚未初始化(如 A2A/子代理虚任务)时跳过,主代理会话启动后必有值
        if allowed is None:
            return
        current = set(self._registered_mcp_names)
        # 移出已被删除/禁用的服务器工具(prev 中不再出现的 MCP 名)
        if prev_registered:
            removed = prev_registered - current
            if removed:
                allowed.difference_update(removed)
        # 加入新增/现存的 MCP 工具
        if current:
            allowed.update(current)

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
                # 桥接工具(resources/prompts 类)带检索关键词,便于 BM25 命中;
                # 普通 MCP 工具保持空关键词(现状,靠工具名/描述命中)。
                keywords = _bridge_keywords(tool.name)
                registry.register(tool, keywords, "MCP工具", is_core=False)
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
