"""WebUI 认证模块：单账号登录 + JWT。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import jwt

from uniclaw.utils.logger import get_logger
from uniclaw.context import get_app_dir, Scope

# ── 常量 ──────────────────────────────────────────────────────────────────

_DB_DIR = get_app_dir(Scope.USER) / "auth"
_DB_PATH = _DB_DIR / "auth.db"
_JWT_SECRET: str | None = None  # 首次使用时从环境或随机生成
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS = 24
_TOKEN_HEADER = "Authorization"
_TOKEN_PREFIX = "Bearer "

logger = get_logger("webui.auth", Path.cwd())

# ── 数据结构 ──────────────────────────────────────────────────────────────


@dataclass
class User:
    id: int
    username: str
    created_at: str


# ── 数据库 ────────────────────────────────────────────────────────────────


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接(自动建表）。"""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn


def user_exists() -> bool:
    """是否已有账号。"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) FROM user").fetchone()
        return row[0] > 0
    finally:
        conn.close()


def create_user(username: str, password: str) -> User:
    """创建账号(仅允许一个）。"""
    if user_exists():
        raise ValueError("账号已存在")
    salt = os.urandom(32).hex()
    pw_hash = _hash_password(password, salt)
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO user (id, username, password_hash, salt) VALUES (1, ?, ?, ?)",
            (username, pw_hash, salt),
        )
        conn.commit()
        logger.info(f"WebUI 账号已创建: {username}")
        return User(id=1, username=username, created_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    finally:
        conn.close()


def verify_user(username: str, password: str) -> bool:
    """验证凭据。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT username, password_hash, salt FROM user LIMIT 1"
        ).fetchone()
        if not row:
            return False
        db_user, db_hash, salt = row
        if db_user != username:
            return False
        return _hash_password(password, salt) == db_hash
    finally:
        conn.close()


def get_user() -> Optional[User]:
    """获取当前用户信息。"""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, created_at FROM user LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return User(id=row[0], username=row[1], created_at=row[2])
    finally:
        conn.close()


def change_password(new_password: str) -> None:
    """修改密码。"""
    salt = os.urandom(32).hex()
    pw_hash = _hash_password(new_password, salt)
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE user SET password_hash = ?, salt = ? WHERE id = (SELECT id FROM user LIMIT 1)",
            (pw_hash, salt),
        )
        conn.commit()
        logger.info("WebUI 密码已修改")
    finally:
        conn.close()


# ── 密码哈希 ──────────────────────────────────────────────────────────────


def _hash_password(password: str, salt: str) -> str:
    """PBKDF2-SHA256 哈希密码。"""
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations=100_000,
    ).hex()


# ── JWT ───────────────────────────────────────────────────────────────────


def _get_jwt_secret() -> str:
    """获取 JWT 密钥(首次随机生成,持久化到文件）。"""
    global _JWT_SECRET
    if _JWT_SECRET:
        return _JWT_SECRET

    secret_file = _DB_DIR / ".jwt_secret"
    if secret_file.exists():
        _JWT_SECRET = secret_file.read_text(encoding="utf-8").strip()
    else:
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        _JWT_SECRET = os.urandom(32).hex()
        secret_file.write_text(_JWT_SECRET, encoding="utf-8")
    return _JWT_SECRET


def create_token(username: str) -> str:
    """签发 JWT。"""
    payload = {
        "sub": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + _JWT_EXPIRE_HOURS * 3600,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=_JWT_ALGORITHM)


def verify_token(token: str) -> Optional[str]:
    """验证 JWT,返回 username 或 None。"""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[_JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── 导出符号 ──────────────────────────────────────────────────────────────

__all__ = [
    "User",
    "user_exists",
    "create_user",
    "verify_user",
    "get_user",
    "change_password",
    "create_token",
    "verify_token",
    "_DB_PATH",
    "_JWT_ALGORITHM",
    "_JWT_EXPIRE_HOURS",
    "_TOKEN_HEADER",
    "_TOKEN_PREFIX",
]
