"""/subagent 命令 — 让用户绕过 LLM 直接管理子智能体。

子智能体的创建由 AI(LLM)通过 subagent_create 工具完成;本命令让**用户**
在终端直接查看/派活/关闭/检查子智能体,复用 subagent_list_tasks /
subagent_send_to_child / subagent_close / subagent_check_result 等工具逻辑。
与 a2a 等纯操作型命令一致,不返回字符串给 LLM 续跑。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from uniclaw.console.ui import info, ok, warn, err

if TYPE_CHECKING:
    from uniclaw.config import AppConfig

# 子命令列表
SUBCOMMANDS = ["list", "send", "close", "check"]

_USAGE = (
    "/subagent 管理子智能体\n"
    "  /subagent list                           - 列出所有子智能体\n"
    "  /subagent send <名称或ID> <消息>          - 发送消息\n"
    "  /subagent close <名称或ID>               - 关闭子智能体\n"
    "  /subagent check <名称或ID>               - 查看结果"
)


async def cmd_subagent(args: str, config: AppConfig) -> bool:
    """通过斜杠命令直接管理子智能体(不经过 LLM)。

    查看、派活、关闭、检查当前 agent 创建的子智能体,等价于手动调用
    subagent_list_tasks / subagent_send_to_child / subagent_close /
    subagent_check_result 等工具。子智能体的创建由 AI 通过
    subagent_create 工具完成。

    用法:
      /subagent list                             - 列出当前 agent 创建的子智能体
      /subagent send <名称或ID> <消息>            - 向指定子智能体发送消息
      /subagent close <名称或ID>                  - 关闭指定子智能体
      /subagent check <名称或ID>                  - 查看指定子智能体结果
    """
    subcmd, _, rest = args.partition(" ")
    subcmd = subcmd.lower()
    rest = rest.strip()

    if subcmd == "list":
        await _list(config)
        return True

    if subcmd == "send":
        # send <名称或ID> <消息>: 第一个词是目标, 剩余全部是消息
        target, _, msg = rest.partition(" ")
        if not target or not msg.strip():
            await warn("用法: /subagent send <名称或ID> <消息>", config)
            return True
        await _send(config, target, msg.strip())
        return True

    if subcmd in ("close", "check"):
        if not rest:
            await warn(f"用法: /subagent {subcmd} <名称或ID>", config)
            return True
        if subcmd == "close":
            await _close(config, rest)
        else:
            await _check(config, rest)
        return True

    # 无子命令:显示用法并列出当前子智能体
    await info(_USAGE, config)
    await _list(config)
    return True


async def _list(config: AppConfig) -> None:
    """列出当前 agent 的子智能体。"""
    from uniclaw.tools.base import ToolRuntime
    from uniclaw.tools.multi_agent.tools import subagent_list_tasks

    result = await subagent_list_tasks(tool_runtime=ToolRuntime(config=config))
    await info(
        f"{result}\n提示: /subagent check/send/close <名称或ID> 可与其交互",
        config,
    )


async def _send(config: AppConfig, target: str, message: str) -> None:
    """向指定子智能体发送消息。"""
    from uniclaw.tools.base import ToolRuntime
    from uniclaw.tools.multi_agent.tools import subagent_send_to_child

    result = await subagent_send_to_child(
        target, message, tool_runtime=ToolRuntime(config=config)
    )
    if result.startswith("[TOOL_ERROR]"):
        await err(result[len("[TOOL_ERROR]") :].strip(), config)
    else:
        await ok(f"✓ {result}", config)


async def _close(config: AppConfig, target: str) -> None:
    """关闭指定子智能体。"""
    from uniclaw.tools.base import ToolRuntime
    from uniclaw.tools.multi_agent.tools import subagent_close

    result = await subagent_close(target, tool_runtime=ToolRuntime(config=config))
    if result.startswith("[TOOL_ERROR]"):
        await err(result[len("[TOOL_ERROR]") :].strip(), config)
    else:
        await ok(f"✓ {result}", config)


async def _check(config: AppConfig, target: str) -> None:
    """查看指定子智能体的结果。"""
    from uniclaw.tools.base import ToolRuntime
    from uniclaw.tools.multi_agent.tools import subagent_check_result

    result = await subagent_check_result(
        target, tool_runtime=ToolRuntime(config=config)
    )
    if result.startswith("[TOOL_ERROR]"):
        await err(result[len("[TOOL_ERROR]") :].strip(), config)
    else:
        await info(result, config)
