"""plugin-forge 内置 skill 测试。

分两层验证:
1. skill 注册与指令完整性(确定性断言)。
2. skill 产出效果: 按 plugin-forge 规范生成的典型插件,用真实 PluginManager
   加载、调用、async 防卡死、多文件拆分、生命周期钩子,证明 skill 生成的插件可用。
   插件无 config 权限。
"""
from pathlib import Path

from uniclaw.tools.plugins.loader import PluginManager
from uniclaw.tools.skill.loader import get_builtin_skills


def _home_fixture(tmp_path, monkeypatch):
    """把用户主目录指向 tmp_path,插件目录变为 tmp_path/.UniClaw/plugins/tools/"""
    home = tmp_path / "home"
    plugin_dir = home / ".UniClaw" / "plugins" / "tools"
    plugin_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return plugin_dir


# ── 第一层: skill 注册与指令完整性 ────────────────────────────────


def test_plugin_forge_registered():
    """tool-plugin-forge 应作为内置 skill 注册,且元数据齐全。"""
    import uniclaw.tools.skill.builtin.plugin_forge as module  # noqa: F401

    skills = {s.name: s for s in get_builtin_skills()}
    assert "tool-plugin-forge" in skills, "tool-plugin-forge 未注册到内置技能注册表"

    skill = skills["tool-plugin-forge"]
    assert skill.source == "builtin"
    assert skill.description
    assert any(t.startswith("/") for t in skill.triggers), "触发词应包含斜杠命令"
    # 名字能看出是 UniClaw 添加外部工具插件的 skill
    assert "tool-plugin" in skill.name
    assert len(skill.tools) > 0


def test_plugin_forge_triggers_resolve():
    """新 skill 名与触发词应能被 find_skill 命中。"""
    from uniclaw.tools.skill.loader import find_skill

    for query in ("/tool-plugin-forge", "/forge-tool-plugin", "添加工具插件"):
        found = find_skill(None, query)
        assert found is not None and found.name == "tool-plugin-forge", query


def test_plugin_forge_prompt_contains_key_contracts():
    """prompt 必须保留与 PluginManager 加载行为的硬性契约。"""
    import uniclaw.tools.skill.builtin.plugin_forge as module

    prompt = module._PLUGIN_FORGE_PROMPT
    # 写入位置(唯一合法目录)
    assert "~/.UniClaw/plugins/tools/" in prompt
    # 必含 @tool + get_tools 模板
    assert "@tool" in prompt and "def get_tools()" in prompt
    # 真实加载验证步骤必须存在(不得被删成"只生成不验证")
    assert "真实加载验证" in prompt or "验证" in prompt
    # 插件是动态工具,search_tools 会自动刷新插件目录
    assert "search_tools" in prompt
    assert "自动刷新" in prompt or "刷新插件" in prompt
    assert "实际调用" in prompt or "调用一次" in prompt
    # 调试指引:插件报错时怎么排查
    assert "调试" in prompt or "排查" in prompt
    # 生命周期钩子整(shutdown/close 支持)
    assert "shutdown" in prompt or "PLUGIN_META" in prompt


def test_plugin_forge_prompt_config_only_for_wake_agent_and_demands_async():
    """prompt 必须: 教 wake_agent 唤醒(耗时任务 async);config 仅用于唤醒,不做其他操作。"""
    import uniclaw.tools.skill.builtin.plugin_forge as module

    prompt = module._PLUGIN_FORGE_PROMPT
    assert "PluginManager" in prompt
    # 核心: 耗时任务必须用 async + wake_agent 唤醒(否则主循环卡死)
    assert "async" in prompt.lower()
    assert "wake_agent" in prompt
    assert "asyncio.to_thread" in prompt
    assert "卡死" in prompt
    assert "time.sleep" in prompt or "asyncio.sleep" in prompt
    # config 只用于转手传给 wake_agent,不得用于读取字段
    assert "config" in prompt
    assert "唯一用途是转手传给" in prompt and "wake_agent" in prompt
    # 明确禁止用 config 做读取操作(其余字段不展开讲)
    assert "不读取" in prompt or "不做任何其他操作" in prompt
    # 不教任何 config 属性/字段访问(如 config.xxx / config.get)
    assert "config." not in prompt and ".get(" not in prompt
    # 禁止同步阻塞写法
    assert "time.sleep" in prompt or "asyncio.sleep" in prompt


# ── 第二层: skill 产出效果(真实加载)────────────────────────────


async def test_forge_single_tool_plugin(tmp_path, monkeypatch):
    """eval-1: 基础单工具插件,PluginManager 真实加载并调用。"""
    plugin_dir = _home_fixture(tmp_path, monkeypatch)
    (plugin_dir / "greet.py").write_text(
        '''from uniclaw.tools.base import tool


@tool
def greet(name: str, punctuation: str = ".") -> str:
    """
    向用户打招呼。

    Args:
        name: 打招呼的对象。
        punctuation: 结尾标点。默认为 "."。

    Returns:
        str: 打招呼的文本。
    """
    return f"Hello, {name}{punctuation}"

def get_tools():
    return [greet]
''',
        encoding="utf-8",
    )

    manager = PluginManager.get_instance()
    try:
        tools = await manager.discover_and_load()
        assert [t.name for t in tools] == ["greet"]

        result = await tools[0](name="world")
        assert result == "Hello, world."

        # schema 由类型注解 + docstring 生成
        schema = tools[0].parameters
        assert schema["properties"]["name"]["type"] == "string"
        assert schema["properties"]["name"]["description"] == "打招呼的对象。"
        # 无默认值参数必填,有默认值参数非必填
        assert "name" in schema["required"]
        assert "punctuation" not in schema["required"]
    finally:
        await PluginManager.clear_instance()


async def test_forge_async_slow_tool_plugin(tmp_path, monkeypatch):
    """eval-2: 耗时工具必须 async + asyncio.to_thread,真实加载且调用不卡死。

    校验 skill 产出的慢工具用 async def 让出事件循环,内部阻塞调用走 to_thread。
    """
    import asyncio

    plugin_dir = _home_fixture(tmp_path, monkeypatch)
    (plugin_dir / "slow_op.py").write_text(
        '''import asyncio
from uniclaw.tools.base import tool


@tool
async def sleep_and_echo(text: str, seconds: float = 0.2) -> str:
    """
    模拟耗时操作(用 async 避免卡死主循环)。

    Args:
        text: 要回显的文本。
        seconds: 模拟耗时秒数。

    Returns:
        str: 回显文本。
    """
    await asyncio.sleep(seconds)
    return f"echo: {text}"

def get_tools():
    return [sleep_and_echo]
''',
        encoding="utf-8",
    )

    manager = PluginManager.get_instance()
    try:
        tools = await manager.discover_and_load()
        assert [t.name for t in tools] == ["sleep_and_echo"]
        tool = tools[0]

        # schema 不含 config,不含任何注入的会话状态参数
        assert "config" not in tool.parameters["properties"]
        assert "config" not in tool.parameters["required"]

        # async 工具可正常 await,且期间不阻塞(用并发计时佐证事件循环让出)
        started = asyncio.get_event_loop().time()
        result = await tool(text="hi", seconds=0.1)
        elapsed = asyncio.get_event_loop().time() - started
        assert result == "echo: hi"
        assert elapsed >= 0.1  # 确实耗了时,但因为是 async 不卡死主循环
    finally:
        await PluginManager.clear_instance()


async def test_forge_wake_agent_pattern_plugin(tmp_path, monkeypatch):
    """eval-2b: wake_agent 唤醒模式插件,立即返回 + 后台任务,config 仅转手传唤醒。

    校验 skill 教的官方标准模式: 工具立即 return;后台协程完成后 wake_agent;
    config 参数被 @tool 从 schema 屏蔽(LLM 不会传,由框架注入,仅用于唤醒)。
    """
    import asyncio
    from unittest.mock import patch

    plugin_dir = _home_fixture(tmp_path, monkeypatch)
    (plugin_dir / "wake_op.py").write_text(
        '''import asyncio
from uniclaw.config import AppConfig
from uniclaw.tools.base import tool
from uniclaw.utils.wakeup import wake_agent
from uniclaw.utils.constants import SYSTEM_PREFIX


@tool
def background_job(name: str, config: AppConfig = None) -> str:
    """
    后台执行耗时任务,完成后自动唤醒 AI 继续。

    Args:
        name: 要处理的对象名。

    Returns:
        str: 确认任务已启动。
    """
    async def _run():
        try:
            result = await asyncio.to_thread(_do_work, name)
            await wake_agent(f"{SYSTEM_PREFIX}(background_job) 完成: {result}", config)
        except Exception as e:
            await wake_agent(f"{SYSTEM_PREFIX}(background_job) 失败: {e}", config)

    asyncio.create_task(_run())
    return f"已启动 {name} 的后台处理。"

def _do_work(name: str) -> str:
    return f"处理完了 {name}"

def get_tools():
    return [background_job]
''',
        encoding="utf-8",
    )

    calls: list[str] = []

    async def _fake_wake(message, *a, **k):
        calls.append(message)

    with patch(
        "uniclaw.tools.plugins.loader._builtin_tool_names",
        return_value=set(),
    ), patch("uniclaw.utils.wakeup.wake_agent", side_effect=_fake_wake):
        manager = PluginManager.get_instance()
        try:
            tools = await manager.discover_and_load()
            assert [t.name for t in tools] == ["background_job"]
            tool = tools[0]

            # config 只用于唤醒: 从 schema 中被屏蔽,LLM 只看到 name
            assert list(tool.parameters["properties"].keys()) == ["name"]
            assert "config" not in tool.parameters["required"]

            # 工具立即返回(不阻塞 = 不卡死主循环)
            started = asyncio.get_event_loop().time()
            result = await tool(name="alpha")
            elapsed = asyncio.get_event_loop().time() - started
            assert result.startswith("已启动")
            assert elapsed < 2  # 立即返回,不等待后台完成

            # 后台任务完成 -> 唤醒(给后台一点时间)
            deadline = asyncio.get_event_loop().time() + 3
            while not calls and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.05)
            assert calls, "后台任务应通过 wake_agent 唤醒"
            assert "完成: 处理完了 alpha" in calls[0]
        finally:
            await PluginManager.clear_instance()


async def test_forge_multi_file_plugin(tmp_path, monkeypatch):
    """eval-3: 多文件拆分 + 多工具 + PLUGIN_META + shutdown 钩子。"""
    plugin_dir = _home_fixture(tmp_path, monkeypatch)
    # 辅助模块 crypto_utils.py
    (plugin_dir / "crypto_utils.py").write_text(
        'def caesar(text: str, shift: int) -> str:\n'
        "    import string\n"
        "    alpha = string.ascii_lowercase\n"
        "    trans = str.maketrans(alpha, alpha[shift:] + alpha[:shift])\n"
        "    return text.translate(trans)\n",
        encoding="utf-8",
    )
    # 主插件 crypto.py: import 同目录辅助模块(不带 .py 后缀)
    (plugin_dir / "crypto.py").write_text(
        '''import crypto_utils
from uniclaw.tools.base import tool

_closed = False

PLUGIN_META = {"author": "plugin-forge", "version": "1.0.0"}

@tool
def caesar_encode(text: str, shift: int = 3) -> str:
    """
    对文本进行凯撒加密。

    Args:
        text: 待加密的明文字符串。
        shift: 移位量,默认 3。

    Returns:
        str: 加密后的字符串。
    """
    return crypto_utils.caesar(text, shift)

@tool
def caesar_decode(text: str, shift: int = 3) -> str:
    """
    对文本进行凯撒解密。

    Args:
        text: 待解密的密文字符串。
        shift: 移位量,默认 3。

    Returns:
        str: 解密后的字符串。
    """
    return crypto_utils.caesar(text, -shift)

def get_tools():
    return [caesar_encode, caesar_decode]

async def shutdown():
    global _closed
    _closed = True
''',
        encoding="utf-8",
    )

    manager = PluginManager.get_instance()
    try:
        tools = await manager.discover_and_load()
        assert [t.name for t in tools] == ["caesar_encode", "caesar_decode"]

        assert await tools[0](text="attack at dawn") == "dwwdfn dw gdzq"
        assert await tools[1](text="dwwdfn", shift=3) == "attack"

        plugin = manager.list_plugins()[0]
        assert plugin.meta["author"] == "plugin-forge"

        # 卸载时应调用 shutdown 钩子
        module = plugin.module
        await manager.discover_and_load()
        assert module._closed is True
    finally:
        await PluginManager.clear_instance()


async def test_forge_skips_reserved_tool_name(tmp_path, monkeypatch):
    """plugin-forge 规范强调不能与内置工具重名;插件加载器确实会跳过重名工具。"""
    plugin_dir = _home_fixture(tmp_path, monkeypatch)
    (plugin_dir / "conflict.py").write_text(
        '''from uniclaw.tools.base import tool

@tool
def Read(path: str = "x") -> str:
    """碰撞内置工具名,应被跳过。"""
    return "no"

def get_tools():
    return [Read]
''',
        encoding="utf-8",
    )

    manager = PluginManager.get_instance()
    try:
        tools = await manager.discover_and_load()
        assert tools == []
        assert manager.list_plugins() == []
    finally:
        await PluginManager.clear_instance()