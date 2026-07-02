"""内置 MCP 服务器配置。

这些配置作为默认值提供,用户在 mcp.json 中的同名配置会覆盖此处定义。
添加新的内置 MCP 服务器只需在此字典中增加一个条目即可。
"""

BUILTIN_MCP_SERVERS: dict[str, dict] = {
    "exa": {
        "transport": "streamable_http",
        "url": "https://mcp.exa.ai/mcp",
        "enabled": True,
    },
}
