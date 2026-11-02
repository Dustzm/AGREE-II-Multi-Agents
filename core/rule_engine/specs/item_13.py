"""
Rule specification for AGREE-II item 13.
"""
from __future__ import annotations

from core.rule_engine.models import ItemSpec, MilestoneRule, RuleObjectSpec, ScoreLevel, all_of


ITEM_13_SPEC = ItemSpec(
    item_id="item_13",
    title="The guideline has been externally reviewed by experts prior to its publication.",
    rule_profile="ordered_milestone",
    object_specs={
        "external_review_mention": RuleObjectSpec(
            type="external_review_mention",
            role="component",
            description="提及指南发布前经过外部评审、同行评议或外部专家审查。",
        ),
        "external_reviewer_identity": RuleObjectSpec(
            type="external_reviewer_identity",
            role="component",
            description="提供外部评审者、评审小组、专家名称、身份、专业或机构。",
        ),
        "review_process_or_result": RuleObjectSpec(
            type="review_process_or_result",
            role="component",
            description="描述外部评审方法、过程、意见收集、修改过程或评审结果。",
        ),
        "review_purpose": RuleObjectSpec(
            type="review_purpose",
            role="component",
            description="说明外部评审目的，例如质量把关、适用性审查、方法学审查或内容审查。",
        ),
    },
    extraction_guidance=(
        "只抽取发布前外部评审相关对象。不要把指南开发组内部讨论、作者审稿或期刊发表后的同行评议误判为外部评审。"
    ),
    rule=MilestoneRule(
        levels=(
            ScoreLevel(
                score_range="6-7",
                condition=all_of(
                    "external_reviewer_identity",
                    "review_purpose",
                    "review_process_or_result",
                ),
                description="描述外部评审目的、评审者身份以及方法、过程或结果。",
            ),
            ScoreLevel(
                score_range="5",
                condition=all_of("external_reviewer_identity", "review_process_or_result"),
                description="描述评审者身份和外部评审方法、过程或结果。",
            ),
            ScoreLevel(
                score_range="4",
                condition=all_of("external_reviewer_identity"),
                description="提供外部评审者或评审小组信息。",
            ),
            ScoreLevel(
                score_range="2-3",
                condition=all_of("external_review_mention"),
                description="仅提及外部评审。",
            ),
            ScoreLevel(score_range="1", condition=all_of(), description="未提及。"),
        )
    ),
)
