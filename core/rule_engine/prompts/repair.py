"""Repair prompts that echo the invalid objects and validation issues back to the model."""
from __future__ import annotations

import json

from core.llm.failures import FailureContext, FailureKind
from core.rule_engine.models import EvidenceChunk, ItemSpec
from core.rule_engine.prompts.extraction import (
    render_chunks,
    render_object_type_lines,
    render_output_example,
)

_MAX_ECHO_OBJECTS = 6

_INTROS = {
    FailureKind.JSON_SYNTAX: (
        "The previous response was not valid JSON. Re-read the content and return the "
        "**complete** JSON object; no explanation, no markdown fences."
    ),
    FailureKind.SCHEMA_VIOLATION: (
        "The previous JSON did not match the required shape (missing \"objects\" or wrong "
        "field types). Return the complete result in the Required JSON Shape below."
    ),
}
_DEFAULT_INTRO = (
    "Some objects in the previous extraction failed validation (see Validation Problems). "
    "Fix only those problems and return the **complete** object list; do not add facts "
    "that are not in the content."
)


def render_object_repair_prompt(
    spec: ItemSpec,
    chunks: list[EvidenceChunk],
    failure: FailureContext,
    lang: str = "zh",
) -> str:
    """Render an actionable repair request.

    Unlike a blind retry this echoes the concrete invalid objects and the exact
    validation codes, so the model can fix rather than re-guess.
    """
    problem_lines = failure.render_for_prompt(limit=8)
    if not problem_lines:
        problem_lines = f"- {failure.kind.value}: {failure.error_text or 'output rejected by validation'}"

    bad_objects = list(failure.bad_objects[:_MAX_ECHO_OBJECTS])
    bad_block = (
        f"\n## Invalid Objects (from the previous response)\n```json\n"
        f"{json.dumps(bad_objects, ensure_ascii=False, indent=2)}\n```\n"
        if bad_objects
        else ""
    )

    return f"""# Extraction Repair

{_INTROS.get(failure.kind, _DEFAULT_INTRO)}

## Item
- item_id: {spec.item_id}
- title: {spec.title}

## Validation Problems
{problem_lines}
{bad_block}
## Allowed Object Types And Limits
{render_object_type_lines(spec)}

## Item-Specific Guidance
{spec.extraction_guidance or "None"}

## Content
```
{render_chunks(chunks)}
```

## Rules
1. Return exactly one JSON object with an "objects" array; no prose, no markdown fences.
2. Every object needs: type, value, chunk_id, quote, confidence, attributes.
3. "value" must be a contiguous substring of "quote" — copied verbatim from the source language.
4. If a whole sentence is not verbatim in the source, shorten "value" to the fragment that does
   appear verbatim. Never paraphrase, translate, reorder, merge fragments, or invent text.
5. "quote" must be copied verbatim from the named chunk; "chunk_id" must match its header.
6. Only return {{"objects": []}} when the content truly contains no allowed object.

## Required JSON Shape
```json
{render_output_example(spec)}
```
"""
