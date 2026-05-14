"""
权限控制依赖项。

提供 FastAPI 依赖注入函数，用于在路由层校验用户角色。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from .auth import AuthenticatedUser


def require_role(*roles: str):
    """
    生成角色校验 FastAPI 依赖。

    用法::

        @router.put("/resource")
        async def update_resource(user=Depends(require_admin)):
            ...
    """

    async def _checker(request: Request) -> AuthenticatedUser:
        user: AuthenticatedUser | None = getattr(request.state, "auth_user", None)
        if user is None:
            raise HTTPException(status_code=401, detail="未登录或登录已失效")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="权限不足，需要管理员权限")
        return user

    return _checker


# 常用预置依赖
require_admin = require_role("admin")
require_logged_in = require_role("admin", "user")
