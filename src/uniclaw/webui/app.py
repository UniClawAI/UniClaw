"""FastAPI 应用实例。"""

from __future__ import annotations
import json
import asyncio
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

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "static"
ASSETS_DIR = Path(__file__).parent.parent / "assets"

# 认证白名单路径前缀(不需要登录即可访问）
_AUTH_WHITELIST = (
    "/login.html",
    "/api/auth/",
    "/static/",
    "/assets/",
    "/favicon.ico",
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
    except Exception:
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

    # 提取 token(header 或 cookie）
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get("uniclaw_token")

    # 验证 token
    if token and auth.verify_token(token):
        return await call_next(request)

    # 未登录：API 请求返回 401,页面请求重定向登录页
    if path.startswith("/api/") or path == "/ws":
        return JSONResponse(
            status_code=401,
            content={"detail": "未登录"},
        )
    return RedirectResponse(url="/login.html", status_code=302)


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
    """创建账号(仅首次无账号时可用）。"""
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
            httponly=True,
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
async def auth_login(req: _AuthRequest):
    """登录,返回 JWT。"""
    if not auth.user_exists():
        return JSONResponse(
            status_code=400,
            content={"detail": "账号不存在,请先创建"},
        )
    if not auth.verify_user(req.username, req.password):
        return JSONResponse(
            status_code=401,
            content={"detail": "用户名或密码错误"},
        )
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
    """获取当前用户信息(也可用于验证 token 有效性）。"""
    user = auth.get_user()
    if not user:
        return JSONResponse(status_code=404, content={"detail": "无用户"})
    return {"username": user.username, "created_at": user.created_at}


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

# 资源文件挂载（logo 等）
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.get("/")
async def index():
    """返回 SPA 入口页面。"""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/login.html")
async def login_page():
    """返回登录页面。"""
    return FileResponse(str(STATIC_DIR / "login.html"))
