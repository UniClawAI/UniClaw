from typing import Union

from uniclaw.agent import AgentTask
from uniclaw.config import AppConfig
from uniclaw.tools.skill.executor import run_skill
from uniclaw.commands.session import cmd_compact, cmd_clear, cmd_export
from uniclaw.commands.model import cmd_model
from uniclaw.commands.system import cmd_cwd, cmd_skills, cmd_exit, cmd_usage, cmd_help
from uniclaw.commands.context_usage import cmd_context
from uniclaw.commands.memory import cmd_memory
from uniclaw.commands.mcp import cmd_mcp
from uniclaw.commands.schedule import cmd_schedule
from uniclaw.commands.permissions import cmd_permissions
from uniclaw.commands.init import cmd_init
from uniclaw.commands.add_dir import cmd_add_dir
from uniclaw.commands.resume import cmd_resume
from uniclaw.commands.cost import cmd_cost
from uniclaw.commands.doctor import cmd_doctor
from uniclaw.commands.task import cmd_task
from uniclaw.commands.btw import cmd_btw
from uniclaw.commands.name import cmd_name
from uniclaw.commands.overseer import cmd_overseer
from uniclaw.commands.goal import cmd_goal
from uniclaw.commands.checkpoint import cmd_checkpoint
from uniclaw.commands.undo import cmd_undo
from uniclaw.commands.voice import cmd_voice
from uniclaw.commands.cu import cmd_cu
from uniclaw.commands.knowledge import cmd_knowledge
from uniclaw.commands.explain import cmd_explain

# 导入子命令列表
from uniclaw.commands import session as _session_mod
from uniclaw.commands import model as _model_mod
from uniclaw.commands import memory as _memory_mod
from uniclaw.commands import mcp as _mcp_mod
from uniclaw.commands import schedule as _schedule_mod
from uniclaw.commands import permissions as _permissions_mod
from uniclaw.commands import resume as _resume_mod
from uniclaw.commands import task as _task_mod
from uniclaw.commands import overseer as _overseer_mod
from uniclaw.commands import goal as _goal_mod
from uniclaw.commands import checkpoint as _checkpoint_mod
from uniclaw.commands import knowledge as _knowledge_mod

# 构建命令子命令映射表
COMMAND_SUBCOMMANDS = {}
_SUBCOMMAND_MODULES = {
    "export": _session_mod,
    "model": _model_mod,
    "memory": _memory_mod,
    "mcp": _mcp_mod,
    "schedule": _schedule_mod,
    "permissions": _permissions_mod,
    "resume": _resume_mod,
    "task": _task_mod,
    "overseer": _overseer_mod,
    "goal": _goal_mod,
    "checkpoint": _checkpoint_mod,
    "kg": _knowledge_mod,
}
for _cmd_name, _mod in _SUBCOMMAND_MODULES.items():
    if hasattr(_mod, "SUBCOMMANDS"):
        COMMAND_SUBCOMMANDS[_cmd_name] = _mod.SUBCOMMANDS

COMMANDS = dict()
COMMANDS["clear"] = cmd_clear
COMMANDS["cls"] = cmd_clear
COMMANDS["compact"] = cmd_compact
COMMANDS["model"] = cmd_model
COMMANDS["cwd"] = cmd_cwd
COMMANDS["pwd"] = cmd_cwd
COMMANDS["cd"] = cmd_cwd
COMMANDS["skills"] = cmd_skills
COMMANDS["exit"] = cmd_exit
COMMANDS["quit"] = cmd_exit
COMMANDS["export"] = cmd_export
COMMANDS["memory"] = cmd_memory
COMMANDS["mcp"] = cmd_mcp
COMMANDS["usage"] = cmd_usage
COMMANDS["context"] = cmd_context
COMMANDS["schedule"] = cmd_schedule
COMMANDS["permissions"] = cmd_permissions
COMMANDS["help"] = cmd_help
COMMANDS["init"] = cmd_init
COMMANDS["add_dir"] = cmd_add_dir
COMMANDS["add-dir"] = cmd_add_dir
COMMANDS["resume"] = cmd_resume
COMMANDS["cost"] = cmd_cost
COMMANDS["doctor"] = cmd_doctor
COMMANDS["task"] = cmd_task
COMMANDS["btw"] = cmd_btw
COMMANDS["name"] = cmd_name
COMMANDS["overseer"] = cmd_overseer
COMMANDS["goal"] = cmd_goal
COMMANDS["checkpoint"] = cmd_checkpoint
COMMANDS["cp"] = cmd_checkpoint
COMMANDS["undo"] = cmd_undo
COMMANDS["voice"] = cmd_voice
COMMANDS["cu"] = cmd_cu
COMMANDS["kg"] = cmd_knowledge
COMMANDS["knowledge"] = cmd_knowledge
COMMANDS["explain"] = cmd_explain


async def handle_slash(line: str, config: AppConfig) -> Union[bool, str]:
    """处理 /command [args]。如果已处理则返回True,技能匹配时返回元组(skill, args)。"""
    task = config.current_agent
    if not line.startswith("/"):
        return False
    parts = line[1:].split(None, 1)
    if not parts:
        return True
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    handler = COMMANDS.get(cmd)
    if handler:
        # /command help — 打印该命令的 docstring
        if args.strip().lower() == "help":
            doc = handler.__doc__
            if doc:
                from uniclaw.console.ui import info

                await info(f"\n/{cmd} — {doc.strip()}\n", config)
            else:
                from uniclaw.console.ui import warn

                await warn(f"/{cmd} 没有帮助文档", config)
            return True
        # 兼容同步和异步handler
        import inspect

        if inspect.iscoroutinefunction(handler):
            return await handler(args, config)
        else:
            return handler(args, config)

    # 回退到技能查找
    from uniclaw.tools.skill.loader import find_skill
    from uniclaw.tools.skill.tools import set_active_skill_tools

    skill = find_skill(task.session.root_dir, parts[0])
    if skill:
        cmd_parts = line.strip().split(maxsplit=1)
        skill_args = cmd_parts[1] if len(cmd_parts) > 1 else ""

        # 区分 prompt-based skill 和 command-based skill:
        # - 如果 skill 名称是 PATH 上的可执行文件,走 run_skill(bash 执行)
        # - 否则是 prompt-based skill,把 prompt 注入为用户消息让 LLM 读取
        import shutil

        if shutil.which(skill.name):
            # command-based skill:直接执行
            rendered = run_skill(skill, skill_args, config=config)
            return f"[skill: {skill.name}]\n\n{rendered}"
        else:
            # prompt-based skill:注入 prompt + 设置工具白名单
            if skill.tools:
                set_active_skill_tools(skill.tools)
            task = skill_args if skill_args else ""
            return f"[skill: {skill.name}] 请使用这个skill帮我完成以下任务: {task}"

    return False
