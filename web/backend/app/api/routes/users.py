"""
用户管理 API 路由。

仅管理员可操作（列表、创建、更新、删除），
所有已登录用户可修改自己的密码。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...auth import (
    AuthenticatedUser,
    create_replacement_session,
    create_session_token,
    revoke_user_sessions,
    set_auth_cookie,
)
from ...database import UserDB
from ...permissions import require_admin, require_logged_in
from ...session_registry import close_session_websockets

from ...i18n.messages import ui_message, LocalizedHTTPException, http_error

router = APIRouter(prefix="/users", tags=["用户管理"])


# ---------- 请求/响应模型 ----------

class UserResponse(BaseModel):
    """用户信息响应（不含密码）。"""

    id: int
    username: str
    display_name: str
    role: str
    enabled: bool
    created_at: str
    updated_at: str


class UserCreateRequest(BaseModel):
    """创建用户请求。"""

    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=4, max_length=128)
    display_name: str = Field(default="", max_length=64)
    role: str = Field(default="user", pattern=r"^(admin|user)$")


class UserUpdateRequest(BaseModel):
    """更新用户请求（所有字段可选）。"""

    password: str | None = Field(default=None, min_length=4, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)
    role: str | None = Field(default=None, pattern=r"^(admin|user)$")
    enabled: bool | None = None


class ChangePasswordRequest(BaseModel):
    """修改自己密码的请求。"""

    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=4, max_length=128)


# ---------- 路由 ----------

@router.get("", response_model=list[UserResponse])
async def list_users(_user: AuthenticatedUser = Depends(require_admin)):
    """获取所有用户列表（仅管理员）。"""
    return UserDB.get_all()


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreateRequest,
    _user: AuthenticatedUser = Depends(require_admin),
):
    """创建新用户（仅管理员）。"""
    try:
        user = UserDB.create(
            username=payload.username.strip(),
            password=payload.password,
            display_name=payload.display_name.strip(),
            role=payload.role,
        )
        return user
    except ValueError as exc:
        raise http_error(409, exc)


@router.put("/{username}", response_model=UserResponse)
async def update_user(
    username: str,
    payload: UserUpdateRequest,
    _user: AuthenticatedUser = Depends(require_admin),
):
    """更新用户信息（仅管理员）。"""
    # 不能修改自己的角色或禁用自己
    if username == _user.username:
        if payload.role is not None and payload.role != _user.role:
            raise LocalizedHTTPException(400, "error.self_role")
        if payload.enabled is not None and not payload.enabled:
            raise LocalizedHTTPException(400, "error.self_disable")

    fields = {}
    if payload.password is not None:
        fields["password"] = payload.password
    if payload.display_name is not None:
        fields["display_name"] = payload.display_name.strip()
    if payload.role is not None:
        fields["role"] = payload.role
    if payload.enabled is not None:
        fields["enabled"] = payload.enabled

    result = UserDB.update(username, **fields)
    if result is None:
        raise LocalizedHTTPException(404, "error.user_not_found")

    if payload.enabled is False or payload.password is not None:
        revoked_session_ids = revoke_user_sessions(username)
        await close_session_websockets(revoked_session_ids)
    return result


@router.delete("/{username}")
async def delete_user(
    username: str,
    _user: AuthenticatedUser = Depends(require_admin),
):
    """删除用户（仅管理员，不能删自己）。"""
    if username == _user.username:
        raise LocalizedHTTPException(400, "error.self_delete")

    revoked_session_ids = revoke_user_sessions(username)
    await close_session_websockets(revoked_session_ids)
    success = UserDB.delete(username)
    if not success:
        raise LocalizedHTTPException(404, "error.user_not_found")
    return ui_message("status.deleted", field="detail", filename=username)


@router.put("/me/password")
async def change_my_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    _user: AuthenticatedUser = Depends(require_logged_in),
):
    """修改自己的密码（任何已登录用户）。"""
    record = UserDB.get_by_username_with_password(_user.username)
    if not record:
        raise LocalizedHTTPException(404, "error.user_not_found")

    if not UserDB.verify_password(payload.old_password, record["password"]):
        raise LocalizedHTTPException(400, "error.old_password")

    UserDB.update(_user.username, password=payload.new_password)
    session_id, revoked_session_ids = create_replacement_session(_user, request=request)
    token = create_session_token(_user, session_id=session_id)
    set_auth_cookie(response, token, request)
    await close_session_websockets(revoked_session_ids)
    return ui_message("status.password_changed", field="detail")
