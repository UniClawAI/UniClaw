from uniclaw.tools.skill.loader import find_skill, load_skills


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
