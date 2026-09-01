# UniClaw 🦞

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**UniClaw** 是一个基于大语言模型的智能代理系统,提供交互式命令行界面、WebUI 和微信集成,支持文件操作、Shell 命令执行、网络搜索、记忆管理、多智能体协作、定时任务调度等丰富功能。通过模块化的工具系统、全异步架构和权限管理机制,帮助用户高效完成各种编程和文本处理任务。

> 📦 项目采用 **src layout** — 所有源码位于 `src/uniclaw/` 下。

## ✨ 特性

- 🤖 **智能代理**: 基于 OpenAI SDK 和 Anthropic SDK 的全异步对话式 AI 助手,多 provider 自动路由,支持 reasoning_content 和思考标签流式解析,内置死循环检测防止工具调用陷入无限循环
- 🔄 **多模型 Fallback**: 主模型失败时自动切换到备用模型,支持 `model_name` 列表配置多个模型,提高可用性
- 🩺 **错误分类重试**: 内置错误分类器将 LLM 调用错误分为六类(RATE_LIMIT/CONTEXT_OVERFLOW/AUTH/SERVER_ERROR/TIMEOUT/UNKNOWN),按分类采用差异化重试与退避策略(CONTEXT_OVERFLOW 先压缩会话再重试,指数/线性退避)
- 🎓 **顾问模型**: 配置 `large_model_name` 作为顾问模型,遇到难题时可同时咨询多个更强力模型获取第二意见
- 🔍 **工具注册表**: BM25 智能工具搜索,核心工具常驻加载 + 扩展工具按需发现(search_tools 元工具),LRU + 能量机制(每工具 30 点能量,每轮-1,调用/搜索恢复满,归零淘汰)自动管理已加载工具,优化 prompt 缓存
- 📡 **工具可用性跟踪**: 环境检测自动记录不可用工具及原因(如 Docker 未安装、未配置 embedding_model),search_tools 搜索时自动提示不可用原因,避免无用调用
- 🌐 **WebUI 界面**: 基于 WebSocket 的 Web 用户界面,支持浏览器中与 AI 对话,可局域网共享,支持移动端响应式设计和触摸手势,支持 HTTPS 和 IPv6
- ⚡ **工具流式输出**: 工具执行过程中实时推送输出到前端,无需等待完成即可看到进度
- 🔐 **可信 IP 免登录**: 配置 `trusted_ips` 后,指定 IP 地址的客户端可跳过 WebUI 登录认证,适合家庭/办公网络环境
- 💬 **微信集成**: 支持通过 iLink Bot 协议接入微信,实现移动端交互
- 🧠 **记忆系统**: 持久化记忆管理,支持用户偏好、项目信息和反馈记录
- 🗺️ **知识图谱**: 基于 SQLite 的知识图谱系统,支持实体/关系管理、全文搜索、路径发现、自动提取和可视化导出,用户级和项目级双层管理
- 📚 **RAG 检索增强**: 文档导入、智能拆分、向量嵌入和语义检索,支持多集合管理和用户级/项目级双层作用域
- 👥 **多智能体协作**: 全异步架构支持创建和管理多个专业智能体,实现任务分工协作和智能体间通信
- 🖥️ **计算机控制**: 屏幕截图、鼠标/键盘自动化操作(全平台异步实现),支持全局热键 (Ctrl+U) 切换,`/cu` 命令一键开关
- 🔊 **语音合成**: TTS 文本转语音,支持多种风格/情绪/方言控制,`/voice` 命令切换语音模式
- 🎨 **图片生成**: 配置 `image_model` 后支持 AI 文生图,可保存为文件或直接分析,支持自定义尺寸
- 📤 **文件发送**: AI 可将生成的文件发送给用户,WebUI 提供下载链接,微信直接发送
- 📋 **任务清单**: 任务分解与跟踪,支持自动进度管理和状态流转
- ⏰ **定时任务**: 支持创建和管理周期性或一次性定时任务,支持会话关联和 monitor 监控类型(命令退出码触发 agent)
- 🔄 **后台进程**: 启动和管理后台进程(异步实现),支持输入/输出流控制
- 🛡️ **死循环检测**: 自动检测 AI 连续相同工具调用,智能打破循环并引导换策略
- 💾 **自动保存**: 会话和记忆在对话结束时自动持久化,数据不丢失
- 🪝 **Hook 系统**: 事件驱动的 Shell 命令钩子,支持会话和工具调用生命周期事件
- 🔔 **系统通知**: 支持 Windows/macOS/Linux 桌面通知,任务完成时自动提醒
- 📝 **计划模式**: 支持进入计划模式进行任务规划,暂存方案后再执行
- 🔧 **工具解释模式**: 通过 `/explain` 命令开启,AI 调用工具前会解释原因,便于理解和调试工具调用逻辑
- 🐍 **IPython 内核**: 持久化 Python 交互式执行环境,变量跨调用保持,支持魔术命令和富文本输出
- 🛠️ **丰富的工具集**: 内置文件系统操作、Shell 命令、网络搜索、技能系统等工具
- 🔒 **权限管理**: 支持多种权限模式(自动/手动/全部接受),保障操作安全
- 📋 **持久化规则**: 自定义权限规则,记住您的权限偏好,避免重复确认
- 💭 **实时反馈**: 显示思考过程、工具调用详情和 Token 使用情况
- ♾️ **无限上下文**: 双列表存储(活跃上下文 + 完整历史) + 三级自动压缩(50%/70%/85%) + 按需历史召回,对话永不失忆
- 📊 **上下文管理**: 自动监控和管理对话上下文长度,三级压力策略自动压缩(50%/70%/85%)
- 🎯 **目标模式**: 设置目标停止条件,agent 停止时用独立 judge 模型评估是否达成,未达标则自动继续工作
- 🌐 **平台搜索**: 支持 GitHub/arXiv/Stack Overflow/Hacker News/B站等多平台并发搜索
- 📚 **扩展搜索平台**: 新增 OpenAlex/Google News/Reddit/Polymarket/SEC EDGAR/HuggingFace(models/datasets)/alphaXiv 等学术与数据搜索平台,OpenAlex 支持 429 重试,HuggingFace 支持 models/datasets 两种类型
- 🔎 **智能搜索**: webSearch 自动切换 Exa 语义搜索 → Bing → DuckDuckGo,内置 Exa MCP 服务器,支持时间范围过滤(time_range)和 LLM 智能重排
- 🌍 **浏览器自动化**: 基于 Playwright 的浏览器控制,支持导航/点击/输入/截图/JS 执行等操作
- ⬇️ **HTTP 下载**: 多协程并发下载、断点续传、代理支持、自动重试和文件校验,支持同步/异步两种模式
- 🎞️ **M3U8/HLS 流下载**: 支持 M3U8/HLS 流媒体下载,自动解析主/子流,支持自定义请求头、重试、代理和断点续传,与 HTTP 下载管理器分离
- 🔄 **可视终端监控**: 后台进程支持 `visible` 参数弹出真实控制台窗口(基于 ConPTY 伪终端),用户可直接交互,支持屏幕快照、窗口尺寸同步、输入转发和多行命令检测
- 🔁 **进程重启**: WebUI 模式支持 `restart_agent` 工具自动重启自身进程,优雅保存会话后热更新代码,重启后自动恢复会话并唤醒 agent
- 📋 **知识图谱混合搜索**: 实体搜索支持 FTS5 前缀匹配 + LIKE 子串回退的混合查询,中英文混合查询均可用,并覆盖实体别名表
- 🔌 **插件系统**: 用户级外部工具插件动态加载,`~/.UniClaw/plugins/tools/` 目录放置 `.py` 文件即可热加载,支持多文件拆分、生命周期钩子、内置工具重名检测
- 🔨 **工具插件锻造**: `/tool-plugin-forge` 技能一键生成工具插件,自动捕获意图 → 检查重名 → 生成代码 → 真实加载验证,支持耗时任务异步唤醒模式
- 🎯 **技能系统**: 可扩展的技能机制,支持自定义任务模板和工作流
- 🎨 **主题定制**: 内置 uniclaw-theme 技能,支持自定义 WebUI 主题颜色、字体和样式
- 🔌 **MCP 集成**: 支持 Model Context Protocol,异步命令管理,可连接多种外部工具服务
- 🌉 **A2A 远程代理**: 支持 Agent2Agent 协议,可调用外部 A2A Agent 或将 UniClaw 暴露为 A2A 端点,实现多 Agent 协作
- ⏱️ **异步等待**: sleep_timer 工具支持延时唤醒,不阻塞主线程
- 📸 **Git 检查点**: 自动创建 git stash 检查点,支持一键回滚 AI 的文件编辑,智能处理 .gitignore,不污染 git 历史
- 📝 **斜杠命令**: 丰富的内置命令系统,支持会话管理、模型切换、任务管理等
- 📁 **文件补全**: 支持 `@` 命令自动补全文件名,支持多级子目录导航
- 🔧 **子命令补全**: 斜杠命令支持子命令自动补全,输入空格后显示可用子命令
- 💬 **对话管理**: 支持历史对话的查看、加载、删除、搜索和分叉功能,历史消息自动去重
- 🎨 **TUI 界面**: 精美的终端用户界面,支持详细/简洁模式切换(F2),侧边栏显示对话列表
- 📈 **用量统计**: 实时监控 Token 使用情况、工具调用统计和费用明细,价格自动从 OpenRouter API 获取
- ⚙️ **会话级配置**: WebUI 支持带 `session_id` 保存配置,当前会话内存立即生效,同时写入全局配置
- 🌐 **跨平台支持**: 兼容 Windows、Linux 和 macOS 系统

## 📋 目录

- [安装](#-安装)
- [快速开始](#-快速开始)
- [配置说明](#-配置说明)
- [使用指南](#-使用指南)
- [WebUI 模式](#-webui-模式)
- [微信机器人集成](#-微信机器人集成)
- [工具系统](#-工具系统)
- [插件系统](#-插件系统)
- [知识图谱系统](#-知识图谱系统)
- [RAG 检索增强系统](#-rag-检索增强系统)
- [MCP 集成](#-mcp-集成)
- [A2A 远程代理](#-a2a-远程代理)
- [架构设计](#-架构设计)
- [常见问题](#-常见问题)

## 🚀 安装

### 前置要求

- Python 3.11 或更高版本
- uv 包管理器
- 可选：Docker(用于代码沙箱功能)、Everything(Windows 文件搜索加速)

```bash
# 安装项目依赖
uv sync

# 或者安装开发依赖(包含测试工具)
uv sync --group dev

# 或者全局安装为命令行工具
uv tool install .
```

**配置文件**

首次启动时会自动运行配置向导(`run_setup_wizard()`),引导您配置 API 地址、密钥和模型。也可手动创建配置文件：

```
# 项目级
.UniClaw/settings.json

# 用户级（全局生效）
~/.UniClaw/settings.json
```

配置文件为 JSON 格式：

```json
{
  "providers": {
    "default": {
      "name": "default",
      "protocol": "openai",
      "api_key": "your_api_key_here",
      "base_url": "https://api.openai.com/v1"
    }
  },
  "model_name": ["default/gpt-5.4"],
  "mini_model_name": [],
  "multimodal_model_name": [],
  "large_model_name": [],
  "image_model": "",
  "tts_model": "",
  "asr_model": "",
  "embedding_model": "",
  "temperature": 0.7,
  "max_tokens": null,
  "top_p": null,
  "proxy_url": "",
  "GITHUB_TOKEN": "",
  "EXA_API_KEY": "",
  "max_agent_depth": 2,
  "permission_timeout": 300,
  "trusted_ips": []
}
```

> 💡 支持多 provider 配置，通过 `providers` 字典管理多个 API 提供商。`model_name` 为列表格式，第一个为主模型，后续为 fallback。首次启动时会自动运行配置向导。

**运行项目**

```
# 使用 uv 运行(控制台模式,默认)
uv run uniclaw

# 使用 uv 运行(WebUI 模式)
uv run uniclaw --mode webui
uv run uniclaw --mode webui --host 0.0.0.0  # 局域网可访问
uv run uniclaw --mode webui --host 0.0.0.0 --no-ssl  # 禁用 HTTPS

# 或者直接运行入口文件
uv run python src/uniclaw/main.py

# 如果通过 uv tool install 安装,可直接运行
uniclaw
uniclaw --mode webui
```

**常用 uv 命令**

```
# 添加新依赖
uv add <package-name>

# 添加开发依赖
uv add --dev <package-name>

# 运行测试
uv run pytest tests/ -v

# 更新依赖
uv lock --upgrade

# 格式化代码
uv run black .
```

## 🎯 快速开始

### 启动交互式会话

```
# 使用 uv 运行(控制台模式,默认)
uv run uniclaw
```

启动后将进入 REPL (Read-Eval-Print Loop) 交互界面：

```
[UniClaw] 5% » 你好,请介绍一下自己
💭 [思考中]
我是一个AI助手...

📝 [回复]
你好！我是 UniClaw 助手...
```

### 启动 WebUI

```
# 启动 WebUI 模式(仅本机访问)
uv run uniclaw --mode webui

# 局域网可访问
uv run uniclaw --mode webui --host 0.0.0.0

# 禁用 HTTPS(使用 HTTP)
uv run uniclaw --mode webui --host 0.0.0.0 --no-ssl

# 指定域名(用于 HTTPS 证书)
uv run uniclaw --mode webui --host 0.0.0.0 --domain uniclaw.example.com
```

详细使用方法请参考 [WebUI 模式](#-webui-模式) 章节。

### 启动微信机器人

微信机器人功能已集成在 WebUI 模式中，启动 WebUI 后在侧边栏管理微信账号：

```
uv run uniclaw --mode webui
```

详细使用方法请参考 [微信机器人集成](#-微信机器人集成) 章节。

### 基本用法

- **直接输入**: 与 AI 助手进行对话
- **! 命令**: 执行 Shell 命令(例如 `!ls -la`)
- **/ 命令**: 执行内置斜杠命令(例如 `/clear`、`/model gpt-4o`)
- **@ 文件补全**: 输入 `@` 后自动补全文件名,支持多级子目录导航
- **空行**: 跳过当前输入
- **Token 提示**: 右侧显示当前上下文使用率(颜色指示：绿色<40%,黄色40-70%,红色>70%)

### 文件补全功能

UniClaw 支持使用 `@` 命令快速补全文件名,方便在对话中引用文件：

#### 基本用法

- 输入 `@` 会显示当前目录下的所有文件和文件夹
- 输入 `@src` 会过滤出以 `src` 开头的文件
- 输入 `@src/` 会显示 `src` 目录下的内容
- 输入 `@src/uniclaw/` 会显示 `src/uniclaw` 目录下的内容

#### 使用示例

```
# 查看当前目录文件
@

# 进入子目录
@src/
@src/uniclaw/
@src/uniclaw/console/

# 过滤文件
@src/uniclaw/console/run
```

#### 功能特性

- ✅ 支持多级子目录导航
- ✅ 自动过滤隐藏文件(以 `.` 开头的文件)
- ✅ 显示文件大小和目录标识
- ✅ 支持路径分隔符(`/` 和 `\`)
- ✅ 目录名不会自动添加斜杠,用户可手动输入进入下级目录

### 子命令补全功能

UniClaw 的斜杠命令支持子命令自动补全,输入命令后按空格会显示可用的子命令：

#### 支持子命令的命令

> 💡 下表反映了 `COMMAND_SUBCOMMANDS` 中实际注册的子命令补全列表。

| 命令 | 子命令 |
|------|--------|
| `/memory` | `consolidate` |
| `/schedule` | `list`, `add`, `remove`, `enable`, `disable` |
| `/mcp` | `list`, `add`, `remove`, `show`, `edit`, `enable`, `disable`, `tools`, `refresh` |
| `/permissions` | `list`, `remove` |
| `/resume` | `list`, `del`, `search`, `fork` |
| `/task` | `list`, `output`, `stop`, `matched` |
| `/overseer` | `start`, `stop` |
| `/checkpoint` | `create`, `pop`, `apply`, `delete`, `diff` |
| `/goal` | `clear`, `status` |
| `/kg` | `stats`, `search`, `list`, `export`, `clear` |
| `/a2a` | `start`, `stop`, `status`, `list`, `add`, `remove`, `test` |

#### 使用示例

```
# 输入命令后按空格,显示子命令
/schedule 
# 显示: list, add, remove, enable, disable

# 输入子命令前缀进行过滤
/schedule a
# 显示: add

# 完整子命令
/schedule add "0 * * * *" "shell: git status"
```

### 工作空间

UniClaw 使用工作空间概念管理文件访问范围：

- **root_dir**: 会话工作目录(`config.root_dir`),可通过 `/cwd` 命令改变,子代理可能有独立的 root_dir
- **当前目录**: 启动 UniClaw 时的进程工作目录(`Path.cwd()`),整个进程生命周期不变
- **额外工作空间目录**: 通过 `/add_dir <路径>` 添加的其他目录,仅当前会话有效

> ⚠️ **注意**: `root_dir` 和 `Path.cwd()` 是两个不同的概念,不能混用。文件读写、安全检查、记忆存储、技能加载等操作使用 `root_dir`。

```bash
/add_dir D:/projects/other-project  # 添加额外工作空间目录
/add_dir                             # 查看当前工作空间目录列表
```

## ⚙️ 配置说明

### 配置项说明

| 配置键 | 说明 | 默认值 | 示例 |
|--------|------|--------|------|
| `providers` | 多 provider 配置(字典格式) | 必需 | 见下方示例 |
| `model_name` | 主模型列表(第一个为主,后续为 fallback) | 无 | `["default/gpt-5.4"]` |
| `mini_model_name` | 迷你模型列表(用于简单任务) | 自动使用 model_name | `["default/gpt-4o-mini"]` |
| `multimodal_model_name` | 多模态模型列表(主模型不支持多模态时使用) | 无 | `["default/gpt-4o"]` |
| `large_model_name` | 顾问模型列表(用于获取更强力模型的建议) | 无 | `["default/o3"]` |
| `image_model` | 图片生成模型(文生图,留空禁用) | `""` | `"default/dall-e-3"` |
| `tts_model` | TTS 语音合成模型(留空禁用) | `""` | `"default/tts-1"` |
| `asr_model` | ASR 语音识别模型(留空禁用) | `""` | `"default/whisper-1"` |
| `embedding_model` | Embedding 向量模型(用于 RAG 语义检索,留空禁用) | `""` | `"default/text-embedding-3-small"` |
| `temperature` | 生成温度(创造性) | `0.7` | `0.0`-`2.0` |
| `max_tokens` | 最大输出 token 数 | `null`(不限制) | `512`, `2048` |
| `top_p` | 核采样概率 | `null`(不限制) | `0.9` |
| `proxy_url` | HTTP 代理地址 | `""` | `http://127.0.0.1:7890` |
| `GITHUB_TOKEN` | GitHub Token(平台搜索提速) | 空 | `ghp_xxx` |
| `EXA_API_KEY` | Exa API Key(语义搜索引擎,webSearch 优先使用) | 空 | `exa-xxx` |
| `max_agent_depth` | 最大嵌套智能体深度 | `2` | `1`-`5` |
| `permission_timeout` | 权限对话框超时时间(秒) | `300` | `60`-`600` |
| `trusted_ips` | 可信 IP 列表(跳过 WebUI 登录认证) | `[]` | `["192.168.1.100"]` |

**Provider 配置示例：**

```json
{
  "providers": {
    "default": {
      "name": "default",
      "protocol": "openai",
      "api_key": "sk-xxx",
      "base_url": "https://api.openai.com/v1"
    },
    "anthropic": {
      "name": "anthropic",
      "protocol": "anthropic",
      "api_key": "sk-ant-xxx",
      "base_url": "https://api.anthropic.com"
    }
  }
}
```

> 💡 `protocol` 支持 `"openai"` 和 `"anthropic"` 两种协议。系统会根据 model_name 中的 provider 前缀(如 `default/`、`anthropic/`)自动路由到对应的 provider。

**超时配置**:
- `timeout`: 连接超时时间(秒),默认根据协议类型自动设置
  - SSE: 5 秒
  - Streamable HTTP: 10 秒
  - 工具发现: 15 秒
- **Provider 请求超时**: LLM API 请求超时默认 180 秒(3 分钟),可通过各 provider 路由方法的 `timeout` 参数覆盖

### 权限模式说明

- **auto**: 自动批准读取类操作,对写入和不安全的 Bash 命令询问用户
- **manual**: 所有工具调用都需要用户手动确认
- **accept-all**: 自动批准所有操作(谨慎使用)
- **plan**: 计划模式(通过工具调用进入)

**配置方式：**
- WebUI 设置页面直接切换权限模式
- 配置文件 `settings.json` 中设置 `permission_mode` 字段
- 命令 `/permissions mode <模式>` 临时切换

### 持久化权限规则 🔒

UniClaw 支持自定义持久化权限规则,可以记住您的权限偏好：

**Bash 命令规则：**
- 基于命令前缀匹配(如 `git commit`、`npm install`)
- 一旦授权,同类命令将自动放行
- 存储在项目的 `permission_rules.json` 文件中

**工具规则：**
- 基于工具名称精确匹配(如 `Write`、`Edit`)
- 授权后该工具的所有调用都自动批准

**管理命令：**
```bash
/permissions list              # 查看所有权限规则
/permissions remove <类型> <模式>  # 删除规则
```

> 💡 **如何添加规则**: `/permissions` 命令本身只支持 `list` 和 `remove`。规则是在权限确认弹窗中按 **`a`**(始终允许)自动保存的——此时系统会自动调用 `add_permission_rule` 写入 `permission_rules.json`,无需手动添加。

**示例：**
```bash
# 当 AI 请求权限时,在确认弹窗中按 a(始终允许)
# → 自动保存规则到 permission_rules.json

# 查看当前规则
/permissions list

# 删除规则
/permissions remove bash "git commit"
```

> 💡 **提示**: 持久化规则在安全检查流程中具有较高优先级,但仍会被危险操作符检测(如 `;`、`&&`、`||`)拦截,确保安全。

### CLAUDE.md 项目指令

在项目根目录创建 `CLAUDE.md` 文件,可以为 AI 提供项目特定的指令和规范。该文件会在每次对话时自动加载到系统提示词中。

**示例 CLAUDE.md：**

```
# 项目规范

## 代码风格
- 使用 TypeScript 严格模式
- 组件使用函数式组件 + Hooks
- 样式使用 Tailwind CSS

## 命名规范
- 文件名：kebab-case
- 组件名：PascalCase
- 变量名：camelCase

## Git 提交规范
- feat: 新功能
- fix: 修复 bug
- docs: 文档更新
- refactor: 重构
```

**特性：**
- 自动加载当前工作目录下的 CLAUDE.md
- 文件大小限制 10KB
- 内置提示词注入防护

## 📖 使用指南

### 交互式会话功能

#### Token 使用监控

会话界面会实时显示当前上下文的 Token 使用率：

- 🟢 **绿色 (<40%)**: 使用率低,空间充足
- 🟡 **黄色 (40-70%)**: 使用率中等,注意控制
- 🔴 **红色 (>70%)**: 使用率高,接近限制

#### 详细显示模式 📊

按 **F2** 键可切换详细/简洁显示模式：

**简洁模式(默认)**：
- 只显示 AI 的思考过程和回复内容
- 隐藏工具调用的元数据信息
- 界面更清爽,适合日常使用

**详细模式**：
- 显示完整的工具调用信息(参数、调用ID等)
- 显示 Token 使用统计(输入/输出 tokens)
- 显示模型名称和工具调用数量
- 显示文件差异的详细内容
- 适合调试和了解 AI 的工作细节

> 💡 **提示**: 可以通过配置 `VERBOSE=true` 在启动时默认启用详细模式。

#### 事件类型展示

系统会显示以下事件类型：

- 💭 **思考中**: AI 的思考过程
- 📝 **回复**: AI 的文本回复
- 🤖 **助手元数据**: 工具调用数量、参数、Token 使用统计
- 🔧 **工具执行**: 工具名称、调用 ID、执行结果

#### 对话侧边栏 💬

TUI 界面左侧提供对话历史侧边栏,方便管理和切换会话：

**功能特性：**
- 📋 **自动保存**: 每次对话结束后自动保存到历史记录
- 🔄 **快速加载**: 通过侧边栏选择并加载历史对话(按 Enter)
- 🔍 **状态标识**: 
  - `>` 表示当前选中的对话
  - `*` 表示当前活跃的会话
- ⌨️ **键盘操作**:
  - `Ctrl+K`: 聚焦/离开对话侧边栏
  - `↑/↓`: 在侧边栏中上下选择
  - `Enter`: 加载选中的对话
  - 其他按键: 返回主输入框

**使用流程：**
1. 按 `Ctrl+K` 聚焦到对话侧边栏
2. 使用方向键选择要加载的对话
3. 按 `Enter` 加载该对话的历史记录
4. 继续在该对话基础上进行交流

> 💡 **提示**: 也可以通过 `/resume` 命令进行更精细的对话管理,包括搜索和删除等操作。

### 斜杠命令系统

UniClaw 提供了丰富的斜杠命令(`/command`),用于管理系统功能和执行特定操作。

**子命令补全功能**：输入命令后按空格,会自动显示该命令的子命令。例如输入 `/schedule ` 会显示 `list`, `add`, `remove`, `enable`, `disable` 等子命令。

#### 会话管理命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/clear` 或 `/cls` | 清空当前对话历史 | `/clear` |
| `/compact` | 压缩上下文,优化 Token 使用 | `/compact` |
| `/export` | 导出当前会话记录 | `/export session.md` |
| `/resume` | 恢复/管理会话(list/del/search/fork) | `/resume list` |
| `/resume list` | 列出所有历史会话 | `/resume list` |
| `/resume del <id>` | 删除指定会话 | `/resume del abc123` |
| `/resume search <关键词>` | 搜索历史会话内容 | `/resume search python` |
| `/resume fork [id] [idx]` | 分叉会话(从指定消息处创建新会话) | `/resume fork abc123 5` |

#### Git 检查点命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/checkpoint`（别名 `/cp`） | 列出所有检查点 | `/checkpoint` |
| `/checkpoint diff` | 查看当前未提交的变更 | `/checkpoint diff` |
| `/checkpoint diff <序号>` | 当前修改 vs 指定检查点 | `/checkpoint diff 0` |
| `/checkpoint diff <a> <b>` | 比较两个检查点 | `/checkpoint diff 0 2` |
| `/checkpoint pop` | 恢复最近的检查点并删除 | `/checkpoint pop` |
| `/checkpoint pop <序号>` | 恢复指定检查点并删除 | `/checkpoint pop 2` |
| `/checkpoint apply` | 恢复最近的检查点(保留) | `/checkpoint apply` |
| `/checkpoint apply <序号>` | 恢复指定检查点(保留) | `/checkpoint apply 2` |
| `/checkpoint delete <序号>` | 删除指定检查点 | `/checkpoint delete 2` |
| `/checkpoint <序号>` | 恢复指定检查点(保留) | `/checkpoint 3` |
| `/undo` | 撤销 AI 的文件编辑,恢复到检查点(保留) | `/undo`、`/undo 2` |

> 💡 检查点在每个 assistant turn 开始前自动创建,支持两种模式：git 仓库使用 `git stash` 实现,非 git 目录自动降级为文件快照模式。智能处理 .gitignore 规则,不污染 git 历史。

#### 模型配置命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/model` | 查看或切换模型,显示能力标签和价格,支持设置为不同角色 | `/model gpt-4o` |
| `/model <关键词>` | 模糊搜索模型(支持 provider 前缀过滤) | `/model gpt-4o` |
| `/model <provider>/<模型>` | 直接切换到指定模型 | `/model default/gpt-4o` |

> 💡 `/model` 列表会显示每个模型的能力标签: 👁 图片输入、🎬 视频输入、🎵 音频输入、🔧 工具调用,以及输入/输出价格(每百万 token)。选择模型后,系统会根据模型能力动态显示可用角色选项(主模型/mini/多模态/顾问/TTS/ASR/图片生成/Embedding)。

#### 工作目录命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/cwd`、`/cd` 或 `/pwd` | 查看或切换工作目录 | `/cd /path/to/project` |

#### 技能系统命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/skills` | 列出所有可用技能 | `/skills` |

#### 记忆系统命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/memory list` | 列出所有记忆条目 | `/memory list` |
| `/memory search <关键词>` | 搜索相关记忆 | `/memory search 代码风格` |
| `/memory delete <名称>` | 删除指定记忆 | `/memory delete 用户偏好-主题` |
| `/memory-organize` | 记忆管家:整理记忆、挖掘会话 | `/memory-organize all` |
| `/skill-forge` | 技能锻造:创建和优化自定义 Skill | `/skill-forge all` |

#### MCP 管理命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/mcp list` | 列出已配置的 MCP 服务器 | `/mcp list` |
| `/mcp add <名称> [JSON]` | 添加 MCP 服务器 | `/mcp add fs {"transport":"stdio",...}` |
| `/mcp remove <名称>` | 删除 MCP 服务器 | `/mcp remove fs` |
| `/mcp enable <名称>` | 启用 MCP 服务器 | `/mcp enable fs` |
| `/mcp disable <名称>` | 禁用 MCP 服务器 | `/mcp disable fs` |
| `/mcp tools` | 列出可用的 MCP 工具 | `/mcp tools` |

#### 定时任务命令 ⏰

| 命令 | 说明 | 示例 |
|------|------|------|
| `/schedule list` | 列出所有定时任务 | `/schedule list` |
| `/schedule add <调度> <动作> [名称]` | 创建定时任务(ID 自动生成) | `/schedule add "0 * * * *" "shell: git status"` |
| `/schedule remove <id>` | 删除定时任务 | `/schedule remove abc12345` |
| `/schedule enable <id>` | 启用定时任务 | `/schedule enable abc12345` |
| `/schedule disable <id>` | 禁用定时任务 | `/schedule disable abc12345` |

**调度格式(Cron 表达式)：**
- `分 时 日 月 周` - 标准 5 字段 Cron 格式,最小粒度 1 分钟
- 示例: `0 * * * *` 每小时、`*/5 * * * *` 每 5 分钟、`0 9 * * *` 每天 9:00、`0 9 * * 1-5` 工作日 9:00

**动作类型：**
- `shell: <命令>` - 执行 Shell 命令
- `agent: <消息>` - 发送给 AI 处理
- `agent:<类型>: <消息>` - 指定子代理类型(如 `agent:coder: 重构代码`)
- `py: <Python代码>` - 在当前 Python 环境执行代码
- `monitor: <命令> → agent[:<类型>]: <消息>` - 先执行 shell 命令,退出码非零时触发 agent

**会话关联：**
- 定时任务创建时自动关联当前会话,执行时复用该会话的 agent
- 跨会话执行:TUI 模式下如果当前会话与任务会话不匹配,任务在后台执行
- WebUI 模式下非活跃会话的消息会触发注意力指示器和 toast 通知

**监控任务(Monitor)：**
- 周期执行 shell 命令,退出码 = 0 表示正常(不触发 agent),非零表示需要处理
- 适用于健康检查、异常检测等"没问题就不处理"的场景
- 每个任务分配独立工作目录,检查脚本放在任务目录中
- 通过 `schedule_create` 的 `type: "monitor"` action 创建,用 `schedule_update` 修改

#### 后台任务命令 🔄

| 命令 | 说明 | 示例 |
|------|------|------|
| `/task` 或 `/task list` | 列出所有后台任务 | `/task` |
| `/task output <id> [N]` | 获取任务输出(默认 50 行) | `/task output abc123 100` |
| `/task stop <id>` | 停止指定任务 | `/task stop abc123` |
| `/task matched <id>` | 获取监控匹配结果 | `/task matched abc123` |

**使用示例：**
```
# 启动后台任务（通过 AI 调用工具）
monitor_start("cargo build", watch_pattern="Finished", name="项目构建")
monitor_start("npm run dev", name="开发服务器")

# 查看任务
/task                          # 列出所有任务
/task output abc123            # 获取输出
/task stop abc123              # 停止任务
```

#### 工作空间命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/add_dir` 或 `/add-dir` | 管理额外工作空间目录(仅当前会话有效) | `/add_dir /path/to/project` |

#### 项目初始化命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/init` | 扫描当前项目并自动生成/更新 CLAUDE.md | `/init` |

#### 上下文分析命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/context` | 显示详细的上下文使用分析(Token 分布、工具占用等) | `/context` |

#### 知识图谱命令 🗺️

| 命令 | 说明 | 示例 |
|------|------|------|
| `/kg` 或 `/knowledge` | 知识图谱命令别名,显示统计信息(用户级+项目级) | `/kg` |
| `/kg search <关键词>` | 搜索实体 | `/kg search Python` |
| `/kg list [类型]` | 列出实体(可选类型过滤) | `/kg list person` |
| `/kg export html\|json\|markdown [user\|project]` | 导出图谱 | `/kg export html` |
| `/kg clear [user\|project]` | 清空图谱(需确认) | `/kg clear project` |

#### A2A 远程代理命令 🌉

| 命令 | 说明 | 示例 |
|------|------|------|
| `/a2a` 或 `/a2a status` | 查看 A2A 服务状态 | `/a2a` |
| `/a2a start [token]` | 启用 A2A 端点(仅 WebUI 模式) | `/a2a start` |
| `/a2a stop` | 禁用 A2A 端点 | `/a2a stop` |
| `/a2a add <名称> <URL> [token]` | 添加远程 A2A Agent | `/a2a add coder http://192.168.1.100:8080/a2a` |
| `/a2a list` | 列出已配置的远程 Agent | `/a2a list` |
| `/a2a remove <名称>` | 删除远程 Agent | `/a2a remove coder` |
| `/a2a test <名称>` | 测试远程 Agent 连接 | `/a2a test coder` |

> 💡 A2A 服务复用 WebUI 的 HTTP 端口,需要先以 `--mode webui` 启动。启动后会自动生成 Bearer Token,其他 UniClaw 实例可通过 `a2a_add_agent` 工具连接。

#### 其他命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `/help` | 显示所有可用的斜杠命令帮助信息 | `/help` |
| `/<命令> help` | 查看特定命令的详细说明 | `/memory help`、`/mcp help` |
| `/usage` | 查看 Token 使用统计(输入/输出 tokens、工具调用次数) | `/usage` |
| `/cost` | 查看费用统计(按模型计费,价格来自 OpenRouter) | `/cost` |
| `/doctor` | 环境诊断(12 项检查) | `/doctor` |
| `/permissions list` | 查看所有持久化权限规则 | `/permissions list` |
| `/permissions remove <类型> <模式>` | 删除权限规则 | `/permissions remove bash "git commit"` |
| `/task` | 管理后台任务(list/output/stop/matched) | `/task`、`/task output abc123` |
| `/goal` | 设置目标停止条件,agent 停止时用 judge 模型评估是否达成 | `/goal 完成所有单元测试` |
| `/goal clear` | 清除当前目标 | `/goal clear` |
| `/goal status` | 查看重入次数和目标状态 | `/goal status` |
| `/undo` | 撤销 AI 的文件编辑,恢复到检查点(支持序号) | `/undo`、`/undo 2` |
| `/cp` | `/checkpoint` 的别名 | `/cp diff` |
| `/btw` | 附带信息,不影响当前对话流程 | `/btw 这段代码很好` |
| `/name` | 为当前会话命名 | `/name 重构讨论` |
| `/overseer` | 监工模式,自动审核任务执行质量 | `/overseer` |
| `/voice` | 切换语音模式(需配置 TTS) | `/voice on`、`/voice off` |
| `/cu` | 切换 Computer Use 模式 | `/cu on`、`/cu off` |
| `/explain` | 工具解释模式,AI 调用工具前解释原因 | `/explain on`、`/explain off`、`/explain Bash Read` |
| `/exit` 或 `/quit` | 退出程序 | `/exit` |

> 💡 **提示**: 所有命令在控制台和微信模式下都可用。输入 `/help` 可查看完整的命令列表,输入 `/<命令> help` 可查看特定命令的详细说明(如 `/memory help`)。未匹配到内置命令时,会自动回退到技能查找系统。

### 快捷命令

在 REPL 中输入 `!` 开头的命令可直接执行 Shell 命令：

```
D:\code\learn\UniClaw  5.23% » !python --version
  $ python --version
Python 3.14.0
```

## 🌐 WebUI 模式

UniClaw 提供基于 WebSocket 的 Web 用户界面,支持在浏览器中与 AI 对话。

### 启动 WebUI

```bash
# 仅本机访问(默认,HTTPS 已启用)
uv run uniclaw --mode webui

# 局域网可访问
uv run uniclaw --mode webui --host 0.0.0.0

# 禁用 HTTPS(使用 HTTP)
uv run uniclaw --mode webui --host 0.0.0.0 --no-ssl

# 指定域名(用于 HTTPS 证书)
uv run uniclaw --mode webui --host 0.0.0.0 --domain uniclaw.example.com

# 或使用安装后的命令
uniclaw --mode webui
```

### 功能特性

- ✅ **浏览器对话** — 在 Web 界面中与 AI 进行交互
- ✅ **实时流式响应** — 基于 WebSocket 的实时消息推送,支持自动重连
- ✅ **WebSocket 会话任务管理** — 每个对话会话作为独立任务管理,支持并发会话
- ✅ **子代理创建 API** — 通过 REST API 创建和管理子代理,支持多智能体协作
- ✅ **微信 Bot 管理** — 在 WebUI 中直接管理微信 Bot 账号,支持异步登录
- ✅ **可信 IP 免登录** — 配置 `trusted_ips` 后指定 IP 跳过登录认证,适合家庭/办公网络
- ✅ **HTTPS 支持** — 默认启用 HTTPS(自动生成自签名证书),`--no-ssl` 可禁用;`--domain` 指定域名
- ✅ **IPv6 支持** — 支持 IPv6 监听地址(如 `::`)和访问
- ✅ **局域网共享** — 通过 `--host 0.0.0.0` 让局域网内其他设备访问
- ✅ **自定义系统提示词** — 每个会话可设置独立的系统提示词,AI 可优化提示词内容
- ✅ **工具调用可视化** — 展示工具调用过程、结果和参数信息
- ✅ **工具解释模式** — 开启后工具调用前显示 AI 的调用原因说明
- ✅ **Git 侧边栏** — 文件状态查看、暂存/取消暂存、提交操作,支持 AI 自动生成 commit message
- ✅ **检查点管理** — 侧边栏 Git 面板中查看检查点列表、diff 对比(支持未跟踪文件)
- ✅ **会话分叉** — 从历史消息处分叉创建新会话,保留上下文继续对话
- ✅ **历史消息检索** — 搜索和查看完整历史消息,支持在 TUI/WebUI 中切换活跃/归档视图
- ✅ **消息导航条** — 右侧消息导航条快速跳转到指定消息
- ✅ **Token 用量显示** — 每条消息显示 Token 消耗量和处理时长
- ✅ **TodoList 同步** — 任务清单实时同步到 WebUI 界面
- ✅ **权限模式切换** — WebUI 中直接切换权限模式(auto/manual/accept-all/plan)
- ✅ **输入对话框** — 权限请求时弹出输入框,支持倒计时和取消按钮
- ✅ **会话级配置** — 带 `session_id` 保存时,当前会话内存配置立即生效,同时写入全局配置
- ✅ **拖拽上传** — 支持拖拽文件到聊天区域上传,自动识别文件类型并添加为附件
- ✅ **多媒体附件** — 支持图片、音频、视频等多媒体文件作为附件发送给 AI 分析
- ✅ **文件大小限制** — 自动限制上传文件大小,防止过大的文件影响性能
- ✅ **登录自动跳转** — 已登录用户打开页面时自动跳转到聊天界面
- ✅ **进程热更新重启** — AI 可调用 `restart_agent` 工具重启自身进程,优雅保存会话后拉起新进程,重启后自动恢复会话并唤醒 agent;前端实时提示服务器重启状态
- ✅ **跨会话通知** — 非活跃会话的消息触发注意力指示器和 toast 通知
- ✅ **移动端响应式设计** — 完美适配手机和平板设备,支持触摸手势操作
- ✅ **侧边栏树形分类** — 会话列表按项目分组显示,支持折叠/展开,便于管理多项目会话
- ✅ **登录 IP 限流** — 登录接口支持 IP 限流防护,防止暴力破解攻击
- ✅ **主题定制** — 内置 uniclaw-theme 技能,支持自定义 WebUI 主题颜色和样式
- ✅ **精美 UI 样式** — 基础 CSS 样式和动画效果,提升视觉体验

---

## 💬 微信机器人集成

UniClaw 支持通过 iLink Bot 协议接入微信,让您可以通过微信与 AI 助手进行交互。支持多账号管理、图片识别和实时消息处理。

### 启动微信机器人

微信机器人功能已集成在 WebUI 模式中。启动 WebUI 后，在侧边栏的微信区域管理账号：

```bash
uv run uniclaw --mode webui
```

### 基本操作

在 WebUI 侧边栏中点击"添加微信账号"按钮，扫描二维码登录即可。

**管理方式：**

微信机器人已完全集成到 WebUI 中，所有操作通过 WebUI 侧边栏完成：
- **添加账号**: 点击侧边栏微信区域的"添加"按钮
- **移除账号**: 点击账号旁的"移除"按钮
- **查看状态**: 侧边栏实时显示账号在线状态
- **消息监听**: 已登录账号自动启动消息监听

### 微信中的使用方法

在微信中与机器人对话时,支持以下功能：

#### 1. 智能对话

直接发送文本消息,AI 会自动理解并回复：

```
你好,请帮我写一个 Python 函数来计算斐波那契数列
```

#### 2. Shell 命令执行

使用 `!` 前缀执行 Shell 命令：

```
!python --version
!ls -la
!git status
```

#### 3. 斜杠命令

使用 `/` 前缀执行内置命令(如查看帮助、切换权限模式等)：

```
/help
/permission auto
```

#### 4. 图片识别

直接发送图片,AI 可以识别图片内容并进行对话。支持多张图片同时发送。

### 功能特性

- ✅ **多账号支持** - 可以同时管理多个微信账号
- ✅ **异步登录** - 微信 Bot 登录过程异步化,不阻塞主线程
- ✅ **WebUI 集成** - 在 WebUI 中直接管理微信 Bot 账号(添加/移除/查看状态)
- ✅ **自动启动** - 已登录账号会自动启动消息监听
- ✅ **图片处理** - 支持接收和识别图片内容
- ✅ **权限请求交互** - 微信模式下支持权限请求,用户可通过微信确认操作
- ✅ **实时反馈** - 工具调用时会发送进度通知
- ✅ **权限控制** - 微信模式默认使用 ACCEPT_ALL 权限模式,无需手动确认
- ✅ **上下文隔离** - 每个用户拥有独立的对话历史和状态
- ✅ **会话自动清理** - 微信模式下的会话自动管理和清理

### 数据存储

微信机器人的数据存储在项目目录下的 `wechat/` 文件夹中,包括：

- 账号登录信息
- 会话历史记录
- 临时文件缓存

### 注意事项

⚠️ **安全提示**：
- 微信模式下所有操作自动批准,请谨慎使用
- 建议在可信环境中使用此功能
- 不要在不信任的网络中暴露机器人接口

⚠️ **性能考虑**：
- 多用户并发时会创建独立的 Agent 实例
- 每个用户的对话历史独立保存
- 长时间运行的会话建议定期清理

---

## 🛠️ 工具系统

UniClaw 提供了丰富的内置工具,AI 助手可以自动调用这些工具完成任务。工具分为**核心工具**(始终加载)和**扩展工具**(通过 `search_tools` 按需发现),详见 [工具注册表系统](#工具注册表系统)。此外,用户可通过 [插件系统](#-插件系统) 自定义外部工具插件。

工具基础设施：
- **`base.py`** — 自定义 `@tool` 装饰器,自动生成 OpenAI function calling schema,自动排除 `config` 注入参数
- **`registry.py`** — 工具注册表,BM25 搜索索引,核心/扩展工具分层管理

#### 文件系统工具

- **Read** - 读取文件内容(支持指定行范围和偏移量)
- **Write** - 写入或创建文件(自动创建父目录,返回差异报告)
- **Edit** - 精确替换文件中的字符串(支持统一差异格式)
- **Glob** - 根据通配符模式搜索文件(如 `*.py`, `**/*.txt`)
- **ConvertToMarkdown** - 将文档转换为 Markdown 格式并保存为 .md 文件(支持 PDF、DOCX、PPTX、XLSX 等,基于 markitdown)

#### Shell 工具

- **Bash** - 执行 Shell 命令(支持超时控制和上限校验,跨平台兼容,支持流式输出)
- **Grep** - 在文件中搜索文本模式(优先使用 ripgrep,支持正则表达式)
- **search_files_with_everything** - 使用 Everything 搜索引擎快速搜索文件名(仅 Windows,需安装 Everything es.exe)
  - 支持通配符(`*`、`?`)和逻辑运算符(AND/OR/NOT)
  - `max_results`: 最大返回结果数(0 为不限制)
  - `path_filter`: 路径过滤器,限定搜索范围到特定目录

> 💡 **流式输出**: Bash 工具执行过程中会实时推送输出到前端(WebUI),无需等待命令完成即可看到进度。

> 💡 **Windows 用户提示**: 在 Windows 系统上,如果检测到 Git Bash,Bash 工具会自动使用 Git Bash 执行命令,提供更好的 Unix 命令兼容性。建议安装 [Git for Windows](https://git-scm.com/download/win) 以获得最佳的 Shell 体验。Bash 工具还会自动修正 Windows 环境下 `nul` 重定向为 `/dev/null`,避免 Git Bash 兼容性问题。

> ⚠️ **注意事项**: 某些命令可能触发分页器(如 `git log`、`man` 等),导致进程阻塞等待用户交互。解决方法：
> - Git 命令添加 `--no-pager` 参数：`git --no-pager log`
> - man 命令设置环境变量：`MANPAGER=cat man ls`
> - 或使用其他非交互式替代方案

#### 实用工具

- **sleep_timer** - 异步等待指定秒数后唤醒 AI 继续工作(1-3600秒,带参数校验)
  - 函数立即返回,不阻塞主线程
  - 可设置等待原因描述,便于追踪
  - 适用于需要延时执行的场景(如等待服务启动、API 限流等)
- **wait** - 同步等待指定秒数(简单版本,适合短时间等待)

#### 多媒体工具

- **ReadMedia** - 读取媒体文件(图片/音频/视频)并以多模态方式发送给 LLM 进行分析,支持本地路径和网络 URL
- **GenerateImage** - AI 文生图工具(需配置 `image_model`),支持自定义尺寸(如 `"2K"`, `"1024x1024"`),可保存为文件或直接返回多模态数据供 AI 分析

#### Docker 沙箱工具

完整的 Docker 容器与镜像生命周期管理(需要 Docker 环境)：

**容器管理：**
- **DockerCreate** - 创建持久容器,支持 `-v` 目录挂载、`-p` 端口映射、自定义内存/CPU 限制
- **DockerExec** - 在已有容器中执行命令,容器状态(安装的包、创建的文件)在多次调用间保持
- **DockerStart** - 启动已停止的容器,容器内文件系统状态保持
- **DockerStop** - 停止运行中的容器
- **DockerRemove** - 停止并删除容器,释放资源
- **DockerList** - 列出所有管理的容器及状态

**镜像管理：**
- **DockerPull** - 拉取 Docker 镜像
- **DockerSearch** - 在 Docker Hub 搜索镜像(返回镜像名、描述、星数、是否官方)
- **DockerImages** - 列出本地所有镜像
- **DockerRemoveImage** - 删除本地镜像
- **DockerBuild** - 从 Dockerfile 构建镜像

**安全限制：** 默认禁止网络访问、内存限制 256MB、CPU 限制 1 核、禁止提权

> ⚠️ **环境依赖**：Grep 需要 ripgrep 或 grep；search_files_with_everything 需要 Everything (es.exe)；Docker 沙箱工具需要 Docker；IPython 工具需要 `ipykernel`(已包含在项目依赖中)。启动时会自动检测环境,不可用的工具会被禁用并提示原因。

#### IPython 工具

提供持久化 Python 交互式执行环境,变量在多次调用间保持,支持 IPython 魔术命令：

- **ipython_start** - 启动 IPython 内核(支持多内核并行,通过 kernel_id 区分)
- **ipython_execute** - 在内核中执行 Python 代码(支持魔术命令如 `%timeit`、`%matplotlib`、`%pip` 等,自动启动默认内核;内核执行串行化,同一时间只执行一个代码块,避免并发读取 iopub 队列导致消息错乱)
- **ipython_inspect** - 检查变量详细信息(类型、值、长度、形状、文档字符串,对 NameError 给出更明确的未定义变量提示)
- **ipython_vars** - 列出当前所有用户变量(支持按类型过滤)
- **ipython_history** - 获取代码执行历史
- **ipython_stop** - 停止内核并释放资源
- **ipython_list_kernels** - 列出所有运行中的内核

> 💡 **使用场景**：IPython 工具提供持久化执行环境,适合需要跨多次调用保持状态的场景,如数据分析、变量调试、包管理等。内核启动失败时会自动输出详细的进程状态和诊断信息(写入诊断文件),便于排查问题。

> ⚠️ **资源管理**：IPython 内核会持续占用系统资源,用完后请调用 `ipython_stop` 关闭。

#### Web 工具

- **webFetch** - 抓取网页内容并提取纯文本(自动清理 HTML 标签)
  - 基于 token 数截断(`max_tokens` 参数),返回统一的元数据头部(HTTP 状态码、内容类型、页面标题)
  - 支持 `raw=True` 返回完整 HTML 源码(不清理标签),保留段落结构
- **webSearch** - 并发搜索多个平台并合并结果,默认只搜 Exa(通用网页搜索),支持按需指定平台:GitHub/arXiv/Stack Overflow/Hacker News/B站/Reddit/Google News/OpenAlex/Polymarket/SEC EDGAR/HuggingFace/alphaXiv/Exa/Bing/DuckDuckGo(`platforms="all"` 全平台)
  - 各平台独立并发搜索,失败平台的错误信息附在末尾,不影响其他平台结果
  - Exa 需配置 `EXA_API_KEY`,Bing 国内直连(无需代理),DuckDuckGo 需代理
  - 结果超过阈值自动用 LLM 重排,失败平台的错误信息会附在末尾
  - 支持 `time_range` 时间范围过滤(预设如 "7d"/"1y" 或 ISO 区间)
  - 支持 `intent` 搜索意图描述,供 LLM 重排时判断相关度参考
  - 自动缓存搜索结果(128条,10分钟过期)
  - GitHub 支持 repositories/code/issues/users 类型和 stars/forks/updated 排序
  - 支持 `GITHUB_TOKEN` 环境变量提高 GitHub API 速率限制

#### 下载工具 ⬇️

HTTP/HTTPS 文件下载引擎,支持多协程并发、断点续传、代理、重试和文件校验：

- **http_download** - 下载文件(支持同步和异步两种模式)
  - **多协程并发**: 默认 10 个协程同时下载,大幅提速大文件下载
  - **断点续传**: 自动记录下载进度,中断后可继续下载,无需重新开始
  - **代理支持**: 支持 HTTP/HTTPS 代理
  - **自动重试**: 失败自动重试(默认 3 次),支持指数退避
  - **文件校验**: 支持 MD5/SHA256 校验,确保文件完整性
  - **Cookie 支持**: 支持需要登录的下载场景
  - **同步模式**: 流式显示进度,下载完成返回结果
  - **异步模式**: 立即返回任务 ID,后台执行,完成/失败时自动唤醒 AI
  - **智能取消**: 同步下载时用户取消自动转为后台继续下载
- **http_download_status** - 查看或管理异步下载任务
  - 查看任务进度(下载速度、已下载大小、剩余时间)
  - 暂停/恢复/取消下载任务
  - 列出所有下载任务
- **http_download_remove** - 删除指定的下载任务记录(清理已完成或已取消的任务)

#### M3U8/HLS 流下载 🎞️

M3U8/HLS 流媒体下载引擎,与 HTTP 下载管理器分离,独立管理异步任务：

- **m3u8_download** - 下载 M3U8/HLS 视频流
  - **多分辨率选择**: 播放列表为主播放列表(含多个 `#EXT-X-STREAM-INF`)时自动列出可用分辨率,通过 `variant` 参数指定清晰度(序号或分辨率);媒体播放列表(直接含 TS 分片)无需 variant 直接下载
  - **多协程并发**: 默认 8 个协程并发下载分片,大幅提速
  - **AES-128 解密**: 自动解析并解密加密分片
  - **断点续传**: 已下载分片自动跳过,中断后可继续
  - **自定义请求头**: 支持代理、Cookie 等登录场景
  - **异步模式**: `async_mode=True` 时后台下载并立即返回任务 ID
  - **自动合并**: 下载完成后自动合并为单个 TS 文件
- **m3u8_download_status** - 查看或管理 m3u8 异步下载任务(暂停/恢复/取消,或列出所有任务)
- **m3u8_download_remove** - 删除已完成的 M3U8 下载任务,释放内存

**使用示例**:
```
# AI 会自动调用工具下载文件
http_download(url="https://example.com/file.zip")

# 指定保存路径
http_download(url="https://example.com/file.zip", save_path="downloads/file.zip")

# 异步下载(后台执行)
http_download(url="https://example.com/large.iso", async_mode=True)
# 返回任务 ID,可稍后查询进度
http_download_status(task_id="xxx")

# 带校验的下载
http_download(
    url="https://example.com/file.zip",
    checksum_algorithm="sha256",
    checksum_value="abc123..."
)
```

#### 浏览器自动化工具 🌍

基于 Playwright 的浏览器控制,支持多标签页管理和持久化浏览器上下文：

**浏览器生命周期：**
- **browser_start** - 启动浏览器(Chromium/Firefox/WebKit)
- **browser_close** - 关闭浏览器

**页面导航：**
- **browser_navigate** - 导航到指定 URL
- **browser_back** - 后退
- **browser_forward** - 前进
- **browser_reload** - 刷新页面
- **browser_get_url** - 获取当前页面 URL
- **browser_get_title** - 获取页面标题

**元素交互：**
- **browser_click** - 点击页面元素(支持 CSS/文本/角色选择器)
- **browser_dblclick** - 双击元素
- **browser_hover** - 悬停在元素上
- **browser_type** - 在输入框中输入文本(可选 `clear` 参数先清空,默认 True)
- **browser_insert_text** - 插入文本(触发 input 事件)
- **browser_check** - 勾选/取消勾选复选框
- **browser_select_option** - 选择下拉框选项
- **browser_drag** - 拖拽元素
- **browser_focus** - 聚焦到元素
- **browser_press_key** - 按下键盘按键
- **browser_key_down** - 按键按下
- **browser_key_up** - 按键释放
- **browser_keyboard_type** - 键盘输入文本

**页面信息获取：**
- **browser_screenshot** - 截取页面截图(全屏或指定区域)
- **browser_get_text** - 获取页面文本内容
- **browser_get_html** - 获取页面 HTML
- **browser_get_attribute** - 获取元素属性值
- **browser_get_value** - 获取输入框的值
- **browser_get_elements** - 获取匹配选择器的所有元素
- **browser_get_count** - 获取匹配元素的数量
- **browser_get_box** - 获取元素的位置和尺寸
- **browser_get_styles** - 获取元素的计算样式

**页面执行：**
- **browser_evaluate** - 执行 JavaScript 代码
- **browser_wait** - 等待指定时间或条件
- **browser_scroll** - 页面滚动
- **browser_scroll_into_view** - 滚动元素到可见区域
- **browser_toggle_mode** - 切换浏览模式

**多标签页管理：**
- **browser_new_page** - 新建标签页
- **browser_close_page** - 关闭标签页
- **browser_switch_page** - 切换到指定标签页
- **browser_list_pages** - 列出所有标签页

#### 记忆系统工具 🧠

- **memory_save** - 保存持久化记忆(支持用户偏好、项目信息、反馈等)
- **memory_delete** - 删除指定的记忆条目
- **memory_search** - 智能搜索相关记忆(SQLite FTS5 全文检索,支持增量索引和相对分数过滤)
- **memory_list** - 列出所有可用的记忆条目

> 💡 **提示**: 记忆系统会自动在对话中加载相关记忆,帮助 AI 更好地理解上下文和用户偏好。

#### 多智能体工具 👥

- **subagent_create** - 创建并启动子智能体任务(支持同步/异步模式和 worktree 隔离)
- **subagent_send_message** - 向指定智能体发送消息进行通信
- **subagent_close** - 关闭后台子智能体释放资源
- **subagent_check_result** - 检查子智能体的执行结果和状态(支持增量读取)
- **subagent_list_tasks** - 列出所有子智能体任务的当前状态
- **subagent_discuss** - 启动多个智能体之间的多轮讨论协作
- **subagent_list_definitions** - 查看所有可用的智能体类型定义
- **subagent_get_definition** - 查看指定子智能体类型的详细定义(系统提示词、工具列表等)

> 💡 **提示**: 多智能体系统采用全异步架构,允许为不同任务创建专门的助手,实现更精细的任务分工。支持智能体间的异步通信和结果传递,可通过 `keep_alive` 模式保持智能体持续运行并接收新指令。支持 worktree 隔离模式(`isolation=True`),子智能体在独立的 git 分支上工作,避免文件冲突。支持事件继承(`inherit_events=True`),子智能体的工具调用、思考过程等事件会自动广播到父级队列,前端可实时显示执行进度。子智能体完成后会自动通知父智能体,支持唤醒代理机制确保父任务及时处理子任务结果。

#### 技能系统

- **skill_suggest** - 根据当前任务智能推荐合适的技能,返回技能总数和推荐数量
- **skill_read** - 读取指定技能的详细内容

**内置技能**:
- `code-review` (`/code-review`, `/review`) — 多维度代码审查(安全性、正确性、性能、代码质量、可读性)
- `commit` (`/commit`) — AI 生成 Conventional Commits 格式的 commit message 并自动提交
- `pr-create` (`/pr-create`, `/pr`) — AI 生成 PR 标题和描述,调用 gh CLI 创建 GitHub PR
- `memory-organize` (`/memory-organize`, `/organize-memory`, `/memory-clean`) — 记忆管家:整理记忆系统,从会话中提取有价值内容
- `skill-forge` (`/skill-forge`, `/forge-skill`, `技能锻造`) — 技能锻造:创建新 Skill 和优化已有的自定义 Skill(只操作自建 Skill)
- `tool-plugin-forge` (`/tool-plugin-forge`, `/forge-tool-plugin`, `+tool-plugin`) — 工具插件锻造:按用户需求自动生成 UniClaw 外部工具插件,写入 `~/.UniClaw/plugins/tools/`,支持多文件拆分、生命周期钩子、耗时任务 async + wake_agent 唤醒,生成后即时验证可加载
- `uniclaw-theme` (`/uniclaw-theme`, `/theme`) — 主题定制:自定义 WebUI 主题颜色、字体和样式

**技能文件搜索路径**:
技能系统支持从多个常见目录中自动加载技能文件：

项目级:
- `skills/` - 通用技能目录
- `.claude/skills/` - Claude 技能目录
- `.codex/skills/` - Codex 技能目录
- `.agents/skills/` - Agents 技能目录

用户级(全局):
- `~/.claude/skills/`
- `~/.codex/skills/`
- `~/.agents/skills/`

> 💡 **提示**: 技能系统会自动检测项目中的技能目录结构,并加载相应的技能定义文件。技能文件使用 Markdown 格式,通过 YAML frontmatter 定义元数据。

#### MCP 工具 🔌

通过 MCP (Model Context Protocol) 连接外部工具服务,支持 stdio、sse、streamable_http、websocket 四种协议。MCP 命令管理已转换为异步实现,提升响应性能。

使用 `/mcp` 命令管理 MCP 服务器：

| 命令 | 说明 |
|------|------|
| `/mcp list` | 列出已配置的 MCP 服务器 |
| `/mcp add <name> [json]` | 添加 MCP 服务器(支持 JSON 配置) |
| `/mcp remove <name>` | 删除 MCP 服务器 |
| `/mcp enable/disable <name>` | 启用/禁用服务器 |
| `/mcp tools` | 列出可用的 MCP 工具 |

配置文件位置：`~/.UniClaw/mcp.json`

#### A2A 远程代理工具 🌉

基于 Agent2Agent 协议的远程代理协作工具,支持调用外部 A2A Agent 和将 UniClaw 暴露为 A2A 端点：

**客户端工具（调用外部 Agent）：**
- **a2a_send_task** - 向外部 A2A Agent 发送任务并同步等待回复(支持多轮对话)
- **a2a_submit_task** - 异步提交远程任务,立即返回 task_id(支持多轮对话)
- **a2a_get_task** - 查询异步任务状态和结果
- **a2a_cancel_task** - 取消正在执行的远程任务
- **a2a_add_agent** - 添加或更新外部 A2A Agent 配置
- **a2a_list_agents** - 列出已配置的外部 A2A Agent

**服务端（暴露 UniClaw 为 A2A 端点）：**
- 通过 `/a2a start` 启用,挂载到 WebUI 的 `/a2a` 路径
- 支持 JSON-RPC 2.0 协议(`message/send`、`tasks/get`、`tasks/cancel`)
- Agent Card 暴露在 `/.well-known/agent-card.json`
- 每个 `context_id` 维护独立的对话上下文

> 💡 **提示**: 使用 `/a2a` 命令管理 A2A 服务。A2A 配置持久化在 `~/.UniClaw/a2a/a2a.json`。服务启动后自动生成 Bearer Token 用于认证。

#### 计算机控制工具 🖥️

- **screenshot** - 截取屏幕截图(全屏或指定区域)
- **mouse_move** - 移动鼠标到指定坐标
- **mouse_click** - 鼠标点击(支持左键、右键、中键)
- **mouse_double_click** - 鼠标双击
- **mouse_drag** - 鼠标拖拽操作
- **mouse_scroll** - 鼠标滚轮滚动
- **keyboard_type** - 输入文本字符串
- **keyboard_type_unicode** - 输入 Unicode 文本
- **keyboard_press** - 按下并释放按键
- **keyboard_key_down** / **keyboard_key_up** - 按键按下/释放控制
- **wait** - 等待指定时间
- **locate_on_screen** - 在屏幕上定位图像位置
- **cu_get_elements** - 获取当前屏幕上的 UI 元素列表(基于 accessibility 树)
- **cu_find_element** - 查找指定的 UI 元素(支持名称、角色、值等条件)
- **cu_interact** - 操作 UI 元素(点击、聚焦、调用等)

> 💡 **提示**: 计算机控制功能可通过全局热键 **Ctrl+U** 切换启用/禁用。依赖 `pyautogui`、`pynput`、`mss`、`pillow`。Windows 上额外依赖 `uiautomation`。

#### 任务清单工具 📋

- **todolist_create** - 创建任务清单(支持任务分解和状态管理)
- **todolist_update** - 更新任务状态(pending → in_progress → completed,全部完成后自动提示清理清单并汇报完成情况)
- **todolist_clear** - 清空任务清单
- **todolist_list** - 列出当前任务清单
- **todolist_cancel** - 取消任务清单

> 💡 **提示**: 任务清单支持自动进度管理,同一时间只有一个任务处于 `in_progress` 状态,完成后自动推进下一个。支持监工模式(`/overseer`),可自动审核任务执行质量。

#### 监控工具 🔄

- **monitor_start** - 启动后台进程(异步实现,支持可选的 watch_pattern 监控匹配)
  - **可视模式**: `visible=True` 时弹出真实控制台窗口(基于 ConPTY 伪终端),进程输出实时显示,用户可直接在窗口内输入与进程交互,适合需要登录账号、输入验证码、确认交互式安装提示等 AI 无法独立完成的步骤
- **monitor_stop** - 停止指定进程
- **monitor_list** - 列出所有后台进程(运行中的进程实时跟踪,已结束条目保留可查询)
- **monitor_output** - 获取进程输出
- **monitor_screen** - 获取控制台当前屏幕快照(类似 tmux capture-pane,含提示符同行、未回车键入内容、vim/htop 全屏界面;非可视模式退化为返回全部输出历史)
- **monitor_input** - 向进程发送输入
- **monitor_get_matched** - 获取监控匹配结果
- **monitor_update_pattern** - 动态修改监控匹配模式
- **monitor_clear** - 批量清理所有已结束的监控条目(运行中的进程保留)

#### Hook 系统工具 🪝

- **hook_docs** - 查看 Hook 系统的详细文档和使用说明
- **hook_read** - 读取当前 Hook 配置
- **hook_add** - 添加事件 Hook(执行 Shell 命令)
- **hook_remove** - 移除指定 Hook

支持的事件类型：
- `SessionStart` / `SessionEnd` - 会话开始/结束
- `PreToolUse` / `PostToolUse` - 工具调用前后
- `PreAssistant` - 助手回复前
- `PermissionRequest` / `PermissionResponse` - 权限请求/响应

#### 系统通知工具 🔔

- **push_notification** - 发送桌面通知(支持 Windows/macOS/Linux)
  - Windows: 使用 PowerShell Toast Notification
  - macOS: 使用 osascript
  - Linux: 使用 notify-send
  - 适用于任务完成、长时间运行后的结果提醒等场景

#### 语音合成工具 🔊

- **text_to_speech** - 文本转语音(需配置 `tts_model` 和 `audio`)
  - 支持风格标签: 在文本中嵌入 `(开心)你好` `(唱歌)歌词` 等风格控制
  - 支持基础情绪(开心/悲伤/愤怒)、复合情绪(怅然/欣慰/无奈)
  - 支持语调(温柔/高冷/活泼)、音色(磁性/醇厚/清亮)
  - 支持方言(东北话/四川话/粤语)和角色扮演
  - 支持音频标签: `[笑声]` `[呼吸]` `[停顿]` 等细粒度控制
  - 可保存为文件或直接播放

> 💡 **提示**: 语音模式可通过 `/voice on|off` 命令切换。配置 `tts_model` 和 `audio` 后自动启用。

#### 文件发送工具 📤

- **send_file** - 发送文件给用户
  - WebUI 模式: 文本文件直接在前端渲染 Markdown 预览(内容不进入模型上下文),其他文件生成临时下载链接(默认 30 分钟有效)
  - 微信模式: 通过 Bot 直接发送文件
  - Console 模式: 不支持(忽略)

#### 微信工具 💬

- **wechat_list_contacts** - 列出当前已登录的微信机器人名称
- **wechat_send_text** - 通过微信向当前对话用户发送文字消息
- **wechat_send_image** - 通过微信向当前对话用户发送图片(支持附带文字说明)
- **wechat_send_file** - 通过微信向当前对话用户发送文件(支持自定义文件名)

#### 知识图谱工具 🗺️

基于 SQLite 的知识图谱系统,支持实体和关系的管理、查询、可视化：

**实体管理：**
- **kg_add_entity** - 添加实体(支持类型、描述、属性、置信度)
- **kg_update_entity** - 更新实体属性
- **kg_delete_entity** - 删除实体(级联删除关系和别名)
- **kg_merge_entities** - 合并两个实体(关系、别名、属性转移)
- **kg_add_alias** - 为实体添加别名(用于实体消歧)

**关系管理：**
- **kg_add_relation** - 添加实体间关系
- **kg_delete_relation** - 删除关系

**查询工具：**
- **kg_get_entity** - 获取实体详细信息(含关系)
- **kg_search** - 智能搜索实体(FTS5 前缀匹配 + LIKE 子串回退的混合查询,支持中英文,覆盖实体别名表)
- **kg_neighbors** - 获取实体邻居(支持多跳遍历)
- **kg_path** - 查找两个实体之间的路径
- **kg_list** - 列出实体(支持类型过滤)
- **kg_stats** - 获取图谱统计信息

**高级功能：**
- **kg_extract** - 从文本或文件中自动提取实体和关系(使用 AI)
- **kg_export** - 导出图谱(JSON/Markdown/HTML 可视化)
- **kg_clear** - 清空图谱(需确认)

**作用域：**
- `user` - 用户级(跨项目共享,存储在 `~/.UniClaw/knowledge.db`)
- `project` - 项目级(默认,存储在 `.UniClaw/knowledge.db`)

> 💡 **提示**: 知识图谱支持自动提取功能,AI 可以分析文本或文件,自动识别实体和关系并添加到图谱中。使用 `/kg` 命令管理图谱。

#### RAG 检索增强工具 📚

基于向量数据库的文档检索增强生成(RAG)系统,支持文档导入、智能拆分和语义检索：

- **rag_ingest** - 读取文件或目录,递归拆分为文档块,生成 embedding 后存入向量数据库
  - 支持格式: txt/md/py/json/yaml/csv/html/pdf 等
  - 可配置文档块大小(`chunk_size`)和重叠度(`chunk_overlap`)
  - 支持用户级/项目级双层作用域
- **rag_search** - 在指定集合中语义检索,返回最相关的文档片段
  - 支持 `score_threshold` 过滤低相关度结果
  - 支持 `top_k` 控制返回数量
  - 多路召回(向量语义 + BM25 关键词)+ RRF 融合 + LLM 重排序
  - 支持 `intent` 搜索意图参数:传入非空意图描述时自动启用 LLM 重排序,提升相关性判断
- **rag_list_collections** - 列出所有 RAG 集合及其统计信息(文档数量、描述等)
- **rag_delete_collection** - 删除指定集合及其所有文档
- **rag_set_desc** - 设置或更新集合描述,便于后续检索时识别
- **rag_evaluate** - 评估 RAG 检索效果:对一组测试问题执行检索,输出 LLM Judge 相关性指标
  - `use_llm_judge=True` 时用 LLM 评估每个检索结果与查询的相关性,无需人工标注
  - 输出 LLM Judge 平均分和 Context Precision@k
  - 支持 `rerank`(LLM 重排序)和 `use_bm25`(BM25 多路召回)开关

**作用域：**
- `user` - 用户级(跨项目共享)
- `project` - 项目级(默认)

> 💡 **提示**: RAG 系统适合对大量文档进行语义检索。先用 `rag_ingest` 导入文档,再用 `rag_search` 检索相关内容。每个集合可独立管理,适合按主题或项目组织文档。

#### 顾问模型工具 🎓

- **advisor_list** - 列出已配置的顾问模型列表
- **ask_advisor** - 向顾问模型提问,获取更强大模型的建议和分析(支持同时咨询多个模型)
  - 适用场景:遇到知识不足、需要验证思路、涉及专业领域需要深入分析
  - 支持并发调用多个顾问模型,汇总结果并标注模型来源
  - 注意:一次性问答,无上下文,无探索能力;如需读取文件应先收集信息再提问
- **investigate** - 调查项目信息并返回精炼摘要,内部启动侦察代理收集和整理数据
  - 适用场景:了解模块/函数实现细节、搜索项目中与某主题相关的代码和文档、收集信息后再做判断

> 💡 **提示**: 顾问模型通过 `large_model_name` 配置,仅当配置了顾问模型时才可用。可在 settings.json 中添加,或通过 `/model` 命令将模型设为顾问模型。

#### AI 自助帮助工具 📖

- **list_slash_commands** - 列出所有可用的斜杠命令及其简要说明
- **get_command_help** - 获取指定斜杠命令的详细帮助信息(参数、用法示例等)
- **list_builtin_tools** - 列出所有内置工具名称(核心 + 扩展),用于插件开发时的重名冲突检测

#### 计划模式工具 📝

- **enter_plan_mode** - 进入计划模式,AI 暂不执行工具调用,仅规划方案
- **exit_plan_mode** - 退出计划模式,开始按计划执行

> 💡 **提示**: 计划模式适合复杂任务的前期规划。进入计划模式后,AI 会分析任务并制定详细方案,经用户确认后再逐步执行。

#### 安全工具 🔒

- **read_llm_safe_prompt** - 读取当前 LLM 安全提示词
- **write_llm_safe_prompt** - 写入 LLM 安全提示词
- **edit_llm_safe_prompt** - 编辑 LLM 安全提示词
- **clear_llm_safe_prompt** - 清除 LLM 安全提示词

> 💡 **提示**: 安全工具用于管理 LLM 安全审查机制,防止提示词注入攻击。核心的 `llm_safe_check` 函数会对工具调用进行 AI 驱动的安全审查。

#### 对话管理工具 💬

- **session_list** - 列出所有历史会话
- **session_detail** - 查看会话详情
- **session_delete** - 删除指定会话
- **session_update_title** - 更新会话标题
- **recall_history** - 关键词搜索归档消息,支持正则匹配和上下文窗口(配合无限上下文机制)
- **get_history_range** - 按索引范围查看历史消息,区分活跃/归档状态

#### 用户交互工具 💬

- **AskUserQuestion** - AI 主动向用户提问(支持单选/多选选项)

> 💡 **提示**: AI 会根据任务需求自动选择合适的工具,无需手动调用。所有工具都具备完善的错误处理和权限控制机制。

#### 调度器工具 ⏰

- **schedule_create** - 创建定时任务(支持 shell/agent/py/monitor 四种类型)
- **schedule_list** - 列出所有定时任务及其状态
- **schedule_update** - 修改定时任务的动作和调度时间
- **schedule_remove** - 删除指定的定时任务
- **schedule_toggle** - 启用或禁用定时任务

> 💡 **提示**: 定时任务可用于自动化运维、定期代码检查、定时报告生成等场景。

---

## 🔌 插件系统

UniClaw 支持用户级外部工具插件动态加载,无需修改源码即可扩展 AI 的能力。

### 核心特性

- **热加载**: 将 `.py` 插件文件放入 `~/.UniClaw/plugins/tools/` 目录即可自动加载,无需重启
- **热重载**: 编辑插件文件后,调用 `search_tools` 即可检测变化并重新加载
- **重名检测**: 自动检测与内置工具和其他插件的名称冲突,冲突的工具会被跳过并记录 warning
- **多文件拆分**: 主文件定义 `@tool` 工具 + `get_tools()`,复杂逻辑可拆到同目录辅助模块
- **生命周期钩子**: 可选的 `PLUGIN_META` 元数据和 `shutdown()/close()` 清理钩子
- **会话通知**: 插件卸载时自动通知所有活跃会话移除旧工具

### 快速开始

#### 方式一: 让 AI 自动生成(推荐)

直接告诉 AI 需要什么工具,AI 会通过 `/tool-plugin-forge` 技能自动完成全部流程:

```
帮我写一个天气查询工具,输入城市名返回天气信息
```

AI 会自动: 捕获意图 → 检查重名 → 生成插件代码 → 真实加载验证 → 交付报告。

#### 方式二: 手动编写插件

在 `~/.UniClaw/plugins/tools/` 下创建 `.py` 文件:

```python
from uniclaw.tools.base import tool


@tool
def my_tool(arg1: str, arg2: int = 10) -> str:
    """
    工具的简要描述。

    Args:
        arg1: 参数1的描述。
        arg2: 参数2的描述。默认为 10。

    Returns:
        str: 返回值的描述。
    """
    return f"result: {arg1}, {arg2}"


def get_tools():
    return [my_tool]
```

### 插件规范

**必须遵守的规则:**

- 每个插件文件必须有 `get_tools()` 函数,返回 `@tool` 装饰的工具列表或元组
- 必须使用 `@tool` 装饰器(`from uniclaw.tools.base import tool`)
- 工具函数必须使用 Google style docstring(包含 `Args:` 和 `Returns:` 段)
- 工具名只含 `[A-Za-z0-9_-]`,不能与内置工具或其他插件重名
- 文件名不能以下划线开头(会被跳过)

**耗时任务模式:**

如果插件需要执行耗时操作(HTTP 请求、文件扫描等),必须使用异步唤醒模式,否则会阻塞整个 agent:

```python
import asyncio
from uniclaw.config import AppConfig
from uniclaw.tools.base import tool
from uniclaw.utils.wakeup import wake_agent
from uniclaw.utils.constants import SYSTEM_PREFIX


@tool
def long_task(name: str, config: AppConfig = None) -> str:
    """
    执行耗时的长任务(立即返回,后台完成后自动唤醒)。

    Args:
        name: 要处理的对象名。

    Returns:
        str: 确认任务已启动。
    """
    async def _run():
        try:
            result = await asyncio.to_thread(_do_heavy_work, name)
            await wake_agent(f"{SYSTEM_PREFIX}(long_task) 完成: {result}", config)
        except Exception as e:
            await wake_agent(f"{SYSTEM_PREFIX}(long_task) 失败: {e}", config)

    asyncio.create_task(_run())
    return f"已启动 {name} 的处理,完成后会自动唤醒通知。"
```

**可选的生命周期钩子:**

```python
PLUGIN_META = {"author": "user", "version": "1.0.0"}


async def shutdown():
    """插件卸载时的清理逻辑(释放连接、临时文件等)。"""
    await _client.close()
```

### 管理命令

| 操作 | 方式 |
|------|------|
| 创建插件 | `/tool-plugin-forge <功能描述>` 或直接告诉 AI |
| 查看内置工具名 | AI 调用 `list_builtin_tools` 工具(用于重名检测) |
| 刷新插件 | AI 调用 `search_tools` 自动检测变化并重载 |
| 删除插件 | 删除 `~/.UniClaw/plugins/tools/` 下的对应文件 |

### 存储位置

- **插件目录**: `~/.UniClaw/plugins/tools/` (用户级,所有项目共享)

> ⚠️ **安全提示**: 插件是可执行 Python 代码,加载前请确认文件来源可信。不要运行不明来源的插件。

---

## 🏗️ 架构设计

### 核心组件

项目采用 **src layout**,所有源码位于 `src/uniclaw/` 下。入口命令: `uniclaw`(通过 `pyproject.toml` 的 `[project.scripts]` 注册)。

```
UniClaw/
├── pyproject.toml          # 项目配置(依赖、入口、构建)
├── .python-version         # Python 版本锁定(本地开发用 3.14,最低要求 3.11)
├── tests/                  # 测试用例(位于仓库根目录)
│
└── src/uniclaw/            # 📦 包根目录
    ├── main.py             # 程序入口(argparse --mode → launcher,run_mode 枚举)
    ├── agent/              # 核心代理(全异步消息循环、工具调用、事件流、死循环检测、asyncio.Queue)
    │   ├── __init__.py     # 向后兼容的 re-export(MultiAgent, AgentTask, 事件类)
    │   ├── multi_agent.py  # MultiAgent 单例,事件循环,子代理管理,权限检查
    │   └── types.py        # AgentStatus, AgentTask, 事件类定义
    ├── config.py           # 配置管理(AppConfig + settings.json + 首次启动向导 + 多模型 fallback)
    ├── context.py          # 上下文管理和提示词构建(root_dir 驱动 + 长任务进展汇报)
    ├── compaction.py       # 上下文压缩(三级压力策略)
    ├── spinner.py          # 加载动画指示器
    │
    ├── provider/           # LLM 层:多 provider 路由(OpenAI / Anthropic)
    │   ├── router.py       # 统一 API:stream/astream/chat/achat + 多模型 fallback
    │   ├── fallback.py     # LLM 调用回退:主模型失败时自动尝试备用模型
    │   ├── openai_provider.py
    │   ├── anthropic_provider.py
    │   ├── thought_parser.py   # 流式解析 <thought>/<think> 标签
    │   ├── error_classifier.py  # 错误分类器:六类错误分类 + 差异化重试策略
    │   ├── types.py        # Protocol/Effort 枚举,Usage 用量统计
    │   └── common.py       # get_provider(),compare_urls(),HTTP 客户端缓存
    │
    ├── commands/           # 斜杠命令系统 📝 (31 个命令 + 7 个别名)
    │   ├── __init__.py     # 命令注册中心(COMMANDS dict)
    │   ├── session.py      # 会话管理(clear/compact/export)
    │   ├── resume.py       # 会话恢复(list/del/search/fork) 💬
    │   ├── model.py        # 模型切换(含图片生成模型)
    │   ├── system.py       # 系统命令(cwd/skills/exit/help/usage)
    │   ├── memory.py       # 记忆管理
    │   ├── mcp.py          # MCP 管理
    │   ├── schedule.py     # 定时任务 ⏰(支持会话关联)
    │   ├── permissions.py  # 权限规则管理
    │   ├── context_usage.py # 上下文使用分析
    │   ├── init.py         # 项目初始化(生成 CLAUDE.md)
    │   ├── add_dir.py      # 工作空间目录管理
    │   ├── cost.py         # 费用统计 💰
    │   ├── doctor.py       # 环境诊断 🩺
    │   ├── task.py         # 后台任务管理 🔄
    │   ├── btw.py          # 附带信息
    │   ├── name.py         # 会话命名
    │   ├── overseer.py     # 监工模式
    │   ├── goal.py         # 目标停止条件 🎯
    │   ├── checkpoint.py   # Git 检查点
    │   ├── undo.py         # 撤销文件编辑
    │   ├── voice.py        # 语音模式切换 🔊
    │   ├── cu.py           # Computer Use 模式切换 🖥️
    │   ├── explain.py      # 工具解释模式切换 🔧
    │   ├── knowledge.py    # 知识图谱管理 🗺️
    │   └── a2a.py          # A2A 远程代理管理 🌉
    │
    ├── console/            # 控制台交互界面(prompt_toolkit REPL)
    │   ├── launcher.py     # 控制台启动器
    │   ├── run.py          # REPL 主循环 + 文件补全 + 子命令补全 + 命令补全弹窗
    │   ├── dialog.py       # 对话管理 + 多问题 Tab 对话框
    │   ├── output_renderer.py
    │   ├── session_panel.py
    │   └── ui.py           # UI 组件
    │
    ├── webui/              # WebUI 界面(WebSocket)
    │   ├── launcher.py     # WebUI 启动器
    │   ├── app.py          # FastAPI 应用
    │   ├── api.py          # REST API 路由(含子代理创建 API)
    │   ├── ws.py           # WebSocket 处理(会话任务管理)
    │   ├── models.py       # 数据模型
    │   ├── auth.py         # 认证层(登录/可信 IP/IP 限流)
    │   ├── crypto.py       # 加密模块
    │   ├── encrypt_static.py # 静态资源加密
    │   ├── spinner.py      # WebUI 加载动画
    │   └── static/         # 前端静态资源(CSS 样式 + 动画效果)
    │
    ├── tools/              # 工具系统
    │   ├── __init__.py     # 工具注册中心
    │   ├── base.py         # @tool 装饰器(自动生成 OpenAI schema)
    │   ├── registry.py     # 工具注册表(BM25 搜索,核心/扩展分层,LRU 淘汰)
    │   ├── fs.py           # 文件系统(Read/Write/Edit/Glob)
    │   ├── shell.py        # Shell(Bash/Grep/Everything)
    │   ├── web.py          # Web(webFetch/webSearch)
    │   ├── search/         # 多平台搜索(GitHub/arXiv/Stack Overflow/B站等)
    │   │   ├── tools.py    # 搜索入口 + 工具定义(webSearch 多平台并发)
    │   │   ├── base.py     # 基础搜索类(带重试的 HTTP 请求)
    │   │   ├── exa.py      # Exa 语义搜索(优先引擎)
    │   │   ├── bing.py     # Bing 搜索(国内直连,无需代理)
    │   │   ├── duckduckgo.py # DuckDuckGo 搜索降级
    │   │   ├── github.py   # GitHub 搜索(repos/code/issues/users)
    │   │   ├── huggingface.py # HuggingFace 搜索(models/datasets)
    │   │   ├── openalex.py # OpenAlex 学术搜索(429 重试)
    │   │   ├── arxiv.py    # arXiv 论文搜索
    │   │   ├── alphaxiv.py # alphaXiv 论文讨论搜索
    │   │   ├── stackoverflow.py # Stack Overflow 搜索
    │   │   ├── hackernews.py # Hacker News 搜索
    │   │   ├── bilibili.py # B站视频搜索
    │   │   ├── google_news.py # Google News 搜索
    │   │   ├── reddit.py   # Reddit 搜索
    │   │   ├── polymarket.py # Polymarket 预测市场搜索
    │   │   ├── sec_edgar.py # SEC EDGAR 金融搜索
    │   │   └── time_range.py # 时间范围过滤
    │   ├── download/       # HTTP + M3U8 下载引擎 ⬇️
    │   │   ├── tools.py    # 工具聚合入口(统一导出 HTTP 与 M3U8 工具)
    │   │   ├── manager.py  # 下载任务管理器(HTTP/M3U8 独立管理)
    │   │   ├── http_tools.py # HTTP/HTTPS 下载工具(多协程并发 + 断点续传)
    │   │   ├── m3u8.py     # M3U8/HLS 核心解析引擎(AES-128 解密 + 合并)
    │   │   └── m3u8_tools.py # M3U8 下载工具定义
    │   ├── media.py        # 多媒体(ReadMedia 多模态 + GenerateImage 文生图)
    │   ├── sandbox/        # 代码沙箱(Docker 容器/镜像管理)
    │   │   ├── tools.py    # 工具定义(DockerCreate/Exec/Pull/Build 等)
    │   │   ├── manager.py  # 沙箱管理器(容器生命周期跟踪)
    │   │   └── models.py   # 数据模型
    │   ├── plan.py         # 计划模式(enter/exit)
    │   ├── sleep.py        # 异步等待
    │   ├── ask.py          # 用户交互(AskUserQuestion)
    │   ├── notify.py       # 系统通知 🔔
    │   ├── computer_use/   # 计算机控制(截图/鼠标/键盘) 🖥️
    │   │   ├── tools.py    # 工具定义(screenshot/mouse/keyboard)
    │   │   ├── automation.py # 自动化抽象层
    │   │   ├── automation_win.py # Windows 实现
    │   │   ├── automation_mac.py # macOS 实现
    │   │   └── automation_linux.py # Linux 实现
    │   ├── web_browse/     # 浏览器自动化(Playwright) 🌍
    │   ├── security/       # 安全检查和权限管理 🔒
    │   ├── scheduler/      # 调度器 ⏰(会话关联 + monitor 监控类型)
    │   ├── skill/          # 技能系统(加载/执行/内置技能)
    │   ├── multi_agent/    # 多智能体(全异步 + worktree 隔离)
    │   ├── mcp/            # MCP 集成 🔌
    │   ├── memory/         # 记忆系统(FTS5 检索 + 自动整合 + 自动保存) 🧠
    │   ├── knowledge/      # 知识图谱(实体/关系管理 + 自动提取 + 可视化) 🗺️
    │   │   ├── __init__.py
    │   │   ├── graph.py    # 核心图谱类(SQLite + FTS5)
    │   │   ├── tools.py    # 工具定义(16 个工具)
    │   │   └── context.py  # 上下文注入
    │   ├── todolist/       # 任务清单 + 监工 + 目标系统 📋
    │   ├── monitor/        # 后台进程管理(异步 + ConPTY 可视终端) 🔄
    │   │   ├── tools.py    # 工具定义(monitor_start/stop/screen/clear 等)
    │   │   ├── manager.py  # 监控管理器(运行/已结束条目管理)
    │   │   ├── models.py   # 数据模型
    │   │   ├── pty_session.py # ConPTY 伪终端会话
    │   │   └── viewer.py   # 可视终端窗口(跨平台终端查找)
    │   ├── restart/        # 进程重启(WebUI 热更新) 🔁
    │   │   ├── tools.py    # restart_agent 工具定义
    │   │   ├── manager.py  # 重启编排(保存会话/关闭服务)
    │   │   ├── relauncher.py # 重启执行与进程拉起
    │   │   └── resumer.py  # 新进程侧会话恢复
    │   ├── session/        # 会话持久化 + 历史消息检索 + 自动保存 💬
    │   ├── hooks/          # Hook 系统 🪝
    │   ├── tts/            # 语音合成(TTS) 🔊
    │   ├── plugins/        # 插件系统(用户级外部工具动态加载) 🔌
    │   │   ├── __init__.py
    │   │   └── loader.py   # PluginManager(热重载/重名检测/生命周期管理)
    │   ├── advisor.py      # 顾问模型工具(ask_advisor 多模型并发咨询) 🎓
    │   ├── wechat.py       # 微信工具(联系人/发送文本/图片/文件)
    │   ├── help.py         # AI 自助帮助工具 📖
    │   ├── send_file.py    # 文件发送工具 📤
    │   ├── stream.py       # 工具流式输出(实时推送执行进度到前端)
    │   ├── rag/            # RAG 检索增强(文档导入/语义检索/集合管理) 📚
    │   │   ├── tools.py    # 工具定义(rag_ingest/search/list/delete/set_desc)
    │   │   ├── rag.py      # RAG 管理器(向量数据库 + embedding)
    │   │   ├── loader.py   # 文档加载器(多格式支持)
    │   │   ├── splitter.py # 文档拆分器(智能分块)
    │   │   └── context.py  # 上下文注入
    │   ├── a2a/            # A2A 远程代理(客户端/服务端/管理器) 🌉
    │   │   ├── client.py   # A2A 客户端(Agent Card 发现 + JSON-RPC)
    │   │   ├── server.py   # A2A 服务端(挂载到 WebUI 的 /a2a 路径)
    │   │   ├── manager.py  # A2A 管理器(配置持久化 + 服务状态)
    │   │   └── tools.py    # 工具定义(send/submit/get/cancel/add/list)
    │   └── ipython/        # IPython 内核(持久化 Python 执行环境) 🐍
    │       ├── kernel.py   # 内核管理器(启动/停止/执行/变量检查)
    │       └── tools.py    # 工具定义(start/execute/inspect/vars/history/stop/list)
    │
    ├── utils/              # 实用工具
    │   ├── checkpoint.py   # 文件快照检查点系统
    │   ├── downloader.py   # 下载器抽象基类(BaseDownloader 接口,DownloadManager 统一管理)
    │   ├── http_download.py # HTTP 下载引擎(多协程并发 + 断点续传)
    │   ├── usage.py        # Token 用量统计 + OpenRouter 定价
    │   ├── tokenize.py     # 分词(BM25 索引用)
    │   ├── truncation.py   # 基于 token 的文本截取
    │   └── ...             # git, format, cache, logger, frontmatter 等
    │
    ├── ilink_bot/          # iLink Bot 微信协议客户端
    │   ├── client.py       # API 客户端
    │   ├── crypto.py       # 消息加密
    │   ├── manager.py      # 多账号管理器(异步登录)
    │   ├── media.py        # 媒体处理
    │   ├── storage.py      # 本地消息缓存
    │   ├── models.py       # 数据模型
    │   └── exceptions.py   # 异常定义
    │
    └── assets/             # 静态资源(logo.png)
```

### 工具注册表系统

采用核心/扩展工具分层架构,对齐 Anthropic 的 `defer_loading` 模式：

- **核心工具** (16 个 + 1 个元工具 `search_tools`): 始终加载完整 schema,是 prompt 缓存的稳定前缀
  - 文件系统: `Read`, `Write`, `Edit`, `Glob`
  - Shell: `Bash`, `Grep`
  - Web: `webFetch`(抓取网页), `webSearch`(多平台搜索, 默认 Exa)
  - 记忆: `memory_save/delete/list/search`
  - 计划: `enter/exit_plan_mode`
  - 技能: `skill_suggest/read`
  - 元工具: `search_tools`（按需发现和加载扩展工具）
- **扩展工具** (170+ 个): 初始不加载,通过 `search_tools` 元工具按需发现
  - 基于 BM25 算法搜索,支持中英文关键词 + 语义同义词
  - **LRU + 能量机制**: 每个扩展工具初始 30 点能量,每轮对话 -1,被调用或搜索命中恢复满能量,归零自动卸载;最多同时加载 25 个扩展工具,超出时按 LRU 顺序淘汰能量最低者
  - 搜索结果自动注入到当前任务的可用工具集
  - 按类别组织: 系统管理、计算机操作、多智能体、任务清单、进程监控、会话管理、定时任务、MCP 管理、安全管理、Hook 管理、沙箱、浏览器、知识图谱、顾问、下载、RAG、IPython、微信、文件、帮助、通知等
  - **可用性跟踪**: 各模块 `get_tools()` 检测依赖(Docker/Everything/embedding_model 等),不可用的工具记录到 `config.unavailable_tool_reasons` 并附带原因;`search_tools` 搜索到不可用工具时自动提示"当前不可用(未启用或无权限)"及原因,避免 AI 做无效调用

**工作流程**: AI 需要使用非常用工具时 → 调用 `search_tools(query)` → BM25 匹配 → 工具自动加载(若超过上限则淘汰 LRU 端能量最低的工具) → 下一轮即可调用。已加载工具每轮能量-1,被调用/搜索命中恢复满,归零自动卸载。

- **插件工具**: 用户级外部工具插件,通过 `PluginManager` 从 `~/.UniClaw/plugins/tools/` 动态加载,支持热重载。插件工具注册到扩展工具类别中,与内置扩展工具统一管理。详见 [插件系统](#-插件系统)。

### LLM 层

多 provider 架构,支持配置多个 API 提供商并自动路由。`provider/router.py` 暴露 `stream()`、`astream()`、`chat()`、`achat()`,根据 model_name 中的 provider 前缀自动选择对应的 provider,调用方无需感知后端差异。

- **ProviderProfile**: 每个 provider 独立配置 protocol/api_key/base_url,支持 OpenAI 和 Anthropic 两种协议
- **Fallback 机制**: `model_name` 列表支持多模型 fallback,主模型失败时自动切换下一个;`provider/fallback.py` 提供 `chat()`/`achat()` 包装,支持 model_name 为 list 时按顺序回退
- **错误分类器**: `provider/error_classifier.py` 将 LLM 调用错误分为六类(RATE_LIMIT/CONTEXT_OVERFLOW/AUTH/SERVER_ERROR/TIMEOUT/UNKNOWN),每类采用不同的重试策略——AUTH/UNKNOWN 不重试立即回退下一模型,CONTEXT_OVERFLOW 先压缩会话再重试,RATE_LIMIT 指数退避,SERVER_ERROR 线性退避
- **顾问模型**: `large_model_name` 配置顾问模型列表,AI 遇到难题时可通过 `ask_advisor` 工具并发咨询多个更强力模型
- **OpenAI SDK**: 流式 + 异步,支持 `reasoning_content` 和思考模型
- **Anthropic SDK**: 等价接口,自动路由
- **ThoughtParser**: 流式解析 `<thought>`/`<think>` 标签,分离思考过程和正文内容
- **Effort 枚举**: 控制推理深度(xhigh/high/medium/minimal/low/none),用于 OpenRouter 的 `reasoning.effort` 参数
- **多模态降级**: 主模型不支持多模态时自动使用 `multimodal_model_name` 重试
- **代理兼容**: 自动检测 Google API / OpenRouter API 并适配 `extra_body` 参数
- **请求超时**: 所有 LLM 请求默认 180 秒超时,`stream()`/`chat()` 等接口的 `timeout` 参数可覆盖客户端默认值

### 工作流程

```
用户输入 → REPL 接收用户消息或斜杠命令
    ↓
LLM 推理(含 thinking 标签解析)→ 权限检查 → 工具执行
    ↓
ToolEvent (result)
    ↓
Update AgentState → auto-compact if pressure > 50%
    ↓
Next Iteration or Final Response → session auto-save
```

### ♾️ 无限上下文机制

UniClaw 通过 **压缩 + 召回** 模式实现无限上下文,对话永不失忆。核心由三层机制协作：

#### 双列表存储

Session 维护两条平行的消息列表：

| 列表 | 用途 | 特点 |
|------|------|------|
| `_messages` | 活跃上下文,每次发给 LLM | 受模型上下文窗口限制,会被压缩 |
| `history` | 完整历史,所有消息的唯一真相 | 无限增长,持久化到磁盘,永不截断 |

每条消息(user/assistant/tool)同时写入两个列表。压缩只影响 `_messages`,`history` 始终完整。

#### 三级自动压缩

压缩在后台异步运行,不阻塞对话：

| 级别 | 压力阈值 | 策略 |
|------|----------|------|
| Level 0 | 50% | 轻度:清除可再生的工具结果(Read/Grep/Glob/webFetch/webSearch) |
| Level 1 | 70% | 中度:清除 + LLM 结构化摘要(保留 30% 近期消息) |
| Level 2 | 85% | 重度:清除 + 激进 LLM 摘要(保留 15% 近期消息) |

压缩后的摘要包含：当前意图、下一步操作、涉及文件、已完成/未完成任务、关键决策、错误信息,以及用于检索的主题关键词。

#### 按需历史召回

当 LLM 需要回忆被压缩的早期内容时,可通过两个工具从 `history` 中检索：

- **recall_history** — 关键词搜索归档消息,支持正则匹配和上下文窗口
- **get_history_range** — 按索引范围查看历史消息,区分活跃/归档状态

系统提示词会自动注入召回提示：*"当前会话有 N 条完整历史消息,其中 M 条已被压缩移出当前上下文。当用户提及或你需要回忆已压缩的早期内容时,使用 recall_history 工具搜索历史..."*

#### 工作流程

```
用户消息 → 同时写入 _messages + history
    ↓
系统提示词(含召回提示,如有归档消息)
    ↓
LLM 推理 → 工具执行 → 后台异步压缩(maybe_compact)
    ↓
需要旧上下文? → recall_history / get_history_range → 从 history 检索
    ↓
会话保存 → _messages + history 序列化到磁盘
```

> 💡 **跨会话持久化**: 会话通过 `SessionManager` 保存到 `.UniClaw/sessions/`,两条列表同时序列化。恢复会话后,完整历史不丢失,召回工具继续可用。

---

## 🗺️ 知识图谱系统

UniClaw 内置基于 SQLite 的知识图谱系统,支持实体和关系的管理、查询、可视化。

### 核心特性

- **双层管理**: 用户级(跨项目共享)和项目级独立管理
- **智能搜索**: 基于 SQLite FTS5 前缀匹配 + LIKE 子串回退的混合查询,支持中英文混合搜索(中文自动添加 `*` 前缀通配),并覆盖实体别名表扩展搜索范围
- **智能提取**: AI 自动从文本或文件中提取实体和关系
- **路径发现**: 查找实体间的关联路径(多跳遍历)
- **实体合并**: 去重操作,自动转移关系和别名
- **可视化导出**: 支持 HTML、JSON、Markdown 格式导出

### 数据模型

**实体类型**:
- `person` - 人物
- `place` - 地点
- `concept` - 概念
- `event` - 事件
- `tool` - 工具
- `organization` - 组织
- `document` - 文档
- `technology` - 技术

**关系类型**:
- `is_a` - 是一种
- `has` - 拥有
- `uses` - 使用
- `related_to` - 相关
- `created_by` - 由...创建
- `belongs_to` - 属于

### 使用示例

#### 手动添加实体和关系

```
# AI 会自动调用工具
kg_add_entity(name="Python", type="technology", description="编程语言")
kg_add_entity(name="Guido", type="person", description="Python 之父")
kg_add_relation(source="Guido", target="Python", relation="created_by")
```

#### 自动提取

```
# 从文本提取
kg_extract(text="Python 是一种解释型编程语言,由 Guido van Rossum 于 1991 年创建。")

# 从文件提取
kg_extract(path="docs/README.md")

# 从目录提取
kg_extract(path="src/")
```

#### 查询和遍历

```
# 搜索实体
kg_search(keyword="Python")

# 获取实体详情
kg_get_entity(name="Python")

# 获取邻居(多跳)
kg_neighbors(name="Python", depth=2)

# 查找路径
kg_path(source="Guido", target="人工智能")
```

#### 使用斜杠命令

```bash
/kg                        # 查看统计信息
/kg search Python          # 搜索实体
/kg list person            # 列出所有人物
/kg export html            # 导出 HTML 可视化
/kg clear project          # 清空项目级图谱
```

### 存储位置

- **用户级**: `~/.UniClaw/knowledge.db` (跨项目共享)
- **项目级**: `.UniClaw/knowledge.db` (当前项目)

### 可视化

导出 HTML 后,可以在浏览器中查看交互式知识图谱可视化,支持：
- 节点拖拽和缩放
- 关系类型过滤
- 实体搜索
- 详情查看

---

## 📚 RAG 检索增强系统

UniClaw 内置 RAG (Retrieval-Augmented Generation) 系统,支持文档导入、智能拆分、向量嵌入和语义检索。

### 核心特性

- **多格式支持**: txt/md/py/json/yaml/csv/html/pdf 等常见文档格式
- **智能拆分**: 可配置文档块大小和重叠度,保证语义连贯性
- **向量嵌入**: 自动生成文档 embedding,支持语义相似度检索
- **多集合管理**: 按主题或项目组织文档,每个集合独立管理
- **双层作用域**: 用户级(跨项目共享)和项目级独立管理

### 使用示例

#### 导入文档

```
# 导入单个文件
rag_ingest(path="docs/api.md", collection="api-docs")

# 导入整个目录
rag_ingest(path="src/", collection="source-code", chunk_size=500)

# 用户级导入(跨项目共享)
rag_ingest(path="~/notes/", collection="my-notes", scope="user")
```

#### 语义检索

```
# 搜索相关文档
rag_search(query="如何配置 MCP 服务器", collection="api-docs")

# 限制返回数量和相关度阈值
rag_search(query="认证流程", collection="api-docs", top_k=3, score_threshold=0.7)
```

#### 管理集合

```
# 列出所有集合
rag_list_collections()

# 设置集合描述
rag_set_desc(collection="api-docs", desc="API 接口文档")

# 删除集合
rag_delete_collection(collection="old-docs")
```

### 存储位置

- **用户级**: `~/.UniClaw/rag.db` (跨项目共享)
- **项目级**: `.UniClaw/rag.db` (当前项目)

---

## 🔌 MCP 集成

UniClaw 支持通过 MCP (Model Context Protocol) 连接外部工具服务,扩展 AI 的能力。

### 支持的协议

| 协议 | 说明 | 典型用途 |
|------|------|----------|
| stdio | 本地进程通信 | 本地工具服务 |
| sse | Server-Sent Events | 远程 HTTP 服务 |
| streamable_http | HTTP Streamable | 远程 HTTP 服务 |
| websocket | WebSocket | 实时双向通信 |

### 内置 MCP 服务器

UniClaw 内置了以下 MCP 服务器配置,首次启动时自动写入 `~/.UniClaw/mcp.json`:

| 服务器 | 协议 | URL | 说明 |
|--------|------|-----|------|
| `exa` | streamable_http | `https://mcp.exa.ai/mcp` | Exa AI 语义搜索引擎,配置 `EXA_API_KEY` 后自动拼接认证参数 |

> 💡 用户在 `mcp.json` 中的同名配置会覆盖内置默认值。

### 快速开始

#### 方式一：使用斜杠命令(手动操作)

1. **添加 MCP 服务器**

   ```
   /mcp add filesystem {"transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","D:/code"]}
   ```

2. **查看已配置的服务器**

   ```
   /mcp list
   ```

3. **查看可用工具**

   ```
   /mcp tools
   ```

#### 方式二：让 AI 直接管理(推荐)

您可以直接告诉 AI 添加 MCP 服务器,AI 会自动调用相应的工具完成配置：

```
帮我添加一个文件系统 MCP 服务器,路径是 D:/code
```

或者更详细的指令：

```
添加一个名为 web-search 的 SSE 类型 MCP 服务器,URL 是 http://localhost:8080/sse
```

AI 会自动调用 `mcp_add_server` 工具完成配置,并刷新工具列表。

### MCP 管理工具

以下工具可供 AI 直接调用,无需使用斜杠命令：

| 工具名称 | 功能 | 示例 |
|---------|------|------|
| `mcp_add_server` | 添加 MCP 服务器 | `mcp_add_server(name="fs", transport="stdio", command="npx", args=[...])` |
| `mcp_remove_server` | 删除 MCP 服务器 | `mcp_remove_server(name="fs")` |
| `mcp_toggle_server` | 启用/禁用服务器 | `mcp_toggle_server(name="fs", enabled=False)` |
| `mcp_list_servers` | 列出所有服务器及其工具 | `mcp_list_servers()` - 返回每个服务器的工具数量和工具描述 |

**mcp_list_servers 工具输出示例：**

```
MCP 服务器列表(共 2 个):

  [✓ 启用] filesystem (stdio)
    npx -y @modelcontextprotocol/server-filesystem D:/code
    工具数量: 5 个
    工具列表:
      - read_file: 读取指定路径的文件内容
      - write_file: 写入内容到指定路径的文件
      - list_directory: 列出目录中的所有文件和子目录
      - create_directory: 创建新目录
      - delete_file: 删除指定文件

  [✓ 启用] web-search (sse)
    http://localhost:8080/sse
    工具数量: 3 个
    工具列表:
      - search_web: 执行网络搜索并返回结果
      - get_page_content: 获取指定URL的页面内容
      - extract_links: 从页面中提取所有链接
```

### 配置文件

MCP 配置存储在 `~/.UniClaw/mcp.json`：

```json
{
  "servers": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/code"],
      "enabled": true
    },
    "remote-api": {
      "transport": "sse",
      "url": "https://api.example.com/mcp/sse",
      "headers": {
        "Authorization": "Bearer your-api-key"
      },
      "timeout": 15,
      "enabled": true
    }
  }
}
```

### 认证配置

HTTP 类协议通过 `headers` 传递认证信息：

```json
{
  "transport": "sse",
  "url": "https://api.example.com/mcp",
  "headers": {
    "Authorization": "Bearer your-api-key",
    "X-API-Key": "your-api-key"
  },
  "timeout": 15
}
```

### 命令参考

| 命令 | 说明 |
|------|------|
| `/mcp list` | 列出所有服务器 |
| `/mcp add <name> [json]` | 添加服务器(支持 JSON 配置) |
| `/mcp remove <name>` | 删除服务器 |
| `/mcp enable <name>` | 启用服务器 |
| `/mcp disable <name>` | 禁用服务器 |
| `/mcp tools [name]` | 列出可用工具 |

> 所有命令在终端和微信模式下都可用,`add` 支持交互式和 JSON 两种模式。

---

## 🌉 A2A 远程代理

UniClaw 支持 Agent2Agent (A2A) 协议,既可以作为客户端调用外部 A2A Agent,也可以将自身暴露为 A2A 端点供其他 Agent 调用。

### 核心特性

- **双向通信**: 既可调用外部 Agent,也可被外部 Agent 调用
- **多轮对话**: 通过 `context_id` 维持对话连续性
- **异步任务**: 支持同步等待和异步提交两种模式
- **自动发现**: 通过 Agent Card (`/.well-known/agent-card.json`) 自动发现服务能力
- **配置持久化**: Agent 配置和服务状态持久化到 `~/.UniClaw/a2a/a2a.json`
- **独立上下文**: 每个 `context_id` 维护独立的 `AppConfig` 和会话

### 作为客户端（调用外部 Agent）

#### 添加外部 Agent

```bash
# 通过斜杠命令添加
/a2a add coder http://192.168.1.100:8080/a2a your-token

# 或让 AI 直接调用工具
a2a_add_agent(agent_name="coder", url="http://192.168.1.100:8080/a2a", token="your-token")
```

#### 同步调用（等待结果）

```python
# AI 自动调用
a2a_send_task(agent_name="coder", message="帮我写一个快速排序算法")
# 带多轮对话
a2a_send_task(agent_name="coder", message="优化一下性能", context_id="上一轮返回的 context_id")
```

#### 异步调用（立即返回）

```python
# 提交任务
result = a2a_submit_task(agent_name="coder", message="重构整个模块")
# 返回: {"task_id": "xxx", "context_id": "yyy", "state": "working"}

# 查询结果
a2a_get_task(agent_name="coder", task_id="xxx")

# 取消任务
a2a_cancel_task(agent_name="coder", task_id="xxx")
```

> 💡 异步任务完成后会自动唤醒 AI,无需手动轮询。

### 作为服务端（暴露 UniClaw 为 A2A 端点）

#### 启用服务

```bash
# 需要先以 WebUI 模式启动
uv run uniclaw --mode webui

# 启用 A2A 端点
/a2a start

# 查看状态（含 Bearer Token）
/a2a status
```

#### 服务端点

| 端点 | 说明 |
|------|------|
| `/.well-known/agent-card.json` | Agent Card（服务能力描述） |
| `/a2a` | JSON-RPC 2.0 端点 |

#### 支持的 JSON-RPC 方法

| 方法 | 说明 |
|------|------|
| `message/send` | 发送消息并创建任务 |
| `tasks/get` | 查询任务状态和结果 |
| `tasks/cancel` | 取消正在执行的任务 |

#### 其他 UniClaw 实例连接示例

```bash
# 在另一个 UniClaw 实例中
/a2a add remote-coder http://192.168.1.100:8080/a2a <token>

# 或让 AI 调用
a2a_add_agent(agent_name="remote-coder", url="http://192.168.1.100:8080/a2a", token="<token>")
a2a_send_task(agent_name="remote-coder", message="帮我审查这段代码")
```

### 命令参考

| 命令 | 说明 |
|------|------|
| `/a2a` 或 `/a2a status` | 查看 A2A 服务状态 |
| `/a2a start [token]` | 启用 A2A 端点（仅 WebUI 模式） |
| `/a2a stop` | 禁用 A2A 端点 |
| `/a2a add <名称> <URL> [token]` | 添加远程 A2A Agent |
| `/a2a list` | 列出已配置的远程 Agent |
| `/a2a remove <名称>` | 删除远程 Agent |
| `/a2a test <名称>` | 测试远程 Agent 连接 |

### 存储位置

- **配置文件**: `~/.UniClaw/a2a/a2a.json`（Agent 列表 + 服务状态）
- **工作空间**: `~/.UniClaw/a2a/workspace/`（A2A 服务端的隔离工作目录）

---

## ❓ 常见问题

### Q: 如何更换 LLM 提供商？

A: 系统支持 OpenAI 和 Anthropic 两种协议,通过 `providers` 配置多个提供商,自动路由。

- **添加 Provider**: 在 `providers` 字典中添加新的提供商配置,指定 `protocol`(openai/anthropic)、`api_key` 和 `base_url`
- **切换模型**: 修改 `model_name` 列表,使用 `provider_name/model_name` 格式指定模型,如 `["anthropic/claude-sonnet-4-20250514"]`
- **Fallback 机制**: `model_name` 列表中多个模型自动 fallback,主模型失败时自动切换到下一个
- **首次配置**: 运行配置向导自动完成 provider 设置

### Q: 如何使用顾问模型？

A: 顾问模型允许 AI 在遇到难题时咨询更强力的模型获取第二意见：

1. **配置顾问模型**: 在 `settings.json` 的 `large_model_name` 中添加顾问模型
   ```json
   {
     "large_model_name": ["default/o3", "anthropic/claude-opus-4-20250514"]
   }
   ```
2. **自动可用**: 配置后,AI 会自动识别并使用 `advisor_list` 和 `ask_advisor` 工具
3. **同时咨询**: 支持同时向多个顾问模型提问,结果会汇总并标注来源

**适用场景**: 遇到知识不足、需要验证思路、涉及专业领域需要深入分析时。

### Q: Token 使用率过高怎么办？

A: 系统会自动进行上下文压缩,采用三级压力策略(50%/70%/85%),详见 [无限上下文机制](#-无限上下文机制)。

压缩只影响活跃上下文(`_messages`),完整历史(`history`)始终保留在磁盘上,AI 可通过 `recall_history` 工具随时检索被压缩的内容。

你也可以手动干预：
- 使用 `/compact` 手动触发压缩
- 开始新的会话(重启程序)
- 减少单次对话的长度
- 使用更简洁的提示词

### Q: 如何启用 Everything 搜索？

A: 
1. 下载并安装 [Everything](https://www.voidtools.com/)
2. 确保 `es.exe` 在系统 PATH 中
3. 重启 UniClaw

### Q: 如何使用微信机器人功能？

A:
1. **启动 WebUI**：运行 `uv run uniclaw --mode webui`
2. **添加账号**：在侧边栏微信区域点击"添加微信账号"，扫描二维码登录
3. **自动监听**：已登录的账号会自动启动消息监听
4. **开始对话**：在微信中直接发送消息即可与 AI 交互

支持的功能包括：
- 智能对话和问答
- Shell 命令执行(使用 `!` 前缀)
- 图片识别和理解
- 多账号同时管理

详细使用方法请参考 [微信机器人集成](#-微信机器人集成) 章节。

### Q: 微信机器人的数据存储在哪里？

A: 微信机器人的数据存储在项目的 `wechat/` 目录下,包括：
- 账号登录凭证
- 会话历史记录
- 临时文件缓存

这些数据会在首次使用时自动创建,无需手动配置。

### Q: 记忆系统如何使用？

A: 记忆系统会自动工作,但您也可以手动管理：
- **自动加载**: AI 会根据对话内容自动检索相关记忆
- **手动保存**: 使用 `memory_save` 工具保存重要信息
- **查看记忆**: 使用 `memory_list` 查看所有记忆
- **删除记忆**: 使用 `memory_delete` 删除不需要的记忆

记忆分为三种作用域：
- **用户级**: 对所有项目生效(如个人偏好)
- **项目级**: 仅对当前项目生效(如项目规范)
- **会话级**: 仅在当前对话中有效

### Q: 多智能体系统如何使用？

A: 多智能体系统采用全异步架构,允许创建专业化的助手并进行协作：

**基本功能**:
- **创建智能体**: 使用 `subagent_create` 创建并启动子智能体任务
- **查看智能体类型**: 使用 `subagent_list_definitions` 查看所有可用智能体类型
- **查看任务**: 使用 `subagent_list_tasks` 查看所有正在运行的智能体任务
- **任务分配**: AI 会根据任务类型自动选择合适的智能体

**高级功能**:
- **智能体通信**: 使用 `subagent_send_message` 向指定智能体发送消息
- **结果检查**: 使用 `subagent_check_result` 查看子智能体的执行结果和状态
- **关闭智能体**: 使用 `subagent_close` 关闭不再需要的智能体
- **智能体讨论**: 使用 `subagent_discuss` 启动多个智能体之间的多轮协作讨论
- **持续运行**: 异步模式(`wait=False`)下智能体可保持运行状态并接收新指令
- **父子任务继承**: 子代理自动继承父代理的工具配置和权限
- **唤醒机制**: 子智能体完成后自动通知父智能体,确保及时处理结果

**使用场景示例**:
```
# 同步创建代码审查智能体(等待完成后返回结果)
subagent_create(prompt="审查这段代码", subagent_type="code_reviewer", name="review-task")

# 异步创建智能体(立即返回,后台运行)
subagent_create(prompt="编写测试", subagent_type="test_writer", name="test-task", wait=False)

# 向智能体发送消息
subagent_send_message(task_id="test-task", message="补充要求")

# 检查结果
subagent_check_result(task_id="test-task")

# 关闭智能体
subagent_close(task_id="test-task")
```

例如,您可以创建专门用于代码审查、文档编写或数据分析的智能体,并通过消息传递实现它们之间的协作。

### Q: 权限模式如何选择？

A:
- **开发环境**: 使用 `accept-all` 提高效率
- **生产环境**: 使用 `auto` 或 `manual` 保证安全
- **敏感操作**: 始终使用 `manual` 模式

**提示**: 可以使用持久化权限规则来记住您的偏好,避免重复确认。当 AI 请求权限时,在确认弹窗中按 **`a`** 即可自动保存规则(始终允许),之后同类命令/工具将自动放行：
```bash
# 在权限确认弹窗中按 a → 自动保存规则到 permission_rules.json

# 查看已保存的规则
/permissions list

# 删除规则
/permissions remove bash "git commit"
```

### Q: 如何使用详细显示模式？

A: 
- **快捷键**: 按 **F2** 键切换详细/简洁模式
- **默认模式**: 简洁模式(只显示核心信息)
- **详细模式**: 显示完整的工具调用参数、Token 统计等元数据
- **配置启动**: 设置环境变量 `VERBOSE=true` 可默认启用详细模式

详细模式适合调试和了解 AI 的工作细节,简洁模式适合日常使用。

**文件编辑预览**:
在详细模式下,当 AI 请求编辑文件权限时,会显示详细的 diff 预览：
- 显示新增行(绿色 + 前缀)
- 显示删除行(红色 - 前缀)
- 显示上下文行(灰色空格前缀)
- 自动限制最大显示行数以避免过度占用屏幕空间

### Q: 如何中断正在运行的任务？

A: 按 **ESC** 键可以中断当前正在执行的任务：
- 任务会被标记为 CANCELLED 状态
- AI 会收到中断通知并停止当前操作
- 系统会显示"已中断,等待您的补充指令..."提示
- 您可以继续输入新的指令

> 💡 **提示**: ESC 键中断功能在输入框为空时触发,如果输入框中有内容,ESC 会先清空输入框。

### Q: sleep_timer 工具有什么用？

A: `sleep_timer` 是一个异步等待工具,可以让 AI 在指定时间后继续工作：

**使用场景**:
- 等待服务启动完成
- API 限流后的延时重试
- 定时任务中的间隔控制

**示例**:
```
# AI 会自动调用
sleep_timer(seconds=30, name="等待服务启动")
# 函数立即返回,30秒后AI会被唤醒继续工作
```

**特点**:
- 不阻塞主线程
- 支持 1-3600 秒的等待时间
- 可添加描述便于追踪

### Q: 计划模式是什么？如何使用？

A: 计划模式是一种任务规划模式,适合处理复杂任务：

**使用方式**：
- **工具调用**: AI 通过 `enter_plan_mode` 工具进入计划模式
- **AI 自动**: AI 在遇到复杂任务时会主动进入计划模式

**工作流程**：
1. 进入计划模式后,AI 会分析任务并制定详细方案
2. 方案包括步骤分解、文件清单、风险评估等
3. 用户确认方案后,AI 逐步执行
4. 执行完成后自动退出计划模式

**适用场景**：
- 大型代码重构
- 多文件修改任务
- 架构调整
- 需要用户确认的敏感操作

### Q: 系统通知功能如何使用？

A: 系统通知工具会在以下场景自动发送桌面通知：
- 长时间运行的任务完成时
- 后台进程状态变化时
- 定时任务执行完成后

支持 Windows (Toast Notification)、macOS (osascript) 和 Linux (notify-send)。

### Q: 如何使用 WebUI？

A:
1. 启动 WebUI：`uv run uniclaw --mode webui`
2. 浏览器自动打开(或手动访问终端显示的地址)
3. 在浏览器中与 AI 进行对话

局域网共享：`uv run uniclaw --mode webui --host 0.0.0.0`,其他设备可通过你的 IP 地址访问。

**HTTPS 模式**：WebUI 默认启用 HTTPS(自动生成自签名证书)。如需使用 HTTP：`--no-ssl`。指定域名：`--domain uniclaw.example.com`。

**可信 IP 免登录**：在 `settings.json` 中配置 `trusted_ips` 后,指定 IP 的客户端无需登录即可访问 WebUI。

### Q: 支持哪些操作系统？

A: 支持 Windows、Linux 和 macOS。部分工具(如 Everything 搜索)仅在 Windows 上可用。WebUI 模式支持所有平台。

### Q: 如何调试工具调用？

A: REPL 界面会显示详细的工具调用信息：
- 工具名称和参数
- 调用 ID
- 执行结果(截断至 3000 字符)
- Token 使用统计

你也可以使用以下命令获取更多信息：
- `/usage` - 查看详细的 Token 使用统计
- `/context` - 查看上下文 Token 构成分析(各工具占用等)
- `/cost` - 查看按模型计费的费用统计

### Q: 如何使用 MCP 工具？

A: 
1. 添加 MCP 服务器：`/mcp add <名称> <JSON配置>`
2. 查看可用工具：`/mcp tools`
3. AI 会自动识别并调用 MCP 工具

示例添加文件系统工具：
```
/mcp add fs {"transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","D:/code"]}
```

### Q: MCP 服务器连接失败怎么办？

A: 
1. 检查配置是否正确(URL、命令路径等)
2. 确认服务器是否正在运行
3. 检查网络连接和防火墙设置
4. 查看日志获取详细错误信息

### Q: 如何使用斜杠命令？

A: 在 REPL 中输入 `/` 开头的命令即可：
- `/clear` - 清空对话历史
- `/model gpt-4o` - 切换模型(显示能力标签和价格,动态角色菜单)
- `/cd /path/to/dir` - 切换工作目录
- `/skills` - 查看可用技能
- `/memory list` - 查看记忆列表
- `/schedule list` - 查看定时任务
- `/task` - 管理后台任务
- `/cost` - 查看费用统计
- `/doctor` - 环境诊断
- `/checkpoint` - 管理检查点(create/pop/apply/delete/diff)
- `/undo` - 撤销 AI 的文件编辑
- `/resume fork` - 从历史消息处分叉会话
- `/goal` - 设置目标停止条件
- `/explain` - 工具解释模式(on/off/指定工具)
- `/help` - 查看所有可用命令

完整命令列表请参考 [斜杠命令系统](#斜杠命令系统) 章节。

### Q: 如何使用文件补全功能？

A: 在 REPL 中输入 `@` 后会自动显示当前目录下的文件和文件夹：

**基本操作：**
- `@` - 显示当前目录下的所有文件和文件夹
- `@src` - 过滤出以 `src` 开头的文件
- `@src/` - 进入 `src` 目录,显示其内容
- `@src/uniclaw/` - 进入 `src/uniclaw` 目录,显示其内容

**功能特性：**
- 支持多级子目录导航
- 目录名不会自动添加斜杠,用户可手动输入进入下级目录
- 自动过滤隐藏文件(以 `.` 开头的文件)
- 显示文件大小和目录标识

### Q: 如何使用子命令补全功能？

A: 在 REPL 中输入斜杠命令后按空格,会自动显示该命令的子命令：

**使用方法：**
1. 输入命令(如 `/schedule`)
2. 按空格键
3. 会显示该命令的所有子命令(如 `list`, `add`, `remove`, `enable`, `disable`)
4. 输入子命令前缀可进行过滤

**支持子命令的命令：**
- `/memory` - `consolidate`
- `/schedule` - `list`, `add`, `remove`, `enable`, `disable`
- `/mcp` - `list`, `add`, `remove`, `show`, `edit`, `enable`, `disable`, `tools`, `refresh`
- `/permissions` - `list`, `remove`
- `/resume` - `list`, `del`, `search`, `fork`
- `/task` - `list`, `output`, `stop`, `matched`
- `/overseer` - `start`, `stop`
- `/checkpoint` - `create`, `pop`, `apply`, `delete`, `diff`
- `/goal` - `clear`, `status`
- `/kg` - `stats`, `search`, `list`, `export`, `clear`
- `/a2a` - `start`, `stop`, `status`, `list`, `add`, `remove`, `test`

### Q: 如何使用工具解释模式？

A: 工具解释模式让 AI 在调用工具前解释原因,便于理解和调试：

**使用方式**：
```
/explain on              # 开启(所有工具)
/explain off             # 关闭
/explain Bash Read Edit  # 仅对指定工具开启
```

**效果**：开启后,AI 调用工具时会在 `_explain` 参数中说明调用原因,WebUI 和 TUI 都会显示解释文本。

**适用场景**：
- 调试 AI 的工具调用逻辑
- 学习 AI 如何使用工具完成任务
- 审查 AI 的操作意图

### Q: 如何使用图片生成功能？

A: 图片生成功能需要配置支持图片生成的模型(如 DALL-E)：

1. **配置模型**: 在 `settings.json` 中设置 `image_model`
   ```json
   {
     "image_model": "default/dall-e-3"
   }
   ```
2. **使用方式**: 直接告诉 AI 生成图片,如 "帮我生成一张 sunset 海滩的图片"
3. **保存文件**: AI 可指定 `path` 参数将图片保存到本地
4. **直接分析**: 不指定路径时,图片以多模态数据返回供 AI 直接分析

> 💡 配置 `image_model` 后,`GenerateImage` 工具自动可用。`/model` 命令选择模型后,从动态菜单中选择"设为图片生成模型"即可。

### Q: 如何配置可信 IP 免登录？

A: 在 `settings.json` 中添加 `trusted_ips` 配置：

```json
{
  "trusted_ips": ["127.0.0.1"]
}
```

配置后,来自这些 IP 的客户端访问 WebUI 时无需登录认证。适合家庭或办公网络环境中信任的设备。

> ⚠️ **安全提示**: 仅将可信的内部网络 IP 添加到列表中,不要在公网环境中使用此功能。

### Q: 如何使用会话级配置？

A: WebUI 支持为每个会话独立配置模型和参数。当请求带 `session_id` 时,修改会同时生效于两个层面：

1. **全局**: 配置写入 `settings.json`(所有会话下次加载时生效)
2. **会话内存**: 当前会话的 `AppConfig` 立即更新(本轮对话立即生效,无需重启)

1. 在 WebUI 设置页面修改配置
2. 选择"会话级保存" — 配置立即应用于当前会话,同时也保存到全局配置文件

**适用场景**：
- 不同任务使用不同模型
- 临时调整温度参数进行实验,当前会话立即生效

### Q: 如何使用定时任务功能？

A: 使用 `/schedule` 命令管理定时任务：

**创建任务：**
```
/schedule add "0 * * * *" "shell: git status" "check-git"
/schedule add "0 9 * * *" "agent: 总结昨天的代码变更" "daily-report"
/schedule add "0 3 * * *" "py: print('nightly job')" "nightly-python"
```

**管理任务：**
```
/schedule list                # 查看所有任务
/schedule remove abc12345     # 删除任务(使用 list 查看 ID)
/schedule disable abc12345    # 禁用任务
/schedule enable abc12345     # 启用任务
```

调度格式支持 Cron 表达式(分 时 日 月 周),最小粒度 1 分钟：
- `0 * * * *` - 每小时
- `*/5 * * * *` - 每 5 分钟
- `0 9 * * *` - 每天 9:00
- `0 9 * * 1-5` - 工作日 9:00

动作类型支持：
- `shell: <命令>` - 执行 Shell 命令
- `agent: <消息>` - 发送给 AI 处理
- `py: <Python代码>` - 在当前 Python 环境执行代码

### Q: 如何使用后台任务功能？

A: 使用 `/task` 命令管理后台任务，后台任务由 AI 通过 `monitor_start` 工具启动：

**查看任务：**
```
/task                          # 列出所有后台任务
/task output abc123            # 获取任务输出
/task output abc123 100        # 获取最后 100 行输出
```

**管理任务：**
```
/task stop abc123              # 停止任务
/task matched abc123           # 查看监控匹配结果
```

**典型场景：**
- 启动开发服务器：`monitor_start("npm run dev", name="开发服务器")`
- 后台构建项目：`monitor_start("cargo build", watch_pattern="Finished")`
- 下载大文件：`monitor_start("curl -O https://example.com/file.zip")`

### Q: 如何使用语音模式？

A: 语音模式需要配置 TTS 模型和音频设备：

1. **配置 TTS**: 在 `settings.json` 中配置 `tts_model` 和 `audio`
2. **切换模式**: 使用 `/voice on` 开启语音模式,`/voice off` 关闭
3. **查看状态**: `/voice` 查看当前语音模式状态

语音模式下 AI 的回复会自动转换为语音播放。支持多种风格控制(开心/悲伤/温柔等)和方言(东北话/四川话/粤语)。

### Q: Computer Use 模式是什么？

A: Computer Use 模式允许 AI 直接控制您的计算机：

- **屏幕截图**: AI 可以截取屏幕内容进行分析
- **鼠标控制**: 移动、点击、拖拽等操作
- **键盘控制**: 输入文本、按键等

使用 `/cu on` 开启,`/cu off` 关闭。开启后可通过 **Ctrl+U** 全局热键快速切换。

> ⚠️ **安全提示**: Computer Use 模式下 AI 可以直接操作您的计算机,请在可信环境中使用。

### Q: 如何让 AI 发送文件给我？

A: AI 可以通过 `send_file` 工具将文件发送给您：

- **WebUI 模式**: AI 会生成临时下载链接,点击即可下载(默认 30 分钟有效)
- **微信模式**: AI 会通过微信直接发送文件
- **Console 模式**: 暂不支持文件发送

示例对话: "帮我生成一个 Python 脚本,然后发送给我"

### Q: 知识图谱如何使用？

A: 知识图谱支持手动添加和自动提取两种方式：

**手动添加**:
```
# 添加实体
kg_add_entity(name="Python", type="technology", description="编程语言")

# 添加关系
kg_add_relation(source="Guido", target="Python", relation="created_by")
```

**自动提取**:
```
# 从文本提取
kg_extract(text="Python 是一种解释型编程语言...")

# 从文件提取
kg_extract(path="docs/README.md")
```

**查询**:
```
kg_search(keyword="Python")           # 搜索
kg_get_entity(name="Python")          # 详情
kg_neighbors(name="Python", depth=2)  # 邻居
kg_path(source="A", target="B")       # 路径
```

**斜杠命令**:
```bash
/kg                        # 统计信息
/kg search Python          # 搜索
/kg list person            # 列出实体
/kg export html            # 导出可视化
```

详细使用方法请参考 [知识图谱系统](#-知识图谱系统) 章节。

### Q: 如何在 WebUI 中上传文件？

A: WebUI 支持多种文件上传方式：

**拖拽上传**:
1. 将文件直接拖拽到聊天区域
2. 系统自动识别文件类型并添加为附件
3. 支持图片、音频、视频等多媒体文件

**文件大小限制**:
- 系统会自动限制上传文件大小
- 过大的文件会提示错误

**多媒体支持**:
- 图片: PNG、JPG、GIF 等
- 音频: MP3、WAV 等
- 视频: MP4 等

### Q: WebUI 支持手机访问吗？

A: 是的,WebUI 完美支持移动端访问：

**响应式设计**:
- 自动适配手机和平板屏幕
- 触摸手势支持
- 移动端优化的 UI 布局

**局域网访问**:
```bash
# 启动时指定 host
uv run uniclaw --mode webui --host 0.0.0.0

# 手机浏览器访问
http://你的IP地址:8080
```

**功能完整**:
- 对话功能完整可用
- 文件上传支持
- 工具调用可视化

### Q: 如何使用 A2A 远程代理功能？

A: A2A 功能允许 UniClaw 与其他 Agent 系统互操作：

**作为客户端调用外部 Agent：**
1. 添加远程 Agent：`/a2a add <名称> <URL> [token]`
2. 测试连接：`/a2a test <名称>`
3. 让 AI 调用：AI 会自动使用 `a2a_send_task` 或 `a2a_submit_task` 工具

**将 UniClaw 暴露为 A2A 端点：**
1. 以 WebUI 模式启动：`uv run uniclaw --mode webui`
2. 启用 A2A 端点：`/a2a start`
3. 查看 Token：`/a2a status`
4. 其他 Agent 可通过 `/.well-known/agent-card.json` 发现服务能力

**多轮对话：** 使用 `context_id` 维持对话连续性,每轮返回的 `context_id` 传入下一轮请求。

**异步任务：** 使用 `a2a_submit_task` 提交后立即返回,任务完成时自动唤醒 AI。

### Q: A2A 服务的安全性如何保障？

A: A2A 服务使用 Bearer Token 认证：
- 启动时自动生成随机 Token（也可手动指定）
- 所有请求必须在 `Authorization` header 中携带 Token
- Token 存储在 `~/.UniClaw/a2a/a2a.json`
- A2A 任务使用独立的 `AppConfig`,与主会话隔离

> ⚠️ 建议仅在可信网络环境中使用 A2A 服务,不要将 Token 泄露给不可信的第三方。

### Q: 如何使用插件系统？

A: 插件系统允许你在不修改 UniClaw 源码的情况下扩展 AI 的工具能力:

**快速开始(推荐):**
直接告诉 AI 你需要什么工具:
```
帮我写一个天气查询工具,输入城市名返回天气信息
```
AI 会通过 `/tool-plugin-forge` 技能自动完成: 捕获意图 → 检查重名 → 生成代码 → 验证加载。

**手动编写:**
1. 在 `~/.UniClaw/plugins/tools/` 下创建 `.py` 文件
2. 使用 `@tool` 装饰器定义工具函数
3. 实现 `get_tools()` 函数返回工具列表
4. 调用 `search_tools` 触发加载

**注意事项:**
- 工具名不能与内置工具重名(可用 `list_builtin_tools` 查看)
- 文件名不能以下划线开头
- 耗时任务必须用 async + wake_agent 模式

详细规范请参考 [插件系统](#-插件系统) 章节。

### Q: 插件工具与内置工具有什么区别？

A:
- **内置工具**: 随 UniClaw 一起安装,始终可用,由开发者维护
- **插件工具**: 用户自定义,放在 `~/.UniClaw/plugins/tools/` 目录,支持热加载和热重载
- **加载方式**: 内置工具有核心(始终加载)和扩展(按需发现)之分;插件工具通过 PluginManager 统一管理
- **命名冲突**: 插件工具名不能与内置工具重名,否则会被跳过

### Q: 插件加载失败怎么办？

A: 常见排查步骤:
1. 检查文件路径是否在 `~/.UniClaw/plugins/tools/` 下
2. 检查文件名是否以下划线开头(会被跳过)
3. 检查是否有 `get_tools()` 函数且返回非空列表
4. 检查工具名是否与内置工具重名(调用 `list_builtin_tools` 查看)
5. 检查 Python 语法是否正确
6. 查看日志中的 warning 信息获取具体错误原因

## 📄 许可证

本项目采用 MIT 许可证。
