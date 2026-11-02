"""
Rule specification for AGREE-II item 14.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    GuidelineUpdateRule,
    ItemSpec,
    ObjectVerificationSpec,
    RuleObjectSpec,
)


_UPDATE_VERIFICATION = ObjectVerificationSpec(
    llm_required=True,
    guidance=(
        "Accept only statements about future updating, review, expiration, maintenance, responsible group, "
        "criteria, conditions, or process for the current guideline/statement/document itself. A review date, "
        "expiration date, valid-until date, standing update panel, evidence surveillance process, or new-evidence "
        "trigger can be valid even without a separate generic update mention. Reject clinical follow-up/review, "
        "literature search dates, external guideline updates, and mere statements that the current article is an "
        "updated/revised version without saying how or when this guideline will be updated. If the text describes a "
        "standing maintenance program or living guideline series that this document belongs to, keep it as a series "
        "update mechanism instead of rejecting it outright. If the text is only a generic methodology-manual "
        "requirement, classify it separately and do not treat it as direct evidence for this guideline."
    ),
)


ITEM_14_SPEC = ItemSpec(
    item_id="item_14",
    title="A procedure for updating the guideline is provided.",
    rule_profile="guideline_update",
    object_specs={
        "update_mention": RuleObjectSpec(
            type="update_mention",
            role="component",
            description="明确提及本指南、本文档、statement 或 guideline 自身的更新、更新计划、更新程序或需要定期更新。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "future_update_mention": RuleObjectSpec(
            type="future_update_mention",
            role="component",
            description="泛泛说明当前指南、声明或文档未来会更新、维护、复审或到期后重新审查，但没有具体细节。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "current_document_future_update_mechanism": RuleObjectSpec(
            type="current_document_future_update_mechanism",
            role="component",
            description="明确说明当前指南、声明或文档自身未来如何更新、何时复审、由谁维护或什么条件触发更新。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "series_or_living_guideline_update_mechanism": RuleObjectSpec(
            type="series_or_living_guideline_update_mechanism",
            role="component",
            description="说明当前文档所属的持续维护指南系列、living guideline、standards programme 或 standing committee 的更新机制。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "update_interval": RuleObjectSpec(
            type="update_interval",
            role="component",
            counts_for="specificity",
            description="本指南自身更新的时间节点、周期、间隔或截止时间。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "update_interval_or_review_date": RuleObjectSpec(
            type="update_interval_or_review_date",
            role="component",
            counts_for="specificity",
            description="当前指南自身未来更新、复审、过期、有效期或 review date 的时间点、周期、间隔或截止时间。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "update_criteria": RuleObjectSpec(
            type="update_criteria",
            role="component",
            counts_for="specificity",
            description="触发本指南自身更新的标准，例如新证据、新药物、新政策或证据变化。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "update_trigger_or_criteria": RuleObjectSpec(
            type="update_trigger_or_criteria",
            role="component",
            counts_for="specificity",
            description="触发当前指南未来更新的条件或标准，例如新证据、新治疗、新安全问题、新政策或证据变化。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "update_condition": RuleObjectSpec(
            type="update_condition",
            role="component",
            counts_for="specificity",
            description="本指南自身的更新条件、负责小组、持续监测机制或具体更新流程。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "update_responsible_body": RuleObjectSpec(
            type="update_responsible_body",
            role="component",
            counts_for="specificity",
            description="负责当前指南未来更新、维护、复审或证据监测的组织、小组、委员会或 standing panel。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "update_surveillance_or_process": RuleObjectSpec(
            type="update_surveillance_or_process",
            role="component",
            counts_for="specificity",
            description="当前指南未来更新的证据监测、维护、审查、触发评估或修订流程。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "current_revision_only": RuleObjectSpec(
            type="current_revision_only",
            role="exclusion",
            description="仅说明当前文档是 update、revision 或新版声明，但没有说明未来更新程序。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "clinical_review_or_followup": RuleObjectSpec(
            type="clinical_review_or_followup",
            role="exclusion",
            description="临床复查、患者随访或治疗 reevaluation，不属于本指南未来更新程序。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "search_date_only": RuleObjectSpec(
            type="search_date_only",
            role="exclusion",
            description="仅说明文献检索日期、检索截止日期或 evidence review 日期，不属于本指南未来更新程序。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "external_guideline_update": RuleObjectSpec(
            type="external_guideline_update",
            role="exclusion",
            description="仅说明外部指南、外部 protocol 或其他标准更新，不属于当前指南自身未来更新程序。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
        "generic_methodology_manual_requirement": RuleObjectSpec(
            type="generic_methodology_manual_requirement",
            role="exclusion",
            description="方法学手册、AGREE/机构标准或通用要求中说明指南应提供更新程序，但没有证明当前指南实际提供了该机制。",
            max_objects=3,
            verification=_UPDATE_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取与本指南、本文档、statement 或 guideline 自身更新程序相关的对象。不要把普通版本号、发布日期、"
        "文献检索日期、外部筛查 protocol、外部 screening guideline、临床复查或 reevaluation、临床流程或推荐内容中提到的定期 "
        "reevaluate/update 误判为本指南更新程序。仅说明当前文档是 updated/revised statement/guideline 不足以抽取更新程序对象，"
        "除非文本明确说明更新对象就是本指南自身。"
        "除非文本明确给出本指南未来如何更新、何时更新、由谁负责更新或何种条件触发更新。"
        "如果文本描述的是当前文档所属的持续维护指南系列、living guideline、standards programme 或 standing committee "
        "的更新机制，抽取 series_or_living_guideline_update_mechanism 并同时抽取具体的时间、触发或责任/流程对象。"
        "如果只是方法学手册或通用标准要求指南应提供更新程序，抽取 generic_methodology_manual_requirement，不要抽取加分对象。"
        "如文本只是当前版本、当前修订、文献检索日期、临床随访或外部指南更新，可抽 current_revision_only、"
        "clinical_review_or_followup、search_date_only、external_guideline_update 或 generic_methodology_manual_requirement "
        "作为排除候选，仅供 engine/debug 使用。"
    ),
    verification_guidance=(
        "AGREE-II item_14 评价当前指南自身是否提供更新程序。泛泛提及指南会更新但没有细节为 2-3 分；"
        "给出更新时间点/周期/有效期、更新标准、更新条件/负责机制三类信息中的任一类为 4-5 分；任两类为 6 分；"
        "三类齐全为 7 分。若证据来自当前文档所属的持续维护指南系列或 living/standards programme，可作为更新机制证据，"
        "但除非明确绑定当前文档，否则最高为 5-6 分。更新对象不能是临床复查、患者随访、文献检索日期、"
        "外部 guideline/protocol 更新、通用方法学要求，也不能只是说明本文是 updated/revised version。"
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "update",
            "updating",
            "guideline update",
            "review",
            "expiry",
            "expiration",
            "methods",
            "更新",
            "修订",
            "复审",
            "有效期",
        ),
        include_phrases=(
            "updating the guideline",
            "guideline will be updated",
            "review date",
            "expiration date",
            "document expiration",
            "valid until",
            "standing panel",
            "new evidence",
            "literature surveillance",
            "指南更新",
            "更新程序",
            "有效期",
        ),
        include_terms=(
            "update",
            "updated",
            "review",
            "expiration",
            "expiry",
            "valid",
            "evidence",
            "更新",
            "修订",
            "复审",
        ),
        negative_terms=(
            "clinical review",
            "follow-up",
            "search date",
            "external guideline",
        ),
        neighbor_window=2,
        max_chunks=18,
        max_domain_chunks=8,
    ),
    rule=GuidelineUpdateRule(),
)
