"""配置管理模块 — 从 settings.json 加载持久化配置。

配置文件查找顺序:
1. 项目级:./.UniClaw/settings.json
2. 用户级:~/.UniClaw/settings.json
优先使用项目级配置。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from uniclaw.provider.types import Protocol
from uniclaw.spinner import BaseSpinner

if TYPE_CHECKING:
    from uniclaw.agent import AgentStatus, AgentTask
    from uniclaw.tools.session.session import Session, SessionType


class Permissions(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    ACCEPT_ALL = "accept-all"
    PLAN = "plan"


class RunMode(StrEnum):
    """运行模式。"""

    CONSOLE = "console"
    WECHAT = "wechat"
    WEBUI = "webui"


@dataclass
class ProviderProfile:
    """单个 LLM 提供商配置。"""

    name: str  # profile 名称(如 "mimo", "deepseek")
    protocol: str  # "openai" 或 "anthropic"
    api_key: str
    base_url: str
    proxy_url: str = ""


@dataclass
class AppConfig:
    """应用配置 dataclass,包含 LLM 配置、运行时状态和 Agent/Session 引用。"""

    # === LLM 配置 (从 settings.json 加载) ===
    model_name: list[str] = field(
        default_factory=list
    )  # 主模型列表(第一个为主,后续为 fallback)
    mini_model_name: list[str] = field(default_factory=list)  # mini 模型列表
    multimodal_model_name: list[str] = field(default_factory=list)  # 多模态模型列表
    tts_model: str = ""  # TTS 模型名称
    tts_voice: str = ""  # TTS 语音名称
    providers: dict[str, ProviderProfile] = field(
        default_factory=dict
    )  # 多 provider 配置
    temperature: float = 0.7
    max_tokens: int | None = None
    top_p: float | None = None
    proxy_url: str = ""
    GITHUB_TOKEN: str = ""
    EXA_API_KEY: str = ""
    max_agent_depth: int = 2
    permission_timeout: int = 300

    # === 运行时状态 (不持久化) ===
    permission_mode: str = Permissions.AUTO
    verbose: bool = False
    depth: int = 0
    workspace: list[str] = field(default_factory=list)
    writable_dirs: list[str] = field(default_factory=list)
    run_mode: RunMode = RunMode.CONSOLE
    spinner: BaseSpinner = field(default=None, repr=False)  # type: ignore[assignment]
    output_callback: (
        "Callable[[str, str], None] | Callable[[str, str], Awaitable[None]] | None"
    ) = field(default=None, repr=False)

    @property
    def is_sub(self) -> bool:
        """是否为子代理(depth > 0)"""
        return self.depth > 0

    # === Agent 引用 (必填,session 通过 current_agent.session 访问) ===
    current_agent: AgentTask = field(default=None)  # type: ignore[assignment]
    parent_config: "AppConfig" | None = field(default=None, repr=False)
    sub_configs: list["AppConfig"] = field(default_factory=list, repr=False)

    @property
    def parent_agent(self) -> "AgentTask | None":
        """父代理,通过 parent_config.current_agent 获得。"""
        return self.parent_config.current_agent if self.parent_config else None

    def get_running_subs(self) -> list["AppConfig"]:
        """获取正在运行的子代理(RUNNING 状态)。"""
        return [
            sub for sub in self.sub_configs
            if sub.current_agent and sub.current_agent.status == AgentStatus.RUNNING
        ]

    def has_running_subs(self) -> bool:
        """是否有正在运行的子代理。"""
        return bool(self.get_running_subs())

    @property
    def root_config(self) -> "AppConfig | None":
        """沿 parent_config 链向上追溯,返回最顶级配置(非 sub)。若顶级仍为 sub 则返回 None。"""
        config = self
        while config.parent_config is not None:
            config = config.parent_config
        return config if not config.is_sub else None

    @property
    def root_dir(self) -> Path:
        """便捷属性,返回 current_agent.session.root_dir。"""
        return self.current_agent.session.root_dir

    @property
    def is_wechat(self) -> bool:
        """便捷属性,返回 current_agent.session.is_wechat。"""
        return self.current_agent.session.is_wechat

    @property
    def is_free_chat(self) -> bool:
        """便捷属性,返回当前会话是否为自由聊天模式。"""
        from uniclaw.tools.session.session import SessionType

        return self.current_agent.session.session_type == SessionType.FREE_CHAT

    def create_sub_config(self, name: str, prompt: str) -> AppConfig:
        """创建子代理配置:新 session (同 root_dir),深度+1,复制其他字段。"""
        from uniclaw.tools.session.session import Session

        sub_session = Session(root_dir=self.root_dir)
        from uniclaw.agent import AgentTask

        sub_task = AgentTask(name=name, prompt=prompt, session=sub_session)
        sub_config = AppConfig(
            current_agent=sub_task,
            parent_config=self,
            depth=self.depth + 1,
            model_name=list(self.model_name),
            mini_model_name=list(self.mini_model_name),
            multimodal_model_name=list(self.multimodal_model_name),
            tts_model=self.tts_model,
            tts_voice=self.tts_voice,
            providers=dict(self.providers),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            top_p=self.top_p,
            proxy_url=self.proxy_url,
            GITHUB_TOKEN=self.GITHUB_TOKEN,
            EXA_API_KEY=self.EXA_API_KEY,
            max_agent_depth=self.max_agent_depth,
            permission_timeout=self.permission_timeout,
            permission_mode=self.permission_mode,
            verbose=self.verbose,
            run_mode=self.run_mode,
            spinner=self.spinner,
            output_callback=self.output_callback,
            workspace=list(self.workspace),
            writable_dirs=list(self.writable_dirs),
        )
        self.sub_configs.append(sub_config)
        return sub_config


def get_config_path() -> Path:
    """获取当前生效的配置文件路径(项目级优先)。
    项目级存在则返回项目级,否则返回用户级。
    """
    from uniclaw.context import get_app_dir, Scope

    project_path = get_app_dir(Path.cwd()) / "settings.json"
    if project_path.exists():
        return project_path
    return get_app_dir(Scope.USER) / "settings.json"


def is_first_launch() -> bool:
    """判断是否首次启动(配置文件不存在、读取失败或缺少 provider 配置)。"""
    path = get_config_path()
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError, OSError:
        return True
    has_providers = bool(data.get("providers")) and any(
        p.get("api_key") for p in data.get("providers", {}).values()
    )
    return not has_providers


async def run_setup_wizard() -> dict:
    """首次启动引导程序,提示用户填写必要配置并验证连通性。"""
    from uniclaw.commands.model import fetch_openai_models

    print("\n=== UniClaw 首次启动配置 ===\n")

    data: dict[str, Any] = {}

    # 预设 Base URL
    BASE_URL_MAP: dict[Protocol, list[tuple[str, str]]] = {
        Protocol.OPENAI: [
            ("https://api.openai.com/v1", "OpenAI"),
            ("https://openrouter.ai/api/v1", "OpenRouter"),
            ("https://generativelanguage.googleapis.com/v1beta/openai/", "Google"),
            ("https://api.xiaomimimo.com/v1", "小米 MiMo"),
            ("https://token-plan-cn.xiaomimimo.com/v1", "小米 MiMo 国内"),
        ],
        Protocol.ANTHROPIC: [
            ("https://api.anthropic.com", "官方"),
        ],
    }

    # 第一步:选择 API 兼容协议
    protocols = list(BASE_URL_MAP.keys())
    while True:
        print("选择 API 兼容协议:")
        for i, p in enumerate(protocols, 1):
            print(f"  {i}. {p.upper()} 兼容")
        protocol_idx = input(f"选择 (1-{len(protocols)}, 默认 1): ").strip() or "1"
        if protocol_idx.isdigit() and 1 <= int(protocol_idx) <= len(protocols):
            protocol = protocols[int(protocol_idx) - 1]
            break
        print(f"无效选择,请输入 1-{len(protocols)}。\n")

    # 第二步:选择 Base URL
    urls = BASE_URL_MAP[protocol]
    while True:
        print(f"\n{protocol.upper()} 兼容 API Base URL:")
        for i, (url, name) in enumerate(urls, 1):
            print(f"  {i}. {url:<55} ({name})")
        url_choice = (
            input(f"选择 (1-{len(urls)} 或直接输入 URL, 默认 1): ").strip() or "1"
        )
        if url_choice.isdigit() and 1 <= int(url_choice) <= len(urls):
            base_url = urls[int(url_choice) - 1][0]
            break
        if url_choice.startswith("http"):
            base_url = url_choice
            break
        print("无效输入,请输入序号或完整 URL。\n")

    # 第三步:输入 API Key
    while True:
        api_key = input("API Key: ").strip()
        if api_key:
            break
        print("API Key 不能为空,请重新输入。\n")

    # 第四步:验证连通性并选择模型
    # 根据 base_url 推断 provider 名称
    provider_name = "default"

    # 第四步:验证连通性并选择模型
    protocol_str = protocol.value if hasattr(protocol, "value") else str(protocol)

    if protocol == Protocol.ANTHROPIC:
        from uniclaw.commands.model import fetch_anthropic_models

        models = None
        try:
            models = await fetch_anthropic_models(base_url, api_key)
            print(f"可用模型: {len(models)} 个")
        except Exception:
            pass  # 部分兼容接口不支持 /v1/models,跳过

        if not models:
            print("该接口不支持自动获取模型列表,请手动输入模型名称。")
            model = input("模型名称: ").strip()
            if not model:
                print("模型名称不能为空,请重新配置。\n")
                return await run_setup_wizard()
            data["model_name"] = [f"{provider_name}/{model}"]
            data["mini_model_name"] = [f"{provider_name}/{model}"]
            print(f"已选择: {model}")
            data["providers"] = {
                provider_name: {
                    "name": provider_name,
                    "protocol": protocol_str,
                    "api_key": api_key,
                    "base_url": base_url,
                }
            }
            _save_settings_json(data)
            print(f"\n配置已保存到: {get_config_path()}\n")
            return data
    else:
        print("正在验证 API 连通性...")
        try:
            models = await fetch_openai_models(base_url, api_key)
            print(f"验证成功,可用模型: {len(models)} 个")
        except Exception as e:
            print(f"验证失败: {e}")
            print("请检查 Base URL 和 API Key 后重试。\n")
            return await run_setup_wizard()

    # 选择模型
    print(f"\n可用模型 ({len(models)} 个):")
    for i, m in enumerate(models, 1):
        print(f"  {i}. {m}")
    while True:
        choice = input(f"\n选择模型 (输入序号或名称, 默认 1): ").strip()
        if not choice:
            choice = "1"
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(models):
                model = models[idx]
                break
            print(f"序号无效,请输入 1-{len(models)}")
        elif choice in models:
            model = choice
            break
        else:
            print(f"未找到模型 '{choice}',请重新选择")
    data["model_name"] = [f"{provider_name}/{model}"]
    data["mini_model_name"] = [f"{provider_name}/{model}"]
    print(f"已选择: {model}")

    # 生成 providers 配置
    data["providers"] = {
        provider_name: {
            "name": provider_name,
            "protocol": protocol_str,
            "api_key": api_key,
            "base_url": base_url,
        }
    }

    # 保存配置
    _save_settings_json(data)
    print(f"\n配置已保存到: {get_config_path()}\n")

    return data


def _normalize_model_field(value: str | list[str] | None) -> list[str]:
    """将模型字段归一化为 list[str]。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [v for v in value if v]


def _load_settings_json() -> dict[str, Any]:
    """从 settings.json 读取原始数据。"""
    data: dict[str, Any] = {}
    path = get_config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # 默认值
    defaults = {
        "providers": {},
        "temperature": 0.7,
        "max_tokens": None,
        "top_p": None,
        "max_agent_depth": 3,
        "permission_timeout": 300,
    }
    for k, v in defaults.items():
        if k not in data:
            data[k] = v

    # model_name / mini_model_name / multimodal_model_name 归一化为 list
    data["model_name"] = _normalize_model_field(data.get("model_name"))
    data["mini_model_name"] = _normalize_model_field(data.get("mini_model_name"))
    data["multimodal_model_name"] = _normalize_model_field(
        data.get("multimodal_model_name")
    )

    # mini_model_name 默认等于 model_name
    if data["model_name"] and not data["mini_model_name"]:
        data["mini_model_name"] = list(data["model_name"])

    return data


def load_config(
    root_dir: Path | None = None,
    spinner: BaseSpinner | None = None,
    session: Session | str = "",
    run_mode: RunMode = RunMode.CONSOLE,
    session_type: SessionType | None = None,  # None 则使用 Session 默认值
) -> AppConfig:
    """从 settings.json 加载配置,内部创建 Session 和 AgentTask(name="root")。

    Args:
        root_dir: 工作目录(若传入 session 可为 None,从 session.root_dir 取)
        spinner: 旋转器实例(子代理共享同一实例)
        session: 可选,传入已有 Session 则直接复用,否则新建
        run_mode: 运行模式,控制输入输出方式(console/wechat/webui)
        session_type: 会话类型,有值时覆盖 Session 的默认值
    """
    # 默认值
    if spinner is None:
        from uniclaw.spinner import NoopSpinner

        spinner = NoopSpinner()

    # 创建 Session 和 AgentTask
    from uniclaw.tools.session.session import Session

    from uniclaw.tools.session.session import SessionType

    if not isinstance(session, Session):
        session = Session(root_dir=root_dir, id=session, session_type=session_type or SessionType.CONSOLE)
    elif session_type is not None:
        session.session_type = session_type
    from uniclaw.agent import AgentTask

    task = AgentTask(name="root", prompt="", session=session)

    # root 任务拥有独立的 TodoList(内含 OverseerManager)
    from uniclaw.tools.todolist import TodoList

    task.todolist = TodoList()

    # root 任务拥有独立的 GoalManager
    from uniclaw.tools.todolist.goal import GoalManager

    task.goal_manager = GoalManager()

    # 读取 settings.json
    data = _load_settings_json()

    # 解析 providers
    providers = {}
    for name, p in data.get("providers", {}).items():
        providers[name] = ProviderProfile(
            name=p.get("name", name),
            protocol=p.get("protocol", "openai"),
            api_key=p.get("api_key", ""),
            base_url=p.get("base_url", ""),
            proxy_url=p.get("proxy_url", ""),
        )

    return AppConfig(
        current_agent=task,
        spinner=spinner,
        run_mode=run_mode,
        model_name=data.get("model_name", []),
        mini_model_name=data.get("mini_model_name", []),
        multimodal_model_name=data.get("multimodal_model_name", []),
        tts_model=data.get("tts_model", ""),
        tts_voice=data.get("tts_voice", ""),
        providers=providers,
        temperature=data.get("temperature", 0.7),
        max_tokens=data.get("max_tokens"),
        top_p=data.get("top_p"),
        proxy_url=data.get("proxy_url", ""),
        GITHUB_TOKEN=data.get("GITHUB_TOKEN", ""),
        EXA_API_KEY=data.get("EXA_API_KEY", ""),
        max_agent_depth=data.get("max_agent_depth", 3),
        permission_timeout=data.get("permission_timeout", 300),
    )


def create_sub_agent_config(
    root_dir: Path | None,
    name: str,
    prompt: str,
    model_name: str | None = None,
    run_mode: RunMode = RunMode.CONSOLE,
) -> AppConfig:
    """为无父代理的场景创建子代理配置 (scheduler 等)。深度默认为1。"""
    from uniclaw.spinner import NoopSpinner

    config = load_config(root_dir=root_dir, spinner=NoopSpinner(), run_mode=run_mode)
    config.current_agent.name = name
    config.current_agent.prompt = prompt
    config.depth = 1
    if model_name:
        config.model_name = [model_name]
    return config


def _save_settings_json(data: dict[str, Any]) -> None:
    """保存原始数据到 settings.json。"""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_config(config: AppConfig) -> None:
    """保存配置到当前生效的 settings.json。
    只持久化 LLM 配置字段,过滤运行时状态。
    """
    cleaned = {
        "model_name": config.model_name,
        "mini_model_name": config.mini_model_name,
        "multimodal_model_name": config.multimodal_model_name,
        "tts_model": config.tts_model,
        "tts_voice": config.tts_voice,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "top_p": config.top_p,
        "proxy_url": config.proxy_url,
        "GITHUB_TOKEN": config.GITHUB_TOKEN,
        "EXA_API_KEY": config.EXA_API_KEY,
        "max_agent_depth": config.max_agent_depth,
        "permission_timeout": config.permission_timeout,
    }

    # 序列化 providers
    cleaned["providers"] = {
        name: {
            "name": p.name,
            "protocol": p.protocol,
            "api_key": p.api_key,
            "base_url": p.base_url,
            **({"proxy_url": p.proxy_url} if p.proxy_url else {}),
        }
        for name, p in config.providers.items()
    }

    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
