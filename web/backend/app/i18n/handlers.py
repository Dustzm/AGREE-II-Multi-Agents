"""HTTP 和参数校验错误的统一国际化响应。"""
import logging
import traceback

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from . import get_language
from .messages import LanguageMiddleware, LocalizedHTTPException, descriptor, sanitize_diagnostic, ui_message

logger = logging.getLogger(__name__)

VALIDATION_KEYS = {
    "missing": ("validation.required", None),
    "string_too_short": ("validation.short", "min_length"),
    "too_short": ("validation.short", "min_length"),
    "string_too_long": ("validation.long", "max_length"),
    "too_long": ("validation.long", "max_length"),
    "greater_than": ("validation.greater", "gt"),
    "greater_than_equal": ("validation.greater_equal", "ge"),
    "less_than": ("validation.less", "lt"),
    "less_than_equal": ("validation.less_equal", "le"),
}


def install_i18n(app):
    """统一注册错误响应，同时让认证中间件使用同一请求语言。"""
    app.add_middleware(LanguageMiddleware)

    @app.exception_handler(HTTPException)
    async def handle_http_error(_request, exc):
        if isinstance(exc, LocalizedHTTPException):
            body = exc.message_data
        else:
            key = {401: "error.unauthorized", 403: "error.forbidden", 404: "error.not_found"}.get(exc.status_code, "error.invalid_request" if exc.status_code < 500 else "error.unexpected")
            body = ui_message(key, field="detail", diagnostic=exc.detail)
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request, exc):
        details = []
        for error in exc.errors():
            key, context_key = VALIDATION_KEYS.get(error["type"], ("validation.invalid", None))
            params = {"limit": error.get("ctx", {}).get(context_key)} if context_key else {}
            message = ui_message(key, **params)
            # 不返回 input，避免把密码、令牌等请求值复制进错误响应。
            details.append({"type": error["type"], "loc": error["loc"], "msg": message["message"], "i18n": descriptor(key, **params)})
        body = ui_message("error.validation")
        body["detail"] = details
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request, exc):
        logger.error("Unhandled API error\n%s", sanitize_diagnostic("".join(traceback.format_exception(exc)), limit=None))
        return JSONResponse(status_code=500, content=ui_message("error.unexpected", get_language(request.headers.get("accept-language")), field="detail", diagnostic=exc))
