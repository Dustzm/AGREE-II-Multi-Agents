"""
Rule specification for AGREE-II item 9.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    EvidenceAppraisalRule,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
)


_EVIDENCE_APPRAISAL_VERIFICATION = ObjectVerificationSpec(
    negative_context_terms=(
        "inclusion criteria",
        "exclusion criteria",
        "eligibility criteria",
        "study selection",
        "literature selection",
        "records screened",
        "recommendations were formulated",
        "formulating the recommendations",
        "consensus process",
        "voting",
        "delphi",
        "diagnostic criteria",
        "treatment algorithm",
        "clinical workflow",
        "纳入标准",
        "排除标准",
        "筛选标准",
        "推荐意见形成",
        "共识过程",
        "投票",
        "诊断标准",
        "治疗流程",
    ),
    llm_required=True,
    guidance=(
        "Accept only evidence from the current guideline that appraises or describes the strengths, limitations, "
        "quality, certainty, level, risk of bias, consistency, applicability, or methodological limitations of the "
        "body of evidence supporting recommendations. The quote may use formal systems such as GRADE or informal "
        "commentary about the evidence body. Reject ordinary clinical conclusions, recommendation text, isolated "
        "study-result summaries without appraisal meaning, epidemiologic background, reference lists, evidence "
        "selection criteria for item 8, and recommendation-formulation methods for item 10."
    ),
)


ITEM_9_SPEC = ItemSpec(
    item_id="item_9",
    title="The strengths and limitations of the body of evidence are clearly described.",
    rule_profile="evidence_appraisal",
    object_specs={
        "evidence_assessment_method": RuleObjectSpec(
            type="evidence_assessment_method",
            role="component",
            description="描述评价证据质量、确定性、强度或偏倚风险的方法，例如 GRADE、risk of bias、quality assessment、certainty assessment。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "explicit_evidence_level": RuleObjectSpec(
            type="explicit_evidence_level",
            role="component",
            description="为证据、结局或推荐明确给出证据水平、质量、确定性、证据等级或推荐证据级别。",
            max_objects=5,
            require_value_in_quote=False,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "evidence_grading_system_applied": RuleObjectSpec(
            type="evidence_grading_system_applied",
            role="component",
            description=(
                "说明当前指南实际使用证据分级、证据质量或推荐强度体系评价推荐依据，例如 GRADE、evidence level、"
                "quality of evidence table、recommendation grading protocol 被应用到本指南推荐。"
            ),
            max_objects=4,
            require_value_in_quote=False,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "study_design_description": RuleObjectSpec(
            type="study_design_description",
            role="component",
            description="在证据体或推荐依据语境中描述纳入证据的研究设计，例如 RCT、cohort、case-control、systematic review、meta-analysis。",
            max_objects=5,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "methodological_limitation": RuleObjectSpec(
            type="methodological_limitation",
            role="component",
            description="描述证据或研究的方法学局限，例如样本量、盲法、分配隐藏、偏倚、不精确性、间接性、发表偏倚或分析方法限制。",
            max_objects=5,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "outcome_relevance": RuleObjectSpec(
            type="outcome_relevance",
            role="component",
            description="评价主要或次要结局的适当性、重要性、相关性或患者重要性。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "result_consistency": RuleObjectSpec(
            type="result_consistency",
            role="component",
            description="评价多项研究结果的一致性、异质性、冲突结果或不一致性。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "result_direction": RuleObjectSpec(
            type="result_direction",
            role="component",
            description="描述多项研究结果方向、效果方向或整体证据方向。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "benefit_harm_magnitude": RuleObjectSpec(
            type="benefit_harm_magnitude",
            role="component",
            description="评价获益和伤害的大小、程度、平衡或相对影响。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "practice_applicability": RuleObjectSpec(
            type="practice_applicability",
            role="component",
            description="评价证据对实践场景、人群、医疗环境或当前指南适用背景的适用性、可推广性或间接性。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "ordinary_clinical_conclusion": RuleObjectSpec(
            type="ordinary_clinical_conclusion",
            role="exclusion",
            description="普通临床结论、推荐正文或治疗效果陈述，没有评价证据体强度、质量、确定性或局限性。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "single_study_result_summary": RuleObjectSpec(
            type="single_study_result_summary",
            role="exclusion",
            description="仅摘录单篇研究或单个试验结果，没有作为证据体强度或局限性评价。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "evidence_selection_criteria": RuleObjectSpec(
            type="evidence_selection_criteria",
            role="exclusion",
            description="文献纳入、排除、筛选、检索或 eligibility 标准，属于 item8 证据选择，不属于 item9 证据强度/局限性评价。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "recommendation_formulation_process": RuleObjectSpec(
            type="recommendation_formulation_process",
            role="exclusion",
            description="共识、投票、Delphi、推荐意见形成或批准过程，属于 item10，不属于 item9 证据评价。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
        "clinical_background_or_workflow": RuleObjectSpec(
            type="clinical_background_or_workflow",
            role="exclusion",
            description="疾病背景、流行病学负担、诊断标准、治疗算法或普通临床流程，不属于证据体强度和局限性评价。",
            max_objects=4,
            verification=_EVIDENCE_APPRAISAL_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取当前指南对 supporting evidence、body of evidence、evidence profile、summary of findings、recommendation rationale "
        "中的证据强度、局限性、质量、确定性、偏倚风险、证据等级、结果一致性、收益伤害程度或实践适用性的描述。"
        "如果文本说明本指南把 GRADE、evidence level、quality of evidence table 或 recommendation grading protocol "
        "实际用于推荐或证据评价，抽取 evidence_grading_system_applied；如果只是外部方法定义且未说明应用于本指南，"
        "不要抽取该对象。"
        "证据可以来自方法、证据总结、推荐依据或证据表。研究设计只有在作为证据体特征或推荐依据的一部分被描述时才抽取。"
        "不要把普通临床结论、普通推荐正文、单篇研究结果摘录、疾病负担或流行病学背景、参考文献列表、"
        "文献纳入/排除/筛选标准、推荐意见形成/投票/共识过程、临床诊断标准或治疗流程误判为 item_9。"
        "如果文本只是说明某研究显示有效或某治疗改善结局，但没有评价证据质量、强度、局限性、确定性、适用性或证据体特征，"
        "抽取对应 exclusion object，不要抽取加分构件。"
    ),
    verification_guidance=(
        "AGREE-II item_9 评价当前指南是否清楚描述证据体的强度和局限性。有效证据包括正式或非正式的证据质量/确定性评价、"
        "证据等级、证据分级体系在本指南中的应用、研究设计、方法学局限、结局相关性、结果一致性、结果方向、"
        "收益伤害程度和实践适用性。"
        "不要把普通研究结论、普通推荐文字、文献选择标准、推荐形成方法或临床流程当作 item_9 证据。"
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "methods",
            "evidence",
            "quality of evidence",
            "certainty of evidence",
            "strength of evidence",
            "risk of bias",
            "evidence profile",
            "summary of findings",
            "recommendation rationale",
            "limitations",
            "grade",
            "方法",
            "证据",
            "证据质量",
            "证据等级",
            "证据概要",
            "推荐依据",
            "局限性",
        ),
        include_phrases=(
            "quality of evidence",
            "certainty of evidence",
            "level of evidence",
            "strength of evidence",
            "body of evidence",
            "risk of bias",
            "evidence profile",
            "summary of findings",
            "study limitations",
            "methodological limitations",
            "inconsistency",
            "indirectness",
            "imprecision",
            "publication bias",
            "applicability",
            "generalizability",
            "benefits and harms",
            "证据质量",
            "证据等级",
            "证据强度",
            "偏倚风险",
            "方法学局限",
            "结果一致性",
            "适用性",
            "获益和伤害",
        ),
        include_terms=(
            "grade",
            "evidence",
            "quality",
            "certainty",
            "bias",
            "limitations",
            "heterogeneity",
            "consistency",
            "imprecision",
            "indirectness",
            "applicability",
            "generalizability",
            "blinding",
            "allocation",
            "sample",
            "证据",
            "质量",
            "等级",
            "偏倚",
            "局限",
            "一致性",
            "适用性",
        ),
        negative_terms=(
            "inclusion criteria",
            "exclusion criteria",
            "study selection",
            "consensus process",
            "recommendations were formulated",
            "diagnostic criteria",
            "treatment algorithm",
        ),
        neighbor_window=1,
        max_chunks=24,
        max_domain_chunks=10,
    ),
    rule=EvidenceAppraisalRule(),
)
