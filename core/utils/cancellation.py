"""
取消令牌工具。

为跨 asyncio / 线程池的协作式取消提供统一接口。
"""
from __future__ import annotations

import threading
from typing import Callable


class CancellationRequested(RuntimeError):
    """表示任务收到取消请求。"""


class CancellationToken:
    """线程安全的协作式取消令牌。"""

    def __init__(self, is_cancelled: Callable[[], bool] | None = None):
        self._event = threading.Event()
        self._is_cancelled = is_cancelled

    def cancel(self) -> None:
        """主动标记取消。"""
        self._event.set()

    def cancelled(self) -> bool:
        """返回当前是否已取消。"""
        if self._event.is_set():
            return True
        if self._is_cancelled is not None:
            try:
                if self._is_cancelled():
                    self._event.set()
                    return True
            except Exception:
                # 取消检查失败时，保守地保持未取消，避免误杀任务。
                return self._event.is_set()
        return False

    def raise_if_cancelled(self) -> None:
        """若已取消则抛出异常。"""
        if self.cancelled():
            raise CancellationRequested("任务已收到取消请求")
