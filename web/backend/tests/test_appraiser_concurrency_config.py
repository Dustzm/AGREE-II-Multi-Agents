"""Appraiser 并发配置测试。"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.agents.tools import appraiser_batch
from core.agents import appraiser
from core.config import refresh_settings
from core.parsers import mineru_parser
from core.utils.cancellation import CancellationToken


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


@pytest.fixture(autouse=True)
def reset_runtime_state():
    """每个测试前后重置配置缓存。"""
    refresh_settings()
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


def test_appraise_all_returns_split_concurrency_fields(monkeypatch: pytest.MonkeyPatch):
    """返回结果应显式区分单指南并发与全局池并发。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "3")
    refresh_settings()

    recorded_pool_sizes: list[int] = []
    executor = ThreadPoolExecutor(max_workers=3)

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: None)
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
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: None)
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
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: None)
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)

    result = asyncio.run(
        appraiser_batch.appraise_all(
            task_id="task-3",
            skill_ids=["item_1", "item_2", "item_3", "item_4"],
        )
    )

    assert result["status"] == "success"
    assert max_active == 2


def test_appraise_all_retries_item_timeout_without_cancelling_batch(monkeypatch: pytest.MonkeyPatch):
    """单条目 LLM 超时应重试，且不应取消整个批量任务。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "4")
    refresh_settings()

    attempts = 0
    retry_events: list[str] = []
    token = CancellationToken()

    original_wait_for = appraiser_batch.asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, timeout=0.01)

    def fake_run(task_id: str, skill_id: str, lang: str, cancellation_token=None) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts < appraiser_batch.ITEM_LLM_MAX_ATTEMPTS:
            time.sleep(0.05)
        return _success_result()

    async def on_item_progress(skill_id: str, title: str, status: str, duration: float | None = None):
        retry_events.append(status)

    monkeypatch.setattr(appraiser_batch.asyncio, "wait_for", fast_wait_for)
    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: None)
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", fake_run)

    result = asyncio.run(
        appraiser_batch.appraise_all(
            task_id="task-timeout",
            skill_ids=["item_1"],
            on_item_progress=on_item_progress,
            cancellation_token=token,
        )
    )

    assert attempts == appraiser_batch.ITEM_LLM_MAX_ATTEMPTS
    assert retry_events == ["started", "retrying", "retrying", "retrying", "completed"]
    assert result["status"] == "success"
    assert token.cancelled() is False


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
    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", lambda **kwargs: None)
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


def test_final_report_failure_returns_error_without_simple_report(monkeypatch: pytest.MonkeyPatch):
    """最终报告失败应返回 error，不应回退成简化成功报告。"""
    evaluator = appraiser.IterativeEvaluator("task-report-error", "item_1")
    evaluator.evidence_cache = ["evidence"]
    evaluator.evidence_chunk_map = ["chunk_1"]
    evaluator.evidence_sources = ["chunk_1"]
    evaluator.current_score = 3

    monkeypatch.setattr(appraiser, "litellm", type("FakeLiteLLM", (), {
        "completion": staticmethod(lambda **kwargs: (_ for _ in ()).throw(RuntimeError("LLM broken")))
    }))

    result = evaluator._synthesize_report()

    assert result["status"] == "error"
    assert "LLM broken" in result["error"]


def test_extract_json_object_text_strips_markdown_fence():
    """模型返回 fenced JSON 时应提取内部 JSON 对象。"""
    evaluator = appraiser.IterativeEvaluator("task-json-fence", "item_1")

    content = """```json
{
  "analysis": "ok",
  "score": 2
}
```"""

    assert evaluator._extract_json_object_text(content) == '{\n  "analysis": "ok",\n  "score": 2\n}'


def test_mineru_health_check_retries_transient_error(monkeypatch: pytest.MonkeyPatch):
    """MinerU 健康检查遇到瞬时异常后应重试。"""
    attempts = 0
    requested_urls: list[str] = []

    class FakeResponse:
        status_code = 200
        text = '{"data":[]}'

    class FakeClient:
        def __init__(self, timeout: int):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers=None):
            nonlocal attempts
            attempts += 1
            requested_urls.append(url)
            if attempts == 1:
                raise RuntimeError("temporary connect error")
            return FakeResponse()

    async def fake_sleep(delay: float):
        return None

    monkeypatch.setattr(mineru_parser.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(mineru_parser.asyncio, "sleep", fake_sleep)

    parser = mineru_parser.MinerUParser(server_url="https://example.test/mineru/")

    assert asyncio.run(parser.health_check()) is True
    assert attempts == 2
    assert requested_urls == [
        "https://example.test/mineru/v1/models",
        "https://example.test/mineru/v1/models",
    ]
