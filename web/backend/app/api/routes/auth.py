"""
认证 API 路由。
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from ...auth import (
    authenticate_user,
    clear_auth_cookie,
    create_replacement_session,
    create_session_token,
    get_auth_disabled_user,
    get_authenticated_user_from_request,
    get_session_id_from_request,
    is_auth_enabled,
    revoke_session,
    set_auth_cookie,
)
from ...session_registry import close_session_websockets

from ...i18n.messages import LocalizedHTTPException

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthUserResponse(BaseModel):
    """前端可见的用户摘要。"""

    username: str
    display_name: str | None = None
    role: str = "user"


class AuthStateResponse(BaseModel):
    """认证状态响应。"""

    authenticated: bool
    user: AuthUserResponse | None = None


class LoginRequest(BaseModel):
    """登录请求。"""

    username: str
    password: str


def _auth_state_for_user(user) -> AuthStateResponse:
    """构造认证状态响应。"""
    return AuthStateResponse(
        authenticated=True,
        user=AuthUserResponse(
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        ),
    )


@router.get("/me", response_model=AuthStateResponse)
async def get_current_auth_state(request: Request) -> AuthStateResponse:
    """获取当前登录状态。"""
    if not is_auth_enabled():
        return _auth_state_for_user(get_auth_disabled_user())

    user = get_authenticated_user_from_request(request)
    if user is None:
        return AuthStateResponse(authenticated=False, user=None)

    return _auth_state_for_user(user)


@router.post("/login", response_model=AuthStateResponse)
async def login(request: Request, response: Response, payload: LoginRequest) -> AuthStateResponse:
    """校验账号密码并建立登录会话。"""
    if not is_auth_enabled():
        clear_auth_cookie(response, request)
        return _auth_state_for_user(get_auth_disabled_user())

    username = payload.username.strip()
    if not username or not payload.password:
        raise LocalizedHTTPException(400, "error.credentials_required")

    user = authenticate_user(username, payload.password)
    if user is None:
        raise LocalizedHTTPException(401, "error.invalid_credentials")

    session_id, revoked_session_ids = create_replacement_session(user, request=request)
    token = create_session_token(user, session_id=session_id)
    set_auth_cookie(response, token, request)
    await close_session_websockets(revoked_session_ids)
    return _auth_state_for_user(user)


@router.post("/logout", response_model=AuthStateResponse)
async def logout(request: Request, response: Response) -> AuthStateResponse:
    """退出登录并清理会话 Cookie。"""
    if not is_auth_enabled():
        clear_auth_cookie(response, request)
        return _auth_state_for_user(get_auth_disabled_user())

    session_id = get_session_id_from_request(request)
    if session_id:
        revoke_session(session_id)
        await close_session_websockets([session_id])
    clear_auth_cookie(response, request)
    return AuthStateResponse(authenticated=False, user=None)
