"""
取消令牌工具。

为跨 asyncio / 线程池的协作式取消提供统一接口。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator


class CancellationRequested(RuntimeError):
    """表示任务收到取消请求。"""


class CancellationToken:
    """线程安全的协作式取消令牌。"""

    def __init__(
        self,
        is_cancelled: Callable[[], bool] | None = None,
        *,
        callback_reason: str = "external",
    ):
        self._event = threading.Event()
        self._is_cancelled = is_cancelled
        self._callback_reason = callback_reason
        self._lock = threading.Lock()
        self._reason: str | None = None

    @classmethod
    def linked_to(cls, parent: "CancellationToken | None") -> "CancellationToken":
        """子令牌：父令牌取消时随之取消，自身取消不影响父令牌。"""
        return cls(parent.cancelled if parent is not None else None, callback_reason="parent")

    def cancel(self, reason: str = "requested") -> None:
        """主动标记取消。"""
        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()

    @property
    def reason(self) -> str | None:
        """返回首次触发取消的原因。"""
        with self._lock:
            return self._reason

    def cancelled(self) -> bool:
        """返回当前是否已取消。"""
        if self._event.is_set():
            return True
        if self._is_cancelled is not None:
            try:
                if self._is_cancelled():
                    self.cancel(self._callback_reason)
                    return True
            except Exception:
                # 取消检查失败时，保守地保持未取消，避免误杀任务。
                return self._event.is_set()
        return False

    def raise_if_cancelled(self) -> None:
        """若已取消则抛出异常。"""
        if self.cancelled():
            raise CancellationRequested("任务已收到取消请求")


_ACTIVE_TOKEN: ContextVar[CancellationToken | None] = ContextVar("active_cancellation_token", default=None)


@contextmanager
def bind_cancellation(token: CancellationToken | None) -> Iterator[None]:
    """把令牌绑定到当前上下文，让其中的每次模型调用都成为取消检查点。"""
    reset = _ACTIVE_TOKEN.set(token)
    try:
        yield
    finally:
        _ACTIVE_TOKEN.reset(reset)


def raise_if_active_cancelled() -> None:
    """当前上下文绑定的令牌已取消时抛出 CancellationRequested。"""
    token = _ACTIVE_TOKEN.get()
    if token is not None:
        token.raise_if_cancelled()
