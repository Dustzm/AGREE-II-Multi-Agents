"""
Rule specification for AGREE-II item 2.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    GroupingSpec,
    HealthQuestionRule,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
)


_HEALTH_QUESTION_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "question",
        "questions",
        "pico",
        "picos",
        "population",
        "patients",
        "intervention",
        "comparison",
        "comparator",
        "outcome",
        "study design",
        "scope",
        "objective",
        "addresses",
        "covers",
        "指南",
        "问题",
        "人群",
        "干预",
        "对照",
        "结局",
        "范围",
        "目的",
    ),
    negative_context_terms=(
        "funding",
        "conflict of interest",
        "implementation tool",
        "audit standard",
        "recommendation specificity only",
        "citation only",
        "资助",
        "利益冲突",
        "实施工具",
    ),
    llm_required=True,
    guidance=(
        "Accept only candidates that describe the health question, clinical question, key question, or implicit PICO/PICOS "
        "scope covered by the current guideline. Item 2 is about what health question the guideline covers, not whether a "
        "single recommendation is specific. Do not reject incomplete PICO elements merely because they are incomplete for "
        "a higher score; group verification and the engine decide completeness. Reject target population alone, ordinary "
        "recommendation details, evidence selection criteria, cited-study PICO, external guideline questions, funding, COI, "
        "implementation, or audit content unless the local text clearly frames it as the current guideline's health question."
    ),
)


ITEM_2_SPEC = ItemSpec(
    item_id="item_2",
    title="The health question(s) covered by the guideline is (are) specifically described.",
    rule_profile="health_question",
    object_specs={
        "health_question_statement": RuleObjectSpec(
            type="health_question_statement",
            role="component",
            description=(
                "当前指南明确提出的卫生问题、临床问题、关键问题、review question 或 PICO/PICOS question。"
            ),
            max_objects=8,
            verification=_HEALTH_QUESTION_VERIFICATION,
        ),
        "implicit_question_scope": RuleObjectSpec(
            type="implicit_question_scope",
            role="component",
            description=(
                "指南未显式写成问题句，但在摘要、引言、范围或方法中描述了本指南涵盖的 PICO/PICOS 范围。"
            ),
            max_objects=6,
            verification=_HEALTH_QUESTION_VERIFICATION,
        ),
        "question_population_or_problem": RuleObjectSpec(
            type="question_population_or_problem",
            role="component",
            description="卫生问题中的 P：患者、人群、疾病、健康问题、临床问题或目标问题。",
            max_objects=8,
            verification=_HEALTH_QUESTION_VERIFICATION,
        ),
        "question_intervention_or_exposure": RuleObjectSpec(
            type="question_intervention_or_exposure",
            role="component",
            description="卫生问题中的 I：干预、治疗、诊断策略、管理措施、暴露因素或服务模式。",
            max_objects=8,
            verification=_HEALTH_QUESTION_VERIFICATION,
        ),
        "question_comparator": RuleObjectSpec(
            type="question_comparator",
            role="component",
            description="卫生问题中的 C：对照、比较对象、替代方案、usual care、placebo 或不同策略比较。",
            max_objects=6,
            verification=_HEALTH_QUESTION_VERIFICATION,
        ),
        "question_outcome": RuleObjectSpec(
            type="question_outcome",
            role="component",
            description="卫生问题中的 O：结局、获益、伤害、死亡率、生活质量、诊断准确性或其他健康结果。",
            max_objects=6,
            verification=_HEALTH_QUESTION_VERIFICATION,
        ),
        "question_study_design": RuleObjectSpec(
            type="question_study_design",
            role="component",
            description="卫生问题中的 S：研究类型、证据类型、systematic review、RCT、cohort 等。",
            max_objects=4,
            verification=_HEALTH_QUESTION_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "AGREE-II item_2 评价指南涵盖的卫生问题是否具体描述。抽取当前指南的 health question、clinical question、"
        "key question、PICO/PICOS question，或摘要/引言/范围/方法中代表本指南问题范围的 P/I/C/O/S 元素。"
        "必须围绕同一个问题或同一个隐含 PICO 范围抽取。不要把不同段落的 P/I/C/O/S 拼成一个问题。不要抽取单纯"
        "目标人群、单条推荐具体性、文献纳入排除标准、引用研究 PICO、外部指南问题、资助、COI、实施或审计内容。"
    ),
    verification_guidance=(
        "Item 2 scores whether the current guideline specifically describes the health question(s) it covers. Foreground "
        "questions are assessed by PICOS elements; background questions are assessed by P and I; when no explicit health "
        "question is stated, the beginning sections may still provide implicit PICO elements. Verify object candidates as "
        "parts of the current guideline's health question, not as item3 target population, item8 evidence selection, or "
        "item15 recommendation-specificity evidence."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "health question",
            "health questions",
            "clinical question",
            "clinical questions",
            "key question",
            "key questions",
            "pico",
            "picos",
            "scope",
            "objective",
            "objectives",
            "abstract",
            "executive summary",
            "introduction",
            "methodology",
            "methods",
            "卫生问题",
            "临床问题",
            "关键问题",
            "范围",
            "目的",
        ),
        include_phrases=(
            "the guideline addresses",
            "this guideline addresses",
            "these recommendations address",
            "recommendations address",
            "questions covered",
            "clinical questions",
            "key questions",
            "population intervention comparator outcome",
            "patients with",
            "compared with",
            "outcomes included",
            "本指南涵盖",
            "本指南解决",
            "临床问题",
        ),
        include_terms=(
            "question",
            "pico",
            "population",
            "intervention",
            "comparator",
            "comparison",
            "outcome",
            "scope",
            "objective",
            "问题",
            "人群",
            "干预",
            "对照",
            "结局",
        ),
        negative_terms=(
            "funding",
            "conflict of interest",
            "implementation",
            "audit",
        ),
        neighbor_window=1,
        max_chunks=28,
        max_domain_chunks=12,
    ),
    grouping=GroupingSpec(
        group_profile="health_question",
        anchor_object_types=(
            "health_question_statement",
            "implicit_question_scope",
            "question_population_or_problem",
            "question_intervention_or_exposure",
        ),
        member_object_types=(
            "question_population_or_problem",
            "question_intervention_or_exposure",
            "question_comparator",
            "question_outcome",
            "question_study_design",
        ),
        neighbor_window=1,
        same_section=True,
        max_groups=12,
        llm_required=True,
        guidance=(
            "For item2, accept a group only when the objects describe one current-guideline health question or one implicit "
            "PICO scope. Set attributes: same_question, question_framing, scope, picos_present. Reject groups that combine "
            "separate questions, evidence selection criteria, single recommendation details, target population alone, cited "
            "studies, or external guideline questions."
        ),
    ),
    rule=HealthQuestionRule(),
)
