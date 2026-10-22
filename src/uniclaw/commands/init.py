"""项目初始化命令 - 扫描项目并生成/更新项目指令文件(AGENTS.md 或 CLAUDE.md)"""

from uniclaw.config import AppConfig
from uniclaw.console.ui import info
from uniclaw.tools.multi_agent.sub_agent import load_agent_definitions
from uniclaw.utils.project_instruction import _resolve_project_instruction


async def cmd_init(args: str, config: AppConfig) -> str:
    """扫描当前项目并生成/更新项目指令文件

    用法: /init

    功能:
        - 启动 project-init 子代理分析项目
        - 目标文件与加载优先级一致:AGENTS.md 优先,CLAUDE.md 只在无
          AGENTS.md 时被当作后备;两者都有时更新 AGENTS.md,都不存在
          时生成 AGENTS.md

    Args:
        args: 命令参数
        task: 当前代理任务对象
        config: 配置字典

    Returns:
        str: 返回给 LLM 的提示词,指示使用 project-init 子代理
    """

    task = config.current_agent
    root_dir = task.session.root_dir
    if root_dir is None:
        return "无法执行 /init: 当前会话未设置工作目录(root_dir 为 None)"
    project_name = root_dir.name

    # 指令文件目标:复用加载逻辑的优先级解析 — AGENTS.md 优先,CLAUDE.md 后备
    resolved = _resolve_project_instruction(root_dir)
    if resolved is not None:
        target_name = resolved.name
        target_path = resolved
    else:
        target_name = "AGENTS.md"
        target_path = root_dir / "AGENTS.md"

    await info(f"正在分析项目: {project_name}", config)

    # 获取子代理名称
    agent_defs = load_agent_definitions(task.session.root_dir)
    init_agent = agent_defs.get("project-init")
    agent_name = init_agent.name if init_agent else "project-init"

    # 获取 subagent_create 工具名称
    from uniclaw.tools.multi_agent.tools import subagent_create

    prompt = (
        f'请使用 {subagent_create.name} 工具,调用 subagent_type="{agent_name}",'
        f'name="init-{project_name}",wait=True,来完成以下任务:\n\n'
        f"分析当前项目并生成/更新 {target_name} 文件。\n\n"
        f"项目路径: {root_dir}\n"
        f"项目名称: {project_name}\n\n"
    )

    if target_path.exists():
        prompt += (
            f"现有 {target_name} 文件\n"
            "请在此基础上更新,保留有用的自定义内容,补充新发现的信息。\n"
        )
    else:
        prompt += f"当前没有 {target_name} 文件,请从头生成。\n"
    return prompt
