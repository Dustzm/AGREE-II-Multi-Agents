"""Deterministic quote re-anchoring, run before any repair request.

Paraphrased or stitched quotes fail as ``value_not_found_in_quote`` or
``quote_not_found_in_chunk``. When the object's value is specific enough to locate
unambiguously in the cited chunk, the quote is replaced by the real source sentence
and the object is validated again.
"""
from __future__ import annotations

from typing import Any

from core.rule_engine.models import EvidenceChunk, ItemSpec, chunk_source_texts
from core.rule_engine.text_normalization import (
    contains_source_span,
    count_source_span,
    find_source_span,
    normalize_text_for_source_match,
)
from core.rule_engine.validation.batch import BatchOutcome, BatchRejected, validate_batch

# Shorter values ("患者", "risk") match too many sentences to pin an object on.
MIN_VALUE_CHARS = 4
# A re-anchored quote is one sentence or line, never a whole chunk.
MAX_QUOTE_CHARS = 600
REANCHORABLE_CODES = frozenset({"value_not_found_in_quote", "quote_not_found_in_chunk"})


def reanchor_quote(chunk_text: str, value: str) -> str | None:
    """Return the verbatim source sentence/line containing ``value``, or None.

    Only a value that is long enough and occurs exactly once in the chunk is
    re-anchored; anything ambiguous is left for the repair request.
    """
    if len(normalize_text_for_source_match(value).replace(" ", "")) < MIN_VALUE_CHARS:
        return None
    if count_source_span(chunk_text, value) != 1:
        return None
    return find_source_span(chunk_text, value, max_chars=MAX_QUOTE_CHARS)


def _reanchored(
    raw: dict[str, Any], contents: dict[str, str], sources: dict[str, str],
) -> dict[str, Any] | None:
    value = str(raw.get("value", "")).strip()
    chunk_id = str(raw.get("chunk_id", "")).strip()
    content = contents.get(chunk_id)
    if not value or content is None:
        return None
    quote = str(raw.get("quote", ""))
    if contains_source_span(quote, value) and contains_source_span(sources[chunk_id], quote):
        return None  # anchoring is fine; the object failed for another reason
    anchored = reanchor_quote(content, value)
    return None if anchored is None else {**raw, "quote": anchored}


def reanchor_outcome(
    spec: ItemSpec,
    chunks: list[EvidenceChunk],
    objects: list[Any],
    outcome: BatchOutcome,
    policy,
) -> tuple[BatchOutcome, int]:
    """Re-quote dropped objects and re-validate; returns the outcome and the rewrite count."""
    if not any(issue.code in REANCHORABLE_CODES for issue in outcome.issues):
        return outcome, 0
    contents = {chunk.chunk_id: chunk.content for chunk in chunks}
    sources = chunk_source_texts(chunks)
    dropped_ids = {id(raw) for raw in outcome.dropped}
    patched: list[Any] = []
    rewritten = 0
    for raw in objects:
        fixed = _reanchored(raw, contents, sources) if id(raw) in dropped_ids else None
        rewritten += fixed is not None
        patched.append(raw if fixed is None else fixed)
    if not rewritten:
        return outcome, 0
    try:
        return validate_batch(spec, patched, policy, sources=sources), rewritten
    except BatchRejected:
        return outcome, 0
