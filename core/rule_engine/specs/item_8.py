"""
Rule specification for AGREE-II item 8.
"""
from __future__ import annotations

from core.rule_engine.models import (
    BranchCase,
    ChunkRoutingSpec,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
    WeightedChecklistItem,
    WeightedChecklistRule,
    all_of,
    any_of,
)


_EVIDENCE_SELECTION_VERIFICATION = ObjectVerificationSpec(
    required_context_terms=(
        "inclusion",
        "exclusion",
        "included",
        "excluded",
        "eligible studies",
        "eligible articles",
        "study selection",
        "literature selection",
        "records screened",
        "selection criteria",
        "eligibility criteria",
        "纳入",
        "排除",
        "筛选",
        "选择标准",
    ),
    negative_context_terms=(
        "diagnostic criteria",
        "screening criteria",
        "treatment eligibility",
        "recommendation applies",
        "medicare",
        "employability",
        "employment evaluation",
        "诊断标准",
        "筛查标准",
        "治疗适应证",
        "推荐适用",
    ),
    llm_required=True,
    guidance=(
        "Accept only evidence/study/literature/record inclusion, exclusion, eligibility, or selection criteria. "
        "Do not reject a candidate merely because the selected evidence criteria mention diagnostic criteria, "
        "screening thresholds, patient eligibility, PICO, population, intervention, setting, language, or study design; "
        "those can be valid when they are criteria for selecting studies/articles/records/evidence. Reject only when "
        "the quote is about clinical diagnosis, treatment eligibility, recommendation applicability, program eligibility, "
        "or employment/Medicare eligibility rather than evidence selection."
    ),
)


ITEM_8_SPEC = ItemSpec(
    item_id="item_8",
    title="The criteria for selecting the evidence are clearly described.",
    rule_profile="weighted_checklist",
    object_specs={
        "simple_selection_criteria_mention": RuleObjectSpec(
            type="simple_selection_criteria_mention",
            role="component",
            description="在证据选择语境中，简单提及检索所得证据、研究、文献、记录或文章的纳入标准、排除标准、选择标准或筛选标准，但缺乏清晰细节。",
            max_objects=3,
            verification=_EVIDENCE_SELECTION_VERIFICATION,
        ),
        "clear_selection_criteria": RuleObjectSpec(
            type="clear_selection_criteria",
            role="gate",
            description="在证据选择语境中，清晰说明检索所得证据、研究、文献、记录或文章的纳入和/或排除标准，足以进入 4-7 分加权评分。",
            max_objects=3,
            verification=_EVIDENCE_SELECTION_VERIFICATION,
        ),
        "study_design": RuleObjectSpec(
            type="study_design",
            role="component",
            description="选择标准中包含研究设计、文献类型或研究类型要求。",
            max_objects=3,
            verification=_EVIDENCE_SELECTION_VERIFICATION,
        ),
        "pico_elements": RuleObjectSpec(
            type="pico_elements",
            role="component",
            description="选择标准中包含 PICO 要素，例如人群、干预、对照或结局。",
            max_objects=3,
            verification=_EVIDENCE_SELECTION_VERIFICATION,
        ),
        "language": RuleObjectSpec(
            type="language",
            role="component",
            description="选择标准中包含语言限制或语言纳入/排除要求。",
            max_objects=3,
            verification=_EVIDENCE_SELECTION_VERIFICATION,
        ),
        "setting_or_context": RuleObjectSpec(
            type="setting_or_context",
            role="component",
            description="选择标准中包含场景、背景、地区、医疗环境或适用情境。",
            max_objects=3,
            verification=_EVIDENCE_SELECTION_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取检索所得证据、研究、文献、记录或文章的纳入、排除、选择或筛选标准相关对象。必须能从原文看出对象是 "
        "evidence identified by the search、included/excluded studies、eligible/ineligible articles、records screened、"
        "study selection、literature selection 或 evidence source selection。证据选择语境必须在同一句或相邻句中可见；"
        "研究设计、PICO、语言和背景只有出现在检索证据/研究/文献/记录的纳入或排除标准中时才可抽取。"
        "不要把指南范围、推荐问题、普通背景介绍、就业评估标准、临床判断标准、protocol 使用标准、"
        "诊断/检测标准、疾病状态、工作岗位、患者/员工是否符合某项推荐或评估条件、治疗或筛查适用条件误判为 item_8 的证据选择标准。"
        "A1C/FPG/OGTT 等诊断阈值、用药/治疗适应证、Medicare 或项目参与 eligibility、患者风险分层或推荐适用人群都不计入，"
        "除非文本明确说明这些是纳入/排除文献、研究或记录的标准。"
        "如果同一语境明确是在描述 literature/study/evidence selection，则不要因为标准内容涉及 diagnostic criteria、eligibility、"
        "PICO population 或筛查阈值就直接排除，应判断它们是否作为文献/研究纳入排除标准出现。"
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "study selection",
            "evidence selection",
            "eligibility criteria",
            "inclusion criteria",
            "exclusion criteria",
            "literature search",
            "methods",
            "纳入标准",
            "排除标准",
        ),
        include_phrases=(
            "inclusion criteria",
            "exclusion criteria",
            "selection criteria",
            "eligibility criteria",
            "eligible studies",
            "eligible articles",
            "included studies",
            "excluded studies",
            "records screened",
            "study selection",
            "literature selection",
            "纳入标准",
            "排除标准",
            "筛选标准",
        ),
        include_terms=(
            "inclusion",
            "exclusion",
            "eligible",
            "screened",
            "selection",
            "studies",
            "articles",
            "records",
            "纳入",
            "排除",
            "筛选",
        ),
        negative_terms=(
            "diagnostic criteria",
            "screening criteria",
            "treatment eligibility",
            "medicare",
            "employability",
            "employment evaluation",
            "a1c/fpg/ogtt",
        ),
        neighbor_window=1,
        max_chunks=24,
        max_domain_chunks=10,
    ),
    rule=WeightedChecklistRule(
        base_score=3,
        max_score=7,
        gate=all_of("clear_selection_criteria"),
        gate_score_range="1",
        gate_fallback_cases=(
            BranchCase(
                name="partial_evidence_selection_criteria",
                condition=any_of(
                    "simple_selection_criteria_mention",
                    "study_design",
                    "pico_elements",
                    "language",
                    "setting_or_context",
                ),
                score_range="2-3",
                description="证据选择语境下仅简单提及纳入/排除标准或单项选择限制。",
            ),
        ),
        items=(
            WeightedChecklistItem(fact="study_design", weight=1),
            WeightedChecklistItem(fact="pico_elements", weight=2),
            WeightedChecklistItem(fact="language", weight=1),
            WeightedChecklistItem(fact="setting_or_context", weight=1),
        ),
    ),
)
