"""
进程内 LLM 并发闸门。

用于把异步标签分类、同步评价、规则引擎和报告生成的 LLM 调用收敛到
同一个进程内上限，避免高并发 batch 下各阶段叠加打满本地 worker。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Iterator

from core.config import get_settings

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 0.05
_WAIT_LOG_SECONDS = 5.0


@dataclass(frozen=True)
class LLMConcurrencySnapshot:
    """LLM 并发闸门运行态快照。"""

    max_concurrency: int
    active: int
    waiting: int


class _GlobalLLMLimiter:
    """同步与异步调用共享的轻量并发闸门。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._max_concurrency = 1
        self._semaphore = threading.BoundedSemaphore(self._max_concurrency)
        self._active = 0
        self._waiting = 0
        self.configure(get_settings().app.llm_global_max_concurrency)

    def configure(self, max_concurrency: int) -> None:
        max_concurrency = max(1, int(max_concurrency))
        with self._lock:
            if max_concurrency == self._max_concurrency:
                return
            if self._active > 0 or self._waiting > 0:
                logger.warning(
                    "LLM 全局并发配置已变更但当前仍有调用运行，延后生效: current=%s requested=%s active=%s waiting=%s",
                    self._max_concurrency,
                    max_concurrency,
                    self._active,
                    self._waiting,
                )
                return
            self._max_concurrency = max_concurrency
            self._semaphore = threading.BoundedSemaphore(max_concurrency)
            logger.info("LLM 全局并发闸门已配置: max_concurrency=%s", max_concurrency)

    def snapshot(self) -> LLMConcurrencySnapshot:
        with self._lock:
            return LLMConcurrencySnapshot(
                max_concurrency=self._max_concurrency,
                active=self._active,
                waiting=self._waiting,
            )

    def _mark_waiting(self, delta: int) -> None:
        with self._lock:
            self._waiting = max(0, self._waiting + delta)

    def _mark_active(self, delta: int) -> None:
        with self._lock:
            self._active = max(0, self._active + delta)

    def _log_wait_if_needed(self, stage: str, started_at: float, last_logged_at: float) -> float:
        now = time.monotonic()
        if now - last_logged_at < _WAIT_LOG_SECONDS:
            return last_logged_at
        snapshot = self.snapshot()
        logger.warning(
            "LLM 全局并发等待中: stage=%s waited=%.1fs active=%s waiting=%s max=%s",
            stage,
            now - started_at,
            snapshot.active,
            snapshot.waiting,
            snapshot.max_concurrency,
        )
        return now

    def _acquire_sync(self, stage: str) -> None:
        started_at = time.monotonic()
        last_logged_at = started_at
        self._mark_waiting(1)
        try:
            while True:
                if self._semaphore.acquire(blocking=False):
                    self._mark_active(1)
                    return
                last_logged_at = self._log_wait_if_needed(stage, started_at, last_logged_at)
                time.sleep(_POLL_INTERVAL_SECONDS)
        finally:
            self._mark_waiting(-1)

    async def _acquire_async(self, stage: str) -> None:
        started_at = time.monotonic()
        last_logged_at = started_at
        self._mark_waiting(1)
        try:
            while True:
                if self._semaphore.acquire(blocking=False):
                    self._mark_active(1)
                    return
                last_logged_at = self._log_wait_if_needed(stage, started_at, last_logged_at)
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        finally:
            self._mark_waiting(-1)

    def release(self) -> None:
        self._mark_active(-1)
        self._semaphore.release()

    @contextmanager
    def sync_slot(self, stage: str) -> Iterator[None]:
        self.configure(get_settings().app.llm_global_max_concurrency)
        self._acquire_sync(stage)
        try:
            yield
        finally:
            self.release()

    @asynccontextmanager
    async def async_slot(self, stage: str) -> AsyncIterator[None]:
        self.configure(get_settings().app.llm_global_max_concurrency)
        await self._acquire_async(stage)
        try:
            yield
        finally:
            self.release()


_GLOBAL_LIMITER = _GlobalLLMLimiter()


def llm_sync_slot(stage: str) -> Iterator[None]:
    """获取同步 LLM 调用槽位。"""
    return _GLOBAL_LIMITER.sync_slot(stage)


def llm_async_slot(stage: str) -> AsyncIterator[None]:
    """获取异步 LLM 调用槽位。"""
    return _GLOBAL_LIMITER.async_slot(stage)


def get_llm_concurrency_snapshot() -> LLMConcurrencySnapshot:
    """返回当前 LLM 全局并发闸门状态。"""
    return _GLOBAL_LIMITER.snapshot()
