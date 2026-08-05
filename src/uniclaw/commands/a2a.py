from __future__ import annotations

from uniclaw.tools.a2a.client import A2AClient
from uniclaw.tools.a2a.manager import A2AManager
from uniclaw.console.ui import err, info, ok, warn

SUBCOMMANDS = ["start", "stop", "status", "list", "add", "remove", "test"]


async def cmd_a2a(args: str, config) -> bool:
    """管理 A2A 服务与外部 Agent。

    仅 WebUI 模式可启用本服务。用法:/a2a start [token];/a2a stop;/a2a status;
    /a2a add <name> <url> [token];/a2a list;/a2a remove <name>;/a2a test <name>。
    """
    manager = A2AManager.get_instance()
    parts = args.split()
    action = parts[0].lower() if parts else "status"
    values = parts[1:]
    if action == "start":
        if not config.is_webui:
            await warn("A2A 服务仅复用 WebUI HTTP 服务;请在 WebUI 模式中执行 /a2a start。", config)
            return True
        try:
            service = manager.enable_service(config, values[0] if values else "")
            await ok("A2A 端点已在当前 WebUI 服务中启用:/.well-known/agent-card.json 和 /a2a", config)
            await warn(f"Bearer Token(请安全保存):{service['token']}", config)
        except Exception as exc:
            await err(f"A2A 服务启动失败:{exc}", config)
    elif action == "stop":
        await (ok("A2A 端点已禁用", config) if manager.disable_service() else warn("A2A 端点未启用", config))
    elif action == "status":
        status = manager.service_status()
        if status:
            await ok(f"A2A 端点已启用\nBearer Token:{status['token']}", config)
        else:
            await info("A2A 端点未启用", config)
    elif action == "list":
        agents = await manager.list_agents()
        await info("\n".join(f"- {x['name']}: {x['url']}" for x in agents) or "尚未添加外部 A2A 服务", config)
    elif action == "add" and len(values) >= 2:
        name, url = values[:2]
        token = values[2] if len(values) > 2 else ""
        try:
            card = await A2AClient(url, token).get_card()
            await manager.add_agent(name, url, token)
            await ok(f"已添加 A2A 服务 {name}({card.get('name', name)})", config)
        except Exception as exc:
            await err(f"无法添加 A2A 服务:{exc}", config)
    elif action == "remove" and values:
        await (ok(f"已删除 A2A 服务 {values[0]}", config) if await manager.remove_agent(values[0]) else warn("A2A 服务不存在", config))
    elif action == "test" and values:
        remote = await manager.get_agent(values[0])
        if not remote:
            await err("A2A 服务不存在", config)
        else:
            try:
                card = await A2AClient(remote["url"], remote.get("token", "")).get_card()
                await ok(f"连接成功:{card.get('name', values[0])}", config)
            except Exception as exc:
                await err(f"连接失败:{exc}", config)
    else:
        await info("用法:/a2a start [token] | stop | status | list | add <name> <url> [token] | remove <name> | test <name>", config)
    return True
