"""
Web 认证中间件。
"""
from __future__ import annotations

from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .i18n.messages import ui_message
from .auth import get_authenticated_user_from_request, is_auth_enabled

PUBLIC_PATHS = {
    "/",
    "/ama/health",
    "/ama/ready",
    "/ama/api/auth/login",
    "/ama/api/auth/logout",
    "/ama/api/auth/me",
}
REDIRECT_PATHS = {
    "/ama/api-docs",
    "/ama/api-redoc",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """保护 Web API、OpenAPI 文档等后端入口。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not is_auth_enabled():
            return await call_next(request)

        if request.method == "OPTIONS" or _is_public_path(request.url.path):
            return await call_next(request)

        user = get_authenticated_user_from_request(request)
        if user is not None:
            request.state.auth_user = user
            return await call_next(request)

        return _build_unauthorized_response(request.url.path)


def _is_public_path(path: str) -> bool:
    """判断路径是否公开。"""
    if path in PUBLIC_PATHS:
        return True
    if path.startswith("/ama/api/auth/"):
        return True
    if not path.startswith("/ama"):
        return True
    if path.startswith("/ama/api/"):
        return False
    if path in REDIRECT_PATHS or path == "/ama/openapi.json":
        return False
    return True


def _build_unauthorized_response(path: str) -> Response:
    """根据路径类型返回 401 或重定向。"""
    if path in REDIRECT_PATHS:
        return RedirectResponse(url="/ama/login", status_code=302)
    if path.startswith("/ama/api/") or path == "/ama/openapi.json":
        return JSONResponse(
            status_code=401,
            content=ui_message("error.unauthorized", field="detail"),
        )
    return RedirectResponse(url="/ama/login", status_code=302)
