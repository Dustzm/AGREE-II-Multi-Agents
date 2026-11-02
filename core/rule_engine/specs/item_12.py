"""
Rule specification for AGREE-II item 12.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    GroupingSpec,
    ItemSpec,
    ObjectVerificationSpec,
    RecommendationEvidenceLinkRule,
    RuleObjectSpec,
)


_RECOMMENDATION_EVIDENCE_VERIFICATION = ObjectVerificationSpec(
    llm_required=True,
    guidance=(
        "Accept only candidate spans that may support AGREE-II item 12 for the current guideline: a recommendation, "
        "recommendation identifier, citation group, evidence level/grade, rationale, evidence summary, or table/figure "
        "reference that can be linked to a specific recommendation. Do not decide full systematic coverage at object "
        "level. Reject standalone reference lists, standalone evidence tables, broad background evidence, or "
        "recommendation-formulation process text instead of extracting them as objects. Accept a general evidence-use "
        "method only when it is relevant as low-score evidence and does not enumerate references or table bodies. Treat "
        "citation numbers such as [42] as citation_group and evidence grades such as Grade A or Level 1B as "
        "evidence_level_or_grade, not as recommendation identifiers."
    ),
)


ITEM_12_SPEC = ItemSpec(
    item_id="item_12",
    title="There is an explicit link between the recommendations and the supporting evidence.",
    rule_profile="recommendation_evidence_link",
    object_specs={
        "recommendation_statement": RuleObjectSpec(
            type="recommendation_statement",
            role="component",
            description="当前指南中的具体推荐意见、推荐表行、推荐 bullet、recommendation box 或算法中的推荐节点。",
            max_objects=10,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "recommendation_identifier": RuleObjectSpec(
            type="recommendation_identifier",
            role="component",
            description=(
                "当前指南中可定位单条推荐的正式推荐编号、statement number、recommendation ID 或表格推荐行标识；"
                "不要把引用编号、文献编号、证据等级或推荐强度标签抽为 recommendation_identifier。"
            ),
            max_objects=10,
            require_value_in_quote=False,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "supporting_evidence_description": RuleObjectSpec(
            type="supporting_evidence_description",
            role="component",
            description="与具体推荐对应的支持证据描述、研究证据摘要、证据体说明或 recommendation-specific evidence statement。",
            max_objects=10,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "citation_group": RuleObjectSpec(
            type="citation_group",
            role="component",
            description="绑定到具体推荐的引用组、文献编号、脚注或 citation set。",
            max_objects=10,
            require_value_in_quote=False,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "evidence_level_or_grade": RuleObjectSpec(
            type="evidence_level_or_grade",
            role="component",
            description="绑定到具体推荐的证据等级、证据质量、certainty、GRADE、level of evidence 或 recommendation grade。",
            max_objects=10,
            require_value_in_quote=False,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "rationale_or_evidence_summary": RuleObjectSpec(
            type="rationale_or_evidence_summary",
            role="component",
            description="紧跟或明确对应具体推荐的 rationale、evidence summary、justification、remarks 或 supporting text。",
            max_objects=10,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "evidence_table_or_figure_reference": RuleObjectSpec(
            type="evidence_table_or_figure_reference",
            role="component",
            description="具体推荐明确引用的 evidence table、evidence profile、summary of findings table、figure 或 appendix。",
            max_objects=8,
            require_value_in_quote=False,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "recommendation_evidence_link_statement": RuleObjectSpec(
            type="recommendation_evidence_link_statement",
            role="component",
            description="说明推荐与证据如何逐条链接、如何通过证据等级/引用/表格映射，或推荐基于哪些证据的表达。",
            max_objects=6,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
        "general_evidence_method": RuleObjectSpec(
            type="general_evidence_method",
            role="exclusion",
            description="泛泛说明指南如何使用证据形成推荐，但没有推荐级映射。",
            max_objects=4,
            verification=_RECOMMENDATION_EVIDENCE_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "抽取当前指南中推荐意见与支持证据之间的明确链接。优先抽取 recommendations section、recommendation tables、"
        "rationale、evidence summary、evidence profile、summary of findings 或 appendix 中与单条推荐直接相连的证据描述、"
        "引用组、证据等级、证据表/图引用和 rationale。recommendation_identifier 只抽正式推荐编号、statement number、"
        "recommendation ID 或表格推荐行标识；[42]、[51]、普通引用编号一律抽为 citation_group，Grade A、Level 1B、"
        "certainty 一律抽为 evidence_level_or_grade。不要把普通叙述性建议、外部组织建议或背景管理建议抽为关键推荐"
        "anchor。优先抽取“推荐 anchor + 最紧邻证据标记/等级/rationale/link statement”的最小组合，不拆出大量重复对象。"
        "不要抽取 standalone reference list、独立证据表正文、背景证据讨论或推荐形成过程；证据表只抽取与具体推荐"
        "相连的表/图标识或引用，不抽整张表。"
    ),
    verification_guidance=(
        "AGREE-II item_12 要求读者能识别哪条推荐由哪些具体证据支持。Object verifier 只验证候选 span 是否可作为 "
        "item_12 候选证据；group verifier 判断推荐与证据是否属于同一 recommendation-level mapping。"
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "recommendations",
            "rationale",
            "evidence summary",
            "supporting evidence",
            "evidence profile",
            "summary of findings",
            "level of evidence",
            "grade",
            "appendix",
            "推荐",
            "推荐依据",
            "证据总结",
            "支持证据",
            "证据等级",
            "附录",
        ),
        include_phrases=(
            "level of evidence",
            "quality of evidence",
            "certainty of evidence",
            "supporting evidence",
            "evidence summary",
            "recommendation rationale",
            "summary of findings",
            "evidence profile",
            "based on",
            "证据等级",
            "支持证据",
            "推荐依据",
            "基于",
        ),
        include_terms=(
            "recommendation",
            "evidence",
            "rationale",
            "citation",
            "grade",
            "appendix",
            "推荐",
            "证据",
            "依据",
            "引用",
            "等级",
        ),
        negative_terms=(
            "search strategy",
            "study selection",
            "inclusion criteria",
            "consensus process",
            "references",
            "reference list",
            "bibliography",
            "literature list",
            "检索策略",
            "文献筛选",
            "纳入标准",
            "共识过程",
            "参考文献",
        ),
        neighbor_window=1,
        max_chunks=24,
        max_domain_chunks=12,
    ),
    grouping=GroupingSpec(
        group_profile="recommendation_evidence_link",
        anchor_object_types=("recommendation_statement", "recommendation_identifier"),
        member_object_types=(
            "supporting_evidence_description",
            "citation_group",
            "evidence_level_or_grade",
            "rationale_or_evidence_summary",
            "evidence_table_or_figure_reference",
            "recommendation_evidence_link_statement",
            "general_evidence_method",
        ),
        neighbor_window=1,
        same_section=True,
        max_groups=16,
        llm_required=True,
        guidance=(
            "Accept a group only when a current-guideline recommendation or recommendation identifier is clearly "
            "linked to specific supporting evidence, citations, evidence grade, rationale, evidence summary, or "
            "evidence table/figure reference. A group may be one recommendation table row, one recommendation block, "
            "a recommendation immediately followed by rationale/evidence, or adjacent chunks in the same local "
            "recommendation section. Treat recommendation identifiers as auxiliary anchors unless no recommendation "
            "statement is present in the same local unit and the identifier is a formal recommendation ID with a local "
            "evidence link. Reject standalone reference lists, standalone evidence tables, broad evidence discussion, "
            "or generic methods text not mapped to a specific recommendation."
        ),
    ),
    rule=RecommendationEvidenceLinkRule(),
)
