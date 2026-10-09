"""内置 MCP 服务器配置。

这些配置作为默认值提供,用户在 mcp.json 中的同名配置会覆盖此处定义。
添加新的内置 MCP 服务器只需在此字典中增加一个条目即可。
"""

BUILTIN_MCP_SERVERS: dict[str, dict] = {
    "exa": {
        "transport": "streamable_http",
        "url": "https://mcp.exa.ai/mcp",
        "enabled": True,
        # exa_agent_run 是长任务:默认读超时已放宽到 DEFAULT_CALL_TIMEOUT(300s),
        # 这里不强制覆盖;若仍不够可在工具调用时传 timeout 或在此加 read_timeout。
    },
}
