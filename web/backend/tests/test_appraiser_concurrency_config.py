"""Appraiser 并发配置测试。"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import mineru_vl_utils.mineru_client as mineru_client_module
from PIL import Image

from core.agents.tools import appraiser_batch
from core.agents import appraiser
from core.llm import gateway as gateway_module
from core.llm import recovery as recovery_module
from core.config import refresh_settings
from core.parsers import mineru_parser
from core.utils.cancellation import CancellationToken
from core.utils.llm_limiter import get_llm_concurrency_snapshot, llm_sync_slot
from mineru.backend.vlm import vlm_analyze
from mineru_vl_utils.mineru_client import MinerUClient
from mineru_vl_utils.vlm_client.http_client import HttpVlmClient


def _success_result() -> dict:
    """构造最小成功评价结果。"""
    return {
        "status": "success",
        "chain_of_thought": {
            "step1_locate": "",
            "step2_evidence": [],
            "step3_reasoning": "",
            "score": 1,
        },
        "evidence_sources": [],
    }


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeCompletionResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]
        self.model = "fake-model"
        self.usage = None


@pytest.fixture(autouse=True)
def reset_runtime_state(monkeypatch):
    """每个测试前后重置配置缓存。"""
    settings = refresh_settings()
    monkeypatch.setattr(appraiser, "current_llm_settings", lambda: settings.llm.model_copy(update={"context_window": 131072}))
    yield
    refresh_settings()


def test_appraisal_pool_concurrency_defaults_to_ten(monkeypatch: pytest.MonkeyPatch):
    """未显式覆盖时应至少保留字段可读且不影响单指南并发设置。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "7")
    monkeypatch.delenv("APPRAISAL_POOL_MAX_CONCURRENCY", raising=False)

    settings = refresh_settings()

    assert settings.app.appraisal_max_concurrency == 7
    assert settings.app.appraisal_pool_max_concurrency >= 1


def test_appraisal_pool_concurrency_reads_explicit_value(monkeypatch: pytest.MonkeyPatch):
    """显式配置时应分别读取单指南并发和全局池并发。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "12")

    settings = refresh_settings()

    assert settings.app.appraisal_max_concurrency == 4
    assert settings.app.appraisal_pool_max_concurrency == 12


def test_llm_global_concurrency_reads_explicit_value(monkeypatch: pytest.MonkeyPatch):
    """显式配置时，全局 LLM 并发闸门应读取新上限。"""
    monkeypatch.setenv("LLM_GLOBAL_MAX_CONCURRENCY", "7")
    settings = refresh_settings()

    assert settings.app.llm_global_max_concurrency == 7
    with llm_sync_slot("test:global_limiter"):
        snapshot = get_llm_concurrency_snapshot()

    assert snapshot.max_concurrency == 7
    assert snapshot.active == 1


def test_instrumented_llm_executor_reports_active_and_pending_tasks():
    """评价线程池应暴露 active/pending 统计，供 Watchdog 诊断。"""
    started = threading.Event()
    release = threading.Event()
    executor = appraiser_batch.create_llm_executor(1)

    def blocked_task():
        started.set()
        release.wait(timeout=2)

    try:
        first = executor.submit(blocked_task)
        assert started.wait(timeout=2)
        second = executor.submit(lambda: None)

        deadline = time.time() + 2
        stats = appraiser_batch.get_llm_executor_stats(executor)
        while time.time() < deadline and stats.get("pending") != 1:
            time.sleep(0.01)
            stats = appraiser_batch.get_llm_executor_stats(executor)

        assert stats["max_workers"] == 1
        assert stats["active"] == 1
        assert stats["pending"] == 1

        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)


def test_instrumented_llm_executor_releases_pending_cancelled_task():
    """排队中的任务被取消时，pending 统计应回落。"""
    started = threading.Event()
    release = threading.Event()
    executor = appraiser_batch.create_llm_executor(1)

    def blocked_task():
        started.set()
        release.wait(timeout=2)

    try:
        first = executor.submit(blocked_task)
        assert started.wait(timeout=2)
        second = executor.submit(lambda: None)
        assert second.cancel() is True

        deadline = time.time() + 2
        stats = appraiser_batch.get_llm_executor_stats(executor)
        while time.time() < deadline and stats.get("pending") != 0:
            time.sleep(0.01)
            stats = appraiser_batch.get_llm_executor_stats(executor)

        assert stats["active"] == 1
        assert stats["pending"] == 0
        assert stats["cancelled"] == 1

        release.set()
        first.result(timeout=2)
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)


def test_appraise_all_returns_split_concurrency_fields(monkeypatch: pytest.MonkeyPatch):
    """返回结果应显式区分单指南并发与全局池并发。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "3")
    refresh_settings()

    recorded_pool_sizes: list[int] = []
    executor = ThreadPoolExecutor(max_workers=3)

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: {"status": "success"})
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", lambda task_id, skill_id, lang, cancellation_token=None: _success_result())
    monkeypatch.setattr(
        appraiser_batch,
        "create_llm_executor",
        lambda pool_concurrency: recorded_pool_sizes.append(pool_concurrency) or executor,
    )
    monkeypatch.setattr(appraiser_batch, "shutdown_llm_executor", lambda _executor: None)

    result = asyncio.run(
        appraiser_batch.appraise_all(
            task_id="task-1",
            skill_ids=["item_1", "item_2"],
        )
    )

    executor.shutdown(wait=True, cancel_futures=True)

    assert result["item_concurrency"] == 2
    assert result["pool_concurrency"] == 3
    assert recorded_pool_sizes == [3]


def test_concurrent_appraisals_keep_task_analysis_engines_isolated(
    monkeypatch: pytest.MonkeyPatch,
):
    """同一进程并发评价时，任务级解析方法不得互相污染。"""
    barrier = threading.Barrier(2)
    observed: dict[str, str] = {}
    lock = threading.Lock()

    def fake_run(
        task_id: str,
        _skill_id: str,
        _lang: str,
        _cancellation_token=None,
        *,
        analysis_engine: str,
    ) -> dict:
        with lock:
            observed[task_id] = analysis_engine
        barrier.wait(timeout=2)
        return _success_result()

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: {"status": "success"})
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)

    async def run_both() -> None:
        await asyncio.gather(
            appraiser_batch.appraise_all(
                "task-llm",
                skill_ids=["item_1"],
                analysis_engine="llm",
            ),
            appraiser_batch.appraise_all(
                "task-rule",
                skill_ids=["item_1"],
                analysis_engine="rule",
            ),
        )

    asyncio.run(run_both())

    assert observed == {"task-llm": "llm", "task-rule": "rule"}


def test_appraise_all_limits_single_guideline_concurrency(monkeypatch: pytest.MonkeyPatch):
    """线程池足够大时，应由单指南并发上限控制活跃评价数。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "4")
    refresh_settings()

    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_run(task_id: str, skill_id: str, lang: str, cancellation_token=None) -> dict:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _success_result()

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: {"status": "success"})
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)

    result = asyncio.run(
        appraiser_batch.appraise_all(
            task_id="task-2",
            skill_ids=["item_1", "item_2", "item_3", "item_4"],
        )
    )

    assert result["status"] == "success"
    assert max_active == 2


def test_appraise_all_limits_global_pool_concurrency(monkeypatch: pytest.MonkeyPatch):
    """单指南并发更大时，应由全局线程池上限控制活跃评价数。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "4")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "2")
    refresh_settings()

    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_run(task_id: str, skill_id: str, lang: str, cancellation_token=None) -> dict:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return _success_result()

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: {"status": "success"})
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)

    result = asyncio.run(
        appraiser_batch.appraise_all(
            task_id="task-3",
            skill_ids=["item_1", "item_2", "item_3", "item_4"],
        )
    )

    assert result["status"] == "success"
    assert max_active == 2


def test_appraise_all_does_not_restart_whole_item_after_timeout(monkeypatch: pytest.MonkeyPatch):
    """条目级超时保留原异常，不从头重算成功证据。"""
    attempts = 0

    def fake_run(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise TimeoutError("model timeout")

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)
    result = asyncio.run(appraiser_batch.appraise_all("task-timeout", skill_ids=["item_1"]))

    assert attempts == 1
    assert result["status"] == "error"
    assert result["failed_details"]["item_1"]["type"] == "TimeoutError"


def test_appraise_all_save_failure_does_not_count_as_complete(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", lambda *args, **kwargs: _success_result())
    def save_fails(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", save_fails)

    result = asyncio.run(appraiser_batch.appraise_all("task-save-failed", skill_ids=["item_1"]))

    assert result["completed"] == []
    assert "disk full" in result["failed"]["item_1"]


def test_appraise_all_item_cancelled_does_not_cancel_batch(monkeypatch: pytest.MonkeyPatch):
    """单条目返回 cancelled 不应主动取消整个批量任务。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "2")
    refresh_settings()

    token = CancellationToken()

    def fake_run(task_id: str, skill_id: str, lang: str, cancellation_token=None) -> dict:
        if skill_id == "item_1":
            return {"status": "cancelled", "error": "模拟单条目取消"}
        return _success_result()

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: {"status": "success"})
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)

    result = asyncio.run(
        appraiser_batch.appraise_all(
            task_id="task-item-cancelled",
            skill_ids=["item_1", "item_2"],
            cancellation_token=token,
        )
    )

    assert result["status"] == "partial"
    assert result["completed"] == ["item_2"]
    assert result["failed"] == {"item_1": "模拟单条目取消"}
    assert token.cancelled() is False


def test_final_report_failure_preserves_original_exception(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-report-error", "item_1")
    evaluator.evidence_cache = ["evidence"]
    evaluator.evidence_chunk_map = ["chunk_1"]
    evaluator.evidence_sources = ["chunk_1"]
    evaluator.current_score = 3
    monkeypatch.setattr(gateway_module.litellm, "completion", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("LLM broken")))

    with pytest.raises(RuntimeError, match="LLM broken"):
        evaluator._synthesize_report()


def test_extract_json_object_text_strips_markdown_fence():
    """模型返回 fenced JSON 时应提取内部 JSON 对象。"""
    content = """```json
{
  "analysis": "ok",
  "score": 2
}
```"""

    from core.llm.json_output import parse_json_object
    assert parse_json_object(content) == {"analysis": "ok", "score": 2}


def test_score_current_retries_only_invalid_call(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-score-retry", "item_12")
    evaluator.evidence_cache = ["Evidence links a recommendation to supporting evidence."]
    evaluator.evidence_sources = ["chunk_1"]
    outputs = ['{"analysis": "unterminated', '{"analysis": "Evidence is weakly linked.", "score": 4}']
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return _FakeCompletionResponse(outputs.pop(0))

    monkeypatch.setattr(gateway_module.litellm, "completion", fake_completion)
    monkeypatch.setattr(recovery_module, "backoff_seconds", lambda retry: 0)

    assert evaluator._retry_llm_call("dynamic_scoring", evaluator._score_current) == 4
    assert len(calls) == 2
    assert all(call["num_retries"] == 0 for call in calls)
    assert evaluator.recovery.remaining == 2


def test_each_item_has_own_retry_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recovery_module, "backoff_seconds", lambda retry: 0)
    for item_id in ("item_1", "item_2"):
        evaluator = appraiser.IterativeEvaluator("task-retry-budget", item_id)
        attempts = 0

        def call():
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise TimeoutError("temporary timeout")
            return "ok"

        assert evaluator._retry_llm_call("evidence_extraction", call) == "ok"
        assert attempts == 4
        assert evaluator.recovery.remaining == 0


def test_scoring_retry_keeps_successful_evidence_batch(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-keep-evidence", "item_1", analysis_engine="llm")
    chunks = [type("Chunk", (), {"chunk_id": "chunk_1"})()]
    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "unused"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda prep_dir: type("Pool", (), {"chunks": chunks})())
    monkeypatch.setattr(appraiser, "EvidenceIterator", lambda items, domain: chunks)
    monkeypatch.setattr(recovery_module, "backoff_seconds", lambda retry: 0)
    extraction_calls = 0
    scoring_calls = 0

    def extract(batch):
        nonlocal extraction_calls
        extraction_calls += 1
        return ["evidence"], ["chunk_1"]

    def score():
        nonlocal scoring_calls
        scoring_calls += 1
        if scoring_calls == 1:
            raise TimeoutError("temporary scoring timeout")
        return 4

    monkeypatch.setattr(evaluator, "_extract_evidence_batch", extract)
    monkeypatch.setattr(evaluator, "_score_current", score)
    monkeypatch.setattr(evaluator, "_synthesize_report", lambda: {"status": "success"})
    assert evaluator.evaluate()["status"] == "success"
    assert extraction_calls == 1
    assert scoring_calls == 2
    assert evaluator.evidence_cache == ["evidence"]


def test_empty_later_batch_does_not_rescore(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-no-rescore", "item_1", analysis_engine="llm")
    chunks = [type("Chunk", (), {"chunk_id": f"chunk_{index}"})() for index in range(10)]
    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "unused"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda prep_dir: type("Pool", (), {"chunks": chunks})())
    monkeypatch.setattr(appraiser, "EvidenceIterator", lambda items, domain: chunks)
    extraction_calls = 0
    scoring_calls = 0

    def extract(batch):
        nonlocal extraction_calls
        extraction_calls += 1
        return (["evidence"], ["chunk_0"]) if extraction_calls == 1 else ([], [])

    def score():
        nonlocal scoring_calls
        scoring_calls += 1
        return 4

    monkeypatch.setattr(evaluator, "_extract_evidence_batch", extract)
    monkeypatch.setattr(evaluator, "_score_current", score)
    monkeypatch.setattr(evaluator, "_synthesize_report", lambda: {"status": "success"})
    assert evaluator.evaluate()["status"] == "success"
    assert extraction_calls == 2
    assert scoring_calls == 1


def test_invalid_final_reasoning_retries_same_report_call(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-final-report", "item_1", analysis_engine="llm")
    monkeypatch.setattr(appraiser, "render_final_report_prompt", lambda **kwargs: "report prompt")
    monkeypatch.setattr(recovery_module, "backoff_seconds", lambda retry: 0)
    outputs = [
        '{"step1_locate":"chapter","step3_reasoning":{}}',
        '{"step1_locate":"chapter","step3_reasoning":"supported explanation"}',
    ]
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return _FakeCompletionResponse(outputs.pop(0))

    monkeypatch.setattr(gateway_module.litellm, "completion", completion)
    result = evaluator._retry_llm_call("item_report", evaluator._synthesize_report)
    assert result["chain_of_thought"]["step3_reasoning"] == "supported explanation"
    assert len(calls) == 2
    assert all(call["num_retries"] == 0 for call in calls)


def test_empty_evidence_is_valid_but_invalid_json_is_not(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-evidence-json", "item_1", analysis_engine="llm")
    chunk = type("Chunk", (), {"chunk_id": "chunk_1", "title": "Section", "content": "No relevant evidence"})()
    monkeypatch.setattr(gateway_module.litellm, "completion", lambda **kwargs: _FakeCompletionResponse('{"evidence": []}'))
    assert evaluator._extract_evidence_batch([chunk]) == ([], [])

    attempts = 0

    def invalid_completion(**kwargs):
        nonlocal attempts
        attempts += 1
        return _FakeCompletionResponse('{"evidence": invalid}')

    monkeypatch.setattr(gateway_module.litellm, "completion", invalid_completion)
    monkeypatch.setattr(recovery_module, "backoff_seconds", lambda retry: 0)
    with pytest.raises(appraiser.InvalidModelJSON):
        evaluator._retry_llm_call("evidence_extraction", lambda: evaluator._extract_evidence_batch([chunk]))
    assert attempts == 4


def test_mineru_health_check_retries_transient_error(monkeypatch: pytest.MonkeyPatch):
    """MinerU 健康检查遇到瞬时异常后应重试。"""
    attempts = 0
    requested_urls: list[str] = []

    class FakeResponse:
        status_code = 200
        text = '{"data":[]}'

    def fake_probe(url: str, headers=None):
        nonlocal attempts
        attempts += 1
        requested_urls.append(url)
        if attempts == 1:
            raise RuntimeError("temporary connect error")
        return FakeResponse()

    async def fake_sleep(delay: float):
        return None

    mineru_parser._health_check_cache.clear()
    monkeypatch.setattr(mineru_parser, "_sync_health_probe", fake_probe)
    monkeypatch.setattr(mineru_parser.asyncio, "sleep", fake_sleep)

    parser = mineru_parser.MinerUParser(server_url="https://example.test/mineru/")

    assert asyncio.run(parser.health_check()) is True
    assert attempts == 2
    assert requested_urls == [
        "https://example.test/mineru/v1/models",
        "https://example.test/mineru/v1/models",
    ]


def test_mineru_health_check_reuses_recent_success(monkeypatch: pytest.MonkeyPatch):
    """MinerU 健康检查短时间内复用成功结果，避免批量并发重复探测。"""
    attempts = 0

    class FakeResponse:
        status_code = 200
        text = '{"data":[]}'

    def fake_probe(url: str, headers=None):
        nonlocal attempts
        attempts += 1
        return FakeResponse()

    mineru_parser._health_check_cache.clear()
    monkeypatch.setattr(mineru_parser, "_sync_health_probe", fake_probe)

    parser = mineru_parser.MinerUParser(server_url="https://example.test/mineru/")

    assert asyncio.run(parser.health_check()) is True
    assert asyncio.run(parser.health_check()) is True
    assert attempts == 1


def test_mineru_health_check_coalesces_concurrent_probes(monkeypatch: pytest.MonkeyPatch):
    """同一 MinerU 地址的并发健康检查只发起一次探测。"""
    attempts = 0

    class FakeResponse:
        status_code = 200
        text = '{"data":[]}'

    def fake_probe(_url: str, headers=None):
        nonlocal attempts
        attempts += 1
        time.sleep(0.02)
        return FakeResponse()

    mineru_parser._health_check_cache.clear()
    mineru_parser._health_check_inflight.clear()
    monkeypatch.setattr(mineru_parser, "_sync_health_probe", fake_probe)
    parser = mineru_parser.MinerUParser(server_url="https://example.test/mineru/")

    async def run() -> list[bool]:
        return await asyncio.gather(*(parser.health_check() for _ in range(20)))

    assert all(asyncio.run(run()))
    assert attempts == 1


def test_http_vlm_client_uses_shared_global_async_request_limit(monkeypatch: pytest.MonkeyPatch):
    """共享 HttpVlmClient 应暴露跨文档复用的全局异步请求限流器。"""
    monkeypatch.setattr(HttpVlmClient, "_new_client", lambda self: object())
    monkeypatch.setattr(HttpVlmClient, "_check_model_name", lambda self, base_url, model_name: None)

    client = HttpVlmClient(
        model_name="mineru",
        server_url="https://example.test/mineru/",
        max_concurrency=80,
    )

    assert client._global_aio_request_limit == 30
    assert client._global_aio_request_semaphore is not None


def test_http_vlm_client_shares_global_async_request_gate_across_batches(monkeypatch: pytest.MonkeyPatch):
    """同一 HttpVlmClient 下的多个批量请求应共享全局异步限流。"""
    active = 0
    max_active = 0
    lock = threading.Lock()

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}'

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}

    class FakeAsyncClient:
        async def post(self, _url: str, json=None):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            with lock:
                active -= 1
            return FakeResponse()

    monkeypatch.setattr(HttpVlmClient, "_new_client", lambda self: object())
    monkeypatch.setattr(HttpVlmClient, "_check_model_name", lambda self, base_url, model_name: None)
    async def fake_new_aio_client(self):
        return FakeAsyncClient()

    monkeypatch.setattr(HttpVlmClient, "_new_aio_client", fake_new_aio_client)

    client = HttpVlmClient(
        model_name="mineru",
        server_url="https://example.test/mineru/",
        max_concurrency=80,
    )
    images = [Image.new("RGB", (1, 1), color="white") for _ in range(40)]

    async def run_batches():
        await asyncio.gather(
            client.aio_batch_predict(images, prompts="layout"),
            client.aio_batch_predict(images, prompts="layout"),
        )

    asyncio.run(run_batches())

    assert max_active <= client._global_aio_request_limit
    assert max_active > 1


def test_mineru_http_client_defaults_to_stepping_mode(monkeypatch: pytest.MonkeyPatch):
    """http-client 后端默认走 staged batching，避免批量多文档并发交错阻塞。"""
    monkeypatch.setattr(mineru_client_module, "new_vlm_client", lambda **kwargs: object())

    client = MinerUClient(
        backend="http-client",
        model_name="mineru",
        server_url="https://example.test/mineru/",
        max_concurrency=12,
    )

    assert client.batching_mode == "stepping"


def test_mineru_model_singleton_passes_model_name_and_scopes_cache(monkeypatch: pytest.MonkeyPatch):
    """MinerU 单例应透传模型名，并按模型名隔离 HTTP client 缓存。"""
    created_clients = []

    class FakeMinerUClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created_clients.append(self)

    vlm_analyze.ModelSingleton._models.clear()
    monkeypatch.setattr(vlm_analyze, "MinerUClient", FakeMinerUClient)

    first_client = vlm_analyze.ModelSingleton().get_model(
        "http-client",
        None,
        "https://example.test/mineru/",
        model_name="mineru-a",
    )
    cached_client = vlm_analyze.ModelSingleton().get_model(
        "http-client",
        None,
        "https://example.test/mineru/",
        model_name="mineru-a",
    )
    second_client = vlm_analyze.ModelSingleton().get_model(
        "http-client",
        None,
        "https://example.test/mineru/",
        model_name="mineru-b",
    )

    assert first_client is cached_client
    assert first_client is not second_client
    assert [client.kwargs["model_name"] for client in created_clients] == ["mineru-a", "mineru-b"]

    vlm_analyze.ModelSingleton._models.clear()


def test_string_reasons_are_coerced_into_structured_items(monkeypatch: pytest.MonkeyPatch):
    import json

    evaluator = appraiser.IterativeEvaluator("task-step3-coerce", "item_1", analysis_engine="llm")
    evaluator.evidence_cache = ["Evidence about the guideline."]
    evaluator.evidence_chunk_map = ["chunk_001"]
    evaluator.evidence_sources = ["chunk_001"]
    evaluator.current_score = 4
    monkeypatch.setattr(appraiser, "render_final_report_prompt", lambda **kwargs: "report prompt")
    payload = json.dumps({
        "step1_locate": "chapter",
        "step3_reasoning": {
            "summary": "Overall the guideline is adequate.",
            "reasons": [
                "Evidence Grading and Strength Indicators",
                {"explanation": "The document reports strong evidence.", "evidence_chunks": ["chunk_001"]},
            ],
            "score_justification": "Score 4 because the relevant range applies.",
        },
    })
    monkeypatch.setattr(gateway_module.litellm, "completion", lambda **kwargs: _FakeCompletionResponse(payload))

    result = evaluator._retry_llm_call("item_report", evaluator._synthesize_report)

    reasons = result["chain_of_thought"]["step3_reasoning"]["reasons"]
    assert reasons[0]["point"] == "Evidence Grading and Strength Indicators"
    assert reasons[0]["explanation"] == reasons[0]["point"]
    assert reasons[0]["evidence_chunks"] == []
    assert reasons[1]["point"] == "The document reports strong evidence."
    assert reasons[1]["evidence_chunks"] == ["chunk_001"]


def test_step3_reasoning_without_justification_derives_it_from_summary(monkeypatch: pytest.MonkeyPatch):
    import json

    evaluator = appraiser.IterativeEvaluator("task-step3-derived", "item_1", analysis_engine="llm")
    evaluator.evidence_cache = ["Evidence"]
    evaluator.evidence_chunk_map = ["chunk_001"]
    evaluator.evidence_sources = ["chunk_001"]
    evaluator.current_score = 3
    monkeypatch.setattr(appraiser, "render_final_report_prompt", lambda **kwargs: "report prompt")
    payload = json.dumps({
        "step1_locate": "chapter",
        "step3_reasoning": {
            "summary": "Partial coverage of the requirement.",
            "reasons": [{"point": "Partial", "explanation": "Only part of the item is described."}],
        },
    })
    monkeypatch.setattr(gateway_module.litellm, "completion", lambda **kwargs: _FakeCompletionResponse(payload))

    result = evaluator._retry_llm_call("item_report", evaluator._synthesize_report)

    reasoning = result["chain_of_thought"]["step3_reasoning"]
    assert reasoning["score_justification"] == "Partial coverage of the requirement."
    assert reasoning["reasons"][0]["point"] == "Partial"


def test_serialized_reasoning_string_is_parsed_before_validation():
    raw = '{"summary": "Overall adequate.", "reasons": [{"point": "P", "explanation": "E"}, "bare string"]}'
    reasoning = appraiser.normalize_step3_reasoning(raw)

    assert reasoning["summary"] == "Overall adequate."
    assert reasoning["reasons"][1]["point"] == "bare string"
    assert reasoning["score_justification"] == "Overall adequate."


@pytest.mark.parametrize(("lang", "marker"), [("zh", "降级"), ("en", "replaces it")])
def test_final_report_degrades_instead_of_failing_when_reasoning_stays_invalid(
    monkeypatch: pytest.MonkeyPatch, lang: str, marker: str,
):
    evaluator = appraiser.IterativeEvaluator("task-degrade", "item_1", analysis_engine="llm", lang=lang)
    chunk = type("Chunk", (), {"chunk_id": "chunk_1", "title": "Section", "content": "text"})()
    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "unused"})
    monkeypatch.setattr(
        appraiser.EvidencePool, "load", lambda prep_dir: type("Pool", (), {"chunks": [chunk]})()
    )
    monkeypatch.setattr(appraiser, "EvidenceIterator", lambda items, domain: [chunk])
    monkeypatch.setattr(evaluator, "_extract_with_budget", lambda batch: ([], []))
    monkeypatch.setattr(appraiser, "render_final_report_prompt", lambda **kwargs: "report prompt")
    monkeypatch.setattr(recovery_module, "backoff_seconds", lambda retry: 0)
    monkeypatch.setattr(
        gateway_module.litellm,
        "completion",
        lambda **kwargs: _FakeCompletionResponse('{"step1_locate":"chapter","step3_reasoning":{}}'),
    )

    result = evaluator.evaluate()

    assert result["status"] == "success"
    assert result["iteration_stats"]["report_degraded"] is True
    # Server-generated degraded text follows the appraisal language.
    assert marker in result["chain_of_thought"]["step3_reasoning"]
    assert result["chain_of_thought"]["score"] == 1


def test_item_timeout_cancels_the_running_item_but_not_the_batch(monkeypatch: pytest.MonkeyPatch):
    """超时后条目线程在下一个检查点退出，不再占用重试的串行槽和模型并发。"""
    monkeypatch.setattr(refresh_settings().app, "batch_item_llm_timeout", 0.2)
    batch_token = CancellationToken()
    seen: dict = {}
    stopped = threading.Event()

    def fake_run(task_id, skill_id, lang, cancellation_token=None):
        seen["token"] = cancellation_token
        deadline = time.time() + 5
        while time.time() < deadline:
            if cancellation_token.cancelled():
                stopped.set()
                return {"status": "cancelled", "error": "stopped"}
            time.sleep(0.01)
        return _success_result()

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)
    result = asyncio.run(
        appraiser_batch.appraise_all("task-item-timeout", skill_ids=["item_1"], cancellation_token=batch_token)
    )

    assert result["failed_details"]["item_1"]["type"] == "TimeoutError"
    assert stopped.wait(2)
    assert seen["token"].reason == "item_timeout"
    assert batch_token.cancelled() is False


def test_gateway_sends_no_request_for_a_cancelled_item(monkeypatch: pytest.MonkeyPatch):
    from core.llm.context import RequestBudget
    from core.utils.cancellation import CancellationRequested, bind_cancellation

    sent: list = []
    monkeypatch.setattr(gateway_module.litellm, "completion", lambda **kwargs: sent.append(kwargs))
    gateway = gateway_module.LLMGateway(
        model="m", base_url="", api_key="k", timeout=1, provider_kwargs={}, budget=RequestBudget(32000, "m"),
    )
    token = CancellationToken()
    token.cancel("item_timeout")

    with bind_cancellation(token), pytest.raises(CancellationRequested):
        gateway.complete("Stage", [{"role": "user", "content": "hi"}], max_tokens=16)
    assert sent == []
