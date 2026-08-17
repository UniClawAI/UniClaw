"""内置 Skill: 插件锻造 — 按用户描述自动生成工具插件。"""

from uniclaw.tools.skill.loader import SkillDef, register_builtin

_PLUGIN_FORGE_PROMPT = """## 工具插件锻造（UniClaw 外部工具插件）

UniClaw 的两个关键系统你需要先理解，才能写出能真正加载的插件：

### 认识 PluginManager（插件管理器）

`uniclaw.tools.plugins.loader.PluginManager` 是**用户级工具插件系统**。它：
- 扫描 `~/.UniClaw/plugins/tools/` 目录下的 `.py` 文件，动态 import 并自动把里面定义的工具注册进 UniClaw 的工具注册表，**无需重启**，改文件即热加载。
- 每个插件文件必须有且仅需一个 `get_tools()` 函数，返回 `@tool` 装饰的工具**列表或元组**。
- 文件名下划线开头的会被跳过；插件可以 `import` 同目录的辅助模块（PluginManager 已把该目录加入 `sys.path`）。
- 工具名冲突的会被**静默跳过**（不报错、不加载），所以重名=整个工具白写。
- 你生成的插件被识别后，会像内置工具一样被 LLM 在后续会话里调用。

你（作为执行者）在本技能里的任务: 捕获意图 → 检查重名 → 写插件 → 真实加载验证 → 报告。

### 操作边界

⚠️ **只操作插件目录**: 生成的文件一律写入 `~/.UniClaw/plugins/tools/`。
**禁止修改**: `src/uniclaw/` 下的内置工具、他人安装的插件、项目的任何源码。
如果用户要求的能力已由内置工具或已有插件覆盖,先说明并推荐复用,再决定是否新建。

### Step 1: 捕获意图

如果当前会话里用户已经描述过完整行为(比如"把刚才的 xx 流程做成工具"),先从会话中提取输入、输出和处理步骤,让用户确认。

如果只有一个模糊主题,问清楚这 3 个问题:

1. **做什么** — LLM 调用它能得到什么结果,核心处理逻辑是什么
2. **输入参数** — 每次调用 LLM 需要传入哪些参数,每个参数的类型和含义
3. **输出** — 返回什么给 LLM(字符串、JSON、文件路径等)

顺带确认一个设计点:
- 是否跨权限依赖(HTTP 请求、外部命令、文件访问、同目录辅助模块)

需求不清晰就确认后再继续,不要猜。

### Step 2: 检查插件目录与重名

1. 用 `Bash` 确认 `~/.UniClaw/plugins/tools/` 存在,不存在则 `mkdir -p` 创建。
2. 用 `Glob` + `Read` 查看目录下已有的插件文件,读一遍: 了解已有能力,避免重复。
3. **重名检测**: 工具名不能与内置工具或现有插件工具重名(重名的会被 PluginManager 静默跳过,整个插件等于白写)。
   获取内置工具名: `python -c "from uniclaw.tools.registry import get_builtin_tool_names; print(get_builtin_tool_names())"`。
   从已有插件文件里提取出口工具名。做差集,冲突时给工具另取名字。

### Step 3: 设计插件

#### 命名

- 插件文件名: 蛇形或连字符,如 `weather_tool.py`。文件名即插件名。
- 工具名: 只含 `[A-Za-z0-9_-]`,简短且表意,如 `get_weather`。不同插件也可以拆到不同文件。

#### 单文件 vs 多文件

- **单文件**: 工具逻辑简单(纯计算、格式化、调用现有 API),核心 + `get_tools()` 集中在一个 `.py`。
- **多文件拆分**: 逻辑复杂时,主文件写 `@tool` 工具 + `get_tools()`,复杂实现拆到同目录辅助模块
  (如 `utils.py`),主文件 `import utils`。PluginManager 会把插件目录加入 `sys.path`,插件
  可以 `import` 同目录的辅助模块,且热重载时会重新导入,不存在缓存污染问题。
  - 注意: 辅助模块文件名不要以下划线开头,否则会被跳过扫描;也不要在辅助模块里定义 `.py` 之外的额外入口。

### Step 4: 按规范写代码

必须用 `@tool` 装饰器(`from uniclaw.tools.base import tool`),自动生成 OpenAI function calling schema。
工具函数永远用 Google style docstring:

```python
from uniclaw.tools.base import tool


@tool
def my_tool(arg1: str, arg2: int = 10) -> str:
    \"\"\"
    工具的简要描述(LLM 据此决定何时调用)。

    更详细的使用说明和注意事项。指令请写清楚。例如: 参数含义、单位、边界情况。

    Args:
        arg1: 参数1的描述。
        arg2: 参数2的描述。默认为 10。

    Returns:
        str: 返回值的描述。
    \"\"\"
    return "result"


def get_tools():
    return [my_tool]
```

#### 关键规则

- **docstring 决定 schema**: `Args:` 段的每行解析为参数描述; 摘要部分为工具描述。
  没有 `Args:` 段时参数只有名字和类型,LLM 不知道传什么。给每个参数写清描述。
- **类型注解**决定参数类型,支持 `str` / `int` / `float` / `bool` / `list[X]` / `dict` / `Optional[X]`。
- **枚举约束**: 参数值是固定集合时,用 `Enum` 导出,会自动生成 enum 列表。
- **默认值参数不强制**(不给 required),无默认值参数为必填。别给必填参数写默认值。
- **不要手动构造 `Tool(...)`**: 除非手写 schema 有特殊用途,否则一律用 `@tool`,保证 `strict` 兼容。
- **返回值**: 返回对 LLM 有用的纯文本或 JSON 字符串,不要返回 `None`。

#### ⏱️ 耗时任务必须用 async + wake_agent 唤醒,否则整个程序会卡死

UniClaw 的主循环是**单个 asyncio 事件循环**,工具调用会被 `await`。如果插件函数里阻塞地执行长时间任务
(大 HTTP 请求、同步文件扫描、等待外部进程、`time.sleep`),整个 agent 会**卡住**,后续所有工具都无法进行。

**标准做法**: 工具函数**立即返回**、不等待,用 `asyncio.create_task(...)` 在后台执行,完成后用
`wake_agent(...)` 唤醒。这是内置 `sleep_timer` / `ConvertToMarkdown` 的官方模式。

```python
import asyncio
from uniclaw.config import AppConfig
from uniclaw.tools.base import tool
from uniclaw.utils.wakeup import wake_agent
from uniclaw.utils.constants import SYSTEM_PREFIX


@tool
def long_task(name: str, config: AppConfig = None) -> str:
    \"\"\"
    执行耗时的长任务(立即返回,后台完成后自动唤醒).

    Args:
        name: 要处理的对象名。

    Returns:
        str: 确认任务已启动。
    \"\"\"
    async def _run():
        try:
            result = await asyncio.to_thread(_do_heavy_work, name)
            await wake_agent(f"{SYSTEM_PREFIX}(long_task) 完成: {result}", config)
        except Exception as e:
            await wake_agent(f"{SYSTEM_PREFIX}(long_task) 失败: {e}", config)

    asyncio.create_task(_run())
    return f"已启动 {name} 的处理,完成后会自动唤醒通知。"
```

要点:
- **函数是同步 `def`,立即 `return`**,不阻塞;真正的耗时逻辑放进后台协程 `_run()`。
- 后台里阻塞的库调用(requests、subprocess、文件 IO)用 `await asyncio.to_thread(...)` 跑在线程池。
- 完成/失败都通过 `wake_agent(f"{SYSTEM_PREFIX}(工具名) 结果", config)` 唤醒,让 LLM 继续。
- **`config` 唯一用途是转手传给 `wake_agent`**,不读取、不操作它的任何字段。
- **不要用 `time.sleep`**,在后台协程里用 `await asyncio.sleep(...)`。

### Step 5: 生命周期钩子(可选)

插件可以声明可选的 `PLUGIN_META` 字典和 `shutdown()/close()` 清理钩子。PluginManager 在退出时
会调用钩子(支持同步和 async)。需要释放后台进程、连接、临时文件时才定义;否则不要写,保持精简。

```python
PLUGIN_META = {"author": "user", "version": "1.0.0"}


async def shutdown():
    await _client.close()
```

### Step 6: 生成后必须真实加载验证

保存插件文件后,**在当前会话中实际调用测试**,不要跳过。插件是**动态工具**,当前会话启动时
工具列表已快照,新插件不在里面。`search_tools` 会**自动刷新插件目录**,无需额外操作。按以下顺序:

1. 调用 `search_tools(query="<新工具名>")` — 自动刷新插件并把工具加载到当前会话。
2. 实际调用刚加载的工具,传入一组有代表性的参数,验证返回结果符合预期。
3. 若工具报错,修正插件文件,再从第 1 步重新开始。

只有真实调用成功了,才算完成验证,才能进入交付报告。

**调试指引 — 插件报错时怎么排查**:

| 症状 | 排查方法 |
|---|---|
| `search_tools` 搜不到插件 | 用 `Read` 检查插件文件路径是否正确、文件名是否下划线开头(会被跳过)、`get_tools()` 是否存在且返回非空列表 |
| 搜到了但调用报 `ToolError` | 插件语法或运行时出错。用 `Bash` 执行 `python -c "import importlib.util; ..."` 加载模块看 traceback |
| `asyncio.create_task` 后台任务静默失败 | 后台协程里的异常不会冒泡到工具返回值。在 `_run()` 里加 `try/except` 并用 `print` 或日志输出错误,或在 `except` 里通过 `wake_agent` 把错误信息传回 |
| 工具返回值不符合预期 | 检查 `@tool` 的 docstring 与实现是否一致:参数名、类型、默认值、返回值类型必须对齐 |
| `config` 相关报错 | 插件不应直接使用 config。如果需要唤醒,只把 config 原样传给 `wake_agent` |

### Step 7: 交付报告

验证通过后,向用户说明:

```
## 🔌 插件创建报告

### 需求
- 做什么: ...
- 参数: ...
- 输出: ...

### 生成的插件
- 文件: ~/.UniClaw/plugins/tools/<name>.py
- 工具: <name> — <description 一句话>
- 依赖: 无 / 辅助模块 xxx.py
- 生命周期: 无 / PLUGIN_META / shutdown

### 验证结果: 通过(真实加载 + 会话内实际调用)

### 生效方式
插件已被 PluginManager 自动发现并热加载;我已在当前会话用 search_tools 搜索加载并通过 <工具名>
实际调用测试成功。现在可直接使用 `<工具名>`;也可以输入 `!` 直接测试。
```

如果验证失败,报告失败原因与已做的修正。"""


def register():
    """注册 tool-plugin-forge 技能(UniClaw 添加外部工具插件)"""
    register_builtin(
        SkillDef(
            name="tool-plugin-forge",
            description="工具插件锻造: 按用户需求自动生成 UniClaw 外部工具插件,写入 ~/.UniClaw/plugins/tools/,"
            "支持多文件拆分、生命周期钩子、耗时任务 async + wake_agent 唤醒,生成后即时验证可加载。"
            "当用户要求给 UniClaw 添加/生成外部工具插件、写个新工具、自定义扩展功能、插件锻造时使用,"
            "即使没有明说'插件'、只说'加个工具'也要先考虑本技能。",
            triggers=[
                "/tool-plugin-forge",
                "/forge-tool-plugin",
                "+tool-plugin",
                "添加工具插件",
                "工具插件锻造",
                "生成工具插件",
                "新增工具",
            ],
            tools=["Bash", "Read", "Write", "Glob", "Grep"],
            prompt=_PLUGIN_FORGE_PROMPT,
            file_path=__file__,
            source="builtin",
            when_to_use="用户要求给 UniClaw 添加/生成外部工具插件、新增自定义工具、扩展 UniClaw 能力时",
            argument_hint="<工具功能描述>",
        )
    )