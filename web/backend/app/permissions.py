"""
权限控制依赖项。

提供 FastAPI 依赖注入函数，用于在路由层校验用户角色。
"""
from __future__ import annotations

from fastapi import Request

from .auth import AuthenticatedUser, get_auth_disabled_user, is_auth_enabled

from .i18n.messages import LocalizedHTTPException


def require_role(*roles: str):
    """
    生成角色校验 FastAPI 依赖。

    用法::

        @router.put("/resource")
        async def update_resource(user=Depends(require_admin)):
            ...
    """

    async def _checker(request: Request) -> AuthenticatedUser:
        if not is_auth_enabled():
            return get_auth_disabled_user()

        user: AuthenticatedUser | None = getattr(request.state, "auth_user", None)
        if user is None:
            raise LocalizedHTTPException(401, "error.unauthorized")
        if user.role not in roles:
            raise LocalizedHTTPException(403, "error.admin_required")
        return user

    return _checker


# 常用预置依赖
require_admin = require_role("admin")
require_logged_in = require_role("admin", "user")
