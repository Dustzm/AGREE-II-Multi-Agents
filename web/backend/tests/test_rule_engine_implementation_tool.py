from __future__ import annotations

import json

from core.rule_engine.grouping import LLMRuleObjectGroupVerifier
from core.rule_engine.models import EvidenceChunk, EvidenceRef, RuleGroupingResult, RuleObject, RuleObjectGroup
from core.rule_engine.prompts.grouping import render_group_verification_prompt
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.stores.object_store import ObjectStore


def _object(object_type: str, *, chunk_id: str = "chunk_001") -> RuleObject:
    return RuleObject(
        type=object_type,
        value=object_type,
        evidence=EvidenceRef(chunk_id=chunk_id, quote=object_type),
        confidence=0.95,
    )


def test_item_19_active_profile_is_implementation_tool():
    spec = get_item_spec("item_19")

    assert spec.rule_profile == "implementation_tool"
    assert spec.grouping is not None
    assert spec.grouping.group_profile == "implementation_tool"
    assert spec.grouping.max_groups == 8
    assert get_rule_engine("implementation_tool") is not None


def test_item_19_group_prompt_uses_compact_candidate_payload():
    spec = get_item_spec("item_19")
    long_text = "Appendix checklist instructions. " * 80
    objects = [
        RuleObject(
            type="eligible_implementation_tool",
            value="checklist " + long_text,
            evidence=EvidenceRef(chunk_id="chunk_001", quote="quote " + long_text),
            confidence=0.95,
        )
    ]
    group = RuleObjectGroup(
        group_id="item_19:implementation_tool:1",
        group_type="implementation_tool",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
    )
    prompt = render_group_verification_prompt(
        spec,
        [group],
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Implementation",
                content=long_text,
            )
        ],
    )

    assert "Appendix checklist instructions. " * 30 not in prompt
    assert "same_support_item" in prompt


def test_item_19_group_verifier_splits_large_candidate_set():
    spec = get_item_spec("item_19")
    groups = [
        RuleObjectGroup(
            group_id=f"item_19:implementation_tool:{index}",
            group_type="implementation_tool",
            objects=(_object("eligible_implementation_tool"),),
            chunk_ids=("chunk_001",),
            relation_hints=("same_chunk",),
        )
        for index in range(9)
    ]
    calls: list[int] = []

    class FakeClient:
        def complete_json(self, **kwargs):
            payload = json.loads(kwargs["user_prompt"].split("## Candidates", 1)[1].split("```json", 1)[1].split("```", 1)[0])
            calls.append(len(payload))
            return {
                "decisions": [
                    {
                        "index": index,
                        "accepted": True,
                        "confidence": 0.9,
                        "rejection_code": "",
                        "reason": "accepted",
                        "attributes": {
                            "scope": "current_guideline",
                            "rubric_role": "concrete_tool",
                            "support_form": "concrete_aid",
                            "implementation_connection": "provided_or_recommended_for_this_guideline",
                            "implementation_purpose": "put_guideline_recommendations_into_practice",
                            "actionability": "mentioned_only",
                            "same_support_item": True,
                        },
                    }
                    for index in range(len(payload))
                ]
            }

    result = LLMRuleObjectGroupVerifier(FakeClient()).verify(
        spec,
        groups,
        [EvidenceChunk(chunk_id="chunk_001", title="Implementation", content="Checklist.")],
    )

    assert calls == [4, 4, 1]
    assert len(result.decisions) == 9
    assert [decision.group_index for decision in result.decisions] == list(range(9))


def test_item_19_scores_complete_current_guideline_tool_group_as_seven():
    spec = get_item_spec("item_19")
    store = ObjectStore(spec)
    objects = [
        _object("eligible_implementation_tool"),
        _object("current_guideline_tool_connection"),
        _object("tool_use_instruction"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_19:implementation_tool:1",
        group_type="implementation_tool",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "rubric_role": "concrete_tool",
            "support_form": "concrete_aid",
            "implementation_connection": "provided_or_recommended_for_this_guideline",
            "implementation_purpose": "put_guideline_recommendations_into_practice",
            "actionability": "clear_use_instruction",
            "same_support_item": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "7"
    assert result.allowed_scores == [7]
    assert result.matched_rules == [
        "eligible_implementation_tool",
        "current_guideline_tool_connection",
        "tool_use_instruction",
    ]


def test_item_19_clear_usage_attribute_without_usage_object_cannot_score_seven():
    spec = get_item_spec("item_19")
    store = ObjectStore(spec)
    objects = [
        _object("implementation_section_or_module"),
        _object("eligible_implementation_tool"),
        _object("current_guideline_tool_connection"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_19:implementation_tool:1",
        group_type="implementation_tool",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "rubric_role": "concrete_tool",
            "support_form": "concrete_aid",
            "implementation_connection": "provided_or_recommended_for_this_guideline",
            "implementation_purpose": "put_guideline_recommendations_into_practice",
            "actionability": "clear_use_instruction",
            "same_support_item": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "5-6"
    assert "tool_use_instruction" in result.missing


def test_item_19_clinical_algorithm_object_cannot_be_promoted_to_concrete_tool_by_attributes():
    spec = get_item_spec("item_19")
    store = ObjectStore(spec)
    objects = [
        _object("clinical_algorithm_or_pathway"),
        _object("current_guideline_tool_connection"),
        _object("tool_use_instruction"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_19:implementation_tool:1",
        group_type="implementation_tool",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "rubric_role": "concrete_tool",
            "support_form": "concrete_aid",
            "implementation_connection": "provided_or_recommended_for_this_guideline",
            "implementation_purpose": "put_guideline_recommendations_into_practice",
            "actionability": "clear_use_instruction",
            "same_support_item": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "2-4"
    assert "eligible_implementation_tool" in result.missing


def test_item_19_scores_empty_verified_groups_as_one():
    spec = get_item_spec("item_19")
    store = ObjectStore(spec)
    grouping = RuleGroupingResult(candidate_groups=[], accepted_groups=[], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "1"
    assert result.missing == [
        "implementation_advice_or_discussion",
        "tool_like_content",
        "eligible_implementation_tool",
    ]
