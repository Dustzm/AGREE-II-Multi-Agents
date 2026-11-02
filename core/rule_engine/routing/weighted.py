"""Deterministic weighted chunk router."""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Sequence

from core.config import app_setting
from core.preprocessor.pojo import SemanticChunk
from core.preprocessor.section_titles import is_non_evidence_title
from core.rule_engine.models import (
    ChunkRoutingResult,
    EvidenceChunk,
    ItemSpec,
    ReviewReason,
    RoutedChunkInfo,
)
from core.utils.unicode_sanitizer import strip_surrogates

logger = logging.getLogger(__name__)


def _excluded_labels(routing=None) -> set[str]:
    """Labels excluded from routing (global setting plus the item-level override)."""
    raw = app_setting("rule_routing_excluded_labels", "Irrelevant")
    labels = {part.strip().casefold() for part in str(raw).split(",") if part.strip()}
    labels.update(part.strip().casefold() for part in getattr(routing, "excluded_labels", ()) if part.strip())
    return labels


def _label_value(chunk: SemanticChunk) -> str:
    label = getattr(chunk, "label", None)
    return str(getattr(label, "value", label) or "").casefold()


def _appendix_score_factor() -> float:
    """Down-weight appendix chunks in scored routing.

    Appendix chunks stay eligible (method/grading content can be the only place a
    rigour item finds its evidence), but they must not crowd body chunks out of a
    capped evidence window just because their titles repeat the item vocabulary.
    """
    return float(app_setting("rule_appendix_score_factor", 0.25))


def _appendix_chunk_cap(max_chunks: int) -> int:
    """Max appendix chunks per routed evidence window."""
    configured = int(app_setting("rule_appendix_max_chunks", 0) or 0)
    if configured > 0:
        return min(configured, max_chunks)
    return min(3, max(2, max_chunks // 4))


def _title_excluded(chunk: SemanticChunk) -> bool:
    """Reference/revision-history/index sections the classifier may have mislabeled.

    Appendix chunks are deliberately kept: DOMAIN_LABEL_MAPPING already decides
    which domains use them (2/3/5/6), and item skills such as item_10 point at the
    Method/Appendices sections explicitly.
    """
    return is_non_evidence_title(getattr(chunk, "title", ""))


class WeightedChunkRouter:
    """Select item-relevant chunks using deterministic weighted signals."""

    DOMAIN_SCORE = 2.0
    HEADING_SCORE = 6.0
    PHRASE_SCORE = 4.0
    TERM_SCORE = 1.0
    NEIGHBOR_SCORE = 1.5
    NEGATIVE_SCORE = -2.0

    def route(
        self,
        *,
        spec: ItemSpec,
        all_chunks: Sequence[SemanticChunk],
        domain_chunks: Sequence[SemanticChunk],
    ) -> ChunkRoutingResult:
        """Return routed chunks or domain chunks when routing is not configured."""
        routing = spec.chunk_routing
        excluded = _excluded_labels(routing)
        if excluded:
            def _keep(chunk: SemanticChunk) -> bool:
                return _label_value(chunk) not in excluded and not _title_excluded(chunk)

            kept_all = [chunk for chunk in all_chunks if _keep(chunk)]
            kept_domain = [chunk for chunk in domain_chunks if _keep(chunk)]
            if len(kept_all) != len(all_chunks):
                logger.info(
                    "路由排除附录/参考文献类 chunk: item=%s excluded=%s kept=%s/%s",
                    spec.item_id, sorted(excluded), len(kept_all), len(all_chunks),
                )
            all_chunks, domain_chunks = kept_all, kept_domain
        if routing is None:
            evidence_chunks = tuple(self._to_evidence_chunk(chunk) for chunk in domain_chunks)
            return ChunkRoutingResult(
                chunks=evidence_chunks,
                routed_chunks=tuple(
                    RoutedChunkInfo(
                        chunk_id=chunk.chunk_id,
                        score=self.DOMAIN_SCORE,
                        reasons=("domain_match",),
                    )
                    for chunk in evidence_chunks
                ),
                input_chunks=len(all_chunks),
                domain_chunks=len(domain_chunks),
                selected_chunks=len(evidence_chunks),
            )

        if routing.max_chunks < 1:
            return ChunkRoutingResult(
                chunks=(),
                input_chunks=len(all_chunks),
                domain_chunks=len(domain_chunks),
                selected_chunks=0,
                review_reasons=(
                    ReviewReason(
                        code="chunk_routing_empty_limit",
                        message=f"{spec.item_id} chunk routing max_chunks < 1",
                        severity="warning",
                    ),
                ),
            )

        chunk_list = list(all_chunks)
        domain_ids = {chunk.chunk_id for chunk in domain_chunks}
        scored: dict[str, float] = defaultdict(float)
        reasons: dict[str, list[str]] = defaultdict(list)
        chunk_by_id = {chunk.chunk_id: chunk for chunk in chunk_list}

        for chunk in chunk_list:
            if chunk.chunk_id in domain_ids:
                scored[chunk.chunk_id] += self.DOMAIN_SCORE
                reasons[chunk.chunk_id].append("domain_match")

            heading_text = self._normalize(" ".join(
                str(part or "")
                for part in (
                    chunk.title,
                    getattr(chunk, "section", ""),
                )
            ))
            content_text = self._normalize(chunk.content)
            combined_text = f"{heading_text}\n{content_text}"

            self._score_terms(
                chunk.chunk_id,
                heading_text,
                routing.heading_terms,
                self.HEADING_SCORE,
                "heading",
                scored,
                reasons,
            )
            self._score_terms(
                chunk.chunk_id,
                combined_text,
                routing.include_phrases,
                self.PHRASE_SCORE,
                "phrase",
                scored,
                reasons,
            )
            self._score_terms(
                chunk.chunk_id,
                combined_text,
                routing.include_terms,
                self.TERM_SCORE,
                "term",
                scored,
                reasons,
            )
            self._score_terms(
                chunk.chunk_id,
                combined_text,
                routing.negative_terms,
                self.NEGATIVE_SCORE,
                "negative",
                scored,
                reasons,
            )

        matched_indices = [
            index
            for index, chunk in enumerate(chunk_list)
            if self._has_item_signal(reasons.get(chunk.chunk_id, []))
        ]
        for index in matched_indices:
            for offset in range(1, routing.neighbor_window + 1):
                for neighbor_index in (index - offset, index + offset):
                    if neighbor_index < 0 or neighbor_index >= len(chunk_list):
                        continue
                    neighbor = chunk_list[neighbor_index]
                    if neighbor.chunk_id == chunk_list[index].chunk_id:
                        continue
                    scored[neighbor.chunk_id] += self.NEIGHBOR_SCORE
                    reasons[neighbor.chunk_id].append(
                        f"neighbor_of:{chunk_list[index].chunk_id}"
                    )

        appendix_factor = _appendix_score_factor()
        appendix_ids = {
            chunk.chunk_id for chunk in chunk_list if _label_value(chunk) == "appendix"
        }
        if appendix_factor != 1.0:
            for chunk_id in appendix_ids:
                scored[chunk_id] *= appendix_factor

        selected = self._select_chunks(
            chunk_list,
            scored,
            reasons,
            domain_ids,
            routing.max_chunks,
            routing.max_domain_chunks,
            appendix_ids=appendix_ids,
        )
        evidence_chunks = tuple(self._to_evidence_chunk(chunk_by_id[chunk_id]) for chunk_id in selected)
        routed_infos = tuple(
            RoutedChunkInfo(
                chunk_id=chunk_id,
                score=round(scored.get(chunk_id, 0.0), 3),
                reasons=tuple(dict.fromkeys(reasons.get(chunk_id, []))),
            )
            for chunk_id in selected
        )
        review_reasons: list[ReviewReason] = []
        if len(selected) < len([score for score in scored.values() if score > 0]):
            review_reasons.append(
                ReviewReason(
                    code="chunk_routing_limit_exceeded",
                    message=f"{spec.item_id} routed chunks exceeded max_chunks={routing.max_chunks}",
                    severity="info",
                )
            )

        return ChunkRoutingResult(
            chunks=evidence_chunks,
            routed_chunks=routed_infos,
            input_chunks=len(all_chunks),
            domain_chunks=len(domain_chunks),
            selected_chunks=len(evidence_chunks),
            review_reasons=tuple(review_reasons),
        )

    def _select_chunks(
        self,
        chunks: list[SemanticChunk],
        scored: dict[str, float],
        reasons: dict[str, list[str]],
        domain_ids: set[str],
        max_chunks: int,
        max_domain_chunks: int | None,
        *,
        appendix_ids: set[str] | None = None,
    ) -> list[str]:
        order = {chunk.chunk_id: index for index, chunk in enumerate(chunks)}
        positive_ids = [
            chunk.chunk_id
            for chunk in chunks
            if scored.get(chunk.chunk_id, 0.0) > 0.0 or chunk.chunk_id in domain_ids
        ]

        ranked = sorted(
            positive_ids,
            key=lambda chunk_id: (-scored.get(chunk_id, 0.0), order.get(chunk_id, 0)),
        )
        appendix_cap = _appendix_chunk_cap(max_chunks)
        selected: list[str] = []
        selected_domain_count = 0
        selected_appendix_count = 0
        for chunk_id in ranked:
            if len(selected) >= max_chunks:
                break
            if chunk_id in selected:
                continue
            if appendix_ids and chunk_id in appendix_ids:
                if selected_appendix_count >= appendix_cap:
                    continue
            if chunk_id in domain_ids and max_domain_chunks is not None:
                if selected_domain_count >= max_domain_chunks:
                    continue
                selected_domain_count += 1
            selected.append(chunk_id)
            if appendix_ids and chunk_id in appendix_ids:
                selected_appendix_count += 1
        return selected

    def _score_terms(
        self,
        chunk_id: str,
        text: str,
        terms: tuple[str, ...],
        weight: float,
        label: str,
        scored: dict[str, float],
        reasons: dict[str, list[str]],
    ) -> None:
        for term in terms:
            normalized_term = self._normalize(term)
            if not normalized_term:
                continue
            count = self._term_count(text, normalized_term)
            if count <= 0:
                continue
            scored[chunk_id] += weight * count
            reasons[chunk_id].append(f"{label}:{term}")

    def _has_item_signal(self, reasons: list[str]) -> bool:
        """Return whether a chunk matched item-specific routing signals."""
        return any(
            reason.startswith(("heading:", "phrase:", "term:"))
            for reason in reasons
        )

    def _term_count(self, text: str, term: str) -> int:
        if not term:
            return 0
        if " " in term:
            return text.count(term)
        if not term.isascii():
            return text.count(term)
        return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text))

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.casefold()).strip()

    def _to_evidence_chunk(self, chunk: SemanticChunk) -> EvidenceChunk:
        return EvidenceChunk(
            chunk_id=strip_surrogates(chunk.chunk_id),
            title=strip_surrogates(chunk.title),
            content=strip_surrogates(chunk.content),
            metadata={
                "label": getattr(chunk.label, "value", str(chunk.label)),
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "section": chunk.section,
            },
        )
