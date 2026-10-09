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
    if not task_description or not task_description.strip():
        return (
            f"{TOOL_ERROR}: task_description 不能为空(收到: {task_description!r});"
            f"请提供要推荐技能的任务描述"
        )
    root_dir = config.root_dir
    all_skills_list = load_skills(root_dir)
    if not all_skills_list:
        return "没有可用的技能。"

    total = len(all_skills_list)
    if total <= max_results:
        all_skills = "\n\n".join([skill_summary(skill) for skill in all_skills_list])
        return f"共 {total} 个可用技能,\n\n{all_skills}"

    try:
        skills = await _suggest_skills(
            task_description, all_skills_list, max_results, config
        )
    except (RuntimeError, OSError) as e:
        # LLM 推荐路径的预期失败(网络/服务不可用): 提示技能总数,让 LLM 改用 skill_read 按名查看
        return (
            f"{TOOL_ERROR}: 技能推荐模型调用失败(共 {total} 个可用技能): "
            f"{type(e).__name__}: {e}。可按名称调用 {skill_read.name} 查看技能详情。"
        )
    if not skills:
        return f"共 {total} 个可用技能,但没有与当前任务匹配的技能。"

    lines = [f"共 {total} 个可用技能,以下 {len(skills)} 个与当前任务相关:\n"]
    for skill in skills:
        lines.append(skill_summary(skill))
    return ("\n".join(lines))


async def _suggest_skills(
    task_description: str,
    all_skills: list[SkillDef],
    max_results: int,
    config,
) -> list[SkillDef]:
    """根据任务描述选择匹配的 skill。Jev 可用时优先使用,否则回退 LLM。"""
    if is_available():
        try:
            picked = await _suggest_via_jev(
                task_description,
                all_skills,
                max_results,
                proxy_url=config.proxy_url,
            )
            if picked:
                return picked
            # Jev 无命中(阈值过滤/空结果)不能直接定论"无匹配",
            # 回退 LLM 做语义推荐
        except (JevAPIError, JevConfigError) as e:
            from uniclaw.console.ui import err
            await err(f"Jev skill 推荐失败,回退 LLM: {e}", e=e)
    return await _suggest_via_llm(task_description, all_skills, max_results, config)


async def _suggest_via_jev(
    task_description: str,
    all_skills: list[SkillDef],
    max_results: int,
    proxy_url: str | None = None,
) -> list[SkillDef]:
    """通过 Jev select_many 选择匹配的 skill。"""
    by_name = {skill.name: skill for skill in all_skills}
    options = {name: skill_summary(skill) for name, skill in by_name.items()}
    results = await select_many(
        state=task_description,
        instruction="这个技能是否能帮助完成该任务。关注技能的描述、触发词和使用时机。",
        options=options,
        proxy_url=proxy_url,
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
以 JSON 对象返回(不要输出任何额外说明或文本),格式:
{{"skills": ["skill_name1", "skill_name2"]}}
skills 中必须是上方列出的确切技能名。
如果没有匹配的技能,返回 {{"skills": []}}。
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
    parsed = parse_json_from_llm(content)
    # 兼容 {"skills": [...]} / {"recommended": [...]} / 直接数组 / dict 内首个列表
    if isinstance(parsed, dict):
        skill_names = (
            parsed.get("skills")
            or parsed.get("recommended")
            or parsed.get("result")
            or next((v for v in parsed.values() if isinstance(v, list)), [])
        )
    elif isinstance(parsed, list):
        skill_names = parsed
    else:
        skill_names = []
    if not isinstance(skill_names, list):
        return []
    by_name = {skill.name: skill for skill in all_skills}
    by_lower = {name.lower(): skill for name, skill in by_name.items()}
    picked: list[SkillDef] = []
    seen: set[str] = set()
    for item in skill_names:
        name = str(item).strip().strip("\"'") if not isinstance(item, str) else item.strip()
        skill = by_name.get(name) or by_lower.get(name.lower())
        if skill and skill.name not in seen:
            seen.add(skill.name)
            picked.append(skill)
    return picked


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
        # 尝试忽略大小写匹配
        all_skills = load_skills(root_dir)
        lower_name = skill_name.strip().lower()
        ci_matches = [s for s in all_skills if s.name.lower() == lower_name]
        if len(ci_matches) == 1:
            skill = ci_matches[0]
            summary = skill_summary(skill)
            return (
                f"已按不区分大小写匹配到 '{skill.name}'(请求: '{skill_name}')。\n\n"
                f"{summary}\n\n{skill.prompt}"
            )
        # 未命中: 提示大小写敏感并列出相近名(忽略大小写匹配到的)
        similar = ", ".join(s.name for s in ci_matches) if ci_matches else ""
        available = ", ".join(s.name for s in all_skills[:20]) or "(无)"
        parts = [f"{TOOL_ERROR}: 未找到技能 '{skill_name}'。技能名区分大小写。"]
        if similar:
            parts.append(f"相近名(忽略大小写): {similar}")
        parts.append(f"当前可用: {available}")
        return "\n".join(parts)
    summary = skill_summary(skill)
    return f"{summary}\n\n{skill.prompt}"


def get_tools() -> list:
    """获取技能工具列表"""
    return [skill_suggest, skill_read]


def get_all_tools() -> list:
    """获取所有技能工具(无条件返回)"""
    return get_tools()
