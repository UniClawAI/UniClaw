"""语言服务器注册表 — 内置定义 + 本机探测。

设计原则:不做用户可配置面。LSP 对大多数用户太专业(配置服务器命令/
参数超出普通用户认知),服务器选择完全由内置注册表 + 本机探测决定:

内置定义结构:
    <language>: {
        "name": 服务器显示名,
        "command": 探测命令候选(shutil.which 顺序命中第一个),
        "args": 启动参数,
        "extensions": 关联的文件扩展名,
    }
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Optional

from uniclaw.utils.process import run_command

# 内置服务器注册表
BUILTIN_SERVERS: dict[str, dict] = {
    "python": {
        "name": "pyright",
        "command": ["pyright-langserver"],
        "args": ["--stdio"],
        "extensions": [".py", ".pyw"],
    },
    "javascript": {
        "name": "typescript-language-server",
        "command": ["typescript-language-server"],
        "args": ["--stdio"],
        "extensions": [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"],
        # 该服务器需要 tsserver 路径(typescript 编译器),见 detect_tsserver_path
        "needs_tsserver": True,
        # initialize 响应后需等 tsserver 启动(此通知为 ready 信号)
        "ready_notification": "$/typescriptVersion",
    },
    "html": {
        "name": "vscode-html-language-server",
        "command": ["vscode-html-language-server"],
        "args": ["--stdio"],
        "extensions": [".html", ".htm", ".vue"],
        # 完整 capabilities 会令 vscode-languageserver 系注册 pull diagnosticProvider
        # 但 pull 未实现(-32601);最小声明使其走 push(publishDiagnostics)
        "minimal_capabilities": True,
    },
    "css": {
        "name": "vscode-css-language-server",
        "command": ["vscode-css-language-server"],
        "args": ["--stdio"],
        "extensions": [".css", ".scss", ".less"],
        "minimal_capabilities": True,
    },
}

# 文件扩展名 → LSP languageId(TS 服务器对 .ts/.js 区分类型)
_LANGUAGE_ID_BY_EXT: dict[str, str] = {
    ".py": "python", ".pyw": "python",
    ".ts": "typescript", ".tsx": "typescriptreact",
    ".js": "javascript", ".jsx": "javascriptreact",
    ".mjs": "javascript", ".cjs": "javascript",
    ".html": "html", ".htm": "html", ".vue": "html",
    ".css": "css", ".scss": "css", ".less": "css",
}

# tsserver.js 相对各 node_modules 根的统一相对路径
_TSSERVER_REL = Path("typescript/lib/tsserver.js")


def language_id_for(path: str) -> str:
    """按文件扩展名返回 LSP languageId(未识别返回 plaintext)。"""
    return _LANGUAGE_ID_BY_EXT.get(Path(path).suffix.lower(), "plaintext")


async def detect_tsserver_path(workspace_root: Path | None = None) -> Optional[str]:
    """探测 TypeScript 编译器 tsserver.js 路径(typescript-language-server 必需)。

    先收集候选根目录列表(node_modules 根),再统一拼接 _TSSERVER_REL 检查。
    优先级:workspace 内 node_modules → 常见全局 npm 目录 → npm root -g 动态定位。
    返回 None 表示未找到(安装提示会引导)。
    """
    bases: list[Path] = []
    if workspace_root:
        bases.append(Path(workspace_root) / "node_modules")
    bases += [
        Path.home() / "AppData/Roaming/npm/node_modules",  # Windows npm 全局
        Path.home() / ".npm-global/lib/node_modules",     # 常见 Linux 全局
    ]
    # npm root -g 动态定位(经 run_command 统一处理超时/解码/回收)
    try:
        r = await run_command(["npm", "root", "-g"], timeout=5)
        root = r.stdout.strip()
        if root:
            bases.append(Path(root))
    except Exception:
        pass
    # 统一拼接 tsserver 相对路径,命中即返回
    for base in bases:
        candidate = base / _TSSERVER_REL
        if candidate.is_file():
            return str(candidate)
    return None


def get_server_def(language: str) -> Optional[dict]:
    """获取语言的内置服务器定义;未注册返回 None。"""
    return BUILTIN_SERVERS.get(language)


def detect_language(path: str) -> Optional[str]:
    """根据文件扩展名推断语言 id;未识别返回 None。"""
    ext = Path(path).suffix.lower()
    for lang, defn in BUILTIN_SERVERS.items():
        if ext in defn.get("extensions", []):
            return lang
    return None


def probe_command(server_def: dict) -> Optional[str]:
    """按候选顺序探测本机可用的服务器命令。

    优先检查当前 Python 解释器同目录(venv 的 Scripts)——pip/uv 安装的
    服务器(pyright 等)落在那里,即使 venv 未激活、不在 PATH 也能命中;
    再按 PATH 探测(npm 全局安装等)。

    Returns:
        命中的命令路径(含 .cmd/.bat 包装路径);全部未命中返回 None。
    """
    venv_bin = Path(sys.executable).resolve().parent
    for cmd in server_def.get("command", []):
        # 1) 当前解释器同目录(venv Scripts):pip/uv 安装
        for name in (cmd, f"{cmd}.exe", f"{cmd}.cmd"):
            candidate = venv_bin / name
            if candidate.is_file():
                return str(candidate)
        # 2) PATH 探测(npm 全局等)
        found = shutil.which(cmd)
        if found:
            return found
    return None


def server_install_hint(language: str) -> str:
    """语言服务器缺失时的安装指引(按 uniclaw 技术栈优先)。"""
    hints = {
        # pyright 是 uniclaw 的 LSP 可选依赖(uv pip install 'uniclaw[lsp]');
        # 其 PyPI 包自带独立二进制(捆绑 node runtime),无需系统 Node
        "python": "uv pip install 'uniclaw[lsp]'(或 pip install 'uniclaw[lsp]';npm i -g pyright 仅备选)",
        # typescript-language-server 需要 typescript 编译器;注意 TS7 无 tsserver,
        # 需用 5.x(npm i -g typescript@5)
        "javascript": "npm i -g typescript-language-server typescript@5",
        # HTML/CSS 来自同一个包:VS Code 官方语言服务器提取版
        "html": "npm i -g vscode-langservers-extracted",
        "css": "npm i -g vscode-langservers-extracted",
    }
    return hints.get(language, "安装对应语言的 LSP 服务器后重试")
