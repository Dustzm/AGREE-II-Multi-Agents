from __future__ import annotations

import pytest

from core.rule_engine.engines.profiles import OrderedMilestoneEngine
from core.rule_engine.models import (
    EvidenceChunk,
    EvidenceUnit,
    EvidenceRef,
    ExtractionBatchResult,
    ItemSpec,
    GroupVerificationDecision,
    MilestoneRule,
    RuleGroupingResult,
    RuleCondition,
    RuleObject,
    RuleObjectGroup,
    RuleObjectSpec,
    RubricAppraiserDecision,
    RubricAppraiserResult,
    RubricDecision,
    RubricUnitResult,
    ScoreLevel,
    all_of,
    any_of,
    not_of,
)
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.prompts.extraction import render_extraction_prompt
from core.rule_engine.runner import run_rule_pipeline
from core.rule_engine.stores.object_store import ObjectStore
from core.rule_engine.verification import DeterministicRuleObjectVerifier


def _rule_object(object_type: str, value: str | None = None, *, chunk_id: str = "chunk_001") -> RuleObject:
    return RuleObject(
        type=object_type,
        value=value or object_type,
        evidence=EvidenceRef(chunk_id=chunk_id, quote=object_type),
        confidence=0.95,
    )


def _evaluate(item_id: str, object_types: list[str]):
    spec = get_item_spec(item_id)
    store = ObjectStore(spec)
    store.merge([_rule_object(object_type) for object_type in object_types], processed_chunks=["chunk_001"])
    snapshot = store.snapshot()
    return get_rule_engine(spec.rule_profile).execute(spec, snapshot)


def _rubric_result(item_id: str, band: str, *, accepted: bool = True, score_range: str | None = None):
    spec = get_item_spec(item_id)
    unit = EvidenceUnit(
        unit_id=f"{item_id}:unit:1",
        chunk_id="chunk_001",
        title="Rubric evidence",
        quote="Rubric evidence quote.",
        content="Rubric evidence quote.",
        unit_type="paragraph",
    )
    if score_range is None:
        score_range = {
            "no_evidence": "1",
            "single_direction_factor": "2-3",
            "both_facilitator_and_barrier": "4",
            "both_plus_collection_or_impact": "5-6",
            "factors_plus_collection_and_impact": "7",
            "excluded_clinical_content": "1",
            "implementation_discussion_only": "2-4",
            "tool_like_but_not_eligible": "2-4",
            "one_eligible_tool_no_instruction": "2-3",
            "multiple_eligible_tools_no_instruction": "4-5",
            "implementation_section_plus_tool_no_instruction": "5-6",
            "eligible_tool_with_use_instruction": "6-7",
            "complete_tool_with_use_instruction": "7",
            "external_resource_only": "1",
            "vague_monitoring": "2",
            "criterion_or_frequency_only": "3-4",
            "criterion_plus_frequency": "5-6",
            "criterion_plus_frequency_plus_method": "7",
            "excluded_clinical_episode": "1",
            "excluded_diagnostic_definition": "1",
            "excluded_device_operation": "1",
            "excluded_cited_study_outcome": "1",
        }[band]
    decision = RubricDecision(
        unit_id=unit.unit_id,
        accepted=accepted,
        band=band,
        score_range=score_range,
        confidence=0.95,
        reason_code="test",
        reason=f"{band} evidence",
        unit_index=0,
        attributes={},
    )
    rubric_result = RubricUnitResult(
        candidate_units=[unit],
        accepted_units=[unit] if accepted else [],
        decisions=[decision],
        best_decision=decision if accepted and score_range != "1" else None,
    )
    return get_rule_engine(spec.rule_profile).score(spec, rubric_result)


def _rubric_appraiser_result(item_id: str, score: int, label: str = "moderate_rubric_support"):
    spec = get_item_spec(item_id)
    result = RubricAppraiserResult(
        candidate_units=[],
        decision=RubricAppraiserDecision(
            score=score,
            confidence=0.9,
            decision_label=label,
            rationale="portfolio-level appraisal",
            why_not_higher="not higher",
            evidence=[],
        ),
    )
    return get_rule_engine(spec.rule_profile).score(spec, result)


@pytest.mark.parametrize(
    ("item_id", "object_types", "score_range"),
    [
        ("item_3", [], "1"),
        ("item_3", ["rough_population"], "2-3"),
        ("item_3", ["detailed_population_characteristics"], "4-6"),
        ("item_3", ["detailed_population_characteristics", "exclusion_criteria"], "7"),
        ("item_5", [], "1"),
        ("item_5", ["preference_considered"], "2-3"),
        ("item_5", ["simple_collection_method"], "4"),
        ("item_5", ["comprehensive_collection_method"], "5"),
        ("item_5", ["simple_collection_method", "preference_summary", "used_in_guideline_or_recommendations"], "6-7"),
        ("item_6", [], "1"),
        ("item_6", ["vague_target_user"], "2"),
        ("item_6", ["vague_intended_use"], "2"),
        ("item_6", ["clear_target_user"], "3-4"),
        ("item_6", ["clear_intended_use"], "3-4"),
        ("item_6", ["vague_target_user", "vague_intended_use"], "5"),
        ("item_6", ["clear_target_user", "vague_intended_use"], "6"),
        ("item_6", ["vague_target_user", "clear_intended_use"], "6"),
        ("item_6", ["clear_target_user", "clear_intended_use"], "7"),
        ("item_7", [], "1"),
        ("item_7", ["search_activity"], "2"),
        ("item_7", ["named_information_source"], "3"),
        ("item_7", ["named_information_source", "search_terms"], "4"),
        ("item_7", ["named_information_source", "search_terms", "search_time_coverage"], "5"),
        ("item_7", ["named_information_source", "search_terms", "search_time_coverage", "complete_search_strategy"], "6-7"),
        ("item_10", [], "1"),
        ("item_10", ["formulation_method"], "2-3"),
        ("item_10", ["consensus_process_heading"], "1"),
        ("item_10", ["approval_or_finalization_statement"], "2-3"),
        ("item_10", ["voting_or_agreement_statement"], "2-3"),
        ("item_10", ["formulation_method", "process_detail"], "4-5"),
        ("item_10", ["consensus_process_heading", "process_detail"], "4-5"),
        ("item_10", ["formulation_method", "result_detail"], "4-5"),
        ("item_10", ["formulation_method", "process_detail", "result_detail", "recommendation_impact"], "6-7"),
        ("item_13", [], "1"),
        ("item_13", ["external_review_mention"], "2-3"),
        ("item_13", ["external_reviewer_identity"], "4"),
        ("item_13", ["external_reviewer_identity", "review_process_or_result"], "5"),
        ("item_13", ["external_reviewer_identity", "review_purpose", "review_process_or_result"], "6-7"),
        ("item_15", [], "1"),
        ("item_15", ["vague_or_unclear_recommendation"], "1"),
        ("item_15", ["directive_language"], "2-3"),
        ("item_15", ["population_or_condition", "intervention_or_action"], "2-3"),
        ("item_15", ["directive_language", "population_or_condition", "intervention_or_action"], "4-5"),
        (
            "item_15",
            [
                "directive_language",
                "population_or_condition",
                "intervention_or_action",
                "purpose_or_context_detail",
            ],
            "6-7",
        ),
        (
            "item_15",
            [
                "recommendation_statement",
                "population_or_condition",
                "intervention_or_action",
                "dose_duration_or_operational_detail",
            ],
            "6-7",
        ),
        ("item_18", [], "1"),
        ("item_18", ["facilitator"], "1"),
        ("item_18", ["facilitator", "barrier"], "1"),
        ("item_18", ["facilitator", "barrier", "collection_method"], "1"),
        ("item_18", ["facilitator", "barrier", "collection_method", "impact_on_guideline_or_recommendations"], "1"),
        ("item_20", [], "1"),
        ("item_20", ["vague_resource_mention"], "2-3"),
        ("item_20", ["resource_implication_context"], "2-3"),
        ("item_20", ["resource_implication_context", "vague_resource_mention"], "2-3"),
        ("item_20", ["cost_or_economic_issue"], "4-5"),
        ("item_20", ["survey_information"], "4-5"),
        ("item_20", ["economic_evaluation_proposal"], "4-5"),
        ("item_20", ["resource_implication_context", "cost_or_economic_issue"], "4-5"),
        ("item_20", ["resource_implication_context", "survey_information"], "4-5"),
        ("item_20", ["survey_information", "resource_information_used_in_recommendations"], "6-7"),
        ("item_20", ["cost_or_economic_issue", "resource_information_used_in_recommendations"], "6-7"),
        ("item_20", ["economic_evaluation_proposal", "resource_information_used_in_recommendations"], "6-7"),
        ("item_21", [], "1"),
        ("item_21", ["vague_monitoring_mention"], "1"),
        ("item_21", ["indicator_or_standard"], "1"),
        ("item_21", ["measurement_frequency_or_interval"], "1"),
        ("item_21", ["measurement_method"], "1"),
        ("item_21", ["indicator_or_standard", "measurement_frequency_or_interval"], "1"),
        (
            "item_21",
            ["indicator_or_standard", "measurement_frequency_or_interval", "measurement_method"],
            "1",
        ),
    ],
)
def test_ordered_milestone_items_score_expected_ranges(item_id, object_types, score_range):
    result = _evaluate(item_id, object_types)

    assert result.score_range == score_range
    assert result.max_score == max(result.allowed_scores)
    assert result.needs_review is False


def test_item_3_extraction_prompt_includes_functional_risk_and_context_characteristics():
    prompt = render_extraction_prompt(
        get_item_spec("item_3"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Population",
                content="Insulin-treated workers with severe hypoglycemia in safety-sensitive jobs.",
            )
        ],
    )

    assert "胰岛素/非胰岛素治疗" in prompt
    assert "严重低血糖" in prompt
    assert "糖尿病并发症" in prompt
    assert "高风险岗位" in prompt
    assert "安全敏感岗位" in prompt


def test_item_6_prompt_distinguishes_target_users_from_target_population():
    prompt = render_extraction_prompt(
        get_item_spec("item_6"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Scope",
                content="This guideline addresses adults with diabetes.",
            )
        ],
    )

    assert "target users/intended users/audience" in prompt
    assert "inform clinical decision-making" in prompt
    assert "policy formulation" in prompt
    assert "standards of care" in prompt
    assert "patients 只有被明确写成 intended users/readers/audience" in prompt
    assert "疾病范围" in prompt
    assert "目标患者人群" in prompt
    assert "作者/制定组" in prompt


def test_item_6_uses_full_text_target_user_and_use_without_grouping():
    spec = get_item_spec("item_6")
    store = ObjectStore(spec)
    store.merge(
        [
            _rule_object("clear_target_user", "primary care clinicians", chunk_id="chunk_001"),
            _rule_object("clear_intended_use", "inform clinical decision-making", chunk_id="chunk_009"),
        ],
        processed_chunks=["chunk_001", "chunk_009"],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert spec.rule_profile == "target_user"
    assert spec.grouping is None
    assert result.score_range == "7"


def test_item_6_target_population_scope_object_is_excluded_from_scoring():
    spec = get_item_spec("item_6")
    store = ObjectStore(spec)
    obj = RuleObject(
        type="clear_target_user",
        value="adults with diabetes",
        evidence=EvidenceRef(chunk_id="chunk_001", quote="adults with diabetes"),
        confidence=0.95,
        attributes={"scope": "target_population"},
    )
    store.merge([obj], processed_chunks=["chunk_001"])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert result.score_range == "1"
    assert any(reason.code == "excluded_target_user_scope" for reason in result.review_reasons)


def test_item_6_exclusion_objects_do_not_score():
    spec = get_item_spec("item_6")
    store = ObjectStore(spec)
    store.merge(
        [
            _rule_object("target_population_or_disease_scope", "adults with diabetes"),
            _rule_object("author_or_developer_group", "the guideline committee"),
            _rule_object("ordinary_clinical_context", "use metformin as first-line therapy"),
        ],
        processed_chunks=["chunk_001"],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert result.score_range == "1"
    assert {reason.code for reason in result.review_reasons} == {"excluded_target_user_object"}


def test_item_6_scores_general_user_and_specific_use_from_object_types():
    spec = get_item_spec("item_6")
    store = ObjectStore(spec)
    objects = [
        _rule_object("vague_target_user", "guideline users"),
        _rule_object("clear_intended_use", "inform clinical decision-making"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert result.score_range == "6"


def test_item_20_cost_or_survey_information_does_not_require_extra_context_gate():
    cost_result = _evaluate("item_20", ["cost_or_economic_issue"])
    survey_result = _evaluate("item_20", ["survey_information"])
    vague_result = _evaluate("item_20", ["resource_implication_context"])
    spec = get_item_spec("item_20")

    assert cost_result.score_range == "4-5"
    assert survey_result.score_range == "4-5"
    assert vague_result.score_range == "2-3"
    assert spec.object_specs["cost_or_economic_issue"].verification is not None
    assert spec.object_specs["cost_or_economic_issue"].verification.llm_required is True
    assert "background economic burden of the disease" in spec.object_specs["cost_or_economic_issue"].verification.guidance


def test_item_20_extraction_prompt_excludes_resource_recommendation_as_used_information():
    prompt = render_extraction_prompt(
        get_item_spec("item_20"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Resources",
                content=(
                    "The guideline recommends one diabetes inpatient specialist nurse "
                    "per 300 beds. Costs may go down after implementation."
                ),
            )
        ],
    )

    assert "resource_information_used_in_recommendations" in prompt
    assert "推荐本身写了人员配置、服务水平、设备要求或报销覆盖" in prompt
    assert "cost may go down" in prompt
    assert "成本差异/成本比较" in prompt
    assert "影响推荐形成、选择、强度、内容或实施决策" in prompt


def test_item_10_extraction_prompt_excludes_clinical_workflow_and_plain_recommendations():
    prompt = render_extraction_prompt(
        get_item_spec("item_10"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="The committee recommends that a confirmatory test is performed on another day.",
            )
        ],
    )

    assert "Delphi" in prompt
    assert "voting" in prompt
    assert "EtD framework" in prompt
    assert "recommend/recommends/recommended" in prompt
    assert "临床检测/诊断/治疗流程" in prompt
    assert "证据检索" in prompt
    assert "当前 guideline、statement、position statement" in prompt
    assert "外部 consensus report/document" in prompt
    assert "外部 task force/report" in prompt
    assert "clinical experience and judgment" in prompt
    assert "meeting participants agreed" in prompt
    assert "当前指南的形成过程" in prompt
    assert "Konsensprozess" in prompt
    assert "Konsensusverfahren" in prompt
    assert "Nominaler Gruppenprozess" in prompt
    assert "Abstimmungsverfahren" in prompt
    assert "Zustimmung" in prompt
    assert "verabschiedet" in prompt
    assert "starker Konsens" in prompt
    assert "普通会议讨论" in prompt
    assert "专家组构成" in prompt
    assert "当前文件引用的外部文件或外部组织流程" in prompt
    assert "外部 ADA/IADPSG/" in prompt
    assert "at most 5 representative object(s)" in prompt


def test_item_10_uses_grouping_for_recommendation_formulation():
    spec = get_item_spec("item_10")

    assert spec.grouping is not None
    assert spec.grouping.group_profile == "recommendation_formulation"
    assert spec.grouping.anchor_object_types == (
        "formulation_method",
        "consensus_process_heading",
        "approval_or_finalization_statement",
        "voting_or_agreement_statement",
    )
    assert "singleton formulation_method group" in spec.grouping.guidance
    assert "heading-only group is not enough" in spec.grouping.guidance


def test_ordered_milestone_scores_item_10_from_accepted_group_only():
    spec = get_item_spec("item_10")
    objects = [
        _rule_object("formulation_method", chunk_id="chunk_001"),
        _rule_object("process_detail", chunk_id="chunk_020"),
        _rule_object("result_detail", chunk_id="chunk_020"),
        _rule_object("recommendation_impact", chunk_id="chunk_020"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001", "chunk_020"])
    accepted_group = RuleObjectGroup(
        group_id="item_10:recommendation_formulation:1",
        group_type="recommendation_formulation",
        objects=(objects[0],),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={},
    )
    rejected_high_group = RuleObjectGroup(
        group_id="item_10:recommendation_formulation:2",
        group_type="recommendation_formulation",
        objects=tuple(objects),
        chunk_ids=("chunk_001", "chunk_020"),
        relation_hints=("local_window",),
        attributes={},
    )
    grouping_result = RuleGroupingResult(
        candidate_groups=[accepted_group, rejected_high_group],
        accepted_groups=[accepted_group],
        decisions=[
            GroupVerificationDecision(
                group_id=accepted_group.group_id,
                group_type=accepted_group.group_type,
                accepted=True,
                confidence=0.95,
                source="llm",
                group_index=0,
            ),
            GroupVerificationDecision(
                group_id=rejected_high_group.group_id,
                group_type=rejected_high_group.group_type,
                accepted=False,
                confidence=0.9,
                source="llm",
                rejection_code="unrelated_objects",
                reason="Objects are not one formulation process.",
                group_index=1,
            ),
        ],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "2-3"
    assert result.matched_rules == ["formulation_method"]


def test_item_10_heading_only_group_does_not_score_as_formulation_method():
    spec = get_item_spec("item_10")
    heading = _rule_object("consensus_process_heading")
    store = ObjectStore(spec)
    store.merge([heading], processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_10:recommendation_formulation:1",
        group_type="recommendation_formulation",
        objects=(heading,),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={},
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "1"


def test_ordered_milestone_selects_highest_accepted_item_10_group():
    spec = get_item_spec("item_10")
    objects = [
        _rule_object("formulation_method"),
        _rule_object("process_detail"),
        _rule_object("result_detail"),
        _rule_object("recommendation_impact"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    low_group = RuleObjectGroup(
        group_id="item_10:recommendation_formulation:1",
        group_type="recommendation_formulation",
        objects=(objects[0],),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={},
    )
    high_group = RuleObjectGroup(
        group_id="item_10:recommendation_formulation:2",
        group_type="recommendation_formulation",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={},
    )
    grouping_result = RuleGroupingResult(
        candidate_groups=[low_group, high_group],
        accepted_groups=[low_group, high_group],
        decisions=[],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "6-7"
    assert result.matched_rules == [
        "formulation_method",
        "process_detail",
        "recommendation_impact",
        "result_detail",
    ]


def test_item_11_scores_complete_benefit_harm_balance_group_as_7():
    spec = get_item_spec("item_11")
    objects = [
        _rule_object("recommendation_or_option"),
        _rule_object("health_benefit"),
        _rule_object("harm_side_effect_or_risk"),
        _rule_object("benefit_supporting_data"),
        _rule_object("harm_supporting_data"),
        _rule_object("benefit_harm_balance_statement"),
        _rule_object("recommendation_impact_statement"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": True,
            "harm_has_supporting_data": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "7"
    assert result.missing == []


def test_item_11_complete_group_with_uncovered_anchor_does_not_score_7():
    spec = get_item_spec("item_11")
    complete_objects = [
        _rule_object("recommendation_or_option", "rec-a", chunk_id="chunk_001"),
        _rule_object("health_benefit", chunk_id="chunk_001"),
        _rule_object("harm_side_effect_or_risk", chunk_id="chunk_001"),
        _rule_object("benefit_supporting_data", chunk_id="chunk_001"),
        _rule_object("harm_supporting_data", chunk_id="chunk_001"),
        _rule_object("benefit_harm_balance_statement", chunk_id="chunk_001"),
        _rule_object("recommendation_impact_statement", chunk_id="chunk_001"),
    ]
    uncovered = _rule_object("recommendation_or_option", "rec-b", chunk_id="chunk_002")
    store = ObjectStore(spec)
    store.merge([*complete_objects, uncovered], processed_chunks=["chunk_001", "chunk_002"])
    complete_group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(complete_objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": True,
            "harm_has_supporting_data": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    uncovered_group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:2",
        group_type="benefit_harm_balance",
        objects=(uncovered,),
        chunk_ids=("chunk_002",),
        relation_hints=("same_chunk",),
        attributes={"portfolio_role": "anchor", "scope": "current_guideline", "same_decision": True},
    )
    grouping_result = RuleGroupingResult(
        candidate_groups=[complete_group, uncovered_group],
        accepted_groups=[complete_group],
        decisions=[],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert "benefit_harm_balance_statement" in result.matched_rules


def test_item_11_rejected_anchor_stays_in_portfolio_denominator():
    spec = get_item_spec("item_11")
    complete_objects = [
        _rule_object("recommendation_or_option", "rec-a", chunk_id="chunk_001"),
        _rule_object("health_benefit", chunk_id="chunk_001"),
        _rule_object("harm_side_effect_or_risk", chunk_id="chunk_001"),
        _rule_object("benefit_supporting_data", chunk_id="chunk_001"),
        _rule_object("harm_supporting_data", chunk_id="chunk_001"),
        _rule_object("benefit_harm_balance_statement", chunk_id="chunk_001"),
        _rule_object("recommendation_impact_statement", chunk_id="chunk_001"),
    ]
    rejected_anchor = _rule_object("recommendation_or_option", "rec-b", chunk_id="chunk_002")
    store = ObjectStore(spec)
    store.merge([*complete_objects, rejected_anchor], processed_chunks=["chunk_001", "chunk_002"])
    complete_group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(complete_objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": True,
            "harm_has_supporting_data": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    rejected_group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:2",
        group_type="benefit_harm_balance",
        objects=(
            rejected_anchor,
            _rule_object("health_benefit", chunk_id="chunk_002"),
            _rule_object("harm_side_effect_or_risk", chunk_id="chunk_002"),
            _rule_object("benefit_supporting_data", chunk_id="chunk_002"),
            _rule_object("harm_supporting_data", chunk_id="chunk_002"),
            _rule_object("benefit_harm_balance_statement", chunk_id="chunk_002"),
            _rule_object("recommendation_impact_statement", chunk_id="chunk_002"),
        ),
        chunk_ids=("chunk_002",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
            "same_decision": False,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": True,
            "harm_has_supporting_data": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    grouping_result = RuleGroupingResult(
        candidate_groups=[complete_group, rejected_group],
        accepted_groups=[complete_group],
        decisions=[],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert "complete_benefit_harm_portfolio" not in result.missing


def test_item_11_all_complete_portfolio_anchors_score_7():
    spec = get_item_spec("item_11")
    groups = []
    objects = []
    for index in range(2):
        group_objects = [
            _rule_object("recommendation_or_option", f"rec-{index}", chunk_id=f"chunk_00{index}"),
            _rule_object("health_benefit", chunk_id=f"chunk_00{index}"),
            _rule_object("harm_side_effect_or_risk", chunk_id=f"chunk_00{index}"),
            _rule_object("benefit_supporting_data", chunk_id=f"chunk_00{index}"),
            _rule_object("harm_supporting_data", chunk_id=f"chunk_00{index}"),
            _rule_object("benefit_harm_balance_statement", chunk_id=f"chunk_00{index}"),
            _rule_object("recommendation_impact_statement", chunk_id=f"chunk_00{index}"),
        ]
        objects.extend(group_objects)
        groups.append(
            RuleObjectGroup(
                group_id=f"item_11:benefit_harm_balance:{index}",
                group_type="benefit_harm_balance",
                objects=tuple(group_objects),
                chunk_ids=(f"chunk_00{index}",),
                relation_hints=("same_chunk",),
                attributes={
                    "portfolio_role": "anchor",
                    "scope": "current_guideline",
                    "same_decision": True,
                    "has_recommendation_or_option": True,
                    "has_benefit": True,
                    "has_harm_or_risk": True,
                    "benefit_has_supporting_data": True,
                    "harm_has_supporting_data": True,
                    "has_explicit_balance": True,
                    "balance_affects_recommendation": True,
                },
            )
        )
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_000", "chunk_001"])
    grouping_result = RuleGroupingResult(candidate_groups=groups, accepted_groups=groups, decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "7"
    assert result.missing == []


def test_item_11_capped_portfolio_counts_omitted_anchors_in_denominator():
    spec = get_item_spec("item_11")
    groups = []
    objects = []
    for index in range(2):
        group_objects = [
            _rule_object("recommendation_or_option", f"rec-{index}", chunk_id=f"chunk_00{index}"),
            _rule_object("health_benefit", chunk_id=f"chunk_00{index}"),
            _rule_object("harm_side_effect_or_risk", chunk_id=f"chunk_00{index}"),
            _rule_object("benefit_supporting_data", chunk_id=f"chunk_00{index}"),
            _rule_object("harm_supporting_data", chunk_id=f"chunk_00{index}"),
            _rule_object("benefit_harm_balance_statement", chunk_id=f"chunk_00{index}"),
            _rule_object("recommendation_impact_statement", chunk_id=f"chunk_00{index}"),
        ]
        objects.extend(group_objects)
        groups.append(
            RuleObjectGroup(
                group_id=f"item_11:benefit_harm_balance:{index}",
                group_type="benefit_harm_balance",
                objects=tuple(group_objects),
                chunk_ids=(f"chunk_00{index}",),
                relation_hints=("same_chunk",),
                attributes={
                    "portfolio_role": "anchor",
                    "portfolio_anchor_cap_exceeded": True,
                    "portfolio_anchor_total": 5,
                    "portfolio_anchor_kept": 2,
                    "portfolio_anchor_omitted": 3,
                    "scope": "current_guideline",
                    "same_decision": True,
                    "has_recommendation_or_option": True,
                    "has_benefit": True,
                    "has_harm_or_risk": True,
                    "benefit_has_supporting_data": True,
                    "harm_has_supporting_data": True,
                    "has_explicit_balance": True,
                    "balance_affects_recommendation": True,
                },
            )
        )
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_000", "chunk_001"])
    grouping_result = RuleGroupingResult(candidate_groups=groups, accepted_groups=groups, decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert any(reason.code == "portfolio_anchor_cap_exceeded" for reason in result.review_reasons)


def test_item_11_does_not_globally_join_benefit_and_harm_objects():
    spec = get_item_spec("item_11")
    objects = [
        _rule_object("health_benefit", chunk_id="chunk_001"),
        _rule_object("harm_side_effect_or_risk", chunk_id="chunk_010"),
        _rule_object("benefit_supporting_data", chunk_id="chunk_001"),
        _rule_object("harm_supporting_data", chunk_id="chunk_010"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001", "chunk_010"])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result=None)

    assert result.score_range == "1"
    assert any(reason.code == "missing_grouping_result" for reason in result.review_reasons)


def test_item_11_both_sides_with_data_without_balance_scores_4_to_5():
    spec = get_item_spec("item_11")
    objects = [
        _rule_object("recommendation_or_option"),
        _rule_object("health_benefit"),
        _rule_object("harm_side_effect_or_risk"),
        _rule_object("benefit_supporting_data"),
        _rule_object("harm_supporting_data"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": True,
            "harm_has_supporting_data": True,
            "has_explicit_balance": False,
            "balance_affects_recommendation": False,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert "benefit_harm_balance_statement" in result.missing


def test_item_11_group_attributes_override_object_presence():
    spec = get_item_spec("item_11")
    objects = [
        _rule_object("recommendation_or_option"),
        _rule_object("health_benefit"),
        _rule_object("harm_side_effect_or_risk"),
        _rule_object("benefit_supporting_data"),
        _rule_object("harm_supporting_data"),
        _rule_object("benefit_harm_balance_statement"),
        _rule_object("recommendation_impact_statement"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": False,
            "harm_has_supporting_data": False,
            "has_explicit_balance": False,
            "balance_affects_recommendation": False,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert "benefit_supporting_data" in result.missing
    assert "harm_supporting_data" in result.missing


def test_item_11_rejected_same_decision_partial_evidence_scores_2_to_3():
    spec = get_item_spec("item_11")
    objects = [
        _rule_object("recommendation_or_option"),
        _rule_object("health_benefit"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
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
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "2-3"
    assert "harm_side_effect_or_risk" in result.missing
    assert any(reason.code == "item11_partial_evidence_retained" for reason in result.review_reasons)


def test_item_11_rejected_same_decision_both_sides_scores_4_to_5_but_not_complete():
    spec = get_item_spec("item_11")
    objects = [
        _rule_object("recommendation_or_option"),
        _rule_object("health_benefit"),
        _rule_object("harm_side_effect_or_risk"),
        _rule_object("benefit_supporting_data"),
        _rule_object("harm_supporting_data"),
        _rule_object("benefit_harm_balance_statement"),
        _rule_object("recommendation_impact_statement"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
            "verifier_accepted": False,
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": True,
            "harm_has_supporting_data": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert result.max_score == 5
    assert "complete_benefit_harm_portfolio" in result.missing
    assert "benefit_harm_balance_statement" not in result.missing


def test_item_11_systematic_balance_with_multiple_local_examples_scores_6_not_7():
    spec = get_item_spec("item_11")
    global_objects = [
        _rule_object(
            "benefit_harm_balance_statement",
            (
                "The guideline panel considered the balance of benefits and harms "
                "when formulating recommendations."
            ),
            chunk_id="chunk_001",
        ),
    ]
    anchor_one = [
        _rule_object("recommendation_or_option", "rec-a", chunk_id="chunk_002"),
        _rule_object("health_benefit", chunk_id="chunk_002"),
    ]
    anchor_two = [
        _rule_object("recommendation_or_option", "rec-b", chunk_id="chunk_003"),
        _rule_object("harm_side_effect_or_risk", chunk_id="chunk_003"),
    ]
    objects = [*global_objects, *anchor_one, *anchor_two]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001", "chunk_002", "chunk_003"])
    global_group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:global",
        group_type="benefit_harm_balance",
        objects=tuple(global_objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "global",
            "scope": "current_guideline",
            "same_decision": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    anchor_group_one = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(anchor_one),
        chunk_ids=("chunk_002",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
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
    anchor_group_two = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:2",
        group_type="benefit_harm_balance",
        objects=tuple(anchor_two),
        chunk_ids=("chunk_003",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": False,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": False,
            "harm_has_supporting_data": False,
            "has_explicit_balance": False,
            "balance_affects_recommendation": False,
        },
    )
    grouping_result = RuleGroupingResult(
        candidate_groups=[global_group, anchor_group_one, anchor_group_two],
        accepted_groups=[global_group, anchor_group_one, anchor_group_two],
        decisions=[],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "6"
    assert result.max_score == 6
    assert "complete_benefit_harm_portfolio" in result.missing


def test_item_11_local_global_balance_with_multiple_examples_does_not_score_6():
    spec = get_item_spec("item_11")
    global_objects = [
        _rule_object(
            "benefit_harm_balance_statement",
            "Drug treatment should balance glucose lowering against hypoglycaemia.",
            chunk_id="chunk_001",
        ),
    ]
    anchor_one = [
        _rule_object("recommendation_or_option", "rec-a", chunk_id="chunk_002"),
        _rule_object("health_benefit", chunk_id="chunk_002"),
    ]
    anchor_two = [
        _rule_object("recommendation_or_option", "rec-b", chunk_id="chunk_003"),
        _rule_object("harm_side_effect_or_risk", chunk_id="chunk_003"),
    ]
    objects = [*global_objects, *anchor_one, *anchor_two]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001", "chunk_002", "chunk_003"])
    global_group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:global",
        group_type="benefit_harm_balance",
        objects=tuple(global_objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "global",
            "scope": "current_guideline",
            "same_decision": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    anchor_group_one = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:1",
        group_type="benefit_harm_balance",
        objects=tuple(anchor_one),
        chunk_ids=("chunk_002",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
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
    anchor_group_two = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:2",
        group_type="benefit_harm_balance",
        objects=tuple(anchor_two),
        chunk_ids=("chunk_003",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": False,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": False,
            "harm_has_supporting_data": False,
            "has_explicit_balance": False,
            "balance_affects_recommendation": False,
        },
    )
    grouping_result = RuleGroupingResult(
        candidate_groups=[global_group, anchor_group_one, anchor_group_two],
        accepted_groups=[global_group, anchor_group_one, anchor_group_two],
        decisions=[],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert result.max_score == 5
    assert "complete_benefit_harm_portfolio" in result.missing


def test_item_11_single_global_balance_statement_stays_2_to_3():
    spec = get_item_spec("item_11")
    objects = [_rule_object("benefit_harm_balance_statement")]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_11:benefit_harm_balance:global",
        group_type="benefit_harm_balance",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "global",
            "scope": "current_guideline",
            "same_decision": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "2-3"


def test_item_12_partial_recommendation_evidence_links_score_4_to_5():
    spec = get_item_spec("item_12")
    objects = [
        _rule_object("recommendation_statement"),
        _rule_object("citation_group"),
        _rule_object("evidence_level_or_grade"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:1",
        group_type="recommendation_evidence_link",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "same_recommendation": True,
            "has_recommendation": True,
            "has_specific_supporting_evidence": True,
            "link_type": "citation_group",
            "mapping_strength": "partial",
            "same_evidence_unit": True,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert "systematic_recommendation_evidence_mapping" in result.missing


def test_item_12_group_attributes_override_object_presence():
    spec = get_item_spec("item_12")
    objects = [
        _rule_object("recommendation_statement"),
        _rule_object("citation_group"),
        _rule_object("evidence_level_or_grade"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:1",
        group_type="recommendation_evidence_link",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "same_recommendation": True,
            "has_recommendation": True,
            "has_specific_supporting_evidence": False,
            "link_type": "citation_group",
            "mapping_strength": "partial",
            "same_evidence_unit": True,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "1"


def test_item_12_mapping_strength_none_does_not_count_as_valid_link():
    spec = get_item_spec("item_12")
    groups = []
    objects = []
    for index in range(2):
        group_objects = [
            _rule_object("recommendation_statement", f"rec-{index}", chunk_id=f"chunk_00{index}"),
            _rule_object("citation_group", chunk_id=f"chunk_00{index}"),
            _rule_object("evidence_level_or_grade", chunk_id=f"chunk_00{index}"),
        ]
        objects.extend(group_objects)
        groups.append(
            RuleObjectGroup(
                group_id=f"item_12:recommendation_evidence_link:{index}",
                group_type="recommendation_evidence_link",
                objects=tuple(group_objects),
                chunk_ids=(f"chunk_00{index}",),
                relation_hints=("same_chunk",),
                attributes={
                    "portfolio_role": "anchor",
                    "scope": "current_guideline",
                    "same_recommendation": True,
                    "has_recommendation": True,
                    "has_specific_supporting_evidence": True,
                    "link_type": "citation_group",
                    "mapping_strength": "none",
                    "same_evidence_unit": True,
                },
            )
        )
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_000", "chunk_001"])
    grouping_result = RuleGroupingResult(candidate_groups=groups, accepted_groups=[], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "1"


def test_item_12_single_systematic_group_does_not_score_6_to_7():
    spec = get_item_spec("item_12")
    objects = [
        _rule_object("recommendation_statement"),
        _rule_object("citation_group"),
        _rule_object("evidence_level_or_grade"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:1",
        group_type="recommendation_evidence_link",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline",
            "same_recommendation": True,
            "has_recommendation": True,
            "has_specific_supporting_evidence": True,
            "link_type": "evidence_level_or_grade",
            "mapping_strength": "systematic",
            "same_evidence_unit": True,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"


def test_item_12_systematic_multiple_links_score_7():
    spec = get_item_spec("item_12")
    groups = []
    objects = []
    for index in range(3):
        group_objects = [
            _rule_object("recommendation_statement", chunk_id=f"chunk_00{index}"),
            _rule_object("citation_group", chunk_id=f"chunk_00{index}"),
            _rule_object("evidence_level_or_grade", chunk_id=f"chunk_00{index}"),
        ]
        objects.extend(group_objects)
        groups.append(
            RuleObjectGroup(
                group_id=f"item_12:recommendation_evidence_link:{index}",
                group_type="recommendation_evidence_link",
                objects=tuple(group_objects),
                chunk_ids=(f"chunk_00{index}",),
                relation_hints=("same_chunk",),
                attributes={
                    "scope": "current_guideline",
                    "same_recommendation": True,
                    "has_recommendation": True,
                    "has_specific_supporting_evidence": True,
                    "link_type": "evidence_level_or_grade",
                    "mapping_strength": "systematic",
                    "same_evidence_unit": True,
                },
            )
        )
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_000", "chunk_001", "chunk_002"])
    grouping_result = RuleGroupingResult(candidate_groups=groups, accepted_groups=groups, decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "7"
    assert result.missing == []


def test_item_12_multiple_links_with_uncovered_anchor_does_not_score_7():
    spec = get_item_spec("item_12")
    groups = []
    objects = []
    for index in range(2):
        group_objects = [
            _rule_object("recommendation_statement", f"rec-{index}", chunk_id=f"chunk_00{index}"),
            _rule_object("citation_group", chunk_id=f"chunk_00{index}"),
            _rule_object("evidence_level_or_grade", chunk_id=f"chunk_00{index}"),
        ]
        objects.extend(group_objects)
        groups.append(
            RuleObjectGroup(
                group_id=f"item_12:recommendation_evidence_link:{index}",
                group_type="recommendation_evidence_link",
                objects=tuple(group_objects),
                chunk_ids=(f"chunk_00{index}",),
                relation_hints=("same_chunk",),
                attributes={
                    "portfolio_role": "anchor",
                    "scope": "current_guideline",
                    "same_recommendation": True,
                    "has_recommendation": True,
                    "has_specific_supporting_evidence": True,
                    "link_type": "evidence_level_or_grade",
                    "mapping_strength": "systematic",
                    "same_evidence_unit": True,
                },
            )
        )
    uncovered = _rule_object("recommendation_statement", "rec-uncovered", chunk_id="chunk_003")
    objects.append(uncovered)
    uncovered_group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:3",
        group_type="recommendation_evidence_link",
        objects=(uncovered,),
        chunk_ids=("chunk_003",),
        relation_hints=("same_chunk",),
        attributes={"portfolio_role": "anchor", "scope": "current_guideline", "same_recommendation": True},
    )
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_000", "chunk_001", "chunk_003"])
    grouping_result = RuleGroupingResult(
        candidate_groups=[*groups, uncovered_group],
        accepted_groups=groups,
        decisions=[],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "4-5"
    assert "systematic_recommendation_evidence_mapping" in result.missing


def test_item_12_generic_method_only_scores_2_to_3():
    spec = get_item_spec("item_12")
    obj = _rule_object("general_evidence_method")
    store = ObjectStore(spec)
    store.merge([obj], processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:1",
        group_type="recommendation_evidence_link",
        objects=(obj,),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={"portfolio_role": "global", "link_type": "generic_method"},
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "2-3"
    assert "recommendation_statement" in result.missing


def test_item_12_standalone_evidence_type_is_no_longer_scoreable():
    spec = get_item_spec("item_12")
    objects = [
        _rule_object("standalone_evidence_table"),
        _rule_object("citation_group"),
        _rule_object("evidence_level_or_grade"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result=None)

    assert result.score_range == "1"
    assert any(reason.code == "unknown_object_type" for reason in store.snapshot().review_reasons)
    assert any(reason.code == "missing_grouping_result" for reason in result.review_reasons)


def test_item_15_external_recommendation_group_is_excluded_from_scoring():
    spec = get_item_spec("item_15")
    objects = [
        _rule_object("directive_language"),
        _rule_object("population_or_condition"),
        _rule_object("intervention_or_action"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_15:recommendation_specificity:1",
        group_type="recommendation_specificity",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "same_recommendation": True,
            "is_current_guideline_recommendation": False,
        },
    )
    grouping_result = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping_result)

    assert result.score_range == "1"
    assert any(reason.code == "excluded_group_attributes" for reason in result.review_reasons)


def test_item_21_extraction_prompt_allows_measurable_clinical_or_health_outcome_criteria():
    prompt = render_extraction_prompt(
        get_item_spec("item_21"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Clinical monitoring",
                content="If HbA1c has not fallen by 0.5% after 3 months, review treatment.",
            )
        ],
    )

    assert "AGREE-II item_21" in prompt
    assert "AUDIT STANDARDS" in prompt
    assert "audit tool" in prompt
    assert "collection of data" in prompt
    assert "临床或健康结局测量" in prompt
    assert "HbA1c" in prompt
    assert "血压" in prompt
    assert "筛查比例" in prompt
    assert "检测/筛查/随访频率" in prompt
    assert "症状持续时间" in prompt
    assert "responders/nonresponders" in prompt
    assert "response status" in prompt
    assert "有无 hypoglycemia" in prompt
    assert "weight" in prompt
    assert "glucose monitoring" in prompt
    assert "titration" in prompt
    assert "每次 clinic visit" in prompt
    assert "非数值但可判定的推荐衍生监测标准" in prompt
    assert "如何测量" in prompt
    assert "质量指标" in prompt
    assert "绩效指标" in prompt
    assert "不要因为内容是临床推荐就直接拒绝" in prompt
    assert "引用研究 outcome" in prompt
    assert "泛泛的 close monitoring" in prompt
    assert "普通患者日常监测" in prompt
    assert "围手术期" in prompt
    assert "术中" in prompt
    assert "设备佩戴/扫描说明" in prompt
    assert "指南自身更新周期" in prompt
    assert "用于衡量推荐应用或持续使用" in prompt
    assert "诊断标准" in prompt
    assert "measurement_method 不能单独作为 3-4 分依据" in prompt


def test_item_21_scores_accepted_monitoring_group():
    spec = get_item_spec("item_21")
    assert spec.rule_profile == "monitoring_criteria"
    assert spec.grouping is not None
    assert spec.grouping.group_profile == "monitoring_criteria"

    store = ObjectStore(spec)
    objects = [
        _rule_object("indicator_or_standard"),
        _rule_object("measurement_frequency_or_interval"),
        _rule_object("measurement_method"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_21:monitoring_criteria:1",
        group_type="monitoring_criteria",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "criterion_origin": "current_guideline_recommendation",
            "measurement_purpose": "measure_guideline_application",
            "measurement_function": "application_audit_measure",
            "criterion_kind": "process_measure",
            "has_measure_target": True,
            "has_threshold_or_standard": True,
            "has_timing_or_frequency": True,
            "has_measurement_method": True,
            "same_measurement_criterion": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "7"
    assert result.matched_rules == [
        "indicator_or_standard",
        "measurement_frequency_or_interval",
        "measurement_method",
    ]


def test_item_21_does_not_score_method_from_attributes_without_method_object():
    spec = get_item_spec("item_21")
    store = ObjectStore(spec)
    objects = [
        _rule_object("indicator_or_standard"),
        _rule_object("measurement_frequency_or_interval"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_21:monitoring_criteria:1",
        group_type="monitoring_criteria",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "criterion_origin": "current_guideline_recommendation",
            "measurement_purpose": "measure_guideline_application",
            "measurement_function": "application_audit_measure",
            "criterion_kind": "process_measure",
            "has_measure_target": True,
            "has_threshold_or_standard": True,
            "has_timing_or_frequency": True,
            "has_measurement_method": True,
            "same_measurement_criterion": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "5-6"
    assert "measurement_method" in result.missing


def test_item_21_method_only_group_cannot_be_promoted_by_attributes():
    spec = get_item_spec("item_21")
    store = ObjectStore(spec)
    objects = [_rule_object("measurement_method")]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_21:monitoring_criteria:1",
        group_type="monitoring_criteria",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "criterion_origin": "current_guideline_recommendation",
            "measurement_purpose": "measure_guideline_application",
            "measurement_function": "application_audit_measure",
            "criterion_kind": "process_measure",
            "has_measure_target": True,
            "has_threshold_or_standard": True,
            "has_timing_or_frequency": True,
            "has_measurement_method": True,
            "same_measurement_criterion": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "1"
    assert result.matched_rules == []


def test_item_21_deterministic_verifier_does_not_hard_reject_clinical_monitoring():
    spec = get_item_spec("item_21")
    obj = RuleObject(
        type="indicator_or_standard",
        value="blood glucose monitoring",
        evidence=EvidenceRef(chunk_id="chunk_001", quote="blood glucose monitoring"),
        confidence=0.95,
    )
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Perioperative care",
        content="Perioperative blood glucose monitoring should be performed during the procedure.",
    )

    result = DeterministicRuleObjectVerifier().verify(spec, [obj], [chunk])

    assert [accepted.type for accepted in result.accepted_objects] == ["indicator_or_standard"]
    assert result.decisions[0].accepted is True
    assert result.decisions[0].source == "deterministic"
    assert result.decisions[0].rejection_code == ""
    assert result.decisions[0].tags == ()


def test_item_21_deterministic_verifier_does_not_apply_acute_recheck_keyword_tags():
    spec = get_item_spec("item_21")
    obj = RuleObject(
        type="measurement_frequency_or_interval",
        value="re-check after 15 minutes",
        evidence=EvidenceRef(chunk_id="chunk_001", quote="re-check after 15 minutes"),
        confidence=0.95,
    )
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Hypoglycemia treatment",
        content="If blood glucose is low, treat hypoglycemia and re-check after 15 minutes.",
    )

    result = DeterministicRuleObjectVerifier().verify(spec, [obj], [chunk])

    assert [accepted.type for accepted in result.accepted_objects] == ["measurement_frequency_or_interval"]
    assert result.decisions[0].accepted is True
    assert result.decisions[0].rejection_code == ""
    assert result.decisions[0].tags == ()


def test_item_21_verifier_allows_acute_disease_duration_standard():
    spec = get_item_spec("item_21")
    obj = RuleObject(
        type="measurement_frequency_or_interval",
        value="symptoms persist for more than 3 days",
        evidence=EvidenceRef(chunk_id="chunk_001", quote="symptoms persist for more than 3 days"),
        confidence=0.95,
    )
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Recommendations",
        content=(
            "For acute otitis media, amoxicillin should be used when symptoms persist "
            "for more than 3 days."
        ),
    )

    result = DeterministicRuleObjectVerifier().verify(spec, [obj], [chunk])

    assert [accepted.type for accepted in result.accepted_objects] == ["measurement_frequency_or_interval"]
    assert result.decisions[0].accepted is True


def test_item_21_verifier_allows_nonnumeric_recommendation_derived_monitoring_standards():
    spec = get_item_spec("item_21")
    objects = [
        RuleObject(
            type="indicator_or_standard",
            value="responders with no hypoglycemia",
            evidence=EvidenceRef(chunk_id="chunk_001", quote="responders with no hypoglycemia"),
            confidence=0.95,
        ),
        RuleObject(
            type="measurement_method",
            value="measure weight and enquire about symptoms at each visit",
            evidence=EvidenceRef(
                chunk_id="chunk_001",
                quote="measure weight and enquire about symptoms at each visit",
            ),
            confidence=0.95,
        ),
    ]
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Monitoring recommendations",
        content=(
            "Classify responders with no hypoglycemia, titrate sulfonylurea based on glucose monitoring, "
            "and measure weight and enquire about symptoms at each visit."
        ),
    )

    result = DeterministicRuleObjectVerifier().verify(spec, objects, [chunk])

    assert [accepted.type for accepted in result.accepted_objects] == [
        "indicator_or_standard",
        "measurement_method",
    ]
    assert [decision.accepted for decision in result.decisions] == [True, True]


def test_item_10_verifier_accepts_current_guideline_voting_terms_before_llm():
    spec = get_item_spec("item_10")
    obj = RuleObject(
        type="result_detail",
        value="final vote",
        evidence=EvidenceRef(chunk_id="chunk_001", quote="final vote"),
        confidence=0.95,
    )
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Methods",
        content="The final vote approved the current guideline recommendations.",
    )

    result = DeterministicRuleObjectVerifier().verify(spec, [obj], [chunk])

    assert [accepted.type for accepted in result.accepted_objects] == ["result_detail"]
    assert result.decisions[0].accepted is True


def test_item_18_extraction_prompt_excludes_clinical_risk_and_recommendation_rationale():
    prompt = render_extraction_prompt(
        get_item_spec("item_18"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="Diabetes increases infection risk, so remote care may reduce exposure.",
            )
        ],
    )

    assert "真实场景中应用、实施、推广或采纳" in prompt
    assert "资源" in prompt
    assert "培训" in prompt
    assert "工作流程" in prompt
    assert "疾病风险" in prompt
    assert "病理机制" in prompt
    assert "治疗获益" in prompt
    assert "普通临床推荐" in prompt
    assert "治疗禁忌" in prompt
    assert "患者病情复杂性" in prompt
    assert "monitor glucose regularly" in prompt
    assert "每类只保留最直接、最代表性的证据" in prompt


def test_item_14_scores_current_guideline_future_update_facts_without_grouping():
    spec = get_item_spec("item_14")
    store = ObjectStore(spec)
    objects = [
        _rule_object("current_document_future_update_mechanism", chunk_id="chunk_001"),
        _rule_object("update_interval", chunk_id="chunk_001"),
        _rule_object("update_criteria", chunk_id="chunk_003"),
        _rule_object("update_condition", chunk_id="chunk_010"),
    ]
    store.merge(objects, processed_chunks=["chunk_001", "chunk_003", "chunk_010"])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert spec.rule_profile == "guideline_update"
    assert spec.grouping is None
    assert result.score_range == "7"


def test_item_14_excludes_current_revision_only_object_from_scoring():
    spec = get_item_spec("item_14")
    store = ObjectStore(spec)
    obj = _rule_object("current_revision_only")
    store.merge([obj], processed_chunks=["chunk_001"])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert result.score_range == "1"
    assert result.review_reasons[-1].code == "excluded_guideline_update_scope"


def test_item_18_scores_accepted_application_factor_group():
    spec = get_item_spec("item_18")
    assert spec.rule_profile == "application_factor"
    assert spec.grouping is not None
    assert spec.grouping.group_profile == "application_factor"

    store = ObjectStore(spec)
    objects = [
        _rule_object("facilitator"),
        _rule_object("barrier"),
        _rule_object("collection_method"),
        _rule_object("impact_on_guideline_or_recommendations"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_18:application_factor:1",
        group_type="application_factor",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline_application",
            "rubric_role": "application_factor",
            "factor_direction": "mixed",
            "factor_specificity": "specific",
            "identification_basis": "explicit_collection_or_analysis",
            "application_impact": "affects_recommendations_or_strategy",
            "same_application_evidence_unit": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "7"
    assert result.matched_rules == [
        "facilitator",
        "barrier",
        "collection_method",
        "impact_on_guideline_or_recommendations",
    ]


def test_item_18_mixed_direction_attribute_does_not_create_missing_barrier_fact():
    spec = get_item_spec("item_18")
    store = ObjectStore(spec)
    objects = [_rule_object("facilitator")]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_18:application_factor:1",
        group_type="application_factor",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline_application",
            "rubric_role": "application_factor",
            "factor_direction": "mixed",
            "factor_specificity": "specific",
            "identification_basis": "explicit_collection_or_analysis",
            "application_impact": "affects_recommendations_or_strategy",
            "same_application_evidence_unit": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "2-3"
    assert result.matched_rules == ["facilitator"]


def test_item_18_complete_attributes_do_not_create_missing_collection_fact():
    spec = get_item_spec("item_18")
    store = ObjectStore(spec)
    objects = [
        _rule_object("facilitator"),
        _rule_object("barrier"),
        _rule_object("impact_on_guideline_or_recommendations"),
    ]
    store.merge(objects, processed_chunks=["chunk_001"])
    group = RuleObjectGroup(
        group_id="item_18:application_factor:1",
        group_type="application_factor",
        objects=tuple(objects),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "scope": "current_guideline_application",
            "rubric_role": "application_factor",
            "factor_direction": "mixed",
            "factor_specificity": "specific",
            "identification_basis": "explicit_collection_or_analysis",
            "application_impact": "affects_recommendations_or_strategy",
            "same_application_evidence_unit": True,
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "5-6"
    assert "collection_method" in result.missing


def test_ordered_milestone_reports_missing_from_next_higher_level():
    result = _evaluate("item_7", ["named_information_source", "search_terms"])

    assert result.score_range == "4"
    assert result.missing == ["search_time_coverage"]


def test_ordered_milestone_missing_uses_shortest_any_branch():
    result = _evaluate(
        "item_5",
        ["comprehensive_collection_method", "preference_summary"],
    )

    assert result.score_range == "5"
    assert result.missing == ["used_in_guideline_or_recommendations"]


def test_rule_condition_supports_nested_all_any_and_not():
    spec = ItemSpec(
        item_id="item_test",
        title="Test item",
        rule_profile="ordered_milestone",
        object_specs={
            "A": RuleObjectSpec(type="A", role="component", description="A"),
            "B": RuleObjectSpec(type="B", role="component", description="B"),
            "C": RuleObjectSpec(type="C", role="component", description="C"),
            "D": RuleObjectSpec(type="D", role="component", description="D"),
        },
        rule=MilestoneRule(
            levels=(
                ScoreLevel(
                    score_range="7",
                    condition=all_of(any_of(all_of("A", "B"), all_of("A", "C")), not_of("D")),
                ),
                ScoreLevel(score_range="1", condition=all_of()),
            )
        ),
    )
    store = ObjectStore(spec)
    store.merge([_rule_object("A"), _rule_object("C")], processed_chunks=["chunk_001"])

    result = OrderedMilestoneEngine().execute(spec, store.snapshot())

    assert result.score_range == "7"
    assert result.matched_rules == ["A", "C"]


def test_rule_condition_not_blocks_matching_when_child_exists():
    spec = ItemSpec(
        item_id="item_test",
        title="Test item",
        rule_profile="ordered_milestone",
        object_specs={
            "A": RuleObjectSpec(type="A", role="component", description="A"),
            "D": RuleObjectSpec(type="D", role="component", description="D"),
        },
        rule=MilestoneRule(
            levels=(
                ScoreLevel(score_range="7", condition=all_of("A", not_of("D"))),
                ScoreLevel(score_range="1", condition=all_of()),
            )
        ),
    )
    store = ObjectStore(spec)
    store.merge([_rule_object("A"), _rule_object("D")], processed_chunks=["chunk_001"])

    result = OrderedMilestoneEngine().execute(spec, store.snapshot())

    assert result.score_range == "1"


def test_rule_condition_not_block_adds_review_reason_for_next_level():
    spec = ItemSpec(
        item_id="item_test",
        title="Test item",
        rule_profile="ordered_milestone",
        object_specs={
            "A": RuleObjectSpec(type="A", role="component", description="A"),
            "D": RuleObjectSpec(type="D", role="component", description="D"),
        },
        rule=MilestoneRule(
            levels=(
                ScoreLevel(score_range="7", condition=all_of("A", not_of("D"))),
                ScoreLevel(score_range="3", condition=all_of("A")),
                ScoreLevel(score_range="1", condition=all_of()),
            )
        ),
    )
    store = ObjectStore(spec)
    store.merge([_rule_object("A"), _rule_object("D")], processed_chunks=["chunk_001"])

    result = OrderedMilestoneEngine().execute(spec, store.snapshot())

    assert result.score_range == "3"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["blocked_by_excluded_fact"]
    assert [reason.object_type for reason in result.review_reasons] == ["D"]


class _Item7ChunkExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        objects: list[RuleObject] = []
        for chunk in chunks:
            for object_type in spec.object_specs:
                if object_type in chunk.content:
                    objects.append(_rule_object(object_type, chunk_id=chunk.chunk_id))
        return ExtractionBatchResult(objects=objects)


def test_ordered_milestone_pipeline_merges_objects_across_chunks():
    result = run_rule_pipeline(
        "item_7",
        [
            EvidenceChunk(chunk_id="chunk_001", title="来源", content="named_information_source"),
            EvidenceChunk(chunk_id="chunk_002", title="词", content="search_terms"),
            EvidenceChunk(chunk_id="chunk_003", title="时间", content="search_time_coverage"),
            EvidenceChunk(chunk_id="chunk_004", title="策略", content="complete_search_strategy"),
        ],
        extractor=_Item7ChunkExtractor(),
        batch_size=1,
    )

    assert result.rule_result.score_range == "6-7"
    assert result.snapshot.processed_chunks == ("chunk_001", "chunk_002", "chunk_003", "chunk_004")


def test_ordered_milestone_returns_error_when_score_levels_missing():
    spec = ItemSpec(
        item_id="item_empty",
        title="Empty",
        rule_profile="ordered_milestone",
        object_specs={},
        rule=MilestoneRule(levels=()),
    )

    result = OrderedMilestoneEngine().execute(
        spec,
        ObjectStore(spec).snapshot(),
    )

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["missing_score_levels"]


def test_ordered_milestone_returns_error_when_no_score_level_matches():
    spec = ItemSpec(
        item_id="item_no_match",
        title="No match",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        rule=MilestoneRule(levels=(ScoreLevel(score_range="7", condition=all_of("A")),)),
    )

    result = OrderedMilestoneEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["no_score_level_matched"]


@pytest.mark.parametrize(
    ("condition", "expected_code"),
    [
        (RuleCondition(op="fact"), "invalid_rule_condition"),
        (RuleCondition(op="any"), "invalid_rule_condition"),
        (RuleCondition(op="not"), "invalid_rule_condition"),
        (all_of("unknown_fact"), "unknown_condition_fact"),
    ],
)
def test_ordered_milestone_returns_error_for_invalid_conditions(condition, expected_code):
    spec = ItemSpec(
        item_id="item_invalid",
        title="Invalid",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        rule=MilestoneRule(levels=(ScoreLevel(score_range="7", condition=condition),)),
    )

    result = OrderedMilestoneEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == [expected_code]


def test_ordered_milestone_returns_error_for_invalid_score_range():
    spec = ItemSpec(
        item_id="item_invalid_range",
        title="Invalid range",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        rule=MilestoneRule(
            levels=(
                ScoreLevel(score_range="6–7", condition=all_of("A")),
                ScoreLevel(score_range="1", condition=all_of()),
            )
        ),
    )

    result = OrderedMilestoneEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["invalid_score_range"]


def test_ordered_milestone_returns_error_when_fallback_is_not_last():
    spec = ItemSpec(
        item_id="item_bad_fallback",
        title="Bad fallback",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        rule=MilestoneRule(
            levels=(
                ScoreLevel(score_range="1", condition=all_of()),
                ScoreLevel(score_range="7", condition=all_of("A")),
            )
        ),
    )

    result = OrderedMilestoneEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["fallback_not_last"]
