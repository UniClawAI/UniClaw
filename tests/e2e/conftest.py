"""Playwright E2E 测试共享 fixture — WebUI 前端真实浏览器测试。

设计目标:
- 完全隔离:测试用临时用户目录(USERPROFILE/HOME),不触碰真实 ~/.UniClaw
- 确定性:预置测试账号 + token,通过 cookie 让 auth 中间件放行,localStorage 供 JS fetch 用
- 离线可跑:拦截外部 CDN(fonts/jsdelivr 等),前端在无外网时也能初始化
- 后台线程起 uvicorn,Playwright 主线程操作浏览器;通过 run_coroutine_threadsafe
  向服务端事件循环注入 WS 广播事件,驱动前端 live 路径渲染

用法:
    uv run pytest tests/e2e/ -v -m e2e
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import threading
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

# 注意:这里不能直接 import uniclaw.webui.ws —— 它依赖 Path.home(),
# 必须等环境隔离设置完成后才能 import(见下方模块级隔离代码)。
# broadcast() 里延迟 import 是刻意的。uvicorn 的延迟 import 同理(避在
# 隔离环境设置前加载整棵 uniclaw 包)。

# 必须最先设置隔离环境(任何 uniclaw 模块 import 之前)。
# 测试进程被 pytest 拉起,这里在 conftest import 阶段就完成隔离,
# 确保 webui_server fixture 里 import uniclaw 时 Path.home() 已经指向临时目录。
_E2E_HOME = Path(os.environ.get("UNICLAW_E2E_HOME") or "")
if not str(_E2E_HOME).strip() or _E2E_HOME == Path("."):
    # 未显式指定:落在系统临时目录下,避免把 USERPROFILE 指到当前工作目录
    # (Path("") == Path("."),当前目录 is_dir() 恒为 True,必须显式排除)
    _E2E_HOME = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp"))) / f"uniclaw-e2e-{uuid.uuid4().hex[:8]}"
    _E2E_HOME.mkdir(parents=True, exist_ok=True)
os.environ["USERPROFILE"] = str(_E2E_HOME)
os.environ["HOME"] = str(_E2E_HOME)
# Node.js os.homedir() 在 Windows 按 USERPROFILE → HOMEDRIVE+HOMEPATH → HOME 解析,
# 全部指向隔离目录,避免 Playwright driver 子进程因 homedir 不存在而启动失败
if _E2E_HOME.is_absolute():
    os.environ["HOMEDRIVE"] = _E2E_HOME.drive or "C:"
    os.environ["HOMEPATH"] = str(_E2E_HOME).replace(_E2E_HOME.drive, "", 1)
else:
    os.environ["HOMEDRIVE"] = "C:"
    os.environ["HOMEPATH"] = "\\" + str(_E2E_HOME)


@pytest.fixture(scope="session")
def e2e_home() -> Path:
    """会话级隔离的用户目录(UNICLAW_E2E_HOME / USERPROFILE 指向它)。"""
    return _E2E_HOME


def pytest_collection_modifyitems(config, items):
    """默认排除 e2e 测试;显式 -m e2e 时正常执行。

    设计:日常 `uv run pytest tests/` 不会触碰真实 ~/.UniClaw(全部自动 skip),
    只有显式 `-m e2e`(或 `-m 'e2e'`)才运行这些测试。
    """
    marker_expr = (config.getoption("-m") or "").strip()
    # 显式选择 e2e 的表达式:单独 e2e,或含 e2e 的复合表达式(如 'e2e and not slow')
    tokens = [t.strip() for t in marker_expr.replace("(", " ").replace(")", " ").split()]
    explicit_e2e = "e2e" in tokens

    if not explicit_e2e:
        skip_e2e = pytest.mark.skip(reason="前端 E2E 测试,需 -m e2e 显式运行")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)


# 外部 CDN 域名:测试离线可跑
_CDN_PATTERN = re.compile(
    r"https?://("
    r"fonts\.googleapis|fonts\.gstatic|cdn\.jsdelivr|cdnjs\.cloudflare|"
    r"unpkg|code\.jquery|cdn\.tailwindcss|cdnjs\.cloudflare"
    r")"
)


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebUIServer:
    """后台线程中运行的 uvicorn 服务 + 事件循环桥。"""

    def __init__(self, port: int, home: Path):
        self.port = port
        self.home = home
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server = None
        self.token: str = ""
        self.base_url = f"http://127.0.0.1:{port}"
        self._ready = threading.Event()

    def start(self) -> None:
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("uvicorn 服务启动超时")

    def _run(self) -> None:
        os.environ["USERPROFILE"] = str(self.home)
        os.environ["HOME"] = str(self.home)
        import uvicorn
        from uniclaw.webui.app import app

        # 关键:不能在 _run 里调用 server.run() —— 它会用 asyncio.Runner 创建
        # 一个与 self.loop 无关的新事件循环,导致 run_coroutine_threadsafe 提交
        # 的任务永远不会被调度(前面的 _serve_and_signal 包装法因此失效)。
        # 正确做法:自己在后台线程创建并运行事件循环,把 server.serve 挂进去。
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="error",
            lifespan="on",
        )
        self.server = uvicorn.Server(config)
        # 端口监听成功(serve 进入轮询前)即视为就绪
        orig_serve = self.server.serve

        async def _serve_and_signal():
            self._ready.set()
            await orig_serve(sockets=None)

        try:
            self.loop.run_until_complete(_serve_and_signal())
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    def broadcast(self, data: dict, timeout: float = 2.0) -> None:
        """向服务端事件循环注入一次 _broadcast(驱动前端 live 渲染)。

        不严格等待广播完成:僵尸连接会让 _safe_send 阻塞最多 10 秒,
        测试侧只需确认事件已提交到事件循环即可(fire-and-forget)。
        前端渲染由后续 wait_for_function 断言。
        """
        from uniclaw.webui import ws as ws_mod

        if self.loop is None:
            raise RuntimeError("服务事件循环未就绪")
        fut = asyncio.run_coroutine_threadsafe(ws_mod._broadcast(data), self.loop)
        try:
            fut.result(timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            # 广播可能被僵尸连接拖慢,不影响前端最终渲染;放弃等待
            pass

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        # 等待端口释放
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    time.sleep(0.1)
            except OSError:
                return
        time.sleep(0.2)


@pytest.fixture(scope="session")
def webui_server(e2e_home) -> WebUIServer:
    """启动测试 WebUI 服务(隔离用户目录 + 预置账号)。"""
    import json
    import urllib.request

    from uniclaw.context import get_app_dir, Scope

    # 预置隔离 settings.json
    # 配置一个 fake provider + model_name:防止 App._checkModelConfig() 探测到
    # 未配置模型时自动弹出设置弹窗,遮挡侧栏导致点击无效。
    # fake key/base_url 不会被真实调用(测试不发 AI 请求)。
    cfg_dir = get_app_dir(Scope.USER)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    (cfg_dir / "settings.json").write_text(
        json.dumps(
            {
                "trusted_ips": [],
                "providers": {
                    "e2e-fake": {
                        "name": "e2e-fake",
                        "protocol": "openai",
                        "api_key": "sk-e2e-test-key",
                        "base_url": "http://127.0.0.1:1/v1",
                    }
                },
                "model_name": ["e2e-fake/e2e-model"],
                "mini_model_name": ["e2e-fake/e2e-model"],
            }
        ),
        encoding="utf-8",
    )

    # 预置测试账号
    from uniclaw.webui import auth

    username = f"e2e_{uuid.uuid4().hex[:8]}"
    auth.create_user(username, "e2e-test-pass")
    token = auth.create_token(username)

    port = _free_port()
    server = WebUIServer(port, e2e_home)
    server.token = token
    server.start()

    # 等 HTTP 就绪
    for _ in range(50):
        try:
            with urllib.request.urlopen(
                f"{server.base_url}/api/auth/status", timeout=1
            ) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        server.stop()
        raise RuntimeError("WebUI HTTP 服务未就绪")

    yield server
    server.stop()


@pytest.fixture(scope="session")
def browser(e2e_home):
    """Playwright 浏览器(带会话级 context 和 CDN 拦截)。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@contextmanager
def _webui_page(browser, server: WebUIServer, token: str | None = None):
    """打开 WebUI 主页:cookie 放行中间件 + localStorage 供 JS fetch + CDN 拦截。

    返回 page。用法: with _webui_page(...) as page:
    """
    token = token or server.token
    ctx = browser.new_context()
    ctx.add_cookies(
        [{"name": "uniclaw_token", "value": token, "domain": "127.0.0.1", "path": "/"}]
    )
    page = ctx.new_page()
    page.route(
        _CDN_PATTERN,
        lambda route: route.abort(),
    )
    page.add_init_script(f"localStorage.setItem('uniclaw_token', '{token}')")
    try:
        page.goto(f"{server.base_url}/", timeout=20000)
        yield page
    finally:
        try:
            ctx.close()
        except Exception:
            # 浏览器已关闭等场合,关闭失败可忽略
            pass


@pytest.fixture
def webui_page(browser, webui_server):
    """函数级:打开 WebUI 主页并等待聊天输入框就绪。"""
    with _webui_page(browser, webui_server) as page:
        page.wait_for_selector("#chat-input", timeout=10000)
        yield page


@pytest.fixture
def open_page(browser, webui_server):
    """工厂 fixture:每个测试可按需打开隔离 context 的 WebUI 页面。

    返回 (page, context_manager);测试结束或不再需要页面时调用 cm.__exit__()。
    页面就绪后会话树分类已渲染(异步数据拉取完成的标志)。
    """

    def _open():
        cm = _webui_page(browser, webui_server)
        page = cm.__enter__()
        page.wait_for_selector("#chat-input", timeout=10000)
        # 会话树数据是异步拉取的,等待初始拉取完成
        page.wait_for_function(
            "() => document.querySelectorAll('#session-tree .tree-category').length > 0",
            timeout=10000,
        )
        # 兜底:隐藏可能自动弹出的设置弹窗,避免遮挡侧栏/聊天区交互
        if page.locator("#settings-modal:not(.hidden)").count():
            page.locator("#settings-modal").evaluate(
                "(el) => el.classList.add('hidden')"
            )
            page.wait_for_timeout(200)
        return page, cm

    return _open
