"""
Rule specification for AGREE-II item 10.
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


_FORMULATION_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "formulated",
        "developed",
        "approved",
        "consensus",
        "delphi",
        "vote",
        "voted",
        "voting",
        "final vote",
        "agreed",
        "panel",
        "meeting",
        "recommendations",
        "statement",
        "guideline",
        "konsensprozess",
        "konsensusverfahren",
        "nominaler gruppenprozess",
        "abstimmungsverfahren",
        "zustimmung",
        "verabschiedet",
        "starker konsens",
        "争议",
        "共识",
        "投票",
        "推荐意见",
        "指南",
    ),
    negative_terms=(
        "diagnostic procedure",
        "treatment algorithm",
        "clinical judgment alone",
        "literature search",
        "evidence search",
        "expert consensus as evidence",
        "external consensus report",
        "诊疗流程",
    ),
    llm_required=True,
    guidance=(
        "Accept only evidence about how the current guideline/statement/recommendations were formulated, "
        "approved, adopted, voted on, finalized, or how disagreement was resolved. Accept European/German "
        "formulation terms such as Konsensprozess, Konsensusverfahren, Nominaler Gruppenprozess, "
        "Abstimmungsverfahren, Zustimmung, verabschiedet, and starker Konsens only when they describe the current "
        "document's recommendation-formulation process. Reject external documents, ordinary recommendation text, "
        "expert consensus used only as evidence grading, evidence search/appraisal alone, panel composition alone, "
        "ordinary discussion, and clinical workflow."
    ),
)


ITEM_10_SPEC = ItemSpec(
    item_id="item_10",
    title="The methods for formulating the recommendations are clearly described.",
    rule_profile="ordered_milestone",
    object_specs={
        "formulation_method": RuleObjectSpec(
            type="formulation_method",
            role="component",
            description="明确用于形成本指南/本文最终推荐意见的方法，例如 Delphi、正式/非正式共识、投票、nominal group、EtD 框架、批准/采纳/定稿流程或争议解决方法。",
            max_objects=5,
            verification=_FORMULATION_VERIFICATION,
        ),
        "consensus_process_heading": RuleObjectSpec(
            type="consensus_process_heading",
            role="component",
            description="当前指南方法学章节中指向推荐形成的共识过程、推荐制定、批准、表决或定稿小节标题。",
            max_objects=4,
            verification=_FORMULATION_VERIFICATION,
        ),
        "approval_or_finalization_statement": RuleObjectSpec(
            type="approval_or_finalization_statement",
            role="component",
            description="说明当前指南/声明/推荐意见被批准、采纳、审定、finalized、verabschiedet 或最终确认的表达。",
            max_objects=4,
            verification=_FORMULATION_VERIFICATION,
        ),
        "voting_or_agreement_statement": RuleObjectSpec(
            type="voting_or_agreement_statement",
            role="component",
            description="说明当前推荐形成中的投票、同意、agreement、Zustimmung、强共识或 consensus level 的表达。",
            max_objects=4,
            verification=_FORMULATION_VERIFICATION,
        ),
        "process_detail": RuleObjectSpec(
            type="process_detail",
            role="component",
            description="本指南推荐形成方法的具体过程细节，例如投票流程、共识步骤、轮次、同意阈值、主持/参与流程、争议解决流程或最终批准/采纳流程。",
            max_objects=5,
            verification=_FORMULATION_VERIFICATION,
        ),
        "result_detail": RuleObjectSpec(
            type="result_detail",
            role="component",
            description="推荐形成过程的结果，例如投票结果、共识度、同意比例、强共识、未达成共识、分歧结果或争议解决结果。",
            max_objects=5,
            verification=_FORMULATION_VERIFICATION,
        ),
        "recommendation_impact": RuleObjectSpec(
            type="recommendation_impact",
            role="component",
            description="说明过程或结果如何影响最终推荐意见。",
            max_objects=5,
            verification=_FORMULATION_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取当前 guideline、statement、position statement、meeting report 或 recommendations 的推荐意见形成方法相关对象，"
        "必须能看出文本在说明本指南/本文推荐意见如何被 formulated、developed、approved、agreed、voted、resolved 或 finalized。"
        "证据必须指向当前文件的最终推荐或共识意见形成过程，而不是当前文件引用的外部文件或外部组织流程。"
        "可抽取 Delphi、Glaser、formal/informal consensus、voting、EtD framework、nominal group、expert panel meeting、"
        "resolution of disagreement，以及德语/欧洲指南常见表达 Konsensprozess、Konsensusverfahren、Nominaler Gruppenprozess、"
        "Abstimmungsverfahren、Zustimmung、verabschiedet、starker Konsens 等。process_detail 只能抽取推荐形成过程细节，"
        "例如投票流程、共识步骤、轮次、同意阈值、主持/参与流程、争议解决流程、最终批准/采纳/定稿流程。"
        "result_detail 可抽取共识比例、投票结果、强共识、未达成共识或分歧结果。"
        "不要把 recommend/recommends/recommended 这类普通推荐内容、临床检测/诊断/治疗流程、证据检索、证据评价、证据分级、"
        "专家组构成、普通会议讨论、普通专家观点、clinical experience and judgment、外部 consensus report/document、外部 task force/report、外部 ADA/IADPSG/"
        "WHO 等组织的立场文件，或仅有专家共识标签"
        "误判为推荐形成方法，除非文本明确说明这些过程用于当前指南/本文的最终推荐意见或最终决定。"
        "反复出现在推荐正文中的 'meeting participants agreed'、'task force agrees' 只能作为推荐内容；"
        "只有当同一语境正在描述当前指南的形成过程、决策过程、投票/共识结果或争议解决时，才可抽取为 formulation_method 或 result_detail。"
    ),
    verification_guidance=(
        "AGREE-II item_10 评价当前指南/声明/推荐意见形成方法是否清楚。仅提及 Delphi、consensus、voting、formal/informal consensus、"
        "nominal group、EtD、批准/采纳/定稿或争议解决等当前推荐形成方法，可作为 2-3 分证据；同一推荐形成证据中进一步说明步骤、"
        "投票/共识流程、轮次、阈值、争议解决或批准流程，或说明共识比例、投票结果、强共识、未达成共识等结果，可进入 4-5 分；"
        "同一证据同时说明过程、结果及其如何影响最终推荐意见，才可进入 6-7 分。不要把外部 consensus report、普通专家组构成、"
        "普通会议讨论、证据检索/评价、证据等级标签、临床流程或推荐正文中的普通 agreed/recommended 当作当前指南推荐形成方法。"
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "methods",
            "guideline development",
            "recommendation formulation",
            "consensus process",
            "konsensprozess",
            "konsensusverfahren",
            "nominaler gruppenprozess",
            "abstimmungsverfahren",
            "development process",
            "方法",
            "指南制定",
            "推荐形成",
        ),
        include_phrases=(
            "recommendations were formulated",
            "formulating the recommendations",
            "developed the recommendations",
            "consensus process",
            "formal consensus",
            "informal consensus",
            "delphi",
            "voting",
            "voted",
            "evidence-to-decision",
            "nominal group",
            "disagreement",
            "nominaler gruppenprozess",
            "konsensprozess",
            "konsensusverfahren",
            "abstimmungsverfahren",
            "starker konsens",
            "strong consensus",
            "final vote",
            "meeting participants",
            "推荐意见形成",
            "达成共识",
            "投票",
        ),
        include_terms=(
            "formulated",
            "approved",
            "consensus",
            "delphi",
            "voting",
            "panel",
            "meeting",
            "finalized",
            "zustimmung",
            "verabschiedet",
            "共识",
            "投票",
        ),
        negative_terms=(
            "previous consensus report",
            "external consensus",
            "diagnostic criteria",
            "treatment algorithm",
            "clinical judgment",
            "evidence search",
        ),
        neighbor_window=1,
        max_chunks=20,
        max_domain_chunks=10,
    ),
    grouping=GroupingSpec(
        group_profile="recommendation_formulation",
        anchor_object_types=(
            "formulation_method",
            "consensus_process_heading",
            "approval_or_finalization_statement",
            "voting_or_agreement_statement",
        ),
        member_object_types=("process_detail", "result_detail", "recommendation_impact"),
        neighbor_window=1,
        same_section=True,
        max_groups=10,
        llm_required=True,
        guidance=(
            "For item10, accept a group only when the objects describe the same current-guideline "
            "recommendation formulation or final-decision process. A singleton formulation_method group "
            "is valid for low-score evidence, but a heading-only group is not enough by itself. "
            "Reject groups that combine a method with unrelated meeting text, "
            "external consensus documents, evidence search/appraisal, or ordinary recommendation wording."
        ),
    ),
    rule=MilestoneRule(
        levels=(
            ScoreLevel(
                score_range="6-7",
                condition=all_of(
                    any_of(
                        "formulation_method",
                        "consensus_process_heading",
                        "approval_or_finalization_statement",
                        "voting_or_agreement_statement",
                    ),
                    "process_detail",
                    "result_detail",
                    "recommendation_impact",
                ),
                description="全面描述过程、结果及其对最终推荐意见的影响。",
            ),
            ScoreLevel(
                score_range="4-5",
                condition=all_of(
                    any_of(
                        "formulation_method",
                        "consensus_process_heading",
                        "approval_or_finalization_statement",
                        "voting_or_agreement_statement",
                    ),
                    any_of("process_detail", "result_detail"),
                ),
                description="提供方法，并描述具体过程或结果。",
            ),
            ScoreLevel(
                score_range="2-3",
                condition=any_of(
                    "formulation_method",
                    "approval_or_finalization_statement",
                    "voting_or_agreement_statement",
                ),
                description="仅说明推荐意见形成方法。",
            ),
            ScoreLevel(score_range="1", condition=all_of(), description="未提及。"),
        )
    ),
)
