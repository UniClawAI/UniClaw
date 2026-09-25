from __future__ import annotations
import asyncio
import json
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


def _flat_error(e: BaseException) -> str:
    """把异常压平成一行可读文本。

    anyio 的 TaskGroup 失败会包装成 ExceptionGroup,str() 只显示
    "unhandled errors in a TaskGroup (1 sub-exception)",真实原因藏在
    子异常里 — 递归展开后拼接,避免报错无法定位。
    """
    if isinstance(e, BaseExceptionGroup) and e.exceptions:
        return "; ".join(_flat_error(sub) for sub in e.exceptions)
    return str(e) or type(e).__name__


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

        async with sse_client(
            url=connection["url"],
            headers=connection.get("headers"),
            timeout=connection.get("timeout", 5),
        ) as (read, write):
            yield read, write

    elif transport == "streamable_http":
        from mcp.client.streamable_http import streamable_http_client

        timeout = connection.get("timeout", 10)
        headers = connection.get("headers")
        proxy = connection.get("proxy")

        client_kwargs = {"timeout": timeout}
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

    async def call_tool(self, tool_name: str, arguments: dict):
        self._in_flight += 1
        self._last_used = time.monotonic()
        try:
            session = await self.get_session()
            try:
                return await session.call_tool(tool_name, arguments=arguments)
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


def _make_mcp_caller(server_name: str, tool_name: str, connection: dict):
    """创建 MCP 工具的异步调用闭包。

    默认每次调用时建立连接、执行、断开。
    当 connection 配置了 "persistent": true 时,复用持久会话(按会话隔离)。
    """
    is_persistent = connection.get("persistent", False)

    async def _call(tool_runtime: ToolRuntime | None = None, **kwargs) -> str | list:
        from mcp import ClientSession

        try:
            if is_persistent:
                session_key = _get_session_key(tool_runtime)
                ps = MCPManager.get_instance().get_persistent_session(
                    session_key, server_name, connection
                )
                result = await ps.call_tool(tool_name, arguments=kwargs)
            else:
                async with _connect_mcp(connection) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments=kwargs)
            return _mcp_blocks_to_content(result.content)
        except Exception as e:
            return f"{TOOL_ERROR}: {_flat_error(e)}"

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
                caller = _make_mcp_caller(server_name, mcp_tool.name, connection)
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
        if not skip_validation and not await self.test_connection(connection, config):
            raise ValueError("连接验证失败")
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
                tools = await asyncio.wait_for(
                    _discover_tools_async(server_name, conn),
                    timeout=conn.get("timeout", 15),
                )
                self.server2tools[server_name] = tools
                await ok(
                    f"MCP [{server_name}] 连接成功,发现 {len(tools)} 个工具", config
                )
            except asyncio.TimeoutError as e:
                await err(f"MCP [{server_name}] 连接超时", config, e=e)
                self.server2tools[server_name] = []
            except Exception as e:
                await err(
                    f"MCP [{server_name}] 连接失败: {_flat_error(e)}", config, e=e
                )
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
    ) -> bool:
        """测试单个 MCP 连接是否可用"""
        timeout = connection.get("timeout", 15)
        try:
            tools = await asyncio.wait_for(
                _discover_tools_async("test", connection),
                timeout=timeout,
            )
            await ok(f"连接验证成功,发现 {len(tools)} 个工具", config)
            return True
        except asyncio.TimeoutError as e:
            await err(f"连接验证超时({timeout}秒)", config, e=e)
            return False
        except Exception as e:
            await err(f"连接验证失败: {_flat_error(e)}", config, e=e)
            return False

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
