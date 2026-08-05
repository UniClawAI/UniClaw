from .fs import get_tools as fs_get_tools, get_all_tools as fs_get_all_tools
from .multi_agent.tools import (
    get_tools as multi_agent_get_tools,
    get_all_tools as multi_agent_get_all_tools,
)
from .plan import get_tools as plan_get_tools, get_all_tools as plan_get_all_tools
from .shell import get_tools as shell_get_tools, get_all_tools as shell_get_all_tools
from .skill.tools import (
    get_tools as skill_get_tools,
    get_all_tools as skill_get_all_tools,
)
from .web import get_tools as web_get_tools, get_all_tools as web_get_all_tools
from .web_browse.tools import (
    get_tools as web_browse_get_tools,
    get_all_tools as web_browse_get_all_tools,
)
from .memory.tools import (
    get_tools as memory_get_tools,
    get_all_tools as memory_get_all_tools,
)
from .media import get_tools as media_get_tools, get_all_tools as media_get_all_tools
from .sandbox import (
    get_tools as sandbox_get_tools,
    get_all_tools as sandbox_get_all_tools,
)
from .scheduler.tools import (
    get_tools as scheduler_get_tools,
    get_all_tools as scheduler_get_all_tools,
)
from .sleep import get_all_tools as sleep_get_all_tools
from .sleep import wait as sleep_wait, sleep_timer
from .monitor.tools import (
    get_tools as process_get_tools,
    get_all_tools as process_get_all_tools,
)
from .todolist import (
    get_tools as todolist_get_tools,
    get_all_tools as todolist_get_all_tools,
)
from .ask import get_tools as ask_get_tools, get_all_tools as ask_get_all_tools
from .mcp.tools import (
    get_tools as mcp_management_get_tools,
    get_all_tools as mcp_management_get_all_tools,
)
from .session import (
    get_tools as session_get_tools,
    get_all_tools as session_get_all_tools,
)
from .session.recall import (
    get_tools as recall_get_tools,
    get_all_tools as recall_get_all_tools,
)
from .security import (
    get_tools as security_get_tools,
    get_all_tools as security_get_all_tools,
)
from .hooks.tools import (
    get_tools as hooks_get_tools,
    get_all_tools as hooks_get_all_tools,
)
from .computer_use import (
    get_tools as computer_use_get_tools,
    get_all_tools as computer_use_get_all_tools,
)
from .notify import get_tools as notify_get_tools, get_all_tools as notify_get_all_tools
from .search import get_tools as search_get_tools, get_all_tools as search_get_all_tools
from .help import get_tools as help_get_tools, get_all_tools as help_get_all_tools
from .tts.tools import get_tools as tts_get_tools, get_all_tools as tts_get_all_tools
from .send_file import (
    get_tools as send_file_get_tools,
    get_all_tools as send_file_get_all_tools,
)
from .wechat import get_tools as wechat_get_tools, get_all_tools as wechat_get_all_tools
from .knowledge import (
    get_tools as knowledge_get_tools,
    get_all_tools as knowledge_get_all_tools,
)
from .advisor import (
    get_tools as advisor_get_tools,
    get_all_tools as advisor_get_all_tools,
)
from .rag import get_tools as rag_get_tools, get_all_tools as rag_get_all_tools
from .download.tools import (
    get_tools as download_get_tools,
    get_all_tools as download_get_all_tools,
)
from uniclaw.tools.a2a.tools import get_tools as a2a_get_tools, get_all_tools as a2a_get_all_tools
from .mcp import MCPManager
from .registry import get_tools as registry_get_tools, init_registry

_registry_initialized = False


async def _ensure_registry():
    """懒初始化工具注册表(仅首次调用时执行)。"""
    global _registry_initialized
    if _registry_initialized:
        return
    _registry_initialized = True
    all_tools = await get_all_tools()
    init_registry(all_tools)


async def get_core_tools(sub_agent: bool = False) -> list:
    """获取核心工具 + search_tools(约 19 个)。

    核心工具始终加载完整 schema,是 prompt 缓存的稳定前缀。
    扩展工具通过 search_tools 按需发现和加载。

    Args:
        sub_agent: 子代理模式时为 True,排除计划模式工具。
    """
    from .registry import CORE_TOOLS

    await _ensure_registry()
    # 直接使用 CORE_TOOLS,无需重复收集再过滤
    tools = list(CORE_TOOLS)
    # 子代理模式排除计划模式工具
    if sub_agent:
        tools = [
            t for t in tools if t.name not in ("enter_plan_mode", "exit_plan_mode")
        ]
    # search_tools 不在 CORE_TOOLS 中,单独添加
    tools.extend(registry_get_tools())
    return tools


async def get_tools(config) -> list:
    """获取所有可用工具(包括 MCP 工具)。

    Args:
        config: AppConfig 实例,从中获取 todolist 和 is_sub。
    """
    mcp_manager = MCPManager.get_instance()
    mcp_tools = await mcp_manager.get_mcp_tools()
    tools = [
        *fs_get_tools(),
        *multi_agent_get_tools(),
        *await shell_get_tools(config),
        *skill_get_tools(),
        *web_get_tools(),
        *web_browse_get_tools(),
        *memory_get_tools(),
        *media_get_tools(config),
        *await sandbox_get_tools(config),
        *scheduler_get_tools(),
        sleep_wait,  # wait 子代理可用
        *process_get_tools(),
        *mcp_management_get_tools(),
        *session_get_tools(),
        *recall_get_tools(),
        *notify_get_tools(),
        *search_get_tools(),
        *help_get_tools(),
        *tts_get_tools(config),
        *send_file_get_tools(),
        *wechat_get_tools(),
        *knowledge_get_tools(),
        *advisor_get_tools(config),
        *download_get_tools(),
        *rag_get_tools(config),
        *a2a_get_tools(),
        *registry_get_tools(),
        *mcp_tools,
    ]
    if not config.is_sub:
        tools.extend(
            [
                *plan_get_tools(),
                *todolist_get_tools(),
                *ask_get_tools(),
                *security_get_tools(),
                *hooks_get_tools(),
                *computer_use_get_tools(config),
                sleep_timer,  # sleep_timer 仅主 agent 可用
            ]
        )
    return tools


async def get_all_tools() -> list:
    """获取所有内置工具"""
    mcp_manager = MCPManager.get_instance()
    mcp_tools = await mcp_manager.get_mcp_tools()
    return [
        *fs_get_all_tools(),
        *multi_agent_get_all_tools(),
        *plan_get_all_tools(),
        *shell_get_all_tools(),
        *skill_get_all_tools(),
        *web_get_all_tools(),
        *web_browse_get_all_tools(),
        *memory_get_all_tools(),
        *media_get_all_tools(),
        *sandbox_get_all_tools(),
        *scheduler_get_all_tools(),
        *sleep_get_all_tools(),
        *process_get_all_tools(),
        *todolist_get_all_tools(),
        *ask_get_all_tools(),
        *mcp_management_get_all_tools(),
        *session_get_all_tools(),
        *recall_get_all_tools(),
        *security_get_all_tools(),
        *hooks_get_all_tools(),
        *computer_use_get_all_tools(),
        *notify_get_all_tools(),
        *search_get_all_tools(),
        *help_get_all_tools(),
        *tts_get_all_tools(),
        *send_file_get_all_tools(),
        *wechat_get_all_tools(),
        *knowledge_get_all_tools(),
        *advisor_get_all_tools(),
        *download_get_all_tools(),
        *rag_get_all_tools(),
        *a2a_get_all_tools(),
        *mcp_tools,
    ]
