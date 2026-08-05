"""FastAPI 应用实例。"""

from __future__ import annotations
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from uniclaw.webui.api import router as api_router
from uniclaw.webui.ws import websocket_endpoint
from uniclaw.webui import auth
from uniclaw.webui.crypto import decrypt_data, is_encrypted
from uniclaw.tools.a2a.server import mount_a2a_routes

logger = logging.getLogger(__name__)

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = Path(__file__).parent.parent / "assets"

# 认证白名单路径前缀(不需要登录即可访问)
_AUTH_WHITELIST = (
    "/login.html",
    "/api/auth/",
    "/static/",
    "/assets/",
    "/favicon.ico",
    "/a2a",
    "/.well-known/agent-card.json",
    "/.well-known/agent.json",
)


def _is_trusted_ip(ip: str) -> bool:
    """检查 IP 是否在可信列表中。"""
    from uniclaw.config import get_config_path

    try:
        path = get_config_path()
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        trusted = data.get("trusted_ips", []) or []
        return ip in trusted
    except Exception as e:
        logger.debug("检查可信 IP 失败: %s", e)
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。"""
    # 启动时初始化微信 BotManager
    from uniclaw.ilink_bot.manager import BotManager

    manager = BotManager()
    # 注册消息处理器(复用微信模式)
    if not manager._handlers:
        from uniclaw.wechat.run import make_handler

        handler = make_handler()
        manager.on_message(handler)
    # 启动已登录 bot 的消息轮询
    if any(b.is_logged_in for b in manager.bots) and not manager.is_running:
        asyncio.create_task(manager.start())
    yield
    # 关闭时停止 BotManager
    if manager.is_running:
        manager.stop()


app = FastAPI(title="UniClaw WebUI", version="1.0.0", lifespan=lifespan)
mount_a2a_routes(app)

# CORS 中间件(开发用)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 认证中间件 ────────────────────────────────────────────────────────────


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """拦截未登录请求,白名单路径和可信 IP 放行。"""
    path = request.url.path

    # 白名单路径放行
    for prefix in _AUTH_WHITELIST:
        if path.startswith(prefix) or path == prefix:
            return await call_next(request)

    # 可信 IP 放行
    client_ip = request.client.host if request.client else ""
    if _is_trusted_ip(client_ip):
        return await call_next(request)

    # 提取 token(header 或 cookie)
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("uniclaw_token")

    # 验证 token
    if token and auth.verify_token(token):
        return await call_next(request)

    # 未登录:API 请求返回 401,页面请求重定向登录页
    if path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "未登录"})
    return RedirectResponse(url="/login.html", status_code=302)


# ── 加密静态文件解密中间件 ──────────────────────────────────────────────────

# 扩展名 → Content-Type 映射
_EXT_CONTENT_TYPE: dict[str, str] = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# 需要检测加密的扩展名
ENCRYPTABLE_EXTS = {".js", ".html"}


@app.middleware("http")
async def decrypt_static_middleware(request: Request, call_next):
    """拦截静态文件请求,若文件已加密则解密后返回。"""
    path = request.url.path

    # 仅处理 /static/ 和 /assets/ 路径
    if not (path.startswith("/static/") or path.startswith("/assets/")):
        return await call_next(request)

    # 计算文件系统路径
    if path.startswith("/static/"):
        rel = path[len("/static/") :]
        base_dir = STATIC_DIR
    else:
        rel = path[len("/assets/") :]
        base_dir = ASSETS_DIR

    file_path = base_dir / rel

    # 只对可加密扩展名做检测,其他文件走正常流程
    if file_path.suffix.lower() not in ENCRYPTABLE_EXTS:
        return await call_next(request)

    if not file_path.exists():
        return await call_next(request)

    try:
        data = file_path.read_bytes()
        if not is_encrypted(data):
            return await call_next(request)  # 未加密,正常处理

        data = decrypt_data(data)
        ext = file_path.suffix.lower()
        ct = _EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
        return Response(content=data, media_type=ct)
    except Exception as e:
        logger.warning("静态文件解密失败: %s", e)
        return Response(content=b"Forbidden", status_code=403)


# ── 认证 API ──────────────────────────────────────────────────────────────


class _AuthRequest(BaseModel):
    username: str
    password: str


class _ChangePwdRequest(BaseModel):
    new_password: str


@app.get("/api/auth/status")
async def auth_status():
    """检查是否已有账号。"""
    return {"has_user": auth.user_exists()}


@app.post("/api/auth/register")
async def auth_register(req: _AuthRequest):
    """创建账号(仅首次无账号时可用)。"""
    if auth.user_exists():
        return JSONResponse(
            status_code=400,
            content={"detail": "账号已存在,请直接登录"},
        )
    if len(req.password) < 6:
        return JSONResponse(
            status_code=400,
            content={"detail": "密码至少 6 位"},
        )
    try:
        auth.create_user(req.username, req.password)
        token = auth.create_token(req.username)
        resp = JSONResponse(content={"token": token, "username": req.username})
        resp.set_cookie(
            "uniclaw_token",
            token,
            httponly=False,  # 允许 JavaScript 读取
            samesite="lax",
            max_age=86400,
        )
        return resp
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"创建失败: {e}"},
        )


@app.post("/api/auth/login")
async def auth_login(req: _AuthRequest, request: Request):
    """登录,返回 JWT。"""
    client_ip = request.client.host if request.client else ""

    # 检查 IP 限流
    wait = auth.login_rate_limiter.check(client_ip)
    if wait is not None:
        return JSONResponse(
            status_code=429,
            content={
                "detail": f"登录失败次数过多,请等待 {wait} 秒后重试",
                "retry_after": wait,
            },
        )

    if not auth.user_exists():
        return JSONResponse(
            status_code=400,
            content={"detail": "账号不存在,请先创建"},
        )
    if not auth.verify_user(req.username, req.password):
        # 记录失败
        auth.login_rate_limiter.record_failure(client_ip)
        return JSONResponse(
            status_code=401,
            content={"detail": "用户名或密码错误"},
        )
    # 登录成功,清除失败记录
    auth.login_rate_limiter.record_success(client_ip)
    token = auth.create_token(req.username)
    resp = JSONResponse(content={"token": token, "username": req.username})
    resp.set_cookie(
        "uniclaw_token",
        token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return resp


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """获取当前用户信息(验证 token 有效性)。"""
    # 此端点在白名单中(跳过中间件),需要自行验证 token
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("uniclaw_token")

    # 验证 token
    if token and auth.verify_token(token):
        user = auth.get_user()
        if user:
            return {"username": user.username, "created_at": user.created_at}
        return JSONResponse(status_code=404, content={"detail": "无用户"})

    # 可信 IP 无需 token,返回默认用户信息
    client_ip = request.client.host if request.client else ""
    if _is_trusted_ip(client_ip):
        user = auth.get_user()
        if user:
            return {
                "username": user.username,
                "created_at": user.created_at,
                "trusted_ip": True,
            }
        # 可信 IP 且无用户,返回特殊状态让前端跳转注册
        return JSONResponse(
            status_code=404, content={"detail": "无用户", "trusted_ip": True}
        )

    return JSONResponse(status_code=401, content={"detail": "未登录或 token 已过期"})


@app.post("/api/auth/change-password")
async def auth_change_password(req: _ChangePwdRequest, request: Request):
    """修改密码。"""
    if not auth.user_exists():
        return JSONResponse(status_code=400, content={"detail": "无账号"})
    if len(req.new_password) < 6:
        return JSONResponse(status_code=400, content={"detail": "密码至少 6 位"})
    auth.change_password(req.new_password)
    return {"ok": True}


# ── 注册路由 ──────────────────────────────────────────────────────────────

# 注册 REST API 路由
app.include_router(api_router)

# WebSocket 路由
app.add_api_websocket_route("/ws", websocket_endpoint)


# 静态文件挂载
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 资源文件挂载(logo 等)
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


def _serve_html(file_path: Path) -> Response:
    """返回 HTML 文件,若已加密则解密。"""
    data = file_path.read_bytes()
    if is_encrypted(data):
        data = decrypt_data(data)
    return Response(content=data, media_type="text/html; charset=utf-8")


@app.get("/")
async def index():
    """返回 SPA 入口页面。"""
    return _serve_html(STATIC_DIR / "index.html")


@app.get("/login.html")
async def login_page(request: Request):
    """返回登录页面。已认证用户或可信IP直接跳转主页。"""
    # 已登录跳转
    token = request.cookies.get("uniclaw_token")
    if token and auth.verify_token(token):
        return RedirectResponse(url="/", status_code=302)
    # 可信 IP 直接跳转(无需登录)
    client_ip = request.client.host if request.client else ""
    if _is_trusted_ip(client_ip):
        return RedirectResponse(url="/", status_code=302)
    return _serve_html(STATIC_DIR / "login.html")
