"""统一执行运行时的并发安全回归测试。"""
import asyncio
import threading
import time

from core.parsers.mineru_parser import MinerUParser
from core.runtime import ExecutionLimits, ExecutionRuntime, ExecutionStage
from core.runtime import bind_execution_runtime, reset_execution_runtime
from core.utils.cancellation import CancellationToken
from core.utils.token_tracker import TokenUsageTracker, get_global_tracker, set_global_tracker


def _runtime(*, workers: int = 2, mineru: int = 2, blocking: int = 2) -> ExecutionRuntime:
    """创建测试运行时。"""
    return ExecutionRuntime(
        ExecutionLimits(
            appraisal_workers=workers,
            mineru_documents=mineru,
            labeler_requests=2,
            reporter_tasks=1,
            blocking_tasks=blocking,
        )
    )


def test_timed_out_retry_waits_for_previous_thread() -> None:
    """同一条目超时后，旧线程结束前不得启动下一次尝试。"""
    runtime = _runtime(workers=2)
    active = 0
    max_active = 0
    calls = 0
    lock = threading.Lock()

    def blocking_call() -> int:
        nonlocal active, max_active, calls
        with lock:
            calls += 1
            call_number = calls
            active += 1
            max_active = max(max_active, active)
        if call_number == 1:
            time.sleep(0.05)
        with lock:
            active -= 1
        return call_number

    async def run() -> None:
        try:
            await runtime.run_blocking(
                ExecutionStage.APPRAISER,
                blocking_call,
                timeout=0.01,
                serialization_key=("task", "item_1"),
            )
        except asyncio.TimeoutError:
            pass
        result = await runtime.run_blocking(
            ExecutionStage.APPRAISER,
            blocking_call,
            serialization_key=("task", "item_1"),
        )
        assert result == 2
        await runtime.aclose()

    asyncio.run(run())

    assert calls == 2
    assert max_active == 1


def test_stage_gate_bounds_submissions_before_thread_pool() -> None:
    """高并发调用只允许 worker 数量的任务进入线程池。"""
    runtime = _runtime(workers=2)
    release = threading.Event()

    def blocking_call() -> None:
        release.wait(timeout=2)

    async def run() -> None:
        tasks = [
            asyncio.create_task(
                runtime.run_blocking(ExecutionStage.APPRAISER, blocking_call)
            )
            for _ in range(100)
        ]
        await asyncio.sleep(0.05)
        snapshot = runtime.snapshot()
        assert snapshot["executor"]["active"] == 2
        assert snapshot["executor"]["pending"] == 0
        assert snapshot["stages"]["appraiser"]["waiting"] == 98
        release.set()
        await asyncio.gather(*tasks)
        await runtime.aclose()

    asyncio.run(run())


def test_blocking_pool_progresses_while_appraiser_pool_is_busy() -> None:
    """LLM 评价占满线程时，文件和预处理任务仍能继续。"""
    runtime = _runtime(workers=1, blocking=1)
    appraiser_started = threading.Event()
    release_appraiser = threading.Event()

    def long_appraiser() -> None:
        appraiser_started.set()
        release_appraiser.wait(timeout=2)

    async def run() -> None:
        appraiser = asyncio.create_task(
            runtime.run_blocking(ExecutionStage.APPRAISER, long_appraiser)
        )
        assert await asyncio.to_thread(appraiser_started.wait, 1)
        try:
            thread_name = await asyncio.wait_for(
                runtime.run_blocking(
                    ExecutionStage.BLOCKING,
                    lambda: threading.current_thread().name,
                ),
                timeout=0.5,
            )
            assert thread_name.startswith("batch_io_")
            snapshot = runtime.snapshot()
            assert snapshot["executors"]["appraiser"]["active"] == 1
            assert snapshot["executors"]["blocking"]["completed"] == 1
        finally:
            release_appraiser.set()
        await appraiser
        await runtime.aclose()

    asyncio.run(run())


def test_mineru_timeout_starts_after_document_slot() -> None:
    """等待 MinerU 文档槽位的时间不计入单文档解析超时。"""
    runtime = _runtime(mineru=1)
    parser = MinerUParser.__new__(MinerUParser)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def fake_parse(*_args, **_kwargs) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            first_started.set()
            await release_first.wait()
        return {"status": "success", "pages": 1}

    parser._parse_pdf_to_file = fake_parse

    async def run() -> None:
        runtime_token = bind_execution_runtime(runtime)
        try:
            first = asyncio.create_task(
                parser.parse_pdf_to_file("first.pdf", "first.md", timeout=1)
            )
            await first_started.wait()
            second = asyncio.create_task(
                parser.parse_pdf_to_file("second.pdf", "second.md", timeout=0.01)
            )
            await asyncio.sleep(0.05)
            assert second.done() is False
            release_first.set()
            assert (await first)["status"] == "success"
            assert (await second)["status"] == "success"
        finally:
            reset_execution_runtime(runtime_token)
            await runtime.aclose()

    asyncio.run(run())


def test_cancellation_token_keeps_first_reason() -> None:
    """并发取消只保留最先发生的根因。"""
    token = CancellationToken()

    token.cancel("lease_lost")
    token.cancel("stop_requested")

    assert token.cancelled() is True
    assert token.reason == "lease_lost"


def test_token_tracker_is_isolated_between_concurrent_tasks() -> None:
    """并行文档不会互相覆盖 Token 追踪器。"""
    first = TokenUsageTracker("first")
    second = TokenUsageTracker("second")

    async def record(tracker: TokenUsageTracker, prompt_tokens: int) -> None:
        set_global_tracker(tracker)
        await asyncio.sleep(0)
        current = get_global_tracker()
        assert current is tracker
        await asyncio.to_thread(
            current.add_openai_usage,
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": 1,
                "total_tokens": prompt_tokens + 1,
            },
        )

    async def run() -> None:
        await asyncio.gather(record(first, 10), record(second, 20))

    asyncio.run(run())

    assert first.prompt_tokens == 10
    assert second.prompt_tokens == 20
