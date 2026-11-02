"""JSON schema for rule-object extraction responses (used for guided decoding)."""
from __future__ import annotations

from typing import Any

from core.rule_engine.models import ItemSpec


def build_extraction_schema(spec: ItemSpec) -> dict[str, Any]:
    """Return a complete response schema for one item's extraction call."""
    object_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": sorted(spec.object_specs)},
            "value": {"type": "string", "minLength": 1},
            "quote": {"type": "string", "minLength": 1},
            "chunk_id": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "attributes": {"type": "object", "additionalProperties": True},
        },
        "required": ["type", "value", "quote", "chunk_id", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"objects": {"type": "array", "items": object_schema}},
        "required": ["objects"],
        "additionalProperties": False,
    }
