from __future__ import annotations

import pytest

from core.rule_engine.models import EvidenceChunk, EvidenceRef, RuleObject
from core.rule_engine.prompts.extraction import render_extraction_prompt
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.stores.object_store import ObjectStore


def _rule_object(
    object_type: str,
    value: str | None = None,
    *,
    attributes: dict[str, str] | None = None,
) -> RuleObject:
    return RuleObject(
        type=object_type,
        value=value or object_type,
        evidence=EvidenceRef(chunk_id="chunk_001", quote="direct quote"),
        confidence=0.95,
        attributes=attributes or {},
    )


def _evaluate_item_14(object_types: list[str]):
    spec = get_item_spec("item_14")
    store = ObjectStore(spec)
    store.merge([_rule_object(object_type) for object_type in object_types], processed_chunks=["chunk_001"])
    snapshot = store.snapshot()
    return get_rule_engine(spec.rule_profile).execute(spec, snapshot)


@pytest.mark.parametrize(
    ("object_types", "score_range", "allowed_scores", "missing"),
    [
        ([], "1", [1], ["future_update_mention"]),
        (["update_mention"], "2-3", [2, 3], [
            "update_interval_or_review_date",
            "update_process_or_responsible_body",
            "update_trigger_or_criteria",
        ]),
        (["update_interval"], "4-5", [4, 5], [
            "update_process_or_responsible_body",
            "update_trigger_or_criteria",
        ]),
        (["update_condition"], "4-5", [4, 5], [
            "update_interval_or_review_date",
            "update_trigger_or_criteria",
        ]),
        (["update_interval", "update_criteria"], "6", [6], ["update_process_or_responsible_body"]),
        (["update_interval", "update_criteria", "update_condition"], "5-6", [5, 6], []),
    ],
)
def test_item_14_guideline_update_engine_scores_expected_ranges(
    object_types,
    score_range,
    allowed_scores,
    missing,
):
    result = _evaluate_item_14(object_types)

    assert result.score_range == score_range
    assert result.allowed_scores == allowed_scores
    assert result.max_score == max(allowed_scores)
    assert result.missing == missing
    assert result.needs_review is False


def test_item_14_extraction_prompt_requires_guideline_self_update():
    prompt = render_extraction_prompt(
        get_item_spec("item_14"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Screening guidelines",
                content="These protocols should be regularly reevaluated and updated.",
            )
        ],
    )

    assert "本指南" in prompt
    assert "自身" in prompt
    assert "外部筛查 protocol" in prompt
    assert "外部 screening guideline" in prompt
    assert "除非文本明确说明更新对象就是本指南自身" in prompt
    assert "文献检索日期" in prompt
    assert "仅说明当前文档是 updated/revised statement/guideline 不足以抽取更新程序对象" in prompt


def test_item_14_uses_guideline_update_engine_without_grouping():
    spec = get_item_spec("item_14")

    assert spec.rule_profile == "guideline_update"
    assert spec.grouping is None
    assert spec.object_specs["update_interval"].verification is not None
    assert spec.object_specs["update_interval"].verification.llm_required is True


def test_item_14_exclusion_objects_do_not_score():
    result = _evaluate_item_14([
        "current_revision_only",
        "search_date_only",
        "external_guideline_update",
        "generic_methodology_manual_requirement",
    ])

    assert result.score_range == "1"
    assert {reason.code for reason in result.review_reasons} == {"excluded_guideline_update_scope"}


def test_item_14_series_update_mechanism_is_capped_below_seven():
    spec = get_item_spec("item_14")
    store = ObjectStore(spec)
    store.merge(
        [
            _rule_object(
                "series_or_living_guideline_update_mechanism",
                attributes={"scope": "guideline_series"},
            ),
            _rule_object("update_interval"),
            _rule_object("update_trigger_or_criteria"),
            _rule_object("update_responsible_body"),
        ],
        processed_chunks=["chunk_001"],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert result.score_range == "5-6"
    assert result.allowed_scores == [5, 6]


def test_item_14_three_categories_without_current_document_anchor_are_capped_below_seven():
    result = _evaluate_item_14([
        "update_interval",
        "update_trigger_or_criteria",
        "update_responsible_body",
    ])

    assert result.score_range == "5-6"
    assert result.allowed_scores == [5, 6]


def test_item_14_current_document_mechanism_can_score_seven():
    result = _evaluate_item_14([
        "current_document_future_update_mechanism",
        "update_interval",
        "update_trigger_or_criteria",
        "update_responsible_body",
    ])

    assert result.score_range == "7"
