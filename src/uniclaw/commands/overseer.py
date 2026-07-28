from uniclaw.config import AppConfig
from uniclaw.console.ui import info, ok, warn

# 子命令列表
SUBCOMMANDS = ["start", "stop"]


async def cmd_overseer(args: str, config: AppConfig) -> bool:
    """启动或退出监工模式

    监工模式下:
    - TodoList 每完成一项必须通过子代理审核验收
    - Agent 试图休息时,如有未完成任务会被督促继续

    用法:
      /overseer start  - 启动监工模式
      /overseer stop   - 退出监工模式
      /overseer        - 查看当前状态
    """
    todo = config.current_agent.todolist
    if todo is None:
        await warn("当前任务没有 TodoList(仅 root 任务支持)", config)
        return True

    manager = todo.overseer
    arg = args.strip().lower()

    if arg == "start":
        if manager.active:
            await warn("监工模式已在运行中", config)
        else:
            manager.start()
            await ok(
                "监工模式已启动: TodoList 完成需审核验收,未完成任务会被督促", config
            )
        return True

    if arg == "stop":
        if not manager.active:
            await warn("监工模式未在运行", config)
        else:
            manager.stop()
            await ok("监工模式已退出", config)
        return True

    # 无参数:显示状态
    if manager.active:
        await info(
            "监工模式: 运行中\n"
            "  - TodoList 完成需审核验收\n"
            "  - 未完成任务会被督促继续",
            config,
        )
    else:
        await info("监工模式: 未运行", config)
        await info("  使用 /overseer start 启动", config)
    return True
