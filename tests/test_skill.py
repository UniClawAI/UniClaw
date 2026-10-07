from types import SimpleNamespace
from uniclaw.tools.base import ToolRuntime
from uniclaw.tools.skill.loader import find_skill, load_skills
from uniclaw.tools.skill.tools import skill_read, skill_suggest


def _make_config(tmp_path):
    return SimpleNamespace(
        root_dir=tmp_path,
        mini_model_name="test-mini",
        spinner=SimpleNamespace(start=lambda *a, **k: 1, stop=lambda **k: None),
    )


def test_load_skills_supports_common_project_dirs(tmp_path, monkeypatch):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    (skill_dir / "demo.md").write_text(
        """---
name: demo
description: Demo skill
triggers: [demo, do demo]
allowed-tools: [Read, Bash]
when-to-use: Testing
---
Run the demo task.
""",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    skills = load_skills(root_dir=tmp_path)
    demo = next(skill for skill in skills if skill.name == "demo")
    assert demo.source == "project"
    assert demo.triggers == ["demo", "do demo"]
    assert demo.tools == ["Read", "Bash"]
    assert demo.when_to_use == "Testing"
    assert find_skill(root_dir=tmp_path, query="do anything").name == "demo"


# ── skill_suggest 空描述校验 ──────────────────────────────


async def test_skill_suggest_empty_description(tmp_path):
    """task_description 为空时应报 TOOL_ERROR。"""
    config = _make_config(tmp_path)
    result = await skill_suggest.func(
        task_description="", tool_runtime=ToolRuntime(config=config)
    )
    assert "TOOL_ERROR" in result
    assert "不能为空" in result


async def test_skill_suggest_whitespace_description(tmp_path):
    """task_description 全空白时应报 TOOL_ERROR。"""
    config = _make_config(tmp_path)
    result = await skill_suggest.func(
        task_description="   ", tool_runtime=ToolRuntime(config=config)
    )
    assert "TOOL_ERROR" in result
    assert "不能为空" in result


# ── skill_read 大小写变体 ─────────────────────────────────


def _write_skill(tmp_path, name):
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir(exist_ok=True)
    (skill_dir / f"{name}.md").write_text(
        f"""---
name: {name}
description: Skill {name}
triggers: [{name}]
---
Prompt for {name}.
""",
        encoding="utf-8",
    )


def test_skill_read_case_insensitive_unique_match(tmp_path):
    """大小写变体唯一命中时返回该技能并注明已按不区分大小写匹配。"""
    _write_skill(tmp_path, "stealth-browser")
    config = _make_config(tmp_path)
    result = skill_read.func(
        skill_name="Stealth-Browser", tool_runtime=ToolRuntime(config=config)
    )
    assert "stealth-browser" in result
    assert "不区分大小写" in result
    assert "TOOL_ERROR" not in result


def test_skill_read_case_insensitive_hint(tmp_path):
    """未命中时提示大小写敏感并列出相近名。"""
    _write_skill(tmp_path, "my-skill")
    config = _make_config(tmp_path)
    result = skill_read.func(
        skill_name="nonexistent", tool_runtime=ToolRuntime(config=config)
    )
    assert "TOOL_ERROR" in result
    assert "区分大小写" in result


def test_skill_read_exact_match_unchanged(tmp_path):
    """精确命中时行为不变(不加不区分大小写提示)。"""
    _write_skill(tmp_path, "my-skill")
    config = _make_config(tmp_path)
    result = skill_read.func(
        skill_name="my-skill", tool_runtime=ToolRuntime(config=config)
    )
    assert "my-skill" in result
    assert "不区分大小写" not in result
    assert "TOOL_ERROR" not in result
