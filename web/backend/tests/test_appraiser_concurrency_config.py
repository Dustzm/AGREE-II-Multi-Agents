"""Appraiser 并发配置测试。"""
from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.agents.tools import appraiser_batch
from core.config import refresh_settings


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


def _reset_appraiser_executor() -> None:
    """重置全局线程池，避免测试间相互影响。"""
    if appraiser_batch._llm_executor is not None:
        appraiser_batch._llm_executor.shutdown(wait=True, cancel_futures=True)
    appraiser_batch._llm_executor = None
    appraiser_batch._llm_executor_pool_size = None


@pytest.fixture(autouse=True)
def reset_runtime_state():
    """每个测试前后重置配置缓存和线程池状态。"""
    _reset_appraiser_executor()
    refresh_settings()
    yield
    _reset_appraiser_executor()
    refresh_settings()


def test_appraisal_pool_concurrency_defaults_to_ten(monkeypatch: pytest.MonkeyPatch):
    """未配置全局池并发时应使用默认值 10。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "7")
    monkeypatch.delenv("APPRAISAL_POOL_MAX_CONCURRENCY", raising=False)

    settings = refresh_settings()

    assert settings.app.appraisal_max_concurrency == 7
    assert settings.app.appraisal_pool_max_concurrency == 10


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
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", lambda task_id, skill_id, lang: _success_result())
    monkeypatch.setattr(
        appraiser_batch,
        "_get_llm_executor",
        lambda pool_concurrency: recorded_pool_sizes.append(pool_concurrency) or executor,
    )

    result = asyncio.run(
        appraiser_batch.appraise_all(
            task_id="task-1",
            skill_ids=["item_1", "item_2"],
        )
    )

    executor.shutdown(wait=True, cancel_futures=True)

    assert result["item_concurrency"] == 2
    assert result["pool_concurrency"] == 3
    assert recorded_pool_sizes == [3, 3]


def test_appraise_all_limits_single_guideline_concurrency(monkeypatch: pytest.MonkeyPatch):
    """线程池足够大时，应由单指南并发上限控制活跃评价数。"""
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "4")
    refresh_settings()

    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_run(task_id: str, skill_id: str, lang: str) -> dict:
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

    def fake_run(task_id: str, skill_id: str, lang: str) -> dict:
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
