"""
Registry for rule-engine item specs and rule profiles.
"""
from __future__ import annotations

from core.rule_engine.engines.base import RuleEngine
from core.rule_engine.engines.profiles import (
    ApplicationFactorEngine,
    BenefitHarmBalanceEngine,
    BranchingPathEngine,
    CompetingInterestDisclosureEngine,
    EvidenceAppraisalEngine,
    FundingDisclosureEngine,
    GateComponentCountEngine,
    GuidelineUpdateEngine,
    HealthQuestionEngine,
    ImplementationToolEngine,
    ManagementOptionsEngine,
    MonitoringCriteriaEngine,
    OrderedMilestoneEngine,
    RecommendationEvidenceLinkEngine,
    TargetUserEngine,
    WeightedChecklistEngine,
)
from core.rule_engine.models import ItemSpec
from core.rule_engine.rubric.engine import RubricUnitEngine
from core.rule_engine.rubric.appraiser_engine import RubricAppraiserEngine
from core.rule_engine.specs import (
    ITEM_1_SPEC,
    ITEM_2_SPEC,
    ITEM_3_SPEC,
    ITEM_4_SPEC,
    ITEM_5_SPEC,
    ITEM_6_SPEC,
    ITEM_7_SPEC,
    ITEM_8_SPEC,
    ITEM_9_SPEC,
    ITEM_10_SPEC,
    ITEM_11_SPEC,
    ITEM_12_SPEC,
    ITEM_13_SPEC,
    ITEM_14_SPEC,
    ITEM_15_SPEC,
    ITEM_16_SPEC,
    ITEM_18_SPEC,
    ITEM_19_SPEC,
    ITEM_20_SPEC,
    ITEM_21_SPEC,
    ITEM_22_SPEC,
    ITEM_23_SPEC,
)


ITEM_SPECS: dict[str, ItemSpec] = {
    ITEM_1_SPEC.item_id: ITEM_1_SPEC,
    ITEM_2_SPEC.item_id: ITEM_2_SPEC,
    ITEM_3_SPEC.item_id: ITEM_3_SPEC,
    ITEM_4_SPEC.item_id: ITEM_4_SPEC,
    ITEM_5_SPEC.item_id: ITEM_5_SPEC,
    ITEM_6_SPEC.item_id: ITEM_6_SPEC,
    ITEM_7_SPEC.item_id: ITEM_7_SPEC,
    ITEM_8_SPEC.item_id: ITEM_8_SPEC,
    ITEM_9_SPEC.item_id: ITEM_9_SPEC,
    ITEM_10_SPEC.item_id: ITEM_10_SPEC,
    ITEM_11_SPEC.item_id: ITEM_11_SPEC,
    ITEM_12_SPEC.item_id: ITEM_12_SPEC,
    ITEM_13_SPEC.item_id: ITEM_13_SPEC,
    ITEM_14_SPEC.item_id: ITEM_14_SPEC,
    ITEM_15_SPEC.item_id: ITEM_15_SPEC,
    ITEM_16_SPEC.item_id: ITEM_16_SPEC,
    ITEM_18_SPEC.item_id: ITEM_18_SPEC,
    ITEM_19_SPEC.item_id: ITEM_19_SPEC,
    ITEM_20_SPEC.item_id: ITEM_20_SPEC,
    ITEM_21_SPEC.item_id: ITEM_21_SPEC,
    ITEM_22_SPEC.item_id: ITEM_22_SPEC,
    ITEM_23_SPEC.item_id: ITEM_23_SPEC,
}

RULE_ENGINES: dict[str, RuleEngine] = {
    "application_factor": ApplicationFactorEngine(),
    "benefit_harm_balance": BenefitHarmBalanceEngine(),
    "branching_path": BranchingPathEngine(),
    "competing_interest_disclosure": CompetingInterestDisclosureEngine(),
    "evidence_appraisal": EvidenceAppraisalEngine(),
    "funding_disclosure": FundingDisclosureEngine(),
    "gate_component_count": GateComponentCountEngine(),
    "guideline_update": GuidelineUpdateEngine(),
    "health_question": HealthQuestionEngine(),
    "implementation_tool": ImplementationToolEngine(),
    "management_options": ManagementOptionsEngine(),
    "monitoring_criteria": MonitoringCriteriaEngine(),
    "ordered_milestone": OrderedMilestoneEngine(),
    "recommendation_evidence_link": RecommendationEvidenceLinkEngine(),
    "rubric_appraiser": RubricAppraiserEngine(),
    "rubric_unit": RubricUnitEngine(),
    "target_user": TargetUserEngine(),
    "weighted_checklist": WeightedChecklistEngine(),
}


def get_item_spec(item_id: str) -> ItemSpec:
    """Return the registered spec for an item."""
    try:
        return ITEM_SPECS[item_id]
    except KeyError as exc:
        raise ValueError(f"未注册规则条目: {item_id}") from exc


def get_rule_engine(rule_profile: str) -> RuleEngine:
    """Return the registered rule engine for a profile."""
    try:
        return RULE_ENGINES[rule_profile]
    except KeyError as exc:
        raise ValueError(f"未注册规则 profile: {rule_profile}") from exc
