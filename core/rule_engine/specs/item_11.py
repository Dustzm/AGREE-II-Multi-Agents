"""
Rule specification for AGREE-II item 11.
"""
from __future__ import annotations

from core.rule_engine.models import (
    BenefitHarmBalanceRule,
    ChunkRoutingSpec,
    GroupingSpec,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
)


_BENEFIT_HARM_VERIFICATION = ObjectVerificationSpec(
    llm_required=True,
    guidance=(
        "Accept only candidate spans that may support AGREE-II item 11 for the current guideline: a concrete "
        "recommendation/option, benefit evidence, harm/side-effect/risk evidence, supporting data for either side, "
        "or an explicit benefit-harm balance statement used in recommendation formulation. Do not decide the final "
        "score at object level. Reject generic efficacy/safety text, adverse-event lists, contraindications, "
        "precautions, background clinical risk, or methods statements unless the span can be tied to a current "
        "recommendation, option, or management decision. Do not treat a drug name, table row label, contraindication, "
        "adverse-event list, or background risk as a recommendation/option anchor by itself."
    ),
)


ITEM_11_SPEC = ItemSpec(
    item_id="item_11",
    title="The health benefits, side effects, and risks have been considered in formulating the recommendations.",
    rule_profile="benefit_harm_balance",
    object_specs={
        "recommendation_or_option": RuleObjectSpec(
            type="recommendation_or_option",
            role="component",
            description="当前指南中的具体推荐、治疗/筛查/管理选项或管理决策，可作为利弊评价的对象。",
            max_objects=8,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
        "health_benefit": RuleObjectSpec(
            type="health_benefit",
            role="component",
            description="同一推荐、选项或管理决策的健康获益，如降低死亡、改善症状、提高生活质量、减少并发症或改善临床结局。",
            max_objects=8,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
        "harm_side_effect_or_risk": RuleObjectSpec(
            type="harm_side_effect_or_risk",
            role="component",
            description="同一推荐、选项或管理决策的危害、副作用、安全性问题、风险或不良后果。",
            max_objects=8,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
        "benefit_supporting_data": RuleObjectSpec(
            type="benefit_supporting_data",
            role="component",
            description="支持获益判断的数据、研究证据、效应量、结局结果、证据摘要或证据等级。",
            max_objects=8,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
        "harm_supporting_data": RuleObjectSpec(
            type="harm_supporting_data",
            role="component",
            description="支持危害/风险判断的数据、研究证据、不良事件结果、安全性结果、证据摘要或证据等级。",
            max_objects=8,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
        "benefit_harm_balance_statement": RuleObjectSpec(
            type="benefit_harm_balance_statement",
            role="component",
            description="明确说明形成推荐时考虑、权衡或平衡获益与危害/风险的表述。",
            max_objects=6,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
        "recommendation_impact_statement": RuleObjectSpec(
            type="recommendation_impact_statement",
            role="component",
            description="说明利弊权衡如何影响推荐方向、推荐强度、目标值、选择或最终管理决策。",
            max_objects=6,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
        "generic_benefit_harm_method_statement": RuleObjectSpec(
            type="generic_benefit_harm_method_statement",
            role="exclusion",
            description="泛泛声称制定推荐时考虑 benefit/harm，但没有连到具体推荐、选项或证据。",
            max_objects=4,
            verification=_BENEFIT_HARM_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "抽取当前指南中用于 item_11 的利弊证据。优先抽取推荐正文、recommendation rationale、evidence-to-decision、"
        "summary of evidence、conclusion 或方法章节中与推荐形成相关的获益、危害/副作用/风险、支持数据和明确权衡表述。"
        "recommendation_or_option 只抽能作为利弊评价对象的当前指南推荐、治疗/筛查/管理选项或管理决策；不要把单纯"
        "药物名、表格行、禁忌证、不良事件列表或背景风险作为核心推荐锚点。对象必须能服务于判断同一条推荐、同一"
        "选项或同一管理决策是否同时考虑了获益与危害。单侧 benefit/harm 可以作为 partial evidence，但不要暗示其本身"
        "构成完整利弊权衡。不要抽取背景疾病风险、普通不良事件列表、禁忌证、注意事项，或与当前推荐/选项无关的"
        "单侧疗效段落；只有泛泛声明"
        "推荐制定时考虑 benefit/harm 但没有推荐级映射时，才抽取为 generic_benefit_harm_method_statement。"
    ),
    verification_guidance=(
        "AGREE-II item_11 要求同一推荐、同一选项或同一管理决策同时具备获益证据和危害/副作用/风险证据；"
        "高分还需要原文明确显示推荐形成时进行了利弊权衡。Object verifier 只验证候选 span 是否可作为 item_11 候选证据，"
        "group verifier 再判断这些对象是否属于同一推荐/选项。"
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "recommendations",
            "rationale",
            "evidence to decision",
            "benefits and harms",
            "benefit-risk",
            "balance",
            "safety",
            "adverse",
            "side effects",
            "risks",
            "summary of evidence",
            "conclusion",
            "推荐",
            "推荐依据",
            "获益",
            "危害",
            "风险",
            "安全性",
            "权衡",
        ),
        include_phrases=(
            "benefits and harms",
            "balance of benefits and harms",
            "benefit-risk",
            "benefit risk",
            "weighed the benefits",
            "balanced against",
            "net benefit",
            "potential harms",
            "side effects",
            "adverse events",
            "risk of harm",
            "formulating the recommendation",
            "获益和危害",
            "利弊权衡",
            "获益风险",
            "不良事件",
            "副作用",
        ),
        include_terms=(
            "benefit",
            "harm",
            "risk",
            "safety",
            "adverse",
            "balance",
            "recommendation",
            "获益",
            "危害",
            "风险",
            "安全",
            "权衡",
            "推荐",
        ),
        negative_terms=(
            "reference list",
            "search strategy",
            "inclusion criteria",
            "diagnostic criteria",
            "参考文献",
            "检索策略",
            "纳入标准",
            "诊断标准",
        ),
        neighbor_window=1,
        max_chunks=24,
        max_domain_chunks=12,
    ),
    grouping=GroupingSpec(
        group_profile="benefit_harm_balance",
        anchor_object_types=("recommendation_or_option", "benefit_harm_balance_statement"),
        member_object_types=(
            "health_benefit",
            "harm_side_effect_or_risk",
            "benefit_supporting_data",
            "harm_supporting_data",
            "recommendation_impact_statement",
            "generic_benefit_harm_method_statement",
        ),
        neighbor_window=1,
        same_section=True,
        max_groups=16,
        llm_required=True,
        guidance=(
            "Accept a group when the objects describe the same current-guideline recommendation, option, management "
            "decision, or local benefit-harm evidence unit. A group may be a recommendation table row, one paragraph, "
            "one recommendation rationale, one evidence-to-decision cell, or adjacent chunks in the same local section. "
            "Do not reject incomplete same-decision groups only because benefit, harm, supporting data, explicit balance, "
            "or recommendation impact is missing; those gaps belong in verifier attributes. Reject groups that combine "
            "benefit and harm from different recommendations/options, background risks, adverse-event lists, "
            "contraindications, or unrelated generic method statements."
        ),
    ),
    rule=BenefitHarmBalanceRule(),
)
