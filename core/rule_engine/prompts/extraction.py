"""
Prompt template for atomic rule-object extraction.
"""
from __future__ import annotations

import json
import math

from core.rule_engine.models import EvidenceChunk, ItemSpec


SYSTEM_PROMPT = "You are a strict AGREE-II atomic object extractor. Output JSON only."

# One response should stay well below the common 8192-token server output cap
# (longer generations also dominate latency). Extracted objects measure ~115 tokens
# on average and ~160 for long-quote items such as item_12; budget on the high side.
TARGET_OUTPUT_TOKENS = 6000
TOKENS_PER_OBJECT = 150


def request_object_limits(spec: ItemSpec) -> dict[str, int | None]:
    """Per-request type limits: the spec limits, scaled down when their sum cannot fit one response.

    The spec limits still apply per item after all batches are merged; this only
    keeps a single batch from asking for more objects than one response can hold.
    """
    limits = {obj_type: obj_spec.max_objects for obj_type, obj_spec in spec.object_specs.items()}
    capacity = TARGET_OUTPUT_TOKENS // TOKENS_PER_OBJECT
    total = sum(limit for limit in limits.values() if limit)
    if total <= capacity:
        return limits
    scale = capacity / total
    return {
        obj_type: limit if limit is None else max(1, math.floor(limit * scale + 0.5))
        for obj_type, limit in limits.items()
    }


def render_object_type_lines(spec: ItemSpec) -> str:
    """Allowed object types with their per-request limits, one bullet per type."""
    limits = request_object_limits(spec)
    lines = []
    for obj_spec in spec.object_specs.values():
        limit = limits[obj_spec.type]
        limit_text = (
            f"; at most {limit} representative object(s)"
            if limit is not None
            else "; do not enumerate duplicate facts"
        )
        lines.append(f"- {obj_spec.type} ({obj_spec.role}{limit_text}): {obj_spec.description}")
    return "\n".join(lines)


def render_chunks(chunks: list[EvidenceChunk]) -> str:
    """Source chunks headed by ``[chunk_id][label]`` so the model sees section roles."""
    parts = []
    for chunk in chunks:
        label = (chunk.metadata or {}).get("label")
        marker = f"[{chunk.chunk_id}][{label}]" if label else f"[{chunk.chunk_id}]"
        parts.append(f"{marker} ## {chunk.title}\n{chunk.content}")
    return "\n".join(parts)


def render_output_example(spec: ItemSpec) -> str:
    """Required JSON shape, using an allowed type of THIS item.

    A fixed health-topic example contradicts the allowed-type list for most items
    and invites ``unknown_object_type`` rejections.
    """
    example_type = next(iter(spec.object_specs), "object_type")
    example = {
        "objects": [
            {
                "type": example_type,
                "value": "<short span copied verbatim from the quote>",
                "chunk_id": "chunk_001",
                "quote": "<sentence copied verbatim from that chunk>",
                "confidence": 0.95,
                "attributes": {},
            }
        ]
    }
    return json.dumps(example, ensure_ascii=False, indent=2)


def render_extraction_prompt(spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> str:
    """Render an item-specific extraction prompt from an ItemSpec."""
    return f"""# AGREE-II Atomic Object Extraction

## Item
- item_id: {spec.item_id}
- title: {spec.title}

## Extraction Goal
Extract only atomic objects that are directly useful for this item's rule judgment.
Do not score the item. Do not summarize. Do not infer missing information.

## Allowed Object Types
{render_object_type_lines(spec)}

## Item-Specific Guidance
{spec.extraction_guidance or "None"}

## Content
```
{render_chunks(chunks)}
```

## Rules
1. Extract only objects explicitly supported by the content.
2. Every object must include the exact chunk_id and a direct quote from that chunk.
3. The "value" must be a short source-language span copied verbatim from the "quote" as a contiguous substring (character for character); the "quote" must itself be copied verbatim from that chunk. Preserve the source document language exactly.
4. Do not translate, normalize, paraphrase, or summarize the "value". If the exact span is hard to copy, shorten the value to the fragment that does appear verbatim instead of rewriting it.
5. Do not output objects without a quote.
6. If uncertain, still include the candidate only when there is a direct quote, and lower confidence.
7. The "type" must be one of the allowed object types.
8. Return an empty objects array if no allowed object appears.
9. Respect per-type object limits. If many similar objects appear, keep only the strongest direct evidence.
10. Before returning, verify every "value" appears inside its own "quote"; drop or shorten objects whose quote does not contain the value verbatim.

## Output JSON
Return exactly this JSON shape:
```json
{render_output_example(spec)}
```
"""
