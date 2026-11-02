"""Dynamic-Hybrid Router 运行时接入测试。"""

from __future__ import annotations

import pytest

from core.agents import appraiser
from core.config import refresh_settings
from core.hybrid_router import HybridRouteDecision, select_appraisal_path
from core.hybrid_router import router as hybrid_router
from core.preprocessor.evidence import EvidencePool
from core.preprocessor.pojo import SemanticChunk, SemanticLabel


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALYSIS_ENGINE", "hybrid")
    refresh_settings()
    yield
    refresh_settings()


def _chunk() -> SemanticChunk:
    return SemanticChunk(
        chunk_id="chunk_001",
        title="Objectives",
        content="The guideline describes its objectives and target population.",
        label=SemanticLabel.OVERVIEW,
        start_page=1,
        end_page=1,
    )


def test_frozen_router_is_self_contained_and_returns_minimal_payload():
    decision = select_appraisal_path(EvidencePool([_chunk()]), "item_1", 1)

    assert decision.selected_path in {"llm", "rule"}
    assert decision.executed_path == decision.selected_path
    assert 0.5 <= decision.confidence <= 1.0
    assert set(decision.as_payload()) == {
        "selected_path",
        "executed_path",
        "confidence",
    }
    assert hybrid_router._sha256(hybrid_router.MODEL_PATH) == hybrid_router.MODEL_SHA256


def test_runtime_feature_vector_stays_compatible_with_frozen_model():
    row = hybrid_router._feature_row(EvidencePool([_chunk()]), "item_1", 1).iloc[0]

    assert row[list(hybrid_router.EXPECTED_NUMERIC_FEATURES)].to_dict() == {
        "total_chunk_count": 1,
        "total_character_count": 71,
        "domain_chunk_count": 1,
        "domain_character_count": 71,
        "domain_chunk_ratio": 1.0,
        "selected_chunk_count": 1,
        "selected_character_count": 71,
        "selected_domain_ratio": 1.0,
        "item_signal_chunk_count": 0,
        "routing_score_mean": 2.0,
        "routing_score_max": 2.0,
        "routing_limit_hit": 0,
        "selected_total_ratio": 1.0,
        "domain_character_ratio": 1.0,
        "selected_character_ratio": 1.0,
        "selected_characters_per_chunk": 71.0,
        "signal_selected_ratio": 0.0,
        "routing_score_spread": 0.0,
    }


def test_item17_is_fixed_to_llm_without_loading_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(hybrid_router, "_load_bundle", lambda: pytest.fail("model loaded"))

    decision = select_appraisal_path(EvidencePool([]), "item_17", 4)

    assert decision == HybridRouteDecision("llm", "llm", 1.0)


def test_hybrid_llm_path_is_attached_to_appraiser_result(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-1", "item_1")
    monkeypatch.setattr(appraiser, "get_task_paths", lambda _task_id: {"prep_dir": "/tmp/prep"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda _prep: EvidencePool([]))
    monkeypatch.setattr(
        appraiser,
        "select_appraisal_path",
        lambda *_args: HybridRouteDecision("llm", "llm", 0.8),
    )

    result = evaluator.evaluate()

    assert result["status"] == "no_evidence"
    assert result["hybrid_router"] == {
        "selected_path": "llm",
        "executed_path": "llm",
        "confidence": 0.8,
    }


def test_hybrid_rule_failure_records_llm_fallback(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-1", "item_1")
    monkeypatch.setattr(appraiser, "get_task_paths", lambda _task_id: {"prep_dir": "/tmp/prep"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda _prep: EvidencePool([]))
    monkeypatch.setattr(
        appraiser,
        "select_appraisal_path",
        lambda *_args: HybridRouteDecision("rule", "rule", 0.7),
    )

    class FailingAdapter:
        def evaluate(self, _chunks):
            raise RuntimeError("rule failed")

    monkeypatch.setattr(
        appraiser.RuleAppraiserAdapter,
        "from_evaluator",
        lambda _evaluator: FailingAdapter(),
    )

    result = evaluator.evaluate()

    assert result["status"] == "no_evidence"
    assert result["hybrid_router"] == {
        "selected_path": "rule",
        "executed_path": "llm",
        "confidence": 0.7,
    }
