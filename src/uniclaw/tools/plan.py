from enum import StrEnum

from uniclaw.tools.base import tool
from uniclaw.config import AppConfig, Permissions
from pathlib import Path
from uniclaw.context import get_app_dir
from uniclaw.tools.shell import Bash
from uniclaw.tools.fs import Write, Edit
from uniclaw.tools.ask import AskUserQuestion
from uniclaw.tools.send_file import send_file


class ExitPermission(StrEnum):
    """退出计划模式时可选的权限模式。"""

    AUTO = "auto"
    ACCEPT_ALL = "accept-all"


def get_plans_dir(config: AppConfig) -> Path:
    """获取项目级计划目录,root_dir 为 None 时使用用户级目录。"""
    from uniclaw.context import Scope

    root = config.root_dir
    return get_app_dir(root) / "plans" if root else get_app_dir(Scope.USER) / "plans"


def get_plan_mode_instructions(config: AppConfig) -> str:
    """获取计划模式完整提示(用于 enter_plan_mode 和系统提示)"""
    plans_dir = get_plans_dir(config)
    if config.is_console:
        open_step = f"2. 使用 {Bash.name} 工具异步打开计划书(timeout<=0)供用户审阅,必须用系统默认GUI编辑器打开(Windows: start, macOS: open, Linux: xdg-open)\n"
    else:
        open_step = f"2. 使用 {send_file.name} 工具将计划书发送给用户审阅\n"

    return (
        f"\n\n将计划方案写入 {plans_dir/'*.md'} 文件。"
        f"\n\n## 计划审核流程(必须严格遵守)\n"
        f"1. 使用 {Write.name} 工具将计划写入上述目录\n"
        f"{open_step}"
        f"3. 使用 {AskUserQuestion.name} 工具询问用户是否同意计划,问题中必须包含计划书的绝对路径,以防文件打开失败时用户无法看到内容。"
        f"选项必须包含:接受并以自动权限执行 / 接受并以完全权限执行,可根据需要添加其他选项\n"
        f"4. 除非明确表达了接受意愿,否则一律视为不同意,应使用 {Edit.name} 工具根据用户的反馈修改计划书,然后重复步骤 2-3\n"
        f"5. 用户同意后,根据用户选择的权限模式调用 {exit_plan_mode.name}(permission_mode 参数)退出计划模式\n"
        f"\n警告:未经用户明确确认不得退出计划模式!"
    )


def get_plan_system_prompt(config: AppConfig) -> str:
    """获取计划模式的系统提示,非计划模式返回简介"""
    if config.current_agent.name != "root":
        return ""
    if config.permission_mode != Permissions.PLAN:
        return (
            "\n\n# 计划模式"
            "\n当遇到复杂任务、需要修改多个文件、或不确定方案时,"
            f"应先调用 {enter_plan_mode.name} 进入计划模式,制定方案并经用户确认后再执行。"
        )
    return (
        f"\n\n# 计划模式"
        f"\n你当前处于计划模式(PLAN)。"
        f"\n请专注于分析和规划,先了解代码结构再提出方案。"
        f"{get_plan_mode_instructions(config)}"
    )


@tool
def enter_plan_mode(config: AppConfig = None) -> str:
    """
    进入计划模式。适用于复杂任务、多文件修改或方案不确定的场景。
    进入后需制定计划并经用户确认,确认后调用 exit_plan_mode 退出并执行。
    """
    config.permission_mode = Permissions.PLAN
    return f"已进入计划模式。{get_plan_mode_instructions(config)}"


@tool
def exit_plan_mode(
    permission_mode: ExitPermission = ExitPermission.AUTO, config: AppConfig = None
) -> str:
    """
    退出计划模式,恢复到指定的权限模式。
    调用前必须已完成完整审核流程:打开计划书供用户审阅 → 使用 AskUserQuestion 工具获得用户明确同意。
    未经用户确认不得调用此工具！

    Args:
        permission_mode: 退出计划模式后的权限模式。可选值: "auto"(自动模式) 或 "accept-all"(完全模式)。默认为 "auto"。
    """
    config.permission_mode = permission_mode
    return f"已退出计划模式。权限模式已切换为 {permission_mode}。现在可以开始执行计划。"


def get_tools() -> list:
    """获取计划模式工具列表"""
    return [enter_plan_mode, exit_plan_mode]


def get_all_tools() -> list:
    """获取所有计划模式工具(无条件返回)"""
    return get_tools()
