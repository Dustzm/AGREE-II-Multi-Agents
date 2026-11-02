"""Deterministic rule-object verifier."""
from __future__ import annotations

import re

from core.rule_engine.models import (
    EvidenceChunk,
    ItemSpec,
    ReviewReason,
    RuleObject,
    VerificationBatchResult,
    VerificationDecision,
)
from core.rule_engine.text_normalization import normalize_text_for_source_match


class DeterministicRuleObjectVerifier:
    """Validate extracted objects with configured lexical context rules."""

    def verify(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunks: list[EvidenceChunk],
        *,
        lang: str = "zh",
    ) -> VerificationBatchResult:
        chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        accepted_objects: list[RuleObject] = []
        decisions: list[VerificationDecision] = []
        review_reasons: list[ReviewReason] = []

        for index, obj in enumerate(objects):
            obj_spec = spec.object_specs.get(obj.type)
            verification = obj_spec.verification if obj_spec is not None else None
            if verification is None:
                accepted_objects.append(obj)
                decisions.append(
                    VerificationDecision(
                        object_type=obj.type,
                        object_value=obj.value,
                        chunk_id=obj.evidence.chunk_id,
                        accepted=True,
                        confidence=obj.confidence,
                        source="skipped",
                        reason="No verification config",
                        object_index=index,
                    )
                )
                continue

            chunk = chunk_by_id.get(obj.evidence.chunk_id)
            local_context_text = self._local_context(chunk, obj.evidence.quote)
            candidate_text = f"{obj.value}\n{obj.evidence.quote}"
            all_text = f"{candidate_text}\n{local_context_text}"
            hard_reject = not verification.llm_required

            accepted, code, reason, tags = self._check_candidate(
                candidate_text=candidate_text,
                context_text=local_context_text,
                all_text=all_text,
                required_any_terms=verification.required_any_terms,
                required_context_terms=verification.required_context_terms,
                hard_negative_terms=verification.hard_negative_terms,
                hard_negative_context_terms=verification.hard_negative_context_terms,
                negative_terms=verification.negative_terms,
                negative_context_terms=verification.negative_context_terms,
                hard_reject=hard_reject,
            )
            decisions.append(
                VerificationDecision(
                    object_type=obj.type,
                    object_value=obj.value,
                    chunk_id=obj.evidence.chunk_id,
                    accepted=accepted,
                    confidence=obj.confidence,
                    source="deterministic",
                    rejection_code=code,
                    reason=reason,
                    object_index=index,
                    tags=tags,
                )
            )
            if accepted:
                accepted_objects.append(obj)
            else:
                review_reasons.append(
                    ReviewReason(
                        code="verification_rejected",
                        message=reason,
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )

        return VerificationBatchResult(
            accepted_objects=accepted_objects,
            decisions=decisions,
            review_reasons=review_reasons,
        )

    def _check_candidate(
        self,
        *,
        candidate_text: str,
        context_text: str,
        all_text: str,
        required_any_terms: tuple[str, ...],
        required_context_terms: tuple[str, ...],
        hard_negative_terms: tuple[str, ...],
        hard_negative_context_terms: tuple[str, ...],
        negative_terms: tuple[str, ...],
        negative_context_terms: tuple[str, ...],
        hard_reject: bool,
    ) -> tuple[bool, str, str, tuple[str, ...]]:
        tags: list[str] = []
        matched_hard_negative = self._matched_term(candidate_text, hard_negative_terms)
        if matched_hard_negative:
            return (
                False,
                "hard_negative_term_matched",
                f"Candidate matched hard negative verification term: {matched_hard_negative}",
                (f"hard_negative_term_matched:{matched_hard_negative}",),
            )
        matched_hard_negative_context = self._matched_term(context_text, hard_negative_context_terms)
        if matched_hard_negative_context:
            return (
                False,
                "hard_negative_context_term_matched",
                f"Candidate context matched hard negative verification term: {matched_hard_negative_context}",
                (f"hard_negative_context_term_matched:{matched_hard_negative_context}",),
            )
        if required_any_terms and not self._has_any_term(all_text, required_any_terms):
            tags.append("required_any_term_missing")
            if hard_reject:
                return (
                    False,
                    "required_any_term_missing",
                    "Candidate did not contain any required verification term.",
                    tuple(tags),
                )
        if required_context_terms and not self._has_any_term(context_text, required_context_terms):
            tags.append("required_context_term_missing")
            if hard_reject:
                return (
                    False,
                    "required_context_term_missing",
                    "Candidate chunk context did not contain any required context term.",
                    tuple(tags),
                )
        matched_negative = self._matched_term(candidate_text, negative_terms)
        if matched_negative and hard_reject:
            return (
                False,
                "negative_term_matched",
                f"Candidate matched negative verification term: {matched_negative}",
                (f"negative_term_matched:{matched_negative}",),
            )
        matched_negative_context = self._matched_term(context_text, negative_context_terms)
        if matched_negative_context and hard_reject:
            return (
                False,
                "negative_context_term_matched",
                f"Candidate context matched negative verification term: {matched_negative_context}",
                (f"negative_context_term_matched:{matched_negative_context}",),
            )
        if matched_negative:
            tags.append(f"negative_term_matched:{matched_negative}")
        if matched_negative_context:
            tags.append(f"negative_context_term_matched:{matched_negative_context}")
        if tags:
            return (
                True,
                "",
                "Deterministic verification tagged candidate for LLM review: "
                f"{', '.join(tags)}",
                tuple(tags),
            )
        return True, "", "Deterministic verification passed.", ()

    def _local_context(self, chunk: EvidenceChunk | None, quote: str, window_chars: int = 360) -> str:
        """Return heading plus quote-neighbor context for lexical verification."""
        if chunk is None:
            return ""
        metadata_text = " ".join(str(value) for value in chunk.metadata.values() if value)
        content = chunk.content or ""
        quote_index = content.find(quote)
        if quote_index < 0:
            quote_index = content.casefold().find(quote.casefold())
        if quote_index < 0:
            local = quote
        else:
            start = max(0, quote_index - window_chars)
            end = min(len(content), quote_index + len(quote) + window_chars)
            local = content[start:end]
        return f"{chunk.title}\n{metadata_text}\n{local}"

    def _has_any_term(self, text: str, terms: tuple[str, ...]) -> bool:
        return self._matched_term(text, terms) is not None

    def _matched_term(self, text: str, terms: tuple[str, ...]) -> str | None:
        normalized_text = normalize_text_for_source_match(text)
        for term in terms:
            normalized_term = normalize_text_for_source_match(term)
            if not normalized_term:
                continue
            if self._contains_term(normalized_text, normalized_term):
                return term
        return None

    def _contains_term(self, text: str, term: str) -> bool:
        if " " in term:
            return term in text
        if not term.isascii():
            return term in text
        return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None
