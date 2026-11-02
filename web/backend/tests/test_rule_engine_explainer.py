from __future__ import annotations

import pytest

from core.rule_engine.explainers.llm import LLMRuleExplainer
from core.rule_engine.explainers.input_builder import build_rule_explanation_input
from core.rule_engine.explainers.models import (
    CriterionEvidence,
    EvidenceSnippet,
    RuleExplanationInput,
)
from core.rule_engine.models import (
    EvidenceChunk,
    EvidenceRef,
    ItemSpec,
    ObjectStoreSnapshot,
    RuleObject,
    RuleObjectSpec,
    RulePipelineResult,
    RuleResult,
)
from core.rule_engine.prompts.explanation import render_explanation_prompt, sanitize_retry_error


def _payload() -> RuleExplanationInput:
    return RuleExplanationInput(
        item_id="item_1",
        title="目标",
        scoring_principle=[{"range": "6-7", "condition": "完整描述"}],
        score_range="6-7",
        allowed_scores=[6, 7],
        needs_review=False,
        matched_criteria=(
            CriterionEvidence(
                description="健康主题",
                evidence=(
                    EvidenceSnippet(
                        chunk_id="chunk_001",
                        title="目标",
                        quote="本指南针对 2 型糖尿病患者。",
                    ),
                ),
            ),
        ),
        missing_criteria=(),
        internal_terms=("health_topic", "target_population"),
    )


def _score_one_payload() -> RuleExplanationInput:
    return RuleExplanationInput(
        item_id="item_1",
        title="目标",
        scoring_principle=[{"range": "1", "condition": "未提及"}],
        score_range="1",
        allowed_scores=[1],
        needs_review=False,
        matched_criteria=(),
        missing_criteria=(),
        internal_terms=("health_topic", "target_population"),
    )


class _FakeClient:
    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.calls = 0
        self.prompts: list[str] = []

    def complete_json(self, *, stage: str, system_prompt: str, user_prompt: str, max_tokens: int, **_: object) -> dict:
        self.calls += 1
        self.prompts.append(user_prompt)
        return self.responses.pop(0)


def _valid_response(score: int = 7, chunk_id: str = "chunk_001", point: str = "目标描述") -> dict:
    return {
        "step1_locate": "证据来自目标章节。",
        "step2_evidence": ["本指南针对 2 型糖尿病患者。"],
        "step3_reasoning": {
            "summary": "目标描述充分。",
            "reasons": [
                {
                    "point": point,
                    "explanation": "原文明确说明指南关注的人群和主题。",
                    "evidence_chunks": [chunk_id],
                }
            ],
            "score_justification": "在允许分数中选择 7 分。",
        },
        "score": score,
    }


def _score_one_response(evidence_chunks: list[str] | None = None, point: str = "未提及") -> dict:
    return {
        "step1_locate": "已检查相关章节，未发现对应信息。",
        "step2_evidence": ["LLM 生成的证据不会被 adapter 信任"],
        "step3_reasoning": {
            "summary": "未发现该条目要求的信息。",
            "reasons": [
                {
                    "point": point,
                    "explanation": "相关内容中未提及该评分要素。",
                    "evidence_chunks": evidence_chunks if evidence_chunks is not None else [],
                }
            ],
            "score_justification": "规则结果为未提及，因此评分为 1。",
        },
        "score": 1,
    }


def test_explainer_accepts_valid_output():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response()))

    result = explainer.explain(_payload(), lang="zh")

    assert result.score == 7
    assert result.step3_reasoning["reasons"][0]["evidence_chunks"] == ["chunk_001"]


def test_explainer_rejects_score_outside_allowed_scores():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response(score=5)), max_retries=1)

    with pytest.raises(ValueError, match="allowed_scores"):
        explainer.explain(_payload(), lang="zh")


def test_explainer_rejects_unknown_chunk_id():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response(chunk_id="chunk_999")), max_retries=1)

    with pytest.raises(ValueError, match="invalid evidence_chunks"):
        explainer.explain(_payload(), lang="zh")


def test_explainer_rejects_internal_fact_leakage():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response(point="health_topic")), max_retries=1)

    with pytest.raises(ValueError, match="internal terms"):
        explainer.explain(_payload(), lang="zh")


def test_explainer_allows_natural_internal_term_variants():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response(point="target population")), max_retries=1)

    result = explainer.explain(_payload(), lang="zh")

    assert result.step3_reasoning["reasons"][0]["point"] == "target population"


def test_explainer_allows_natural_agree_terms():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response(point="study design and language")), max_retries=1)

    result = explainer.explain(_payload(), lang="zh")

    assert result.step3_reasoning["reasons"][0]["point"] == "study design and language"


def test_explainer_allows_plain_word_object_keys_as_natural_terms():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response(score=4, point="language")), max_retries=1)
    payload = RuleExplanationInput(
        item_id="item_8",
        title="证据选择标准",
        scoring_principle=[{"range": "3-4", "condition": "mentions language limits"}],
        score_range="3-4",
        allowed_scores=[3, 4],
        needs_review=False,
        matched_criteria=_payload().matched_criteria,
        missing_criteria=(),
        internal_terms=("language",),
    )

    result = explainer.explain(payload, lang="zh")

    assert result.step3_reasoning["reasons"][0]["point"] == "language"


def test_explainer_rejects_code_like_identifier_in_string_values_only():
    explainer = LLMRuleExplainer(_FakeClient(_valid_response(point="matched_rules")), max_retries=1)

    with pytest.raises(ValueError, match="code-like identifiers"):
        explainer.explain(_payload(), lang="zh")


def test_explanation_prompt_excludes_object_values_and_review_notes():
    prompt = render_explanation_prompt(_payload(), lang="zh")

    assert '"value"' not in prompt
    assert "review_notes" not in prompt


def test_explainer_allows_empty_citations_for_score_one():
    explainer = LLMRuleExplainer(_FakeClient(_score_one_response()), max_retries=1)

    result = explainer.explain(_score_one_payload(), lang="zh")

    assert result.score == 1
    assert result.step3_reasoning["reasons"][0]["evidence_chunks"] == []


def test_explainer_rejects_unknown_chunk_even_for_score_one():
    explainer = LLMRuleExplainer(_FakeClient(_score_one_response(["chunk_999"])), max_retries=1)

    with pytest.raises(ValueError, match="invalid evidence_chunks"):
        explainer.explain(_score_one_payload(), lang="zh")


def test_explainer_rejects_empty_citations_above_score_one():
    response = _valid_response()
    response["step3_reasoning"]["reasons"][0]["evidence_chunks"] = []
    explainer = LLMRuleExplainer(_FakeClient(response), max_retries=1)

    with pytest.raises(ValueError, match="non-empty"):
        explainer.explain(_payload(), lang="zh")


def test_explainer_retries_invalid_output():
    client = _FakeClient(_valid_response(score=5), _valid_response(score=6))
    explainer = LLMRuleExplainer(client, max_retries=2)

    result = explainer.explain(_payload(), lang="zh")

    assert result.score == 6
    assert client.calls == 2
    assert "Retry Rule Engine Explanation" in client.prompts[1]
    assert "allowed_scores" in client.prompts[1]
    assert "Return JSON only" in client.prompts[1]
    assert "scoring_principle" not in client.prompts[1]


def test_retry_prompt_sanitizes_internal_identifiers():
    client = _FakeClient(_valid_response(point="health_topic"), _valid_response(score=6))
    explainer = LLMRuleExplainer(client, max_retries=2)

    result = explainer.explain(_payload(), lang="zh")

    assert result.score == 6
    assert "health_topic" not in client.prompts[1]
    assert "target_population" not in client.prompts[1]
    assert "matched_rules" not in sanitize_retry_error("bad matched_rules and ['health_topic']")


def test_input_builder_limits_explainer_evidence_volume():
    spec = ItemSpec(
        item_id="item_test",
        title="Test",
        rule_profile="ordered_milestone",
        object_specs={
            "A": RuleObjectSpec(type="A", role="component", description="A"),
            "B": RuleObjectSpec(type="B", role="component", description="B"),
            "C": RuleObjectSpec(type="C", role="component", description="C"),
            "D": RuleObjectSpec(type="D", role="component", description="D"),
            "E": RuleObjectSpec(type="E", role="component", description="E"),
        },
    )
    objects = []
    chunk_by_id = {}
    for object_type in spec.object_specs:
        for index in range(5):
            chunk_id = f"chunk_{object_type}_{index}"
            quote = f"{object_type} quote {index}"
            objects.append(
                RuleObject(
                    type=object_type,
                    value=quote,
                    evidence=EvidenceRef(chunk_id=chunk_id, quote=quote),
                    confidence=0.9,
                )
            )
            chunk_by_id[chunk_id] = EvidenceChunk(chunk_id=chunk_id, title=chunk_id, content=quote)

    payload = build_rule_explanation_input(
        spec,
        RulePipelineResult(
            item_id=spec.item_id,
            objects=objects,
            snapshot=ObjectStoreSnapshot(
                item_id=spec.item_id,
                objects=tuple(objects),
                processed_chunks=tuple(chunk_by_id),
            ),
            rule_result=RuleResult(
                item_id=spec.item_id,
                status="matched",
                score_range="7",
                allowed_scores=[7],
                max_score=7,
                matched_rules=list(spec.object_specs),
                missing=[],
                needs_review=False,
            ),
        ),
        chunk_by_id=chunk_by_id,
        scoring_principle=[],
    )

    evidence_counts = [len(criterion.evidence) for criterion in payload.matched_criteria]
    assert max(evidence_counts) <= 3
    assert sum(evidence_counts) == 12
