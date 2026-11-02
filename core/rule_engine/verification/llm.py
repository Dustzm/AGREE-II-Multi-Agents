"""LLM-backed rule-object verifier."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from core.llm.json_output import TruncatedModelOutput
from core.llm.recovery import split_on_overflow, try_note_repair
from core.rule_engine.models import (
    EvidenceChunk,
    ItemSpec,
    ReviewReason,
    RuleLLMClient,
    RuleObject,
    VerificationBatchResult,
    VerificationDecision,
)
from core.rule_engine.prompts.verification import (
    SYSTEM_PROMPT,
    render_verification_prompt,
    render_verification_retry_prompt,
)


class LLMRuleObjectVerifier:
    """Verify semantically ambiguous candidate objects with a JSON LLM call."""

    def __init__(self, llm_client: RuleLLMClient, max_tokens: int = 12000):
        self.llm_client = llm_client
        self.max_tokens = max_tokens

    def verify(self, spec, objects, chunks, *, lang="zh", candidate_decisions=None):
        # 候选判定与对象一一对应，拆分时成对切分。
        pairs = list(zip(objects, candidate_decisions or [None] * len(objects)))

        def verify_pairs(batch):
            batch_objects = [obj for obj, _ in batch]
            batch_decisions = [decision for _, decision in batch] if candidate_decisions else None
            return self._verify_once(spec, batch_objects, chunks, lang=lang, candidate_decisions=batch_decisions)

        return split_on_overflow(pairs, verify_pairs, _merge_verifications, client=self.llm_client)

    def _verify_once(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunks: list[EvidenceChunk],
        *,
        lang: str = "zh",
        candidate_decisions: list[VerificationDecision] | None = None,
    ) -> VerificationBatchResult:
        if not objects:
            return VerificationBatchResult(accepted_objects=[], decisions=[])

        schema = _decision_schema(len(objects), include_attributes=False)
        try:
            payload = self.llm_client.complete_json(
                stage=f"RuleObjectVerification:{spec.item_id}",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=render_verification_prompt(
                    spec,
                    objects,
                    chunks,
                    lang=lang,
                    candidate_decisions=candidate_decisions,
                ),
                max_tokens=self.max_tokens,
                json_schema=schema,
                schema_name="rule_object_verification",
                list_wrapper="decisions",
            )
            return self._build_result(
                spec,
                objects,
                payload,
                candidate_decisions=candidate_decisions,
            )
        except TruncatedModelOutput:
            raise
        except ValueError as exc:
            if not try_note_repair(self.llm_client, f"RuleObjectVerificationRetry:{spec.item_id}"):
                raise exc
            payload = self.llm_client.complete_json(
                stage=f"RuleObjectVerificationRetry:{spec.item_id}",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=render_verification_retry_prompt(
                    spec,
                    objects,
                    chunks,
                    error_summary=_safe_error_summary(exc),
                    lang=lang,
                    candidate_decisions=candidate_decisions,
                ),
                max_tokens=self.max_tokens,
                json_schema=schema,
                schema_name="rule_object_verification",
                list_wrapper="decisions",
            )
            return self._build_result(
                spec,
                objects,
                payload,
                candidate_decisions=candidate_decisions,
                retry_used=True,
            )

    def _build_result(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        payload: dict[str, Any],
        *,
        candidate_decisions: list[VerificationDecision] | None,
        retry_used: bool = False,
    ) -> VerificationBatchResult:
        decisions_payload = payload.get("decisions")
        if not isinstance(decisions_payload, list):
            raise ValueError("Verifier JSON field 'decisions' must be a list")

        accepted_objects: list[RuleObject] = []
        decisions: list[VerificationDecision] = []
        review_reasons: list[ReviewReason] = []
        decision_by_index = self._parse_decisions(decisions_payload)

        if set(decision_by_index) - set(range(len(objects))):
            raise ValueError("Unknown candidate decision index")

        for index, obj in enumerate(objects):
            raw_decision = decision_by_index.get(index)
            if raw_decision is None:
                raise ValueError(f"Verifier missing decision for candidate index {index}")
            accepted = raw_decision["accepted"]
            confidence = raw_decision["confidence"]
            rejection_code = raw_decision["rejection_code"]
            reason = raw_decision["reason"]
            tags = (
                candidate_decisions[index].tags
                if candidate_decisions is not None and index < len(candidate_decisions)
                else ()
            )
            decisions.append(
                VerificationDecision(
                    object_type=obj.type,
                    object_value=obj.value,
                    chunk_id=obj.evidence.chunk_id,
                    accepted=accepted,
                    confidence=confidence,
                    source="llm",
                    rejection_code="" if accepted else rejection_code,
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
                        message=reason or rejection_code or "LLM verifier rejected candidate",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )

        if retry_used:
            review_reasons.insert(
                0,
                ReviewReason(
                    code="verification_json_retry",
                    message="Verifier JSON/schema repair retry was used",
                    severity="warning",
                ),
            )

        return VerificationBatchResult(
            accepted_objects=accepted_objects,
            decisions=decisions,
            review_reasons=review_reasons,
        )

    def _parse_decisions(self, decisions_payload: list[Any]) -> dict[int, dict[str, Any]]:
        decisions: dict[int, dict[str, Any]] = {}
        for item in decisions_payload:
            if not isinstance(item, dict):
                raise ValueError("Verifier decision must be an object")
            try:
                index = item.get("index")
                if type(index) is not int:
                    raise ValueError("Decision index must be an integer")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Verifier decision has invalid index: {item.get('index')}") from exc
            if index in decisions:
                raise ValueError(f"Verifier returned duplicate decision index: {index}")
            accepted = item.get("accepted")
            if not isinstance(accepted, bool):
                raise ValueError(f"Verifier decision {index} accepted must be boolean")
            confidence = self._parse_confidence(item.get("confidence", 1.0), index)
            rejection_code = str(item.get("rejection_code", "")).strip()
            reason = str(item.get("reason", "")).strip()
            if not accepted and not rejection_code:
                rejection_code = "llm_rejected"
            decisions[index] = {
                "accepted": accepted,
                "confidence": confidence,
                "rejection_code": rejection_code,
                "reason": reason,
            }
        return decisions

    def _parse_confidence(self, value: Any, index: int) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Verifier decision {index} confidence is invalid: {value}") from exc
        if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
            raise ValueError(f"Verifier decision {index} confidence must be between 0 and 1")
        return confidence


def _decision_schema(candidate_count: int, *, include_attributes: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "index": {"type": "integer", "minimum": 0, "maximum": max(0, candidate_count - 1)},
        "accepted": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rejection_code": {"type": "string"},
        "reason": {"type": "string"},
    }
    required = ["index", "accepted", "confidence", "rejection_code", "reason"]
    if include_attributes:
        properties["attributes"] = {
            "type": "object",
            "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
        }
        required.append("attributes")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "minItems": candidate_count,
                "maxItems": candidate_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": required,
                    "properties": properties,
                },
            }
        },
    }


def _safe_error_summary(error: Exception) -> str:
    return str(error).replace("\n", " ")[:500]


def _merge_verifications(parts) -> VerificationBatchResult:
    return VerificationBatchResult(
        accepted_objects=[obj for _, result in parts for obj in result.accepted_objects],
        decisions=[replace(d, object_index=d.object_index + offset) for offset, result in parts for d in result.decisions],
        review_reasons=[reason for _, result in parts for reason in result.review_reasons],
    )
