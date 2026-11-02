"""
Rule specification for AGREE-II item 15.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    GroupingSpec,
    ItemSpec,
    MilestoneRule,
    ObjectVerificationSpec,
    RuleObjectSpec,
    ScoreLevel,
    all_of,
    any_of,
)


_RECOMMENDATION_SPECIFICITY_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "recommend",
        "recommendation",
        "should",
        "must",
        "offer",
        "consider",
        "do not",
        "avoid",
        "population",
        "patients",
        "adults",
        "children",
        "condition",
        "intervention",
        "treatment",
        "dose",
        "duration",
        "frequency",
        "when",
        "if",
        "推荐",
        "建议",
        "应",
        "不应",
        "患者",
        "人群",
        "治疗",
        "剂量",
        "疗程",
        "频率",
    ),
    negative_context_terms=(
        "background",
        "evidence summary",
        "study recommends",
        "external guideline",
        "diagnostic criteria only",
        "eligibility criteria only",
        "drug table only",
        "dose table only",
        "背景",
        "证据总结",
        "外部指南",
        "诊断标准",
        "纳入标准",
        "药物表",
        "剂量表",
    ),
    llm_required=True,
    guidance=(
        "Accept only objects that describe a recommendation from the current guideline. Item 15 evaluates whether one "
        "recommendation is specific and unambiguous. Do not reject a candidate merely because the same recommendation "
        "is incomplete for a higher score; group verification and the engine decide completeness. Reject background "
        "text, evidence summaries, cited-study or external-guideline recommendations, plain diagnostic/eligibility "
        "criteria, target population alone, and drug/dose tables unless the local text frames them as a current "
        "guideline recommendation."
    ),
)


ITEM_15_SPEC = ItemSpec(
    item_id="item_15",
    title="The recommendations are specific and unambiguous.",
    rule_profile="ordered_milestone",
    object_specs={
        "recommendation_statement": RuleObjectSpec(
            type="recommendation_statement",
            role="component",
            description="当前指南中的一条可识别推荐意见，包括 recommendation box、summary table、algorithm 或 bullet 中的推荐。",
            max_objects=8,
            verification=_RECOMMENDATION_SPECIFICITY_VERIFICATION,
        ),
        "directive_language": RuleObjectSpec(
            type="directive_language",
            role="component",
            description="推荐意见中的指令性语言，如 recommend、should、must、offer、consider、do not use、avoid。",
            max_objects=8,
            verification=_RECOMMENDATION_SPECIFICITY_VERIFICATION,
        ),
        "population_or_condition": RuleObjectSpec(
            type="population_or_condition",
            role="component",
            description="同一条推荐适用的人群、疾病状态、临床条件、适用场景或触发条件。",
            max_objects=8,
            verification=_RECOMMENDATION_SPECIFICITY_VERIFICATION,
        ),
        "intervention_or_action": RuleObjectSpec(
            type="intervention_or_action",
            role="component",
            description="同一条推荐要求采取或避免的具体干预、检查、治疗、管理动作或行为。",
            max_objects=8,
            verification=_RECOMMENDATION_SPECIFICITY_VERIFICATION,
        ),
        "purpose_or_context_detail": RuleObjectSpec(
            type="purpose_or_context_detail",
            role="component",
            description="同一条推荐的目的、临床场景、适用前提、使用条件或决策背景。",
            max_objects=6,
            verification=_RECOMMENDATION_SPECIFICITY_VERIFICATION,
        ),
        "dose_duration_or_operational_detail": RuleObjectSpec(
            type="dose_duration_or_operational_detail",
            role="component",
            description="同一条推荐中的剂量、疗程、频率、时长、操作步骤或实施细节。",
            max_objects=6,
            verification=_RECOMMENDATION_SPECIFICITY_VERIFICATION,
        ),
        "vague_or_unclear_recommendation": RuleObjectSpec(
            type="vague_or_unclear_recommendation",
            role="review_flag",
            description="模糊、不具体或难以判断是否为推荐意见的表述。",
            max_objects=4,
            verification=_RECOMMENDATION_SPECIFICITY_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "AGREE-II item_15 评价单条推荐是否具体、明确、不含糊。抽取当前指南 recommendation、summary of recommendations、"
        "key recommendations、algorithm、table row 或 bullet 中的推荐对象。必须围绕同一条推荐抽取 P、I、指令性语言、"
        "目的/场景、剂量/疗程/频率/操作细节。不要把不同推荐中的 P/I/detail 拼为一条推荐。不要抽取背景、证据总结、"
        "外部指南推荐、研究结论、单纯目标人群、诊断标准、纳入标准、药物表或剂量表本身，除非局部文本明确把它写成当前指南推荐。"
    ),
    verification_guidance=(
        "Item 15 scores one recommendation's specificity. A high-quality recommendation clearly identifies the target "
        "population or condition, the intervention/action, directive recommendation language, and additional purpose, "
        "clinical context, dosage, duration, frequency, or operational detail. Verify candidates only as current-guideline "
        "recommendation components; the accepted group must decide whether they belong to one recommendation."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "recommendations",
            "summary of recommendations",
            "key recommendations",
            "executive summary",
            "algorithm",
            "treatment recommendations",
            "diagnosis recommendations",
            "推荐",
            "建议",
        ),
        include_phrases=(
            "we recommend",
            "we suggest",
            "it is recommended",
            "should be",
            "should not",
            "do not use",
            "offer",
            "consider",
            "recommendations",
            "summary of recommendations",
            "key recommendations",
            "推荐意见",
            "应当",
            "不应",
        ),
        include_terms=(
            "recommend",
            "should",
            "must",
            "offer",
            "consider",
            "avoid",
            "dose",
            "duration",
            "frequency",
            "algorithm",
            "推荐",
            "建议",
            "剂量",
            "疗程",
        ),
        negative_terms=(
            "evidence summary",
            "background",
            "external guideline",
            "study recommends",
        ),
        neighbor_window=1,
        max_chunks=28,
        max_domain_chunks=12,
    ),
    grouping=GroupingSpec(
        group_profile="recommendation_specificity",
        anchor_object_types=("recommendation_statement", "directive_language", "vague_or_unclear_recommendation"),
        member_object_types=(
            "population_or_condition",
            "intervention_or_action",
            "purpose_or_context_detail",
            "dose_duration_or_operational_detail",
        ),
        neighbor_window=1,
        same_section=True,
        max_groups=14,
        llm_required=True,
        guidance=(
            "For item15, accept a group only when the objects describe the same current-guideline recommendation. "
            "Reject groups that combine separate recommendations, evidence summaries, external recommendations, "
            "background text, or diagnostic/eligibility criteria not framed as a recommendation."
        ),
    ),
    rule=MilestoneRule(
        levels=(
            ScoreLevel(
                score_range="6-7",
                condition=all_of(
                    any_of("recommendation_statement", "directive_language"),
                    "population_or_condition",
                    "intervention_or_action",
                    any_of("purpose_or_context_detail", "dose_duration_or_operational_detail"),
                ),
                description="同一推荐中有 P、I、指令/推荐表达，并进一步说明目的、场景、剂量、疗程或操作细节。",
            ),
            ScoreLevel(
                score_range="4-5",
                condition=all_of(
                    any_of("recommendation_statement", "directive_language"),
                    "population_or_condition",
                    "intervention_or_action",
                ),
                description="同一推荐中有 P、I 和明确推荐/指令表达。",
            ),
            ScoreLevel(
                score_range="2-3",
                condition=any_of(
                    "recommendation_statement",
                    "directive_language",
                    all_of("population_or_condition", "intervention_or_action"),
                ),
                description="推荐可识别，但具体性不足。",
            ),
            ScoreLevel(score_range="1", condition=all_of(), description="未提及或推荐模糊。"),
        )
    ),
)
