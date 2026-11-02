"""
Rule specification for AGREE-II item 4.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ItemSpec,
    RuleObjectSpec,
    WeightedChecklistItem,
    WeightedChecklistRule,
)


ITEM_4_SPEC = ItemSpec(
    item_id="item_4",
    title=(
        "The guideline development group includes individuals from all relevant "
        "professional groups."
    ),
    rule_profile="weighted_checklist",
    object_specs={
        "member_name": RuleObjectSpec(
            type="member_name",
            role="component",
            description="指南制订组、工作组、专家组或作者组成员姓名。",
            max_objects=3,
        ),
        "specialty_or_discipline": RuleObjectSpec(
            type="specialty_or_discipline",
            role="component",
            description="成员专业、学科、科室、专业领域或相关专长。",
            max_objects=3,
        ),
        "institution": RuleObjectSpec(
            type="institution",
            role="component",
            description="成员所属机构、单位、医院、大学、协会或组织。",
            max_objects=3,
        ),
        "geographic_location": RuleObjectSpec(
            type="geographic_location",
            role="component",
            description="成员或机构所在国家、地区、省市等地理位置信息。",
            max_objects=3,
        ),
        "member_role": RuleObjectSpec(
            type="member_role",
            role="component",
            description="成员在指南开发中的角色，例如执笔人、方法学专家、临床专家或秘书组。",
            max_objects=3,
        ),
        "multidisciplinary_team": RuleObjectSpec(
            type="multidisciplinary_team",
            role="component",
            description="体现多学科团队构成的信息，例如临床、护理、方法学、统计或管理等不同专业参与。",
            max_objects=3,
        ),
    },
    extraction_guidance=(
        "只抽取指南开发小组成员构成相关对象。不要把外部评审者或目标人群代表当作指南开发小组成员，"
        "除非文本明确说明其参与了指南开发。每类对象只需要证明该事实存在，不要逐个穷举全部成员、机构、专业或地区；"
        "如果同一名单或表格中有大量相似信息，只保留最直接、最有代表性的少量证据。"
    ),
    rule=WeightedChecklistRule(
        base_score=1,
        max_score=7,
        items=(
            WeightedChecklistItem(fact="member_name"),
            WeightedChecklistItem(fact="specialty_or_discipline"),
            WeightedChecklistItem(fact="institution"),
            WeightedChecklistItem(fact="geographic_location"),
            WeightedChecklistItem(fact="member_role"),
            WeightedChecklistItem(fact="multidisciplinary_team"),
        ),
    ),
)
