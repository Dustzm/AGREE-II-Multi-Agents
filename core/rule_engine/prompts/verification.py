"""Prompt template for LLM rule-object verification."""
from __future__ import annotations

import json

from core.rule_engine.models import EvidenceChunk, ItemSpec, RuleObject, VerificationDecision


SYSTEM_PROMPT = (
    "You are a strict AGREE-II evidence verifier. "
    "Return JSON only. Do not score the item and do not create new evidence."
)


def render_verification_prompt(
    spec: ItemSpec,
    objects: list[RuleObject],
    chunks: list[EvidenceChunk],
    *,
    lang: str = "zh",
    candidate_decisions: list[VerificationDecision] | None = None,
) -> str:
    """Render a yes/no semantic verification prompt for candidate objects."""
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    candidate_decisions = candidate_decisions or []
    object_type_summaries = []
    for object_type in sorted({obj.type for obj in objects}):
        obj_spec = spec.object_specs[object_type]
        verification = obj_spec.verification
        object_type_summaries.append(
            {
                "type": object_type,
                "description": obj_spec.description,
                "guidance": verification.guidance if verification else "",
            }
        )

    candidates = []
    for index, obj in enumerate(objects):
        obj_spec = spec.object_specs[obj.type]
        verification = obj_spec.verification
        chunk = chunk_by_id.get(obj.evidence.chunk_id)
        deterministic_decision = (
            candidate_decisions[index]
            if index < len(candidate_decisions)
            else None
        )
        candidates.append(
            {
                "index": index,
                "type": obj.type,
                "type_description": obj_spec.description,
                "object_guidance": verification.guidance if verification else "",
                "deterministic_tags": list(deterministic_decision.tags) if deterministic_decision else [],
                "deterministic_note": deterministic_decision.reason if deterministic_decision else "",
                "value": obj.value,
                "chunk_id": obj.evidence.chunk_id,
                "quote": obj.evidence.quote,
                "chunk_title": chunk.title if chunk else "",
                "chunk_context": _quote_context(chunk, obj.evidence.quote),
            }
        )

    output_example = {
        "decisions": [
            {
                "index": 0,
                "accepted": True,
                "confidence": 0.9,
                "rejection_code": "",
                "reason": "The quote and context satisfy this object type under the item standard.",
            }
        ]
    }

    item_standard = (
        spec.verification_guidance.strip()
        or spec.extraction_guidance.strip()
        or "Use the item title and object type descriptions as the verification standard."
    )
    grouped_item_note = ""
    if spec.grouping is not None and spec.grouping.llm_required:
        grouped_item_note = (
            "\nFor this group-aware item, object verification is only a candidate-evidence check. "
            "Accept a candidate when the quote and context can reasonably support its object type as "
            "material for the item rubric. Do not decide final rubric completeness, object relationships, "
            "or score level; those are handled by group verification and the rule engine.\n"
        )

    return f"""# AGREE-II Candidate Evidence Verification

## Item
- item_id: {spec.item_id}
- title: {spec.title}

## Item Standard
{item_standard}

## Object Type Guidance
```json
{json.dumps(object_type_summaries, ensure_ascii=False, indent=2)}
```

## Task
Decide whether each candidate object truly satisfies its object type for this item.
Only verify the candidate. Do not infer new objects. Do not change type, value, quote, or chunk_id.
{grouped_item_note}

## Decision Rules
1. Accept only when the quote and nearby chunk context clearly support the exact object type under the Item Standard and Object Type Guidance.
2. Reject candidates about the wrong source, cited studies, external documents, unrelated background facts, acknowledgments, affiliations, or ordinary clinical care when those do not satisfy this item's standard.
3. Deterministic tags are caution signals, not final decisions. Use them to inspect the context carefully, but decide semantically.
4. For accepted=false, use a concise rejection_code such as "wrong_context", "external_source", "clinical_management", "funding_only", "acknowledgment_only", or "insufficient_support".
5. Return one decision for every candidate index.
6. Output JSON only.

## Candidates
```json
{json.dumps(candidates, ensure_ascii=False, indent=2)}
```

## Required JSON Shape
```json
{json.dumps(output_example, ensure_ascii=False, indent=2)}
```
"""


def render_verification_retry_prompt(
    spec: ItemSpec,
    objects: list[RuleObject],
    chunks: list[EvidenceChunk],
    *,
    error_summary: str,
    lang: str = "zh",
    candidate_decisions: list[VerificationDecision] | None = None,
) -> str:
    """Render a short repair prompt for object verification JSON."""
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    candidate_decisions = candidate_decisions or []
    item_standard = (
        spec.verification_guidance.strip()
        or spec.extraction_guidance.strip()
        or spec.title
    )
    grouped_item_note = ""
    if spec.grouping is not None and spec.grouping.llm_required:
        grouped_item_note = (
            "For this group-aware item, only decide whether each object is plausible candidate evidence. "
            "Do not decide group relationship, rubric completeness, or score.\n"
        )
    candidates = []
    for index, obj in enumerate(objects):
        deterministic_decision = (
            candidate_decisions[index]
            if index < len(candidate_decisions)
            else None
        )
        chunk = chunk_by_id.get(obj.evidence.chunk_id)
        candidates.append(
            {
                "index": index,
                "type": obj.type,
                "type_description": spec.object_specs[obj.type].description,
                "tags": list(deterministic_decision.tags) if deterministic_decision else [],
                "value": _truncate(obj.value, 240),
                "quote": _truncate(obj.evidence.quote, 500),
                "chunk_id": obj.evidence.chunk_id,
                "chunk_title": chunk.title if chunk else "",
                "context": _quote_context(chunk, obj.evidence.quote, window_chars=260),
            }
        )
    return f"""# Retry: Candidate Evidence Verification

Previous error: {_truncate(error_summary, 300)}

Item standard:
{_truncate(item_standard, 900)}

{grouped_item_note}
Return valid JSON only. Return one decision for every candidate index.
Each decision must include: index, accepted, confidence, rejection_code, reason.
Use rejection_code="" when accepted=true.

Candidates:
```json
{json.dumps(candidates, ensure_ascii=False, indent=2)}
```
"""


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _quote_context(chunk: EvidenceChunk | None, quote: str, window_chars: int = 700) -> str:
    """Return heading plus quote-neighbor context for LLM verification."""
    if chunk is None:
        return ""

    metadata_text = " ".join(str(value) for value in chunk.metadata.values() if value)
    content = chunk.content or ""
    quote_index = content.find(quote)
    if quote_index < 0:
        quote_index = content.casefold().find(quote.casefold())
    if quote_index < 0:
        local = content[: window_chars * 2]
    else:
        start = max(0, quote_index - window_chars)
        end = min(len(content), quote_index + len(quote) + window_chars)
        local = content[start:end]
    return _truncate(
        "\n".join(part for part in (chunk.title, metadata_text, local) if part),
        1800,
    )
