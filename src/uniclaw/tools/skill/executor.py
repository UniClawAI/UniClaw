from pathlib import Path
import shlex
import shutil
from uniclaw.config import AppConfig
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.tools.shell import Bash
from uniclaw.tools.skill.loader import SkillDef, find_skill


def normalize_skill_command(skill: SkillDef, command: str) -> str:
    """使常见的技能命令代码片段在 PowerShell 中可执行。"""
    stripped = command.strip()
    if not stripped:
        raise ValueError("命令不能为空")

    first_token = shlex.split(stripped, posix=False)[0].strip("'\"")
    if first_token == skill.name:
        return stripped

    if shutil.which(skill.name):
        return f"{skill.name} {stripped}"

    return stripped


async def _run_command(
    command: str, cwd: Path, config: AppConfig, timeout: int = 30
) -> str:
    """在指定目录下执行命令,并返回输出结果。"""
    from uniclaw.tools.session.session import Session

    # 创建子配置,修改 root_dir 为 cwd,避免并发时修改共享对象
    sub_config = config.create_sub_config(name=config.current_agent.name, prompt="")
    sub_config.current_agent.session = Session(root_dir=cwd)
    return await Bash(command, timeout=timeout, config=sub_config)


async def run_skill(skill_name: str, command: str, config: AppConfig | None = None) -> str:
    root_dir = config.root_dir
    skill = find_skill(root_dir, skill_name)

    if skill is None:
        return f"{TOOL_ERROR}: 未找到技能 '{skill_name}'。"

    return await _run_command(
        normalize_skill_command(skill, command),
        cwd=Path(skill.file_path).parent,
        config=config,
    )
