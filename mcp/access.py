"""AMA MCP HTTP 入口共用的访问控制。"""
from __future__ import annotations

import asyncio
import hmac
import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config import MCPSettings, get_mcp_settings
from errors import MCPErrorCode, error_payload

JSON_HEADERS = [(b"content-type", b"application/json; charset=utf-8")]


class RequestTooLarge(RuntimeError):
    """请求体超过代码层限制。"""


class MCPAccessMiddleware:
    """在读取请求体前执行开关、Bearer、限流和体积校验。"""

    def __init__(self, app: ASGIApp, settings: MCPSettings | None = None):
        self.app = app
        self.settings = settings or get_mcp_settings()
        self._request_slots = asyncio.Semaphore(self.settings.max_inflight_requests)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self.settings.enabled:
            await self._send_error(send, 503, MCPErrorCode.MCP_DISABLED)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {self.settings.api_key}"
        if not hmac.compare_digest(authorization, expected):
            await self._send_error(send, 401, MCPErrorCode.UNAUTHORIZED)
            return

        content_length = headers.get(b"content-length")
        if content_length:
            try:
                parsed_length = int(content_length)
                if parsed_length < 0:
                    raise ValueError
                if parsed_length > self.settings.max_request_bytes:
                    await self._send_error(send, 413, MCPErrorCode.REQUEST_TOO_LARGE)
                    return
            except ValueError:
                await self._send_error(send, 400, MCPErrorCode.INVALID_CONTENT_LENGTH)
                return

        if self._request_slots.locked():
            await self._send_error(
                send,
                429,
                MCPErrorCode.TOO_MANY_REQUESTS,
                extra_headers=[(b"retry-after", b"2")],
            )
            return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.settings.max_request_bytes:
                    raise RequestTooLarge
            return message

        await self._request_slots.acquire()
        try:
            try:
                await self.app(scope, limited_receive, send)
            except RequestTooLarge:
                await self._send_error(send, 413, MCPErrorCode.REQUEST_TOO_LARGE)
        finally:
            self._request_slots.release()

    @staticmethod
    async def _send_error(
        send: Send,
        status: int,
        code: MCPErrorCode,
        *,
        extra_headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        """发送不伪装成成功响应的 HTTP JSON 错误。"""
        payload = json.dumps({"error": error_payload(code)}, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [*JSON_HEADERS, *(extra_headers or [])],
            }
        )
        await send({"type": "http.response.body", "body": payload})
