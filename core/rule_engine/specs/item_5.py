"""
Rule specification for AGREE-II item 5.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    ItemSpec,
    MilestoneRule,
    ObjectVerificationSpec,
    RuleObjectSpec,
    ScoreLevel,
    all_of,
    any_of,
)


_PATIENT_PREFERENCE_VERIFICATION = ObjectVerificationSpec(
    required_any_terms=(
        "patient",
        "public",
        "consumer",
        "lay",
        "target population",
        "representative",
        "preference",
        "value",
        "view",
        "experience",
        "focus group",
        "interview",
        "survey",
        "consultation",
        "患者",
        "公众",
        "目标人群",
        "偏好",
        "价值观",
        "观点",
        "体验",
        "访谈",
        "问卷",
    ),
    negative_context_terms=(
        "expert opinion",
        "clinical judgment",
        "clinician preference",
        "physician preference",
        "panel preference",
        "专家意见",
        "临床判断",
    ),
    guidance="Accept only target-population, patient, public, consumer, lay representative, or caregiver views/preferences.",
)


ITEM_5_SPEC = ItemSpec(
    item_id="item_5",
    title="The views and preferences of the target population have been sought.",
    rule_profile="ordered_milestone",
    object_specs={
        "preference_considered": RuleObjectSpec(
            type="preference_considered",
            role="component",
            description="声明考虑患者、公众或目标人群的观点、价值观、体验或偏好。",
            max_objects=5,
            verification=_PATIENT_PREFERENCE_VERIFICATION,
        ),
        "simple_collection_method": RuleObjectSpec(
            type="simple_collection_method",
            role="component",
            description="简单收集方法，例如咨询、访谈、问卷或征求意见。",
            max_objects=5,
            verification=_PATIENT_PREFERENCE_VERIFICATION,
        ),
        "comprehensive_collection_method": RuleObjectSpec(
            type="comprehensive_collection_method",
            role="component",
            description="深入参与方法，例如患者代表参与制订、优先级设定、工作组或外部评审。",
            max_objects=5,
            verification=_PATIENT_PREFERENCE_VERIFICATION,
        ),
        "preference_summary": RuleObjectSpec(
            type="preference_summary",
            role="component",
            description="对患者或公众观点、偏好、体验或价值观进行总结。",
            max_objects=5,
            verification=_PATIENT_PREFERENCE_VERIFICATION,
        ),
        "used_in_guideline_or_recommendations": RuleObjectSpec(
            type="used_in_guideline_or_recommendations",
            role="component",
            description="说明患者或公众观点被用于指南开发过程或推荐意见形成。",
            max_objects=5,
            verification=_PATIENT_PREFERENCE_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "只抽取患者、公众或目标人群观点偏好相关对象。不要把专家意见、指南使用者意见或作者共识误判为目标人群偏好。"
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "patient preferences",
            "patient values",
            "stakeholder involvement",
            "public consultation",
            "consumer involvement",
            "患者偏好",
            "公众参与",
        ),
        include_phrases=(
            "patient preferences",
            "patient values",
            "patients' views",
            "public consultation",
            "focus groups",
            "patient representatives",
            "lay representatives",
            "target population",
            "患者偏好",
            "患者观点",
            "目标人群观点",
        ),
        include_terms=(
            "preference",
            "values",
            "views",
            "experience",
            "survey",
            "interview",
            "consultation",
            "representative",
            "偏好",
            "价值观",
            "观点",
            "体验",
            "问卷",
            "访谈",
        ),
        negative_terms=(
            "clinician preference",
            "physician preference",
            "clinical judgment",
            "expert opinion",
        ),
        neighbor_window=1,
        max_chunks=18,
        max_domain_chunks=8,
    ),
    rule=MilestoneRule(
        levels=(
            ScoreLevel(
                score_range="6-7",
                condition=all_of(
                    any_of("simple_collection_method", "comprehensive_collection_method"),
                    "preference_summary",
                    "used_in_guideline_or_recommendations",
                ),
                description="提供收集方法、总结偏好，并用于指南开发或推荐形成。",
            ),
            ScoreLevel(
                score_range="5",
                condition=all_of("comprehensive_collection_method"),
                description="提供较深入的患者或公众偏好收集方法。",
            ),
            ScoreLevel(
                score_range="4",
                condition=all_of("simple_collection_method"),
                description="提供简单偏好收集方法。",
            ),
            ScoreLevel(
                score_range="2-3",
                condition=all_of("preference_considered"),
                description="仅声明考虑患者或公众偏好。",
            ),
            ScoreLevel(score_range="1", condition=all_of(), description="未提及。"),
        )
    ),
)
