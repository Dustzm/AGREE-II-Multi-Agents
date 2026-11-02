"""
Rule specification for AGREE-II item 3.
"""
from __future__ import annotations

from core.rule_engine.models import ItemSpec, MilestoneRule, RuleObjectSpec, ScoreLevel, all_of


ITEM_3_SPEC = ItemSpec(
    item_id="item_3",
    title="The population to whom the guideline is meant to apply is specifically described.",
    rule_profile="ordered_milestone",
    object_specs={
        "rough_population": RuleObjectSpec(
            type="rough_population",
            role="component",
            description="粗略目标人群类别，例如糖尿病患者、孕妇、儿童或公众。",
        ),
        "detailed_population_characteristics": RuleObjectSpec(
            type="detailed_population_characteristics",
            role="component",
            description=(
                "具体人群特征，例如年龄、性别、临床状态、严重程度、分期、共病、治疗方式、风险状态、"
                "功能状态、适用情境或场景。对于就业或安全相关指南，胰岛素/非胰岛素治疗、严重低血糖、"
                "低血糖无感知、糖尿病并发症、高风险岗位、特定工作职责或安全敏感场景也属于详细人群特征。"
            ),
        ),
        "exclusion_criteria": RuleObjectSpec(
            type="exclusion_criteria",
            role="component",
            description="明确不适用或排除的人群、条件或疾病状态。",
        ),
    },
    extraction_guidance=(
        "只抽取指南适用患者、公众或目标人群相关对象。不要把指南使用者、医务人员或作者机构误判为目标人群。"
        "对就业、教育、公共卫生等非纯临床指南，目标人群的治疗方式、风险状态、功能状态、工作职责、"
        "安全敏感岗位或适用场景也应视为详细人群特征。"
    ),
    rule=MilestoneRule(
        levels=(
            ScoreLevel(
                score_range="7",
                condition=all_of("detailed_population_characteristics", "exclusion_criteria"),
                description="详细描述目标人群特征，并明确排除标准。",
            ),
            ScoreLevel(
                score_range="4-6",
                condition=all_of("detailed_population_characteristics"),
                description="详细描述目标人群特征。",
            ),
            ScoreLevel(
                score_range="2-3",
                condition=all_of("rough_population"),
                description="粗略说明目标人群类别。",
            ),
            ScoreLevel(score_range="1", condition=all_of(), description="未提及。"),
        )
    ),
)
