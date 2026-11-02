"""Per-object validation: judge one extracted object, never decide batch outcome."""
from __future__ import annotations

import math
from typing import Any, Mapping

from core.llm.failures import ValidationIssue
from core.rule_engine.models import EvidenceRef, ItemSpec, RuleObject
from core.rule_engine.text_normalization import contains_source_span

HINTS = {
    "invalid_object_shape": "每个对象必须是 JSON 对象（{...}）",
    "unknown_object_type": "type 必须取自 Allowed Object Types",
    "missing_quote": "quote 不能为空，必须逐字复制原文片段",
    "missing_chunk_id": "chunk_id 必须是内容块头部 [chunk_id] 给出的 id",
    "missing_value": "value 不能为空，且必须是 quote 中确实出现的片段",
    "value_not_found_in_quote": (
        "value 必须是 quote 中的连续片段；若整句不是原文，请截短到原文中确实出现的那一段"
    ),
    "unknown_quote_chunk": "chunk_id 必须是本次内容中某个块头部 [chunk_id] 给出的 id",
    "quote_not_found_in_chunk": (
        "quote 必须从 chunk_id 指向的块中逐字复制；句子被表格或分页打断时，只引用未被打断的那一段"
    ),
    "invalid_confidence": "confidence 必须是 0 到 1 之间的数字",
    "invalid_attributes": "attributes 必须是 JSON 对象（{}），不能是数组或其他类型",
}


class ObjectValidator:
    """Validate raw extraction objects against one ItemSpec.

    With ``sources`` (``chunk_id -> source text``) the quote must also be found in
    the cited chunk, so unanchored quotes reach re-anchoring and repair instead of
    being dropped only after verification.
    """

    def __init__(self, spec: ItemSpec, sources: Mapping[str, str] | None = None):
        self.spec = spec
        self.sources = sources

    def validate(self, raw: Any, index: int) -> tuple[RuleObject | None, tuple[ValidationIssue, ...]]:
        def issue(code: str, field: str, message: str, **extra: Any) -> ValidationIssue:
            return ValidationIssue(
                code=code,
                object_index=index,
                field=field,
                message=message,
                hint=HINTS.get(code),
                **extra,
            )

        if not isinstance(raw, dict):
            return None, (issue("invalid_object_shape", "object", "Extractor object must be a JSON object"),)

        object_type = str(raw.get("type", "")).strip()
        value_text = self._safe_value(raw.get("value"))
        if object_type not in self.spec.object_specs:
            return None, (
                issue(
                    "unknown_object_type",
                    "type",
                    f"unknown object type: {object_type}",
                    object_type=object_type or None,
                    object_value=value_text,
                ),
            )
        object_spec = self.spec.object_specs[object_type]

        quote = str(raw.get("quote", "")).strip()
        if not quote:
            return None, (
                issue(
                    "missing_quote",
                    "quote",
                    f"object without quote: {object_type}",
                    object_type=object_type,
                    object_value=value_text,
                ),
            )

        chunk_id = str(raw.get("chunk_id", "")).strip()
        if not chunk_id:
            return None, (
                issue(
                    "missing_chunk_id",
                    "chunk_id",
                    f"object without chunk_id: {object_type}",
                    object_type=object_type,
                    object_value=value_text,
                ),
            )

        value = str(raw.get("value", "")).strip()
        if not value:
            return None, (
                issue("missing_value", "value", f"object without value: {object_type}", object_type=object_type),
            )

        if object_spec.require_value_in_quote and not contains_source_span(quote, value):
            return None, (
                issue(
                    "value_not_found_in_quote",
                    "value",
                    f"value is not a contiguous span of quote: {object_type}",
                    object_type=object_type,
                    object_value=value,
                    quote=quote,
                ),
            )

        if self.sources is not None:
            source = self.sources.get(chunk_id)
            if source is None:
                return None, (
                    issue(
                        "unknown_quote_chunk",
                        "chunk_id",
                        f"chunk_id is not in this batch: {chunk_id}",
                        object_type=object_type,
                        object_value=value,
                    ),
                )
            if not contains_source_span(source, quote):
                return None, (
                    issue(
                        "quote_not_found_in_chunk",
                        "quote",
                        f"quote is not verbatim in {chunk_id}: {object_type}",
                        object_type=object_type,
                        object_value=value,
                        quote=quote,
                    ),
                )

        confidence = raw.get("confidence", 1.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            return None, (
                issue(
                    "invalid_confidence",
                    "confidence",
                    f"invalid confidence: {confidence}",
                    object_type=object_type,
                    object_value=value,
                ),
            )
        if not math.isfinite(confidence_value) or confidence_value < 0 or confidence_value > 1:
            return None, (
                issue(
                    "invalid_confidence",
                    "confidence",
                    f"invalid confidence: {confidence}",
                    object_type=object_type,
                    object_value=value,
                ),
            )

        attributes = raw.get("attributes", {})
        if not isinstance(attributes, dict):
            return None, (
                issue(
                    "invalid_attributes",
                    "attributes",
                    f"attributes must be a dict: {object_type}",
                    object_type=object_type,
                    object_value=value,
                ),
            )

        return (
            RuleObject(
                type=object_type,
                value=value,
                evidence=EvidenceRef(chunk_id=chunk_id, quote=quote),
                confidence=confidence_value,
                attributes=attributes,
            ),
            (),
        )

    @staticmethod
    def _safe_value(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
