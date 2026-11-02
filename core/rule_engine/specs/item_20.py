"""
Rule specification for AGREE-II item 20.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    ItemSpec,
    MilestoneRule,
    ObjectVerificationSpec,
    RuleObjectSpec,
    ScoreLevel,
    all_of,
    any_of,
)


_RESOURCE_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "resource",
        "cost",
        "economic",
        "budget",
        "funding",
        "staff",
        "equipment",
        "implementation",
        "applying the recommendation",
        "application of the recommendation",
        "资源",
        "成本",
        "费用",
        "经济",
        "预算",
        "实施",
        "应用",
    ),
    negative_context_terms=(
        "no cost to employer",
        "undue burden",
        "workplace accommodation",
        "disease burden",
        "economic burden of disease",
        "drug price",
        "price of drug",
        "cost-effectiveness of the study",
        "cost effectiveness of the study",
        "included study",
        "trial cost",
        "雇主",
        "工作场所便利",
        "疾病负担",
        "药品价格",
    ),
    llm_required=True,
    guidance=(
        "Accept resource, cost, budget, staffing, equipment, program cost, payment/coverage, cost-effectiveness, "
        "economic evaluation, or survey information when it is relevant to applying recommendations or guideline "
        "implementation. Cost/economic issues, economic evaluation proposals, and concrete survey/data information "
        "can stand on their own for a middle score. Only accept resource_information_used_in_recommendations when the "
        "quote directly says resource, cost, survey, or economic information was considered, informed, was used to "
        "formulate, shaped, determined, or changed recommendation formation, recommendation selection, recommendation "
        "strength, recommendation content, or an implementation decision. "
        "Do not accept a recommendation that merely specifies staffing, service level, equipment, or coverage as this "
        "use-in-recommendations object. Do not accept 'costs may decrease', a cost differential/comparison, affordability "
        "rationale, or a resource recommendation itself as use in recommendation formation unless the text explicitly "
        "links that information to how the recommendations were formed or selected. "
        "Reject background economic burden of the disease, stand-alone drug prices, cost-effectiveness of cited studies, "
        "ordinary clinical content, and workplace accommodation costs unless they are explicitly framed as resource "
        "implications of applying the current recommendations."
    ),
)


ITEM_20_SPEC = ItemSpec(
    item_id="item_20",
    title="The potential resource implications of applying the recommendations have been considered.",
    rule_profile="ordered_milestone",
    object_specs={
        "resource_implication_context": RuleObjectSpec(
            type="resource_implication_context",
            role="component",
            description="明确将资源、成本、预算、人力、设备或经济影响关联到推荐意见应用、指南实施或实施决策。",
            max_objects=5,
            verification=_RESOURCE_VERIFICATION,
        ),
        "vague_resource_mention": RuleObjectSpec(
            type="vague_resource_mention",
            role="component",
            description="在推荐应用或指南实施语境中模糊提及资源、成本、预算、人力、设备或实施负担，但缺乏具体信息。",
            max_objects=5,
            verification=_RESOURCE_VERIFICATION,
        ),
        "cost_or_economic_issue": RuleObjectSpec(
            type="cost_or_economic_issue",
            role="component",
            description="提及推荐应用或指南实施所需资源、人员、设备、预算、成本、支付覆盖、成本效果或其他具体经济问题。",
            max_objects=5,
            verification=_RESOURCE_VERIFICATION,
        ),
        "economic_evaluation_proposal": RuleObjectSpec(
            type="economic_evaluation_proposal",
            role="component",
            description="针对推荐意见应用或指南实施提出经济学评价、成本效果分析、预算影响分析、卫生经济学评估或相关建议。",
            max_objects=5,
            verification=_RESOURCE_VERIFICATION,
        ),
        "survey_information": RuleObjectSpec(
            type="survey_information",
            role="component",
            description="提供推荐应用或指南实施所需资源、人员、设备、成本、支付覆盖或经济影响方面的调查、测算、数据或具体信息。",
            max_objects=5,
            verification=_RESOURCE_VERIFICATION,
        ),
        "resource_information_used_in_recommendations": RuleObjectSpec(
            type="resource_information_used_in_recommendations",
            role="component",
            description=(
                "明确说明资源、调查、成本或经济信息被考虑、用于或影响了指南/推荐意见形成、推荐选择、"
                "推荐强度、推荐内容或实施决策；不能只是推荐某种人员、服务、设备或资源配置。"
            ),
            max_objects=5,
            verification=_RESOURCE_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取推荐意见应用、指南实施或实施决策中的资源、成本、经济、预算、人力、设备、项目成本、支付覆盖或成本效果相关对象。"
        "具体成本/经济问题、经济评价建议或调查信息本身可进入中档，不要求额外抽到 resource_implication_context；"
        "调查信息包括资源可及性、设备/人员/项目成本、预算、支付覆盖、补贴、报销、费用表、用药或服务成本数据等可支持推荐应用判断的信息。"
        "只有说明这些资源、调查、成本或经济信息如何用于指南或推荐意见形成、推荐选择、推荐强度或实施决策时，"
        "才抽取 resource_information_used_in_recommendations，例如 considered/informed/used to formulate/shaped recommendations。"
        "不要把推荐本身写了人员配置、服务水平、设备要求或报销覆盖，误抽为 resource_information_used_in_recommendations；"
        "不要把 cost may go down、成本差异/成本比较、可负担性理由或资源推荐本身当作“用于形成推荐”的证据，"
        "除非文本明确说明这些信息如何影响推荐形成、选择、强度、内容或实施决策。不要把疾病经济负担背景、"
        "药品价格常识、引用研究的 cost-effectiveness、普通临床内容、雇主义务、工作场所便利措施的一般成本、"
        "no cost to employer 或 undue burden 误判为 item20，除非文本明确把它们作为当前指南实施或推荐应用的资源影响来讨论。"
    ),
    verification_guidance=(
        "AGREE-II item 20 assesses whether the guideline considered potential resource implications of applying the "
        "recommendations. Middle scores are supported by any concrete cost/economic issue, economic evaluation proposal, "
        "or specific survey/data information about resources, staffing, equipment, budgets, payment, coverage, "
        "reimbursement, subsidies, program costs, or economic impact. High scores require both such resource information "
        "and a direct statement that the resource, cost, survey, or economic information was considered, informed, was "
        "used to formulate, shaped, determined, or changed guideline/recommendation formation, recommendation selection, "
        "recommendation strength, recommendation content, or implementation decisions. A recommendation that itself "
        "specifies staffing level, service level, equipment, coverage, or resources is not enough for the high-score "
        "use object. Expected cost reductions, cost differentials, affordability rationale, or resource recommendations "
        "also do not show use in recommendation formation unless explicitly linked to formation/selection/strength/content. "
        "Vague mentions of resources or implementation burden "
        "without detail support only low scores. Do not require a separate context object before accepting concrete "
        "survey/cost/economic information."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "resource implications",
            "costs",
            "economic considerations",
            "implementation",
            "applicability",
            "资源",
            "成本",
            "经济",
        ),
        include_phrases=(
            "resource implications",
            "cost implications",
            "economic implications",
            "budget impact",
            "cost-effectiveness",
            "economic evaluation",
            "resources required",
            "implementation costs",
            "applying the recommendations",
            "资源影响",
            "成本影响",
            "经济评价",
            "预算影响",
        ),
        include_terms=(
            "resource",
            "cost",
            "economic",
            "budget",
            "funding",
            "equipment",
            "staff",
            "implementation",
            "资源",
            "成本",
            "经济",
            "预算",
        ),
        negative_terms=(
            "no cost to employer",
            "undue burden",
            "workplace accommodation",
            "economic burden of disease",
        ),
        neighbor_window=1,
        max_chunks=20,
        max_domain_chunks=8,
    ),
    rule=MilestoneRule(
        levels=(
            ScoreLevel(
                score_range="6-7",
                condition=any_of(
                    all_of("cost_or_economic_issue", "resource_information_used_in_recommendations"),
                    all_of("economic_evaluation_proposal", "resource_information_used_in_recommendations"),
                    all_of("survey_information", "resource_information_used_in_recommendations"),
                ),
                description="提供具体资源/经济信息，并说明其如何用于指南或推荐形成。",
            ),
            ScoreLevel(
                score_range="4-5",
                condition=any_of(
                    "cost_or_economic_issue",
                    "economic_evaluation_proposal",
                    "survey_information",
                ),
                description="提及成本、经济问题、经济评价建议或具体调查信息。",
            ),
            ScoreLevel(
                score_range="2-3",
                condition=any_of("resource_implication_context", "vague_resource_mention"),
                description="仅模糊提及资源或成本。",
            ),
            ScoreLevel(score_range="1", condition=all_of(), description="未提及。"),
        ),
    ),
)
