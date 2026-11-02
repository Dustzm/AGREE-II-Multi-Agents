from __future__ import annotations

import pytest

from core.rule_engine.models import (
    EvidenceRef,
    GroupVerificationDecision,
    RuleGroupingResult,
    RuleObject,
    RuleObjectGroup,
)
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.stores.object_store import ObjectStore


def _object(object_type: str, value: str | None = None, *, chunk_id: str = "chunk_001") -> RuleObject:
    return RuleObject(
        type=object_type,
        value=value or object_type,
        evidence=EvidenceRef(chunk_id=chunk_id, quote=value or object_type),
        confidence=0.95,
    )


def _group(
    objects: list[RuleObject],
    *,
    attributes: dict | None = None,
    index: int = 1,
) -> RuleObjectGroup:
    chunk_ids = tuple(dict.fromkeys(obj.evidence.chunk_id for obj in objects))
    return RuleObjectGroup(
        group_id=f"item_16:management_options:{index}",
        group_type="management_options",
        objects=tuple(objects),
        chunk_ids=chunk_ids,
        relation_hints=("same_section",),
        attributes=attributes or {},
    )


def _grouping_result(groups: list[RuleObjectGroup]) -> RuleGroupingResult:
    return RuleGroupingResult(
        candidate_groups=groups,
        accepted_groups=groups,
        decisions=[
            GroupVerificationDecision(
                group_id=group.group_id,
                group_type=group.group_type,
                accepted=True,
                confidence=0.95,
                source="llm",
                group_index=index,
                attributes=dict(group.attributes),
            )
            for index, group in enumerate(groups)
        ],
    )


def _evaluate(groups: list[RuleObjectGroup]):
    spec = get_item_spec("item_16")
    store = ObjectStore(spec)
    objects = [obj for group in groups for obj in group.objects]
    store.merge(objects, processed_chunks=[chunk_id for group in groups for chunk_id in group.chunk_ids])
    return get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), _grouping_result(groups))


def test_item_16_no_options_scores_one():
    spec = get_item_spec("item_16")
    store = ObjectStore(spec)

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert result.score_range == "1"


def test_item_16_single_option_group_scores_one():
    result = _evaluate([
        _group(
            [_object("management_option", "metformin")],
            attributes={"same_issue": True, "option_count": 1, "scope": "current_guideline"},
        )
    ])

    assert result.score_range == "1"


def test_item_16_multiple_options_without_conditions_scores_two_three():
    result = _evaluate([
        _group(
            [
                _object("clinical_issue_or_decision_point", "initial treatment"),
                _object("management_option", "metformin"),
                _object("management_option", "insulin"),
            ],
            attributes={"same_issue": True, "option_count": 2, "scope": "current_guideline"},
        )
    ])

    assert result.score_range == "2-3"
    assert result.missing == ["option_condition_or_population"]


def test_item_16_options_with_conditions_scores_four_five():
    result = _evaluate([
        _group(
            [
                _object("clinical_issue_or_decision_point", "initial treatment"),
                _object("management_option", "metformin"),
                _object("management_option", "insulin"),
                _object("option_condition_or_population", "if HbA1c remains above target"),
            ],
            attributes={
                "same_issue": True,
                "option_count": 2,
                "has_conditions": True,
                "scope": "current_guideline",
            },
        )
    ])

    assert result.score_range == "4-5"


@pytest.mark.parametrize(
    "extra_object",
    [
        _object("preference_order", "first-line"),
        _object("selection_criteria", "choose based on contraindications"),
        _object("decision_pathway_or_algorithm", "if/then algorithm"),
    ],
)
def test_item_16_options_with_preference_selection_or_pathway_scores_six_seven(extra_object):
    result = _evaluate([
        _group(
            [
                _object("clinical_issue_or_decision_point", "initial treatment"),
                _object("management_option", "metformin"),
                _object("management_option", "insulin"),
                _object("option_condition_or_population", "if HbA1c remains above target"),
                extra_object,
            ],
            attributes={
                "same_issue": True,
                "option_count": 2,
                "has_conditions": True,
                "has_preference_or_pathway": True,
                "scope": "current_guideline",
            },
        )
    ])

    assert result.score_range == "6-7"


def test_item_16_external_or_different_issue_group_does_not_score_high():
    external = _group(
        [
            _object("management_option", "metformin"),
            _object("management_option", "insulin"),
            _object("preference_order", "first-line"),
        ],
        attributes={"same_issue": True, "option_count": 2, "scope": "external_or_evidence_only"},
    )
    different_issue = _group(
        [
            _object("management_option", "screening", chunk_id="chunk_010"),
            _object("management_option", "treatment", chunk_id="chunk_011"),
        ],
        attributes={"same_issue": False, "option_count": 2, "scope": "current_guideline"},
        index=2,
    )

    result = _evaluate([external, different_issue])

    assert result.score_range == "1"
    assert result.needs_review is True


def test_item_16_unclear_scope_group_does_not_score():
    unclear = _group(
        [
            _object("management_option", "metformin"),
            _object("management_option", "insulin"),
            _object("preference_order", "first-line"),
        ],
        attributes={
            "same_issue": True,
            "option_count": 2,
            "has_preference_or_pathway": True,
            "scope": "unclear",
        },
    )

    result = _evaluate([unclear])

    assert result.score_range == "1"
