"""
活跃 WebSocket 会话注册表。

在单副本部署下，用于按认证 session_id 主动踢下线。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import DefaultDict

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_session_connections: DefaultDict[str, set[WebSocket]] = defaultdict(set)


async def register_session_websocket(session_id: str, websocket: WebSocket) -> None:
    """注册某个认证会话下的 WebSocket 连接。"""
    if not session_id:
        return
    async with _lock:
        _session_connections[session_id].add(websocket)


async def unregister_session_websocket(session_id: str, websocket: WebSocket) -> None:
    """注销某个认证会话下的 WebSocket 连接。"""
    if not session_id:
        return
    async with _lock:
        connections = _session_connections.get(session_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            _session_connections.pop(session_id, None)


async def close_session_websockets(
    session_ids: list[str],
    *,
    code: int = 1008,
    reason: str = "unauthorized",
) -> None:
    """主动关闭指定会话下的所有 WebSocket。"""
    if not session_ids:
        return

    async with _lock:
        targets: list[WebSocket] = []
        seen: set[int] = set()
        for session_id in session_ids:
            for websocket in _session_connections.pop(session_id, set()):
                identifier = id(websocket)
                if identifier in seen:
                    continue
                seen.add(identifier)
                targets.append(websocket)

    for websocket in targets:
        try:
            await websocket.close(code=code, reason=reason)
        except Exception as exc:
            logger.debug("主动关闭 WebSocket 失败: %s", exc)
