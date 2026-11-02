"""
Rule specification for AGREE-II item 18.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ApplicationFactorRule,
    ChunkRoutingSpec,
    GroupingSpec,
    ItemSpec,
    ObjectVerificationSpec,
    RubricAppraiserSpec,
    RubricUnitSpec,
    RuleObjectSpec,
)


_IMPLEMENTATION_VERIFICATION = ObjectVerificationSpec(
    llm_required=True,
    guidance=(
        "Verify only whether the quote can serve as candidate evidence for item 18. Do not decide the final score. "
        "The final rubric judgment belongs to the group verifier: whether the evidence identifies a condition that "
        "affects application of the current guideline, its direction, basis, and link to application."
    ),
)


ITEM_18_SPEC = ItemSpec(
    item_id="item_18",
    title="The guideline describes facilitators and barriers to its application.",
    rule_profile="application_factor",
    object_specs={
        "facilitator": RuleObjectSpec(
            type="facilitator",
            role="component",
            description="指南推荐在实际应用、实施、推广或采纳过程中的促进因素。",
            max_objects=5,
            verification=_IMPLEMENTATION_VERIFICATION,
        ),
        "barrier": RuleObjectSpec(
            type="barrier",
            role="component",
            description="指南推荐在实际应用、实施、推广或采纳过程中的阻碍因素、障碍或限制。",
            max_objects=5,
            verification=_IMPLEMENTATION_VERIFICATION,
        ),
        "collection_method": RuleObjectSpec(
            type="collection_method",
            role="component",
            description="实施促进或阻碍因素的收集、识别、调查、访谈、问卷、试点、质量改进回顾或分析方法。",
            max_objects=5,
            verification=_IMPLEMENTATION_VERIFICATION,
        ),
        "impact_on_guideline_or_recommendations": RuleObjectSpec(
            type="impact_on_guideline_or_recommendations",
            role="component",
            description="说明实施促进或阻碍因素如何影响指南开发过程、实施策略、工具配置或推荐意见。",
            max_objects=5,
            verification=_IMPLEMENTATION_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取会影响指南推荐在真实场景中应用、实施、推广或采纳的促进和阻碍因素，例如资源、人员、培训、成本、可及性、"
        "患者依从性、工作流程、信息系统、服务容量、地区差异、实施工具或制度安排。"
        "这些因素必须影响推荐意见能否被临床、机构、服务系统或目标用户实际采用；仅描述患者疾病状态、治疗选择、护理步骤或临床管理要求不算。"
        "不要把普通推荐理由、疾病风险、病理机制、证据质量、治疗获益、公共卫生负担、患者临床风险、药物不良反应、治疗禁忌、"
        "用药安全观察、诊断条件、患者病情复杂性、护理建议或背景事实"
        "误判为实施促进/阻碍因素，除非文本明确说明这些因素影响指南实施或推荐采纳。"
        "例如 'continued access to devices' 可作为可及性因素；但 'monitor glucose regularly'、'avoid drug in renal failure'、"
        "'patients with comorbidities need care' 这类普通临床建议不能单独作为 facilitator 或 barrier。"
        "impact_on_guideline_or_recommendations 必须说明某个实施促进/阻碍因素如何改变指南开发过程、实施策略、工具配置或推荐意见；"
        "不能因为文本基于疾病风险给出普通临床推荐，就抽取为 impact。"
        "每类只保留最直接、最代表性的证据，不要穷举所有患者挑战、治疗风险或服务建议。"
    ),
    verification_guidance=(
        "AGREE-II item 18 evaluates whether the current guideline identifies conditions that affect application of "
        "its recommendations. The rubric asks whether an application factor exists, whether its direction is a "
        "facilitator, barrier, or mixed, whether the basis for identifying the factor is described, and whether the "
        "factor is linked to guideline application or recommendations. The group verifier should judge the evidence's "
        "functional role in the guideline, not the terminology or heading used."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "implementation",
            "facilitators",
            "barriers",
            "applicability",
            "feasibility",
            "resource implications",
            "实施",
            "应用",
            "障碍",
            "促进因素",
        ),
        include_phrases=(
            "barriers to implementation",
            "facilitators to implementation",
            "implementation barriers",
            "implementation facilitators",
            "application of the recommendations",
            "uptake of recommendations",
            "adoption of recommendations",
            "feasibility of implementation",
            "resource requirements",
            "capacity constraints",
            "staff training",
            "staffing constraints",
            "access to care",
            "financial barriers",
            "implementation considerations",
            "促进因素",
            "阻碍因素",
            "实施障碍",
            "推荐应用",
        ),
        include_terms=(
            "implementation",
            "barrier",
            "facilitator",
            "feasibility",
            "adoption",
            "uptake",
            "workflow",
            "resources",
            "capacity",
            "staff",
            "staffing",
            "training",
            "access",
            "cost",
            "financial",
            "实施",
            "应用",
            "采纳",
            "障碍",
            "资源",
        ),
        negative_terms=(
            "contraindication",
            "adverse effect",
            "risk of hypoglycaemia",
            "risk of hypoglycemia",
            "diagnostic criteria",
            "monitor glucose",
        ),
        neighbor_window=1,
        max_chunks=22,
        max_domain_chunks=10,
    ),
    grouping=GroupingSpec(
        group_profile="application_factor",
        anchor_object_types=("facilitator", "barrier"),
        member_object_types=(
            "facilitator",
            "barrier",
            "collection_method",
            "impact_on_guideline_or_recommendations",
        ),
        neighbor_window=1,
        same_section=True,
        max_groups=10,
        llm_required=True,
        guidance=(
            "Build local groups around application factors. Keep candidate grouping high-recall; the group verifier "
            "decides whether objects belong to the same application evidence unit."
        ),
    ),
    rubric_appraiser=RubricAppraiserSpec(
        profile="item18_application_factors_appraiser",
        max_units=8,
        unit_char_limit=1400,
        decision_labels=(
            "no_relevant_evidence",
            "excluded_clinical_content",
            "facilitator_or_barrier_described",
            "facilitator_and_barrier_described",
            "factors_with_basis_or_impact",
            "complete_application_factor_assessment",
        ),
        guidance=(
            "AGREE-II item 18 evaluates whether the current guideline describes facilitators and barriers that "
            "affect application of its recommendations. Score 1 when not mentioned; 2-3 for a facilitator or "
            "barrier; 4 for both facilitator and barrier; 5-6 for both plus either collection/identification "
            "methods or impact on recommendations; and 7 when factors, collection methods, and impact on guideline "
            "development and/or recommendations are all described. Do not count ordinary clinical risks, treatment "
            "contraindications, disease burden, recommendation rationale, or background facts unless the evidence "
            "functions as an application/implementation factor for this guideline."
        ),
    ),
    rubric_unit=RubricUnitSpec(
        profile="item18_application_factors",
        bands=(
            "no_evidence",
            "single_direction_factor",
            "both_facilitator_and_barrier",
            "both_plus_collection_or_impact",
            "factors_plus_collection_and_impact",
            "excluded_clinical_content",
        ),
        max_units=10,
        guidance=(
            "Deprecated rubric-unit configuration retained only for unit-level builder/classifier tests. "
            "Active scoring uses the application_factor object/group rule engine."
        ),
    ),
    rule=ApplicationFactorRule(),
)
