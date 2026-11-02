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


_PICOS_TYPES = {
    "P": "question_population_or_problem",
    "I": "question_intervention_or_exposure",
    "C": "question_comparator",
    "O": "question_outcome",
    "S": "question_study_design",
}


def _object(object_type: str, value: str | None = None, *, chunk_id: str = "chunk_001") -> RuleObject:
    return RuleObject(
        type=object_type,
        value=value or object_type,
        evidence=EvidenceRef(chunk_id=chunk_id, quote=value or object_type),
        confidence=0.95,
    )


def _group(
    letters: str,
    *,
    framing: str,
    index: int = 1,
    same_question: bool = True,
    scope: str = "current_guideline",
) -> RuleObjectGroup:
    objects = [
        _object("health_question_statement" if framing != "implicit" else "implicit_question_scope"),
        *[_object(_PICOS_TYPES[letter]) for letter in letters],
    ]
    return RuleObjectGroup(
        group_id=f"item_2:health_question:{index}",
        group_type="health_question",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "same_question": same_question,
            "question_framing": framing,
            "scope": scope,
            "picos_present": ",".join(letters),
        },
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
    spec = get_item_spec("item_2")
    store = ObjectStore(spec)
    objects = [obj for group in groups for obj in group.objects]
    store.merge(objects, processed_chunks=[chunk_id for group in groups for chunk_id in group.chunk_ids])
    return get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), _grouping_result(groups))


@pytest.mark.parametrize(
    ("letters", "score_range"),
    [
        ("P", "2-4"),
        ("I", "2-4"),
        ("PI", "5-7"),
    ],
)
def test_item_2_background_question_scores_by_population_and_intervention(letters, score_range):
    result = _evaluate([_group(letters, framing="background")])

    assert result.score_range == score_range


@pytest.mark.parametrize(
    ("letters", "score_range"),
    [
        ("PI", "2-3"),
        ("PIC", "4-5"),
        ("PICO", "6"),
        ("PICOS", "7"),
    ],
)
def test_item_2_foreground_question_scores_by_picos_count(letters, score_range):
    result = _evaluate([_group(letters, framing="foreground")])

    assert result.score_range == score_range


@pytest.mark.parametrize(
    ("letters", "score_range"),
    [
        ("PI", "4-5"),
        ("PIC", "4-5"),
        ("PICO", "6-7"),
        ("PICOS", "6-7"),
    ],
)
def test_item_2_implicit_question_scores_by_pico_count(letters, score_range):
    result = _evaluate([_group(letters, framing="implicit")])

    assert result.score_range == score_range


@pytest.mark.parametrize(
    "group",
    [
        _group("PICOS", framing="foreground", same_question=False),
        _group("PICOS", framing="foreground", scope="external_or_cited"),
        _group("PICOS", framing="foreground", scope="evidence_selection_only"),
        _group("PICOS", framing="foreground", scope="unclear"),
        _group("PICOS", framing="not_question"),
    ],
)
def test_item_2_rejected_scope_or_framing_does_not_score(group):
    result = _evaluate([group])

    assert result.score_range == "1"
    assert result.needs_review is True


@pytest.mark.parametrize(
    "picos_present",
    ["PICOS", "PICO", "P I C O", "P;I;C;O", "P|I|C|O"],
)
def test_item_2_picos_present_accepts_common_llm_string_formats(picos_present):
    group = _group("P", framing="implicit")
    group = RuleObjectGroup(
        group_id=group.group_id,
        group_type=group.group_type,
        objects=group.objects,
        chunk_ids=group.chunk_ids,
        relation_hints=group.relation_hints,
        attributes={**group.attributes, "picos_present": picos_present},
    )

    result = _evaluate([group])

    assert result.score_range == "6-7"
