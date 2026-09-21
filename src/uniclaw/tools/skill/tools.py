from pathlib import Path
from uniclaw.tools.base import tool, ToolRuntime
from uniclaw.utils.constants import TOOL_ERROR
from uniclaw.context import APP_NAME
from uniclaw.provider.fallback import achat
from uniclaw.utils.format import parse_json_from_llm
from uniclaw.utils.jev import is_available, select_many, JevAPIError, JevConfigError
from .loader import SkillDef, load_skills, find_skill

# ── 活跃 skill 工具白名单 ─────────────────────────────────────
# 当 skill 被调用时,其 tools 字段中的工具名会被加入此集合,
# _check_permission 会据此自动放行这些工具。
_active_skill_tools: set[str] = set()


def get_active_skill_tools() -> set[str]:
    """获取当前活跃 skill 允许的工具集合。"""
    return _active_skill_tools


def set_active_skill_tools(tools: list[str]) -> None:
    """设置活跃 skill 的工具白名单。"""
    _active_skill_tools.clear()
    _active_skill_tools.update(tools)


def clear_active_skill_tools() -> None:
    """清除活跃 skill 的工具白名单。"""
    _active_skill_tools.clear()


def get_skill_system_prompt(root_dir: Path | None) -> str:
    skills = load_skills(root_dir)
    if not skills:
        return ""
    skill_names = "\n".join(skill.name for skill in skills)
    dir_info = f"用户级 ~/.{APP_NAME}/skills/"
    if root_dir:
        dir_info += f", 项目级 {root_dir}/.{APP_NAME}/skills/"
    return f"""
如果用户的请求无法直接完成,可以先使用 {skill_suggest.name} 查看可用技能,寻找可帮助解决问题的 skill,而不是立刻拒绝。
如果某个skill有用,在执行前调用 {skill_read.name} 加载其完整的 skill.md。
按照技能说明操作。如果技能需要 CLI 命令或脚本,直接使用 Bash 工具在技能目录下执行。
你已掌握的技能包括:
{skill_names}。通过 {skill_read.name} 获取技能详情。
技能目录: {dir_info}"""


def skill_summary(skill: SkillDef) -> str:
    """获取技能的摘要信息。

    Args:
        skill: 技能定义对象

    Returns:
        str: 技能摘要信息字符串
    """
    triggers = ", ".join(skill.triggers)
    hint = f"参数: {skill.argument_hint}\n" if skill.argument_hint else ""
    when = f"使用时机: {skill.when_to_use}\n" if skill.when_to_use else ""
    tools = f"允许工具: {', '.join(skill.tools)}\n" if skill.tools else ""
    return f"- **{skill.name}** [{triggers}] {hint}{when}{tools}{skill.description}\n路径:{Path(skill.file_path).parent}"


@tool
async def skill_suggest(
    task_description: str, max_results: int = 10, tool_runtime: ToolRuntime = None
) -> str:
    """获取可用技能的列表信息。
    根据任务描述推荐可用skill,返回技能名称和简介。

    Args:
        task_description: 用户输入的任务描述文本
        max_results: 返回的最大技能数量

    Returns:
        格式化的字符串,包含匹配到的技能名称和简介；若无直接匹配,则返回若干技能的简介作为备选。
    """
    config = tool_runtime.config
    root_dir = config.root_dir
    all_skills_list = load_skills(root_dir)
    if not all_skills_list:
        return "没有可用的技能。"

    total = len(all_skills_list)
    if total <= max_results:
        all_skills = "\n\n".join([skill_summary(skill) for skill in all_skills_list])
        return f"共 {total} 个可用技能,\n\n{all_skills}"

    skills = await _suggest_skills(task_description, all_skills_list, max_results, config)
    if not skills:
        return f"共 {total} 个可用技能,但没有与当前任务匹配的技能。"

    lines = [f"共 {total} 个可用技能,以下 {len(skills)} 个与当前任务相关:\n"]
    for skill in skills:
        lines.append(skill_summary(skill))
    return "\n".join(lines)


async def _suggest_skills(
    task_description: str,
    all_skills: list[SkillDef],
    max_results: int,
    config,
) -> list[SkillDef]:
    """根据任务描述选择匹配的 skill。Jev 可用时优先使用,否则回退 LLM。"""
    if is_available():
        try:
            return await _suggest_via_jev(task_description, all_skills, max_results)
        except (JevAPIError, JevConfigError) as e:
            from uniclaw.console.ui import err
            await err(f"Jev skill 推荐失败,回退 LLM: {e}")
        except Exception as e:
            from uniclaw.console.ui import err
            await err(f"Jev skill 推荐异常,回退 LLM: {e}")
    return await _suggest_via_llm(task_description, all_skills, max_results, config)


async def _suggest_via_jev(
    task_description: str,
    all_skills: list[SkillDef],
    max_results: int,
) -> list[SkillDef]:
    """通过 Jev select_many 选择匹配的 skill。"""
    by_name = {skill.name: skill for skill in all_skills}
    options = {name: skill_summary(skill) for name, skill in by_name.items()}
    results = await select_many(
        state=task_description,
        instruction="这个技能是否能帮助完成该任务。关注技能的描述、触发词和使用时机。",
        options=options,
    )
    # noul > 0.5 视为选中,按 noul 降序取 top max_results
    matched = [
        (name, noul_result.noul)
        for name, noul_result in results.items()
        if noul_result.noul > 0.5 and name in by_name
    ]
    matched.sort(key=lambda x: x[1], reverse=True)
    return [by_name[name] for name, _ in matched[:max_results]]


async def _suggest_via_llm(
    task_description: str,
    all_skills: list[SkillDef],
    max_results: int,
    config,
) -> list[SkillDef]:
    """通过 LLM 选择匹配的 skill(原有方案)。"""
    all_skills_text = "\n\n".join([skill_summary(skill) for skill in all_skills])
    system_prompt = f"""
你是skill顾问,一个skill推荐系统。
共有 {len(all_skills)} 个可用技能,已掌握以下全部技能:

{all_skills_text}

请根据用户的任务描述,
从中推荐最多 {max_results} 个最适合的技能。
只返回符合要求的技能名称列表,
并使用 JSON 数组格式,例如:
["skill_name1", "skill_name2"]
不要输出任何额外说明或文本。
如果没有匹配的技能,直接返回 []。
"""
    from uniclaw.tools.session.session import Session

    _session = Session()
    _session.add_user_message(content=task_description)
    wait_id = config.spinner.start("推荐技能...")
    try:
        resp = await achat(
            system_prompt,
            _session,
            model_name=config.mini_model_name,
            enable_thinking=False,
            thinking=False,
            response_format={"type": "json_object"},
            config=config,
        )
        content = resp.content
    finally:
        config.spinner.stop(wait_id=wait_id)
    skill_names = parse_json_from_llm(content)
    if not isinstance(skill_names, list):
        return []
    by_name = {skill.name: skill for skill in all_skills}
    return [by_name[name] for name in skill_names if name in by_name]


# @tool
def skill_list(
    skill_name: str | None = None, tool_runtime: ToolRuntime = None
) -> str:
    """获取可用技能的列表信息。

    该函数加载所有已定义的技能,并根据提供的技能名称进行过滤,
    最后返回格式化的技能列表信息,包括技能名称、触发词、参数提示、
    描述和使用时机等详细信息。

    Args:
        skill_name: 可选的技能名称。如果提供,则只返回匹配的技能；
                   如果不提供,则返回所有可用技能

    Returns:
        str: 格式化的技能列表字符串。可能的返回值包括:
             - 包含技能详细信息的格式化列表(技能名称、触发词、参数、描述、使用时机)
             - 如果没有可用技能或没有匹配的技能,返回"没有可用的技能。"
    """
    config = tool_runtime.config
    root_dir = config.root_dir
    skills = load_skills(root_dir)

    # 根据提供的技能名称过滤技能列表
    if skill_name:
        skills = [s for s in skills if s.name == skill_name]

    if not skills:
        return "没有可用的技能。"

    # 构建格式化的技能列表输出
    lines = ["可用技能:\n"]
    for skill in skills:
        lines.append(skill_summary(skill))

    return "\n".join(lines)


@tool
def skill_read(skill_name: str, tool_runtime: ToolRuntime = None) -> str:
    """读取指定技能的详细信息。

    该函数根据提供的技能名称查找对应的技能,并返回该技能的详细信息,
    包括技能名称、触发词、参数提示、描述和使用时机等。如果未找到技能,则返回错误信息。

    Args:
        skill_name: 要查询的技能名称,用于查找和匹配对应的技能定义
    Returns:
        str: 技能的详细信息字符串。如果技能存在,返回包含技能名称、触发词、参数提示、描述和使用时机的格式化字符串；
             如果未找到技能,返回错误信息提示。
    """
    config = tool_runtime.config
    root_dir = config.root_dir
    skill = find_skill(root_dir, skill_name)

    if skill is None:
        return f"{TOOL_ERROR}: 未找到技能 '{skill_name}'。"
    summary = skill_summary(skill)
    return f"{summary}\n\n{skill.prompt}"


def get_tools() -> list:
    """获取技能工具列表"""
    return [skill_suggest, skill_read]


def get_all_tools() -> list:
    """获取所有技能工具(无条件返回)"""
    return get_tools()
