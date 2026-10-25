# UniClaw 🦞

**English** · [简体中文](README.md)

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**UniClaw** is an LLM-powered intelligent agent system with an interactive command-line interface, WebUI, and WeChat integration. It supports file operations, Shell command execution, web search, memory management, multi-agent collaboration, scheduled tasks, and many more capabilities. With a modular tool system, a fully async architecture, and a layered permission mechanism, it helps you get programming and text-processing tasks done efficiently.

> 📦 The project uses the **src layout** — all source code lives under `src/uniclaw/`.

## 🎬 Demo Videos

| UniClaw Intro | WebUI Guide |
|:---:|:---:|
| [▶ Play](https://github.com/user-attachments/assets/4d4dc952-7536-4f33-883b-bfb2149b1049) | [▶ Play](https://github.com/user-attachments/assets/5ae0010f-6770-4507-b34c-d832be8b1ac7) |

## ✨ Features

- 🤖 **Smart Agent**: A fully-async conversational AI assistant built on the OpenAI SDK and Anthropic SDK, with automatic multi-provider routing and streaming parsing of `reasoning_content` and thought tags; built-in infinite-loop detection prevents tool calls from spinning forever
- 🛠️ **Unified ToolRuntime**: Tool functions get config, call ID, and streaming output through a `tool_runtime` parameter, which the `@tool` decorator automatically hides from the schema; streaming routes by `tool_call_id`, so even async/background tasks stream into the correct tool block
- 🔄 **Multi-model fallback**: When the primary model fails, backup models are tried automatically; configure a list via `model_name` for higher availability
- 🩺 **Error-classified retry**: A built-in error classifier buckets LLM call failures into six categories (RATE_LIMIT/CONTEXT_OVERFLOW/AUTH/SERVER_ERROR/TIMEOUT/UNKNOWN) with differentiated retry and backoff per category (CONTEXT_OVERFLOW compacts the session before retrying; exponential/linear backoff); LLM requests default to a 180-second timeout, overridable per call
- 🎓 **Advisor models**: Configure `large_model_name` as advisor models; when stuck on a hard problem, consult several more powerful models in parallel for a second opinion
- 🔍 **Tool registry**: BM25-powered smart tool search — always-loaded core tools plus on-demand discovery of extended tools (via the `search_tools` meta-tool). An LRU + energy mechanism (30 energy points per tool, -1 per round, restored to full on call/search, evicted at zero) manages loaded tools to optimize the prompt cache
- 📡 **Tool availability tracking**: Environment detection records unvailable tools and why (e.g. Docker not installed, `embedding_model` not configured); `search_tools` surfaces the reason to avoid useless calls; calling a disabled tool directly returns "not enabled + reason" rather than a misleading "tool does not exist"
- 🌐 **WebUI**: A WebSocket-based web interface to chat with the AI from a browser — LAN-shareable, mobile-responsive with touch gestures, HTTPS and IPv6 support
- 🎨 **A2UI interactive UI**: The agent emits `a2ui` code blocks declaring JSON component trees (cards/forms/button groups/tabs/charts), which a frontend whitelist renders into interactive UIs; button and form results flow back to the agent, closing a two-way interaction loop. The component spec is now loaded on demand via the `get_a2ui_guide` tool instead of occupying the resident prompt
- ⚡ **Streaming tool output**: Tool execution pushes progress to the frontend in real time — no need to wait for completion
- 🔐 **Trusted IP auto-login**: Configure `trusted_ips` and clients from those addresses skip WebUI login — perfect for home/office LANs
- 💬 **WeChat integration**: Connect WeChat through the iLink Bot protocol for mobile interaction
- 🧠 **Memory system**: Persistent memory management for user preferences, project information, and feedback
- 🗺️ **Knowledge graph**: A SQLite-based knowledge graph with entity/relation management, full-text search, path discovery, auto-extraction, and visual export, managed at both user and project scope
- 📚 **RAG retrieval**: Document ingestion, smart chunking, vector embedding, and semantic retrieval, with multi-collection management and user/project dual scope
- 👥 **Multi-agent collaboration**: The fully-async architecture supports creating and managing specialized agents for task division and inter-agent communication
- 🖥️ **Computer use**: Screenshot, mouse, and keyboard automation (platform-agnostic async implementation); the global hotkey Ctrl+U emergency-stops running write operations, and `/cu` toggles enablement
- 🔊 **Text-to-speech**: TTS with style/emotion/dialect control; `/voice` toggles voice mode
- 🎨 **Image generation**: Configure `image_model` for AI text-to-image; save to a file or analyze directly, with custom sizes
- 📤 **File sending**: The AI can send generated files to you — download links in WebUI, direct send over WeChat
- 📋 **Todo list**: Task decomposition and tracking with automatic progress management and state transitions
- ⏰ **Scheduled tasks**: Create and manage recurring or one-shot scheduled tasks, with session association and a monitor task type (command exit code triggers the agent)
- 🔄 **Background processes**: Start and manage async background processes with input/output control; process end (natural exit / timeout / exception) auto-notifies and wakes the agent
- 🎯 **Jev structured decisions**: TypeSafe SDK integration for fast, low-cost structured AI decisions (select_one/select_many/yes_no/rate), applied to memory semantic search, fast security screening, RAG document reranking, and smart skill recommendation — falls back to the LLM when Jev is unavailable
- 🛡️ **Infinite-loop detection**: Detects consecutive identical tool calls (using result fingerprints to tell "spinning in place" from "making progress every step"), breaks the loop, and steers toward a different strategy
- ✂️ **Output truncation detection**: Uses `finish_reason` (length/max_tokens) to detect when LLM output hit the length cap and got truncated, and warns that content/tool args may be incomplete; when tool arg parsing fails it distinguishes "truncated generation" from "corrupted args", with clear guidance to retry in chunks or raise `max_tokens`
- 💾 **Auto-save**: Sessions persist automatically at conversation end; the memory system periodically reviews sessions and extracts long-term knowledge
- 🪝 **Hook system**: Event-driven shell command hooks for session and tool lifecycle events
- 🔔 **Desktop notifications**: Windows/macOS/Linux notifications when tasks finish
- 📝 **Plan mode**: Enter plan mode to design a solution first, then execute after you confirm it
- 🔧 **Tool explanation mode**: Enabled via `/explain`; the AI explains its reason before each tool call, making the tool-call logic easy to understand and debug
- 🐍 **IPython kernel**: A persistent Python execution environment where variables survive across calls, with magic commands and rich output
- 🔤 **LSP language intelligence**: Language-Server-Protocol code intelligence — type diagnostics, go-to-definition, hover, references, and symbol outline, with built-in pyright/TypeScript/HTML/CSS servers, lazy startup, and crash auto-restart
- 📝 **Batch file editing**: The `apply_patch` tool performs add/update/delete/move across multiple files in a single call using a standard patch text protocol, with three-level lenient context matching, CRLF handling, and rejection of binary files and paths escaping the workspace, plus `dry_run` preview
- 🛠️ **Rich built-in tools**: file-system operations, Shell commands, web search, and the skill system, among others
- 🔒 **Permission management**: Multiple permission modes (auto/manual/accept-all) for safe operations
- 📋 **Persistent rules**: Custom permission rules that remember your preferences and avoid repeated confirmations
- 💭 **Real-time feedback**: Shows the thinking chain, tool call details, and token usage
- ♾️ **Infinite context**: Dual-list storage (active context + full history) + 3-level auto compaction (50%/70%/85%) + on-demand history recall — the conversation never loses memory; tool call messages dedupe by content hash and repeated results collapse to save context
- 📝 **Session notes**: Notes persist with the session, their summaries are auto-injected during compaction so key facts survive compression; full CRUD support
- 🕐 **Time awareness**: When you resume after 1+ hours or across days, the current time and the gap are auto-injected so the AI senses the break (mid-run interjections don't trigger)
- 📊 **Context management**: Monitors context length and auto-compacts at three pressure levels (50%/70%/85%)
- 🎯 **Goal mode**: Set a goal; when the agent stops, an independent judge model evaluates whether it is met and re-injects feedback to continue if not
- 🌐 **Platform search**: Concurrent multi-platform search (GitHub/arXiv/Stack Overflow/Hacker News/Bilibili, etc.)
- 📚 **Extended search platforms**: OpenAlex/Google News/Reddit/Polymarket/SEC EDGAR/HuggingFace (models/datasets)/alphaXiv and more; OpenAlex retries on 429
- 🔎 **Smart search**: `webSearch` defaults to Exa, with Bing, DuckDuckGo, or other platforms on demand; built-in Exa MCP server, time-range filtering, and LLM smart reranking
- 🌍 **Browser automation**: Playwright-based browser control — navigate/click/type/screenshot/JS evaluation, multi-tab support, and CDP attachment to an external browser
- ⬇️ **HTTP downloads**: Multi-coroutine concurrency, resumable transfers, proxy support, auto retry, and file verification, in sync and async modes
- 🎞️ **M3U8/HLS streams**: Stream download with master/sub-stream parsing, custom headers, retries, proxy, and resume — managed separately from the HTTP downloader
- 🔄 **Visible terminal monitor**: Background processes support a `visible` real console window (ConPTY pseudo-terminal) for direct interaction — screen snapshots, window-size sync, input forwarding, and multi-line command detection
- 🔁 **Process restart**: WebUI's `restart_agent` tool restarts its own process — saving the session gracefully, hot-loading new code, then resuming the session and waking the agent
- 📋 **Hybrid KG search**: FTS5 prefix matching + LIKE substring fallback for mixed Chinese/English queries, covering the alias table
- 🔌 **Plugin system**: User-level external tool plugins hot-loaded from `~/.UniClaw/plugins/tools/`; multi-file split, lifecycle hooks, and builtin-name collision detection
- 🔨 **Tool plugin forge**: The `/tool-plugin-forge` skill generates tool plugins in one shot — capture intent → check collisions → generate code → actually load and verify; async wake mode for long-running tasks
- 🎯 **Skill system**: Extensible skill mechanism with support for custom task templates and workflows
- 🎨 **Theme customization**: Built-in `uniclaw-theme` skill for custom WebUI colors, fonts, and styles
- 🔌 **MCP integration**: Model Context Protocol (mcp 2.x) with async command management, persistent session reuse, and multimodal results
- 🌉 **A2A remote agents**: Agent2Agent protocol — call external A2A agents or expose UniClaw as an A2A endpoint for multi-agent collaboration
- ⏱️ **Async wait**: The `sleep_timer` tool wakes the agent up later without blocking the main loop
- 📸 **Git checkpoints**: Automatic git stash checkpoints with one-key rollback of AI file edits; .gitignore-aware and history-pollution-free; the file-snapshot mode in non-git directories fully supports Chinese/special filenames
- 📝 **Slash commands**: A rich built-in command system for session management, model switching, task management, and more
- 📁 **File completion**: `@` auto-completes filenames with multi-level subdirectory navigation
- 🔧 **Subcommand completion**: Slash commands autocomplete subcommands — press space to see available ones
- 💬 **Conversation management**: View/load/delete/search/fork history conversations; history messages are auto-deduplicated
- 🎨 **TUI interface**: A polished terminal UI with detail/compact toggle (F2) and a conversation sidebar
- 📈 **Usage stats**: Real-time token usage, tool call statistics, and cost details; prices fetched from the OpenRouter API automatically
- ⚙️ **Session-level config**: WebUI saves config with a `session_id` — applied to the current session in memory immediately while also written to the global config
- 🌐 **Cross-platform**: Windows, Linux, and macOS

## 📋 Table of Contents

- [Demo Videos](#-demo-videos)
- [Installation](#-installation)
- [Quick Start](#-quick-start)
- [Docker Deployment](#-docker-deployment)
- [Configuration](#-configuration)
- [Usage Guide](#-usage-guide)
- [WebUI Mode](#-webui-mode)
- [A2UI Interactive UI](#-a2ui-interactive-ui)
- [WeChat Bot Integration](#-wechat-bot-integration)
- [Tool System](#-tool-system)
- [Plugin System](#-plugin-system)
- [Knowledge Graph System](#-knowledge-graph-system)
- [RAG Retrieval System](#-rag-retrieval-system)
- [MCP Integration](#-mcp-integration)
- [A2A Remote Agents](#-a2a-remote-agents)
- [Architecture](#-architecture)
- [FAQ](#-faq)

## 🚀 Installation

### Prerequisites

- Python 3.11 or later
- The `uv` package manager
- Optional: Docker (for the Docker container tool), Everything (Windows filename search acceleration), language servers (for LSP code intelligence)

```bash
# Install project dependencies
uv sync

# Or install dev dependencies (includes testing tools)
uv sync --group dev

# Or install globally as a command-line tool
uv tool install .

# Optional: LSP language intelligence support (pyright ships its own binary, no system Node needed)
uv pip install 'uniclaw[lsp]'
```

**Configuration file**

On the first start in console mode without a configured provider, a setup wizard (`run_setup_wizard()`) runs automatically to guide you through API address, key, and model configuration. WebUI mode does not run the interactive wizard — configure a provider through the WebUI settings page, or create the config file in advance:

```
# Project level
.UniClaw/settings.json

# User level (global)
~/.UniClaw/settings.json
```

The config file is JSON:

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

> 💡 Multiple providers are supported via the `providers` dict. `model_name` is a list — the first entry is the primary model, the rest are fallbacks. Only console mode runs the setup wizard on first start when no provider is configured.

**Run the project**

```
# Run with uv (console mode, default)
uv run uniclaw

# Run with uv (WebUI mode)
uv run uniclaw --mode webui
uv run uniclaw --mode webui --host 0.0.0.0  # LAN access
uv run uniclaw --mode webui --host 0.0.0.0 --no-ssl  # disable HTTPS

# Or run the entry file directly
uv run python src/uniclaw/main.py

# If installed via uv tool install, run directly
uniclaw
uniclaw --mode webui
```

**Common uv commands**

```
# Add a dependency
uv add <package-name>

# Add a dev dependency
uv add --dev <package-name>

# Run tests
uv run pytest tests/ -v -m 'not slow' -n auto   # daily tests (parallel, skips slow Docker tests)
uv run pytest tests/ -v                          # full test suite (serial)

# Update dependencies
uv lock --upgrade

# Format code
uvx black .
```

## 🎯 Quick Start

### Start an interactive session

```
# Run with uv (console mode, default)
uv run uniclaw
```

This lands you in the REPL (Read-Eval-Print Loop) interface:

```
[UniClaw] 5% » hello, introduce yourself
💭 [Thinking]
I am an AI assistant...

📝 [Reply]
Hi! I am the UniClaw assistant...
```

### Start the WebUI

```
# WebUI mode (local access only)
uv run uniclaw --mode webui

# LAN access
uv run uniclaw --mode webui --host 0.0.0.0

# Disable HTTPS (use HTTP)
uv run uniclaw --mode webui --host 0.0.0.0 --no-ssl

# Specify a domain (for the HTTPS certificate)
uv run uniclaw --mode webui --host 0.0.0.0 --domain uniclaw.example.com
```

See the [WebUI Mode](#-webui-mode) section for details.

### Start the WeChat bot

The WeChat bot is integrated into WebUI mode — start WebUI and manage accounts from the sidebar:

```
uv run uniclaw --mode webui
```

See the [WeChat Bot Integration](#-wechat-bot-integration) section for details.

### Basic usage

- **Type freely**: Chat with the AI assistant
- **! commands**: Run shell commands (e.g. `!ls -la`)
- **/ commands**: Run built-in slash commands (e.g. `/clear`, `/model gpt-4o`)
- **@ file completion**: Type `@` to auto-complete filenames, with multi-level subdirectory navigation
- **Empty line**: Skip the current input
- **Token indicator**: The right side shows current context usage (color-coded: green < 40%, yellow 40-70%, red > 70%)

### File completion

Type `@` to quickly complete filenames for use in the conversation:

#### Basic usage

- `@` — lists all files and folders in the current directory
- `@src` — filters files starting with `src`
- `@src/` — lists the contents of the `src` directory
- `@src/uniclaw/` — lists the contents of the `src/uniclaw` directory

#### Examples

```
# List current directory files
@

# Enter a subdirectory
@src/
@src/uniclaw/
@src/uniclaw/console/

# Filter files
@src/uniclaw/console/run
```

#### Features

- ✅ Multi-level subdirectory navigation
- ✅ Hidden files (starting with `.`) are filtered out automatically
- ✅ Shows file sizes and directory markers
- ✅ Both `/` and `\` path separators supported
- ✅ Directory names don't get a trailing slash automatically; type it manually to go deeper

### Subcommand completion

Slash commands autocomplete subcommands — type the command and press space to see available subcommands:

#### Commands with subcommands

> 💡 This table reflects the subcommand groups actually registered in `COMMAND_SUBCOMMANDS`.

| Command | Subcommands |
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

#### Examples

```
# Type the command then press space to show subcommands
/schedule
# Shows: list, add, remove, enable, disable

# Type a prefix to filter
/schedule a
# Shows: add

# Full subcommand
/schedule add "0 * * * *" "shell: git status"
```

### Workspace

UniClaw uses a workspace concept to manage file access scope:

- **root_dir**: The session working directory (`config.root_dir`), changeable via `/cwd`; subagents may have their own root_dir
- **Current directory**: The process working directory at launch (`Path.cwd()`), fixed for the process lifetime
- **Extra workspace directories**: Other directories added via `/add_dir <path>`, valid for the current session only

> ⚠️ **Note**: `root_dir` and `Path.cwd()` are two different concepts — don't mix them. File I/O, security checks, memory storage, and skill loading use `root_dir`.

```bash
/add_dir D:/projects/other-project  # add an extra workspace directory
/add_dir                             # list current workspace directories
```

## 🐳 Docker Deployment

UniClaw ships an official Dockerfile for containerized WebUI deployment.

### Quick build & run

```bash
# Build the image
docker build -t uniclaw .

# Run the container (default port 8082, config file mounted only; sessions and other data stay inside the container)
docker run -d --name uniclaw \
  -p 8082:8082 \
  -v ~/.UniClaw/settings.json:/root/.UniClaw/settings.json \
  uniclaw
```

> 💡 Only `settings.json` is mounted to share secrets like API keys; sessions, memory, and checkpoints inside the container stay isolated from the host to avoid path/data mixing.

### Custom configuration

```bash
# Set environment variables
docker run -d --name uniclaw \
  -p 8082:8082 \
  -v ~/.UniClaw/settings.json:/root/.UniClaw/settings.json \
  -e HTTPS_PROXY=http://host.docker.internal:7890 \
  uniclaw

# Custom port
docker run -d --name uniclaw \
  -p 9090:9090 \
  -v ~/.UniClaw/settings.json:/root/.UniClaw/settings.json \
  uniclaw uv run uniclaw --mode webui --host 0.0.0.0 --port 9090
```

### Dockerfile highlights

- Based on `python:3.14-slim` for a small image
- `uv` manages dependencies for fast builds
- Playwright Chromium pre-installed (for browser automation)
- Production dependencies only — dev tools excluded
- The build toolchain is cleaned up after the build to shrink the final image

> 💡 **Tip**: Docker deployment starts in WebUI mode by default, listening on `0.0.0.0:8082`. HTTPS is enabled by default (self-signed cert); disable it with `--no-ssl`.

---

## ⚙️ Configuration

### Config keys

| Key | Description | Default | Example |
|--------|------|--------|------|
| `providers` | Multi-provider configuration (dict) | required | see below |
| `model_name` | Primary model list (first is primary, the rest fallback) | none | `["default/gpt-5.4"]` |
| `mini_model_name` | Mini model list (for simple tasks) | auto-uses model_name | `["default/gpt-4o-mini"]` |
| `multimodal_model_name` | Multimodal model list (used when the primary can't do multimodal) | none | `["default/gpt-4o"]` |
| `large_model_name` | Advisor model list (for advice from more powerful models) | none | `["default/o3"]` |
| `image_model` | Image generation model (leave empty to disable) | `""` | `"default/dall-e-3"` |
| `tts_model` | TTS model (leave empty to disable) | `""` | `"default/tts-1"` |
| `asr_model` | ASR model (leave empty to disable) | `""` | `"default/whisper-1"` |
| `embedding_model` | Embedding model (for RAG semantic retrieval, leave empty to disable) | `""` | `"default/text-embedding-3-small"` |
| `temperature` | Generation temperature | `0.7` | `0.0`-`2.0` |
| `max_tokens` | Max output tokens | `null` (unlimited) | `512`, `2048` |
| `top_p` | Nucleus sampling probability | `null` (unlimited) | `0.9` |
| `proxy_url` | HTTP proxy URL | `""` | `http://127.0.0.1:7890` |
| `GITHUB_TOKEN` | GitHub Token (speeds up platform search) | empty | `ghp_xxx` |
| `EXA_API_KEY` | Exa API key (semantic search engine, preferred by webSearch) | empty | `exa-xxx` |
| `max_agent_depth` | Max nested agent depth | `2` | `1`-`5` |
| `permission_timeout` | Permission dialog timeout (seconds) | `300` | `60`-`600` |
| `trusted_ips` | Trusted IP list (skip WebUI login auth) | `[]` | `["192.168.1.100"]` |

**Provider config example:**

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

> 💡 `protocol` supports both `"openai"` and `"anthropic"`. The system routes to the matching provider automatically based on the provider prefix in the model name (e.g. `default/`, `anthropic/`).

**Timeouts:**
- `timeout`: connection timeout (seconds), auto-set per protocol type by default
  - SSE: 5 seconds
  - Streamable HTTP: 10 seconds
  - Tool discovery: 15 seconds
- **Provider request timeout**: LLM API requests default to 180 seconds (3 minutes), overridable via the `timeout` parameter on each provider routing method

### Permission modes

- **auto**: Decides based on tool type, authorized directories, persistent rules, and safety checks; operations that can't be safely auto-approved ask the user
- **manual**: Every tool call needs manual confirmation
- **accept-all**: Auto-approve everything (use with caution)
- **plan**: Plan mode (entered via a tool call)

**How to set:**
- Toggle in the WebUI settings page
- Set the `permission_mode` field in `settings.json`
- Console: press **Shift+Tab** to cycle through permission modes

### Persistent permission rules 🔒

UniClaw lets you define persistent permission rules to remember your preferences:

**Bash command rules:**
- Prefix-based matching (e.g. `git commit`, `npm install`)
- Once authorized, similar commands are auto-approved
- Stored in the project's `permission_rules.json`

**Tool rules:**
- Exact tool-name matching (e.g. `Write`, `Edit`)
- Once authorized, all calls to that tool are auto-approved

**Manage commands:**
```bash
/permissions list              # view all permission rules
/permissions remove <type> <pattern>  # remove a rule
```

> 💡 **How rules get added**: `/permissions` itself only supports `list` and `remove`. Rules are saved automatically when you press **`a`** (always allow) in the permission confirmation dialog — that call auto-invokes `add_permission_rule` to write `permission_rules.json`. No manual entry needed.

**Example:**
```bash
# When the AI requests permission, press a in the confirmation dialog
# → the rule is saved to permission_rules.json automatically

# View current rules
/permissions list

# Remove a rule
/permissions remove bash "git commit"
```

> 💡 **Tip**: Persistent rules have high priority in the security check flow, but dangerous-operator detection (e.g. `;`, `&&`, `||`) still blocks them for safety.

### Project instruction files (AGENTS.md / CLAUDE.md)

Create an `AGENTS.md` (preferred) or `CLAUDE.md` (fallback) file in the project root to provide project-specific instructions and conventions to the AI. The file is loaded into the system prompt automatically on every conversation.

**Example CLAUDE.md:**

```
# Project conventions

## Code style
- Strict TypeScript mode
- Functional components + Hooks
- Tailwind CSS for styling

## Naming
- Files: kebab-case
- Components: PascalCase
- Variables: camelCase

## Git commit conventions
- feat: new feature
- fix: bug fix
- docs: documentation
- refactor: refactor
```

**Features:**
- **Priority**: When both exist in the same directory, `AGENTS.md` wins over `CLAUDE.md`
- **Root instruction**: The instruction file in the session root is loaded into the system prompt on every conversation
- **Subdirectory on-demand loading**: When a tool touches a file in a subdirectory, UniClaw looks upward for the nearest `AGENTS.md`/`CLAUDE.md` and injects it; the same subdirectory is deduplicated within the current message flow. Only paths inside trusted roots (session `root_dir` + workspace dirs added via `/add_dir`) are loaded; paths outside trusted roots are not
- 10KB size limit (content beyond it is truncated with a marker)
- Built-in prompt-injection guard (filters dangerous patterns like "ignore previous instructions", impersonated system messages, etc.)
- `/init` follows the AGENTS.md-first strategy when generating project instruction files

## 📖 Usage Guide

### Interactive session features

#### Token usage monitor

The session interface shows the current context token usage in real time:

- 🟢 **Green (<40%)**: low usage, plenty of room
- 🟡 **Yellow (40-70%)**: medium usage, keep an eye on it
- 🔴 **Red (>70%)**: high usage, near the limit

#### Detailed display mode 📊

Press **F2** to toggle between detail and compact display:

**Compact mode (default)**:
- Shows only the AI's thinking and replies
- Hides tool-call metadata
- Cleaner, for daily use

**Detail mode**:
- Shows full tool-call info (args, call ID, etc.)
- Shows token usage (input/output tokens)
- Shows model name and tool call count
- Shows file diff details
- Great for debugging and exploring how the AI works

> 💡 **Tip**: Press **F2** to toggle detail/compact display.

#### Event types

The system displays the following event types:

- 💭 **Thinking**: the AI's reasoning chain
- 📝 **Reply**: the AI's text reply
- 🤖 **Assistant metadata**: tool call count, args, token usage
- 🔧 **Tool execution**: tool name, call ID, result

#### Conversation sidebar 💬

The left side of the TUI shows a conversation history sidebar for managing and switching sessions:

**Features:**
- 📋 **Auto-save**: every conversation is saved to history at the end
- 🔄 **Quick load**: select and load a history conversation from the sidebar (press Enter)
- 🔍 **Status markers**:
  - `>` marks the currently selected conversation
  - `*` marks the currently active session
- ⌨️ **Keybindings**:
  - `Ctrl+K`: focus/leave the sidebar
  - `↑/↓`: move up/down in the sidebar
  - `Enter`: load the selected conversation
  - any other key: return to the main input

**Flow:**
1. Press `Ctrl+K` to focus the sidebar
2. Use the arrow keys to select a conversation
3. Press `Enter` to load its history
4. Keep chatting on top of it

> 💡 **Tip**: Use `/resume` for more fine-grained conversation management, including search and delete.

### Slash command system

UniClaw ships a rich set of slash commands (`/command`) for managing system features and performing operations.

**Subcommand completion**: type the command and press space to see its subcommands. For example, `/schedule ` shows `list`, `add`, `remove`, `enable`, `disable`.

#### Session management

| Command | Description | Example |
|------|------|------|
| `/clear` or `/cls` | Clear the current conversation history | `/clear` |
| `/compact` | Compact the context to optimize token usage | `/compact` |
| `/export` | Export the current session | `/export session.md` |
| `/resume` | Resume/manage sessions (list/del/search/fork) | `/resume list` |
| `/resume list` | List all history sessions | `/resume list` |
| `/resume del <id>` | Delete a session | `/resume del abc123` |
| `/resume search <keyword>` | Search session content | `/resume search python` |
| `/resume fork [id] [idx]` | Fork a session (start a new session from a message) | `/resume fork abc123 5` |

#### Git checkpoint commands

| Command | Description | Example |
|------|------|------|
| `/checkpoint` (alias `/cp`) | List all checkpoints | `/checkpoint` |
| `/checkpoint diff` | View uncommitted changes | `/checkpoint diff` |
| `/checkpoint diff <index>` | Current changes vs. a checkpoint | `/checkpoint diff 0` |
| `/checkpoint diff <a> <b>` | Compare two checkpoints | `/checkpoint diff 0 2` |
| `/checkpoint pop` | Restore the latest checkpoint and delete it | `/checkpoint pop` |
| `/checkpoint pop <index>` | Restore a checkpoint and delete it | `/checkpoint pop 2` |
| `/checkpoint apply` | Restore the latest checkpoint (keep it) | `/checkpoint apply` |
| `/checkpoint apply <index>` | Restore a checkpoint (keep it) | `/checkpoint apply 2` |
| `/checkpoint delete <index>` | Delete a checkpoint | `/checkpoint delete 2` |
| `/checkpoint <index>` | Restore a checkpoint (keep it) | `/checkpoint 3` |
| `/undo` | Undo AI file edits by restoring a checkpoint (kept) | `/undo`、`/undo 2` |

> 💡 A checkpoint is created automatically before each assistant turn, with two modes: `git stash` in git repos and an automatic file-snapshot fallback in non-git directories. .gitignore is honored and git history stays clean. The snapshot mode emits paths via `git ls-files -z` as raw bytes, so Chinese/special filenames aren't escaped and silently dropped.
>
> 💡 **HEAD anchor mechanism**: when the working tree is clean, `git stash` can't create an entry — UniClaw records a lightweight anchor in `.git/uniclaw-head-anchors.json` (HEAD hash + timestamp) so every chat turn still has a recoverable checkpoint. Stash entries and HEAD anchors merge into one time-descending index (list/pop/apply/diff/delete all use the same ordering); restoring an anchor means discarding all uncommitted changes back to a clean HEAD. `stash apply` uses `--index` to restore the staging state, degrading to a bare `apply` on failure.

#### Model commands

| Command | Description | Example |
|------|------|------|
| `/model` | View or switch models, showing capability tags and prices, with role assignment | `/model gpt-4o` |
| `/model <keyword>` | Fuzzy-search models (provider-prefix filter supported) | `/model gpt-4o` |
| `/model <provider>/<model>` | Switch directly to a model | `/model default/gpt-4o` |

> 💡 `/model` shows capability tags per model: 👁 image input, 🎬 video input, 🎵 audio input, 🔧 tool calling, plus input/output prices (per million tokens). After picking a model, available roles appear dynamically (primary/mini/multimodal/advisor/TTS/ASR/image-gen/embedding).

#### Working directory commands

| Command | Description | Example |
|------|------|------|
| `/cwd`, `/cd`, or `/pwd` | View or change the working directory | `/cd /path/to/project` |

#### Skill commands

| Command | Description | Example |
|------|------|------|
| `/skills` | List all available skills | `/skills` |

#### Memory commands

| Command | Description | Example |
|------|------|------|
| `/memory list` | List all memory entries | `/memory list` |
| `/memory search <keyword>` | Search related memories | `/memory search code style` |
| `/memory delete <name>` | Delete a memory | `/memory delete user-preference-theme` |
| `/memory-organize` | Memory manager: organize memories, mine sessions | `/memory-organize all` |
| `/skill-forge` | Skill forge: create and refine custom skills | `/skill-forge all` |

#### MCP management commands

| Command | Description | Example |
|------|------|------|
| `/mcp list` | List configured MCP servers | `/mcp list` |
| `/mcp add <name> [JSON]` | Add an MCP server | `/mcp add fs {"transport":"stdio",...}` |
| `/mcp remove <name>` | Remove an MCP server | `/mcp remove fs` |
| `/mcp enable <name>` | Enable an MCP server | `/mcp enable fs` |
| `/mcp disable <name>` | Disable an MCP server | `/mcp disable fs` |
| `/mcp tools` | List available MCP tools | `/mcp tools` |

#### Scheduled task commands ⏰

| Command | Description | Example |
|------|------|------|
| `/schedule list` | List all scheduled tasks | `/schedule list` |
| `/schedule add <cron> <action> [name]` | Create a scheduled task (auto ID) | `/schedule add "0 * * * *" "shell: git status"` |
| `/schedule remove <id>` | Remove a scheduled task | `/schedule remove abc12345` |
| `/schedule enable <id>` | Enable a scheduled task | `/schedule enable abc12345` |
| `/schedule disable <id>` | Disable a scheduled task | `/schedule disable abc12345` |

**Cron format:**
- `minute hour day month weekday` — standard 5-field cron, 1-minute granularity
- Examples: `0 * * * *` every hour, `*/5 * * * *` every 5 minutes, `0 9 * * *` daily at 9:00, `0 9 * * 1-5` weekdays at 9:00

**Action types:**
- `shell: <command>` — run a shell command
- `agent: <message>` — send to the AI to handle
- `agent:<type>: <message>` — target a specific subagent type (e.g. `agent:coder: refactor this code`)
- `py: <python>` — execute code in the current Python environment
- `monitor: <command> → agent[:<type>]: <message>` — run a shell command; if its exit code is non-zero, trigger the agent

**Session association:**
- A scheduled task auto-associates with the current session and reuses that session's agent when it fires
- Cross-session execution: in TUI mode, tasks for a different session run in the background
- In WebUI mode, messages for inactive sessions trigger an attention indicator and a toast

**Monitor tasks:**
- Run a shell command periodically; exit code 0 means OK (no agent trigger), non-zero means something needs attention
- Suited to health checks and anomaly detection — "do nothing unless something's wrong"
- Each task gets its own working directory; the check script lives in the task directory
- Created via `schedule_create` with `type: "monitor"` action, modified via `schedule_update`

#### Background task commands 🔄

| Command | Description | Example |
|------|------|------|
| `/task` or `/task list` | List all background tasks | `/task` |
| `/task output <id> [N]` | Get task output (default 50 lines) | `/task output abc123 100` |
| `/task stop <id>` | Stop a task | `/task stop abc123` |
| `/task matched <id>` | Get monitor match results | `/task matched abc123` |

**Examples:**
```
# Start a background task (via the AI calling a tool)
monitor_start("cargo build", watch_pattern="Finished", name="Project build")
monitor_start("npm run dev", name="Dev server")

# View tasks
/task                          # list all tasks
/task output abc123            # get output
/task stop abc123              # stop a task
```

#### Workspace commands

| Command | Description | Example |
|------|------|------|
| `/add_dir` or `/add-dir` | Manage extra workspace directories (session only) | `/add_dir /path/to/project` |

#### Project init commands

| Command | Description | Example |
|------|------|------|
| `/init` | Scan the current project and generate/update the project instruction file (AGENTS.md first, CLAUDE.md fallback) | `/init` |

#### Context analysis commands

| Command | Description | Example |
|------|------|------|
| `/context` | Show a detailed context usage analysis (token distribution, tool footprint, etc.) | `/context` |

#### Knowledge graph commands 🗺️

| Command | Description | Example |
|------|------|------|
| `/kg` or `/knowledge` | KG command alias, shows stats (user + project scope) | `/kg` |
| `/kg search <keyword>` | Search entities | `/kg search Python` |
| `/kg list [type]` | List entities (optional type filter) | `/kg list person` |
| `/kg export html\|json\|markdown [user\|project]` | Export the graph | `/kg export html` |
| `/kg clear [user\|project]` | Clear the graph (confirmation required) | `/kg clear project` |

#### A2A remote agent commands 🌉

| Command | Description | Example |
|------|------|------|
| `/a2a` or `/a2a status` | View A2A service status | `/a2a` |
| `/a2a start [token]` | Enable the A2A endpoint (WebUI mode only) | `/a2a start` |
| `/a2a stop` | Disable the A2A endpoint | `/a2a stop` |
| `/a2a add <name> <URL> [token]` | Add a remote A2A agent | `/a2a add coder http://192.168.1.100:8080/a2a` |
| `/a2a list` | List configured remote agents | `/a2a list` |
| `/a2a remove <name>` | Remove a remote agent | `/a2a remove coder` |
| `/a2a test <name>` | Test connectivity to a remote agent | `/a2a test coder` |

> 💡 The A2A service reuses the WebUI HTTP port, so start with `--mode webui` first. A Bearer Token is generated on startup; other UniClaw instances can connect via the `a2a_add_agent` tool.

#### Other commands

| Command | Description | Example |
|------|------|------|
| `/help` | Show help for all available slash commands | `/help` |
| `/<command> help` | Detailed help for a specific command | `/memory help`、`/mcp help` |
| `/usage` | Token usage stats (input/output tokens, tool calls) | `/usage` |
| `/cost` | Cost statistics (per model, pricing from OpenRouter) | `/cost` |
| `/doctor` | Environment diagnostics (12 checks) | `/doctor` |
| `/permissions list` | View all persistent permission rules | `/permissions list` |
| `/permissions remove <type> <pattern>` | Remove a permission rule | `/permissions remove bash "git commit"` |
| `/task` | Manage background tasks (list/output/stop/matched) | `/task`、`/task output abc123` |
| `/goal` | Set a goal stop condition; a judge model evaluates completion when the agent stops | `/goal finish all unit tests` |
| `/goal clear` | Clear the current goal | `/goal clear` |
| `/goal status` | View re-entry count and goal status | `/goal status` |
| `/undo` | Undo AI file edits by restoring a checkpoint (with index) | `/undo`、`/undo 2` |
| `/cp` | Alias for `/checkpoint` | `/cp diff` |
| `/btw` | Add side information without affecting the conversation flow | `/btw this code is good` |
| `/name` | Name the current session | `/name refactor discussion` |
| `/overseer` | Overseer mode, auto-reviews task execution quality | `/overseer` |
| `/voice` | Toggle voice mode (requires TTS config) | `/voice on`、`/voice off` |
| `/cu` | Toggle Computer Use mode | `/cu on`、`/cu off` |
| `/explain` | Tool explanation mode; the AI explains before calling tools | `/explain on`、`/explain off`、`/explain Bash Read` |
| `/exit` or `/quit` | Exit the program | `/exit` |

> 💡 **Tip**: All commands work in console and WeChat modes. Type `/help` for the full list, and `/<command> help` for details on a specific command (e.g. `/memory help`). Commands that don't match a builtin fall back to the skill lookup system.

### Shell passthrough

Prefix a line with `!` to run a shell command directly from the REPL:

```
D:\code\learn\UniClaw  5.23% » !python --version
  $ python --version
Python 3.14.0
```

## 🌐 WebUI Mode

UniClaw provides a WebSocket-based web interface for chatting with the AI from a browser.

### Start the WebUI

```bash
# Local access only (default, HTTPS enabled)
uv run uniclaw --mode webui

# LAN access
uv run uniclaw --mode webui --host 0.0.0.0

# Disable HTTPS (use HTTP)
uv run uniclaw --mode webui --host 0.0.0.0 --no-ssl

# Specify a domain (for the HTTPS certificate)
uv run uniclaw --mode webui --host 0.0.0.0 --domain uniclaw.example.com

# Or use the installed command
uniclaw --mode webui
```

### Features

- ✅ **Browser chat** — interact with the AI in a web interface
- ✅ **Real-time streaming** — WebSocket push with auto-reconnect
- ✅ **Concurrent sessions** — each conversation is an independent task, concurrent sessions supported
- ✅ **Subagent API** — create and manage subagents via REST API
- ✅ **WeChat bot management** — manage WeChat bot accounts directly in the WebUI, async login
- ✅ **Trusted IP auto-login** — `trusted_ips` clients skip login
- ✅ **HTTPS** — on by default (auto self-signed cert), disable with `--no-ssl`; `--domain` for a real domain
- ✅ **IPv6** — IPv6 listen addresses (e.g. `::`) and access
- ✅ **LAN sharing** — `--host 0.0.0.0` makes it reachable on your LAN
- ✅ **Custom system prompt** — per-session system prompts, with AI prompt optimization
- ✅ **Tool call visualization** — tool process, results, and args displayed
- ✅ **Tool explanation mode** — shows the AI's reason before each tool call
- ✅ **Git sidebar** — file status, stage/unstage, and commits, with AI-generated commit messages
- ✅ **Checkpoint management** — view checkpoints and diffs (untracked files included) in the sidebar Git panel
- ✅ **Session forking** — fork a new session from a history message, keeping the context
- ✅ **History search** — search and view full history; switch active/archived views in TUI/WebUI
- ✅ **Message nav bar** — right-side quick jump to any message
- ✅ **Token display** — per-message token cost and processing time
- ✅ **TodoList sync** — tasks sync to the WebUI in real time
- ✅ **A2UI interactive UI** — agent emits `a2ui` JSON component trees rendered as cards/forms/buttons/tabs/charts; results flow back (see [A2UI Interactive UI](#-a2ui-interactive-ui))
- ✅ **Project browse dialog** — browse directories to create projects, with parent-dir navigation and new-folder creation (illegal-char validation, friendly duplicate/permission errors)
- ✅ **Permission mode switch** — switch permission modes in the WebUI (auto/manual/accept-all/plan)
- ✅ **Input dialog** — permission requests pop up with countdown and cancel
- ✅ **Session-level config** — saving with a `session_id` applies to the current session in memory while also writing the global config
- ✅ **Drag-and-drop upload** — drop files onto the chat area; types detected and added as attachments
- ✅ **Multimedia attachments** — images/audio/video sent as attachments for AI analysis
- ✅ **File size limits** — upload size auto-limited to protect performance
- ✅ **Login auto-redirect** — logged-in users land directly in the chat
- ✅ **Hot restart** — the `restart_agent` tool restarts its own process, saves the session gracefully, resumes it, and wakes the agent; the frontend shows the restart status live
- ✅ **Cross-session notifications** — messages for inactive sessions trigger an attention indicator and toast
- ✅ **Mobile responsive** — phone/tablet friendly, touch gestures
- ✅ **Sidebar tree** — conversations grouped by project, collapsible; empty project groups can be removed with one click (frontend only, disk sessions untouched)
- ✅ **Login IP throttling** — rate limiting against brute-force login attacks
- ✅ **Theme customization** — built-in `uniclaw-theme` skill for custom colors/styles
- ✅ **Polished UI** — base CSS styling and animations for a better look

---

## 🎨 A2UI Interactive UI

In WebUI mode, the agent can output **declarative interactive interfaces**: a code block fenced as `a2ui` contains a JSON component tree (root node `{"root": {component}}`), which the frontend whitelist renderer turns into an interactive UI (cards, forms, button groups, tabs, charts, etc.). User interactions are sent back to the agent over WebSocket, closing a **two-way interaction loop**.

> 💡 **Scope**: WebUI mode, main agent only — console/WeChat modes have no renderer and subagents don't call it, so no wasted tokens. When a plain-text answer is enough the agent won't emit components.
>
> 💡 **On-demand spec loading**: the A2UI component spec was moved out of the resident system prompt. In WebUI mode only a one-line trigger remains in the system prompt; when the agent decides an answer is better presented as an interactive UI, it first calls the `get_a2ui_guide` tool for the full spec, then emits the `a2ui` block per that spec — saving tokens.

### Format

The block content is a **single JSON object** with root node `{"root": {component}}`; the tree can be mixed with explanatory text:

```a2ui
{"root": {"type": "Card", "title": "Deploy Status", "children": [
  {"type": "Row", "children": [
    {"type": "Badge", "content": "Running", "tone": "success"},
    {"type": "Progress", "value": 72, "label": "Progress"}
  ]},
  {"type": "Button", "label": "Restart Service", "variant": "danger", "action": {"type": "agent", "payload": "Restart service"}}
]}}
```

### Available components

| Category | Components |
|------|------|
| Layout | `Column{children, gap}`, `Row{children, gap}` |
| Display | `Text{content, variant: h1\|h2\|body\|caption, color}`, `Badge{content, tone: info\|success\|warn\|danger}`, `Divider{}`, `Progress{value: 0-100, label}`, `Chart{chart: bar\|line\|pie, title, data}` |
| Container | `Card{title, children, footer}`, `Tabs{tabs: [{label, children}]}` |
| Interaction | `Button{label, variant: primary\|default\|danger, action}`, `TextField{label, name, placeholder, value}`, `Checkbox{label, name, checked}`, `Select{label, name, options: [{label, value}], value}` |

**Chart component** renders with pure SVG; data structure:

```json
{
  "type": "Chart",
  "chart": "bar",
  "title": "Monthly Sales",
  "data": {
    "labels": ["Jan", "Feb", "Mar"],
    "series": [
      {"name": "2025", "values": [120, 150, 90]},
      {"name": "2026", "values": [180, 140, 200]}
    ]
  }
}
```

- `bar` / `line`: shared-axis layout with gridlines, tick labels, and data-point annotations; multi-series auto-colors and generates a legend
- `pie`: takes the first series' `values` (or `data.values`), annotates sector percentages, and generates a legend
- Empty or non-positive data shows a friendly placeholder instead of a blank chart

### Actions and callbacks

`Button`'s `action` supports three forms:

| action | behavior |
|--------|------|
| `"copy"` or `{"type": "copy", "text": "..."}` | click to copy text |
| `{"type": "link", "url": "https://..."}` | click to open a link |
| `{"type": "agent", "payload": "..."}` | send `payload` back to the agent as a system message |

With the `agent` action, any `TextField` / `Checkbox` / `Select` on the page collect their `name`/value and send them along — the agent can **gather user input** (forms, config items, option confirmations, etc.).

Callback messages are injected as system notices (`SYSTEM_PREFIX[a2ui] The user performed an action on UI components: ...`): queued while the agent is running, or waking it if idle. Against injection, callbacks are validated — only JSON-native types, 4KB length limit; anything over or invalid is ignored with a notice.

### Security model

- **Component whitelist**: only the components listed above are rendered; unknown types stay as the raw code block, never executed
- **textContent only**: all text is written via `textContent`, no HTML parsing — XSS-proof
- **Invalid JSON fallback**: non-legal JSON inside the block renders as a plain code block

> 💡 **Tip**: don't force components when text is clearer. Good A2UI uses: status overviews, action button groups, config forms, multi-tab content, data charts.

---

## 💬 WeChat Bot Integration

UniClaw connects to WeChat via the iLink Bot protocol, letting you chat with the AI assistant from WeChat. Multiple accounts, image recognition, and real-time message handling are supported.

### Start the WeChat bot

The WeChat bot is integrated into WebUI mode. Start WebUI and manage accounts from the sidebar's WeChat area:

```bash
uv run uniclaw --mode webui
```

### Basic operations

Click "Add WeChat account" in the WebUI sidebar and scan the QR code to log in.

**Management:**
- **Add account**: click the "Add" button in the sidebar WeChat area
- **Remove account**: click "Remove" next to the account
- **View status**: the sidebar shows online status in real time
- **Message listening**: logged-in accounts auto-start message listening

### Chatting from WeChat

All of the following work from a WeChat conversation:

#### 1. Intelligent chat

Send text directly; the AI understands and replies:

```
Hello, please write a Python function to compute the Fibonacci sequence
```

#### 2. Shell commands

Prefix with `!` to run shell commands:

```
!python --version
!ls -la
!git status
```

#### 3. Slash commands

Prefix with `/` for built-in commands (help, model switching, permission rules, etc.):

```
/help
/permissions list
```

#### 4. Image recognition

Send images directly; the AI recognizes and discusses them. Multiple images at once are supported.

### Features

- ✅ **Multi-account** — manage several WeChat accounts at once
- ✅ **Async login** — WeChat bot login is async and never blocks the main thread
- ✅ **WebUI integration** — add/remove/view status directly in the WebUI
- ✅ **Auto-start** — logged-in accounts auto-start message listening
- ✅ **Image handling** — receive and recognize image content
- ✅ **Permission requests** — tool calls follow the configured permission mode; when confirmation is needed, you approve/deny from WeChat
- ✅ **Real-time feedback** — progress notifications during tool calls
- ✅ **Access control** — WeChat sessions use the configured permission mode (default `auto`); whether confirmation is needed depends on the tool and permission rules
- ✅ **Context isolation** — each user has an independent conversation history and state
- ✅ **Session auto-cleanup** — WeChat-mode sessions are managed and cleaned automatically

### Data storage

WeChat bot data lives under `~/.UniClaw/`:

- Account config and credentials: `~/.UniClaw/wechat/`
- Session history: `~/.UniClaw/sessions/`

### Notes

⚠️ **Security**:
- WeChat session permissions follow the configured mode; with `accept-all`, every operation is auto-approved — use with care
- Recommended for trusted environments
- Don't expose the bot interface on untrusted networks

⚠️ **Performance**:
- Concurrent users spawn independent agent instances
- Each user's history is saved separately
- Clean up long-running sessions periodically

---

## 🛠️ Tool System

UniClaw ships a rich set of built-in tools that the AI calls automatically. Tools split into **core tools** (always loaded) and **extended tools** (discovered on demand via `search_tools`); see [Tool Registry](#tool-registry-system). You can also add external tool plugins via the [Plugin System](#-plugin-system).

Tool infrastructure:
- **`base.py`** — the custom `@tool` decorator auto-generates OpenAI function-calling schemas from type hints and automatically excludes the `tool_runtime` injection parameter; `ToolRuntime` is the runtime context dataclass (`config` / `tool_call_id` / `stream_writer`). A tool function declares `tool_runtime: ToolRuntime = None` to get config, call ID, and streaming output (`await tool_runtime.stream(content)`)
- **Argument-error distinction** — distinguishes "the model didn't pass an argument" from "arguments truncated/corrupted": the corrupted case keeps the `_raw_arguments` scene instead of masquerading as a missing-arg error; argument binding/type failures return an error string (with `TOOL_ERROR` prefix) consistent with the tool-return convention. A tool message's `args` field keeps the full call arguments in the session; scrubbing only happens at the API send boundary, raw records are preserved in storage
- **`registry.py`** — the tool registry: BM25 search index, core/extended layering
- **Async tool cancellation** — async tool execution races the task's `cancel_event`; on ESC/interrupt the tool task is auto-cancelled via `asyncio.wait`, no tool-side cancellation checks needed; sync tools are unaffected

#### File-system tools

- **Read** — read file content (with line-range and offset support)
  - Out-of-range `offset` reports a clear error with the total line count (not a fake empty file); `limit` beyond the remaining lines notes end-of-file
- **Write** — write or create a file (auto-creates parent directories, returns a diff report)
- **Edit** — exact string replacement in a file (also supports unified diff format)
- **Glob** — pattern-based file search (e.g. `*.py`, `**/*.txt`)
  - At most 500 results per call; beyond that it truncates and notes the total count (never silently drops)
- **apply_patch** — batch edit multiple files: add/update/delete/move in a single call
  - Standard patch text protocol: `*** Begin Patch` / `*** Add File:` / `*** Update File:` / `*** Delete File:` / `*** Move to:` / `*** End of File` / `*** End Patch`
  - Context-matching edits with three-level lenient fallback (exact → ignore trailing whitespace → ignore both ends); misses return line-numbered errors for self-correction
  - Handles CRLF files (preserves original newlines and BOM); rejects binary files and non-UTF-8 encodings with a clear error instead of a misleading "context not matched"
  - Path safety checks: relative paths only, no `..`, no Windows reserved device names, per-component symlink checks to prevent escape
  - Same-path multi-operation conflict detection (only "delete then add" is legal); a failure inside one call stops subsequent applies and reports applied/failed files
  - `dry_run=True` parses and validates without writing, returning the diff report to be applied; every round of edits auto-creates a checkpoint, undoable via `/undo`
- **ConvertToMarkdown** — convert documents (PDF, DOCX, PPTX, XLSX, etc., via markitdown) to Markdown and save as `.md`

#### Shell tools

- **Bash** — run shell commands (timeout control, output caps, cross-platform, streaming)
  - On timeout the process hands off to the background monitor (returns a monitor ID; `monitor_output` reads later output)
  - Pushes output to the frontend live during execution (WebUI) — see progress before the command finishes
  - Output cap 50,000 lines / 10MB: beyond that the process tree is killed and truncation is clearly flagged
  - Windows heredoc compatibility: commands containing heredoc (`<<` / `<<-`) are rewritten to run a temp script via bash instead of dragging into the cmd.exe interactive REPL
- **Grep** — search for text patterns in files, regex supported
  - Execution chain: ripgrep → grep (non-Windows) → pure-Python fallback for all platforms
  - `output_mode` (content/files_with_matches/count), `case_insensitive`, `context`
  - The pure-Python fallback ignores hidden files/dirs by default and auto-detects multiple encodings
- **search_files_with_everything** — instant filename search via the Everything engine (Windows only, requires Everything's es.exe)
  - Wildcards (`*`, `?`) and logical operators (AND/OR/NOT)
  - `max_results` (0 = unlimited), `path_filter` to scope the search

> 💡 **Streaming output**: Bash and other tools push live output to the frontend (WebUI) — no need to wait for completion. Tools stream via `ToolRuntime.stream()`, routed by `tool_call_id` into the right tool block; async/background tasks work too.

> 💡 **Windows tip**: on Windows, Bash runs via cmd.exe by default; for Unix-command compatibility use `bash -c "command"` to reach Git Bash (auto-detected and added to PATH), or `powershell -c "command"` for PowerShell. [Git for Windows](https://git-scm.com/download/win) is recommended for the best shell experience. Bash also rewrites `nul` redirects to `/dev/null` on Windows to avoid Git Bash issues.

> ⚠️ **Note**: some commands may hit a pager (`git log`, `man`, etc.) and block waiting for input. Workarounds:
> - Git: add `--no-pager` — `git --no-pager log`
> - man: `MANPAGER=cat man ls`
> - or use another non-interactive alternative

#### Utility tools

- **sleep_timer** — async wait for N seconds, then the AI is woken to continue (1-3600s, validated)
  - Returns immediately without blocking the main loop
  - Optional reason description for traceability
  - Great for delayed work (waiting for a service to start, API rate limiting, etc.)
  - Available in the main agent and async subagents; sync subagents don't get it (they return results when their turn ends and a timed wakeup has nobody to receive it — hand long waits back to the parent)
- **wait** — synchronous wait for N seconds (simple, short waits)

#### Multimedia tools

- **ReadMedia** — read media files (image/audio/video) and send them multimodally to the LLM for analysis; local paths and URLs supported
- **GenerateImage** — AI text-to-image (requires `image_model`), custom sizes (e.g. `"2K"`, `"1024x1024"`), save to a file or return multimodal data for direct analysis

#### Docker tools

Full Docker container and image lifecycle management (requires a Docker environment):

**Containers:**
- **DockerCreate** — create a persistent container with `-v` mounts, `-p` port mapping, and memory/CPU limits
- **DockerExec** — run commands inside an existing container; container state (installed packages, created files) persists across calls
- **DockerStart** — start a stopped container; filesystem state is kept
- **DockerStop** — stop a running container
- **DockerRemove** — stop and delete a container, freeing resources
- **DockerList** — list all managed containers and their status

**Images:**
- **DockerPull** — pull a Docker image
- **DockerSearch** — search Docker Hub (name, description, stars, official)
- **DockerImages** — list local images
- **DockerRemoveImage** — remove a local image
- **DockerBuild** — build an image from a Dockerfile

**Security limits:** network access off by default, 256MB memory, 1 CPU core, no privilege escalation

> ⚠️ **Dependencies**: Grep needs ripgrep or grep; search_files_with_everything needs Everything (es.exe); Docker tools need Docker; IPython tools need `ipykernel` (included). The environment is probed at startup — unavailable tools are disabled with the reason shown.

#### IPython tools

A persistent Python execution environment; variables survive across calls and IPython magic commands work:

- **ipython_start** — start an IPython kernel (multiple parallel kernels, keyed by kernel_id)
- **ipython_execute** — execute code in a kernel (magic commands like `%timeit`, `%matplotlib`, `%pip` etc.; auto-starts the default kernel; kernel execution is serialized so only one block runs at a time, avoiding iopub queue races)
- **ipython_inspect** — inspect a variable (type, value, length, shape, docstring; clearer undefined-variable hints for NameError)
- **ipython_vars** — list all user variables (type filter supported)
- **ipython_history** — execution history
- **ipython_stop** — stop a kernel and release resources
- **ipython_list_kernels** — list running kernels

> 💡 **Use cases**: a persistent environment for stateful multi-call work — data analysis, variable debugging, package management, and more. Kernel startup failures print detailed process status and diagnostics (written to a diagnostic file).

> ⚠️ **Resources**: kernels hold system resources — call `ipython_stop` when you're done.

#### LSP language intelligence tools 🔤

Language-Server-Protocol code intelligence with real-time type diagnostics and semantic queries — no code execution needed:

**Lifecycle:**
- **lsp_status** — server status (registered languages, startup state, synced document count)
- **lsp_start** — start a language server (lazy, usually automatic; returns install instructions if missing)
- **lsp_stop** — stop servers and release subprocess resources (individual or all)

**Semantic queries:**
- **lsp_diagnostics** — type diagnostics for a file (errors/warnings/info), severity-sorted — the go-to tool for self-checks after edits
- **lsp_definition** — jump to a symbol's definition (cross-file, inheritance, re-exports), returning location + code excerpt
- **lsp_hover** — hover info (type signature, docs) to confirm a type or API
- **lsp_references** — all references to a symbol (declaration included), for refactor impact assessment (up to 100)
- **lsp_symbols** — document symbol outline (classes/functions/variables with line numbers)

**Built-in servers:**

| Language | Server | Extensions | Install |
|------|--------|-----------|---------|
| Python | pyright | `.py` `.pyw` | `uv pip install 'uniclaw[lsp]'` |
| JavaScript/TypeScript | typescript-language-server | `.js` `.jsx` `.ts` `.tsx` `.mjs` `.cjs` | `npm i -g typescript-language-server typescript@5` |
| HTML | vscode-html-language-server | `.html` `.htm` `.vue` | `npm i -g vscode-langservers-extracted` |
| CSS | vscode-css-language-server | `.css` `.scss` `.less` | `npm i -g vscode-langservers-extracted` |

**Examples:**
```
# The AI calls tools automatically (or discover via search_tools "lsp diagnostics")
lsp_diagnostics(file="src/uniclaw/config.py")

# Go to definition (supports "path:line:col" inline, 1-based)
lsp_definition(file="src/uniclaw/main.py:10:5")

# Find references (assess refactor impact)
lsp_references(file="src/uniclaw/config.py", line=42)

# Symbol outline of a file
lsp_symbols(file="src/uniclaw/agent/multi_agent.py")
```

> 💡 **Tip**: servers are managed per "language + project root", lazily started on first query, auto-restarted after crashes, and idle-reclaimed. Windows `.cmd` wrappers for npm-global installs resolve their real entry point. Location queries (`lsp_definition`/`lsp_hover`/`lsp_references`) accept `"path:line[:col]"` and/or separate `line`/`column` args (1-based); whole-file queries (`lsp_diagnostics`/`lsp_symbols`) take a file path only — inline line/col is ignored and they have no `line`/`column` params. If a language server isn't installed, the tool returns install instructions for that language.

#### Web tools

- **webFetch** — fetch a page and extract plain text (HTML tags stripped)
  - Token-based truncation (`max_tokens`), with a uniform metadata header (HTTP status, content type, page title)
  - `raw=True` returns the full HTML source (uncleaned), preserving structure
- **webSearch** — concurrent multi-platform search merged into one result; Exa only by default (universal web search), platforms on demand: GitHub/arXiv/Stack Overflow/Hacker News/Bilibili/Reddit/Google News/OpenAlex/Polymarket/SEC EDGAR/HuggingFace/alphaXiv/Exa/Bing/DuckDuckGo (`platforms="all"` for everything)
  - Platforms search independently and concurrently; per-platform errors are appended at the end without affecting other results
  - Exa needs `EXA_API_KEY`; Bing works directly from China (no proxy); DuckDuckGo needs a proxy
  - Results over a threshold are auto-reranked by LLM
  - `time_range` filtering (presets like "7d"/"1y" or ISO ranges)
  - `intent` description for the reranker to judge relevance
  - Result caching (128 entries, 10-min expiry)
  - GitHub: repositories/code/issues/users types and stars/forks/updated sorting
  - Set `GITHUB_TOKEN` in `settings.json` to raise the GitHub API rate limit

#### Download tools ⬇️

HTTP/HTTPS download engine with multi-coroutine concurrency, resume, proxy, retry, and file verification:

- **http_download** — download a file (sync and async modes)
  - **Multi-coroutine**: 10 coroutines by default for big speedups
  - **Resume**: progress is tracked; interrupted downloads continue where they left off
  - **Proxy support** for HTTP/HTTPS
  - **Auto retry** (3 tries default) with exponential backoff
  - **Verification** via MD5/SHA256
  - **Cookies** for logged-in scenarios
  - **Custom headers** for anti-hotlink scenes (Referer/User-Agent)
  - **Sync mode**: streaming progress, result on completion
  - **Async mode**: returns a task ID immediately; the AI is woken on completion/failure
  - **Smart cancel**: cancelling a sync download turns it into a background download automatically
- **http_download_status** — view/manage async download tasks
  - Progress (speed, downloaded size, ETA)
  - Pause/resume/cancel
  - List all tasks
- **http_download_remove** — remove a download task record (cleanup of finished/cancelled tasks)

#### M3U8/HLS streams 🎞️

An M3U8/HLS stream download engine, managed separately from the HTTP downloader:

- **m3u8_download** — download an M3U8/HLS stream
  - **Multi-resolution selection**: master playlists (with multiple `#EXT-X-STREAM-INF`) list available resolutions automatically (bitrate-descending, with audio/subtitle track lists); pick quality via the `variant` parameter (index or resolution); media playlists (direct TS segments) don't need a variant
  - **Multi-coroutine**: 8 coroutines by default
  - **AES-128 decryption**: auto-parse and decrypt encrypted segments; fMP4/CMAF `#EXT-X-MAP` init segments supported (`init_iv` parameter for IV conventions)
  - **fMP4/CMAF**: handles `#EXT-X-MAP` init segments, `#EXT-X-BYTERANGE` byte ranges, and `#EXT-X-MEDIA` alternate audio/subtitle tracks
  - **Resume**: downloaded segments are skipped; cancelling cleans up the intermediate segment workdir but keeps the final artifact
  - **Custom headers** and cookies for logged-in scenarios
  - **Async mode**: background download with immediate task ID
  - **Auto-merge**: merges into a single TS file (init segment included) on completion
- **m3u8_download_status** — view/manage m3u8 async tasks (pause/resume/cancel/list)
- **m3u8_download_remove** — remove a finished M3U8 task, freeing memory

**Examples:**
```
# The AI calls tools automatically to download files
http_download(url="https://example.com/file.zip")

# Specify a save path
http_download(url="https://example.com/file.zip", save_path="downloads/file.zip")

# Async download (background)
http_download(url="https://example.com/large.iso", async_mode=True)
# Returns a task ID; query progress later
http_download_status(task_id="xxx")

# Download with verification
http_download(
    url="https://example.com/file.zip",
    checksum_algorithm="sha256",
    checksum_value="abc123..."
)
```

#### Browser automation tools 🌍

Playwright-based browser control with multi-tab management and persistent contexts:

**Lifecycle:**
- **browser_start** — start a browser (Chromium/Firefox/WebKit)
- **browser_close** — close the browser

**Navigation:**
- **browser_navigate** — navigate to a URL
- **browser_back** / **browser_forward** / **browser_reload**
- **browser_get_url** / **browser_get_title**

**Element interaction:**
- **browser_click** — click an element (CSS/text/role selectors)
- **browser_dblclick** / **browser_hover**
- **browser_type** — type into an input (optional `clear` first, default True)
- **browser_insert_text** — insert text (fires the input event)
- **browser_check** — check/uncheck a checkbox
- **browser_select_option** — select a dropdown option
- **browser_drag** — drag an element
- **browser_focus** — focus an element
- **browser_press_key** / **browser_key_down** / **browser_key_up** — keyboard
- **browser_keyboard_type** — keyboard text input

**Page info:**
- **browser_screenshot** — full-page or region screenshot
- **browser_get_text** — page text
- **browser_get_html** — page HTML
- **browser_get_attribute** — element attribute value
- **browser_get_value** — input value
- **browser_get_elements** — matching elements (`limit`/`offset` paging; first 50 by default with a total count)
- **browser_get_count** — number of matching elements
- **browser_get_box** — element position and size
- **browser_get_styles** — computed styles

**Execution:**
- **browser_evaluate** — run JavaScript
- **browser_wait** — wait for a time or condition
- **browser_scroll** / **browser_scroll_into_view** — scrolling
- **browser_toggle_mode** — toggle browsing mode

**Tabs:**
- **browser_new_page** / **browser_close_page** / **browser_switch_page** / **browser_list_pages**

**External browser:**
- **browser_connect** — attach to a running external browser over CDP (default `http://localhost:9222`) instead of launching a new one; reuse logged-in sessions

#### Memory system tools 🧠

- **memory_save** — save a persistent memory (preferences, project info, feedback)
- **memory_delete** — delete a memory entry
- **memory_search** — smart memory search (SQLite FTS5, incremental index, relative-score filtering; prefers Jev AI semantic search with LLM fallback)
- **memory_list** — list all memory entries

> 💡 **Tip**: the memory system auto-loads relevant memories during conversation to help the AI understand context and preferences. Set `TYPESAFE_API_KEY` to enable Jev semantic search for faster, sharper retrieval.

#### Multi-agent tools 👥

- **subagent_create** — create and start a subagent task (sync/async modes, worktree isolation)
- **subagent_send_to_child** — parent → child message (extra instructions)
- **subagent_send_to_parent** — child → parent message (async subagents only)
- **subagent_close** — close a background subagent to free resources
- **subagent_check_result** — check a subagent's result and status (incremental reads)
- **subagent_list_tasks** — list all subagent tasks and statuses
- **subagent_discuss** — multi-round discussion between multiple agents
- **subagent_list_definitions** — view all available agent definitions
- **subagent_get_definition** — a definition's details (system prompt, tool list, etc.)

> 💡 **Tip**: the fully-async multi-agent system lets you spin up specialized assistants for different tasks. Inter-agent communication is bidirectional: parents append instructions via `subagent_send_to_child`; async children proactively report progress or ask for direction via `subagent_send_to_parent` (this tool is loaded for all subagents by default, exempt from the `tools` whitelist; sync subagents don't get it — the parent is blocked waiting for the final result, so report in the output). `keep_alive` keeps an agent running to receive new instructions. `isolation=True` gives a subagent its own git branch to avoid file conflicts. `inherit_events=True` broadcasts a child's tool calls and thinking to the parent queue for live frontend progress. Children auto-notify the parent on completion, waking it to process results promptly. Subagent definitions support a `tools` whitelist field (in YAML frontmatter) that hard-restricts the subagent; without it, the subagent discovers extended tools via `search_tools` like the main agent.

#### Skill system

- **skill_suggest** — recommend suitable skills for the current task (returns skill and recommendation counts; prefers Jev select_many with LLM fallback)
- **skill_read** — read a skill's full content

**Builtin skills:**
- `code-review` (`/code-review`, `/review`) — multi-dimensional code review (security, correctness, performance, quality, readability)
- `commit` (`/commit`) — AI generates a Conventional Commits message and commits
- `pr-create` (`/pr-create`, `/pr`) — AI generates a PR title/description and creates a GitHub PR via `gh`
- `memory-organize` (`/memory-organize`, `/organize-memory`, `/memory-clean`) — memory manager: organize memories, extract value from sessions
- `skill-forge` (`/skill-forge`, `/forge-skill`, `skill forging`) — skill forge: create new skills and optimize existing custom skills (self-built skills only)
- `tool-plugin-forge` (`/tool-plugin-forge`, `/forge-tool-plugin`, `+tool-plugin`) — tool-plugin forge: auto-generate UniClaw external tool plugins per your needs, written to `~/.UniClaw/plugins/tools/`; multi-file split, lifecycle hooks, long-running tasks with async + wake_agent, verified loadable after generation
- `uniclaw-theme` (`/uniclaw-theme`, `/theme`) — theme customization: WebUI colors, fonts, and styles

**Skill search paths:**
Skills auto-load from common directories:

Project level:
- `skills/`
- `.claude/skills/`
- `.codex/skills/`
- `.agents/skills/`

User level (global):
- `~/.claude/skills/`
- `~/.codex/skills/`
- `~/.agents/skills/`

> 💡 **Tip**: the skill system detects these directory structures in the project and loads skill definitions. Skill files are Markdown with YAML frontmatter metadata.

#### MCP tools 🔌

Connect external tool services via MCP (Model Context Protocol), supporting stdio, sse, and streamable_http (mcp 2.x SDK). MCP command management is async for better responsiveness.

- **Persistent sessions**: with `"persistent": true`, connections are reused across tool calls — good for stateful services (login-then-query) or slow-cold-start stdio servers; idle connections are reclaimed after 30 minutes
- **Multimodal results**: image/audio/video content blocks returned by MCP tools become multimodal messages for the AI to analyze directly; oversized media degrades to a text placeholder

Manage MCP servers with `/mcp`:

| Command | Description |
|------|------|
| `/mcp list` | List configured MCP servers |
| `/mcp add <name> [json]` | Add an MCP server (JSON config supported) |
| `/mcp remove <name>` | Remove an MCP server |
| `/mcp enable/disable <name>` | Enable/disable a server |
| `/mcp tools` | List available MCP tools |

Config file: `~/.UniClaw/mcp.json`

#### A2A remote agent tools 🌉

Agent2Agent protocol tools for remote agent collaboration — calling external A2A agents and exposing UniClaw as an A2A endpoint:

**Client tools (call external agents):**
- **a2a_send_task** — send a task and wait synchronously for a reply (multi-turn supported)
- **a2a_submit_task** — submit async, returns a task_id immediately (multi-turn)
- **a2a_get_task** — query async task status and result
- **a2a_cancel_task** — cancel a running remote task
- **a2a_add_agent** — add or update an external A2A agent config
- **a2a_remove_agent** — remove an external A2A agent config
- **a2a_list_agents** — list configured external A2A agents

**Server (expose UniClaw as an A2A endpoint):**
- Enable via `/a2a start`, mounted at WebUI's `/a2a` path
- JSON-RPC 2.0 protocol (`message/send`, `tasks/get`, `tasks/cancel`)
- Agent Card at `/.well-known/agent-card.json`
- Each `context_id` keeps an independent conversation context

> 💡 **Tip**: manage the A2A service via `/a2a`. Config persists to `~/.UniClaw/a2a/a2a.json`. A Bearer Token is generated on startup for auth.

#### Computer use tools 🖥️

- **cu_screenshot** — capture the screen (full or region)
- **cu_mouse_move** / **cu_mouse_click** (left/right/middle) / **cu_mouse_double_click** / **cu_mouse_drag** / **cu_mouse_scroll**
- **cu_keyboard_type** / **cu_keyboard_type_unicode** / **cu_keyboard_press** / **cu_keyboard_key_down** / **cu_keyboard_key_up**
- **cu_locate_on_screen** — locate an image on screen
- **cu_get_elements** — list UI elements (accessibility tree)
- **cu_find_element** — find a UI element (name, role, value, etc.)
- **cu_interact** — interact with a UI element (click, focus, invoke, etc.)

> 💡 **Tip**: the global hotkey **Ctrl+U** emergency-stops running Computer Use write operations; `/cu on|off` toggles enablement. Depends on `pyautogui`, `pynput`, `mss`, `pillow`. Windows additionally needs `uiautomation`.

#### Todo list tools 📋

- **todolist_create** — create a todo list (decomposition and status management)
- **todolist_update** — update task status (pending/in_progress/completed transitions; pending can go straight to completed; the list auto-closes with a summary when all tasks complete)
- **todolist_clear** — manually clear the list (auto-closes when all done)
- **todolist_list** — list the current list
- **todolist_cancel** — cancel the list

> 💡 **Tip**: progress is managed automatically — exactly one task is `in_progress` at a time (marking a new one sends others back to `pending`), and the next task advances on completion. Overseer mode (`/overseer`) auto-reviews execution quality.

#### Monitor tools 🔄

- **monitor_start** — start a background process (async, optional watch_pattern)
  - **Auto-wake on exit**: process end (natural exit / timeout / exception) notifies the user and wakes the agent (independent of `notify_on_match`, exactly once); an agent-initiated `monitor_stop` doesn't re-wake
  - **watch_pattern**: regex-match output lines to notify (empty = no matching)
  - **Visible mode**: `visible=True` opens a real console window (ConPTY pseudo-terminal) — live output, direct input for interactions like logins, verification codes, and interactive install prompts the AI can't handle alone
- **monitor_stop** — stop a process (active stop doesn't trigger the exit wake)
- **monitor_list** — list all background processes (running ones tracked live; finished entries remain queryable)
- **monitor_output** — get process output
- **monitor_screen** — console screen snapshot (tmux capture-pane style, including prompt-line content, unsubmitted typed text, full-screen vim/htop; non-visible mode degrades to full output history)
- **monitor_input** — send input to the process
- **monitor_get_matched** — get monitor match results
- **monitor_update_pattern** — change the watch pattern dynamically
- **monitor_clear** — batch-clean finished entries (running processes kept)

#### Hook tools 🪝

- **hook_docs** — Hook system documentation and usage
- **hook_read** — read current hook config
- **hook_add** — add an event hook (runs a shell command)
- **hook_remove** — remove a hook

Event types:
- `SessionStart` / `SessionEnd` — session start/end
- `PreToolUse` / `PostToolUse` — before/after a tool call
- `PreAssistant` — before an assistant reply
- `PermissionRequest` / `PermissionResponse` — permission request/response

#### Notification tools 🔔

- **push_notification** — desktop notification (Windows/macOS/Linux)
  - Windows: PowerShell Toast Notification
  - macOS: osascript
  - Linux: notify-send
  - For task completion and long-running-result reminders

#### TTS tools 🔊

- **text_to_speech** — text-to-speech (needs `tts_model` and `audio`)
  - Style tags: `(happy)hello` `(sing)lyrics` and so on embedded in text
  - Basic emotions (happy/sad/angry), compound emotions (wistful/gratified/helpless)
  - Tone (gentle/cold/lively), timbre (magnetic/mellow/clear)
  - Dialects (Northeastern/Sichuan/Cantonese) and roleplay
  - Audio tags: `[laugh]` `[breath]` `[pause]` for fine control
  - Save to a file or play directly

> 💡 **Tip**: toggle voice mode with `/voice on|off`. Auto-enabled when `tts_model` and `audio` are configured.

#### File sending 📤

- **send_file** — send a file to the user (`expire_minutes` for validity, default 30 minutes)
  - WebUI: text files (UTF-8 decodable without NUL bytes) render a Markdown preview directly in the frontend, content doesn't enter the model context; other files get a temporary download link
  - WeChat: direct send via the bot
  - Console: unsupported (ignored)

#### WeChat tools 💬

- **wechat_list_contacts** — list currently logged-in WeChat bot names
- **wechat_send_text** — send text to the current conversation user over WeChat
- **wechat_send_image** — send an image (with optional caption)
- **wechat_send_file** — send a file (custom filename supported)

#### Knowledge graph tools 🗺️

A SQLite-based knowledge graph with entity/relation management, querying, and visualization:

**Entities:**
- **kg_add_entity** — add an entity (type, description, attributes, confidence)
- **kg_update_entity** — update entity attributes
- **kg_delete_entity** — delete an entity (cascades relations and aliases)
- **kg_merge_entities** — merge two entities (relations, aliases, attributes transfer)
- **kg_add_alias** — add an entity alias (entity disambiguation)

**Relations:**
- **kg_add_relation** — add a relation between entities
- **kg_delete_relation** — delete a relation

**Queries:**
- **kg_get_entity** — entity details (relations included)
- **kg_search** — smart entity search (FTS5 prefix + LIKE substring hybrid, Chinese/English, alias-table aware)
- **kg_neighbors** — entity neighbors (multi-hop)
- **kg_path** — path between two entities
- **kg_list** — list entities (type filter)
- **kg_stats** — graph statistics

**Advanced:**
- **kg_extract** — auto-extract entities and relations from text or files (AI)
- **kg_export** — export the graph (JSON/Markdown/HTML visualization)
- **kg_clear** — clear the graph (confirmation required)

**Scope:**
- `user` — user level (shared across projects, stored in `~/.UniClaw/knowledge.db`)
- `project` — project level (default, `.UniClaw/knowledge.db`)

> 💡 **Tip**: auto-extraction lets the AI analyze text or files, identify entities and relations, and add them to the graph. Manage with `/kg`.

#### RAG retrieval tools 📚

A vector-database RAG (Retrieval-Augmented Generation) system with document ingestion, smart chunking, and semantic retrieval:

- **rag_ingest** — read a file or directory, recursively split into chunks, embed, and store
  - Formats: txt/md/py/json/yaml/csv/html/pdf, etc.
  - Configurable `chunk_size` and `chunk_overlap`
  - User/project dual scope
  - Re-imports skip unchanged chunks and clean stale blocks from sources whose content changed this run; old blocks for deleted files aren't auto-cleaned
  - Honors `.gitignore`
- **rag_search** — semantic retrieval from a collection
  - `min_score` filters low-relevance results
  - `top_k` controls count
  - Multi-recall (vector + BM25) with RRF fusion and Jev/LLM reranking (Jev first, LLM fallback)
  - `intent` description auto-enables reranking for sharper relevance
  - `where` metadata filtering (source/filename/suffix/chunk_index/created_at)
- **rag_list_collections** — list collections with stats (document count, description, etc.)
- **rag_delete_collection** — delete a collection and all documents
- **rag_set_desc** — set/update a collection description for later recognition
- **rag_evaluate** — evaluate retrieval quality: run a test question set, output LLM Judge relevance metrics
  - `use_llm_judge=True` evaluates each result's relevance with an LLM — no manual labeling
  - Outputs mean LLM Judge score and relevant-result ratio (relevant blocks / returned blocks; reported as Context Precision@k, order-insensitive)
  - `rerank` (LLM rerank) and `use_bm25` (BM25 recall) toggles

**Scope:**
- `user` — user level (shared across projects)
- `project` — project level (default)

> 💡 **Tip**: RAG suits semantic retrieval over large document sets. `rag_ingest` first, then `rag_search`. Collections are independent, so organize by topic or project.

#### Advisor model tools 🎓

- **advisor_list** — list configured advisor models
- **ask_advisor** — ask advisor models for advice and analysis (multiple models at once)
  - Use cases: knowledge gaps, validating approaches, deep domain analysis
  - Concurrent multi-model calls, results merged and labeled by source
  - Note: one-shot Q&A, no context, no exploration — gather information before asking
- **investigate** — investigate project information and return a distilled summary, internally starting a recon agent to collect and organize data
  - Use cases: module/function implementation details, locating project code/docs around a topic, gathering info before judging

> 💡 **Tip**: advisor models come from `large_model_name`; available only when configured. Add them in settings.json, or assign via `/model`.

#### AI self-help tools 📖

- **list_slash_commands** — list all slash commands with brief descriptions
- **get_command_help** — detailed help for a slash command (args, usage examples, etc.)
- **list_builtin_tools** — list all builtin tool names (core + extended), for plugin name-collision detection

#### Plan mode tools 📝

- **enter_plan_mode** — enter plan mode; the AI plans without executing tools
- **exit_plan_mode** — leave plan mode and start executing

> 💡 **Tip**: plan mode suits complex tasks that deserve upfront planning. After entering, the AI analyzes and designs a plan for your confirmation before stepping through it.

#### Security tools 🔒

- **read_llm_safe_prompt** / **write_llm_safe_prompt** / **edit_llm_safe_prompt** / **clear_llm_safe_prompt** — manage the LLM safety prompt

> 💡 **Tip**: security tools manage the LLM security review against prompt-injection attacks. The core `llm_safe_check` AI-drives tool-call safety review. Set `TYPESAFE_API_KEY` to enable fast Jev pre-screening before the LLM analysis.

#### Conversation management tools 💬

- **session_list** — list all history sessions
- **session_detail** — session details
- **session_delete** — delete a session
- **session_update_title** — update a session title
- **recall_history** — keyword-search archived messages (regex + context window, pairs with infinite context)
- **get_history_range** — view history messages by index range (active/archived status)

#### Session note tools 📝

Notes persist with the session; summaries are auto-injected during compaction so key facts survive. Good for decisions, key variable values, code snippets, and API usage that must bridge compressions.

- **session_note_add** — add a note (name + summary + full content)
- **session_note_update** — update a note's description and/or content (empty fields keep their values)
- **session_note_delete** — delete a note
- **session_note_list** — list notes (name and summary only)
- **session_note_get** — view a note's full content

> 💡 **Tip**: notes appear in compaction summaries as "see note xxx", so the body never gets lost to compression. The AI proactively suggests notes when it detects important information.

#### User interaction tools 💬

- **AskUserQuestion** — the AI asks you (single/multi-select options)

> 💡 **Tip**: the AI picks tools automatically; no manual calls needed. All tools have thorough error handling and permission controls.

#### Scheduler tools ⏰

- **schedule_create** — create a scheduled task (shell/agent/py/monitor types)
- **schedule_list** — list all tasks and statuses
- **schedule_update** — modify a task's action and schedule
- **schedule_remove** — remove a task
- **schedule_toggle** — enable/disable a task

> 💡 **Tip**: scheduled tasks suit automated ops, periodic code checks, and scheduled reports.

---

## 🔌 Plugin System

UniClaw supports hot-loaded user-level external tool plugins — extend the AI's capabilities without touching the source.

### Core features

- **Hot loading**: drop a `.py` plugin file into `~/.UniClaw/plugins/tools/` and it's loaded automatically — no restart
- **Hot reload**: edit a plugin file, call `search_tools`, and it's detected and reloaded
- **Name-collision detection**: conflicts with builtin tools and other plugins are detected; conflicting tools are skipped with a warning
- **Multi-file split**: the main file defines `@tool` tools + `get_tools()`; complex logic can live in sibling helper modules
- **Lifecycle hooks**: optional `PLUGIN_META` metadata and `shutdown()/close()` cleanup hooks
- **Session notifications**: unloading a plugin notifies all active sessions to drop its old tools

### Quick start

#### Way 1: let the AI generate it (recommended)

Just tell the AI what tool you need — it runs the whole flow through the `/tool-plugin-forge` skill:

```
Write me a weather tool that takes a city name and returns the weather
```

The AI will: capture intent → check collisions → generate plugin code → actually load and verify → deliver a report.

#### Way 2: write it by hand

Create a `.py` file in `~/.UniClaw/plugins/tools/`:

```python
from uniclaw.tools.base import tool


@tool
def my_tool(arg1: str, arg2: int = 10) -> str:
    """
    Brief description of the tool.

    Args:
        arg1: Description of argument 1.
        arg2: Description of argument 2. Defaults to 10.

    Returns:
        str: Description of the return value.
    """
    return f"result: {arg1}, {arg2}"


def get_tools():
    return [my_tool]
```

### Plugin conventions

**Must follow:**
- Every plugin file needs a `get_tools()` function returning a list/tuple of `@tool`-decorated tools
- Must use the `@tool` decorator (`from uniclaw.tools.base import tool`)
- Tools must use Google-style docstrings (with `Args:` and `Returns:` sections)
- For runtime context, declare `tool_runtime: ToolRuntime = None` (last parameter); it's injected by the caller and hidden from the LLM-visible schema
- Tool names contain only `[A-Za-z0-9_-]` and must not collide with builtin tools or other plugins
- Filenames can't start with an underscore (skipped)

**Long-running tasks:**

If a plugin does slow work (HTTP requests, file scans, etc.), use the async-wake pattern or it blocks the whole agent:

```python
import asyncio
from uniclaw.tools.base import ToolRuntime, tool
from uniclaw.utils.wakeup import wake_agent
from uniclaw.utils.constants import SYSTEM_PREFIX


@tool
def long_task(name: str, tool_runtime: ToolRuntime = None) -> str:
    """
    Run a slow long task (returns immediately, wakes the agent when done).

    Args:
        name: The object name to process.

    Returns:
        str: Confirmation that the task started.
    """
    config = tool_runtime.config

    async def _run():
        try:
            result = await asyncio.to_thread(_do_heavy_work, name)
            await wake_agent(f"{SYSTEM_PREFIX}(long_task) done: {result}", config)
        except Exception as e:
            await wake_agent(f"{SYSTEM_PREFIX}(long_task) failed: {e}", config)

    asyncio.create_task(_run())
    return f"Started processing {name}; you'll be woken when it completes."
```

**Optional lifecycle hooks:**

```python
PLUGIN_META = {"author": "user", "version": "1.0.0"}


async def shutdown():
    """Cleanup on plugin unload (connections, temp files, etc.)."""
    await _client.close()
```

### Management

| Action | How |
|------|------|
| Create a plugin | `/tool-plugin-forge <description>` or just tell the AI |
| List builtin tool names | the AI calls `list_builtin_tools` (for collision checks) |
| Refresh plugins | the AI calls `search_tools` to detect changes and reload |
| Delete a plugin | delete the file under `~/.UniClaw/plugins/tools/` |

### Storage

- **Plugin directory**: `~/.UniClaw/plugins/tools/` (user level, shared across projects)

> ⚠️ **Security**: plugins are executable Python. Verify a file's origin is trustworthy before loading. Don't run plugins from unknown sources.

---

## 🏗️ Architecture

### Core components

The project uses the **src layout**; all source lives under `src/uniclaw/`. The entry command is `uniclaw` (registered via `pyproject.toml`'s `[project.scripts]`).

```
UniClaw/
├── pyproject.toml          # project config (deps, entry, build)
├── .python-version         # pinned Python for local dev (3.14; minimum 3.11)
├── Dockerfile              # containerized deployment (python:3.14-slim + uv + Playwright)
├── .dockerignore           # Docker build ignore rules
├── LICENSE                 # MIT license
├── tests/                  # tests (repo root)
│
└── src/uniclaw/            # 📦 package root
    ├── main.py             # entry (argparse --mode → launcher, run_mode enum)
    ├── agent/              # core agent (async message loop, tool calls, event stream, loop detection with result fingerprints, output truncation detection, asyncio.Queue)
    │   ├── __init__.py     # backward-compat re-exports (MultiAgent, AgentTask, event classes)
    │   ├── multi_agent.py  # MultiAgent singleton, event loop, subagent management, permission checks, subdirectory project-instruction injection
    │   └── types.py        # AgentStatus, AgentTask, event classes
    ├── config.py           # config (AppConfig + settings.json + first-run wizard + multi-model fallback)
    ├── context.py          # context management and system prompt building (root_dir-driven + long-task progress reporting)
    ├── compaction.py       # context compaction (3-level pressure strategy)
    ├── spinner.py          # loading animation
    │
    ├── provider/           # LLM layer: multi-provider routing (OpenAI / Anthropic)
    │   ├── router.py       # unified API: stream/astream/chat/achat + multi-model fallback
    │   ├── fallback.py     # LLM call fallback to backup models
    │   ├── openai_provider.py
    │   ├── anthropic_provider.py
    │   ├── thought_parser.py   # streaming <thought>/ thinking tag parsing
    │   ├── error_classifier.py  # six-category error classification + differentiated retry
    │   ├── types.py        # Protocol/Effort enums, StreamChunk (finish_reason)/Usage
    │   └── common.py       # get_provider(), compare_urls(), HTTP client cache
    │
    ├── commands/           # slash commands 📝 (31 commands + 7 aliases, 38 registered)
    │   ├── __init__.py     # command registry (COMMANDS dict)
    │   ├── session.py      # session management (clear/compact/export)
    │   ├── resume.py       # session resume (list/del/search/fork) 💬
    │   ├── model.py        # model switching (image-gen model included)
    │   ├── system.py       # system commands (cwd/skills/exit/help/usage)
    │   ├── memory.py       # memory management
    │   ├── mcp.py          # MCP management
    │   ├── schedule.py     # scheduled tasks ⏰ (session-associated)
    │   ├── permissions.py  # permission rule management
    │   ├── context_usage.py # context usage analysis
    │   ├── init.py         # project init (generates AGENTS.md/CLAUDE.md)
    │   ├── add_dir.py      # workspace directory management
    │   ├── cost.py         # cost stats 💰
    │   ├── doctor.py       # environment diagnostics 🩺
    │   ├── task.py         # background task management 🔄
    │   ├── btw.py          # side information
    │   ├── name.py         # session naming
    │   ├── overseer.py     # overseer mode
    │   ├── goal.py         # goal stop conditions 🎯
    │   ├── checkpoint.py   # git checkpoints
    │   ├── undo.py         # undo file edits
    │   ├── voice.py        # voice mode toggle 🔊
    │   ├── cu.py           # Computer Use mode toggle 🖥️
    │   ├── explain.py      # tool explanation mode toggle 🔧
    │   ├── knowledge.py    # knowledge graph management 🗺️
    │   └── a2a.py          # A2A remote agent management 🌉
    │
    ├── console/            # console UI (prompt_toolkit REPL)
    │   ├── launcher.py     # console launcher
    │   ├── run.py          # REPL main loop + file completion + subcommand completion + command completion popup
    │   ├── dialog.py       # conversations + multi-question Tab dialog
    │   ├── output_renderer.py
    │   ├── session_panel.py
    │   └── ui.py           # UI components
    │
    ├── webui/              # WebUI interface (WebSocket)
    │   ├── launcher.py     # WebUI launcher
    │   ├── app.py          # FastAPI app
    │   ├── api.py          # REST API routes (subagent creation API, directory-creation endpoint)
    │   ├── ws.py           # WebSocket handling (session task management)
    │   ├── models.py       # data models
    │   ├── auth.py         # auth layer (login/trusted IP/IP throttling)
    │   ├── crypto.py       # crypto module
    │   ├── encrypt_static.py # static resource encryption
    │   ├── spinner.py      # WebUI loading animation
    │   └── static/         # frontend static assets
    │
    ├── tools/              # tool system
    │   ├── __init__.py     # tool registry hub
    │   ├── base.py         # @tool decorator (auto OpenAI schema) + ToolRuntime
    │   ├── registry.py     # tool registry (BM25 search, core/extended layers, LRU + energy eviction, availability tracking)
    │   ├── fs.py           # file system (Read/Write/Edit/Glob/ConvertToMarkdown)
    │   ├── apply_patch.py  # batch file editing (patch protocol: Add/Update/Delete/Move)
    │   ├── shell.py        # Shell (Bash/Grep/Everything, pure-Python grep fallback)
    │   ├── web.py          # Web (webFetch/webSearch)
    │   ├── search/         # multi-platform search (GitHub/arXiv/Stack Overflow/Bilibili, etc.)
    │   │   ├── tools.py    # search entry + tool definitions (webSearch multi-platform concurrency)
    │   │   ├── base.py     # base search class (retrying HTTP)
    │   │   ├── exa.py      # Exa semantic search (preferred engine)
    │   │   ├── bing.py     # Bing search (direct from China, no proxy)
    │   │   ├── duckduckgo.py # DuckDuckGo fallback
    │   │   ├── github.py   # GitHub search (repos/code/issues/users)
    │   │   ├── huggingface.py # HuggingFace search (models/datasets)
    │   │   ├── openalex.py # OpenAlex academic search (429 retry)
    │   │   ├── arxiv.py    # arXiv paper search
    │   │   ├── alphaxiv.py # alphaXiv paper-discussion search
    │   │   ├── stackoverflow.py # Stack Overflow search
    │   │   ├── hackernews.py # Hacker News search
    │   │   ├── bilibili.py # Bilibili video search
    │   │   ├── google_news.py # Google News search
    │   │   ├── reddit.py   # Reddit search
    │   │   ├── polymarket.py # Polymarket prediction-market search
    │   │   ├── sec_edgar.py # SEC EDGAR finance search
    │   │   └── time_range.py # time-range filtering
    │   ├── download/       # HTTP + M3U8 download engines ⬇️
    │   │   ├── tools.py    # tool aggregation (HTTP + M3U8)
    │   │   ├── manager.py  # download task manager (HTTP/M3U8 managed separately)
    │   │   ├── http_tools.py # HTTP/HTTPS download tools (multi-coroutine + resume)
    │   │   ├── m3u8.py     # M3U8/HLS core parser (AES-128 decrypt + merge)
    │   │   └── m3u8_tools.py # M3U8 download tool definitions
    │   ├── media.py        # multimedia (ReadMedia multimodal + GenerateImage)
    │   ├── docker/         # Docker container/image management
    │   │   ├── tools.py    # tool definitions
    │   │   ├── manager.py  # Docker manager (container lifecycle tracking)
    │   │   └── models.py   # data models
    │   ├── plan.py         # plan mode (enter/exit)
    │   ├── sleep.py        # async wait
    │   ├── ask.py          # user interaction (AskUserQuestion)
    │   ├── notify.py       # desktop notifications 🔔
    │   ├── computer_use/   # computer control (screenshot/mouse/keyboard) 🖥️
    │   │   ├── tools.py    # tool definitions
    │   │   ├── automation.py # automation abstraction
    │   │   ├── automation_win.py # Windows impl
    │   │   ├── automation_mac.py # macOS impl
    │   │   └── automation_linux.py # Linux impl
    │   ├── web_browse/     # browser automation (Playwright) 🌍
    │   ├── security/       # security checks and permissions 🔒
    │   ├── scheduler/      # scheduler ⏰ (session association + monitor type)
    │   ├── skill/          # skill system (loading/execution/builtin skills)
    │   ├── multi_agent/    # multi-agent (async + worktree isolation + tool whitelist)
    │   ├── mcp/            # MCP integration 🔌
    │   ├── memory/         # memory system (FTS5 search + auto-organize + auto-save) 🧠
    │   ├── knowledge/      # knowledge graph (entity/relation + auto-extract + visualization) 🗺️
    │   │   ├── __init__.py
    │   │   ├── graph.py    # core graph class (SQLite + FTS5)
    │   │   ├── tools.py    # tool definitions (16 tools)
    │   │   └── context.py  # context injection
    │   ├── todolist/       # todo list + overseer + goal system 📋
    │   ├── monitor/        # background process management (async + ConPTY visible terminal) 🔄
    │   │   ├── tools.py    # tool definitions
    │   │   ├── manager.py  # monitor manager
    │   │   ├── models.py   # data models
    │   │   ├── pty_session.py # ConPTY pseudo-terminal sessions
    │   │   └── viewer.py   # visible terminal window
    │   ├── restart/        # process restart (WebUI hot update) 🔁
    │   │   ├── tools.py    # restart_agent tool
    │   │   ├── manager.py  # restart orchestration (save session / shutdown server)
    │   │   ├── relauncher.py # restart execution and process launch
    │   │   └── resumer.py  # new-process session resume
    │   ├── webui/          # WebUI-specific tools (on-demand) 🎨
    │   │   └── a2ui.py     # get_a2ui_guide (A2UI component spec, WebUI main agent only)
    │   ├── session/        # session persistence + history search + auto-save 💬
    │   │   ├── session.py  # Session (dual-list storage + content-hash dedup)
    │   │   ├── session_manager.py # SessionManager (persists to .UniClaw/sessions/)
    │   │   ├── recall.py   # recall_history/get_history_range
    │   │   ├── notes.py    # session notes (add/update/delete/list/get) 📝
    │   │   └── tools.py    # session management tool definitions
    │   ├── hooks/          # Hook system 🪝
    │   ├── lsp/            # LSP language intelligence (server lifecycle + diagnostics/definition/hover/references/symbols) 🔤
    │   │   ├── client.py   # LspClient (subprocess + JSON-RPC + diagnostic cache)
    │   │   ├── manager.py  # LSPManager (per-language/root sessions, crash restart + idle reclaim)
    │   │   ├── protocol.py # LSP protocol constants and URI conversion
    │   │   ├── servers.py  # builtin server registry + local probing (pyright/TS/HTML/CSS)
    │   │   └── tools.py    # tool definitions
    │   ├── tts/            # text-to-speech 🔊
    │   ├── plugins/        # plugin system (user-level dynamic loading) 🔌
    │   │   ├── __init__.py
    │   │   └── loader.py   # PluginManager (hot reload/collision detection/lifecycle)
    │   ├── advisor.py      # advisor model tools (ask_advisor multi-model) 🎓
    │   ├── wechat.py       # WeChat tools (contacts/send text/image/file)
    │   ├── help.py         # AI self-help tools 📖
    │   ├── send_file.py    # file sending (WebUI text preview/download link) 📤
    │   ├── rag/            # RAG retrieval (ingest/search/collection management) 📚
    │   │   ├── tools.py    # tool definitions
    │   │   ├── rag.py      # RAG manager (vector DB + embedding)
    │   │   ├── loader.py   # document loader (multi-format)
    │   │   ├── splitter.py # document splitter (smart chunking)
    │   │   └── context.py  # context injection
    │   ├── a2a/            # A2A remote agents (client/server/manager) 🌉
    │   │   ├── client.py   # A2A client (Agent Card discovery + JSON-RPC)
    │   │   ├── server.py   # A2A server (mounted at WebUI /a2a)
    │   │   ├── manager.py  # A2A manager (config persistence + service state)
    │   │   └── tools.py    # tool definitions (send/submit/get/cancel/add/list)
    │   └── ipython/        # IPython kernel (persistent Python env) 🐍
    │       ├── kernel.py   # kernel manager (start/stop/execute/inspect)
    │       └── tools.py    # tool definitions
    │
    ├── utils/              # utilities
    │   ├── checkpoint.py   # file-snapshot checkpoint system
    │   ├── project_instruction.py # project instruction files (AGENTS.md first/CLAUDE.md fallback), parsing and sanitizing
    │   ├── downloader.py   # downloader base (BaseDownloader, unified DownloadManager)
    │   ├── http_download.py # HTTP download engine (multi-coroutine + resume, chunked)
    │   ├── usage.py        # token usage + OpenRouter pricing
    │   ├── tokenize.py     # tokenizer (for BM25; camelCase/underscore subword split)
    │   ├── truncation.py   # token-based text truncation
    │   ├── wakeup.py       # wake_agent (plugins/background tasks)
    │   ├── jev.py          # Jev structured decision (TypeSafe SDK: choice/score/noul/batch)
    │   └── ...             # git, format, cache, logger, frontmatter, etc.
    │
    ├── ilink_bot/          # iLink Bot WeChat protocol client
    │   ├── client.py       # API client
    │   ├── crypto.py       # message encryption
    │   ├── manager.py      # multi-account manager (async login)
    │   ├── media.py        # media handling
    │   ├── storage.py      # local message cache
    │   ├── models.py       # data models
    │   └── exceptions.py   # exceptions
    │
    └── assets/             # static resources (logo.png)
```

### Tool registry system

The core/extended tool layering aligns with Anthropic's `defer_loading` pattern:

- **Core tools** (17 + 1 meta-tool `search_tools`): always load full schemas — the stable prompt-cache prefix
  - File system: `Read`, `Write`, `Edit`, `Glob`, `apply_patch`
  - Shell: `Bash`, `Grep`
  - Web: `webFetch`, `webSearch` (multi-platform, Exa by default)
  - Memory: `memory_save/delete/list/search`
  - Plan: `enter/exit_plan_mode`
  - Skill: `skill_suggest/read`
  - Meta-tool: `search_tools` (on-demand extended tool discovery)
- **Extended tools** (188): not loaded initially; discovered on demand via `search_tools`
  - BM25 search with Chinese/English keywords + semantic synonyms
  - **LRU + energy mechanism**: each extended tool starts with 30 energy points, -1 per dialogue round, restored to full on call/search-hit, evicted at zero; at most 25 extended tools loaded at once — beyond that, the lowest-energy LRU end is evicted
  - Search results auto-inject into the current task's available tools
  - Organized by category: system management, WebUI, computer operations, multi-agent, todo list, process monitor, session management, scheduled tasks, MCP management, security, hooks, Docker, browser, knowledge graph, advisor, downloads, RAG, IPython, LSP, WeChat, files, help, notifications, etc.
  - **Availability tracking**: each module's `get_tools()` probes dependencies (Docker/Everything/embedding_model, etc.); unavailable tools are recorded in `config.unavailable_tool_reasons` with the reason; `search_tools` surfaces "currently unavailable (not enabled or no permission)" plus the reason so the AI wastes no calls. Calling a disabled tool directly returns "not enabled + reason"; only names outside the full inventory report "tool does not exist" — the wording distinction keeps callers from trying to rename tools. Jev features check the `TYPESAFE_API_KEY` env var

**Flow**: the AI needs a less-common tool → calls `search_tools(query)` → BM25 match → tool auto-loaded (evicting the LRU low-energy end if at cap) → callable next round. Loaded tools lose 1 energy per round, restore to full on call/search hit, evict at zero.

- **Plugin tools**: user-level external tool plugins, hot-loaded by `PluginManager` from `~/.UniClaw/plugins/tools/`. Registered into the extended-tool category and managed uniformly with builtin extended tools. See [Plugin System](#-plugin-system).

### LLM layer

Multi-provider architecture with automatic routing. `provider/router.py` exposes `stream()`, `astream()`, `chat()`, `achat()`, auto-selecting the provider from the model-name prefix — callers never touch the backend.

- **ProviderProfile**: each provider configures protocol/api_key/base_url independently; OpenAI and Anthropic protocols supported
- **Fallback**: `model_name` lists support multi-model fallback — on primary failure the next model is tried; `provider/fallback.py` wraps `chat()`/`achat()` for list-of-models fallback
- **Error classifier**: `provider/error_classifier.py` classifies LLM errors into six categories (RATE_LIMIT/CONTEXT_OVERFLOW/AUTH/SERVER_ERROR/TIMEOUT/UNKNOWN), each with its own retry strategy — AUTH/UNKNOWN fail over immediately without retry, CONTEXT_OVERFLOW compacts the session first, RATE_LIMIT uses exponential backoff, SERVER_ERROR linear backoff
- **Advisor models**: `large_model_name` configures advisor models; `ask_advisor` consults several in parallel when stuck
- **OpenAI SDK**: streaming + async, `reasoning_content` and thinking models
- **Anthropic SDK**: equivalent interface, auto-routed
- **ThoughtParser**: streams and parses `<thought>`/` thinking` tags, separating reasoning from body text
- **Output truncation detection**: `StreamChunk` carries `finish_reason` (OpenAI `finish_reason` / Anthropic `stop_reason`) through to the main loop; `length`/`max_tokens` means the output hit the cap — a warn fires, and tool-arg parse failures get clear guidance: retry in chunks or raise `max_tokens`
- **Effort enum**: reasoning depth (xhigh/high/medium/minimal/low/none) for OpenRouter's `reasoning.effort`
- **Multimodal fallback**: auto-retries with `multimodal_model_name` when the primary can't handle multimodal
- **Proxy compatibility**: auto-detects Google API / OpenRouter API and adapts `extra_body`
- **Request timeout**: 180 seconds by default; the `timeout` parameter on `stream()`/`chat()` etc. overrides the client default

### Jev structured decisions

TypeSafe-SDK-based structured AI decisions (`src/uniclaw/utils/jev.py`) — fast and cheap, integrated into several subsystems:

**Core primitives:**
- `choice` — pick from options (returns the pick, probability distribution, confidence)
- `score` — scale rating (interpolated between levels)
- `noul` — yes/no (0~1 probability)
- `batch` — batch decisions (multiple questions per call)

**Common pattern functions:**
- `select_one` — single select
- `select_many` — multi select
- `yes_no` — yes/no
- `rate` — scale rating
- `multi_judge` — multi-dimensional batch judgment (parallel evaluation of independent dimensions)

**Integrated scenarios:**
- **Memory search**: semantic relevance, Jev first
- **Security checks**: fast pre-screening before LLM analysis
- **RAG reranking**: document relevance scoring
- **Skill recommendation**: smart skill matching

**Config**: set the `TYPESAFE_API_KEY` env var (an environment variable, not a settings.json key). Jev falls back to the LLM automatically, so features always work.

### Workflow

```
User input → REPL receives message or slash command
    ↓
LLM reasoning (with thought-tag parsing) → permission check → tool execution
    ↓
ToolEvent (result)
    ↓
Update AgentState → auto-compact if pressure > 50%
    ↓
Next iteration or final response → session auto-save
```

### ♾️ Infinite context

UniClaw implements infinite context via **compaction + recall** — the conversation never loses memory. Three layers:

#### Dual-list storage

Session maintains two parallel message lists:

| List | Purpose | Characteristics |
|------|------|------|
| `_messages` | active context, sent to the LLM each time | bounded by the model's context window; compacted |
| `history` | full history, the single source of truth | grows unbounded, persisted to disk, never truncated |

Every message (user/assistant/tool) writes to both lists. Compaction only affects `_messages`; `history` stays complete.

**Tool-result dedup**: `ToolCallMessage` computes a SHA-256 content hash over "tool name + args + result" (with cache invalidation), so duplicates collapse; the main loop also folds identical read-only tool results (Read/Glob/Grep/webFetch) over 500 chars into a stub.

#### 3-level auto compaction

Compaction runs in the background, non-blocking:

| Level | Threshold | Strategy |
|------|----------|------|
| Level 0 | 50% | light: drop regenerable tool results (Read/Grep/Glob/webFetch/webSearch) |
| Level 1 | 70% | medium: LLM summary (keeps the recent 30%) |
| Level 2 | 85% | heavy: aggressive LLM summary (keeps the recent 15%) |

The summary captures current intent, next steps, files touched, completed/pending tasks, key decisions, errors, plus topic keywords for retrieval.

**Session-note protection**: notes via `session_note_add` are auto-injected into the summary (as "see note xxx"), so their body never gets lost. The AI proactively suggests notes on important information.

#### On-demand history recall

When the LLM needs earlier compacted content, two tools search `history`:

- **recall_history** — keyword search over archived messages, regex + context window
- **get_history_range** — history messages by index range, active/archived status

The system prompt auto-injects a recall hint: *"This session has N full history messages, M of which have been compacted out of the current context. When the user mentions or you need to recall compacted early content, use recall_history to search..."*

#### Workflow

```
User message → written to both _messages + history
    ↓
System prompt (with recall hint if archived messages exist)
    ↓
LLM reasoning → tool execution → background async compaction (maybe_compact)
    ↓
Need old context? → recall_history / get_history_range → fetch from history
    ↓
Session save → _messages + history serialized to disk
```

> 💡 **Cross-session persistence**: sessions persist to `.UniClaw/sessions/` via `SessionManager`, both lists serialized. Resumed sessions keep full history; recall tools keep working.

---

## 🗺️ Knowledge Graph System

UniClaw ships a SQLite-based knowledge graph with entity/relation management, querying, and visualization.

### Core features

- **Dual scope**: user level (shared across projects) and project level, managed independently
- **Smart search**: SQLite FTS5 prefix matching + LIKE substring fallback, mixed Chinese/English (Chinese adds `*` prefix wildcards automatically), covering the entity alias table
- **Smart extraction**: the AI auto-extracts entities and relations from text or files
- **Path discovery**: association paths between entities (multi-hop)
- **Entity merging**: dedup that transfers relations and aliases
- **Visual export**: HTML, JSON, Markdown

### Data model

**Entity types:**
- `person` / `place` / `concept` / `event` / `tool` / `organization` / `document` / `technology`

**Relation types:**
- `is_a` / `has` / `uses` / `related_to` / `created_by` / `belongs_to`

### Examples

#### Add entities and relations manually

```
# The AI calls the tools automatically
kg_add_entity(name="Python", type="technology", description="programming language")
kg_add_entity(name="Guido", type="person", description="father of Python")
kg_add_relation(source="Guido", target="Python", relation="created_by")
```

#### Auto-extraction

```
# From text
kg_extract(text="Python is an interpreted programming language created by Guido van Rossum in 1991.")

# From a file
kg_extract(path="docs/README.md")

# From a directory
kg_extract(path="src/")
```

#### Query and traverse

```
# Search entities
kg_search(keyword="Python")

# Entity details
kg_get_entity(name="Python")

# Neighbors (multi-hop)
kg_neighbors(name="Python", depth=2)

# Path
kg_path(source="Guido", target="artificial intelligence")
```

#### Slash commands

```bash
/kg                        # statistics
/kg search Python          # search entities
/kg list person            # list all persons
/kg export html            # export HTML visualization
/kg clear project          # clear the project-level graph
```

### Storage

- **User level**: `~/.UniClaw/knowledge.db` (shared across projects)
- **Project level**: `.UniClaw/knowledge.db` (current project)

### Visualization

Exported HTML opens an interactive knowledge graph in the browser:
- Node drag and zoom
- Relation-type filtering
- Entity search
- Detail viewing

---

## 📚 RAG Retrieval System

UniClaw ships an RAG (Retrieval-Augmented Generation) system with document ingestion, smart chunking, vector embedding, and semantic retrieval.

### Core features

- **Multi-format**: txt/md/py/json/yaml/csv/html/pdf and more
- **Smart chunking**: configurable chunk size and overlap for semantic coherence
- **Vector embedding**: automatic document embeddings for semantic similarity search
- **Multi-collection**: organize by topic or project; collections are independent
- **Dual scope**: user (shared across projects) and project level

### Examples

#### Ingest documents

```
# Single file
rag_ingest(path="docs/api.md", collection="api-docs")

# Whole directory
rag_ingest(path="src/", collection="source-code", chunk_size=500)

# User-level (shared across projects)
rag_ingest(path="./notes/", collection="my-notes", scope="user")
```

#### Semantic search

```
# Search related docs
rag_search(query="how to configure MCP servers", collection="api-docs")

# Limit count and relevance threshold
rag_search(query="auth flow", collection="api-docs", top_k=3, min_score=0.7)
```

#### Manage collections

```
# List collections
rag_list_collections()

# Set a description
rag_set_desc(collection="api-docs", description="API documentation")

# Delete a collection
rag_delete_collection(collection="old-docs")
```

### Storage

- **User level**: `~/.UniClaw/rag/chroma_data/` (shared across projects)
- **Project level**: `.UniClaw/rag/chroma_data/` (current project)

---

## 🔌 MCP Integration

UniClaw connects external tool services via MCP (Model Context Protocol), built on the mcp 2.x SDK.

### Supported transports

| Transport | Description | Typical use |
|------|------|----------|
| stdio | local process communication | local tool services |
| sse | Server-Sent Events | remote HTTP services |
| streamable_http | HTTP Streamable | remote HTTP services |

> ⚠️ The websocket transport was removed in mcp 2.x and is no longer supported. Legacy websocket configs will fail to connect — switch to one of the three above.

### Builtin MCP server

On first start, UniClaw writes the following builtin MCP server config to `~/.UniClaw/mcp.json`:

| Server | Transport | URL | Description |
|--------|------|-----|------|
| `exa` | streamable_http | `https://mcp.exa.ai/mcp` | Exa AI semantic search; auth params auto-appended when `EXA_API_KEY` is set |

> 💡 A user entry with the same name in `mcp.json` overrides the builtin default.

### Quick start

#### Way 1: slash commands (manual)

1. **Add an MCP server**

   ```
   /mcp add filesystem {"transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","D:/code"]}
   ```

2. **View configured servers**

   ```
   /mcp list
   ```

3. **View available tools**

   ```
   /mcp tools
   ```

#### Way 2: let the AI manage it (recommended)

Just tell the AI to add an MCP server — it calls the tools itself:

```
Add a filesystem MCP server with path D:/code
```

Or more precisely:

```
Add a web-search SSE MCP server at http://localhost:8080/sse
```

The AI calls `mcp_add_server` and refreshes the tool list.

### MCP management tools

Tools the AI calls directly, no slash command needed:

| Tool | Function | Example |
|---------|------|------|
| `mcp_add_server` | Add an MCP server | `mcp_add_server(name="fs", transport="stdio", command="npx", args=[...])` |
| `mcp_remove_server` | Remove an MCP server | `mcp_remove_server(name="fs")` |
| `mcp_toggle_server` | Enable/disable a server | `mcp_toggle_server(name="fs", enabled=False)` |
| `mcp_list_servers` | List servers and their tools | `mcp_list_servers()` — tool counts and descriptions per server |

`mcp_add_server` optional params: `env`/`cwd` (stdio env vars and working dir), `headers`/`timeout` (HTTP auth and timeout), `persistent` (persistent session, below).

**Example `mcp_list_servers` output:**

```
MCP servers (2 total):

  [✓ enabled] filesystem (stdio)
    npx -y @modelcontextprotocol/server-filesystem D:/code
    Tools: 5
      - read_file: read file content at a path
      - write_file: write content to a path
      - list_directory: list files and subdirectories
      - create_directory: create a new directory
      - delete_file: delete a file

  [✓ enabled] web-search (sse)
    http://localhost:8080/sse
    Tools: 3
      - search_web: run a web search and return results
      - get_page_content: get a URL's page content
      - extract_links: extract all links from a page
```

### Config file

MCP config lives at `~/.UniClaw/mcp.json`:

```json
{
  "servers": {
    "filesystem": {
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/code"],
      "persistent": true,
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

### Auth config

HTTP transports pass auth via `headers`:

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

### Persistent sessions 🔁

By default every tool call connects, executes, and disconnects independently. For services that must stay logged in (e.g. `login` then `query`) or slow-cold-start stdio servers, set `"persistent": true`:

- **Connection reuse**: connections are reused across tool calls in a conversation; the main agent and subagents share
- **Session isolation**: different conversations are isolated, each holding its own connection
- **Auto-reclaim**: connections are reclaimed after 30 minutes idle; deleting a session cleans up its persistent connections
- **Note**: calls with side effects aren't auto-retried on failure, so side effects don't run twice

### Multimodal results

Media content blocks from MCP tools become UniClaw multimodal messages for direct analysis:

| MCP block | Converted to |
|-----------|---------|
| `image` | image message (`image_url` data URI) |
| `audio` | audio message (`input_audio` data URI) |
| `video` | video message (`video_url` data URI) |
| `resource` | text resource → text; binary → converted by media type |
| `resource_link` | text placeholder (name + MIME type) |

> 💡 Media over the size limits (image 20MB / audio 25MB / video 100MB) degrade to a text placeholder so the context doesn't blow up.

### Command reference

| Command | Description |
|------|------|
| `/mcp list` | List all servers |
| `/mcp add <name> [json]` | Add a server (JSON config supported) |
| `/mcp remove <name>` | Remove a server |
| `/mcp enable <name>` | Enable a server |
| `/mcp disable <name>` | Disable a server |
| `/mcp tools [name]` | List available tools |

> All commands work in terminal and WeChat modes; `add` supports both interactive and JSON modes.

---

## 🌉 A2A Remote Agents

UniClaw supports the Agent2Agent (A2A) protocol — act as a client calling external A2A agents, or expose UniClaw itself as an A2A endpoint for other agents to call.

### Core features

- **Bidirectional**: call external agents and be called by external agents
- **Multi-turn**: `context_id` maintains continuity
- **Async tasks**: sync-wait and async-submit modes
- **Auto-discovery**: Agent Card (`/.well-known/agent-card.json`)
- **Persistent config**: agents and service state persist to `~/.UniClaw/a2a/a2a.json`
- **Isolated contexts**: each `context_id` holds an independent `AppConfig` and session

### As a client (call external agents)

#### Add an external agent

```bash
# Via slash command
/a2a add coder http://192.168.1.100:8080/a2a your-token

# Or let the AI call the tool
a2a_add_agent(agent_name="coder", url="http://192.168.1.100:8080/a2a", token="your-token")
```

#### Sync call (wait for the result)

```python
# The AI calls automatically
a2a_send_task(agent_name="coder", message="Write me a quicksort algorithm")
# Multi-turn
a2a_send_task(agent_name="coder", message="Optimize the performance", context_id="the context_id from the last turn")
```

#### Async call (returns immediately)

```python
# Submit
result = a2a_submit_task(agent_name="coder", message="Refactor the whole module")
# Returns: {"task_id": "xxx", "context_id": "yyy", "state": "working"}

# Query
a2a_get_task(agent_name="coder", task_id="xxx")

# Cancel
a2a_cancel_task(agent_name="coder", task_id="xxx")
```

> 💡 Async tasks wake the AI automatically on completion — no manual polling.

### As a server (expose UniClaw as an A2A endpoint)

#### Enable

```bash
# Start in WebUI mode first
uv run uniclaw --mode webui

# Enable the A2A endpoint
/a2a start

# View status (includes the Bearer Token)
/a2a status
```

#### Endpoints

| Endpoint | Description |
|------|------|
| `/.well-known/agent-card.json` | Agent Card (service capability description) |
| `/a2a` | JSON-RPC 2.0 endpoint |

#### JSON-RPC methods

| Method | Description |
|------|------|
| `message/send` | send a message and create a task |
| `tasks/get` | query task status and result |
| `tasks/cancel` | cancel a running task |

#### Another UniClaw instance connecting

```bash
# In another UniClaw instance
/a2a add remote-coder http://192.168.1.100:8080/a2a <token>

# Or have the AI call
a2a_add_agent(agent_name="remote-coder", url="http://192.168.1.100:8080/a2a", token="<token>")
a2a_send_task(agent_name="remote-coder", message="Review this code")
```

### Command reference

| Command | Description |
|------|------|
| `/a2a` or `/a2a status` | A2A service status |
| `/a2a start [token]` | Enable the A2A endpoint (WebUI mode only) |
| `/a2a stop` | Disable the A2A endpoint |
| `/a2a add <name> <URL> [token]` | Add a remote A2A agent |
| `/a2a list` | List configured remote agents |
| `/a2a remove <name>` | Remove a remote agent |
| `/a2a test <name>` | Test connectivity |

### Storage

- **Config**: `~/.UniClaw/a2a/a2a.json` (agent list + service state)
- **Workspace**: `~/.UniClaw/a2a/workspace/` (isolated A2A server working dir)

---

## ❓ FAQ

### Q: How do I change the LLM provider?

A: OpenAI and Anthropic protocols are supported; configure multiple providers via `providers` with auto-routing.

- **Add a provider**: add a config entry to `providers` with `protocol` (openai/anthropic), `api_key`, and `base_url`
- **Switch models**: edit `model_name` using `provider_name/model_name`, e.g. `["anthropic/claude-sonnet-4-20250514"]`
- **Fallback**: multiple models in `model_name` fall back automatically
- **First-time setup**: console mode runs the setup wizard on first start without a provider; WebUI mode uses the settings page or a pre-made `settings.json`

### Q: How do I use advisor models?

A: Advisor models let the AI consult more powerful models for a second opinion:

1. **Configure**: add advisor models to `large_model_name` in `settings.json`
   ```json
   {
     "large_model_name": ["default/o3", "anthropic/claude-opus-4-20250514"]
   }
   ```
2. **Auto-available**: the AI sees `advisor_list` and `ask_advisor` automatically once configured
3. **Concurrent consultation**: consult several models at once; results merge with sources labeled

**Use cases**: knowledge gaps, validating an approach, deep domain analysis.

### Q: Token usage too high?

A: Auto-compaction handles it with the 3-level strategy (50%/70%/85%); see [Infinite Context](#-infinite-context).

Compaction affects only the active context (`_messages`); the full history (`history`) stays on disk and the AI can always recall it via `recall_history`.

Manual options:
- `/compact` to trigger compaction
- start a new session (restart)
- shorten individual conversations
- use terser prompts

### Q: How do I enable Everything search?

A:
1. Download and install [Everything](https://www.voidtools.com/)
2. Make sure `es.exe` is on your PATH
3. Restart UniClaw

### Q: How do I use the WeChat bot?

A:
1. **Start the WebUI**: `uv run uniclaw --mode webui`
2. **Add an account**: click "Add WeChat account" in the sidebar WeChat area and scan the QR code
3. **Auto-listening**: logged-in accounts auto-start message listening
4. **Chat**: send messages from WeChat

Supported:
- Intelligent chat and Q&A
- Shell commands (`!` prefix)
- Image recognition
- Multi-account management

See [WeChat Bot Integration](#-wechat-bot-integration).

### Q: Where does WeChat bot data live?

A: Under `~/.UniClaw/`:
- Account config and credentials: `~/.UniClaw/wechat/`
- Session history: `~/.UniClaw/sessions/`

Created automatically on first use.

### Q: How does the memory system work?

A: Mostly automatic, but you can manage it:
- **Auto-loading**: the AI retrieves relevant memories from the conversation
- **Manual save**: `memory_save`
- **View**: `memory_list`
- **Delete**: `memory_delete`

Three scopes:
- **User**: applies to all projects (e.g. personal preferences)
- **Project**: applies to the current project only (e.g. project conventions)
- **Session**: current conversation only

### Q: How does the multi-agent system work?

A: Fully-async, specialized assistants collaborating:

**Basics**:
- **Create**: `subagent_create`
- **View definitions**: `subagent_list_definitions`
- **View tasks**: `subagent_list_tasks`
- **Assignment**: the AI picks the right agent type automatically

**Advanced**:
- **Parent → child**: `subagent_send_to_child`
- **Child → parent**: async children use `subagent_send_to_parent` to report progress or ask for direction
- **Results**: `subagent_check_result`
- **Close**: `subagent_close`
- **Discussion**: `subagent_discuss` for multi-agent multi-round collaboration
- **Keep alive**: async mode (`wait=False`) keeps an agent running for new instructions
- **Inheritance**: children inherit the parent's tool config and permissions
- **Wake-up**: children auto-notify the parent on completion

**Examples:**
```
# Sync code-review agent (returns when done)
subagent_create(prompt="review this code", subagent_type="reviewer", name="review-task")

# Async agent (returns immediately, runs in background)
subagent_create(prompt="write tests", subagent_type="tester", name="test-task", wait=False)

# Message to an agent
subagent_send_to_child(task_id="test-task", message="extra requirement")

# (Inside an async child) report to/ask the parent
subagent_send_to_parent(message="50% done, hit issue X, need direction")

# Check results
subagent_check_result(task_id="test-task")

# Close
subagent_close(task_id="test-task")
```

Create agents for code review, docs, data analysis… and coordinate them with messages.

### Q: How do I pick a permission mode?

A:
- **Development**: `accept-all` for speed
- **Production**: `auto` or `manual` for safety
- **Sensitive operations**: always `manual`

**Tip**: persistent permission rules remember your preferences. When the AI requests permission, press **`a`** in the dialog to auto-save a rule (always allow); similar commands/tools are auto-approved from then on:
```bash
# Press a in the confirmation dialog → rule saved to permission_rules.json

# View saved rules
/permissions list

# Remove a rule
/permissions remove bash "git commit"
```

### Q: How do I use detail display mode?

A:
- **Hotkey**: **F2** toggles detail/compact
- **Default**: compact (core info only)
- **Detail mode**: full tool-call args, token stats, and other metadata
- **Toggle**: F2

Detail mode suits debugging; compact suits daily use.

**File-edit preview**: in detail mode, permission requests for file edits show a diff preview:
- Added lines (green `+`)
- Deleted lines (red `-`)
- Context lines (gray, space prefix)
- Max display lines auto-limited to avoid dominating the screen

### Q: How do I interrupt a running task?

A: Press **ESC**:
- The task is marked CANCELLED
- The AI is notified and stops
- "Interrupted, waiting for your instructions..." appears
- Keep typing new instructions

> 💡 ESC triggers when the input is empty; with content in the input, ESC clears it first.

### Q: What's sleep_timer for?

A: An async wait tool that wakes the AI later:

**Use cases**:
- waiting for a service to start
- delayed retry after rate limiting
- interval control in scheduled tasks

**Example**:
```
# The AI calls it automatically
sleep_timer(seconds=30, name="wait for service start")
# returns immediately; the AI is woken 30s later
```

**Features**:
- non-blocking
- 1-3600 seconds
- optional description
- available in the main agent and async subagents; sync subagents don't get it (hand long waits to the parent)

### Q: What is plan mode?

A: A planning mode for complex tasks:

**How to enter**:
- **Tool call**: `enter_plan_mode`
- **Automatic**: the AI enters it proactively for complex tasks

**Flow**:
1. Enter plan mode; the AI analyzes and designs a detailed plan
2. The plan covers steps, file lists, risk assessment, etc.
3. You confirm, then the AI executes step by step
4. Auto-exits when done

**Use cases**:
- large refactors
- multi-file changes
- architecture changes
- sensitive operations needing your confirmation

### Q: How do desktop notifications work?

A: Sent automatically when:
- long-running tasks finish
- background process state changes
- scheduled tasks complete

Windows (Toast), macOS (osascript), Linux (notify-send).

### Q: How do I use the WebUI?

A:
1. Start it: `uv run uniclaw --mode webui`
2. Open the address shown in the terminal
3. Chat

LAN sharing: `uv run uniclaw --mode webui --host 0.0.0.0`, reachable via your IP.

**HTTPS**: on by default (self-signed). HTTP: `--no-ssl`. Custom domain: `--domain uniclaw.example.com`.

**Trusted IP auto-login**: set `trusted_ips` in `settings.json`, and those clients skip login.

### Q: Which operating systems are supported?

A: Windows, Linux, macOS. Some tools (e.g. Everything search) are Windows-only. WebUI works everywhere.

### Q: How do I debug tool calls?

A: The REPL shows tool-call details:
- name and args
- call ID
- result (truncated to 3000 chars)
- token usage

More info:
- `/usage` — token stats
- `/context` — context token breakdown (per-tool footprint)
- `/cost` — per-model cost

### Q: How do I use MCP tools?

A:
1. Add a server: `/mcp add <name> <JSON config>`
2. View tools: `/mcp tools`
3. The AI recognizes and calls MCP tools automatically

Filesystem example:
```
/mcp add fs {"transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","D:/code"]}
```

### Q: MCP server won't connect?

A:
1. Check the config (URL, command paths, etc.)
2. Confirm the server is running
3. Check network and firewall
4. Confirm the transport is stdio/sse/streamable_http (websocket was removed in mcp 2.x)
5. Check the logs for details

### Q: How do I use slash commands?

A: Type `/` commands in the REPL:
- `/clear` — clear history
- `/model gpt-4o` — switch models (capability tags and prices, dynamic role menu)
- `/cd /path/to/dir` — change the working directory
- `/skills` — view skills
- `/memory list` — view memories
- `/schedule list` — view scheduled tasks
- `/task` — manage background tasks
- `/cost` — cost stats
- `/doctor` — environment diagnostics
- `/checkpoint` — manage checkpoints (create/pop/apply/delete/diff)
- `/undo` — undo AI file edits
- `/resume fork` — fork a session
- `/goal` — set a goal stop condition
- `/explain` — tool explanation mode
- `/help` — all commands

Full list: [Slash command system](#slash-command-system).

### Q: How do I use file completion?

A: Type `@` in the REPL:

**Basics:**
- `@` — files/folders in the current directory
- `@src` — filter by `src` prefix
- `@src/` — enter `src`
- `@src/uniclaw/` — enter `src/uniclaw`

**Features:**
- multi-level subdirectory navigation
- directory names don't auto-add a trailing slash
- hidden files (`.` prefix) filtered
- file sizes and directory markers shown

### Q: How do I use subcommand completion?

A: Type a slash command and press space:

1. Type a command (e.g. `/schedule`)
2. Press space
3. Its subcommands appear (e.g. `list`, `add`, `remove`, `enable`, `disable`)
4. Type a prefix to filter

**Commands with subcommands:**
- `/memory` — `consolidate`
- `/schedule` — `list`, `add`, `remove`, `enable`, `disable`
- `/mcp` — `list`, `add`, `remove`, `show`, `edit`, `enable`, `disable`, `tools`, `refresh`
- `/permissions` — `list`, `remove`
- `/resume` — `list`, `del`, `search`, `fork`
- `/task` — `list`, `output`, `stop`, `matched`
- `/overseer` — `start`, `stop`
- `/checkpoint` — `create`, `pop`, `apply`, `delete`, `diff`
- `/goal` — `clear`, `status`
- `/kg` — `stats`, `search`, `list`, `export`, `clear`
- `/a2a` — `start`, `stop`, `status`, `list`, `add`, `remove`, `test`

### Q: How do I use tool explanation mode?

A: The AI explains its reason before each tool call:

```
/explain on              # enable (all tools)
/explain off             # disable
/explain Bash Read Edit  # enable for specific tools
```

**Effect**: a `_explain` argument notes the reason, shown in both WebUI and TUI.

**Use cases**:
- debugging tool-call logic
- learning how the AI uses tools
- reviewing the AI's intent

### Q: How do I use image generation?

A: Requires a capable model (e.g. DALL-E):

1. **Configure**: set `image_model` in `settings.json`
   ```json
   {
     "image_model": "default/dall-e-3"
   }
   ```
2. **Use**: just ask — "generate a sunset beach image"
3. **Save**: a `path` arg saves the image locally
4. **Analyze**: without a path, the image comes back as multimodal data for direct analysis

> 💡 With `image_model` set, `GenerateImage` is available. `/model` can assign a model as the image-gen model from its dynamic role menu.

### Q: How do I configure trusted IP auto-login?

A: Add `trusted_ips` to `settings.json`:

```json
{
  "trusted_ips": ["127.0.0.1"]
}
```

Clients from those IPs skip WebUI login. Good for trusted home/office devices.

> ⚠️ **Security**: only add trusted internal IPs; don't use this on public networks.

### Q: How do I use session-level config?

A: WebUI supports per-session model/parameter config. A request with `session_id` applies the change at two levels:

1. **Global**: written to `settings.json` (applies to all sessions on next load)
2. **Session memory**: the current session's `AppConfig` updates immediately (this round, no restart)

1. Change settings in the WebUI settings page
2. Choose "session-level save" — the config applies to the current session immediately and saves globally

**Use cases**:
- different models per task
- temporary temperature experiments that take effect now

### Q: How do I use scheduled tasks?

A: Manage via `/schedule`:

**Create:**
```
/schedule add "0 * * * *" "shell: git status" "check-git"
/schedule add "0 9 * * *" "agent: summarize yesterday's code changes" "daily-report"
/schedule add "0 3 * * *" "py: print('nightly job')" "nightly-python"
```

**Manage:**
```
/schedule list                # list
/schedule remove abc12345     # remove (list shows IDs)
/schedule disable abc12345    # disable
/schedule enable abc12345     # enable
```

Cron format (minute hour day month weekday), 1-minute granularity:
- `0 * * * *` — hourly
- `*/5 * * * *` — every 5 minutes
- `0 9 * * *` — daily 9:00
- `0 9 * * 1-5` — weekdays 9:00

Action types:
- `shell: <command>`
- `agent: <message>`
- `agent:<type>: <message>` (e.g. `agent:coder: refactor this`)
- `py: <python>`
- `monitor: <command> → agent[:<type>]: <message>`

### Q: How do I use background tasks?

A: Manage via `/task`; background tasks are started by the AI through `monitor_start`:

**View:**
```
/task                          # list
/task output abc123            # get output
/task output abc123 100        # last 100 lines
```

**Manage:**
```
/task stop abc123              # stop
/task matched abc123           # monitor match results
```

**Typical uses:**
- dev server: `monitor_start("npm run dev", name="dev server")`
- background build: `monitor_start("cargo build", watch_pattern="Finished")`
- big downloads: `monitor_start("curl -O https://example.com/file.zip")`

> 💡 Process end (natural exit/timeout/exception) auto-notifies and wakes the agent — no more watch_pattern + echo markers. An agent-initiated `/task stop` or `monitor_stop` doesn't trigger the end-wakeup.

### Q: How do I use voice mode?

A: Requires TTS model and audio device:

1. **Configure** `tts_model` and `audio` in `settings.json`
2. **Toggle**: `/voice on`, `/voice off`
3. **Status**: `/voice`

Voice mode plays AI replies as audio. Style control (happy/sad/gentle) and dialects (Northeastern/Sichuan/Cantonese).

### Q: What is Computer Use mode?

A: The AI can control your computer:

- **Screenshots**: capture and analyze
- **Mouse**: move, click, drag
- **Keyboard**: type, press keys

`/cu on` enables, `/cu off` disables. The global hotkey **Ctrl+U** emergency-cancels running write operations without toggling enablement.

> ⚠️ **Security**: the AI directly operates your computer — use in trusted environments.

### Q: How do I make the AI send me a file?

A: Via `send_file`:

- **WebUI**: a temporary download link (30 minutes by default)
- **WeChat**: sent directly
- **Console**: unsupported

Example: "generate a Python script and send it to me"

### Q: How do I use the knowledge graph?

A: Manual adding and auto-extraction:

**Manual:**
```
kg_add_entity(name="Python", type="technology", description="programming language")
kg_add_relation(source="Guido", target="Python", relation="created_by")
```

**Auto-extraction:**
```
kg_extract(text="Python is an interpreted programming language...")
kg_extract(path="docs/README.md")
```

**Query:**
```
kg_search(keyword="Python")          # search
kg_get_entity(name="Python")         # details
kg_neighbors(name="Python", depth=2) # neighbors
kg_path(source="A", target="B")      # path
```

**Slash commands:**
```bash
/kg                        # stats
/kg search Python          # search
/kg list person            # list entities
/kg export html            # export visualization
```

See [Knowledge Graph System](#-knowledge-graph-system).

### Q: How do I upload files in the WebUI?

A: Multiple ways:

**Drag-and-drop**:
1. drop files onto the chat area
2. types are detected and added as attachments
3. images/audio/video supported

**Size limits**:
- auto-limited; oversized files error out

**Multimedia**:
- Images: PNG, JPG, GIF, etc.
- Audio: MP3, WAV, etc.
- Video: MP4, etc.

### Q: Does the WebUI work on mobile?

A: Yes, fully responsive:

**Responsive design**:
- adapts to phones and tablets
- touch gestures
- mobile-optimized layout

**LAN access**:
```bash
uv run uniclaw --mode webui --host 0.0.0.0

# phone browser
https://your-ip:8080
```

HTTPS by default (self-signed); for HTTP add `--no-ssl` and visit `http://your-ip:8080`.

**Full featured**:
- chat works fully
- file upload
- tool-call visualization

### Q: How do I use the A2UI interactive UI?

A: A2UI lets the agent render interactive UIs instead of plain text — status overviews, action buttons, config forms, data charts.

**Basics**:
1. Start WebUI: `uv run uniclaw --mode webui`
2. Ask for something suited to a UI — "give me a restart-service button with current progress", "chart these data series"
3. The agent emits an `a2ui`-fenced block; the frontend renders it interactively

**Callback**: clicking a button with an `agent` action, or filling form controls and submitting, sends the action/form values back to the AI as a system message — closing the loop.

**Notes**:
- WebUI main agent only (not console/WeChat/subagents)
- Unknown components and invalid JSON fall back to a plain code block; nothing injected executes
- Full component list and data formats: [A2UI Interactive UI](#-a2ui-interactive-ui)

### Q: How do I use A2A remote agents?

A: Interoperate with other agent systems:

**As a client:**
1. Add: `/a2a add <name> <URL> [token]`
2. Test: `/a2a test <name>`
3. The AI calls `a2a_send_task` / `a2a_submit_task` automatically

**Expose UniClaw as an endpoint:**
1. Start WebUI: `uv run uniclaw --mode webui`
2. Enable: `/a2a start`
3. Get the token: `/a2a status`
4. Others discover capability via `/.well-known/agent-card.json`

**Multi-turn:** `context_id` maintains continuity every turn.

**Async:** `a2a_submit_task` returns immediately; the AI is woken on completion.

### Q: How secure is the A2A service?

A: Bearer Token auth:
- random token generated at startup (or given explicitly)
- every request must carry it in the `Authorization` header
- stored in `~/.UniClaw/a2a/a2a.json`
- A2A tasks use an independent `AppConfig`, isolated from the main session

> ⚠️ Recommended for trusted networks only; don't leak the token to untrusted third parties.

### Q: How do I use the plugin system?

A: Extend tools without touching UniClaw source:

**Quick start (recommended):**
Just tell the AI what tool you need:
```
Write me a weather tool that takes a city and returns the weather
```
The AI runs `/tool-plugin-forge`: capture intent → check collisions → generate code → verify loading.

**By hand:**
1. Create a `.py` file in `~/.UniClaw/plugins/tools/`
2. Define a tool function with `@tool`
3. Implement `get_tools()` returning the tools
4. Call `search_tools` to trigger loading

**Notes:**
- no collisions with builtin names (check via `list_builtin_tools`)
- no underscore-prefixed filenames
- long-running tasks must use async + wake_agent

See [Plugin System](#-plugin-system).

### Q: Plugin tools vs. builtin tools?

A:
- **Builtin**: ship with UniClaw, always available, maintained by developers
- **Plugin**: user-defined, in `~/.UniClaw/plugins/tools/`, hot-loadable/reloadable
- **Loading**: builtin tools split into core (always loaded) and extended (on-demand); plugins are managed by PluginManager
- **Collisions**: plugin names can't collide with builtin names (skipped otherwise)

### Q: Plugin won't load?

A: Common checks:
1. File under `~/.UniClaw/plugins/tools/`?
2. Filename not underscore-prefixed (skipped)?
3. `get_tools()` present and returning a non-empty list?
4. Name not conflicting with builtin tools (`list_builtin_tools`)
5. Python syntax valid?
6. Check the logs for the specific warning

### Q: What is Jev and how do I configure it?

A: TypeSafe-SDK-based structured decisions:

**Core**:
- **select_one/select_many**
- **yes_no** (0~1 probability)
- **rate**
- **multi_judge**

**Integrated**:
- memory search
- security pre-screening
- RAG reranking
- skill recommendation

**Setup**: set the `TYPESAFE_API_KEY` env var:
```bash
# Linux/macOS
export TYPESAFE_API_KEY=your_typesafe_api_key

# Windows (PowerShell)
$env:TYPESAFE_API_KEY="your_typesafe_api_key"

# Windows (CMD)
set TYPESAFE_API_KEY=your_typesafe_api_key
```

**Why**:
- fast (10-100x the LLM)
- cheap (structured decisions cost less than full LLM calls)
- stable (same input, same output)
- auto-fallback to the LLM — features always work

> 💡 Jev suits fast, cheap decisions. For complex reasoning, use full LLM calls.

### Q: How do I deploy UniClaw with Docker?

A: Official Dockerfile:

```bash
# Build
docker build -t uniclaw .

# Run (config file mounted; session data stays inside the container)
docker run -d --name uniclaw \
  -p 8082:8082 \
  -v ~/.UniClaw/settings.json:/root/.UniClaw/settings.json \
  uniclaw
```

The Dockerfile is based on `python:3.14-slim` with `uv` and pre-installed Playwright Chromium. Default: WebUI mode on `0.0.0.0:8082`.

**Note**: API keys are needed on first run. Prepare `~/.UniClaw/settings.json` via a mounted directory before starting.

### Q: How do I attach to an external browser?

A: `browser_connect` via CDP (Chrome DevTools Protocol):

1. Start Chrome/Chromium with remote debugging:
   ```bash
   chrome --remote-debugging-port=9222
   ```
2. The AI calls `browser_connect(cdp_url="http://localhost:9222")`
3. Reuse logged-in browser sessions — no re-login

For authenticated pages and browser-state reuse.

### Q: How do I use LSP language intelligence?

A: Language servers provide real-time type diagnostics and semantic queries; the AI calls them on demand:

**Core tools**:
- `lsp_diagnostics` — diagnostics (post-edit self-check)
- `lsp_definition` / `lsp_hover` / `lsp_references`
- `lsp_symbols` — symbol outline
- `lsp_status` / `lsp_start` / `lsp_stop` — lifecycle

**Enable**:
1. Install language servers (tools return install instructions when missing):
   ```bash
   # Python (pyright, recommended; standalone binary, no Node)
   uv pip install 'uniclaw[lsp]'

   # JavaScript/TypeScript
   npm i -g typescript-language-server typescript@5

   # HTML/CSS
   npm i -g vscode-langservers-extracted
   ```
2. No config needed; servers lazily start on first query
3. Ask the AI, e.g. "check config.py for type errors" or "who references this function"

**Notes**:
- servers are managed per "language + project root"; crash auto-restart, idle reclaim
- diagnostics are real-time; large projects may take seconds on first index — retry shortly
- discover these tools via `search_tools` with "lsp diagnostics", etc.

## 📄 License

MIT License.