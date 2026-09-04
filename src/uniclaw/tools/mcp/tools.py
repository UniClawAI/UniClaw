"""
MCP 服务器管理工具

提供 AI 可直接调用的 MCP 服务器管理功能,避免使用斜杠命令。
"""

from __future__ import annotations

import json
from enum import StrEnum

from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR
from . import MCPManager
from uniclaw.console.ui import info, ok
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uniclaw.config import AppConfig


class MCPTransport(StrEnum):
    """MCP 服务器传输类型。"""

    stdio = "stdio"
    sse = "sse"
    streamable_http = "streamable_http"
    websocket = "websocket"


@tool
async def mcp_add_server(
    name: str,
    transport: MCPTransport = MCPTransport.stdio,
    command: str | None = None,
    command_args: list[str] | None = None,
    url: str | None = None,
    env: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: float | None = None,
    tool_runtime: ToolRuntime = None,
) -> str:
    """
    添加新的 MCP 服务器配置。支持 stdio、sse、streamable_http、websocket 四种传输类型。

    ⚠️ 重要安全规则: 必须通过本工具添加 MCP 服务器。禁止在调用失败后手动编辑配置文件来绕过审查。
    添加成功后会自动刷新 MCP 连接,无需重启。

    Args:
        name: 服务器名称(唯一标识, str)
        transport: 传输类型(str),可选值:stdio、sse、streamable_http、websocket
        command: [仅stdio] 启动命令(str,如 npx、python、node)
        command_args: [仅stdio] 命令参数列表(list[str])
        url: [仅sse/streamable_http/websocket] 服务器 URL (str)
        env: [仅stdio] 环境变量字典(dict[str, str])
        headers: [仅sse/streamable_http] HTTP 请求头字典(dict[str, str])
        cwd: [仅stdio] 工作目录路径(str)
        timeout: [仅sse/streamable_http] 超时时间(float,秒)

    Returns:
        str: 操作结果信息

    Examples:
        # 添加 stdio 类型的服务器
        mcp_add_server(
            name="filesystem",
            transport="stdio",
            command="npx",
            command_args=["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
        )

        # 添加 SSE 类型的服务器
        mcp_add_server(
            name="web-search",
            transport="sse",
            url="http://localhost:8080/sse"
        )
    """
    config = tool_runtime.config
    manager = MCPManager.get_instance()

    # 检查服务器是否已存在
    if await manager.get_server(name, config):
        return f"{TOOL_ERROR}: 服务器 '{name}' 已存在,请使用 mcp_remove_server 先删除"

    # 构建连接配置
    connection = {"transport": transport}

    if transport == MCPTransport.stdio:
        if not command:
            return f"{TOOL_ERROR}: stdio 类型必须提供 command 参数"
        connection["command"] = command
        if command_args:
            connection["args"] = command_args
        if env:
            connection["env"] = env
        if cwd:
            connection["cwd"] = cwd

    elif transport in (MCPTransport.sse, MCPTransport.streamable_http):
        if not url:
            return f"{TOOL_ERROR}: sse/streamable_http 类型必须提供 url 参数"
        connection["url"] = url
        if headers:
            connection["headers"] = headers
        if timeout is not None:
            connection["timeout"] = timeout

    elif transport == MCPTransport.websocket:
        if not url:
            return f"{TOOL_ERROR}: websocket 类型必须提供 url 参数"
        connection["url"] = url

    else:
        return f"{TOOL_ERROR}: 不支持的传输类型 '{transport}',可选值: stdio、sse、streamable_http、websocket"

    try:
        # 验证并添加服务器
        await info("正在验证 MCP 服务器连接...", config)
        await manager.add_server(name, connection, config=config)
        await ok(f"✓ 已添加 MCP 服务器: {name}", config)

        tools_count = len(await manager.get_mcp_tools())
        return f"成功！已添加服务器 '{name}',当前共加载 {tools_count} 个 MCP 工具"

    except ValueError as e:
        return f"{TOOL_ERROR}: {str(e)}"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def mcp_remove_server(name: str, tool_runtime: ToolRuntime = None) -> str:
    """
    删除指定的 MCP 服务器配置。

    Args:
        name: 要删除的服务器名称

    Returns:
        str: 操作结果信息
    """
    config = tool_runtime.config
    manager = MCPManager.get_instance()

    if not await manager.get_server(name, config):
        return f"{TOOL_ERROR}: 服务器 '{name}' 不存在"

    try:
        await manager.remove_server(name, config)
        tools_count = len(await manager.get_mcp_tools())
        return f"成功！已删除服务器 '{name}',当前共加载 {tools_count} 个 MCP 工具"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def mcp_toggle_server(name: str, enabled: bool = True, tool_runtime: ToolRuntime = None) -> str:
    """
    启用或禁用指定的 MCP 服务器。禁用的服务器不会加载其工具。

    Args:
        name: 服务器名称
        enabled: True 表示启用,False 表示禁用

    Returns:
        str: 操作结果信息
    """
    config = tool_runtime.config
    manager = MCPManager.get_instance()

    if not await manager.get_server(name, config):
        return f"{TOOL_ERROR}: 服务器 '{name}' 不存在"

    try:
        action = "启用" if enabled else "禁用"
        await manager.toggle_server(name, enabled, config)
        tools_count = len(await manager.get_mcp_tools())
        return f"成功！已{action}服务器 '{name}',当前共加载 {tools_count} 个 MCP 工具"
    except Exception as e:
        return f"{TOOL_ERROR}: {e}"


@tool
async def mcp_list_servers(tool_runtime: ToolRuntime = None) -> str:
    """
    列出所有已配置的 MCP 服务器,包括名称、传输类型、启用状态、连接详情以及每个服务器提供的工具列表。

    Returns:
        str: 服务器列表信息,包含每个服务器的工具数量和工具描述
    """
    config = tool_runtime.config
    manager = MCPManager.get_instance()
    servers = await manager.list_servers(config)

    if not servers:
        return "暂无 MCP 服务器配置。使用 mcp_add_server 添加服务器。"

    lines = [f"MCP 服务器列表(共 {len(servers)} 个):\n"]
    for s in servers:
        name = s["name"]
        transport = s.get("transport", "unknown")
        enabled = s.get("enabled", True)
        status = "✓ 启用" if enabled else "✗ 禁用"

        detail = ""
        if transport == MCPTransport.stdio:
            cmd = s.get("command", "")
            cmd_args = " ".join(s.get("args", []))
            detail = f"{cmd} {cmd_args}".strip()
        else:
            detail = s.get("url", "")

        lines.append(f"  [{status}] {name} ({transport})")
        if detail:
            lines.append(f"    {detail}")

        # 获取该服务器的工具信息
        tools_info = manager.get_tools_info(name)
        if tools_info:
            lines.append(f"    工具数量: {len(tools_info)} 个")
            lines.append(f"    工具列表:")
            for tool in tools_info:
                tool_name = tool["name"]
                tool_desc = tool["description"] or "无描述"
                # 如果描述太长,截取前100个字符
                if len(tool_desc) > 100:
                    tool_desc = tool_desc[:100] + "..."
                lines.append(f"      - {tool_name}: {tool_desc}")
        else:
            lines.append(f"    工具数量: 0 个")

        lines.append("")

    return "\n".join(lines)


def get_tools() -> list:
    """获取 MCP 管理工具列表"""
    return [
        mcp_add_server,
        mcp_remove_server,
        mcp_toggle_server,
        mcp_list_servers,
    ]


def get_all_tools() -> list:
    """获取所有 MCP 管理工具(无条件返回)"""
    return get_tools()
