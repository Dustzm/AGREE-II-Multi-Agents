from __future__ import annotations

import pytest

from core.rule_engine.engines.profiles import WeightedChecklistEngine
from core.rule_engine.models import (
    BranchCase,
    EvidenceChunk,
    EvidenceRef,
    ItemSpec,
    RuleObject,
    RuleObjectSpec,
    WeightedChecklistItem,
    WeightedChecklistRule,
    all_of,
)
from core.rule_engine.prompts.extraction import render_extraction_prompt
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.stores.object_store import ObjectStore
from core.rule_engine.verification import DeterministicRuleObjectVerifier


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


@pytest.mark.parametrize(
    ("object_types", "score_range", "missing"),
    [
        (
            [],
            "1",
            [
                "member_name",
                "specialty_or_discipline",
                "institution",
                "geographic_location",
                "member_role",
                "multidisciplinary_team",
            ],
        ),
        (
            ["member_name", "institution", "member_role"],
            "4",
            ["specialty_or_discipline", "geographic_location", "multidisciplinary_team"],
        ),
        (
            [
                "member_name",
                "specialty_or_discipline",
                "institution",
                "geographic_location",
                "member_role",
                "multidisciplinary_team",
            ],
            "7",
            [],
        ),
    ],
)
def test_item_4_weighted_checklist_scores_expected_ranges(object_types, score_range, missing):
    result = _evaluate("item_4", object_types)

    assert result.score_range == score_range
    assert result.allowed_scores == [int(score_range)]
    assert result.missing == missing
    assert result.needs_review is False


@pytest.mark.parametrize(
    ("object_types", "score_range", "allowed_scores", "missing"),
    [
        ([], "1", [1], ["clear_selection_criteria"]),
        (
            ["simple_selection_criteria_mention"],
            "2-3",
            [2, 3],
            ["clear_selection_criteria"],
        ),
        (
            ["language"],
            "2-3",
            [2, 3],
            ["clear_selection_criteria"],
        ),
        (
            ["study_design"],
            "2-3",
            [2, 3],
            ["clear_selection_criteria"],
        ),
        (["clear_selection_criteria"], "3", [3], ["study_design", "pico_elements", "language", "setting_or_context"]),
        (["clear_selection_criteria", "study_design"], "4", [4], ["pico_elements", "language", "setting_or_context"]),
        (["clear_selection_criteria", "pico_elements"], "5", [5], ["study_design", "language", "setting_or_context"]),
        (
            ["clear_selection_criteria", "study_design", "pico_elements", "language", "setting_or_context"],
            "7",
            [7],
            [],
        ),
    ],
)
def test_item_8_gate_and_weighted_checklist_scores_expected_ranges(
    object_types,
    score_range,
    allowed_scores,
    missing,
):
    result = _evaluate("item_8", object_types)

    assert result.score_range == score_range
    assert result.allowed_scores == allowed_scores
    assert result.missing == missing
    assert result.needs_review is False


def test_item_8_score_is_capped_at_seven():
    result = _evaluate(
        "item_8",
        [
            "clear_selection_criteria",
            "study_design",
            "pico_elements",
            "language",
            "setting_or_context",
        ],
    )

    assert result.score_range == "7"
    assert result.max_score == 7


def test_item_8_matched_rules_include_gate_after_gate_is_satisfied():
    result = _evaluate("item_8", ["clear_selection_criteria", "study_design"])

    assert result.score_range == "4"
    assert result.matched_rules == ["clear_selection_criteria", "study_design"]


@pytest.mark.parametrize(
    ("object_types", "score_range", "allowed_scores"),
    [
        ([], "1", [1]),
        (["study_design_description"], "2", [2]),
        (["study_design_description", "result_direction"], "3", [3]),
        (["evidence_assessment_method"], "4", [4]),
        (["evidence_assessment_method", "explicit_evidence_level"], "4", [4]),
        (["evidence_assessment_method", "result_consistency"], "5", [5]),
        (
            [
                "evidence_assessment_method",
                "explicit_evidence_level",
                "evidence_grading_system_applied",
                "study_design_description",
                "result_consistency",
            ],
            "7",
            [7],
        ),
        (
            [
                "evidence_assessment_method",
                "explicit_evidence_level",
                "study_design_description",
                "methodological_limitation",
                "result_consistency",
                "practice_applicability",
            ],
            "7",
            [7],
        ),
    ],
)
def test_item_9_evidence_appraisal_engine_scores_unique_dimensions(
    object_types,
    score_range,
    allowed_scores,
):
    result = _evaluate("item_9", object_types)

    assert result.score_range == score_range
    assert result.allowed_scores == allowed_scores
    assert result.needs_review is False


def test_item_9_formal_appraisal_path_can_score_seven_without_explicit_limitation():
    result = _evaluate(
        "item_9",
        [
            "evidence_assessment_method",
            "explicit_evidence_level",
            "evidence_grading_system_applied",
            "study_design_description",
            "result_consistency",
            "practice_applicability",
        ],
    )

    assert result.score_range == "7"
    assert "methodological_limitation" not in result.missing


def test_item_9_single_formal_method_still_needs_limitation_or_applied_system():
    result = _evaluate(
        "item_9",
        [
            "evidence_assessment_method",
            "explicit_evidence_level",
            "study_design_description",
            "result_consistency",
            "practice_applicability",
        ],
    )

    assert result.score_range == "6"
    assert "methodological_limitation" in result.missing


def test_item_9_formal_path_requires_applied_grading_system_anchor():
    result = _evaluate(
        "item_9",
        [
            "evidence_assessment_method",
            "explicit_evidence_level",
            "study_design_description",
            "result_consistency",
        ],
    )

    assert result.score_range == "5"
    assert "methodological_limitation" in result.missing


def test_item_9_exclusion_objects_do_not_score():
    result = _evaluate(
        "item_9",
        [
            "ordinary_clinical_conclusion",
            "single_study_result_summary",
            "evidence_selection_criteria",
            "recommendation_formulation_process",
            "clinical_background_or_workflow",
        ],
    )

    assert result.score_range == "1"
    assert {reason.code for reason in result.review_reasons} == {"excluded_evidence_appraisal_object"}


def test_item_9_score_is_capped_at_seven():
    result = _evaluate(
        "item_9",
        [
            "evidence_assessment_method",
            "explicit_evidence_level",
            "study_design_description",
            "methodological_limitation",
            "outcome_relevance",
            "result_consistency",
            "result_direction",
            "benefit_harm_magnitude",
            "practice_applicability",
        ],
    )

    assert result.score_range == "7"
    assert result.max_score == 7


def test_item_9_duplicate_dimension_does_not_increase_score():
    spec = get_item_spec("item_9")
    store = ObjectStore(spec)
    store.merge(
        [
            _rule_object("evidence_assessment_method", "GRADE"),
            _rule_object("evidence_assessment_method", "risk of bias assessment"),
        ],
        processed_chunks=["chunk_001"],
    )

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot())

    assert result.score_range == "4"
    assert result.matched_rules == ["evidence_assessment_method"]


def test_item_9_extraction_prompt_requires_evidence_appraisal_context():
    prompt = render_extraction_prompt(
        get_item_spec("item_9"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Clinical recommendation",
                content="The trial showed benefit and the panel recommends treatment.",
            )
        ],
    )

    assert "证据强度、局限性、质量、确定性" in prompt
    assert "GRADE" in prompt
    assert "evidence_grading_system_applied" in prompt
    assert "普通临床结论" in prompt
    assert "普通推荐正文" in prompt
    assert "单篇研究结果摘录" in prompt
    assert "文献纳入/排除/筛选标准" in prompt
    assert "推荐意见形成/投票/共识过程" in prompt
    assert "没有评价证据质量、强度、局限性、确定性、适用性或证据体特征" in prompt


def test_item_9_deterministic_verifier_tags_selection_context_but_leaves_llm_decision():
    spec = get_item_spec("item_9")
    obj = RuleObject(
        type="study_design_description",
        value="randomized trials",
        evidence=EvidenceRef(
            chunk_id="chunk_001",
            quote="randomized trials",
        ),
        confidence=0.95,
    )
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Study selection",
        content="Inclusion criteria were randomized trials of diabetes interventions.",
    )

    result = DeterministicRuleObjectVerifier().verify(spec, [obj], [chunk])

    assert result.accepted_objects == [obj]
    assert result.decisions[0].accepted is True
    assert result.decisions[0].tags == ("negative_context_term_matched:inclusion criteria",)


def test_item_8_extraction_prompt_requires_evidence_selection_context():
    prompt = render_extraction_prompt(
        get_item_spec("item_8"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Employment evaluations",
                content="Urine glucose tests should never be used to evaluate employability.",
            )
        ],
    )

    assert "检索所得证据、研究、文献、记录或文章" in prompt
    assert "evidence identified by the search" in prompt
    assert "就业评估标准" in prompt
    assert "临床判断标准" in prompt
    assert "诊断/检测标准" in prompt
    assert "疾病状态" in prompt
    assert "工作岗位" in prompt
    assert "患者/员工是否符合某项推荐或评估条件" in prompt
    assert "治疗或筛查适用条件" in prompt
    assert "included/excluded studies" in prompt
    assert "study selection" in prompt
    assert "同一句或相邻句" in prompt
    assert "A1C/FPG/OGTT" in prompt
    assert "Medicare" in prompt
    assert "推荐适用人群" in prompt
    assert "不要因为标准内容涉及 diagnostic criteria、eligibility" in prompt
    assert "每类对象只需要证明该事实存在" not in prompt


def test_item_8_deterministic_verifier_tags_but_does_not_reject_selection_context():
    spec = get_item_spec("item_8")
    obj = RuleObject(
        type="pico_elements",
        value="studies using diagnostic criteria for type 2 diabetes",
        evidence=EvidenceRef(
            chunk_id="chunk_001",
            quote="studies using diagnostic criteria for type 2 diabetes",
        ),
        confidence=0.95,
    )
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Study selection",
        content=(
            "Inclusion criteria were randomized studies using diagnostic criteria "
            "for type 2 diabetes."
        ),
    )

    result = DeterministicRuleObjectVerifier().verify(spec, [obj], [chunk])

    assert result.accepted_objects == [obj]
    assert result.decisions[0].accepted is True
    assert result.decisions[0].tags == ("negative_context_term_matched:diagnostic criteria",)


def test_item_4_extraction_prompt_limits_representative_member_evidence():
    prompt = render_extraction_prompt(
        get_item_spec("item_4"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Committee",
                content="A large author list includes many names and institutions.",
            )
        ],
    )

    assert "at most 3 representative object(s)" in prompt
    assert "不要逐个穷举全部成员、机构、专业或地区" in prompt
    assert "只保留最直接、最有代表性的少量证据" in prompt


def test_weighted_checklist_returns_error_for_invalid_score_range():
    spec = ItemSpec(
        item_id="item_invalid_weighted",
        title="Invalid weighted",
        rule_profile="weighted_checklist",
        object_specs={
            "gate": RuleObjectSpec(type="gate", role="gate", description="gate"),
            "A": RuleObjectSpec(type="A", role="component", description="A"),
        },
        rule=WeightedChecklistRule(
            items=(WeightedChecklistItem(fact="A"),),
            gate=all_of("gate"),
            gate_score_range="bad",
        ),
    )

    result = WeightedChecklistEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["invalid_score_range"]


def test_weighted_checklist_returns_error_when_gate_fallback_is_not_last():
    spec = ItemSpec(
        item_id="item_bad_fallback",
        title="Bad fallback",
        rule_profile="weighted_checklist",
        object_specs={
            "gate": RuleObjectSpec(type="gate", role="gate", description="gate"),
            "A": RuleObjectSpec(type="A", role="component", description="A"),
        },
        rule=WeightedChecklistRule(
            items=(WeightedChecklistItem(fact="A"),),
            gate=all_of("gate"),
            gate_fallback_cases=(
                BranchCase(name="fallback", condition=all_of(), score_range="1"),
                BranchCase(name="specific", condition=all_of("A"), score_range="2-3"),
            ),
        ),
    )

    result = WeightedChecklistEngine().execute(spec, ObjectStore(spec).snapshot())

    assert result.status == "error"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["fallback_not_last"]
