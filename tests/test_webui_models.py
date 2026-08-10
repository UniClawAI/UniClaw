"""WebUI Pydantic 模型测试 — 覆盖请求/响应模型的字段校验。"""

import pytest
from pydantic import ValidationError

from uniclaw.webui.models import (
    AsrRequest,
    CheckpointCreate,
    ConfigUpdate,
    GitCommit,
    GitStage,
    HookUpdate,
    MessageDelete,
    PermissionRuleDelete,
    ProviderConfig,
    SessionCreate,
    SessionRename,
    SettingsUpdate,
)


class TestSessionCreate:
    """SessionCreate 测试。"""

    def test_valid(self):
        """合法创建。"""
        s = SessionCreate(root_dir="/tmp", title="测试会话")
        assert s.root_dir == "/tmp"
        assert s.title == "测试会话"

    def test_title_default_empty(self):
        """标题默认为空。"""
        s = SessionCreate(root_dir="/tmp")
        assert s.title == ""

    def test_title_stripped(self):
        """标题去除首尾空格。"""
        s = SessionCreate(root_dir="/tmp", title="  会话  ")
        assert s.title == "会话"

    def test_title_too_long(self):
        """标题超过 200 字符报错。"""
        with pytest.raises(ValidationError):
            SessionCreate(root_dir="/tmp", title="a" * 201)

    def test_required_root_dir(self):
        """缺少 root_dir 报错。"""
        with pytest.raises(ValidationError):
            SessionCreate()


class TestSessionRename:
    """SessionRename 测试。"""

    def test_valid(self):
        """合法重命名。"""
        s = SessionRename(title="新标题")
        assert s.title == "新标题"

    def test_empty_title(self):
        """空标题报错。"""
        with pytest.raises(ValidationError):
            SessionRename(title="  ")

    def test_too_long(self):
        """标题超长报错。"""
        with pytest.raises(ValidationError):
            SessionRename(title="a" * 201)


class TestConfigUpdate:
    """ConfigUpdate 测试。"""

    def test_valid_defaults(self):
        """全部为空默认。"""
        c = ConfigUpdate(session_id="s1")
        assert c.permission_mode is None
        assert c.temperature is None
        assert c.max_tokens is None

    def test_valid_values(self):
        """合法值。"""
        c = ConfigUpdate(
            session_id="s1",
            permission_mode="auto",
            temperature=0.7,
            max_tokens=4096,
            computer_use_enabled=True,
        )
        assert c.temperature == 0.7
        assert c.max_tokens == 4096

    def test_temperature_out_of_range(self):
        """温度超出 0-2 报错。"""
        with pytest.raises(ValidationError):
            ConfigUpdate(session_id="s1", temperature=2.5)

    def test_temperature_negative(self):
        """温度负数报错。"""
        with pytest.raises(ValidationError):
            ConfigUpdate(session_id="s1", temperature=-0.1)

    def test_max_tokens_out_of_range(self):
        """max_tokens 越界报错。"""
        with pytest.raises(ValidationError):
            ConfigUpdate(session_id="s1", max_tokens=0)
        with pytest.raises(ValidationError):
            ConfigUpdate(session_id="s1", max_tokens=1000001)

    def test_permission_mode_invalid(self):
        """无效权限模式报错。"""
        with pytest.raises(ValidationError):
            ConfigUpdate(session_id="s1", permission_mode="bogus")

    def test_permission_mode_valid_list(self):
        """所有有效权限模式。"""
        for mode in ["auto", "manual", "accept-all", "plan"]:
            c = ConfigUpdate(session_id="s1", permission_mode=mode)
            assert c.permission_mode == mode


class TestCheckpointCreate:
    """CheckpointCreate 测试。"""

    def test_valid(self):
        """合法。"""
        c = CheckpointCreate(root_dir="/tmp", message="备份")
        assert c.message == "备份"

    def test_message_too_long(self):
        """消息超长报错。"""
        with pytest.raises(ValidationError):
            CheckpointCreate(root_dir="/tmp", message="a" * 501)


class TestGitCommit:
    """GitCommit 测试。"""

    def test_valid(self):
        """合法。"""
        c = GitCommit(root_dir="/tmp", message="feat: 添加功能")
        assert c.message == "feat: 添加功能"
        assert c.files == []

    def test_message_stripped(self):
        """消息去除首尾空格。"""
        c = GitCommit(root_dir="/tmp", message="  提交信息  ")
        assert c.message == "提交信息"

    def test_empty_message(self):
        """空消息报错。"""
        with pytest.raises(ValidationError):
            GitCommit(root_dir="/tmp", message="   ")

    def test_message_too_long(self):
        """消息超长报错。"""
        with pytest.raises(ValidationError):
            GitCommit(root_dir="/tmp", message="a" * 1001)

    def test_files_too_many(self):
        """文件数量过多报错。"""
        with pytest.raises(ValidationError):
            GitCommit(root_dir="/tmp", message="x", files=["a"] * 1001)


class TestGitStage:
    """GitStage 测试。"""

    def test_valid(self):
        """合法。"""
        s = GitStage(root_dir="/tmp", files=["a.py", "b.py"])
        assert len(s.files) == 2

    def test_empty_files(self):
        """空文件列表报错。"""
        with pytest.raises(ValidationError):
            GitStage(root_dir="/tmp", files=[])

    def test_files_too_many(self):
        """文件数量过多报错。"""
        with pytest.raises(ValidationError):
            GitStage(root_dir="/tmp", files=["a"] * 1001)


class TestPermissionRuleDelete:
    """PermissionRuleDelete 测试。"""

    def test_valid_types(self):
        """合法规则类型。"""
        for t in ["tool", "path", "command"]:
            p = PermissionRuleDelete(root_dir="/tmp", rule_type=t, pattern="Read")
            assert p.rule_type == t

    def test_invalid_type(self):
        """无效规则类型报错。"""
        with pytest.raises(ValidationError):
            PermissionRuleDelete(root_dir="/tmp", rule_type="bogus", pattern="Read")


class TestHookUpdate:
    """HookUpdate 测试。"""

    def test_valid(self):
        """合法。"""
        h = HookUpdate(root_dir="/tmp", hooks={"on_session_start": "echo hi"})
        assert h.hooks["on_session_start"] == "echo hi"

    def test_hooks_too_large(self):
        """hooks 配置过大报错。"""
        with pytest.raises(ValidationError):
            HookUpdate(root_dir="/tmp", hooks={"big": "a" * 100001})


class TestMessageDelete:
    """MessageDelete 测试。"""

    def test_defaults(self):
        """默认值。"""
        m = MessageDelete()
        assert m.source == "messages"
        assert m.from_idx == -1

    def test_custom(self):
        """自定义值。"""
        m = MessageDelete(source="history", from_idx=3)
        assert m.source == "history"
        assert m.from_idx == 3


class TestAsrRequest:
    """AsrRequest 测试。"""

    def test_valid(self):
        """合法。"""
        a = AsrRequest(audio="base64data")
        assert a.format == "webm"

    def test_missing_audio(self):
        """缺少 audio 报错。"""
        with pytest.raises(ValidationError):
            AsrRequest()


class TestProviderConfig:
    """ProviderConfig 测试。"""

    def test_valid_protocols(self):
        """合法协议。"""
        for p in ["openai", "anthropic"]:
            c = ProviderConfig(name="x", protocol=p)
            assert c.protocol == p

    def test_invalid_protocol(self):
        """无效协议报错。"""
        with pytest.raises(ValidationError):
            ProviderConfig(name="x", protocol="bogus")


class TestSettingsUpdate:
    """SettingsUpdate 测试。"""

    def test_defaults(self):
        """默认值。"""
        s = SettingsUpdate()
        assert s.max_agent_depth == 2
        assert s.permission_timeout == 300
        assert s.permission_mode == "auto"
        assert s.model_name == []

    def test_valid(self):
        """合法值。"""
        s = SettingsUpdate(model_name=["gpt-4o"], temperature=0.5, max_tokens=8192)
        assert s.model_name == ["gpt-4o"]
        assert s.temperature == 0.5

    def test_temperature_out_of_range(self):
        """温度越界报错。"""
        with pytest.raises(ValidationError):
            SettingsUpdate(temperature=2.5)

    def test_max_tokens_out_of_range(self):
        """max_tokens 越界报错。"""
        with pytest.raises(ValidationError):
            SettingsUpdate(max_tokens=0)

    def test_max_agent_depth_out_of_range(self):
        """max_agent_depth 越界报错。"""
        with pytest.raises(ValidationError):
            SettingsUpdate(max_agent_depth=0)
        with pytest.raises(ValidationError):
            SettingsUpdate(max_agent_depth=21)

    def test_permission_timeout_out_of_range(self):
        """permission_timeout 越界报错。"""
        with pytest.raises(ValidationError):
            SettingsUpdate(permission_timeout=0)
        with pytest.raises(ValidationError):
            SettingsUpdate(permission_timeout=3601)

    def test_permission_mode_invalid(self):
        """无效权限模式报错。"""
        with pytest.raises(ValidationError):
            SettingsUpdate(permission_mode="bogus")

    def test_providers_nested(self):
        """嵌套 ProviderConfig。"""
        s = SettingsUpdate(providers={"openai": ProviderConfig(name="o", api_key="k")})
        assert s.providers["openai"].api_key == "k"
