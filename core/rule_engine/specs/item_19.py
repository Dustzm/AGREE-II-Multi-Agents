"""
Rule specification for AGREE-II item 19.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    GroupingSpec,
    ImplementationToolRule,
    ItemSpec,
    ObjectVerificationSpec,
    RubricAppraiserSpec,
    RubricUnitSpec,
    RuleObjectSpec,
)


_IMPLEMENTATION_TOOL_VERIFICATION = ObjectVerificationSpec(
    llm_required=True,
    guidance=(
        "For AGREE-II item 19, verify only whether the quoted span is a plausible candidate scoring material for "
        "this object type. Do not decide whether the group is finally eligible, do not decide score, and do not reject "
        "only because the span is a clinical table, implementation discussion, protocol/process/EHR statement, "
        "structured education program, or tool-like material. The group verifier and engine decide whether it is "
        "current-guideline implementation support, a concrete tool/resource, clinical content, or ineligible material."
    ),
)


ITEM_19_SPEC = ItemSpec(
    item_id="item_19",
    title="The guideline provides advice or tools on how the recommendations can be put into practice.",
    rule_profile="implementation_tool",
    object_specs={
        "implementation_advice_or_discussion": RuleObjectSpec(
            type="implementation_advice_or_discussion",
            role="component",
            description="提及本指南推荐意见的实施、推广、落实、执行、障碍、策略或实施讨论，但未提供具体可用工具。",
            max_objects=5,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "implementation_section_or_module": RuleObjectSpec(
            type="implementation_section_or_module",
            role="component",
            description="独立的 Implementation、Tools、Resources、Appendix、Implementation module 或类似实施章节/模块。",
            max_objects=4,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "tool_like_content": RuleObjectSpec(
            type="tool_like_content",
            role="component",
            description="看起来像工具/资源的具体内容，例如 checklist、leaflet、summary sheet、flowchart、template、questionnaire、manual、audit form/tool 或 patient material，但尚需验证是否为合格实施工具。",
            max_objects=8,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "clinical_algorithm_or_pathway": RuleObjectSpec(
            type="clinical_algorithm_or_pathway",
            role="component",
            description="算法、pathway、flowchart 或决策路径候选；只有被明确作为落实本指南推荐的实施辅助物时才算合格工具。",
            max_objects=6,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "patient_or_clinician_material": RuleObjectSpec(
            type="patient_or_clinician_material",
            role="component",
            description="面向患者、照护者或临床人员使用的材料、leaflet、summary sheet、educational material、handout 或类似资源。",
            max_objects=6,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "appendix_or_template": RuleObjectSpec(
            type="appendix_or_template",
            role="component",
            description="本指南提供的 appendix、template、form、questionnaire、checklist、manual 或可直接使用的附录资源。",
            max_objects=6,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "eligible_implementation_tool": RuleObjectSpec(
            type="eligible_implementation_tool",
            role="component",
            description="具体、可使用的实施辅助物，用于帮助落实本指南推荐，例如清单、患者材料、实施流程图、模板、问卷、教育材料、操作手册、审计表或计算机支持。",
            max_objects=8,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "current_guideline_tool_connection": RuleObjectSpec(
            type="current_guideline_tool_connection",
            role="relationship",
            description="明确说明该工具/资源由本指南提供、嵌入、采用，或推荐用于落实本指南推荐。",
            max_objects=6,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "tool_use_instruction": RuleObjectSpec(
            type="tool_use_instruction",
            role="relationship",
            description="说明如何填写、发放、部署、操作、应用或使用某个合格实施工具/资源；不包括普通临床管理步骤。",
            max_objects=6,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
        "ineligible_tool_or_resource": RuleObjectSpec(
            type="ineligible_tool_or_resource",
            role="exclusion",
            description="不合格的工具/资源候选，例如诊断/筛查/适用标准表、药物/剂量表、证据表、风险分层表、普通治疗流程、外部工具引用或主要用于规划/调查/研究执行的材料。",
            max_objects=6,
            verification=_IMPLEMENTATION_TOOL_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "AGREE-II item_19 评价本指南是否提供 advice or tools/resources 帮助把本指南推荐意见落实到实践中。"
        "抽取阶段只负责保留可能相关的候选材料，不要在这里决定最终分档。"
        "应抽取实施讨论、实施章节、structured education program、protocol/process/EHR changes、implementation elements table、"
        "工具样内容、具体工具/资源、当前指南连接、工具使用说明和明确不合格候选。"
        "具体工具/资源包括 checklist、patient leaflet/material、summary sheet、implementation flowchart、questionnaire、"
        "educational material、computer support、how-to manual、template、audit form/tool、resource package。"
        "algorithm/pathway/flowchart、drug/dose/practice table、diagnostic/screening/risk tool、evidence table、"
        "resource/staffing/training/process discussion 也可以作为候选抽取，但要按最准确的 object type 或 tool_like/ineligible 候选保留，"
        "由 group verifier 判断其 rubric_role、support_form 和 actionability。"
        "current_guideline_tool_connection 只抽原文明确说明本指南提供、采用、嵌入或推荐使用该工具来落实本指南推荐的表达。"
        "tool_use_instruction 只抽如何填写、发放、部署、操作、应用或使用工具/资源的说明；普通临床治疗步骤不要抽为工具使用说明。"
        "每类只保留最直接、最代表性的证据。"
    ),
    verification_guidance=(
        "AGREE-II item 19 asks whether this guideline provides advice, implementation support, or concrete "
        "tools/resources for putting this guideline's recommendations into practice. Use a two-path rubric. "
        "Concrete tool/resource evidence can support the highest score only when the group shows a current-guideline "
        "tool/resource and clear instructions for using that tool/resource. Implementation support evidence, such as a structured "
        "education program, protocol/process/EHR implementation changes, or an implementation elements table, may "
        "support middle or high-middle scores when it is tied to applying this guideline, but it should not receive "
        "score 7 unless it is also a concrete tool/resource with clear use instructions. Clinical recommendations, "
        "drug/dose/practice tables, diagnostic/screening/risk tools, evidence tables, and ordinary clinical workflows "
        "are clinical content unless the group verifier can clearly classify them as current-guideline implementation support."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "implementation",
            "tools",
            "resources",
            "appendix",
            "checklist",
            "patient materials",
            "audit",
            "template",
            "questionnaire",
            "manual",
            "实施",
            "工具",
            "资源",
            "附录",
        ),
        include_phrases=(
            "implementation tools",
            "implementation resources",
            "tools and resources",
            "patient leaflet",
            "patient information",
            "patient material",
            "summary sheet",
            "checklist",
            "audit tool",
            "audit form",
            "template",
            "questionnaire",
            "flowchart",
            "manual",
            "resource package",
            "implementation package",
            "educational material",
            "computer support",
            "how to use",
            "use this tool",
            "appendix",
            "put into practice",
            "实施工具",
            "实施资源",
            "患者材料",
            "审计工具",
            "模板",
            "问卷",
            "流程图",
        ),
        include_terms=(
            "implementation",
            "implement",
            "tool",
            "resource",
            "appendix",
            "checklist",
            "leaflet",
            "template",
            "questionnaire",
            "flowchart",
            "manual",
            "audit",
            "patient",
            "material",
            "package",
            "实施",
            "工具",
            "资源",
            "附录",
            "清单",
        ),
        negative_terms=(
            "diagnostic table",
            "diagnostic tool",
            "screening tool",
            "risk calculator",
            "risk test",
            "drug table",
            "dose table",
            "evidence table",
            "pharmacokinetic table",
            "risk stratification",
            "eligibility criteria",
            "screening criteria",
            "treatment algorithm",
            "clinical workflow",
        ),
        neighbor_window=2,
        max_chunks=28,
        max_domain_chunks=12,
    ),
    grouping=GroupingSpec(
        group_profile="implementation_tool",
        anchor_object_types=(
            "eligible_implementation_tool",
            "tool_like_content",
            "patient_or_clinician_material",
            "appendix_or_template",
            "implementation_advice_or_discussion",
            "implementation_section_or_module",
        ),
        member_object_types=(
            "implementation_advice_or_discussion",
            "implementation_section_or_module",
            "tool_like_content",
            "clinical_algorithm_or_pathway",
            "patient_or_clinician_material",
            "appendix_or_template",
            "eligible_implementation_tool",
            "current_guideline_tool_connection",
            "tool_use_instruction",
            "ineligible_tool_or_resource",
        ),
        neighbor_window=2,
        same_section=True,
        max_groups=8,
        llm_required=True,
        guidance=(
            "Build local groups around implementation advice, tools, resources, appendices, and usage statements. "
            "The group verifier decides whether the grouped objects form a current-guideline implementation-support "
            "or tool/resource evidence unit."
        ),
    ),
    rubric_appraiser=RubricAppraiserSpec(
        profile="item19_implementation_support_appraiser",
        max_units=8,
        unit_char_limit=1400,
        decision_labels=(
            "no_relevant_evidence",
            "excluded_clinical_content",
            "external_resource_only",
            "implementation_discussion_only",
            "partial_tool_or_resource_support",
            "clear_tool_or_resource_support",
            "complete_tool_or_resource_support",
        ),
        guidance=(
            "AGREE-II item 19 evaluates whether this guideline provides advice, tools, or resources for putting "
            "this guideline's recommendations into practice. A candidate is an eligible implementation tool/resource "
            "only when it is a concrete usable aid and the text clearly shows this guideline provides, adopts, embeds, "
            "or recommends it for implementing this guideline. Implementation discussion or tool-like content without "
            "eligible tools/resources scores 2-4. One eligible tool without clear use guidance scores 2-3; two or more "
            "eligible tools without clear use guidance score 4-5; a separate implementation section plus an eligible "
            "tool without clear use guidance scores 5-6; eligible tools/resources with clear practical use guidance "
            "score 6-7, with 7 reserved for complete practical instructions. Do not count ordinary clinical recommendations, treatment steps, monitoring rules, diagnostic/"
            "screening/risk tools, drug/dose tables, evidence tables, external resources merely cited or linked, or "
            "planning/survey/research-operation materials unless explicitly adopted for implementing this guideline."
        ),
    ),
    rubric_unit=RubricUnitSpec(
        profile="item19_implementation_support",
        bands=(
            "no_evidence",
            "implementation_discussion_only",
            "tool_like_but_not_eligible",
            "one_eligible_tool_no_instruction",
            "multiple_eligible_tools_no_instruction",
            "implementation_section_plus_tool_no_instruction",
            "eligible_tool_with_use_instruction",
            "complete_tool_with_use_instruction",
            "excluded_clinical_content",
            "external_resource_only",
        ),
        max_units=12,
        guidance=(
            "Deprecated rubric-unit configuration retained only for unit-level builder/classifier tests. "
            "Active scoring uses the implementation_tool object/group rule engine."
        ),
    ),
    rule=ImplementationToolRule(),
)
