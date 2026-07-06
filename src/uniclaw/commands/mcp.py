import json
from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn, err

# 子命令列表
SUBCOMMANDS = [
    "list",
    "add",
    "remove",
    "show",
    "edit",
    "enable",
    "disable",
    "tools",
    "refresh",
]


async def cmd_mcp(args: str, config: AppConfig) -> bool:
    """MCP (Model Context Protocol) 服务器管理命令

    支持以下子命令:
    - list: 列出所有已配置的 MCP 服务器(默认命令)
    - add <名称> [JSON]: 添加新的 MCP 服务器,支持交互式或 JSON 配置
    - remove <名称>: 删除指定的 MCP 服务器
    - show <名称>: 显示指定服务器的详细配置信息
    - edit <名称> [JSON]: 编辑现有 MCP 服务器配置
    - enable <名称>: 启用指定的 MCP 服务器
    - disable <名称>: 禁用指定的 MCP 服务器
    - tools [服务器名]: 列出可用的 MCP 工具
    - refresh: 刷新并重新加载所有 MCP 工具

    Args:
        args: 命令参数,格式为 "<子命令> [参数]"
        task: 当前代理任务对象
        config: 配置字典,包含 interactive 等配置项

    Returns:
        bool: 始终返回 True 表示命令执行完成
    """
    from uniclaw.tools.mcp import MCPManager

    manager = MCPManager.get_instance()
    parts = args.strip().split(None, 1) if args else []
    subcmd = parts[0].lower() if parts else "list"
    subargs = parts[1] if len(parts) > 1 else ""

    if subcmd == "list" or not subcmd:
        await _mcp_list(manager, config=config)
    elif subcmd == "add":
        # 解析 name 和 json_str
        add_parts = subargs.split(None, 1) if subargs else []
        name = add_parts[0] if add_parts else ""
        json_str = add_parts[1] if len(add_parts) > 1 else ""
        await _mcp_add(manager, name, json_str, config)
    elif subcmd == "remove":
        await _mcp_remove(manager, subargs, config)
    elif subcmd == "show":
        await _mcp_show(manager, subargs, config)
    elif subcmd == "edit":
        # 解析 name 和 json_str
        edit_parts = subargs.split(None, 1) if subargs else []
        name = edit_parts[0] if edit_parts else ""
        json_str = edit_parts[1] if len(edit_parts) > 1 else ""
        await _mcp_edit(manager, name, json_str, config)
    elif subcmd == "enable":
        await _mcp_toggle(manager, subargs, True, config)
    elif subcmd == "disable":
        await _mcp_toggle(manager, subargs, False, config)
    elif subcmd == "tools":
        await _mcp_tools(manager, subargs, config)
    elif subcmd == "refresh":
        await _mcp_refresh(manager, config)
    else:
        await err(f"未知子命令: {subcmd}", config)
        await info(
            "可用命令: list, add, remove, show, edit, enable, disable, tools, refresh",
            config,
        )
    return True


async def _mcp_list(manager, config: AppConfig) -> bool:
    """列出所有已配置的 MCP 服务器

    显示每个服务器的名称、传输协议、启用状态和连接详情。

    Args:
        manager: MCPManager 实例

    Returns:
        bool: 始终返回 True
    """
    servers = await manager.list_servers()
    if not servers:
        await warn("暂无 MCP 服务器配置", config)
        await info("使用 /mcp add <名称> 添加服务器", config)
        return True
    await info(f"\nMCP 服务器 (共 {len(servers)} 个):\n", config)
    for s in servers:
        name = s["name"]
        transport = s.get("transport", "unknown")
        enabled = s.get("enabled", True)
        status = "✓ 启用" if enabled else "✗ 禁用"
        detail = ""
        if transport == "stdio":
            detail = f"{s.get('command', '')} {' '.join(s.get('args', []))}"
        else:
            detail = s.get("url", "")
        await info(f"  [{status}] {name} ({transport})", config)
        await info(f"    {detail}", config)
        await info("", config)
    return True


async def _mcp_add(
    manager, name: str, json_str: str = "", config: AppConfig = None
) -> bool:
    """添加 MCP 服务器

    用法:
        /mcp add <名称>  - 交互式添加
        /mcp add <名称> <JSON>  - 通过 JSON 配置添加

    Args:
        manager: MCPManager 实例
        name: 服务器名称
        json_str: JSON 格式的服务器配置字符串(可选)

    Returns:
        bool: 添加成功返回 True,失败返回 False
    """
    if not name:
        await err("请指定服务器名称: /mcp add <名称> [JSON]", config)
        return True

    if await manager.get_server(name):
        await err(f"服务器 '{name}' 已存在", config)
        return True

    # JSON 模式
    if json_str:
        try:
            connection = json.loads(json_str)
        except json.JSONDecodeError as e:
            await err(f"JSON 格式错误: {e}", config)
            return False
        if "transport" not in connection:
            await err("配置必须包含 'transport' 字段", config)
            return False
    else:
        # 交互式模式
        connection = await _mcp_interactive_input(config)
        if connection is None:
            return False

    try:
        await info("正在验证连接...", config)
        manager.add_server(name, connection)
    except ValueError as e:
        await err(str(e), config)
        return False

    await ok(f"✓ 已添加 MCP 服务器: {name}", config)
    await info("正在刷新 MCP 工具...", config)
    manager.refresh()
    tools_count = len(await manager.get_mcp_tools())
    await ok(f"✓ 已加载 {tools_count} 个 MCP 工具", config)
    return True


async def _mcp_interactive_input(config: AppConfig) -> dict | None:
    """交互式输入 MCP 配置

    引导用户逐步输入 MCP 服务器的配置信息,包括传输类型、命令/URL、参数等。

    Returns:
        dict | None: 配置字典,如果用户取消则返回 None
    """
    from uniclaw.console.ui import get_input

    prompt = """\n选择传输类型:
  [1] stdio (本地进程)
  [2] sse (Server-Sent Events)
  [3] streamable_http (HTTP Streamable)
  [4] websocket (WebSocket)

请输入编号: """
    choice = (await get_input(prompt, config=config)).strip()

    transport_map = {"1": "stdio", "2": "sse", "3": "streamable_http", "4": "websocket"}
    transport = transport_map.get(choice)
    if not transport:
        await err("无效选择", config)
        return None

    connection = {"transport": transport}

    if transport == "stdio":
        command = (
            await get_input("请输入命令 (例如: npx, python, node): ", config=config)
        ).strip()
        if not command:
            await err("命令不能为空", config)
            return None
        connection["command"] = command

        args_str = (await get_input("请输入参数 (空格分隔): ", config=config)).strip()
        if args_str:
            connection["args"] = args_str.split()

        env_prompt = "[可选] 环境变量 (KEY=VALUE 格式, 空行结束):"
        env = {}
        while True:
            line = (await get_input(f"{env_prompt}\n> ", config=config)).strip()
            if not line:
                break
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
            env_prompt = ""
        if env:
            connection["env"] = env

        cwd = (await get_input("[可选] 工作目录: ", config=config)).strip()
        if cwd:
            connection["cwd"] = cwd

    elif transport in ("sse", "streamable_http"):
        url = (await get_input("请输入 URL: ", config=config)).strip()
        if not url:
            await err("URL 不能为空", config)
            return None
        connection["url"] = url

        headers_prompt = "[可选] 请求头 (KEY=VALUE 格式, 空行结束):"
        headers = {}
        while True:
            line = (await get_input(f"{headers_prompt}\n> ", config=config)).strip()
            if not line:
                break
            if "=" in line:
                k, v = line.split("=", 1)
                headers[k.strip()] = v.strip()

        if headers:
            connection["headers"] = headers

        timeout_str = (
            await get_input("[可选] 超时时间 (秒, 直接回车跳过): ", config=config)
        ).strip()
        if timeout_str:
            try:
                connection["timeout"] = float(timeout_str)
            except ValueError:
                pass

    elif transport == "websocket":
        url = (await get_input("请输入 WebSocket URL: ", config=config)).strip()
        if not url:
            await err("URL 不能为空", config)
            return None
        connection["url"] = url

    return connection


async def _mcp_remove(manager, name: str, config: AppConfig = None) -> bool:
    """删除指定的 MCP 服务器

    Args:
        manager: MCPManager 实例
        name: 要删除的服务器名称

    Returns:
        bool: 始终返回 True
    """
    if not name:
        await err("请指定服务器名称: /mcp remove <名称>", config)
        return True
    if not await manager.get_server(name):
        await err(f"服务器 '{name}' 不存在", config)
        return True

    if not config.is_wechat:
        from uniclaw.console.ui import get_input

        confirm = (
            (await get_input(f"确认删除服务器 '{name}'? [y/N]: ", config=config))
            .strip()
            .lower()
        )
        if confirm != "y":
            await info("已取消", config)
            return True

    manager.remove_server(name)
    await ok(f"✓ 已删除服务器: {name}", config)
    manager.refresh()
    return True


async def _mcp_show(manager, name: str, config: AppConfig) -> bool:
    """显示指定 MCP 服务器的详细配置信息

    Args:
        manager: MCPManager 实例
        name: 服务器名称

    Returns:
        bool: 始终返回 True
    """
    if not name:
        await err("请指定服务器名称: /mcp show <名称>", config)
        return True
    server = await manager.get_server(name)
    if not server:
        await err(f"服务器 '{name}' 不存在", config)
        return True

    await info(f"\n服务器: {name}\n", config)
    for k, v in server.items():
        if k == "name":
            continue
        if isinstance(v, dict):
            await info(f"  {k}:", config)
            for dk, dv in v.items():
                await info(f"    {dk}: {dv}", config)
        elif isinstance(v, list):
            await info(f"  {k}: {' '.join(str(i) for i in v)}", config)
        else:
            await info(f"  {k}: {v}", config)
    await info("", config)
    return True


async def _mcp_edit(
    manager, name: str, json_str: str = "", config: AppConfig = None
) -> bool:
    """编辑 MCP 服务器

    用法:
        /mcp edit <名称>  - 交互式编辑
        /mcp edit <名称> <JSON>  - 通过 JSON 配置编辑

    Args:
        manager: MCPManager 实例
        name: 要编辑的服务器名称
        json_str: JSON 格式的新配置字符串(可选)

    Returns:
        bool: 编辑成功返回 True,失败返回 False
    """
    if not name:
        await err("请指定服务器名称: /mcp edit <名称> [JSON]", config)
        return True
    server = await manager.get_server(name)
    if not server:
        await err(f"服务器 '{name}' 不存在", config)
        return True

    await info(f"正在编辑服务器 '{name}'", config)
    old_connection = {k: v for k, v in server.items() if k not in ("name", "enabled")}
    old_enabled = server.get("enabled", True)
    manager.remove_server(name)

    try:
        result = await _mcp_add(manager, name, json_str)
        if not result:
            raise Exception("添加失败")
        return True
    except Exception:
        # 恢复旧配置(跳过验证)
        try:
            manager.add_server(name, old_connection, old_enabled, skip_validation=True)
            manager.refresh()
            await warn("已恢复原配置", config)
        except Exception:
            await err("恢复原配置失败", config)
        return True


async def _mcp_toggle(
    manager, name: str, enabled: bool, config: AppConfig = None
) -> bool:
    """启用或禁用指定的 MCP 服务器

    Args:
        manager: MCPManager 实例
        name: 服务器名称
        enabled: True 表示启用,False 表示禁用

    Returns:
        bool: 始终返回 True
    """
    if not name:
        cmd = "enable" if enabled else "disable"
        await err(f"请指定服务器名称: /mcp {cmd} <名称>", config)
        return True
    if not await manager.get_server(name):
        await err(f"服务器 '{name}' 不存在", config)
        return True

    action = "启用" if enabled else "禁用"
    manager.toggle_server(name, enabled)
    await ok(f"✓ 已{action}服务器: {name}", config)
    manager.refresh()
    return True


async def _mcp_tools(manager, server_name: str, config: AppConfig = None) -> bool:
    """列出可用的 MCP 工具

    Args:
        manager: MCPManager 实例
        server_name: 服务器名称(可选),为空则列出所有服务器的工具

    Returns:
        bool: 始终返回 True
    """
    tools_info = manager.get_tools_info(server_name if server_name else None)
    if not tools_info:
        await warn("暂无可用的 MCP 工具", config)
        return True

    await info(f"\nMCP 工具 (共 {len(tools_info)} 个):\n", config)
    for t in tools_info:
        await info(f"  • {t['name']} (来自: {t['server']})", config)
        if t["description"]:
            await info(f"    {t['description']}", config)
    await info("", config)
    return True


async def _mcp_refresh(manager, config: AppConfig = None) -> bool:
    """刷新并重新加载所有 MCP 工具

    Args:
        manager: MCPManager 实例

    Returns:
        bool: 始终返回 True
    """
    await info("正在刷新 MCP 工具...", config)
    manager.refresh()
    tools_count = len(await manager.get_mcp_tools())
    await ok(f"✓ 已加载 {tools_count} 个 MCP 工具", config)
    return True
