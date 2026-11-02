"""Prompt template for group-level rule-object verification."""
from __future__ import annotations

import json

from core.rule_engine.models import EvidenceChunk, ItemSpec, RuleObjectGroup


SYSTEM_PROMPT = (
    "You are a strict AGREE-II evidence group verifier. "
    "Return JSON only. Do not score the item and do not create new evidence."
)


def render_group_verification_prompt(
    spec: ItemSpec,
    groups: list[RuleObjectGroup],
    chunks: list[EvidenceChunk],
    *,
    lang: str = "zh",
) -> str:
    """Render a yes/no semantic verification prompt for candidate object groups."""
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    grouping = spec.grouping
    compact = _use_compact_group_prompt(spec)
    grouping_guidance = grouping.guidance if grouping else ""
    item_standard = (
        spec.verification_guidance.strip()
        or spec.extraction_guidance.strip()
        or "Use the item title and object descriptions as the verification standard."
    )
    object_type_summaries = [
        {
            "type": object_type,
            "description": obj_spec.description,
        }
        for object_type, obj_spec in spec.object_specs.items()
    ]

    context_chunk_ids = sorted({
        chunk_id
        for group in groups
        for chunk_id in group.chunk_ids
        if chunk_id in chunk_by_id
    })
    context_chunks = [
        _chunk_payload(chunk_by_id[chunk_id], limit=700 if compact else 1800)
        for chunk_id in context_chunk_ids
    ]
    candidates = []
    for index, group in enumerate(groups):
        candidates.append(
            {
                "index": index,
                "group_id": group.group_id,
                "group_type": group.group_type,
                "relation_hints": list(group.relation_hints),
                "attributes": group.attributes,
                "chunk_ids": list(group.chunk_ids),
                "objects": [
                    {
                        "type": obj.type,
                        "value": _truncate(obj.value, 180 if compact else 1000),
                        "chunk_id": obj.evidence.chunk_id,
                        "quote": _truncate(obj.evidence.quote, 280 if compact else 1200),
                        "confidence": obj.confidence,
                    }
                    for obj in group.objects
                ],
            }
        )

    output_example = {
        "decisions": [
            {
                "index": 0,
                "accepted": True,
                "confidence": 0.9,
                "rejection_code": "",
                "reason": "The objects describe one coherent evidence group for this item.",
                "attributes": _attribute_example(spec),
            }
        ]
    }

    return f"""# AGREE-II Candidate Evidence Group Verification

## Item
- item_id: {spec.item_id}
- title: {spec.title}

## Item Standard
{item_standard}

## Grouping Standard
{grouping_guidance or "Accept a group only when the objects belong to one coherent evidence statement for this item."}

## Object Type Reference
```json
{json.dumps(object_type_summaries, ensure_ascii=False, indent=2)}
```

## Task
Decide whether each candidate group is a coherent group of related objects for this item.
Only verify the relationship among existing objects. Do not infer new objects. Do not change object type, value, quote, or chunk_id.

## Decision Rules
1. Accept only when the candidate objects describe the same disclosure, criterion, or local evidence unit under the Item Standard.
2. Reject groups that combine unrelated recommendations, distant evidence, cited-study details, unrelated meeting/workshop support, or generic background text.
{_profile_decision_rules(spec)}
3. Return one decision for every candidate index.
4. Output JSON only.

## Context Chunks
```json
{json.dumps(context_chunks, ensure_ascii=False, indent=2)}
```

## Candidates
```json
{json.dumps(candidates, ensure_ascii=False, indent=2)}
```

## Required JSON Shape
```json
{json.dumps(output_example, ensure_ascii=False, indent=2)}
```
"""


def render_group_verification_retry_prompt(
    spec: ItemSpec,
    groups: list[RuleObjectGroup],
    chunks: list[EvidenceChunk],
    *,
    error_summary: str,
    lang: str = "zh",
) -> str:
    """Render a short repair prompt for group verification JSON."""
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    grouping = spec.grouping
    compact = _use_compact_group_prompt(spec)
    item_standard = (
        spec.verification_guidance.strip()
        or spec.extraction_guidance.strip()
        or spec.title
    )
    group_standard = (
        grouping.guidance
        if grouping
        else "Accept only coherent object groups for this item."
    )
    context_chunk_ids = sorted({
        chunk_id
        for group in groups
        for chunk_id in group.chunk_ids
        if chunk_id in chunk_by_id
    })
    context_chunks = [
        _chunk_payload(chunk_by_id[chunk_id], limit=450 if compact else 1800)
        for chunk_id in context_chunk_ids
    ]
    candidates = []
    for index, group in enumerate(groups):
        candidates.append(
            {
                "index": index,
                "group_type": group.group_type,
                "relation_hints": list(group.relation_hints),
                "group_attributes": group.attributes,
                "chunk_ids": list(group.chunk_ids),
                "objects": [
                    {
                        "type": obj.type,
                        "value": _truncate(obj.value, 140 if compact else 220),
                        "quote": _truncate(obj.evidence.quote, 220 if compact else 420),
                        "chunk_id": obj.evidence.chunk_id,
                    }
                    for obj in group.objects
                ],
            }
        )
    return f"""# Retry: Candidate Evidence Group Verification

Previous error: {_truncate(error_summary, 300)}

Item standard:
{_truncate(item_standard, 900)}

Grouping standard:
{_truncate(group_standard, 900)}

Return valid JSON only. Return one decision for every candidate index.
Each decision must include: index, accepted, confidence, rejection_code, reason, attributes.
Use rejection_code="" when accepted=true.
{_profile_retry_rules(spec)}

Context chunks:
```json
{json.dumps(context_chunks, ensure_ascii=False, indent=2)}
```

Candidates:
```json
{json.dumps(candidates, ensure_ascii=False, indent=2)}
```
"""


def _profile_decision_rules(spec: ItemSpec) -> str:
    profile = spec.grouping.group_profile if spec.grouping else ""
    rules = {
        "monitoring_criteria": """2. For item21 monitoring groups, apply the Item Rubric Protocol and set attributes: criterion_origin, measurement_purpose, measurement_function, criterion_kind, has_measure_target, has_threshold_or_standard, has_timing_or_frequency, has_measurement_method, and same_measurement_criterion.
   - criterion_origin must be one of: current_guideline_recommendation, audit_or_quality_standard, external_or_cited, background_or_evidence, unclear.
   - measurement_purpose must be one of: measure_guideline_application, ongoing_use_or_care_target, diagnosis_or_initial_screening, acute_management, device_operation, study_outcome, guideline_update, unclear.
   - measurement_function must be one of: application_audit_measure, ongoing_care_target, recommendation_trigger_standard, clinical_episode_instruction, diagnostic_or_screening_definition, device_operation, cited_study_outcome, guideline_update, unclear.
   - criterion_kind must be one of: process_measure, behavioural_measure, clinical_or_health_outcome_measure, diagnostic_definition, treatment_action_only, not_applicable.
   - same_measurement_criterion must be true only when the objects describe one coherent measurement criterion or a tightly related criterion set derived from current guideline recommendations.
   - Classify acute treatment rechecks, perioperative/operative immediate monitoring, and 15-15 hypoglycaemia treatment workflows as clinical_episode_instruction, even when they include a target, timing, and measurement method.""",
        "implementation_tool": """2. For item19 implementation-support groups, apply the Item Rubric Protocol and set attributes: scope, rubric_role, support_form, implementation_connection, implementation_purpose, actionability, and same_support_item.
   - scope must be one of: current_guideline, external_only, unclear, unrelated.
   - rubric_role must be one of: concrete_tool, implementation_resource_package, implementation_support, clinical_content, not_applicable.
   - support_form must be one of: concrete_aid, implementation_resource_package, structured_program, protocol_process_ehr, implementation_table, clinical_practice_table, diagnostic_screening_risk_tool, drug_dose_table, evidence_table, general_discussion, external_resource, other.
   - implementation_connection must be one of: provided_or_recommended_for_this_guideline, embedded_or_linked_by_guideline, mentioned_only, external_or_cited, unclear.
   - implementation_purpose must be one of: put_guideline_recommendations_into_practice, clinical_management, background_or_evidence, unclear.
   - actionability must be one of: clear_tool_use_instruction, operational_elements, mentioned_only, none.
   - same_support_item must be true only when the objects describe one coherent implementation support/tool evidence unit.
   - concrete_tool requires a usable aid such as a checklist, leaflet, template, questionnaire, audit form, manual, implementation flowchart, or patient/clinician material.
   - implementation_resource_package requires a guideline-provided or recommended structured resource/program package with operational components; ordinary structured education, care models, EHR/process needs, and implementation discussion are implementation_support, not concrete_tool.
   - Ordinary clinical recommendations, drug/dose/practice tables, diagnostic/screening/risk tools, evidence tables, and clinical workflows are clinical_content unless clearly presented as current-guideline implementation resources.""",
        "application_factor": """2. For item18 application-factor groups, apply the Item Rubric Protocol and set attributes: scope, rubric_role, factor_direction, factor_specificity, identification_basis, application_impact, and same_application_evidence_unit.
   - scope must be one of: current_guideline_application, clinical_care_or_patient_management, background_or_context, outside_scope, unclear.
   - rubric_role must be one of: application_factor, clinical_content, resource_implication_only, not_applicable.
   - factor_direction must be one of: facilitator, barrier, mixed, absent.
   - factor_specificity must be one of: specific, general, absent.
   - identification_basis must be one of: explicit_collection_or_analysis, expert_judgment, implementation_reasoning, absent. Only explicit_collection_or_analysis indicates a real collection or identification method.
   - application_impact must be one of: affects_recommendations_or_strategy, affects_implementation_strategy_or_tools, affects_implementation, general_context, none. Only affects_recommendations_or_strategy or affects_implementation_strategy_or_tools indicates impact on recommendations, development, implementation strategy, or tool configuration.
   - same_application_evidence_unit must be true when the objects belong to one local implementation/applicability analysis unit, such as one paragraph, bullet list, table row, or local subsection.""",
        "funding_disclosure": """2. For item22 funding groups, set attributes when supported: status, scope, funder_type, and influence.
   - status = none|funded|commissioned|meeting_only|unknown.
   - influence may be funding_body_independence when editorial/content independence is directly linked to the funding body or funder.""",
        "recommendation_specificity": """2. For item15 recommendation-specificity groups, set attributes when supported: same_recommendation, is_current_guideline_recommendation, and specificity_components. Accept only when the objects describe one current-guideline recommendation. Reject groups that mix separate recommendations, evidence summaries, external guideline recommendations, background text, or plain diagnostic/eligibility criteria.""",
        "management_options": """2. For item16 management-options groups, set attributes when supported: same_issue, option_count, has_conditions, has_preference_or_pathway, and scope.
   - same_issue must be true only when all options belong to the same clinical issue, decision point, or management scenario.
   - option_count is the number of distinct management options in the group.
   - has_conditions indicates whether the group states when, for whom, or under what condition options should be used.
   - has_preference_or_pathway indicates first-line/second-line order, selection criteria, or decision pathway/branching logic.
   - scope must be one of: current_guideline, external_or_evidence_only, unclear.""",
        "health_question": """2. For item2 health-question groups, always set attributes: same_question, question_framing, scope, and picos_present. For rejected groups, use false, not_question, unclear, and an empty string when the evidence does not support a more specific value.
   - same_question must be true only when the objects describe one health question or one implicit PICO scope.
   - question_framing must be one of: background, foreground, implicit, not_question.
   - scope must be one of: current_guideline, external_or_cited, evidence_selection_only, unclear.
   - picos_present must be a comma-separated string using only P,I,C,O,S, such as \"P,I,C,O\"; do not return an array.
   Reject groups that are only target-population descriptions, recommendation-specificity evidence, evidence-selection criteria, cited-study PICO, external guideline questions, or unrelated background.""",
        "benefit_harm_balance": """2. For item11 benefit-harm groups, set attributes: scope, same_decision, has_recommendation_or_option, has_benefit, has_harm_or_risk, benefit_has_supporting_data, harm_has_supporting_data, has_explicit_balance, and balance_affects_recommendation.
   - scope must be one of: current_guideline, external_or_background, unclear.
   - same_decision must be true only when all objects refer to the same current-guideline recommendation, option, or management decision.
   - has_benefit and has_harm_or_risk describe whether the group contains benefit and harm/risk evidence for that same decision.
   - benefit_has_supporting_data and harm_has_supporting_data require data, studies, effect estimates, evidence summaries, or evidence grades supporting each side.
   - has_explicit_balance requires original text showing benefits and harms/risks were weighed, balanced, compared, or considered together in recommendation formulation.
   - balance_affects_recommendation requires the balance to influence the recommendation, option choice, target, strength, or management decision.
   - accepted=true only means the group describes the same current-guideline recommendation, option, management decision, or local evidence unit.
   - Accept incomplete groups when they still describe the same decision or local evidence unit; do not reject because benefit, harm/risk, supporting data, explicit balance, or recommendation impact is missing.
   - set missing components to false instead of rejecting incomplete same-decision groups.
   Reject only when the group mixes different recommendations/options/drugs, is external/background-only, or only contains background risk, adverse-event lists, contraindications, precautions, or unrelated text.""",
        "recommendation_evidence_link": """2. For item12 recommendation-evidence link groups, set attributes: scope, same_recommendation, has_recommendation, has_specific_supporting_evidence, link_type, mapping_strength, and same_evidence_unit.
   - scope must be one of: current_guideline, external_or_background, unclear.
   - same_recommendation must be true only when the evidence/citation/grade/rationale is tied to the same current-guideline recommendation or recommendation identifier.
   - Recommendation identifiers are formal recommendation numbers, statement IDs, recommendation IDs, or recommendation table row labels; citation numbers like [42] and evidence grades like Grade A or Level 1B are not recommendation identifiers.
   - link_type must be one of: citation_group, evidence_level_or_grade, rationale, evidence_table_reference, explicit_link_statement, generic_method, none.
   - mapping_strength must be one of: systematic, partial, generic, none.
   - systematic means the local evidence indicates recommendation-by-recommendation mapping, such as a recommendation table consistently carrying evidence levels/citations/rationale or an explicit system for linking each recommendation to evidence.
   - partial means this candidate links one or some recommendations to evidence, but does not establish systematic mapping for all recommendations.
   - same_evidence_unit must be true only when the recommendation and supporting evidence belong to the same row, recommendation block, paragraph, bullet, or adjacent rationale/evidence text.
   - Accept local recommendation-evidence links with mapping_strength=partial even when they do not prove systematic coverage.
   Reject standalone reference lists, standalone evidence tables, broad evidence discussion, or general methods text not mapped to a specific recommendation.""",
    }
    return rules.get(profile, "2. Set only attributes directly supported by the candidate evidence.")


def _profile_retry_rules(spec: ItemSpec) -> str:
    profile = spec.grouping.group_profile if spec.grouping else ""
    return _profile_decision_rules(spec).replace("\n2. ", "\n").strip()


def _chunk_payload(chunk: EvidenceChunk, *, limit: int = 1800) -> dict[str, str]:
    metadata_text = " ".join(str(value) for value in chunk.metadata.values() if value)
    return {
        "chunk_id": chunk.chunk_id,
        "title": chunk.title,
        "metadata": metadata_text,
        "content": _truncate(chunk.content, limit),
    }


def _use_compact_group_prompt(spec: ItemSpec) -> bool:
    profile = spec.grouping.group_profile if spec.grouping else ""
    return profile in {"implementation_tool", "benefit_harm_balance", "recommendation_evidence_link"}


def _attribute_example(spec: ItemSpec) -> dict[str, object]:
    profile = spec.grouping.group_profile if spec.grouping else ""
    if profile == "implementation_tool":
        return {
            "scope": "current_guideline",
            "rubric_role": "concrete_tool",
            "support_form": "concrete_aid",
            "implementation_connection": "provided_or_recommended_for_this_guideline",
            "implementation_purpose": "put_guideline_recommendations_into_practice",
            "actionability": "clear_tool_use_instruction",
            "same_support_item": True,
        }
    if profile == "monitoring_criteria":
        return {
            "criterion_origin": "current_guideline_recommendation",
            "measurement_purpose": "measure_guideline_application",
            "measurement_function": "application_audit_measure",
            "criterion_kind": "process_measure",
            "has_measure_target": True,
            "has_threshold_or_standard": True,
            "has_timing_or_frequency": True,
            "has_measurement_method": True,
            "same_measurement_criterion": True,
        }
    if profile == "application_factor":
        return {
            "scope": "current_guideline_application",
            "rubric_role": "application_factor",
            "factor_direction": "facilitator",
            "factor_specificity": "specific",
            "identification_basis": "absent",
            "application_impact": "none",
            "same_application_evidence_unit": True,
        }
    if profile == "benefit_harm_balance":
        return {
            "scope": "current_guideline",
            "same_decision": True,
            "has_recommendation_or_option": True,
            "has_benefit": True,
            "has_harm_or_risk": True,
            "benefit_has_supporting_data": True,
            "harm_has_supporting_data": True,
            "has_explicit_balance": True,
            "balance_affects_recommendation": True,
        }
    if profile == "recommendation_evidence_link":
        return {
            "scope": "current_guideline",
            "same_recommendation": True,
            "has_recommendation": True,
            "has_specific_supporting_evidence": True,
            "link_type": "citation_group",
            "mapping_strength": "partial",
            "same_evidence_unit": True,
        }
    return {}


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
