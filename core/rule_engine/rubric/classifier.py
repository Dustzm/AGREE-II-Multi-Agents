"""LLM-backed classifier for evidence-unit rubric decisions."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from core.llm.json_output import TruncatedModelOutput
from core.llm.recovery import split_on_overflow, try_note_repair
from core.rule_engine.models import (
    EvidenceUnit,
    ItemSpec,
    ReviewReason,
    RubricDecision,
    RubricUnitResult,
    RuleLLMClient,
)
from core.rule_engine.rubric.prompts import (
    SYSTEM_PROMPT,
    render_rubric_classification_prompt,
    render_rubric_retry_prompt,
)
from core.rule_engine.rubric.schemas import decision_schema, score_range_for_band


class RubricClassifier:
    """Classify evidence units with profile-specific JSON schema."""

    def __init__(self, llm_client: RuleLLMClient, max_tokens: int = 2048):
        self.llm_client = llm_client
        self.max_tokens = max_tokens

    def classify(self, spec, units, *, lang="zh"):
        def merge(parts):
            decisions = [replace(d, unit_index=d.unit_index + offset) for offset, result in parts for d in result.decisions]
            return RubricUnitResult(
                candidate_units=[unit for _, result in parts for unit in result.candidate_units],
                accepted_units=[unit for _, result in parts for unit in result.accepted_units],
                decisions=decisions,
                best_decision=self._best_decision(spec, decisions),
                review_reasons=[reason for _, result in parts for reason in result.review_reasons],
            )

        return split_on_overflow(units, lambda batch: self._classify_once(spec, batch, lang=lang), merge,
                                 client=self.llm_client)

    def _classify_once(
        self,
        spec: ItemSpec,
        units: list[EvidenceUnit],
        *,
        lang: str = "zh",
    ) -> RubricUnitResult:
        """Return rubric decisions for candidate evidence units."""
        if not units:
            return RubricUnitResult(candidate_units=[], accepted_units=[], decisions=[])

        schema = decision_schema(spec, len(units))
        try:
            payload = self.llm_client.complete_json(
                stage=f"RubricUnitClassification:{spec.item_id}",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=render_rubric_classification_prompt(spec, units, lang=lang),
                max_tokens=self.max_tokens,
                json_schema=schema,
                schema_name="rubric_unit_classification",
                list_wrapper="decisions",
            )
            return self._build_result(spec, units, payload)
        except TruncatedModelOutput:
            raise
        except ValueError as exc:
            if not try_note_repair(self.llm_client, f"RubricUnitClassificationRetry:{spec.item_id}"):
                raise exc
            payload = self.llm_client.complete_json(
                stage=f"RubricUnitClassificationRetry:{spec.item_id}",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=render_rubric_retry_prompt(
                    spec,
                    units,
                    error_summary=_safe_error_summary(exc),
                    lang=lang,
                ),
                max_tokens=self.max_tokens,
                json_schema=schema,
                schema_name="rubric_unit_classification",
                list_wrapper="decisions",
            )
            return self._build_result(
                spec,
                units,
                payload,
                retry_used=True,
            )

    def _build_result(
        self,
        spec: ItemSpec,
        units: list[EvidenceUnit],
        payload: dict[str, Any],
        *,
        retry_used: bool = False,
    ) -> RubricUnitResult:
        decisions_payload = payload.get("decisions")
        if not isinstance(decisions_payload, list):
            raise ValueError("Rubric classifier JSON field 'decisions' must be a list")

        decision_by_index = self._parse_decisions(spec, decisions_payload)
        decisions: list[RubricDecision] = []
        accepted_units: list[EvidenceUnit] = []
        review_reasons: list[ReviewReason] = []

        if set(decision_by_index) - set(range(len(units))):
            raise ValueError("Unknown candidate decision index")

        for index, unit in enumerate(units):
            raw_decision = decision_by_index.get(index)
            if raw_decision is None:
                raise ValueError(f"Rubric classifier missing decision for candidate index {index}")
            decision = RubricDecision(
                unit_id=unit.unit_id,
                accepted=raw_decision["accepted"],
                band=raw_decision["band"],
                score_range=raw_decision["score_range"],
                confidence=raw_decision["confidence"],
                reason_code=raw_decision["reason_code"],
                reason=raw_decision["reason"],
                unit_index=index,
                attributes=raw_decision["attributes"],
            )
            decisions.append(decision)
            if decision.accepted:
                accepted_units.append(unit)

        if retry_used:
            review_reasons.insert(
                0,
                ReviewReason(
                    code="rubric_classification_json_retry",
                    message="Rubric classifier JSON/schema repair retry was used",
                    severity="warning",
                ),
            )

        return RubricUnitResult(
            candidate_units=units,
            accepted_units=accepted_units,
            decisions=decisions,
            best_decision=self._best_decision(spec, decisions),
            review_reasons=review_reasons,
        )

    def _parse_decisions(self, spec: ItemSpec, payload: list[Any]) -> dict[int, dict[str, Any]]:
        decisions: dict[int, dict[str, Any]] = {}
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("Rubric classifier decision must be an object")
            try:
                index = item.get("index")
                if type(index) is not int:
                    raise ValueError("Decision index must be an integer")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Rubric classifier decision has invalid index: {item.get('index')}") from exc
            if index in decisions:
                raise ValueError(f"Rubric classifier returned duplicate decision index: {index}")

            accepted = item.get("accepted")
            if not isinstance(accepted, bool):
                raise ValueError(f"Rubric classifier decision {index} accepted must be boolean")
            band = str(item.get("band", "")).strip()
            score_range = str(item.get("score_range", "")).strip()
            expected_score_range = score_range_for_band(spec.rubric_unit.profile, band)  # type: ignore[union-attr]
            if score_range != expected_score_range:
                raise ValueError(
                    f"Rubric classifier decision {index} score_range={score_range} "
                    f"does not match band {band} expected {expected_score_range}"
                )
            expected_accepted = score_range != "1"
            if accepted != expected_accepted:
                raise ValueError(
                    f"Rubric classifier decision {index} accepted={accepted} "
                    f"does not match band {band} score_range={score_range}; "
                    f"expected accepted={expected_accepted}"
                )
            confidence = self._parse_confidence(item.get("confidence", 1.0), index)
            reason_code = str(item.get("reason_code", "")).strip()
            reason = str(item.get("reason", "")).strip()
            attributes = item.get("attributes") or {}
            if not isinstance(attributes, dict):
                raise ValueError(f"Rubric classifier decision {index} attributes must be an object")
            decisions[index] = {
                "accepted": accepted,
                "band": band,
                "score_range": score_range,
                "confidence": confidence,
                "reason_code": reason_code,
                "reason": reason,
                "attributes": attributes,
            }
        return decisions

    def _best_decision(self, spec: ItemSpec, decisions: list[RubricDecision]) -> RubricDecision | None:
        scoreable = [
            decision
            for decision in decisions
            if decision.accepted and decision.score_range != "1"
        ]
        if not scoreable:
            return None
        return max(
            scoreable,
            key=lambda decision: (
                max(_parse_score_range(decision.score_range)),
                decision.confidence,
                -(decision.unit_index or 0),
            ),
        )

    def _parse_confidence(self, value: Any, index: int) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Rubric classifier decision {index} confidence is invalid: {value}") from exc
        if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
            raise ValueError(f"Rubric classifier decision {index} confidence must be between 0 and 1")
        return confidence


def _parse_score_range(score_range: str) -> list[int]:
    if "-" not in score_range:
        return [int(score_range)]
    start, end = score_range.split("-", 1)
    return list(range(int(start), int(end) + 1))


def _safe_error_summary(error: Exception) -> str:
    return str(error).replace("\n", " ")[:500]
