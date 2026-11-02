"""
Rule specification for AGREE-II item 23.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    CompetingInterestDisclosureRule,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
)


_COI_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "conflict",
        "competing interest",
        "duality of interest",
        "disclosure",
        "declaration of interest",
        "relevant relationship",
        "financial relationship",
        "interests",
        "declare",
        "declared",
        "authors declare",
        "all authors declare",
        "利益冲突",
        "竞争性利益",
        "利益声明",
        "披露",
        "相关关系",
    ),
    negative_terms=(
        "funding",
        "financial support",
        "acknowledgments",
        "affiliation",
        "author contributions",
        "professional interest",
        "资助",
        "致谢",
        "作者单位",
        "贡献说明",
    ),
    llm_required=True,
    guidance=(
        "Accept current guideline authors or development group COI/competing interest/disclosure/relevant relationship collection, "
        "reporting, absence/presence, influence, or management. Accept statements such as 'authors declare...', "
        "'all authors declare...', and 'declaration of interests' unless context clearly shows cited studies or external documents. "
        "Accept section headers such as 'Competing interests', 'Conflict of interest', or 'Declarations of interest' as coi_mention. "
        "Accept explicit no-conflict statements such as 'There are no conflicts of interest' or 'No competing interests declared' "
        "as no_conflicts_statement even when no collection/declaration process is present; the scoring engine handles whether "
        "the disclosure is complete enough for a high score. Do not reject a true disclosure result only because the collection "
        "method is missing. "
        "Accept 'disclosures were discussed/reviewed' only as coi_reviewed_or_discussed; accept recusal, exclusion, restriction, "
        "or other concrete handling only as coi_management_action; accept explicit 'no influence/no effect on recommendations' "
        "only as coi_no_influence_statement. "
        "Reject acknowledgments, funding-only statements, affiliations, author contributions, or ordinary professional interest."
    ),
)


ITEM_23_SPEC = ItemSpec(
    item_id="item_23",
    title="Competing interests of guideline development group members have been recorded and addressed.",
    rule_profile="competing_interest_disclosure",
    object_specs={
        "coi_mention": RuleObjectSpec(
            type="coi_mention",
            role="component",
            description="提及利益冲突、竞争性利益、利益声明或相关披露。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
        "coi_collection_process": RuleObjectSpec(
            type="coi_collection_process",
            role="component",
            description="说明利益冲突的收集、申报、填写表格、调查或管理流程。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
        "coi_disclosure_result": RuleObjectSpec(
            type="coi_disclosure_result",
            role="component",
            description="报告指南开发组成员的利益冲突披露结果、披露表、register、appendix 或 listed disclosures。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
        "no_conflicts_statement": RuleObjectSpec(
            type="no_conflicts_statement",
            role="component",
            description="明确说明指南开发组成员无利益冲突或无相关竞争性利益。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
        "conflicts_present": RuleObjectSpec(
            type="conflicts_present",
            role="component",
            description="明确说明一个或多个指南开发组成员存在利益冲突。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
        "coi_reviewed_or_discussed": RuleObjectSpec(
            type="coi_reviewed_or_discussed",
            role="component",
            description="说明利益冲突披露被讨论、审查或 reviewed，但未明确说明回避、排除、限制参与或未影响推荐。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
        "coi_management_action": RuleObjectSpec(
            type="coi_management_action",
            role="component",
            description="说明利益冲突如何被具体处理，例如回避、排除、限制参与、不得参与相关讨论或投票。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
        "coi_no_influence_statement": RuleObjectSpec(
            type="coi_no_influence_statement",
            role="component",
            description="明确说明利益冲突未影响指南开发过程、内容或推荐意见。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_COI_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取指南开发组成员利益冲突相关对象，包括 competing interests、conflict of interest、duality of interest、"
        "author disclosures、financial disclosure、declaration of interests、declarations、disclosures、relevant relationships "
        "和 financial relationships 等表达。必须是利益冲突、竞争性利益、披露或相关关系语境。不要把资助披露、"
        "章节标题或表格行标题如 Competing interests、Conflict of interest、Declarations of interest 可抽为 coi_mention；"
        "There are no conflicts of interest、No competing interests declared、Conflict of interest: None 等明确无冲突声明"
        "可抽为 no_conflicts_statement，即使附近没有收集/申报流程。collection/process 只影响评分完整性，不影响这些对象本身是否成立。"
        "'authors declare...'、'all authors declare...'、'declaration of interests' 如果指向当前指南/本文档作者或开发组，"
        "可作为 coi_disclosure_result、no_conflicts_statement 或 conflicts_present；除非上下文明确是引用研究或外部文件。"
        "'disclosures were discussed/reviewed' 只抽取 coi_reviewed_or_discussed；只有回避、排除、限制参与等具体处理动作"
        "才抽取 coi_management_action；只有明确写出未影响指南内容/推荐才抽取 coi_no_influence_statement。不要把资助披露、"
        "Acknowledgments 人员名单、作者机构 affiliation、贡献说明、普通 professional interest 或 interest in diabetes/endocrinology "
        "直接等同于成员利益冲突，除非文本明确关联成员申报、披露、存在/不存在冲突或处理方式。"
    ),
    verification_guidance=(
        "AGREE-II item 23 evaluates whether guideline development group members' competing interests were recorded "
        "and addressed. A COI or competing-interest mention, a collection/declaration process, or a disclosure result "
        "alone is incomplete. A concise title/table row such as 'Conflict of interest: None' or 'Competing interests: "
        "none declared' is a valid disclosure result, but without a collection/declaration process it remains incomplete "
        "under the scoring rule. The verifier should accept true mention/result/process objects and must not reject a "
        "no-conflicts statement, detailed disclosure result, or COI section header merely because the collection method "
        "is absent. High scores require a collection/declaration process plus either a no-conflicts result "
        "or conflicts reported with concrete management/no-influence information. 'Disclosures were discussed/reviewed' "
        "is a review/discussion fact, not a concrete management or no-influence statement by itself. Reject acknowledgments lists, "
        "funding-only statements, affiliations, author contributions, and ordinary professional interests unless the "
        "text explicitly connects them to current guideline member COI disclosure or management."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "conflicts of interest",
            "competing interests",
            "disclosures",
            "declaration of interests",
            "duality of interest",
            "利益冲突",
            "利益声明",
            "披露",
        ),
        include_phrases=(
            "conflict of interest",
            "conflicts of interest",
            "competing interests",
            "duality of interest",
            "author disclosures",
            "financial disclosure",
            "declaration of interests",
            "discussed at the onset",
            "reviewed",
            "recusal",
            "excluded from",
            "no influence",
            "relevant relationships",
            "financial relationships",
            "authors declare",
            "all authors declare",
            "no conflicts",
            "利益冲突",
            "竞争性利益",
            "利益声明",
            "相关关系",
        ),
        include_terms=(
            "conflict",
            "competing",
            "disclosure",
            "declaration",
            "relationships",
            "duality",
            "利益冲突",
            "披露",
        ),
        negative_terms=(
            "acknowledgments",
            "affiliation",
            "author contributions",
            "funding",
            "financial support",
            "professional interest",
        ),
        neighbor_window=1,
        max_chunks=18,
        max_domain_chunks=8,
    ),
    rule=CompetingInterestDisclosureRule(
        conflict_groups=(
            ("no_conflicts_statement", "conflicts_present"),
        ),
    ),
)
