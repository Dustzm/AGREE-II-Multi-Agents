from __future__ import annotations

import json

import pytest

from core.agents import appraiser
from core.agents.tools import appraiser_batch
from core.agents.tools import appraiser_tools
from core.config import refresh_settings
from core.preprocessor.evidence import EvidencePool
from core.preprocessor.pojo import SemanticChunk, SemanticLabel
from core.rule_engine.models import (
    ChunkRoutingResult,
    EvidenceRef,
    ObjectStoreSnapshot,
    ReviewReason,
    RubricAppraiserDecision,
    RubricAppraiserResult,
    RuleGroupingResult,
    RuleObject,
    RuleObjectGroup,
    RulePipelineResult,
    RuleResult,
    VerificationDecision,
)
from core.rule_engine.appraiser_adapter import RuleAppraiserAdapter, append_rule_engine_title_suffix
from core.rule_engine.registry import get_item_spec, get_rule_engine
from web.backend.app.api.routes import reports


@pytest.fixture(autouse=True)
def reset_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALYSIS_ENGINE", "llm")
    refresh_settings()
    yield
    refresh_settings()


def _success_result(rule_engine: dict | None = None) -> dict:
    result = {
        "status": "success",
        "chain_of_thought": {
            "step1_locate": "locate",
            "step2_evidence": [],
            "step3_reasoning": "reasoning",
            "score": 1,
        },
        "evidence_sources": [],
    }
    if rule_engine is not None:
        result["rule_engine"] = rule_engine
    return result


def test_rule_engine_title_suffix_is_localized(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALYSIS_ENGINE", "rule")
    refresh_settings()

    assert append_rule_engine_title_suffix("条目 1", "item_1", lang="zh") == "条目 1（规则引擎解析）"
    assert append_rule_engine_title_suffix("Item 1", "item_1", lang="en") == "Item 1 (Rule engine)"
    assert append_rule_engine_title_suffix("Item 2", "item_2", lang="en") == "Item 2 (Rule engine)"
    assert append_rule_engine_title_suffix("Item 17", "item_17", lang="en") == "Item 17"


def test_task_analysis_engine_overrides_global_title_mode():
    assert append_rule_engine_title_suffix(
        "Item 1",
        "item_1",
        analysis_engine="rule",
    ) == "Item 1 (Rule engine)"
    assert append_rule_engine_title_suffix(
        "Item 1",
        "item_1",
        analysis_engine="llm",
    ) == "Item 1"


def test_item_11_and_item_12_are_registered_rule_engine_profiles():
    item11 = get_item_spec("item_11")
    item12 = get_item_spec("item_12")

    assert item11.rule_profile == "benefit_harm_balance"
    assert item11.grouping is not None
    assert item11.grouping.group_profile == "benefit_harm_balance"
    assert item11.grouping.max_groups == 16
    assert "background_clinical_risk" not in item11.object_specs
    assert "adverse_event_list_only" not in item11.object_specs
    assert get_rule_engine(item11.rule_profile) is not None

    assert item12.rule_profile == "recommendation_evidence_link"
    assert item12.grouping is not None
    assert item12.grouping.group_profile == "recommendation_evidence_link"
    assert item12.grouping.max_groups == 16
    assert "standalone_reference_list" not in item12.object_specs
    assert "standalone_evidence_table" not in item12.object_specs
    assert "broad_evidence_discussion" not in item12.object_specs
    assert "recommendation_formulation_process" not in item12.object_specs
    assert "references" not in item12.chunk_routing.include_phrases
    assert "reference" not in item12.chunk_routing.include_terms
    assert "references" in item12.chunk_routing.negative_terms
    assert get_rule_engine(item12.rule_profile) is not None


def test_rule_adapter_no_evidence_uses_routing_specific_reason():
    adapter = RuleAppraiserAdapter(
        skill_id="item_8",
        skill_config={"title": "Item 8"},
        domain_id=2,
        lang="zh",
        batch_size=5,
    )

    result = adapter._no_evidence_result(
        routing_result=ChunkRoutingResult(
            chunks=(),
            input_chunks=3,
            domain_chunks=2,
            selected_chunks=0,
        )
    )

    reason = result["rule_engine"]["review_reasons"][0]
    assert reason["code"] == "no_routed_chunks"
    assert "证据路由" in result["chain_of_thought"]["step1_locate"]


def test_group_rule_adapter_empty_domain_route_returns_no_evidence():
    adapter = RuleAppraiserAdapter(
        skill_id="item_21",
        skill_config={"title": "Item 21"},
        domain_id=5,
        lang="zh",
        batch_size=5,
    )
    chunk = SemanticChunk(
        chunk_id="chunk_001",
        title="Background",
        content="This paragraph does not contain rubric routing terms.",
        label=SemanticLabel.IRRELEVANT,
        start_page=1,
        end_page=1,
    )

    result = adapter.evaluate([chunk])

    assert result["status"] == "no_evidence"
    assert result["rule_engine"]["used"] is False
    assert result["rule_engine"]["fallback_used"] is False
    assert result["rule_engine"]["review_reasons"][0]["code"] == "no_domain_chunks"
    assert "rubric" not in result["rule_engine"]


def test_batch_save_passes_rule_engine_payload(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APPRAISAL_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("APPRAISAL_POOL_MAX_CONCURRENCY", "1")
    refresh_settings()

    saved: list[dict] = []
    rule_payload = {"enabled": True, "used": True}
    hybrid_payload = {
        "selected_path": "rule",
        "executed_path": "rule",
        "confidence": 0.75,
    }

    monkeypatch.setattr(appraiser_batch, "get_skill_summary", lambda skill_id, lang="en": skill_id)
    result_payload = _success_result(rule_payload)
    result_payload["hybrid_router"] = hybrid_payload
    monkeypatch.setattr(appraiser_batch, "run_iterative_evaluation", lambda *args: result_payload)
    def fake_save(**kwargs):
        saved.append(kwargs)
        return {"status": "success"}

    monkeypatch.setattr(appraiser_batch, "save_item_evaluation", fake_save)

    result = __import__("asyncio").run(appraiser_batch.appraise_all("task-1", skill_ids=["item_1"]))

    assert result["status"] == "success"
    assert saved[0]["rule_engine"] == rule_payload
    assert saved[0]["hybrid_router"] == hybrid_payload


def test_save_item_evaluation_writes_rule_engine_top_level(tmp_path, monkeypatch: pytest.MonkeyPatch):
    task_dir = tmp_path / "task-1"
    weaver_dir = task_dir / ".ama"
    weaver_dir.mkdir(parents=True)
    monkeypatch.setattr(
        appraiser_tools,
        "get_task_paths",
        lambda task_id: {
            "task_dir": str(task_dir),
            "weaver_dir": str(weaver_dir),
            "prep_dir": str(weaver_dir / "prep"),
            "eval_dir": str(weaver_dir / "evaluations"),
        },
    )

    result = appraiser_tools.save_item_evaluation(
        task_id="task-1",
        skill_id="item_1",
        step1_locate="locate",
        step2_evidence=[],
        step3_reasoning="reasoning",
        score=1,
        evidence_sources=[],
        rule_engine={"enabled": True, "used": True},
        hybrid_router={
            "selected_path": "rule",
            "executed_path": "rule",
            "confidence": 0.75,
        },
    )

    assert result["status"] == "success"
    saved = json.loads((weaver_dir / "evaluations" / "item_1.json").read_text(encoding="utf-8"))
    assert saved["rule_engine"] == {"enabled": True, "used": True}
    assert saved["hybrid_router"] == {
        "selected_path": "rule",
        "executed_path": "rule",
        "confidence": 0.75,
    }


def test_report_loader_keeps_optional_rule_engine_payload():
    eval_data = {
        "item_id": "item_1",
        "domain": 1,
        "title": "目标",
        "chain_of_thought": {
            "score": 7,
            "step1_locate": "locate",
            "step2_evidence": [],
            "step3_reasoning": "reasoning",
        },
        "metadata": {"evaluated_at": "2026-01-01T00:00:00"},
        "evidence_sources": ["chunk_001"],
        "rule_engine": {"enabled": True, "used": True},
    }

    item = reports.ItemEvaluation(
        item_id=eval_data["item_id"],
        domain=eval_data["domain"],
        title=eval_data["title"],
        score=eval_data["chain_of_thought"]["score"],
        step1_locate=eval_data["chain_of_thought"]["step1_locate"],
        step2_evidence=eval_data["chain_of_thought"]["step2_evidence"],
        step3_reasoning=eval_data["chain_of_thought"]["step3_reasoning"],
        evaluated_at=eval_data["metadata"]["evaluated_at"],
        evidence_sources=eval_data["evidence_sources"],
        rule_engine=eval_data.get("rule_engine"),
    )

    assert item.rule_engine == {"enabled": True, "used": True}


def test_report_loader_accepts_legacy_item_without_rule_engine():
    item = reports.ItemEvaluation(
        item_id="item_1",
        domain=1,
        title="目标",
        score=1,
        step1_locate="locate",
        step2_evidence=[],
        step3_reasoning="reasoning",
        evaluated_at="2026-01-01T00:00:00",
    )

    assert item.rule_engine is None


def test_appraiser_falls_back_when_rule_engine_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALYSIS_ENGINE", "rule")
    monkeypatch.setenv("RULE_ENGINE_FALLBACK_ON_ERROR", "true")
    refresh_settings()

    evaluator = appraiser.IterativeEvaluator("task-1", "item_1")
    chunk = SemanticChunk(
        chunk_id="chunk_001",
        title="目标",
        content="主题：2 型糖尿病",
        label=SemanticLabel.OVERVIEW,
        start_page=1,
        end_page=1,
    )
    pool = EvidencePool([chunk])

    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "/tmp/prep"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda prep_dir: pool)
    monkeypatch.setattr(appraiser.RuleAppraiserAdapter, "from_evaluator", lambda _evaluator: type(
        "FailingAdapter",
        (),
        {"evaluate": lambda self, chunks: (_ for _ in ()).throw(RuntimeError("rule failed"))},
    )())
    monkeypatch.setattr(evaluator, "_extract_evidence_batch", lambda chunks: ([], []))
    monkeypatch.setattr(evaluator, "_synthesize_report", lambda: evaluator._with_rule_engine_payload(_success_result()))

    result = evaluator.evaluate()

    assert result["status"] == "success"
    assert result["rule_engine"]["fallback_used"] is True
    assert "rule failed" in result["rule_engine"]["error"]


def test_fallback_gets_fresh_recovery_after_rule_engine_exhausts_it(monkeypatch: pytest.MonkeyPatch):
    from core.llm.failures import FailureKind
    from core.utils.llm_errors import InvalidModelJSON

    monkeypatch.setenv("ANALYSIS_ENGINE", "rule")
    monkeypatch.setenv("RULE_ENGINE_FALLBACK_ON_ERROR", "true")
    refresh_settings()

    evaluator = appraiser.IterativeEvaluator("task-1", "item_1")
    pool = EvidencePool([
        SemanticChunk(
            chunk_id="chunk_001", title="目标", content="主题：2 型糖尿病",
            label=SemanticLabel.OVERVIEW, start_page=1, end_page=1,
        )
    ])

    def exhaust_and_fail(self, chunks):
        while evaluator.recovery.remaining:
            evaluator.recovery.ledger.reserve(FailureKind.TRANSPORT, "rule")
        raise InvalidModelJSON("RuleObjectExtraction:item_1: transport recovery allowance exhausted")

    seen_remaining = []
    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "/tmp/prep"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda prep_dir: pool)
    monkeypatch.setattr(appraiser.RuleAppraiserAdapter, "from_evaluator", lambda _evaluator: type(
        "ExhaustingAdapter", (), {"evaluate": exhaust_and_fail},
    )())
    monkeypatch.setattr(evaluator, "_extract_evidence_batch", lambda chunks: ([], []))

    def synthesize():
        seen_remaining.append(evaluator.recovery.remaining)
        return evaluator._with_rule_engine_payload(_success_result())

    monkeypatch.setattr(evaluator, "_synthesize_report", synthesize)

    result = evaluator.evaluate()

    assert result["rule_engine"]["failure_kind"] == "budget_exceeded"
    assert seen_remaining and seen_remaining[0] > 0


@pytest.mark.parametrize("item_id", ["item_11", "item_12", "item_18", "item_19", "item_21"])
def test_appraiser_degrades_strict_group_items_by_default(
    monkeypatch: pytest.MonkeyPatch,
    item_id: str,
):
    monkeypatch.setenv("ANALYSIS_ENGINE", "rule")
    monkeypatch.setenv("RULE_ENGINE_FALLBACK_ON_ERROR", "true")
    refresh_settings()

    evaluator = appraiser.IterativeEvaluator("task-1", item_id)
    pool = EvidencePool([
        SemanticChunk(
            chunk_id="chunk_001",
            title="Implementation",
            content="Implementation content.",
            label=SemanticLabel.RECOMMENDATION,
            start_page=1,
            end_page=1,
        )
    ])

    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "/tmp/prep"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda prep_dir: pool)
    monkeypatch.setattr(appraiser.RuleAppraiserAdapter, "from_evaluator", lambda _evaluator: type(
        "FailingAdapter",
        (),
        {"evaluate": lambda self, chunks: (_ for _ in ()).throw(RuntimeError("rule failed"))},
    )())
    monkeypatch.setattr(evaluator, "_extract_evidence_batch", lambda chunks: ([], []))
    monkeypatch.setattr(evaluator, "_synthesize_report", lambda: evaluator._with_rule_engine_payload(_success_result()))

    result = evaluator.evaluate()

    assert result["status"] == "success"
    assert result["rule_engine"]["fallback_used"] is True
    assert result["rule_engine"]["degraded"] is True
    assert result["rule_engine"]["failure_kind"] == "transport"


@pytest.mark.parametrize("item_id", ["item_11", "item_18"])
def test_appraiser_legacy_policy_keeps_strict_items_fatal(
    monkeypatch: pytest.MonkeyPatch,
    item_id: str,
):
    monkeypatch.setenv("ANALYSIS_ENGINE", "rule")
    monkeypatch.setenv("RULE_ENGINE_FALLBACK_ON_ERROR", "true")
    monkeypatch.setenv("RULE_FALLBACK_POLICY", "legacy")
    refresh_settings()

    evaluator = appraiser.IterativeEvaluator("task-1", item_id)
    pool = EvidencePool([
        SemanticChunk(
            chunk_id="chunk_001",
            title="Implementation",
            content="Implementation content.",
            label=SemanticLabel.RECOMMENDATION,
            start_page=1,
            end_page=1,
        )
    ])
    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "/tmp/prep"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda prep_dir: pool)
    monkeypatch.setattr(appraiser.RuleAppraiserAdapter, "from_evaluator", lambda _evaluator: type(
        "FailingAdapter",
        (),
        {"evaluate": lambda self, chunks: (_ for _ in ()).throw(RuntimeError("rule failed"))},
    )())

    with pytest.raises(RuntimeError, match="rule failed"):
        evaluator.evaluate()
    assert evaluator._rule_engine_payload["fallback_used"] is False
    assert evaluator._rule_engine_payload["degraded"] is False
    assert evaluator._rule_engine_payload["fallback_reason"] == "strict_item_legacy_mode"


def test_appraiser_returns_error_when_rule_engine_fails_without_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALYSIS_ENGINE", "rule")
    monkeypatch.setenv("RULE_ENGINE_FALLBACK_ON_ERROR", "false")
    refresh_settings()

    evaluator = appraiser.IterativeEvaluator("task-1", "item_1")
    pool = EvidencePool([
        SemanticChunk(
            chunk_id="chunk_001",
            title="目标",
            content="主题：2 型糖尿病",
            label=SemanticLabel.OVERVIEW,
            start_page=1,
            end_page=1,
        )
    ])

    monkeypatch.setattr(appraiser, "get_task_paths", lambda task_id: {"prep_dir": "/tmp/prep"})
    monkeypatch.setattr(appraiser.EvidencePool, "load", lambda prep_dir: pool)
    monkeypatch.setattr(appraiser.RuleAppraiserAdapter, "from_evaluator", lambda _evaluator: type(
        "FailingAdapter",
        (),
        {"evaluate": lambda self, chunks: (_ for _ in ()).throw(RuntimeError("rule failed"))},
    )())

    with pytest.raises(RuntimeError, match="rule failed"):
        evaluator.evaluate()
    assert evaluator._rule_engine_payload["fallback_used"] is False
    assert evaluator._rule_engine_payload["degraded"] is False


def test_rule_adapter_success_with_score_one_allows_empty_citations(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-1", "item_1")
    chunk = SemanticChunk(
        chunk_id="chunk_001",
        title="目标",
        content="未提及指南总体目标。",
        label=SemanticLabel.OVERVIEW,
        start_page=1,
        end_page=1,
    )
    pipeline_result = RulePipelineResult(
        item_id="item_1",
        objects=[],
        snapshot=ObjectStoreSnapshot(
            item_id="item_1",
            objects=(),
            processed_chunks=("chunk_001",),
            review_reasons=(),
        ),
        rule_result=RuleResult(
            item_id="item_1",
            status="matched",
            score_range="1",
            allowed_scores=[1],
            max_score=1,
            matched_rules=[],
            missing=["health_topic"],
            needs_review=False,
            review_reasons=[],
        ),
    )

    class FakeExplainer:
        def __init__(self, _client):
            pass

        def explain(self, payload, lang="zh"):
            return type(
                "Explanation",
                (),
                {
                    "step1_locate": "已检查相关章节，未发现对应信息。",
                    "step2_evidence": ["LLM 证据不应保存"],
                    "step3_reasoning": {
                        "summary": "未提及总体目标。",
                        "reasons": [
                            {
                                "point": "未提及",
                                "explanation": "相关内容中未发现该条目要求的信息。",
                                "evidence_chunks": [],
                            }
                        ],
                        "score_justification": "未提及，评分为 1。",
                    },
                    "score": 1,
                },
            )()

    monkeypatch.setattr(appraiser.RuleAppraiserAdapter, "_build_llm_client", lambda self: object())
    monkeypatch.setattr("core.rule_engine.appraiser_adapter.run_rule_pipeline", lambda *args, **kwargs: pipeline_result)
    monkeypatch.setattr("core.rule_engine.appraiser_adapter.LLMRuleExplainer", FakeExplainer)

    result = appraiser.RuleAppraiserAdapter.from_evaluator(evaluator).evaluate([chunk])

    assert result["status"] == "success"
    assert result["chain_of_thought"]["score"] == 1
    assert result["chain_of_thought"]["step2_evidence"] == []
    assert result["chain_of_thought"]["step3_reasoning"]["reasons"][0]["evidence_chunks"] == []
    assert result["rule_engine"]["used"] is True
    assert result["rule_engine"]["fallback_used"] is False


def test_rule_adapter_uses_verified_quotes_for_step2_evidence(monkeypatch: pytest.MonkeyPatch):
    evaluator = appraiser.IterativeEvaluator("task-1", "item_1")
    chunk = SemanticChunk(
        chunk_id="chunk_001",
        title="目标",
        content="主题：2 型糖尿病。用于治疗。",
        label=SemanticLabel.OVERVIEW,
        start_page=1,
        end_page=1,
    )
    from core.rule_engine.models import EvidenceRef, RuleObject

    pipeline_result = RulePipelineResult(
        item_id="item_1",
        objects=[],
        snapshot=ObjectStoreSnapshot(
            item_id="item_1",
            objects=(
                RuleObject(
                    type="health_topic",
                    value="2 型糖尿病",
                    evidence=EvidenceRef(chunk_id="chunk_001", quote="主题：2 型糖尿病。"),
                    confidence=0.95,
                ),
            ),
            processed_chunks=("chunk_001",),
            review_reasons=(),
        ),
        rule_result=RuleResult(
            item_id="item_1",
            status="matched",
            score_range="2-3",
            allowed_scores=[2, 3],
            max_score=3,
            matched_rules=["health_topic"],
            missing=["health_intent"],
            needs_review=False,
            review_reasons=[],
        ),
        verification_decisions=[
            VerificationDecision(
                object_type="health_topic",
                object_value="2 型糖尿病",
                chunk_id="chunk_001",
                accepted=True,
                confidence=0.95,
                source="skipped",
            )
        ],
    )

    class FakeExplainer:
        def __init__(self, _client):
            pass

        def explain(self, payload, lang="zh"):
            return type(
                "Explanation",
                (),
                {
                    "step1_locate": "证据来自目标章节。",
                    "step2_evidence": ["LLM 自由生成证据"],
                    "step3_reasoning": {
                        "summary": "提及健康主题。",
                        "reasons": [
                            {
                                "point": "目标描述",
                                "explanation": "原文提及健康主题。",
                                "evidence_chunks": ["chunk_001"],
                            }
                        ],
                        "score_justification": "在 2-3 中选择 3。",
                    },
                    "score": 3,
                },
            )()

    monkeypatch.setattr(appraiser.RuleAppraiserAdapter, "_build_llm_client", lambda self: object())
    monkeypatch.setattr("core.rule_engine.appraiser_adapter.run_rule_pipeline", lambda *args, **kwargs: pipeline_result)
    monkeypatch.setattr("core.rule_engine.appraiser_adapter.LLMRuleExplainer", FakeExplainer)

    result = appraiser.RuleAppraiserAdapter.from_evaluator(evaluator).evaluate([chunk])

    assert result["chain_of_thought"]["step2_evidence"] == ["主题：2 型糖尿病。"]
    assert result["rule_engine"]["routing"]["selected_chunks"] == 1
    assert result["rule_engine"]["verification"]["candidate_total"] == 1
    assert result["rule_engine"]["verification"]["candidate_accepted"] == 1


def test_rule_adapter_item_9_uses_evidence_appraisal_rule_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANALYSIS_ENGINE", "rule")
    refresh_settings()

    chunk = SemanticChunk(
        chunk_id="chunk_001",
        title="Quality of evidence",
        content="The certainty of evidence was assessed using GRADE.",
        label=SemanticLabel.METHOD,
        start_page=1,
        end_page=1,
    )
    obj = RuleObject(
        type="evidence_assessment_method",
        value="GRADE",
        evidence=EvidenceRef(
            chunk_id="chunk_001",
            quote="The certainty of evidence was assessed using GRADE.",
        ),
        confidence=0.95,
    )
    pipeline_result = RulePipelineResult(
        item_id="item_9",
        objects=[obj],
        snapshot=ObjectStoreSnapshot(
            item_id="item_9",
            objects=(obj,),
            processed_chunks=("chunk_001",),
            review_reasons=(),
        ),
        rule_result=RuleResult(
            item_id="item_9",
            status="matched",
            score_range="4",
            allowed_scores=[4],
            max_score=4,
            matched_rules=["evidence_assessment_method"],
            missing=[
                "explicit_evidence_level",
                "study_design_description",
                "methodological_limitation",
                "outcome_relevance",
                "result_consistency",
                "result_direction",
                "benefit_harm_magnitude",
                "practice_applicability",
            ],
            needs_review=False,
            review_reasons=[],
        ),
        verification_decisions=[
            VerificationDecision(
                object_type="evidence_assessment_method",
                object_value="GRADE",
                chunk_id="chunk_001",
                accepted=True,
                confidence=0.95,
                source="llm",
            )
        ],
    )

    class FakeExplainer:
        def __init__(self, _client):
            pass

        def explain(self, payload, lang="zh"):
            return type(
                "Explanation",
                (),
                {
                    "step1_locate": "证据来自证据质量章节。",
                    "step2_evidence": ["LLM evidence should be replaced"],
                    "step3_reasoning": {
                        "summary": "提及证据评价方法。",
                        "reasons": [
                            {
                                "point": "证据评价方法",
                                "explanation": "使用 GRADE 评价证据确定性。",
                                "evidence_chunks": ["chunk_001"],
                            }
                        ],
                        "score_justification": "命中一个构件，评分为 2。",
                    },
                    "score": 7,
                },
            )()

    monkeypatch.setattr(RuleAppraiserAdapter, "_build_llm_client", lambda self: object())
    monkeypatch.setattr("core.rule_engine.appraiser_adapter.run_rule_pipeline", lambda *args, **kwargs: pipeline_result)
    monkeypatch.setattr("core.rule_engine.appraiser_adapter.LLMRuleExplainer", FakeExplainer)

    adapter = RuleAppraiserAdapter(
        skill_id="item_9",
        skill_config={"title": "Item 9"},
        domain_id=3,
        lang="zh",
        batch_size=5,
    )
    result = adapter.evaluate([chunk])

    assert result["status"] == "success"
    assert result["chain_of_thought"]["score"] == 4
    assert result["chain_of_thought"]["step2_evidence"] == [
        "The certainty of evidence was assessed using GRADE."
    ]
    assert result["rule_engine"]["used"] is True
    assert result["rule_engine"]["fallback_used"] is False
    assert result["rule_engine"]["profile"] == "evidence_appraisal"
    assert result["rule_engine"]["score_range"] == "4"


def test_rule_adapter_serializes_item12_portfolio_summary():
    adapter = RuleAppraiserAdapter(
        skill_id="item_12",
        skill_config={"title": "Item 12"},
        domain_id=3,
        lang="zh",
        batch_size=5,
    )
    objects = (
        RuleObject(
            type="recommendation_statement",
            value="Offer treatment A",
            evidence=EvidenceRef(chunk_id="chunk_001", quote="Offer treatment A"),
            confidence=0.95,
        ),
        RuleObject(
            type="citation_group",
            value="[1-3]",
            evidence=EvidenceRef(chunk_id="chunk_001", quote="[1-3]"),
            confidence=0.95,
        ),
    )
    group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:1",
        group_type="recommendation_evidence_link",
        objects=objects,
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "portfolio_anchor_key": "rec-a",
            "has_specific_supporting_evidence": True,
            "same_evidence_unit": True,
            "link_type": "citation_group",
            "mapping_strength": "systematic",
        },
    )
    pipeline_result = RulePipelineResult(
        item_id="item_12",
        objects=list(objects),
        snapshot=ObjectStoreSnapshot(
            item_id="item_12",
            objects=objects,
            processed_chunks=("chunk_001",),
            review_reasons=(),
        ),
        rule_result=RuleResult(
            item_id="item_12",
            status="matched",
            score_range="4-5",
            allowed_scores=[4, 5],
            max_score=5,
            matched_rules=["recommendation_statement"],
            missing=[],
            needs_review=False,
            review_reasons=[],
        ),
        grouping_result=RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[]),
    )

    payload = adapter._serialize_rule_engine_output(
        pipeline_result,
        profile="recommendation_evidence_link",
        used=True,
        fallback_used=False,
    )

    portfolio = payload["grouping"]["portfolio"]
    assert portfolio["denominator"] == 1
    assert portfolio["complete_count"] == 1
    assert portfolio["anchors"][0]["anchor_key"] == "rec-a"


def test_rule_adapter_portfolio_summary_counts_rejected_and_omitted_anchors():
    adapter = RuleAppraiserAdapter(
        skill_id="item_12",
        skill_config={"title": "Item 12"},
        domain_id=3,
        lang="zh",
        batch_size=5,
    )
    objects = (
        RuleObject(
            type="recommendation_statement",
            value="Offer treatment A",
            evidence=EvidenceRef(chunk_id="chunk_001", quote="Offer treatment A"),
            confidence=0.95,
        ),
        RuleObject(
            type="citation_group",
            value="[1-3]",
            evidence=EvidenceRef(chunk_id="chunk_001", quote="[1-3]"),
            confidence=0.95,
        ),
    )
    group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:1",
        group_type="recommendation_evidence_link",
        objects=objects,
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "portfolio_anchor_key": "rec-a",
            "portfolio_anchor_cap_exceeded": True,
            "portfolio_anchor_total": 2,
            "portfolio_anchor_kept": 1,
            "portfolio_anchor_omitted": 1,
            "same_recommendation": False,
            "has_specific_supporting_evidence": True,
            "same_evidence_unit": True,
            "link_type": "citation_group",
            "mapping_strength": "systematic",
        },
    )
    pipeline_result = RulePipelineResult(
        item_id="item_12",
        objects=list(objects),
        snapshot=ObjectStoreSnapshot(
            item_id="item_12",
            objects=objects,
            processed_chunks=("chunk_001",),
            review_reasons=(),
        ),
        rule_result=RuleResult(
            item_id="item_12",
            status="matched",
            score_range="1",
            allowed_scores=[1],
            max_score=1,
            matched_rules=[],
            missing=[],
            needs_review=False,
            review_reasons=[],
        ),
        grouping_result=RuleGroupingResult(candidate_groups=[group], accepted_groups=[], decisions=[]),
    )

    payload = adapter._serialize_rule_engine_output(
        pipeline_result,
        profile="recommendation_evidence_link",
        used=True,
        fallback_used=False,
    )

    portfolio = payload["grouping"]["portfolio"]
    assert portfolio["denominator"] == 2
    assert portfolio["complete_count"] == 0
    assert portfolio["uncovered_count"] == 2
    assert portfolio["omitted_count"] == 1
    assert portfolio["anchors"][0]["status"] == "uncovered"


def test_rule_adapter_item11_rejected_same_decision_anchor_serializes_as_partial():
    adapter = RuleAppraiserAdapter(
        skill_id="item_11",
        skill_config={"title": "Item 11"},
        domain_id=3,
        lang="zh",
        batch_size=5,
    )
    objects = (
        RuleObject(
            type="recommendation_or_option",
            value="Use treatment A",
            evidence=EvidenceRef(chunk_id="chunk_001", quote="Use treatment A"),
            confidence=0.95,
        ),
        RuleObject(
            type="health_benefit",
            value="reduces complications",
            evidence=EvidenceRef(chunk_id="chunk_001", quote="reduces complications"),
            confidence=0.95,
        ),
    )
    group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=objects,
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "portfolio_anchor_key": "rec-a",
            "scope": "current_guideline",
            "verifier_accepted": False,
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": False,
            "benefit_has_supporting_data": False,
            "harm_has_supporting_data": False,
            "has_explicit_balance": False,
            "balance_affects_recommendation": False,
        },
    )
    pipeline_result = RulePipelineResult(
        item_id="item_11",
        objects=list(objects),
        snapshot=ObjectStoreSnapshot(
            item_id="item_11",
            objects=objects,
            processed_chunks=("chunk_001",),
            review_reasons=(),
        ),
        rule_result=RuleResult(
            item_id="item_11",
            status="matched",
            score_range="2-3",
            allowed_scores=[2, 3],
            max_score=3,
            matched_rules=["health_benefit"],
            missing=["harm_side_effect_or_risk"],
            needs_review=False,
            review_reasons=[],
        ),
        grouping_result=RuleGroupingResult(candidate_groups=[group], accepted_groups=[], decisions=[]),
    )

    payload = adapter._serialize_rule_engine_output(
        pipeline_result,
        profile="benefit_harm_balance",
        used=True,
        fallback_used=False,
    )

    portfolio = payload["grouping"]["portfolio"]
    assert portfolio["denominator"] == 1
    assert portfolio["partial_count"] == 1
    assert portfolio["uncovered_count"] == 0
    assert portfolio["anchors"][0]["status"] == "partial"
    assert portfolio["anchors"][0]["accepted"] is False


def test_rule_adapter_rubric_appraiser_debug_marks_error_scored():
    adapter = RuleAppraiserAdapter(
        skill_id="item_19",
        skill_config={"title": "Item 19"},
        domain_id=5,
        lang="zh",
        batch_size=5,
    )
    pipeline_result = RulePipelineResult(
        item_id="item_19",
        objects=[],
        snapshot=ObjectStoreSnapshot(
            item_id="item_19",
            objects=(),
            processed_chunks=("chunk_001",),
            review_reasons=(),
        ),
        rule_result=RuleResult(
            item_id="item_19",
            status="matched",
            score_range="1",
            allowed_scores=[1],
            max_score=1,
            matched_rules=[],
            missing=["scoreable_rubric_evidence"],
            needs_review=True,
            review_reasons=[
                ReviewReason(
                    code="rubric_appraiser_json_failed",
                    message="failed",
                    severity="error",
                )
            ],
        ),
        rubric_appraiser_result=RubricAppraiserResult(
            candidate_units=[],
            decision=RubricAppraiserDecision(
                score=1,
                confidence=0.0,
                decision_label="rubric_appraiser_failed",
                rationale="failed",
                why_not_higher="failed",
                evidence=[],
            ),
            review_reasons=[
                ReviewReason(
                    code="rubric_appraiser_json_failed",
                    message="failed",
                    severity="error",
                )
            ],
        ),
    )

    payload = adapter._serialize_rule_engine_output(
        pipeline_result,
        profile="rubric_appraiser",
        used=True,
        fallback_used=False,
    )

    assert payload["rubric"]["status"] == "error_scored"
    assert payload["rubric"]["decision_label"] == "rubric_appraiser_failed"
