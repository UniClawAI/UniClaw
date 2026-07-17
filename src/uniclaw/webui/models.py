"""WebUI Pydantic 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SessionCreate(BaseModel):
    """创建会话。"""

    root_dir: str
    title: str = ""

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        """验证标题长度。"""
        if len(v) > 200:
            raise ValueError("标题过长(最大 200 字符)")
        return v.strip()


class SessionRename(BaseModel):
    """会话重命名。"""

    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        """验证标题。"""
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空")
        if len(v) > 200:
            raise ValueError("标题过长(最大 200 字符)")
        return v


class SessionMove(BaseModel):
    """移动会话到其他项目。"""

    root_dir: str


class ConfigUpdate(BaseModel):
    """配置更新(模型切换请使用 /model 命令)。"""

    session_id: str
    permission_mode: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    computer_use_enabled: bool | None = None

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float | None) -> float | None:
        """验证温度参数。"""
        if v is not None and (v < 0 or v > 2):
            raise ValueError("温度必须在 0-2 之间")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int | None) -> int | None:
        """验证最大 token 数。"""
        if v is not None and (v < 1 or v > 1000000):
            raise ValueError("max_tokens 必须在 1-1000000 之间")
        return v

    @field_validator("permission_mode")
    @classmethod
    def validate_permission_mode(cls, v: str | None) -> str | None:
        """验证权限模式。"""
        if v is not None:
            valid_modes = ["auto", "manual", "accept-all", "plan"]
            if v not in valid_modes:
                raise ValueError(f'无效的权限模式,可选: {", ".join(valid_modes)}')
        return v


class CheckpointCreate(BaseModel):
    """创建 checkpoint。"""

    root_dir: str
    message: str = ""

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        """验证消息长度。"""
        if len(v) > 500:
            raise ValueError("消息过长(最大 500 字符)")
        return v


class CheckpointRestore(BaseModel):
    """恢复 checkpoint。"""

    root_dir: str


class GitCommit(BaseModel):
    """Git 提交。"""

    root_dir: str
    message: str
    files: list[str] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        """验证提交信息。"""
        v = v.strip()
        if not v:
            raise ValueError("提交信息不能为空")
        if len(v) > 1000:
            raise ValueError("提交信息过长")
        return v

    @field_validator("files")
    @classmethod
    def validate_files(cls, v: list) -> list:
        """验证文件列表。"""
        if len(v) > 1000:
            raise ValueError("文件数量过多")
        return v


class GitStage(BaseModel):
    """Git 暂存。"""

    root_dir: str
    files: list[str]

    @field_validator("files")
    @classmethod
    def validate_files(cls, v: list) -> list:
        """验证文件列表。"""
        if not v:
            raise ValueError("文件列表不能为空")
        if len(v) > 1000:
            raise ValueError("文件数量过多")
        return v


class GitAiCommitMessage(BaseModel):
    """AI 生成 commit message。"""

    root_dir: str


class PermissionRuleDelete(BaseModel):
    """删除权限规则。"""

    root_dir: str
    rule_type: str
    pattern: str

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str) -> str:
        """验证规则类型。"""
        valid_types = ["tool", "path", "command"]
        if v not in valid_types:
            raise ValueError(f'无效的规则类型,可选: {", ".join(valid_types)}')
        return v


class WechatBotCreate(BaseModel):
    """创建微信 Bot。"""

    name: str = Field(default="", description="Bot 名称,留空自动生成")


class SubAgentCreate(BaseModel):
    """创建子代理。"""

    prompt: str
    subagent_type: str = "general-purpose"
    name: str = ""
    root_dir: str | None = None


class HookUpdate(BaseModel):
    """更新 hooks 配置。"""

    root_dir: str
    hooks: dict

    @field_validator("hooks")
    @classmethod
    def validate_hooks(cls, v: dict) -> dict:
        """验证 hooks 配置大小。"""
        import json

        if len(json.dumps(v)) > 100000:  # 100KB
            raise ValueError("hooks 配置过大")
        return v


class MessageDelete(BaseModel):
    """删除消息(从指定索引到末尾)。"""

    source: str = Field(
        default="messages",
        description="消息来源: 'messages'(_messages) 或 'history'(完整历史)",
    )
    from_idx: int = Field(
        default=-1,
        description="起始索引(含)。后端自动计算 count = len - from_idx。-1 表示删除全部。",
    )


class AsrRequest(BaseModel):
    """语音识别请求。"""

    audio: str = Field(description="Base64 编码的音频数据")
    format: str = Field(default="webm", description="音频格式: webm/wav/mp3")


class AsrStreamRequest(BaseModel):
    """免提语音请求(音频 → ASR → 过滤 → 直接发送给 Agent)。"""

    audio: str = Field(description="Base64 编码的音频数据")
    format: str = Field(default="wav", description="音频格式")
    session_id: str = Field(description="目标会话 ID")


class PromptOptimize(BaseModel):
    """优化提示词。"""

    prompt: str
    type: str = "system"  # "system" | "user"


class ProviderConfig(BaseModel):
    """单个 Provider 配置。"""

    name: str = ""
    protocol: str = "openai"
    api_key: str = ""
    base_url: str = ""
    proxy_url: str = ""

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v: str) -> str:
        if v not in ("openai", "anthropic"):
            raise ValueError('协议必须是 "openai" 或 "anthropic"')
        return v


class SettingsUpdate(BaseModel):
    """全局 settings.json 更新。"""

    model_name: list[str] = Field(default_factory=list)
    mini_model_name: list[str] = Field(default_factory=list)
    multimodal_model_name: list[str] = Field(default_factory=list)
    large_model_name: list[str] = Field(default_factory=list)
    tts_model: str = ""
    asr_model: str = ""
    audio: dict | None = None  # TTS 音频配置 (voice, format, speed 等,自由键值)
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    proxy_url: str = ""
    GITHUB_TOKEN: str = ""
    EXA_API_KEY: str = ""
    max_agent_depth: int = 2
    permission_timeout: int = 300
    trusted_ips: list[str] = Field(default_factory=list)  # 可信 IP 列表,跳过登录认证
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v: float | None) -> float | None:
        if v is not None and (v < 0 or v > 2):
            raise ValueError("温度必须在 0-2 之间")
        return v

    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int | None) -> int | None:
        if v is not None and (v < 1 or v > 1000000):
            raise ValueError("max_tokens 必须在 1-1000000 之间")
        return v

    @field_validator("max_agent_depth")
    @classmethod
    def validate_depth(cls, v: int) -> int:
        if v < 1 or v > 20:
            raise ValueError("max_agent_depth 必须在 1-20 之间")
        return v

    @field_validator("permission_timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 1 or v > 3600:
            raise ValueError("permission_timeout 必须在 1-3600 之间")
        return v
