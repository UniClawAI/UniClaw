"""进程重启工具(restart_agent)。

支持 AI 重启自身所在的 UniClaw 进程(WebUI 模式):
修改自身代码/配置后重启使改动生效,重启完成后自动恢复原会话并继续工作。

时序保证:工具立即返回 → LLM 生成后续回复 → agent 本轮结束 → 后台任务
(manager.do_restart)确认 agent 空闲后会话数据完整 → 保存会话 → 写标志文件 →
优雅关闭服务 → launch() 拉起新进程 → 新进程读取标志恢复会话并按固定提示词唤醒。
"""

import asyncio

from uniclaw.config import AppConfig, RunMode
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.tools.restart import manager
from uniclaw.utils.constants import TOOL_ERROR


@tool
def restart_agent(tool_runtime: ToolRuntime = None) -> str:
    """
    重启 UniClaw 自身所在的进程(WebUI 模式专用)。当你修改了 UniClaw 的源代码
    或配置文件后,用本工具重启使改动生效。重启不会丢失工作进度:等待本轮回复
    结束后保存会话,新进程启动后会自动唤醒你继续未完成的任务。

    调用后向用户简要说明原因,然后立即结束回复,不要再执行任何操作。
    A2A/WeChat 会话或存在其他运行中的 agent 时会被拒绝。

    Returns:
        str: 成功返回重启计划确认消息;失败返回 "TOOL_ERROR: " 前缀的错误原因。
    """
    config = tool_runtime.config

    # 仅 WebUI 模式可用
    if config is None or config.run_mode != RunMode.WEBUI:
        return f"{TOOL_ERROR}: restart_agent 仅在 WebUI 模式下可用"

    task = config.current_agent
    session = task.session if task else None

    # A2A 会话不持久化、WeChat 会话由 Bot 管理,均不支持重启
    from uniclaw.tools.session.session import SessionType

    if session is None or session.session_type in (
        SessionType.A2A,
        SessionType.WECHAT,
    ):
        return f"{TOOL_ERROR}: 当前会话类型不支持重启进程"

    # 防止重复调度
    if manager.restart_scheduled:
        return "重启已在调度中,请勿重复调用"

    # 其他会话仍有 agent 运行时拒绝重启(重启进程会终止所有会话的任务)
    running = manager.find_running_other_sessions(config)
    if running:
        titles = ", ".join(running[:3])
        return (
            f"{TOOL_ERROR}: 还有 {len(running)} 个其他会话的 agent 正在运行"
            f"({titles}),重启进程会中断它们。请等它们完成后再重启"
        )

    manager.restart_scheduled = True
    asyncio.create_task(manager.do_restart(config))

    return (
        "✅ 重启已调度:等待本轮对话结束后,系统将保存会话并重启进程,"
        "新进程启动后会自动唤醒本会话继续工作。请结束当前回复,不要再执行其他操作。"
    )


def get_tools(config: AppConfig = None) -> list:
    """获取重启工具列表。

    仅 WebUI 模式下暴露给 LLM,其他模式返回空列表
    (工具内部仍有 run_mode 校验作为兜底)。
    """
    if config is None or config.run_mode != RunMode.WEBUI:
        if config:
            config.record_unavailable_tools(
                [restart_agent.name],
                "仅 WebUI 模式下可用",
            )
        return []
    config.clear_unavailable_tools([restart_agent.name])
    return [restart_agent]


def get_all_tools() -> list:
    """获取所有重启工具(无条件返回,供 registry BM25 索引发现)。"""
    return [restart_agent]
