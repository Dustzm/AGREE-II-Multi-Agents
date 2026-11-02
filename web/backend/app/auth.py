"""
Web 登录认证支持。

提供数据库用户加载、密码校验、会话签名与请求鉴权能力。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request, WebSocket

from core.config import Settings, get_settings

AUTH_COOKIE_NAME = "ama_auth_session"
PROJECT_ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthenticatedUser:
    """已认证用户摘要。"""

    username: str
    display_name: str | None = None
    role: str = "user"
    session_id: str | None = None


AUTH_DISABLED_USER = AuthenticatedUser(
    username="local",
    display_name="免登录用户",
    role="admin",
)


def _optional_text(value: Any) -> str | None:
    """将可选文本字段规范化。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def validate_auth_configuration(settings: Settings | None = None) -> None:
    """验证认证配置，启动时调用。"""
    settings = settings or get_settings()
    if not settings.auth.enabled:
        return

    # 初始化数据库（建表 + 迁移 + 种子）
    from .database import init_db
    init_db()
    logger.info("数据库初始化完成")


def authenticate_user(username: str, password: str) -> AuthenticatedUser | None:
    """校验用户名和密码（从 SQLite 数据库）。"""
    from .database import UserDB

    record = UserDB.get_by_username_with_password(username)
    if not record or not record.get("enabled"):
        return None

    if not UserDB.verify_password(password, record["password"]):
        return None

    return AuthenticatedUser(
        username=record["username"],
        display_name=record.get("display_name") or None,
        role=record.get("role", "user"),
    )


def create_session_token(
    user: AuthenticatedUser,
    *,
    session_id: str,
    settings: Settings | None = None,
) -> str:
    """生成签名会话令牌。"""
    settings = settings or get_settings()
    now = int(time.time())
    payload = {
        "sub": user.username,
        "role": user.role,
        "sid": session_id,
        "iat": now,
        "exp": now + settings.auth.session_ttl_hours * 3600,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_encoded = base64.urlsafe_b64encode(payload_json).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.auth.secret.encode("utf-8"),
        payload_encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_encoded}.{signature}"


def create_replacement_session(
    user: AuthenticatedUser,
    *,
    request: Request | None = None,
    settings: Settings | None = None,
) -> tuple[str, list[str]]:
    """创建新的唯一活跃会话，并撤销该用户旧会话。"""
    settings = settings or get_settings()
    from .database import AuthSessionDB

    session, revoked_session_ids = AuthSessionDB.replace_for_user(
        user.username,
        ttl_seconds=settings.auth.session_ttl_hours * 3600,
        created_ip=_extract_client_ip(request),
        user_agent=_extract_user_agent(request),
    )
    return str(session["session_id"]), revoked_session_ids


def revoke_session(session_id: str) -> bool:
    """撤销指定会话。"""
    from .database import AuthSessionDB

    return AuthSessionDB.revoke(session_id)


def revoke_user_sessions(username: str) -> list[str]:
    """撤销某个用户的所有活跃会话。"""
    from .database import AuthSessionDB

    return AuthSessionDB.revoke_all_for_user(username)


def parse_session_token(
    token: str,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """解析并验签会话令牌。"""
    settings = settings or get_settings()
    try:
        payload_encoded, signature = token.split(".", 1)
    except ValueError:
        return None

    expected_signature = hmac.new(
        settings.auth.secret.encode("utf-8"),
        payload_encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None

    try:
        payload_bytes = base64.urlsafe_b64decode(_pad_base64(payload_encoded))
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def get_authenticated_user_from_token(
    token: str,
    settings: Settings | None = None,
) -> AuthenticatedUser | None:
    """解析并校验会话令牌。"""
    settings = settings or get_settings()
    payload = parse_session_token(token, settings)
    if payload is None:
        return None

    exp = payload.get("exp")
    username = str(payload.get("sub", "")).strip()
    session_id = str(payload.get("sid", "")).strip()
    now = int(time.time())
    if not username or not session_id or not isinstance(exp, int) or exp <= now:
        return None

    # 从数据库确认用户与会话仍然有效
    from .database import AuthSessionDB, UserDB

    record = UserDB.get_by_username(username)
    if not record or not record.get("enabled"):
        return None

    session = AuthSessionDB.get_active(session_id, username=username, now=now)
    if not session:
        return None
    AuthSessionDB.touch(session_id)

    return AuthenticatedUser(
        username=record["username"],
        display_name=record.get("display_name") or None,
        role=record.get("role", "user"),
        session_id=session_id,
    )


def get_authenticated_user_from_request(request: Request) -> AuthenticatedUser | None:
    """从 HTTP 请求中解析当前用户。"""
    settings = get_settings()
    if not settings.auth.enabled:
        return None
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    return get_authenticated_user_from_token(token, settings)


def get_authenticated_user_from_websocket(websocket: WebSocket) -> AuthenticatedUser | None:
    """从 WebSocket 握手请求中解析当前用户。"""
    settings = get_settings()
    if not settings.auth.enabled:
        return None
    token = websocket.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    return get_authenticated_user_from_token(token, settings)


def get_auth_disabled_user() -> AuthenticatedUser:
    """返回认证关闭时使用的内置用户。"""
    return AUTH_DISABLED_USER


def get_session_id_from_request(request: Request) -> str | None:
    """从 HTTP 请求 Cookie 中解析 session_id。"""
    settings = get_settings()
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    payload = parse_session_token(token, settings)
    if not payload:
        return None
    session_id = _optional_text(payload.get("sid"))
    return session_id


def is_auth_enabled() -> bool:
    """返回是否启用认证。"""
    return get_settings().auth.enabled


def is_secure_request(request: Request) -> bool:
    """判断当前请求是否应使用 Secure Cookie。"""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def set_auth_cookie(response: Any, token: str, request: Request) -> None:
    """写入认证 Cookie。"""
    settings = get_settings()
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=settings.auth.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=is_secure_request(request),
        path="/ama",
    )


def clear_auth_cookie(response: Any, request: Request) -> None:
    """清除认证 Cookie。"""
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=is_secure_request(request),
        path="/ama",
    )


def _pad_base64(value: str) -> str:
    """恢复 base64 padding。"""
    return value + "=" * (-len(value) % 4)


def _extract_client_ip(request: Request | None) -> str:
    """提取客户端 IP。"""
    if request is None:
        return ""
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def _extract_user_agent(request: Request | None) -> str:
    """提取请求 UA。"""
    if request is None:
        return ""
    return request.headers.get("user-agent", "").strip()
