"""
Rule specification for AGREE-II item 22.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    FundingDisclosureRule,
    GroupingSpec,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
)


_FUNDING_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "fund",
        "funding",
        "financial support",
        "sponsor",
        "support",
        "supported by",
        "commission",
        "commissioned by",
        "convening",
        "industry support",
        "industry involvement",
        "external funding",
        "funding none",
        "financial support none",
        "no funding",
        "no external funding",
        "general revenues",
        "editorial independence",
        "funding body",
        "funding bodies",
        "independent from the funding body",
        "editorially independent from the funding body",
        "grant",
        "academic grant",
        "financial support and sponsorship",
        "financial support and sponsorship none",
        "资助",
        "资金",
        "赞助",
        "经费",
    ),
    negative_terms=(
        "trial funded",
        "study was funded",
        "research grant",
        "workshop",
        "引用研究",
    ),
    llm_required=True,
    guidance=(
        "Accept only funding/sponsor information for the current guideline, statement, document, or its development. "
        "Judge each candidate against its exact object type: no funding, funding disclosed, commissioned/sponsored, direct no influence, "
        "funding-body independence, sponsor influence, no industry support, generic editorial independence, related meeting/convening funding, "
        "for-profit funder, or non-profit/government/professional-association funder. "
        "Accept concise disclosure formats such as 'Funding: None', 'Financial support: none', 'no external funding', "
        "or 'supported by [organization]' when they refer to the current guideline/document/manuscript. "
        "Accept 'commissioned by [organization]' as commissioned/sponsored support when it refers to this guideline or statement. "
        "Accept 'does not use industry support' only as no_industry_support_statement, not as direct no influence. "
        "Accept 'editorially independent from the funding body' as funding_body_independence_statement when it directly links independence to the funder. "
        "Accept general editorial-independence wording only as generic_editorial_independence_statement. "
        "Accept meeting/workshop/convening support only as related_meeting_or_convening_funding unless it explicitly funded this guideline. "
        "Accept acknowledgments only when they explicitly disclose a grant, financial support, sponsorship, or support for "
        "the current manuscript/guideline/development work. Reject ordinary thanks, author affiliations, and COI-only statements."
    ),
)


ITEM_22_SPEC = ItemSpec(
    item_id="item_22",
    title="The views of the funding body have not influenced the content of the guideline.",
    rule_profile="funding_disclosure",
    object_specs={
        "no_funding_statement": RuleObjectSpec(
            type="no_funding_statement",
            role="component",
            description="明确声明本指南、本文档或本指南制定过程没有资助、赞助或资金支持。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "funding_disclosed": RuleObjectSpec(
            type="funding_disclosed",
            role="component",
            description="明确披露本指南、本文档或其制定过程存在资助、赞助、资金支持、一般收入支持或机构经费用于指南开发。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "funding_section_heading": RuleObjectSpec(
            type="funding_section_heading",
            role="component",
            description="资助披露章节或表格标题，如 Funding、Financial support、Financial support and sponsorship。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "funding_section_value": RuleObjectSpec(
            type="funding_section_value",
            role="component",
            description="紧邻资助披露标题的值或正文，如 None、Sanofi India、No external funding。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "acknowledged_financial_support": RuleObjectSpec(
            type="acknowledged_financial_support",
            role="component",
            description="Acknowledgments 或类似部分中明确披露当前指南/本文稿/项目获得 grant、financial support、sponsorship 或资金支持。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "development_meeting_funding_statement": RuleObjectSpec(
            type="development_meeting_funding_statement",
            role="component",
            description="明确说明当前指南制定会议、共识会议或 convening 的资助/无资助状态，但未完整说明指南本身资助状态。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "commission_or_sponsorship_statement": RuleObjectSpec(
            type="commission_or_sponsorship_statement",
            role="component",
            description="明确说明本指南、声明或文档由某组织委托、赞助、commissioned 或 sponsored。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "direct_no_influence_statement": RuleObjectSpec(
            type="direct_no_influence_statement",
            role="component",
            description="明确声明资助方、赞助方或基金方 had no role/no influence/no effect，未影响指南内容、推荐意见或制定过程。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "funding_body_independence_statement": RuleObjectSpec(
            type="funding_body_independence_statement",
            role="component",
            description="明确说明本指南、本文档或内容在编辑或制定上独立于资助方、funding body 或 funder。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "sponsor_influence_statement": RuleObjectSpec(
            type="sponsor_influence_statement",
            role="component",
            description="明确说明资助方、赞助方或基金方影响、参与或决定了指南内容或推荐意见。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "no_industry_support_statement": RuleObjectSpec(
            type="no_industry_support_statement",
            role="component",
            description="明确声明本指南、本文档或其制定过程没有行业支持、商业支持或行业参与。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "generic_editorial_independence_statement": RuleObjectSpec(
            type="generic_editorial_independence_statement",
            role="component",
            description="泛化说明发布机构、指南组织或编辑流程保持 editorial independence，但未直接说明某资助方没有影响本指南内容。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "related_meeting_or_convening_funding": RuleObjectSpec(
            type="related_meeting_or_convening_funding",
            role="component",
            description="仅说明相关会议、workshop、consensus meeting 或 convening 的资助/无资助，未明确指向本指南本身。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "for_profit_funding": RuleObjectSpec(
            type="for_profit_funding",
            role="component",
            description="资助方为营利机构、企业、制药公司、器械公司或商业组织。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
        "non_profit_or_government_funding": RuleObjectSpec(
            type="non_profit_or_government_funding",
            role="component",
            description="资助方为非营利机构、政府机构、基金会、学会、协会或专业组织。",
            max_objects=3,
            require_value_in_quote=False,
            verification=_FUNDING_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取明确指向本指南、本文档、本文稿或指南制定过程的资助/赞助信息。不要把会议、workshop、"
        "相关项目、机构使命、版权、作者单位或利益冲突声明推断为本指南资助状态。"
        "如果原文写明 'funds development of the Standards/guideline'、'funded from general revenues'、"
        "'core funding supports individual guideline development projects'、'supported by [funder]' 等直接资助本指南/指南开发的表达，"
        "必须抽取 funding_disclosed；同时根据资助方性质抽取 non_profit_or_government_funding 或 for_profit_funding。"
        "资助披露标题和值必须尽量完整抽取：如 'Financial support and sponsorship' 抽 funding_section_heading，"
        "紧邻的 'Sanofi India' 抽 funding_section_value，并根据语境抽 acknowledged_financial_support 或 funding_disclosed；"
        "'Funding: None'、'Funding None' 可同时作为完整行 quote 抽 no_funding_statement。"
        "Acknowledgments 中如果明确写 academic grant、financial support、sponsorship 或 support for this manuscript/guideline，"
        "抽 acknowledged_financial_support；普通感谢、编辑协助或作者单位不要抽。"
        "'commissioned by [organization]'、'sponsored by [organization]' 等当前指南/声明委托或赞助表达，"
        "抽取 commission_or_sponsorship_statement；如能判断资助方性质，同时抽取相应 funder type。"
        "'Funding: None'、'Funding None'、'Financial support: none'、'no external funding' 等标题式或表格式披露，"
        "如果指向当前指南/本文档/本文稿，可抽取 no_funding_statement。"
        "'had no role'、'did not influence'、'had no effect on the content/recommendations' 等直接说明资助方不影响内容的表达，"
        "抽取 direct_no_influence_statement。'does not use industry support'、'with no industry involvement or support' "
        "只抽取 no_industry_support_statement，不能抽为 direct_no_influence_statement，也不能单独当作 no_funding_statement。"
        "'editorially independent from the funding body'、'independent from the funder/funding body' 这类直接关联资助方的独立性表达，"
        "抽取 funding_body_independence_statement；'retains editorial independence' 等没有关联资助方的泛化编辑独立声明只抽取 "
        "generic_editorial_independence_statement。"
        "如果文字明确是当前指南制定会议、共识会议或 convening 的资助/无资助，抽 development_meeting_funding_statement；"
        "如果只是相关 meeting、workshop 或 convening 的 funding/no funding，抽取 related_meeting_or_convening_funding，"
        "不要抽为本指南 funding_disclosed 或 no_funding_statement。"
    ),
    verification_guidance=(
        "AGREE-II item 22 requires explicit evidence about funding or sponsorship for this guideline, document, "
        "manuscript, or guideline development process, and whether the funder influenced the content. A concise title "
        "or table row such as 'Funding: None', 'Financial support: none', or 'no external funding' is a valid no-funding "
        "disclosure and can support score 7 by itself. If funding is disclosed, identify whether it is commercial, "
        "non-commercial/government/professional-association, or unknown, and whether there is a direct no-influence or "
        "influence statement. Section heading plus value formats such as 'Financial support and sponsorship' followed "
        "by 'Sanofi India' are valid disclosure candidates. Commissioned/sponsored wording counts as disclosed support. No-industry-support wording "
        "and generic editorial independence are separate partial facts, not direct no-influence facts. Direct editorial independence from the funding body "
        "is a funder no-influence statement and can support score 7 when it refers to the current guideline/document. Do not infer no funding from silence, publisher identity, nonprofit status, affiliations, "
        "COI statements, or lack of named commercial sponsors. Acknowledgments may count only when they explicitly disclose "
        "financial support, a grant, or sponsorship for the current guideline/manuscript/development work. Reject funding of cited studies, workshops, related "
        "meetings, parent organizations, or general institutional operations as current-guideline funding unless the text explicitly ties it to this "
        "guideline or its development; current development meeting/convening-only statements are partial evidence, while unrelated meeting/convening-only statements may be accepted only as related_meeting_or_convening_funding."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "funding",
            "financial support",
            "financial support and sponsorship",
            "sponsor",
            "editorial independence",
            "acknowledgments",
            "资助",
            "资金",
            "赞助",
        ),
        include_phrases=(
            "funding support",
            "financial support",
            "funded by",
            "supported by",
            "commissioned by",
            "sponsored by",
            "funding none",
            "financial support none",
            "financial support and sponsorship",
            "no external funding",
            "academic grant",
            "Sanofi India",
            "sponsor",
            "role of the funder",
            "funding body",
            "independent from the funding body",
            "editorially independent from the funding body",
            "no industry involvement",
            "no industry support",
            "general revenues",
            "editorial independence",
            "convening this meeting",
            "no funding was sought for convening",
            "资助方",
            "资金支持",
            "无行业支持",
        ),
        include_terms=(
            "funding",
            "funded",
            "sponsor",
            "support",
            "commission",
            "industry",
            "independent",
            "grant",
            "revenue",
            "资助",
            "资金",
            "赞助",
        ),
        negative_terms=(
            "trial funded",
            "study was funded",
            "research grant",
            "conflict of interest",
            "affiliation",
        ),
        neighbor_window=1,
        max_chunks=18,
        max_domain_chunks=8,
    ),
    grouping=GroupingSpec(
        group_profile="funding_disclosure",
        anchor_object_types=(
            "no_funding_statement",
            "funding_disclosed",
            "commission_or_sponsorship_statement",
            "acknowledged_financial_support",
            "development_meeting_funding_statement",
            "related_meeting_or_convening_funding",
            "funding_section_heading",
            "funding_section_value",
        ),
        member_object_types=(
            "no_funding_statement",
            "funding_disclosed",
            "commission_or_sponsorship_statement",
            "acknowledged_financial_support",
            "development_meeting_funding_statement",
            "related_meeting_or_convening_funding",
            "funding_section_heading",
            "funding_section_value",
            "direct_no_influence_statement",
            "funding_body_independence_statement",
            "sponsor_influence_statement",
            "no_industry_support_statement",
            "generic_editorial_independence_statement",
            "for_profit_funding",
            "non_profit_or_government_funding",
        ),
        neighbor_window=2,
        same_section=True,
        max_groups=12,
        llm_required=True,
        guidance=(
            "Accept a funding_disclosure group only when the objects describe one funding/sponsorship disclosure unit. "
            "Use title-value relations: a funding heading and its adjacent value belong together. For each accepted group, "
            "set attributes when supported: status = none|funded|commissioned|meeting_only|unknown; scope = "
            "current_guideline|current_manuscript|development_process|development_meeting|related_only; funder_type = "
            "for_profit|non_profit_or_government|unknown; influence = direct_no_influence|funding_body_independence|"
            "sponsor_influence|no_industry_support|generic_independence|none. Accept current development meeting/convening support as "
            "scope=development_meeting, but classify unrelated meeting/workshop support as scope=related_only. Do not "
            "reject partial disclosures merely because they are insufficient for a high score; the engine handles scoring."
        ),
    ),
    rule=FundingDisclosureRule(
        conflict_groups=(
            ("no_funding_statement", "funding_disclosed"),
            ("no_funding_statement", "commission_or_sponsorship_statement"),
            ("no_funding_statement", "acknowledged_financial_support"),
            ("no_funding_statement", "funding_section_value"),
            ("no_funding_statement", "for_profit_funding"),
            ("no_funding_statement", "non_profit_or_government_funding"),
            ("no_funding_statement", "direct_no_influence_statement"),
            ("no_funding_statement", "funding_body_independence_statement"),
            ("no_funding_statement", "sponsor_influence_statement"),
            ("direct_no_influence_statement", "sponsor_influence_statement"),
            ("funding_body_independence_statement", "sponsor_influence_statement"),
            ("for_profit_funding", "non_profit_or_government_funding"),
        ),
    ),
)
