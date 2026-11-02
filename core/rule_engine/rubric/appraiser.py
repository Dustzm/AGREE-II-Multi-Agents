"""LLM-backed portfolio-level rubric appraiser."""
from __future__ import annotations

import math
from typing import Any

from core.llm.json_output import TruncatedModelOutput
from core.llm.recovery import try_note_repair
from core.rule_engine.models import (
    EvidenceChunk,
    EvidenceRef,
    EvidenceUnit,
    ItemSpec,
    ReviewReason,
    RubricAppraiserDecision,
    RubricAppraiserResult,
    RuleLLMClient,
    chunk_source_texts,
)
from core.rule_engine.rubric.appraiser_prompts import (
    SYSTEM_PROMPT,
    render_rubric_appraisal_prompt,
    render_rubric_appraisal_retry_prompt,
)
from core.rule_engine.rubric.appraiser_schemas import rubric_appraiser_schema
from core.rule_engine.text_normalization import contains_source_span


class RubricPortfolioAppraiser:
    """Appraise an item from a compact evidence portfolio."""

    def __init__(self, llm_client: RuleLLMClient, max_tokens: int = 1024):
        self.llm_client = llm_client
        self.max_tokens = max_tokens

    def appraise(
        self,
        spec: ItemSpec,
        units: list[EvidenceUnit],
        chunks: list[EvidenceChunk],
        *,
        lang: str = "zh",
    ) -> RubricAppraiserResult:
        """Return one item-level rubric appraisal decision."""
        if not units:
            return self._deterministic_result(
                units,
                score=1,
                code="no_rubric_portfolio_units",
                message="未生成可供 rubric appraiser 判断的证据组合。",
            )

        review_reasons: list[ReviewReason] = []
        try:
            payload = self.llm_client.complete_json(
                stage=f"RubricPortfolioAppraisal:{spec.item_id}",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=render_rubric_appraisal_prompt(spec, units, lang=lang),
                max_tokens=self.max_tokens,
                json_schema=rubric_appraiser_schema(spec),
                schema_name="rubric_portfolio_appraisal",
            )
            decision = self._parse_payload(spec, payload, minimal=False)
            decision, quote_reasons = self._validate_evidence(decision, chunks)
        except ValueError as exc:
            review_reasons.append(
                ReviewReason(
                    code="rubric_appraiser_json_retry",
                    message=f"Rubric appraiser JSON/schema retry was used: {_safe_error_summary(exc)}",
                    severity="warning",
                )
            )
            try:
                if not try_note_repair(self.llm_client, f"RubricPortfolioAppraisalRetry:{spec.item_id}"):
                    raise exc
                payload = self.llm_client.complete_json(
                    stage=f"RubricPortfolioAppraisalRetry:{spec.item_id}",
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=render_rubric_appraisal_retry_prompt(
                        spec,
                        units,
                        error_summary=_safe_error_summary(exc),
                        lang=lang,
                    ),
                    max_tokens=self.max_tokens * 2 if isinstance(exc, TruncatedModelOutput) else 512,
                    json_schema=rubric_appraiser_schema(spec, minimal=True),
                    schema_name="rubric_portfolio_appraisal_minimal",
                )
                decision = self._parse_payload(spec, payload, minimal=True)
                decision, quote_reasons = self._validate_evidence(decision, chunks)
            except ValueError as retry_exc:
                raise ValueError("Rubric appraiser failed after JSON/schema repair") from retry_exc

        review_reasons.extend(quote_reasons)
        return RubricAppraiserResult(
            candidate_units=units,
            decision=decision,
            review_reasons=review_reasons,
        )

    def _parse_payload(
        self,
        spec: ItemSpec,
        payload: dict[str, Any],
        *,
        minimal: bool,
    ) -> RubricAppraiserDecision:
        score = _parse_score(payload.get("score"))
        confidence = 0.0 if minimal else _parse_confidence(payload.get("confidence", 0.0))
        decision_label = str(payload.get("decision_label", "")).strip()
        rubric = spec.rubric_appraiser
        labels = set(rubric.decision_labels if rubric else ())
        if labels and decision_label not in labels:
            raise ValueError(f"invalid decision_label: {decision_label}")
        evidence_payload = payload.get("evidence")
        if not isinstance(evidence_payload, list):
            raise ValueError("rubric appraiser evidence must be a list")

        evidence: list[EvidenceRef] = []
        if len(evidence_payload) > 4:
            raise ValueError("Rubric appraisal evidence exceeds four citations")
        for item in evidence_payload:
            if not isinstance(item, dict):
                raise ValueError("rubric appraiser evidence item must be an object")
            chunk_id = str(item.get("chunk_id", "")).strip()
            quote = str(item.get("quote", "")).strip()
            if not chunk_id or not quote:
                raise ValueError("Rubric appraisal evidence requires chunk_id and quote")
            evidence.append(EvidenceRef(chunk_id=chunk_id, quote=quote))

        return RubricAppraiserDecision(
            score=score,
            confidence=confidence,
            decision_label=decision_label,
            rationale=str(payload.get("rationale", "")).strip() if not minimal else "",
            why_not_higher=str(payload.get("why_not_higher", "")).strip() if not minimal else "",
            evidence=evidence,
        )

    def _validate_evidence(
        self,
        decision: RubricAppraiserDecision,
        chunks: list[EvidenceChunk],
    ) -> tuple[RubricAppraiserDecision, list[ReviewReason]]:
        chunk_text_by_id = chunk_source_texts(chunks)
        valid_evidence: list[EvidenceRef] = []
        reasons: list[ReviewReason] = []
        for evidence in decision.evidence:
            chunk_text = chunk_text_by_id.get(evidence.chunk_id)
            if chunk_text is None:
                reasons.append(
                    ReviewReason(
                        code="rubric_unknown_quote_chunk",
                        message=f"Rubric appraiser 引用的 chunk_id 不在当前证据组合中: {evidence.chunk_id}",
                        severity="warning",
                    )
                )
                continue
            if not contains_source_span(chunk_text, evidence.quote):
                reasons.append(
                    ReviewReason(
                        code="rubric_quote_not_found_in_chunk",
                        message=f"Rubric appraiser quote 未在对应 chunk 原文中出现: {evidence.chunk_id}",
                        severity="warning",
                    )
                )
                continue
            valid_evidence.append(evidence)

        if reasons or (decision.score > 1 and not valid_evidence):
            raise ValueError("Rubric appraisal cites invalid or missing source evidence")

        return (
            RubricAppraiserDecision(
                score=decision.score,
                confidence=decision.confidence,
                decision_label=decision.decision_label,
                rationale=decision.rationale,
                why_not_higher=decision.why_not_higher,
                evidence=valid_evidence,
            ),
            reasons,
        )

    def _deterministic_result(
        self,
        units: list[EvidenceUnit],
        *,
        score: int,
        code: str,
        message: str,
        review_reasons: list[ReviewReason] | None = None,
        severity: str = "warning",
    ) -> RubricAppraiserResult:
        reasons = list(review_reasons or [])
        reasons.append(
            ReviewReason(
                code=code,
                message=message,
                severity=severity,  # type: ignore[arg-type]
            )
        )
        decision_label = "rubric_appraiser_failed" if code == "rubric_appraiser_json_failed" else "no_relevant_evidence"
        return RubricAppraiserResult(
            candidate_units=units,
            decision=RubricAppraiserDecision(
                score=score,
                confidence=0.0,
                decision_label=decision_label,
                rationale=message,
                why_not_higher=message,
                evidence=[],
            ),
            review_reasons=reasons,
        )


def _parse_score(value: Any) -> int:
    if type(value) is not int:
        raise ValueError("Rubric appraisal score must be an integer")
    score = value
    if score < 1 or score > 7:
        raise ValueError(f"rubric appraiser score must be 1-7: {score}")
    return score


def _parse_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"rubric appraiser confidence is invalid: {value}") from exc
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        raise ValueError(f"rubric appraiser confidence must be between 0 and 1: {confidence}")
    return confidence


def _safe_error_summary(error: Exception) -> str:
    return str(error).replace("\n", " ")[:500]
