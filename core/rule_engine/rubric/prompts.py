"""Prompt templates for rubric-unit classification."""
from __future__ import annotations

import json

from core.rule_engine.models import EvidenceUnit, ItemSpec


SYSTEM_PROMPT = (
    "You are a strict AGREE-II rubric classifier. "
    "Classify only the provided evidence units. Return JSON only."
)


def render_rubric_classification_prompt(
    spec: ItemSpec,
    units: list[EvidenceUnit],
    *,
    lang: str = "zh",
) -> str:
    """Render the main rubric-unit classification prompt."""
    rubric = spec.rubric_unit
    if rubric is None:
        raise ValueError(f"{spec.item_id} is missing RubricUnitSpec")

    candidates = [_unit_payload(index, unit) for index, unit in enumerate(units)]
    output_example = {
        "decisions": [
            {
                "index": 0,
                "accepted": False,
                "band": rubric.bands[0] if rubric.bands else "no_evidence",
                "score_range": "1",
                "confidence": 0.9,
                "reason_code": "no_applicable_evidence",
                "reason": "The evidence unit does not support a scoreable rubric band.",
                "attributes": {},
            }
        ]
    }

    return f"""# AGREE-II Rubric Unit Classification

## Item
- item_id: {spec.item_id}
- title: {spec.title}

## Item Standard
{rubric.guidance}

## Profile Rubric
{_profile_rules(rubric.profile)}

## Task
Classify each evidence unit according to the item standard.
Judge only the current evidence unit. Do not combine separate units, infer missing evidence, create new citations, or change the quote.
Use the score_range dictated by the selected band.
Set accepted=false for no_evidence and exclusion bands.

## Candidates
```json
{json.dumps(candidates, ensure_ascii=False, indent=2)}
```

## Required JSON Shape
```json
{json.dumps(output_example, ensure_ascii=False, indent=2)}
```
"""


def render_rubric_retry_prompt(
    spec: ItemSpec,
    units: list[EvidenceUnit],
    *,
    error_summary: str,
    lang: str = "zh",
) -> str:
    """Render a short schema-repair prompt."""
    rubric = spec.rubric_unit
    if rubric is None:
        raise ValueError(f"{spec.item_id} is missing RubricUnitSpec")
    candidates = [_unit_payload(index, unit, compact=True) for index, unit in enumerate(units)]
    return f"""# Retry: AGREE-II Rubric Unit Classification

Previous error: {_truncate(error_summary, 300)}

Item standard:
{_truncate(rubric.guidance, 1000)}

Profile rubric:
{_truncate(_profile_rules(rubric.profile), 1200)}

Return valid JSON only. Return one decision for every candidate index.
Each decision must include: index, accepted, band, score_range, confidence, reason_code, reason, attributes.
Use the deterministic score_range for the selected band.

Candidates:
```json
{json.dumps(candidates, ensure_ascii=False, indent=2)}
```
"""


def _unit_payload(index: int, unit: EvidenceUnit, *, compact: bool = False) -> dict[str, object]:
    limit = 900 if compact else 1800
    return {
        "index": index,
        "unit_id": unit.unit_id,
        "chunk_id": unit.chunk_id,
        "title": unit.title,
        "unit_type": unit.unit_type,
        "quote": _truncate(unit.quote, limit),
        "content": _truncate(unit.content, limit),
        "metadata": unit.metadata,
    }


def _profile_rules(profile: str) -> str:
    rules = {
        "item18_application_factors": """Bands and deterministic score ranges:
- no_evidence -> 1
- single_direction_factor -> 2-3: mentions a facilitator OR a barrier affecting application of this guideline.
- both_facilitator_and_barrier -> 4: mentions both facilitator and barrier for the same local application evidence unit.
- both_plus_collection_or_impact -> 5-6: both directions plus either an explicit collection/identification method OR impact on recommendations/guideline development/implementation strategy.
- factors_plus_collection_and_impact -> 7: application factors plus explicit collection/identification method and impact on guideline development and/or recommendations.
- excluded_clinical_content -> 1: ordinary clinical care, clinical risk, treatment contraindication, disease burden, evidence rationale, or unrelated background.

Attributes: scope, factor_coverage, has_collection_method, has_recommendation_or_development_impact, exclusion_reason.
scope must be one of: current_guideline_application, clinical_care_or_patient_management, background_or_context, outside_scope, unclear.
Only explicit collection/analysis counts as collection method. Ordinary statements that something affects implementation do not count as impact unless the evidence links it to recommendations, guideline development, implementation strategy, or tools.""",
        "item19_implementation_support": """Bands and deterministic score ranges:
- no_evidence -> 1
- implementation_discussion_only -> 2-4: implementation advice/discussion exists, but no eligible implementation tool/resource is provided.
- tool_like_but_not_eligible -> 2-4: tool-like content exists but fails current-guideline eligibility.
- one_eligible_tool_no_instruction -> 2-3: exactly one eligible current-guideline tool/resource, no clear use instruction, no separate implementation section.
- multiple_eligible_tools_no_instruction -> 4-5: two or more eligible current-guideline tools/resources, no clear use instruction, no separate implementation section.
- implementation_section_plus_tool_no_instruction -> 5-6: separate implementation section/module plus at least one eligible tool/resource, but no clear use instruction.
- eligible_tool_with_use_instruction -> 6-7: this guideline provides/adopts/recommends an eligible concrete tool/resource and explains how to use it in practice.
- complete_tool_with_use_instruction -> 7: use only when this guideline itself clearly provides/adopts/recommends an eligible concrete tool/resource and gives complete, practical instructions for using it.
- excluded_clinical_content -> 1: ordinary clinical recommendation, clinical workflow, diagnostic/screening/risk tool, drug/dose table, evidence table, or clinical practice table.
- external_resource_only -> 1: external tool/resource is only cited, linked, or mentioned and is not adopted/recommended for implementing this guideline.

Attributes: is_current_guideline_resource, is_concrete_usable_aid, eligible_tool_count, has_implementation_section, has_clear_tool_use_instruction, exclusion_reason.
A concrete aid may be a checklist, leaflet, template, questionnaire, audit form, manual, implementation flowchart, patient/clinician material, or resource package. Clinical management instructions are not tool-use instructions.""",
        "item21_monitoring_criteria": """Bands and deterministic score ranges:
- no_evidence -> 1
- vague_monitoring -> 2: vague monitoring/auditing mention without clear criterion, frequency, or method.
- criterion_or_frequency_only -> 3-4: provides an indicator/evaluation standard OR measurement frequency/interval.
- criterion_plus_frequency -> 5-6: provides indicator/evaluation standard AND measurement frequency/interval.
- criterion_plus_frequency_plus_method -> 7: provides indicator/evaluation standard, measurement frequency/interval, and how to measure.
- excluded_clinical_episode -> 1: acute treatment recheck, 15-15 hypoglycaemia workflow, perioperative immediate monitoring, or similar clinical episode instruction.
- excluded_diagnostic_definition -> 1: pure diagnostic/screening definition not functioning as a guideline-application or ongoing-use criterion.
- excluded_device_operation -> 1
- excluded_cited_study_outcome -> 1

Attributes: is_current_guideline_criterion, has_indicator_or_standard, has_frequency_or_interval, has_measurement_method, measurement_function, exclusion_reason.
Clinical or health outcome measures count when they function as recommendation-derived criteria for measuring application or ongoing use of key recommendations.""",
    }
    return rules.get(profile, "Classify the evidence unit according to the item rubric.")


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
