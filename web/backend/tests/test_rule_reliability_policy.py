"""Failure policy, routing label exclusion, and failure report contracts."""
from __future__ import annotations

from types import SimpleNamespace

from core.llm.failures import FailureKind, StageContractError
from core.observability.appraisal import build_failure_report, write_failure_report
from core.preprocessor.pojo import SemanticChunk, SemanticLabel
from core.rule_engine.failure_policy import RuleFailurePolicy
from core.rule_engine.routing.weighted import WeightedChunkRouter


class TruncatedModelOutput(RuntimeError):
    """Stand-in: classification is by exception name."""


def _chunk(chunk_id: str, label: SemanticLabel) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        title=chunk_id,
        content=f"content of {chunk_id}",
        label=label,
        start_page=1,
        end_page=1,
    )


def test_default_policy_degrades_even_strict_items():
    policy = RuleFailurePolicy(fallback_enabled=True, mode="degraded", strict_items={"item_11"})
    decision = policy.decide("item_11", RuntimeError("network boom"))
    assert decision.fatal is False
    assert decision.degraded is True
    assert decision.kind is FailureKind.TRANSPORT


def test_legacy_mode_keeps_strict_items_fatal():
    policy = RuleFailurePolicy(fallback_enabled=True, mode="legacy", strict_items={"item_11"})
    assert policy.decide("item_11", RuntimeError("boom")).fatal is True
    assert policy.decide("item_3", RuntimeError("boom")).degraded is True


def test_fallback_disabled_is_always_fatal():
    policy = RuleFailurePolicy(fallback_enabled=False)
    decision = policy.decide("item_3", RuntimeError("boom"))
    assert decision.fatal is True
    assert decision.reason == "fallback_disabled"


def test_contract_failures_stay_fatal():
    policy = RuleFailurePolicy(fallback_enabled=True, mode="degraded")
    decision = policy.decide("item_3", StageContractError("bad contract"))
    assert decision.fatal is True
    assert decision.kind is FailureKind.STAGE_CONTRACT


def test_model_output_errors_degrade_even_for_strict_items():
    policy = RuleFailurePolicy(fallback_enabled=True, mode="degraded")
    for item_id in ("item_3", "item_11", "item_18"):
        decision = policy.decide(item_id, ValueError("Verifier JSON field 'decisions' must be a list"))
        assert decision.fatal is False
        assert decision.degraded is True
        assert decision.kind is FailureKind.SCHEMA_VIOLATION
        assert decision.reason == "schema_violation"


def test_programming_errors_are_not_degraded_as_model_failures():
    policy = RuleFailurePolicy(fallback_enabled=True, mode="degraded")
    for error in (TypeError("x"), KeyError("x"), AttributeError("x"), IndexError("x")):
        decision = policy.decide("item_3", error)
        assert decision.kind is FailureKind.STAGE_CONTRACT
        assert decision.fatal is True


def test_policy_reads_settings():
    settings = SimpleNamespace(
        app=SimpleNamespace(
            rule_engine_fallback_on_error=True,
            rule_fallback_policy="legacy",
            rule_strict_items="item_11,item_18",
        )
    )
    policy = RuleFailurePolicy.from_settings(settings)
    assert policy.strict_items == frozenset({"item_11", "item_18"})
    assert policy.decide("item_18", RuntimeError("x")).fatal is True


def test_router_excludes_appendix_chunks(monkeypatch):
    monkeypatch.setattr(
        "core.rule_engine.routing.weighted._excluded_labels",
        lambda routing=None: {"appendix", "irrelevant"},
    )
    spec = SimpleNamespace(item_id="item_18", chunk_routing=None)
    all_chunks = [_chunk("c1", SemanticLabel.APPENDIX), _chunk("c2", SemanticLabel.RECOMMENDATION)]
    result = WeightedChunkRouter().route(spec=spec, all_chunks=all_chunks, domain_chunks=all_chunks)
    assert [chunk.chunk_id for chunk in result.chunks] == ["c2"]
    assert result.input_chunks == 1


def test_failure_report_counts_kinds_and_degraded():
    summary = {
        "status": "partial",
        "total": 3,
        "completed": ["item_1", "item_2"],
        "degraded": {"item_2": "transport"},
        "failed": {"item_11": "boom"},
        "failed_details": {
            "item_11": {"type": "TruncatedModelOutput", "stage": "rule_engine", "message": "truncated"}
        },
    }
    report = build_failure_report("task-1", summary)
    # Degraded items are counted once (degraded), not also as succeeded.
    assert report["counts"] == {"succeeded": 1, "degraded": 1, "failed": 1}
    assert report["succeeded"] == ["item_1"]
    assert "item_2" in report["degraded"]
    assert report["failed"][0]["kind"] == "truncated"
    assert report["task_id"] == "task-1"


def test_failure_report_tracks_report_degraded_separately():
    summary = {
        "status": "success",
        "total": 2,
        "completed": ["item_1", "item_2"],
        "degraded": {},
        "report_degraded": {"item_2": "report_explanation_degraded"},
        "failed": {},
        "failed_details": {},
    }

    report = build_failure_report("task-3", summary)

    assert report["report_degraded"] == {"item_2": {"reason": "report_explanation_degraded"}}
    assert report["counts"] == {"succeeded": 2, "degraded": 0, "failed": 0}


def test_failure_report_is_written_to_task_dir(tmp_path):
    summary = {"status": "error", "total": 1, "completed": [], "degraded": {}, "failed": {"item_3": "x"}, "failed_details": {}}
    path = write_failure_report("task-2", summary, task_dir=tmp_path)
    assert path is not None and path.exists()
    assert path.name == "failure_report.json"


def test_failure_report_includes_per_item_durations():
    summary = {
        "status": "success",
        "total": 2,
        "completed": ["item_1", "item_2"],
        "degraded": {},
        "report_degraded": {},
        "durations": {"item_1": 1.23456, "item_2": 2.0},
        "failed": {},
        "failed_details": {},
    }
    report = build_failure_report("task-dur", summary)

    assert report["durations"] == {"item_1": 1.235, "item_2": 2.0}
