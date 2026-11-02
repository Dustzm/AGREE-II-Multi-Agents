"""
Rule specification for AGREE-II item 16.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    GroupingSpec,
    ItemSpec,
    ManagementOptionsRule,
    ObjectVerificationSpec,
    RuleObjectSpec,
)


_MANAGEMENT_OPTIONS_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "option",
        "options",
        "alternative",
        "first-line",
        "second-line",
        "third-line",
        "initial",
        "preferred",
        "choice",
        "choose",
        "if",
        "when",
        "then",
        "algorithm",
        "pathway",
        "treatment",
        "management",
        "screening",
        "diagnosis",
        "prevention",
        "subgroup",
        "population",
        "condition",
        "criteria",
        "选择",
        "方案",
        "一线",
        "二线",
        "首选",
        "替代",
        "路径",
        "算法",
        "治疗",
        "管理",
        "筛查",
        "诊断",
    ),
    negative_context_terms=(
        "evidence table",
        "study intervention",
        "background",
        "drug classification only",
        "pharmacology table",
        "resource requirement",
        "implementation tool",
        "external guideline",
        "证据表",
        "研究干预",
        "背景",
        "药物分类",
        "资源要求",
        "实施工具",
        "外部指南",
    ),
    llm_required=True,
    guidance=(
        "Judge only whether the candidate object truly represents part of current-guideline management options. "
        "Item 16 requires two or more distinct options for the same clinical issue, plus conditions or selection logic "
        "for higher scores. Do not decide final score here. Accept first-line/second-line options, if/then branching, "
        "subgroup-specific options, treatment/screening/diagnosis pathways, and tables that compare options by scenario. "
        "Reject evidence-table study interventions, background lists, drug classification tables without selection logic, "
        "ordinary sequential workflows without a choice, implementation tools/resources, and external guideline options."
    ),
)


ITEM_16_SPEC = ItemSpec(
    item_id="item_16",
    title="The different options for management of the condition or health issue are clearly presented.",
    rule_profile="management_options",
    object_specs={
        "clinical_issue_or_decision_point": RuleObjectSpec(
            type="clinical_issue_or_decision_point",
            role="component",
            description="同一临床问题、决策点、管理场景或需要选择方案的情境。",
            max_objects=6,
            verification=_MANAGEMENT_OPTIONS_VERIFICATION,
        ),
        "management_option": RuleObjectSpec(
            type="management_option",
            role="component",
            description="当前指南针对同一问题提供的筛查、预防、诊断、治疗或管理选项。",
            max_objects=12,
            verification=_MANAGEMENT_OPTIONS_VERIFICATION,
        ),
        "option_condition_or_population": RuleObjectSpec(
            type="option_condition_or_population",
            role="component",
            description="说明某个管理选项适用于何时、何人、何种临床状态、亚组、条件或禁忌。",
            max_objects=8,
            verification=_MANAGEMENT_OPTIONS_VERIFICATION,
        ),
        "preference_order": RuleObjectSpec(
            type="preference_order",
            role="component",
            description="选项之间的推荐顺序或优先级，例如 first-line、second-line、preferred、alternative、step-up。",
            max_objects=6,
            verification=_MANAGEMENT_OPTIONS_VERIFICATION,
        ),
        "selection_criteria": RuleObjectSpec(
            type="selection_criteria",
            role="component",
            description="如何在管理选项之间选择的明确依据，例如疗效、风险、禁忌、合并症、治疗失败或患者特征。",
            max_objects=6,
            verification=_MANAGEMENT_OPTIONS_VERIFICATION,
        ),
        "decision_pathway_or_algorithm": RuleObjectSpec(
            type="decision_pathway_or_algorithm",
            role="component",
            description="清晰的决策路径、治疗路径、algorithm、if/then 分支或 stepwise management 逻辑。",
            max_objects=6,
            verification=_MANAGEMENT_OPTIONS_VERIFICATION,
        ),
        "single_option_only": RuleObjectSpec(
            type="single_option_only",
            role="review_flag",
            description="仅呈现单一管理方案，或方案之间不可区分。",
            max_objects=4,
            verification=_MANAGEMENT_OPTIONS_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "AGREE-II item_16 评价同一临床问题下是否清楚呈现不同管理选项及选择逻辑。先锁定 clinical issue、"
        "decision point 或 management scenario，再抽取同一问题下的多个 management_option、适用条件、优先顺序、"
        "选择标准或决策路径。可抽取 first-line/second-line、if/then 分支、亚组方案、treatment pathway、algorithm、"
        "按场景/人群对比方案的表格。不要把全篇不同章节散落的干预拼成同一问题选项；不要把证据表研究干预、背景方案列举、"
        "药物分类表、普通顺序流程、资源/工具/实施材料或外部指南选项误判为当前指南的管理选项。"
    ),
    verification_guidance=(
        "Item 16 asks whether the current guideline clearly presents different management options for the same clinical "
        "issue. A valid high-score group must show two or more distinct options for one issue and how to choose among "
        "them through conditions, populations, preference order, selection criteria, or decision pathway. Object "
        "verification only checks candidate truth; group verification determines whether options belong to the same issue."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "recommendations",
            "management",
            "treatment options",
            "management options",
            "screening",
            "diagnosis",
            "algorithm",
            "pathway",
            "executive summary",
            "推荐",
            "管理",
            "治疗方案",
            "路径",
            "算法",
        ),
        include_phrases=(
            "treatment options",
            "management options",
            "first-line",
            "second-line",
            "third-line",
            "alternative treatment",
            "preferred option",
            "if",
            "when",
            "then",
            "treatment pathway",
            "decision pathway",
            "algorithm",
            "stepwise",
            "monotherapy",
            "combination therapy",
            "一线",
            "二线",
            "首选",
            "替代方案",
            "治疗路径",
            "决策路径",
        ),
        include_terms=(
            "option",
            "alternative",
            "first-line",
            "second-line",
            "preferred",
            "algorithm",
            "pathway",
            "subgroup",
            "criteria",
            "management",
            "treatment",
            "screening",
            "diagnosis",
            "方案",
            "选择",
            "路径",
            "算法",
        ),
        negative_terms=(
            "evidence table",
            "study intervention",
            "pharmacology",
            "resource requirement",
            "implementation tool",
        ),
        neighbor_window=2,
        max_chunks=30,
        max_domain_chunks=14,
    ),
    grouping=GroupingSpec(
        group_profile="management_options",
        anchor_object_types=(
            "clinical_issue_or_decision_point",
            "management_option",
            "decision_pathway_or_algorithm",
            "single_option_only",
        ),
        member_object_types=(
            "management_option",
            "option_condition_or_population",
            "preference_order",
            "selection_criteria",
            "decision_pathway_or_algorithm",
        ),
        neighbor_window=2,
        same_section=True,
        max_groups=14,
        llm_required=True,
        guidance=(
            "For item16, accept a group only when the objects describe management options for the same clinical issue, "
            "decision point, or management scenario. Set attributes: same_issue, option_count, has_conditions, "
            "has_preference_or_pathway, scope. Reject groups that combine options from different issues, evidence tables, "
            "background lists, external options, or ordinary sequential workflows without a choice."
        ),
    ),
    rule=ManagementOptionsRule(),
)
