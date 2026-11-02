"""
LLM-backed rule-engine explainer.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from core.llm.recovery import try_note_repair
from core.rule_engine.explainers.models import RuleExplanationInput, RuleExplanationResult
from core.rule_engine.models import RuleLLMClient
from core.rule_engine.prompts.explanation import (
    SYSTEM_PROMPT,
    render_explanation_prompt,
    render_explanation_retry_prompt,
)

logger = logging.getLogger(__name__)


class LLMRuleExplainer:
    """Generate appraiser-compatible reasoning from rule-engine output."""

    def __init__(
        self,
        llm_client: RuleLLMClient,
        *,
        max_tokens: int = 12000,
        max_retries: int = 3,
    ):
        self.llm_client = llm_client
        self.max_tokens = max_tokens
        self.max_retries = max_retries

    def explain(self, payload: RuleExplanationInput, lang: str = "zh") -> RuleExplanationResult:
        """Generate and validate the final explanation."""
        user_prompt = render_explanation_prompt(payload, lang=lang)
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            if attempt > 1 and last_error is not None:
                user_prompt = render_explanation_retry_prompt(
                    payload,
                    previous_error=str(last_error),
                    lang=lang,
                )
            stage = f"RuleExplanation{'Retry' if attempt > 1 else ''}:{payload.item_id}:attempt:{attempt}"
            if attempt > 1 and not try_note_repair(self.llm_client, stage):
                break
            try:
                result = self.llm_client.complete_json(
                    stage=stage,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    max_tokens=self.max_tokens,
                )
                explanation = self._parse_result(payload, result)
                logger.info(
                    "规则引擎 explainer 成功: item=%s, score=%s, citations=%s, attempt=%s",
                    payload.item_id,
                    explanation.score,
                    self._citation_count(explanation.step3_reasoning),
                    attempt,
                )
                return explanation
            except ValueError as exc:
                last_error = exc
                logger.warning(
                    "规则引擎 explainer 输出校验失败: item=%s, attempt=%s/%s, error=%s",
                    payload.item_id,
                    attempt,
                    self.max_retries,
                    exc,
                )

        raise ValueError(f"规则引擎 explainer 失败: {last_error}") from last_error

    def _parse_result(
        self,
        payload: RuleExplanationInput,
        data: dict[str, Any],
    ) -> RuleExplanationResult:
        score = data.get("score")
        if isinstance(score, str):
            score = int(score.strip())
        if type(score) is not int or score not in payload.allowed_scores:
            raise ValueError(f"score must be one of allowed_scores {payload.allowed_scores}: {score}")

        step1_locate = str(data.get("step1_locate", "")).strip()
        step2_evidence = data.get("step2_evidence", [])
        step3_reasoning = data.get("step3_reasoning")
        if not step1_locate:
            raise ValueError("step1_locate is required")
        if not isinstance(step2_evidence, list) or not all(isinstance(item, str) for item in step2_evidence):
            raise ValueError("step2_evidence must be a list[str]")
        if not isinstance(step3_reasoning, dict):
            raise ValueError("step3_reasoning must be an object")

        self._validate_reasoning(payload, step3_reasoning)
        self._validate_internal_terms(payload, {
            "step1_locate": step1_locate,
            "step2_evidence": step2_evidence,
            "step3_reasoning": step3_reasoning,
        })
        return RuleExplanationResult(
            step1_locate=step1_locate,
            step2_evidence=step2_evidence,
            step3_reasoning=step3_reasoning,
            score=score,
        )

    def _validate_reasoning(self, payload: RuleExplanationInput, reasoning: dict[str, Any]) -> None:
        summary = reasoning.get("summary")
        reasons = reasoning.get("reasons")
        score_justification = reasoning.get("score_justification")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("step3_reasoning.summary is required")
        if not isinstance(score_justification, str) or not score_justification.strip():
            raise ValueError("step3_reasoning.score_justification is required")
        if not isinstance(reasons, list) or not reasons:
            raise ValueError("step3_reasoning.reasons must be a non-empty list")

        allowed_chunk_ids = {
            evidence.chunk_id
            for criterion in payload.matched_criteria
            for evidence in criterion.evidence
        }
        allow_empty_citations = payload.allowed_scores == [1]
        for index, reason in enumerate(reasons):
            if not isinstance(reason, dict):
                raise ValueError(f"reason {index} must be an object")
            if not str(reason.get("point", "")).strip():
                raise ValueError(f"reason {index}.point is required")
            if not str(reason.get("explanation", "")).strip():
                raise ValueError(f"reason {index}.explanation is required")
            evidence_chunks = reason.get("evidence_chunks")
            if not isinstance(evidence_chunks, list):
                raise ValueError(f"reason {index}.evidence_chunks must be a list")
            if not allow_empty_citations and not evidence_chunks:
                raise ValueError(f"reason {index}.evidence_chunks must be a non-empty list")
            invalid_chunks = [
                chunk_id
                for chunk_id in evidence_chunks
                if not isinstance(chunk_id, str) or chunk_id not in allowed_chunk_ids
            ]
            if invalid_chunks:
                raise ValueError(f"reason {index} contains invalid evidence_chunks: {invalid_chunks}")

    def _validate_internal_terms(self, payload: RuleExplanationInput, output: dict[str, Any]) -> None:
        leaked_terms = []
        leaked_identifiers = []
        string_values = list(self._iter_string_values(output))
        for term in payload.internal_terms:
            if not term or "_" not in term:
                continue
            if any(
                re.search(rf"(?<![A-Za-z0-9_]){re.escape(term)}(?![A-Za-z0-9_])", text, flags=re.IGNORECASE)
                for text in string_values
            ):
                leaked_terms.append(term)
        snake_case_pattern = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+(?![A-Za-z0-9_])")
        for text in string_values:
            leaked_identifiers.extend(
                identifier
                for identifier in snake_case_pattern.findall(text)
                if not self._is_allowed_code_like_identifier(identifier)
            )
        if leaked_terms:
            raise ValueError(f"explainer leaked internal terms: {sorted(set(leaked_terms))}")
        if leaked_identifiers:
            raise ValueError(f"explainer leaked code-like identifiers: {sorted(set(leaked_identifiers))}")

    def _iter_string_values(self, value: Any):
        """Yield only string values from nested LLM output, excluding JSON keys."""
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from self._iter_string_values(item)
        elif isinstance(value, list):
            for item in value:
                yield from self._iter_string_values(item)

    def _is_allowed_code_like_identifier(self, identifier: str) -> bool:
        """Return whether a code-like string is an allowed citation identifier."""
        return bool(re.fullmatch(r"chunk_\d+", identifier))

    def _citation_count(self, reasoning: dict[str, Any]) -> int:
        reasons = reasoning.get("reasons", [])
        if not isinstance(reasons, list):
            return 0
        return sum(
            len(reason.get("evidence_chunks", []))
            for reason in reasons
            if isinstance(reason, dict) and isinstance(reason.get("evidence_chunks"), list)
        )
