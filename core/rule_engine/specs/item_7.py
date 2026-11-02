"""
Rule specification for AGREE-II item 7.
"""
from __future__ import annotations

from core.rule_engine.models import ItemSpec, MilestoneRule, RuleObjectSpec, ScoreLevel, all_of, any_of


ITEM_7_SPEC = ItemSpec(
    item_id="item_7",
    title="Systematic methods were used to search for evidence.",
    rule_profile="ordered_milestone",
    object_specs={
        "search_activity": RuleObjectSpec(
            type="search_activity",
            role="component",
            description="泛泛说明进行了文献、证据或数据库检索，但未提供具体数据库名称。",
        ),
        "vague_database_reference": RuleObjectSpec(
            type="vague_database_reference",
            role="component",
            description="仅提及数据库或资料来源这类泛化表述，没有具体命名的信息源。",
        ),
        "named_information_source": RuleObjectSpec(
            type="named_information_source",
            role="component",
            description="明确命名的数据库、网站或信息源，例如 PubMed、Embase、CNKI、Cochrane。",
        ),
        "search_terms": RuleObjectSpec(
            type="search_terms",
            role="component",
            description="具体检索词、关键词、主题词、检索式或 Boolean 组合。",
        ),
        "search_time_coverage": RuleObjectSpec(
            type="search_time_coverage",
            role="component",
            description="明确检索日期、检索截止日期或文献覆盖时间范围。",
        ),
        "complete_search_strategy": RuleObjectSpec(
            type="complete_search_strategy",
            role="component",
            description="足以复现的完整检索策略，例如字段、逻辑组合、数据库特异检索式或附录检索策略表。",
        ),
    },
    extraction_guidance=(
        "只抽取证据检索策略相关对象。具体数据库、检索词、时间范围和完整检索策略可以来自不同 chunk。"
        "不要根据“系统评价”“参考文献”或方法学严谨性推断缺失检索细节。"
    ),
    rule=MilestoneRule(
        levels=(
            ScoreLevel(
                score_range="6-7",
                condition=all_of(
                    "named_information_source",
                    "search_terms",
                    "search_time_coverage",
                    "complete_search_strategy",
                ),
                description="明确数据库、检索词、检索时间，并给出完整检索策略。",
            ),
            ScoreLevel(
                score_range="5",
                condition=all_of(
                    "named_information_source",
                    "search_terms",
                    "search_time_coverage",
                ),
                description="明确数据库、检索词和检索时间。",
            ),
            ScoreLevel(
                score_range="4",
                condition=all_of("named_information_source", "search_terms"),
                description="明确数据库和检索词。",
            ),
            ScoreLevel(
                score_range="3",
                condition=all_of("named_information_source"),
                description="明确具体数据库或信息源。",
            ),
            ScoreLevel(
                score_range="2",
                condition=any_of("search_activity", "vague_database_reference"),
                description="仅泛泛提及检索数据库或检索活动。",
            ),
            ScoreLevel(score_range="1", condition=all_of(), description="未提及。"),
        )
    ),
)
