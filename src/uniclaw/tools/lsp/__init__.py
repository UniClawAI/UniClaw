"""LSP 语言智能工具包 — AI agent 查询语言服务器的语义信息。

模块结构:
- protocol.py  LSP 帧编解码与协议常量
- client.py    LspClient:单语言服务器子进程的 JSON-RPC 客户端
- servers.py   语言服务器注册表(内置,自动探测,无可配置面)
- manager.py   LSPManager:按语言管理会话、文档同步、崩溃重启
- tools.py     @tool 定义(lsp_status/lsp_start/lsp_stop/lsp_diagnostics/
               lsp_definition/lsp_hover/lsp_references/lsp_symbols)

使用:经 registry._build_extended_keywords 注册为扩展工具,
AI 通过 search_tools 搜索 "lsp 诊断" 等关键词按需加载。
"""

from .manager import LSPManager
from .tools import get_tools

__all__ = ["LSPManager", "get_tools"]
