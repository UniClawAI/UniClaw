from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uniclaw.utils.frontmatter import parse_frontmatter


@dataclass
class SkillDef:
    name: str
    description: str
    triggers: list[str]  # ["/commit", "提交更改"]
    tools: list[str]  # ["Bash", "Read"]  (允许使用的工具)
    prompt: str  # 前置元数据后的完整提示词正文
    file_path: str
    # 增强字段
    when_to_use: str = ""  # Claude 应自动调用此技能的时机
    argument_hint: str = ""  # 例如 "[分支] [描述]"
    arguments: list[str] = field(default_factory=list)  # 命名参数名称列表
    model: str = ""  # 模型覆盖设置
    user_invocable: bool = True  # 是否出现在 /skills 列表中
    context: str = "inline"  # "inline"(内联)或 "fork"(子智能体)
    source: str = "user"  # "user"(用户)、"project"(项目)、"builtin"(内置)


def _get_skill_paths(root_dir: Path | None) -> dict[str, list[Path]]:
    skill_paths = {
        "user": [
            Path.home() / ".claude" / "skills",
            Path.home() / ".codex" / "skills",
            Path.home() / ".agents" / "skills",
            *Path.home().glob(".*/skills"),
        ],
    }
    if root_dir:
        skill_paths["project"] = [
            root_dir / "skills",
            root_dir / ".claude" / "skills",
            root_dir / ".codex" / "skills",
            root_dir / ".agents" / "skills",
            *root_dir.glob(".*/skills"),
        ]
    return skill_paths


def _iter_skill_files(skill_dir: Path):
    """生成在 `skill_dir` 中找到的技能 Markdown 文件。

    支持两种布局:
    - 扁平式:<skill_dir>/<name>.md
    - 嵌套式:<skill_dir>/<name>/skill.md
    """
    if not skill_dir.is_dir():
        return
    yield from sorted(skill_dir.glob("*.md"))
    for child in sorted(skill_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        for candidate in (child / "skill.md", child / "SKILL.md"):
            if candidate.exists():
                yield candidate
                break


def _parse_list_field(value: str) -> list[str]:
    """解析类 YAML 列表:``[a, b, c]`` 或 ``"a, b, c"``。"""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [
        item.strip().strip('"').strip("'") for item in value.split(",") if item.strip()
    ]


def _coerce_list_field(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return _parse_list_field(value)
    return [str(value).strip()] if str(value).strip() else []


def _parse_skill_file(path: Path, source: str = "user") -> Optional[SkillDef]:
    """将带有 ``---`` 前置元数据的 Markdown 文件解析为 SkillDef。

    前置元数据字段:
        name, description, triggers, tools / allowed-tools,
        when_to_use, argument-hint, arguments, model,
        user-invocable, context
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    metadata, prompt = parse_frontmatter(text)

    name = metadata.get("name", "")
    if not name:
        return None

    # 如果存在,allowed-tools 优先于 tools
    tools = _coerce_list_field(metadata.get("allowed-tools", metadata.get("tools", [])))

    triggers = metadata.get("triggers", [f"/{name}"])
    triggers = _coerce_list_field(triggers)
    if not triggers:
        triggers = [f"/{name}"]

    arguments = _coerce_list_field(metadata.get("arguments", []))

    user_invocable = metadata.get("user-invocable", "true")
    if isinstance(user_invocable, str):
        user_invocable = user_invocable.lower() not in ("false", "0", "no")

    context = str(metadata.get("context", "inline")).strip().lower()
    if context not in ("inline", "fork"):
        context = "inline"

    return SkillDef(
        name=name,
        description=metadata.get("description", ""),
        triggers=triggers,
        tools=tools,
        prompt=prompt,
        file_path=str(path),
        when_to_use=metadata.get("when_to_use", metadata.get("when-to-use", "")),
        argument_hint=metadata.get("argument-hint", ""),
        arguments=arguments,
        model=metadata.get("model", ""),
        user_invocable=user_invocable,
        context=context,
        source=source,
    )


# ── 内置技能注册表(由 builtin.py 注册) ────────────────

_BUILTIN_SKILLS: list[SkillDef] = []


def register_builtin(skill: SkillDef) -> None:
    _BUILTIN_SKILLS.append(skill)


def get_builtin_skills() -> list[SkillDef]:
    return list(_BUILTIN_SKILLS)


def load_skills(root_dir: Path | None) -> list[SkillDef]:
    skills: list[SkillDef] = []
    skill_keys = set()
    # 加载内置技能
    skills.extend(get_builtin_skills())

    # 加载用户和项目技能
    paths = _get_skill_paths(root_dir)
    for source, dirs in paths.items():
        for skill_dir in dirs:
            for file_path in _iter_skill_files(skill_dir):
                skill_name = file_path.parent.name
                if skill_name in skill_keys:
                    continue
                skill = _parse_skill_file(file_path, source=source)
                if skill:
                    skills.append(skill)
                    skill_keys.add(skill.name)

    return skills


def find_skill(root_dir: Path | None, query: str) -> Optional[SkillDef]:
    """查找触发器与查询的第一个单词(或整个字符串)匹配的技能。"""
    query = query.strip()
    if not query:
        return None

    first_word = query.split()[0]
    for skill in load_skills(root_dir):
        if first_word == skill.name:
            return skill
        for trigger in skill.triggers:
            if first_word.lstrip("/") == trigger.lstrip("/"):
                return skill
            if trigger.startswith(first_word + " "):
                return skill
    return None


# ── 参数替换 ─────────────────────────────────────────────────────────


def substitute_arguments(prompt: str, args: str, arg_names: list[str]) -> str:
    """替换 $ARGUMENTS(完整参数字符串)和 $ARG_NAME 占位符。

    命名参数按位置对应:第一个单词 → 第一个名称,依此类推。
    """
    # 始终替换 $ARGUMENTS
    result = prompt.replace("$ARGUMENTS", args)

    # 命名参数:按空白字符分割
    arg_values = args.split()
    for i, arg_name in enumerate(arg_names):
        placeholder = f"${arg_name.upper()}"
        value = arg_values[i] if i < len(arg_values) else ""
        result = result.replace(placeholder, value)

    return result
