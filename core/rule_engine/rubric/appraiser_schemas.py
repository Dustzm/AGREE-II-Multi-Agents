"""JSON schemas for portfolio-level rubric appraisal."""
from __future__ import annotations

from typing import Any

from core.rule_engine.models import ItemSpec


DEFAULT_DECISION_LABELS = (
    "no_relevant_evidence",
    "excluded_clinical_content",
    "partial_relevant_evidence",
    "moderate_rubric_support",
    "strong_rubric_support",
)


def rubric_appraiser_schema(spec: ItemSpec, *, minimal: bool = False) -> dict[str, Any]:
    """Return JSON schema for one item-level rubric appraisal decision."""
    rubric = spec.rubric_appraiser
    if rubric is None:
        raise ValueError(f"{spec.item_id} is missing RubricAppraiserSpec")
    labels = list(rubric.decision_labels or DEFAULT_DECISION_LABELS)
    required = ["score", "decision_label", "evidence"]
    properties: dict[str, Any] = {
        "score": {"type": "integer", "minimum": 1, "maximum": 7},
        "decision_label": {"type": "string", "enum": labels},
        "evidence": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["chunk_id", "quote"],
                "properties": {
                    "chunk_id": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
    }
    if not minimal:
        required.extend(["confidence", "rationale", "why_not_higher"])
        properties.update(
            {
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "rationale": {"type": "string"},
                "why_not_higher": {"type": "string"},
            }
        )

    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }
