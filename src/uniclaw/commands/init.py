"""项目初始化命令 - 扫描项目并生成/更新 CLAUDE.md"""

from pathlib import Path

from uniclaw.config import AppConfig
from uniclaw.console.ui import info, warn
from uniclaw.tools.fs import Glob, Read
from uniclaw.tools.multi_agent.sub_agent import load_agent_definitions
from uniclaw.tools.shell import Grep


async def cmd_init(args: str, config: AppConfig) -> str:
    """扫描当前项目并生成/更新 CLAUDE.md

    用法: /init

    功能:
        - 启动 project-init 子代理分析项目
        - 自动生成 CLAUDE.md 文件

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
    claude_md_path = root_dir / "CLAUDE.md"

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
        f"分析当前项目并生成/更新 CLAUDE.md 文件。\n\n"
        f"项目路径: {root_dir}\n"
        f"项目名称: {project_name}\n\n"
    )

    if claude_md_path.exists():
        prompt += (
            f"现有 CLAUDE.md 文件\n"
            "请在此基础上更新,保留有用的自定义内容,补充新发现的信息。\n"
        )
    else:
        prompt += "当前没有 CLAUDE.md 文件,请从头生成。\n"
    return prompt
