from __future__ import annotations

import pytest

from core.rule_engine.engines.profiles import BranchingPathEngine
from core.rule_engine.models import (
    BranchCase,
    BranchingRule,
    EvidenceChunk,
    EvidenceRef,
    ItemSpec,
    GroupVerificationDecision,
    RuleGroupingResult,
    RuleObject,
    RuleObjectGroup,
    RuleObjectSpec,
    all_of,
)
from core.rule_engine.prompts.extraction import render_extraction_prompt
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.stores.object_store import ObjectStore


def _rule_object(object_type: str, value: str | None = None) -> RuleObject:
    return RuleObject(
        type=object_type,
        value=value or object_type,
        evidence=EvidenceRef(chunk_id="chunk_001", quote=object_type),
        confidence=0.95,
    )


def _evaluate(item_id: str, object_types: list[str]):
    spec = get_item_spec(item_id)
    store = ObjectStore(spec)
    store.merge([_rule_object(object_type) for object_type in object_types], processed_chunks=["chunk_001"])
    snapshot = store.snapshot()
    return get_rule_engine(spec.rule_profile).execute(spec, snapshot)


def _grouping_result(group: RuleObjectGroup) -> RuleGroupingResult:
    return RuleGroupingResult(
        candidate_groups=[group],
        accepted_groups=[group],
        decisions=[
            GroupVerificationDecision(
                group_id=group.group_id,
                group_type=group.group_type,
                accepted=True,
                confidence=0.95,
                source="llm",
                group_index=0,
                attributes=dict(group.attributes),
            )
        ],
    )


def _funding_group(
    object_types: list[str],
    *,
    attributes: dict | None = None,
    chunk_id: str = "chunk_001",
) -> RuleObjectGroup:
    return RuleObjectGroup(
        group_id="item_22:funding_disclosure:1",
        group_type="funding_disclosure",
        objects=tuple(
            RuleObject(
                type=object_type,
                value=object_type,
                evidence=EvidenceRef(chunk_id=chunk_id, quote=object_type),
                confidence=0.95,
            )
            for object_type in object_types
        ),
        chunk_ids=(chunk_id,),
        relation_hints=("same_chunk",),
        attributes=attributes or {},
    )


@pytest.mark.parametrize(
    ("object_types", "score_range", "allowed_scores", "needs_review"),
    [
        ([], "1", [1], False),
        (["no_funding_statement"], "7", [7], False),
        (["funding_disclosed"], "2-4", [2, 3, 4], True),
        (["commission_or_sponsorship_statement"], "2-4", [2, 3, 4], True),
        (["related_meeting_or_convening_funding"], "1", [1], True),
        (["funding_disclosed", "direct_no_influence_statement"], "7", [7], False),
        (["funding_body_independence_statement"], "7", [7], False),
        (["funding_disclosed", "sponsor_influence_statement"], "5-6", [5, 6], False),
        (["funding_disclosed", "for_profit_funding"], "2-4", [2, 3, 4], False),
        (["funding_disclosed", "non_profit_or_government_funding"], "5-6", [5, 6], False),
        (["funding_disclosed", "no_industry_support_statement"], "5-6", [5, 6], False),
        (["funding_disclosed", "generic_editorial_independence_statement"], "2-4", [2, 3, 4], False),
    ],
)
def test_item_22_disclosure_engine_scores_expected_ranges(
    object_types,
    score_range,
    allowed_scores,
    needs_review,
):
    result = _evaluate("item_22", object_types)

    assert result.status == "matched"
    assert result.score_range == score_range
    assert result.allowed_scores == allowed_scores
    assert result.needs_review is needs_review


def test_item_22_no_influence_has_priority_over_funding_type():
    result = _evaluate(
        "item_22",
        ["funding_disclosed", "for_profit_funding", "direct_no_influence_statement"],
    )

    assert result.score_range == "7"
    assert result.matched_rules == ["funding_disclosed", "direct_no_influence_statement"]


@pytest.mark.parametrize(
    ("group", "score_range", "needs_review"),
    [
        (
            _funding_group(["no_funding_statement"], attributes={"status": "none", "scope": "current_manuscript"}),
            "7",
            False,
        ),
        (
            _funding_group(
                ["funding_section_heading", "funding_section_value"],
                attributes={
                    "status": "funded",
                    "scope": "current_manuscript",
                    "funder_type": "for_profit",
                    "influence": "none",
                },
            ),
            "2-4",
            False,
        ),
        (
            _funding_group(
                ["funding_disclosed", "non_profit_or_government_funding"],
                attributes={
                    "status": "funded",
                    "scope": "current_guideline",
                    "funder_type": "non_profit_or_government",
                    "influence": "none",
                },
            ),
            "5-6",
            False,
        ),
        (
            _funding_group(
                ["funding_disclosed", "no_industry_support_statement"],
                attributes={
                    "status": "funded",
                    "scope": "current_guideline",
                    "funder_type": "unknown",
                    "influence": "no_industry_support",
                },
            ),
            "5-6",
            False,
        ),
        (
            _funding_group(
                ["funding_disclosed", "funding_body_independence_statement"],
                attributes={
                    "status": "funded",
                    "scope": "current_guideline",
                    "funder_type": "unknown",
                    "influence": "funding_body_independence",
                },
            ),
            "7",
            False,
        ),
        (
            _funding_group(
                ["development_meeting_funding_statement"],
                attributes={"status": "meeting_only", "scope": "development_meeting"},
            ),
            "2-4",
            True,
        ),
        (
            _funding_group(
                ["related_meeting_or_convening_funding"],
                attributes={"status": "meeting_only", "scope": "related_only"},
            ),
            "1",
            True,
        ),
    ],
)
def test_item_22_grouped_disclosure_engine_scores_expected_ranges(group, score_range, needs_review):
    spec = get_item_spec("item_22")
    store = ObjectStore(spec)
    store.merge(list(group.objects), processed_chunks=list(group.chunk_ids))

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), _grouping_result(group))

    assert result.score_range == score_range
    assert result.needs_review is needs_review


def test_item_22_extraction_prompt_requires_funding_disclosed_for_general_revenue_support():
    spec = get_item_spec("item_22")
    prompt = render_extraction_prompt(
        spec,
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Funding",
                content=(
                    "The ADA funds development of the Standards of Care from its general "
                    "revenues, with no industry involvement or support."
                ),
            )
        ],
    )

    assert "funds development of the Standards/guideline" in prompt
    assert "funded from general revenues" in prompt
    assert "Funding: None" in prompt
    assert "Financial support: none" in prompt
    assert "no external funding" in prompt
    assert "必须抽取 funding_disclosed" in prompt
    assert "commission_or_sponsorship_statement" in prompt
    assert "direct_no_influence_statement" in prompt
    assert "funding_body_independence_statement" in prompt
    assert "no_industry_support_statement" in prompt
    assert "generic_editorial_independence_statement" in prompt
    assert "related_meeting_or_convening_funding" in prompt
    assert "不能单独当作 no_funding_statement" in prompt


@pytest.mark.parametrize(
    ("object_types", "score_range", "allowed_scores"),
    [
        ([], "1", [1]),
        (["coi_mention"], "2-4", [2, 3, 4]),
        (["coi_collection_process"], "2-4", [2, 3, 4]),
        (["no_conflicts_statement"], "5", [5]),
        (["coi_disclosure_result"], "5", [5]),
        (["conflicts_present"], "5", [5]),
        (["coi_collection_process", "coi_disclosure_result"], "5-6", [5, 6]),
        (["coi_collection_process", "coi_disclosure_result", "no_conflicts_statement"], "7", [7]),
        (
            ["coi_collection_process", "no_conflicts_statement", "coi_no_influence_statement"],
            "7",
            [7],
        ),
        (
            ["coi_collection_process", "conflicts_present"],
            "5-6",
            [5, 6],
        ),
        (
            ["coi_collection_process", "conflicts_present", "coi_reviewed_or_discussed"],
            "5-6",
            [5, 6],
        ),
        (
            [
                "coi_collection_process",
                "coi_disclosure_result",
                "coi_management_action",
            ],
            "5-6",
            [5, 6],
        ),
        (
            [
                "coi_collection_process",
                "conflicts_present",
                "coi_management_action",
            ],
            "7",
            [7],
        ),
    ],
)
def test_item_23_disclosure_engine_scores_expected_ranges(object_types, score_range, allowed_scores):
    result = _evaluate("item_23", object_types)

    assert result.status == "matched"
    assert result.score_range == score_range
    assert result.allowed_scores == allowed_scores
    assert result.needs_review is False


def test_item_22_conflicting_facts_mark_needs_review_without_stopping_scoring():
    result = _evaluate("item_22", ["no_funding_statement", "funding_disclosed"])

    assert result.status == "matched"
    assert result.score_range == "7"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["conflicting_facts"]


@pytest.mark.parametrize(
    "conflicting_fact",
    [
        "for_profit_funding",
        "non_profit_or_government_funding",
        "direct_no_influence_statement",
        "sponsor_influence_statement",
    ],
)
def test_item_22_no_funding_conflicts_with_funder_detail_facts(conflicting_fact):
    result = _evaluate("item_22", ["no_funding_statement", conflicting_fact])

    assert result.status == "matched"
    assert result.score_range == "7"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["conflicting_facts"]


def test_item_23_conflicting_facts_mark_needs_review_without_stopping_scoring():
    result = _evaluate(
        "item_23",
        [
            "coi_collection_process",
            "coi_disclosure_result",
            "no_conflicts_statement",
            "conflicts_present",
            "coi_no_influence_statement",
        ],
    )

    assert result.status == "matched"
    assert result.score_range == "7"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["conflicting_facts"]


def test_item_23_extraction_guidance_excludes_acknowledgments_only():
    spec = get_item_spec("item_23")
    prompt = render_extraction_prompt(
        spec,
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Acknowledgments",
                content="Acknowledgments - The association thanks the members of the volunteer writing group.",
            )
        ],
    )

    assert "core/rule_engine/specs/item_23.py" not in prompt
    assert "Acknowledgments 人员名单" in prompt
    assert "duality of interest" in prompt
    assert "author disclosures" in prompt
    assert "章节标题或表格行标题" in prompt
    assert "There are no conflicts of interest" in prompt
    assert "Conflict of interest: None" in prompt
    assert "collection/process 只影响评分完整性" in prompt
    assert "authors declare" in prompt
    assert "all authors declare" in prompt
    assert "当前指南/本文档作者或开发组" in prompt
    assert "coi_disclosure_result" in prompt
    assert "coi_reviewed_or_discussed" in prompt
    assert "coi_management_action" in prompt
    assert "coi_no_influence_statement" in prompt
    assert "professional interest" in prompt
    assert "interest in diabetes/endocrinology" in prompt


def test_item_23_no_conflicts_statement_can_act_as_report_result():
    result = _evaluate(
        "item_23",
        ["coi_collection_process", "no_conflicts_statement", "coi_no_influence_statement"],
    )

    assert result.score_range == "7"


def test_item_23_conflicts_present_can_act_as_report_result():
    result = _evaluate(
        "item_23",
        ["coi_collection_process", "conflicts_present"],
    )

    assert result.score_range == "5-6"


def test_item_23_coi_report_without_management_scores_five_to_six():
    result = _evaluate(
        "item_23",
        ["coi_collection_process", "coi_disclosure_result"],
    )

    assert result.score_range == "5-6"


def test_item_23_detailed_report_without_collection_scores_incomplete():
    result = _evaluate("item_23", ["coi_disclosure_result"])

    assert result.score_range == "5"


def test_item_23_conflicts_present_with_management_scores_seven():
    result = _evaluate(
        "item_23",
        ["coi_collection_process", "conflicts_present", "coi_management_action"],
    )

    assert result.score_range == "7"


def test_item_23_disclosure_result_with_management_without_conflict_presence_scores_five_to_six():
    result = _evaluate(
        "item_23",
        ["coi_collection_process", "coi_disclosure_result", "coi_management_action"],
    )

    assert result.score_range == "5-6"


def test_item_23_management_without_collection_scores_incomplete():
    result = _evaluate(
        "item_23",
        ["coi_disclosure_result", "coi_management_action"],
    )

    assert result.score_range == "5"


def test_branching_path_returns_error_for_invalid_score_range():
    spec = ItemSpec(
        item_id="item_invalid_branch",
        title="Invalid branch",
        rule_profile="branching_path",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        rule=BranchingRule(
            cases=(
                BranchCase(name="bad", condition=all_of("A"), score_range="7+"),
                BranchCase(name="fallback", condition=all_of(), score_range="1"),
            )
        ),
    )

    result = BranchingPathEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["invalid_score_range"]


def test_branching_path_returns_error_when_fallback_is_not_last():
    spec = ItemSpec(
        item_id="item_bad_branch_fallback",
        title="Bad branch fallback",
        rule_profile="branching_path",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        rule=BranchingRule(
            cases=(
                BranchCase(name="fallback", condition=all_of(), score_range="1"),
                BranchCase(name="specific", condition=all_of("A"), score_range="7"),
            )
        ),
    )

    result = BranchingPathEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["fallback_not_last"]
