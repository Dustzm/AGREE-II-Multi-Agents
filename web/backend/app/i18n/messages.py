"""结构化用户消息、请求级语言及诊断脱敏。"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from fastapi import HTTPException

from core.utils.diagnostics import sanitize_diagnostic

from . import get_language, request_language, t


def descriptor(key: str, **params: Any) -> dict:
    """生成与语言无关的消息；参数只能包含可序列化的业务值。"""
    return {"key": key, "params": params}


def ui_message(key: str, lang: str | None = None, *, field: str = "message", diagnostic: Any = None, **params: Any) -> dict:
    """同时返回兼容文字和可供前端重新翻译的描述。"""
    lang = get_language(lang) if lang else request_language.get()
    if "error" in params:
        error = params.pop("error")
        diagnostic = diagnostic or error
    params = {name: sanitize_diagnostic(value) if isinstance(value, str) else value for name, value in params.items()}
    result = {field: t(key, lang, **params), "i18n": descriptor(key, **params)}
    if diagnostic is not None:
        result["diagnostic"] = sanitize_diagnostic(diagnostic)
    return result


class LocalizedHTTPException(HTTPException):
    """HTTP 状态码保持不变，错误文字由请求语言生成。"""

    def __init__(self, status_code: int, key: str, *, diagnostic: Any = None, **params: Any):
        self.message_data = ui_message(key, field="detail", diagnostic=diagnostic, **params)
        super().__init__(status_code=status_code, detail=self.message_data["detail"])


class LocalizedValueError(ValueError):
    """携带业务标识的服务错误，兼容现有 ValueError 捕获路径。"""

    def __init__(self, key: str, **params: Any):
        self.i18n = descriptor(key, **params)
        super().__init__(t(key, "en", **params))


class LocalizedRuntimeError(RuntimeError):
    """兼容任务服务原有 RuntimeError 契约的业务异常。"""

    def __init__(self, key: str, **params: Any):
        self.i18n = descriptor(key, **params)
        super().__init__(t(key, "en", **params))


def http_error(status: int, error: Exception) -> LocalizedHTTPException:
    """服务错误优先保留语义；未知错误使用友好提示并保留诊断。"""
    data = getattr(error, "i18n", None)
    if data:
        return LocalizedHTTPException(status, data["key"], **data["params"])
    return LocalizedHTTPException(status, "error.not_found" if status == 404 else "error.invalid_request" if status < 500 else "error.unexpected", diagnostic=error)


class LanguageMiddleware:
    """ASGI 请求级语言上下文，避免并发请求之间串语言。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        header = dict(scope.get("headers", [])).get(b"accept-language", b"").decode("latin-1")
        if scope["type"] == "websocket":
            # 浏览器 WebSocket 不支持自定义请求头，使用同一语言代码的查询参数。
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            header = query.get("lang", [header])[0]
        token = request_language.set(get_language(header))
        try:
            await self.app(scope, receive, send)
        finally:
            request_language.reset(token)
