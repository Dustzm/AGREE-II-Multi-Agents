"""
Rule specification for AGREE-II item 21.
"""
from __future__ import annotations

from core.rule_engine.models import (
    ChunkRoutingSpec,
    GroupingSpec,
    ItemSpec,
    MonitoringCriteriaRule,
    ObjectVerificationSpec,
    RubricAppraiserSpec,
    RubricUnitSpec,
    RuleObjectSpec,
)


_MEASUREMENT_VERIFICATION = ObjectVerificationSpec(
    llm_required=True,
    guidance=(
        "Verify only whether the quote can serve as candidate evidence for item 21. Do not decide the final score. "
        "The final rubric judgment belongs to the group verifier: whether the evidence provides a measurable "
        "criterion for applying, continuing, or auditing the current guideline recommendations."
    ),
)


ITEM_21_SPEC = ItemSpec(
    item_id="item_21",
    title="The guideline presents monitoring and/or auditing criteria.",
    rule_profile="monitoring_criteria",
    object_specs={
        "indicator_or_standard": RuleObjectSpec(
            type="indicator_or_standard",
            role="component",
            description=(
                "源自指南关键推荐、可用于衡量推荐应用、持续使用或达标情况的指标、评价标准、目标、阈值、比例、"
                "审计标准、质量指标、绩效指标、临床指标或健康结局指标；也包括 responders/nonresponders、"
                "有无 hypoglycemia、症状状态、体重、基于 glucose monitoring 的滴定标准等非数值但可判定的监测标准。"
            ),
            max_objects=5,
            verification=_MEASUREMENT_VERIFICATION,
        ),
        "measurement_frequency_or_interval": RuleObjectSpec(
            type="measurement_frequency_or_interval",
            role="component",
            description=(
                "源自指南关键推荐、可用于衡量推荐应用或达标情况的测量频率、审计周期、时间窗口、筛查/检测/随访间隔，"
                "或症状持续时间等触发条件。"
            ),
            max_objects=5,
            verification=_MEASUREMENT_VERIFICATION,
        ),
        "measurement_method": RuleObjectSpec(
            type="measurement_method",
            role="component",
            description=(
                "说明如何测量、检测、收集、计算、审核或评价指标/标准的方法，包括 audit tool、数据收集方法、"
                "计算方式、检测方法、glucose monitoring、测量体重、询问低血糖/相关症状、记录或文档化监测结果。"
            ),
            max_objects=5,
            verification=_MEASUREMENT_VERIFICATION,
        ),
        "vague_monitoring_mention": RuleObjectSpec(
            type="vague_monitoring_mention",
            role="component",
            description="模糊提及监督、审计、监测、评价、质量改进或实施评估，但缺乏具体指标、频率或方法。",
            max_objects=5,
            verification=_MEASUREMENT_VERIFICATION,
        ),
    },
    extraction_guidance=(
        "AGREE-II item_21 评价指南是否提供源自关键推荐、用于衡量推荐应用或持续使用的监督/审计标准。"
        "可抽取过程测量、行为测量、临床或健康结局测量；例如 HbA1c 或血压目标、筛查比例、检测/筛查/随访频率、"
        "症状持续时间触发条件、responders/nonresponders、response status、有无 hypoglycemia、symptoms、weight、"
        "基于 glucose monitoring 的 titration 标准、每次 clinic visit 的测量/询问要求、AUDIT STANDARDS、audit tool、"
        "quality indicator、performance measure、proportion、collection of data、如何测量或如何收集数据。"
        "不要因为内容是临床推荐就直接拒绝；非数值但可判定的推荐衍生监测标准也可以抽取。只要它给出可衡量的指标、"
        "标准、频率/间隔或测量方法，就可作为 item_21 候选。不要把 HbA1c、血压、TIR、glucose range、"
        "screening/diagnostic threshold、SMBG/CGM、follow-up interval、symptom duration、FPG、OGTT、A1C、"
        "TIR、血压目标或筛查阈值一概拒绝；"
        "如果它们是推荐衍生的可测量标准，应作为候选。不要抽取引用研究 outcome、与推荐测量无关的纯诊断标准/纯诊断定义、普通治疗建议、"
        "泛泛的 close monitoring、普通患者日常监测、围手术期即时监测、术中/操作中即时监测、驾驶前检测、设备佩戴/扫描说明、"
        "指南自身更新周期。measurement_method 不能单独作为 3-4 分依据；每类只保留最直接、最代表性的证据。"
    ),
    verification_guidance=(
        "AGREE-II item 21 evaluates whether the current guideline provides criteria for measuring the application "
        "or ongoing use of its key recommendations. The rubric should separate criterion origin, measurement purpose, "
        "criterion kind, and whether the objects describe the same measurement criterion. Clinical or health outcome "
        "measures can count when they function as recommendation-derived monitoring/auditing criteria; ordinary "
        "diagnosis, acute management, device operation, cited-study outcomes, and guideline update timing do not."
    ),
    chunk_routing=ChunkRoutingSpec(
        heading_terms=(
            "audit standards",
            "audit",
            "quality indicators",
            "performance measures",
            "implementation",
            "monitoring",
            "measures",
            "指标",
            "审计",
            "质量评价",
        ),
        include_phrases=(
            "audit standards",
            "monitoring criteria",
            "auditing criteria",
            "audit criteria",
            "audit tool",
            "quality indicator",
            "quality indicators",
            "performance measure",
            "performance measures",
            "implementation monitoring",
            "measurable standard",
            "treatment target",
            "screening interval",
            "follow-up interval",
            "symptom duration",
            "responders",
            "nonresponders",
            "response status",
            "hypoglycemia",
            "hypoglycaemia",
            "glucose monitoring",
            "titration",
            "measure weight",
            "enquire about symptoms",
            "clinic visit",
            "each visit",
            "every clinic visit",
            "HbA1c",
            "TIR",
            "time in range",
            "glucose range",
            "fasting plasma glucose",
            "FPG",
            "OGTT",
            "screening threshold",
            "blood pressure",
            "fecal occult blood",
            "less than",
            "greater than",
            "collection of data",
            "proportion of people",
            "评价指标",
            "质量指标",
            "绩效指标",
            "审计标准",
        ),
        include_terms=(
            "audit",
            "indicator",
            "quality",
            "performance",
            "monitoring",
            "measure",
            "standard",
            "proportion",
            "percentage",
            "screening",
            "follow-up",
            "duration",
            "symptom",
            "hypoglycemia",
            "hypoglycaemia",
            "weight",
            "responder",
            "nonresponder",
            "response",
            "titration",
            "target",
            "threshold",
            "HbA1c",
            "TIR",
            "time in range",
            "glucose range",
            "FPG",
            "OGTT",
            "blood pressure",
            "mmHg",
            "审计",
            "指标",
            "质量",
            "绩效",
            "筛查",
            "检测",
            "随访",
            "症状",
            "监测",
        ),
        negative_terms=(
            "guideline update",
            "perioperative",
            "driving",
            "device is worn",
            "scan the sensor",
        ),
        neighbor_window=1,
        max_chunks=22,
        max_domain_chunks=10,
    ),
    grouping=GroupingSpec(
        group_profile="monitoring_criteria",
        anchor_object_types=("indicator_or_standard", "measurement_frequency_or_interval"),
        member_object_types=(
            "indicator_or_standard",
            "measurement_frequency_or_interval",
            "measurement_method",
            "vague_monitoring_mention",
        ),
        neighbor_window=1,
        same_section=True,
        max_groups=8,
        llm_required=True,
        guidance=(
            "Build local groups around measurable criteria and timing/frequency objects. Keep grouping local to the "
            "same evidence unit or adjacent chunks; the group verifier decides whether the objects describe the same "
            "monitoring or auditing criterion."
        ),
    ),
    rubric_appraiser=RubricAppraiserSpec(
        profile="item21_monitoring_criteria_appraiser",
        max_units=8,
        unit_char_limit=1400,
        decision_labels=(
            "no_relevant_evidence",
            "excluded_clinical_episode",
            "excluded_diagnostic_definition",
            "excluded_device_or_study_content",
            "vague_monitoring",
            "partial_monitoring_criteria",
            "clear_monitoring_criteria",
            "complete_monitoring_or_audit_criteria",
        ),
        guidance=(
            "AGREE-II item 21 evaluates whether the current guideline presents monitoring and/or auditing criteria "
            "derived from key recommendations to measure their application or ongoing use. Score 1 when not mentioned; "
            "2 for vague monitoring/auditing mention; 3-4 for indicators/evaluation standards or measurement "
            "frequency/intervals; 5-6 for indicators/evaluation standards plus frequency/intervals; and 7 when "
            "indicators/evaluation standards, frequency/intervals, and how to measure are all provided. Process, "
            "behavioural, clinical, or health outcome measures can count when they function as recommendation-derived "
            "criteria. Do not count acute treatment rechecks, device operation, cited-study outcomes, guideline update "
            "timing, or pure diagnostic definitions that do not function as application or ongoing-use criteria."
        ),
    ),
    rubric_unit=RubricUnitSpec(
        profile="item21_monitoring_criteria",
        bands=(
            "no_evidence",
            "vague_monitoring",
            "criterion_or_frequency_only",
            "criterion_plus_frequency",
            "criterion_plus_frequency_plus_method",
            "excluded_clinical_episode",
            "excluded_diagnostic_definition",
            "excluded_device_operation",
            "excluded_cited_study_outcome",
        ),
        max_units=10,
        guidance=(
            "Deprecated rubric-unit configuration retained only for unit-level builder/classifier tests. "
            "Active scoring uses the monitoring_criteria object/group rule engine."
        ),
    ),
    rule=MonitoringCriteriaRule(),
)
