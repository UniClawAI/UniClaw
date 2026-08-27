"""Exa 搜索实现(通过内置 MCP 服务器调用)。

Exa 是 AI 搜索引擎, 通过 MCP 协议调用。内置服务器配置见
tools/mcp/builtin.py 中的 "exa" 条目 (https://mcp.exa.ai/mcp)。
"""

import asyncio
import weakref

import httpx

from uniclaw.config import AppConfig
from uniclaw.utils.constants import TOOL_ERROR

from .base import PLATFORM_ERROR, safe_search
from .time_range import exa_rfc3339, parse_time_range

NAME = "exa"
LABEL = "Exa"

_MCP_SERVER = "exa"
# 常见的 Exa MCP 搜索工具名, 按优先级尝试 (全名形如 "exa_web_search_exa")
_SEARCH_TOOL_CANDIDATES = ("search", "web_search", "web_search_exa", "exa_search")

# ── 长连接缓存 ─────────────────────────────────────────────
# mcp.exa.ai 每次握手需数秒, 是此前反复超时的主因。首次搜索建立连接后
# 缓存该会话, 后续搜索复用, 只在连接失效、参数变化或事件循环变化时重建。
# 注意: 缓存的 session 绑定创建它的事件循环, 而 asyncio.run() 每次调用
# 都会新建 loop, 若跨 loop 复用会导致在新 loop 上 await 旧 loop 的 IO 永久挂起。
_exa_cache: dict = {
    "lock": None,
    "cm": None,
    "session": None,
    "conn": None,
    "loop": None,
    "tool": None,
    "tool_props": None,
}

# 已注册退出清理的 loop 集合 (弱引用, 不阻止 loop 被回收)
_patched_loops: "weakref.WeakSet" = weakref.WeakSet()


def _install_shutdown_cleanup(loop) -> None:
    """在 loop 的 shutdown_asyncgens 前插入 Exa 连接清理。

    asyncio.run()/进程退出时 shutdown_asyncgens 会强制 aclose 仍挂起的
    streamable_http_client async generator, 其内部 anyio task group 无法在
    GeneratorExit 下收尾, 导致 stderr 打印 cancel-scope/aclose 错误。
    改为趁 loop 还存活时主动干净关闭, 之后原 shutdown_asyncgens 无事可做。
    """
    if loop in _patched_loops:
        return
    _patched_loops.add(loop)
    orig_shutdown = loop.shutdown_asyncgens

    async def _shutdown_with_exa_cleanup():
        try:
            if _exa_cache["loop"] is loop:
                await _close_exa_connection()
        except Exception:
            pass
        await orig_shutdown()

    loop.shutdown_asyncgens = _shutdown_with_exa_cleanup


def _reset_exa_cache():
    """丢弃 Exa 连接缓存 (测试隔离用), 下次搜索会重新建立连接。"""
    _exa_cache.update(
        lock=asyncio.Lock(),
        cm=None,
        session=None,
        conn=None,
        loop=None,
        tool=None,
        tool_props=None,
    )


async def _close_exa_connection():
    """关闭当前 Exa 连接并清空缓存。异常时仅清空缓存, 不阻塞调用方。

    asyncio 关闭期间调用 __aexit__ 可能触发 anyio 的
    "Attempted to exit cancel scope in a different task" 告警,
    此时进程即将退出, 连接由 OS 回收, 无需重试。
    注意: 仅在当前事件循环内调用, 跨 loop 时旧连接不可 await, 直接丢弃引用。
    """
    cm, session = _exa_cache["cm"], _exa_cache["session"]
    _exa_cache.update(
        cm=None, session=None, conn=None, loop=None, tool=None, tool_props=None
    )
    for closer in (session, cm):
        if closer is not None:
            try:
                await closer.__aexit__(None, None, None)
            except BaseException:
                pass


async def _acquire_session(connection: dict):
    """获取可复用的 Exa MCP 会话。连接参数与事件循环均未变时直接复用。"""
    loop = asyncio.get_running_loop()
    if (
        _exa_cache["session"] is not None
        and _exa_cache["conn"] == connection
        and _exa_cache["loop"] is loop
    ):
        return _exa_cache["session"]

    lock = _exa_cache["lock"] or asyncio.Lock()
    _exa_cache["lock"] = lock
    async with lock:
        if (
            _exa_cache["session"] is not None
            and _exa_cache["conn"] == connection
            and _exa_cache["loop"] is loop
        ):
            return _exa_cache["session"]

        if _exa_cache["session"] is not None:
            if _exa_cache["loop"] is not loop:
                # 缓存绑定的是已关闭的旧 loop, 其 IO 不可在新 loop 上 await,
                # 直接丢弃引用 (旧 loop 关闭时自会回收资源)。
                _exa_cache.update(
                    cm=None,
                    session=None,
                    conn=None,
                    loop=None,
                    tool=None,
                    tool_props=None,
                )
            else:
                await _close_exa_connection()

        from mcp import ClientSession

        from uniclaw.tools.mcp import _connect_mcp

        cm = _connect_mcp(connection)
        read, write = await cm.__aenter__()
        session = ClientSession(read, write)
        # 必须进入 session 上下文: __aenter__ 才会启动 _receive_loop 消费响应流,
        # 否则 read_stream (容量 0) 上 send 永久阻塞, initialize() 挂死。
        await session.__aenter__()
        await session.initialize()
        # 进程/loop 退出前主动关闭长连接, 避免 shutdown_asyncgens 强制 aclose 报错
        _install_shutdown_cleanup(loop)
        _exa_cache.update(cm=cm, session=session, conn=connection, loop=loop)
        return session


async def _search_rest(
    query: str,
    api_key: str,
    limit: int,
    sort: str = "",
    search_type: str = "",
    time_range: str = "",
) -> str:
    """Exa REST API 快速路径 (单次 POST, 比 MCP 首次握手快数秒)。

    Args:
        query: 搜索关键词
        api_key: Exa API key
        limit: 结果数量
        sort: Exa category (如 company/research), 空则不指定
        search_type: 检索类型, 空则用 "auto"
        time_range: 时间范围过滤 (REST 原生支持日期参数, 无需 schema 门控)

    Returns:
        str: 格式化的搜索结果文本

    Raises:
        Exception: 网络/HTTP 错误时抛出, 由调用方决定是否回退 MCP
    """
    payload = {
        "query": query,
        "numResults": limit,
        "type": search_type or "auto",
        # 请求富文本字段: 正文全文 + 高亮片段 (与 MCP 路径信息量对齐,
        # 否则 API 默认只返回 id/title/url, 导致加 key 后输出反而更简陋)
        "contents": {
            "text": True,
            "highlights": {"numSentences": 5},
        },
    }
    if sort:
        payload["category"] = sort
    tr = parse_time_range(time_range)
    if tr.start:
        payload["startPublishedDate"] = exa_rfc3339(tr.start, end=False)
    if tr.end:
        payload["endPublishedDate"] = exa_rfc3339(tr.end, end=True)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            "https://api.exa.ai/search", json=payload, headers=headers
        )
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return f"Exa: 无搜索结果"
    lines = [f"**Exa 搜索结果** ({len(results)} 条):\n"]
    for item in results[:limit]:
        title = (item.get("title") or "").strip() or item.get("url", "")
        lines.append(f"**{title}**")
        url = item.get("url") or ""
        if url:
            lines.append(f"  URL: {url}")
        published = (item.get("publishedDate") or "").strip()
        if published:
            lines.append(f"  发布时间: {published}")
        author = (item.get("author") or "").strip()
        if author:
            lines.append(f"  作者: {author}")
        # 摘要: 优先拼接 highlights, 兜底 text (不截断, 完整输出)
        highlights = [h for h in (item.get("highlights") or []) if h]
        text = (item.get("text") or "").strip()
        if highlights:
            lines.append(f"  摘要: {' '.join(highlights)}")
        elif text:
            lines.append(f"  摘要: {text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@safe_search
async def search(
    query: str,
    limit: int,
    sort: str,
    search_type: str,
    config: AppConfig | None,
    time_range: str = "",
) -> str:
    """Exa 搜索 (配置 key 时优先 REST 直连, 否则/失败回退内置 MCP 服务器)。"""
    # 有 key 时优先 REST 直连: 单次 POST 比 MCP 握手快数秒;
    # 失败静默回退 MCP 免费端点 (mcp.exa.ai 匿名限流可用)
    api_key = (config.EXA_API_KEY if config else "") or ""
    if api_key:
        try:
            return await _search_rest(
                query,
                api_key,
                limit,
                sort=sort,
                search_type=search_type,
                time_range=time_range,
            )
        except Exception:
            pass

    from uniclaw.tools.mcp import MCPManager

    # 获取 Exa MCP 服务器配置
    manager = MCPManager.get_instance()
    server = await manager.get_server(_MCP_SERVER, config)
    if server is None:
        return (
            f"{PLATFORM_ERROR}: Exa MCP 服务器不可用。"
            f"请确认已配置 EXA_API_KEY 且网络可访问 mcp.exa.ai"
        )

    connection = {k: v for k, v in server.items() if k not in ("name", "enabled")}

    # Exa (mcp.exa.ai) 未被墙, 可直连, 不注入全局代理: 走代理反而可能
    # 因本地节点慢/不稳定而拖慢连接。EXA_API_KEY 已由 MCPManager.load_config
    # 统一拼接到 URL, 此处无需重复处理。
    # 单条长连接内完成 发现工具 + 调用: 连接建立后复用, 握手只发生一次。

    try:
        session = await _acquire_session(connection)

        # 发现搜索工具: 已缓存工具名时跳过 list_tools 往返 (~4-6s)
        tool_name = _exa_cache["tool"]
        tool_obj = None
        if not tool_name:
            # 按候选名优先匹配已知工具 (全名形如 "web_search_exa")
            tools_result = await session.list_tools()
            for candidate in _SEARCH_TOOL_CANDIDATES:
                for t in tools_result.tools:
                    if t.name.lower() == candidate or t.name.lower().endswith(
                        f"_{candidate}"
                    ):
                        tool_name = t.name
                        tool_obj = t
                        break
                if tool_name:
                    break

            if not tool_name:
                names = ", ".join(t.name for t in tools_result.tools)
                return (
                    f"{PLATFORM_ERROR}: Exa MCP 服务器上未找到搜索工具, "
                    f"可用工具: {names}"
                )
            _exa_cache["tool"] = tool_name
            # 记录远端工具 schema 支持的参数名 (小写), 供 time_range 门控
            schema = getattr(tool_obj, "inputSchema", None) or {}
            _exa_cache["tool_props"] = {
                p.lower() for p in (schema.get("properties") or {})
            }

        # 构造搜索参数
        kwargs = {"query": query, "numResults": limit}
        if search_type:
            kwargs["type"] = search_type
        if sort:
            kwargs["category"] = sort  # Exa 将 sort 视作 category (如 company/research)
        # 时间过滤: 仅当远端工具 schema 声明支持日期参数时才传,
        # 避免 call_tool 因未知参数被拒导致整个平台失败 (静默降级)
        if time_range.strip():
            tr = parse_time_range(time_range)
            props = _exa_cache["tool_props"]
            if props is None or "startpublisheddate" in props:
                if tr.start:
                    kwargs["startPublishedDate"] = exa_rfc3339(tr.start, end=False)
                if tr.end:
                    kwargs["endPublishedDate"] = exa_rfc3339(tr.end, end=True)

        result = await session.call_tool(tool_name, arguments=kwargs)

        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        content = "\n".join(parts) if parts else "(无输出)"
    except asyncio.TimeoutError:
        await _close_exa_connection()
        return (
            f"{PLATFORM_ERROR}: Exa MCP 服务器连接超时。"
            f"请确认网络可访问 mcp.exa.ai, 或在 settings.json 中配置 proxy_url"
        )
    except Exception:
        await _close_exa_connection()
        return (
            f"{PLATFORM_ERROR}: Exa MCP 服务器不可用。"
            f"请确认已配置 EXA_API_KEY 且网络可访问 mcp.exa.ai"
        )

    if content.startswith(TOOL_ERROR):
        return f"{PLATFORM_ERROR}: {content[len(TOOL_ERROR):].strip()}"
    return f"**Exa 搜索结果** ({query}):\n\n{content}"
