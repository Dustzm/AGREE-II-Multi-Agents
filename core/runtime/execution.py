"""统一管理批处理阶段并发、阻塞线程与运行态统计。"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Hashable


class ExecutionStage(StrEnum):
    """需要统一限流的执行阶段。"""

    MINERU = "mineru"
    LABELER = "labeler"
    APPRAISER = "appraiser"
    REPORTER = "reporter"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class ExecutionLimits:
    """进程内各执行阶段的最大并发。"""

    appraisal_workers: int
    mineru_documents: int
    labeler_requests: int
    reporter_tasks: int
    blocking_tasks: int = 2

    @classmethod
    def from_settings(cls, settings: Any) -> "ExecutionLimits":
        """从项目配置构造并发上限。"""
        app = settings.app
        return cls(
            appraisal_workers=max(1, int(app.appraisal_pool_max_concurrency)),
            mineru_documents=max(1, int(getattr(app, "batch_mineru_max_concurrency", 2))),
            labeler_requests=max(1, int(app.labeler_max_concurrency)),
            reporter_tasks=max(1, int(getattr(app, "batch_reporter_max_concurrency", 2))),
            blocking_tasks=max(1, int(getattr(app, "batch_blocking_max_concurrency", 4))),
        )


class InstrumentedThreadPoolExecutor(ThreadPoolExecutor):
    """提供 active/pending 统计的共享阻塞线程池。"""

    def __init__(self, max_workers: int, thread_name_prefix: str = "batch_exec_"):
        super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
        self._stats_lock = threading.Lock()
        self._active_tasks = 0
        self._pending_tasks = 0
        self._submitted_tasks = 0
        self._completed_tasks = 0
        self._cancelled_tasks = 0

    def submit(self, fn, /, *args, **kwargs) -> Future:
        """提交任务并维护线程池统计。"""
        with self._stats_lock:
            self._pending_tasks += 1
            self._submitted_tasks += 1
        state = {"started": False, "pending_released": False}

        def release_pending_locked() -> None:
            if state["pending_released"]:
                return
            self._pending_tasks = max(0, self._pending_tasks - 1)
            state["pending_released"] = True

        def wrapped_fn(*wrapped_args, **wrapped_kwargs):
            with self._stats_lock:
                state["started"] = True
                release_pending_locked()
                self._active_tasks += 1
            try:
                return fn(*wrapped_args, **wrapped_kwargs)
            finally:
                with self._stats_lock:
                    self._active_tasks = max(0, self._active_tasks - 1)
                    self._completed_tasks += 1

        try:
            future = super().submit(wrapped_fn, *args, **kwargs)
        except Exception:
            with self._stats_lock:
                release_pending_locked()
            raise

        def mark_cancelled_if_pending(done_future: Future) -> None:
            if not done_future.cancelled():
                return
            with self._stats_lock:
                if not state["started"]:
                    release_pending_locked()
                    self._cancelled_tasks += 1

        future.add_done_callback(mark_cancelled_if_pending)
        return future

    def stats(self) -> dict[str, int]:
        """返回线程池当前统计。"""
        with self._stats_lock:
            return {
                "max_workers": self._max_workers,
                "active": self._active_tasks,
                "pending": self._pending_tasks,
                "submitted": self._submitted_tasks,
                "completed": self._completed_tasks,
                "cancelled": self._cancelled_tasks,
            }


@dataclass
class _KeyGate:
    """同一逻辑调用的串行闸门。"""

    lock: asyncio.Lock
    users: int = 0


class JobExecutionScope:
    """单个批量任务的文档并发范围。"""

    def __init__(self, concurrency: int):
        self._semaphore = asyncio.Semaphore(max(1, concurrency))

    @asynccontextmanager
    async def document_slot(self):
        """占用一个文档执行槽。"""
        async with self._semaphore:
            yield


class ExecutionRuntime:
    """一个进程共享的批处理执行资源管理器。"""

    def __init__(
        self,
        limits: ExecutionLimits,
        *,
        executor: ThreadPoolExecutor | None = None,
        owns_executor: bool = True,
    ):
        self.limits = limits
        self.appraiser_executor = executor or InstrumentedThreadPoolExecutor(
            limits.appraisal_workers,
            thread_name_prefix="llm_eval_",
        )
        self.blocking_executor = InstrumentedThreadPoolExecutor(
            limits.blocking_tasks,
            thread_name_prefix="batch_io_",
        )
        # 兼容现有显式注入 Appraiser 线程池的调用方。
        self.executor = self.appraiser_executor
        self._owns_executor = owns_executor if executor is not None else True
        self._stage_semaphores = {
            ExecutionStage.MINERU: asyncio.Semaphore(limits.mineru_documents),
            ExecutionStage.LABELER: asyncio.Semaphore(limits.labeler_requests),
            ExecutionStage.APPRAISER: asyncio.Semaphore(limits.appraisal_workers),
            ExecutionStage.REPORTER: asyncio.Semaphore(limits.reporter_tasks),
            ExecutionStage.BLOCKING: asyncio.Semaphore(limits.blocking_tasks),
        }
        self._stats_lock = threading.Lock()
        self._active = {stage: 0 for stage in ExecutionStage}
        self._waiting = {stage: 0 for stage in ExecutionStage}
        self._draining = 0
        self._key_gates: dict[Hashable, _KeyGate] = {}

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        executor: ThreadPoolExecutor | None = None,
        owns_executor: bool = True,
    ) -> "ExecutionRuntime":
        """从项目配置创建执行运行时。"""
        return cls(
            ExecutionLimits.from_settings(settings),
            executor=executor,
            owns_executor=owns_executor,
        )

    def open_job(self, concurrency: int) -> JobExecutionScope:
        """创建单个批量任务的文档并发范围。"""
        return JobExecutionScope(concurrency)

    async def _acquire_stage(self, stage: ExecutionStage) -> None:
        with self._stats_lock:
            self._waiting[stage] += 1
        try:
            await self._stage_semaphores[stage].acquire()
        finally:
            with self._stats_lock:
                self._waiting[stage] = max(0, self._waiting[stage] - 1)
        with self._stats_lock:
            self._active[stage] += 1

    def _release_stage(self, stage: ExecutionStage) -> None:
        with self._stats_lock:
            self._active[stage] = max(0, self._active[stage] - 1)
        self._stage_semaphores[stage].release()

    @asynccontextmanager
    async def stage_slot(self, stage: ExecutionStage):
        """占用并自动释放一个阶段槽。"""
        await self._acquire_stage(stage)
        try:
            yield
        finally:
            self._release_stage(stage)

    async def _acquire_key(self, key: Hashable | None) -> _KeyGate | None:
        if key is None:
            return None
        gate = self._key_gates.setdefault(key, _KeyGate(asyncio.Lock()))
        gate.users += 1
        try:
            await gate.lock.acquire()
            return gate
        except BaseException:
            gate.users -= 1
            if gate.users == 0 and self._key_gates.get(key) is gate:
                self._key_gates.pop(key, None)
            raise

    def _release_key(self, key: Hashable | None, gate: _KeyGate | None) -> None:
        if key is None or gate is None:
            return
        gate.lock.release()
        gate.users -= 1
        if gate.users == 0 and self._key_gates.get(key) is gate:
            self._key_gates.pop(key, None)

    async def run_blocking(
        self,
        stage: ExecutionStage,
        function: Callable[..., Any],
        *args: Any,
        timeout: float | None = None,
        serialization_key: Hashable | None = None,
        **kwargs: Any,
    ) -> Any:
        """在线程池执行阻塞调用；超时后仍占槽，避免旧调用与重试重叠。"""
        loop = asyncio.get_running_loop()
        key_gate = await self._acquire_key(serialization_key)
        try:
            await self._acquire_stage(stage)
        except BaseException:
            self._release_key(serialization_key, key_gate)
            raise

        context = contextvars.copy_context()
        call = functools.partial(function, *args, **kwargs)
        try:
            executor = (
                self.appraiser_executor
                if stage == ExecutionStage.APPRAISER
                else self.blocking_executor
            )
            future = loop.run_in_executor(executor, context.run, call)
        except BaseException:
            self._release_stage(stage)
            self._release_key(serialization_key, key_gate)
            raise

        released = False
        draining = False

        def release_resources(_future: asyncio.Future) -> None:
            nonlocal released
            if released:
                return
            released = True
            if draining:
                with self._stats_lock:
                    self._draining = max(0, self._draining - 1)
            self._release_stage(stage)
            self._release_key(serialization_key, key_gate)

        def schedule_release(done_future: asyncio.Future) -> None:
            try:
                loop.call_soon_threadsafe(release_resources, done_future)
            except RuntimeError:
                pass

        future.add_done_callback(schedule_release)
        try:
            if timeout is None:
                return await asyncio.shield(future)
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if not future.done():
                draining = True
                with self._stats_lock:
                    self._draining += 1
            raise

    def snapshot(self) -> dict[str, Any]:
        """返回运行时并发与线程池快照。"""
        with self._stats_lock:
            stages = {
                stage.value: {
                    "active": self._active[stage],
                    "waiting": self._waiting[stage],
                }
                for stage in ExecutionStage
            }
            draining = self._draining
        appraiser_stats = (
            self.appraiser_executor.stats()
            if hasattr(self.appraiser_executor, "stats")
            else {}
        )
        blocking_stats = self.blocking_executor.stats()
        return {
            "stages": stages,
            "draining": draining,
            "executor": appraiser_stats,
            "executors": {
                "appraiser": appraiser_stats,
                "blocking": blocking_stats,
            },
        }

    def shutdown(self, *, wait: bool = True) -> None:
        """关闭运行时拥有的线程池。"""
        self.blocking_executor.shutdown(wait=wait, cancel_futures=True)
        if self._owns_executor:
            self.appraiser_executor.shutdown(wait=wait, cancel_futures=True)

    async def aclose(self) -> None:
        """异步等待线程池安全退出。"""
        await asyncio.to_thread(self.shutdown, wait=True)


_CURRENT_RUNTIME: contextvars.ContextVar[ExecutionRuntime | None] = contextvars.ContextVar(
    "execution_runtime",
    default=None,
)


def get_execution_runtime() -> ExecutionRuntime | None:
    """获取当前协程绑定的执行运行时。"""
    return _CURRENT_RUNTIME.get()


def bind_execution_runtime(runtime: ExecutionRuntime) -> contextvars.Token:
    """把执行运行时绑定到当前协程上下文。"""
    return _CURRENT_RUNTIME.set(runtime)


def reset_execution_runtime(token: contextvars.Token) -> None:
    """恢复绑定前的执行运行时。"""
    _CURRENT_RUNTIME.reset(token)


@asynccontextmanager
async def execution_stage(stage: ExecutionStage):
    """占用当前运行时的阶段槽；非托管入口保持兼容。"""
    runtime = get_execution_runtime()
    if runtime is None:
        yield
        return
    async with runtime.stage_slot(stage):
        yield


async def run_blocking(
    stage: ExecutionStage,
    function: Callable[..., Any],
    *args: Any,
    timeout: float | None = None,
    serialization_key: Hashable | None = None,
    **kwargs: Any,
) -> Any:
    """通过当前运行时执行阻塞调用；无运行时时使用 asyncio 默认线程池。"""
    runtime = get_execution_runtime()
    if runtime is not None:
        return await runtime.run_blocking(
            stage,
            function,
            *args,
            timeout=timeout,
            serialization_key=serialization_key,
            **kwargs,
        )
    awaitable = asyncio.to_thread(function, *args, **kwargs)
    if timeout is None:
        return await awaitable
    return await asyncio.wait_for(awaitable, timeout=timeout)
