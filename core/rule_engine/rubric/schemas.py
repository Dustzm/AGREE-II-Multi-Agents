"""Profile-specific schemas and score maps for rubric-unit classification."""
from __future__ import annotations

from typing import Any

from core.rule_engine.models import ItemSpec


PROFILE_SCORE_MAP: dict[str, dict[str, str]] = {
    "item18_application_factors": {
        "no_evidence": "1",
        "single_direction_factor": "2-3",
        "both_facilitator_and_barrier": "4",
        "both_plus_collection_or_impact": "5-6",
        "factors_plus_collection_and_impact": "7",
        "excluded_clinical_content": "1",
    },
    "item19_implementation_support": {
        "no_evidence": "1",
        "implementation_discussion_only": "2-4",
        "tool_like_but_not_eligible": "2-4",
        "one_eligible_tool_no_instruction": "2-3",
        "multiple_eligible_tools_no_instruction": "4-5",
        "implementation_section_plus_tool_no_instruction": "5-6",
        "eligible_tool_with_use_instruction": "6-7",
        "complete_tool_with_use_instruction": "7",
        "excluded_clinical_content": "1",
        "external_resource_only": "1",
    },
    "item21_monitoring_criteria": {
        "no_evidence": "1",
        "vague_monitoring": "2",
        "criterion_or_frequency_only": "3-4",
        "criterion_plus_frequency": "5-6",
        "criterion_plus_frequency_plus_method": "7",
        "excluded_clinical_episode": "1",
        "excluded_diagnostic_definition": "1",
        "excluded_device_operation": "1",
        "excluded_cited_study_outcome": "1",
    },
}


def score_range_for_band(profile: str, band: str) -> str:
    """Return the deterministic score range for a profile band."""
    try:
        return PROFILE_SCORE_MAP[profile][band]
    except KeyError as exc:
        raise ValueError(f"Unknown rubric band for {profile}: {band}") from exc


def decision_schema(spec: ItemSpec, candidate_count: int) -> dict[str, Any]:
    """Return a strict JSON schema for rubric-unit decisions."""
    rubric = spec.rubric_unit
    if rubric is None:
        raise ValueError(f"{spec.item_id} is missing RubricUnitSpec")
    score_ranges = sorted(set(PROFILE_SCORE_MAP.get(rubric.profile, {}).values()))
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "minItems": candidate_count,
                "maxItems": candidate_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "index",
                        "accepted",
                        "band",
                        "score_range",
                        "confidence",
                        "reason_code",
                        "reason",
                        "attributes",
                    ],
                    "properties": {
                        "index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": max(0, candidate_count - 1),
                        },
                        "accepted": {"type": "boolean"},
                        "band": {"type": "string", "enum": list(rubric.bands)},
                        "score_range": {"type": "string", "enum": score_ranges},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason_code": {"type": "string"},
                        "reason": {"type": "string"},
                        "attributes": _attributes_schema(rubric.profile),
                    },
                },
            }
        },
    }


def _attributes_schema(profile: str) -> dict[str, Any]:
    if profile == "item18_application_factors":
        return _object_schema(
            {
                "scope": _enum(
                    "current_guideline_application",
                    "clinical_care_or_patient_management",
                    "background_or_context",
                    "outside_scope",
                    "unclear",
                ),
                "factor_coverage": _enum("none", "single_direction", "both_directions"),
                "has_collection_method": {"type": "boolean"},
                "has_recommendation_or_development_impact": {"type": "boolean"},
                "exclusion_reason": {"type": "string"},
            }
        )
    if profile == "item19_implementation_support":
        return _object_schema(
            {
                "is_current_guideline_resource": {"type": "boolean"},
                "is_concrete_usable_aid": {"type": "boolean"},
                "eligible_tool_count": {"type": "integer", "minimum": 0, "maximum": 20},
                "has_implementation_section": {"type": "boolean"},
                "has_clear_tool_use_instruction": {"type": "boolean"},
                "exclusion_reason": {"type": "string"},
            }
        )
    if profile == "item21_monitoring_criteria":
        return _object_schema(
            {
                "is_current_guideline_criterion": {"type": "boolean"},
                "has_indicator_or_standard": {"type": "boolean"},
                "has_frequency_or_interval": {"type": "boolean"},
                "has_measurement_method": {"type": "boolean"},
                "measurement_function": _enum(
                    "application_audit_measure",
                    "ongoing_care_target",
                    "recommendation_trigger_standard",
                    "clinical_episode_instruction",
                    "diagnostic_definition",
                    "device_operation",
                    "cited_study_outcome",
                    "unclear",
                ),
                "exclusion_reason": {"type": "string"},
            }
        )
    return {
        "type": "object",
        "additionalProperties": {
            "type": ["string", "number", "boolean", "null"],
        },
    }


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties.keys()),
        "properties": properties,
    }


def _enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}
