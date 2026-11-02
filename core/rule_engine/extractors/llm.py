"""LLM-backed atomic rule-object extractor with a bounded repair ladder."""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from core.config import app_setting
from core.llm.context import ContextBudgetExceeded, split_chunk
from core.llm.failures import FailureContext, FailureKind, ValidationIssue, classify
from core.llm.json_output import TruncatedModelOutput
from core.llm.planner import plan_batches
from core.llm.recovery import split_on_overflow, try_note_repair
from core.llm.repair import RepairPlanner
from core.llm.structured_output import JSON_SCHEMA, build_structured_format
from core.llm.tokenizer import count_messages_tokens, count_text_tokens
from core.rule_engine.extractors.schema import build_extraction_schema
from core.rule_engine.models import (
    EvidenceChunk,
    ExtractionBatchResult,
    ItemSpec,
    ReviewReason,
    RuleLLMClient,
    RuleObject,
    chunk_source_texts,
)
from core.rule_engine.prompts.extraction import SYSTEM_PROMPT, render_extraction_prompt
from core.rule_engine.prompts.repair import render_object_repair_prompt
from core.rule_engine.text_normalization import normalize_text_for_source_match
from core.rule_engine.validation import (
    BatchOutcome,
    BatchRejected,
    build_batch_policy,
    reanchor_outcome,
    validate_batch,
)
from core.utils.cancellation import CancellationRequested
from core.utils.llm_errors import InvalidModelJSON

# Truncated extraction output is handled by splitting the batch (cheapest fix);
# the tree is bounded by this many splits instead of the shared truncation allowance.
_MAX_TRUNCATION_SPLITS = 24

logger = logging.getLogger(__name__)


class ExtractionPayloadShapeError(ValueError):
    """The model payload has no usable `objects` list; repairable but never silent."""


# Model-output failures a diagnosis-carrying resend can address.
_REPAIRABLE_ERRORS = (InvalidModelJSON, ExtractionPayloadShapeError, BatchRejected)


class LLMObjectExtractor:
    """Extract rule objects through an injected JSON LLM client."""

    def __init__(
        self,
        llm_client: RuleLLMClient,
        max_tokens: int = 12000,
        *,
        policy=None,
        planner: RepairPlanner | None = None,
        max_resends: int | None = None,
    ):
        self.llm_client = llm_client
        self.max_tokens = max_tokens
        self.policy = policy if policy is not None else build_batch_policy(
            app_setting("rule_extraction_policy", None)
        )
        if max_resends is None:
            max_resends = int(app_setting("rule_extraction_repair_attempts", 1))
        self.planner = planner or RepairPlanner(max_resends=max_resends)

    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        if not chunks:
            return ExtractionBatchResult(objects=[])
        batches = self._planned_batches(spec, list(chunks), lang)
        if len(batches) == 1:
            return self._extract_split(spec, batches[0], lang)
        parts = []
        offset = 0
        for batch in batches:
            parts.append((offset, self._extract_split(spec, batch, lang)))
            offset += len(batch)
        return _merge_extractions(parts)

    # ------------------------------------------------------------ batching

    def _planned_batches(
        self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str,
    ) -> list[list[EvidenceChunk]]:
        """Pre-split by the window plan; one batch leaves overflow handling to split_on_overflow."""
        budget = getattr(self.llm_client, "budget", None)
        if budget is None or not hasattr(budget, "plan_window"):
            return [chunks]
        try:
            plan = budget.plan_window(self.max_tokens)
        except Exception:  # noqa: BLE001 - an unusable window falls back to overflow splitting
            return [chunks]
        try:
            planned = plan_batches(
                chunks, lambda batch: self._estimate(spec, list(batch), lang), plan,
                split_unit=split_chunk, label=f"RuleObjectExtraction:{spec.item_id}",
            )
        except ContextBudgetExceeded as exc:  # the fixed template may be indivisible
            logger.warning(
                "抽取规划超出上下文预算，回退到溢出拆分路径: item=%s reason=%s", spec.item_id, exc,
            )
            return [chunks]
        if planned.batch_count <= 1:
            return [chunks]
        logger.info(
            "抽取按上下文窗口分批: item=%s batches=%s %s",
            spec.item_id, planned.batch_count, plan.describe(),
        )
        return [list(batch) for batch in planned.batches]

    def _extract_split(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        def ensure_template_fits():
            # If the fixed template cannot fit, splitting source text cannot help.
            budget = getattr(self.llm_client, "budget", None)
            if budget is not None:
                budget.check(_messages(spec, [], lang), self.max_tokens)

        return split_on_overflow(
            list(chunks), lambda batch: self._extract_once(spec, batch, lang), _merge_extractions,
            client=self.llm_client, split_single=split_chunk, before_split=ensure_template_fits,
            charge_overflow=False, max_splits=_MAX_TRUNCATION_SPLITS,
        )

    def _estimate(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str) -> int:
        """Estimate exactly what the request budget will check (prompt plus schema)."""
        messages = _messages(spec, chunks, lang)
        schema_format, _ = build_structured_format(
            JSON_SCHEMA, build_extraction_schema(spec), schema_name=_schema_name(spec),
        )
        estimate = getattr(getattr(self.llm_client, "budget", None), "estimate", None)
        if callable(estimate):
            return estimate(messages, schema_format)
        return count_messages_tokens(messages) + count_text_tokens(str(schema_format)) + 64

    # ---------------------------------------------------------- extraction

    def _extract_once(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        """Extract, validate, and repair only when validation finds problems."""
        stage = f"RuleObjectExtraction:{spec.item_id}"
        try:
            payload = self._complete(spec, stage, render_extraction_prompt(spec, chunks, lang=lang))
            outcome = self._validate_payload(spec, chunks, payload)
        except TruncatedModelOutput:
            raise
        except _REPAIRABLE_ERRORS as exc:
            return self._repair(
                spec, chunks, lang=lang, failure=_failure_from_error(spec, stage, chunks, exc), cause=exc,
            )

        if not outcome.has_issues:
            return self._finalize(outcome.to_result(), chunks)

        logger.warning(
            "抽取对象校验未通过，尝试定向修复: item=%s codes=%s dropped=%s",
            spec.item_id,
            sorted({issue.code for issue in outcome.issues}),
            len(outcome.dropped),
        )
        failure = _failure(
            spec, stage, chunks, kind=FailureKind.SEMANTIC_SPAN,
            issues=outcome.issues, bad_objects=outcome.dropped,
        )
        return self._repair(spec, chunks, lang=lang, failure=failure, base=outcome)

    def _complete(self, spec: ItemSpec, stage: str, user_prompt: str) -> dict[str, Any]:
        return self.llm_client.complete_json(
            stage=stage,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            max_tokens=self.max_tokens,
            json_schema=build_extraction_schema(spec),
            schema_name=_schema_name(spec),
        )

    def _validate_payload(
        self, spec: ItemSpec, chunks: list[EvidenceChunk], payload: dict[str, Any],
    ) -> BatchOutcome:
        """Shape check, per-object validation, then deterministic quote re-anchoring."""
        if "objects" not in payload:
            raise ExtractionPayloadShapeError("Extractor JSON field 'objects' is required")
        objects = payload.get("objects", [])
        if not isinstance(objects, list):
            raise ExtractionPayloadShapeError("Extractor JSON field 'objects' must be a list")
        outcome = validate_batch(spec, objects, self.policy, sources=chunk_source_texts(chunks))
        recovered, rewritten = reanchor_outcome(spec, chunks, objects, outcome, self.policy)
        if rewritten:
            logger.info(
                "对象引文已回锚: item=%s rewritten=%s remaining_dropped=%s",
                spec.item_id, rewritten, len(recovered.dropped),
            )
        return recovered

    # -------------------------------------------------------------- repair

    def _repair(
        self,
        spec: ItemSpec,
        chunks: list[EvidenceChunk],
        *,
        lang: str,
        failure: FailureContext,
        base: BatchOutcome | None = None,
        cause: Exception | None = None,
    ) -> ExtractionBatchResult:
        """Send diagnosis-carrying repair request(s), bounded by the planner attempts.

        Each resend echoes the problems of the previous response, and every validated
        object from any attempt is kept.
        """
        plan = self.planner.plan(failure)
        if not plan.should_resend:
            return self._keep_base(spec, chunks, base, cause, reason=f"未执行定向修复（{plan.reason}）")

        attempts = max(1, int(getattr(self.planner, "max_resends", 1) or 1))
        repairs: list[BatchOutcome] = []
        last_error = cause
        for attempt in range(1, attempts + 1):
            stage = f"RuleObjectExtractionRepair:{spec.item_id}"
            if attempt > 1:
                stage = f"{stage}#{attempt}"
            if not try_note_repair(self.llm_client, stage):
                break
            try:
                payload = self._complete(spec, stage, render_object_repair_prompt(spec, chunks, failure, lang=lang))
                outcome = self._validate_payload(spec, chunks, payload)
            except (TruncatedModelOutput, CancellationRequested):
                raise
            except _REPAIRABLE_ERRORS as exc:
                last_error = exc
                logger.warning(
                    "抽取修复第 %s/%s 次未产出可用结果: item=%s error=%s",
                    attempt, attempts, spec.item_id, _error_summary(exc),
                )
                failure = _next_failure(failure, attempt, error=exc)
                continue
            except Exception as exc:  # noqa: BLE001 - never lose already-validated objects
                if base is None:
                    raise
                last_error = exc
                break
            repairs.append(outcome)
            if not outcome.has_issues:
                break
            logger.warning(
                "抽取修复第 %s/%s 次仍有校验问题: item=%s codes=%s",
                attempt, attempts, spec.item_id, sorted({issue.code for issue in outcome.issues}),
            )
            failure = _next_failure(failure, attempt, outcome=outcome)

        if not repairs:
            return self._keep_base(spec, chunks, base, last_error, reason="抽取修复未产出可用结果")
        return self._finalize(_merge_outcomes(base, repairs).to_result(), chunks)

    def _keep_base(
        self,
        spec: ItemSpec,
        chunks: list[EvidenceChunk],
        base: BatchOutcome | None,
        cause: Exception | None,
        *,
        reason: str,
    ) -> ExtractionBatchResult:
        """Keep already-validated objects when the repair cannot run or fails."""
        if base is None:
            raise cause if cause is not None else ValueError(reason)
        logger.warning(
            "%s，保留已通过校验的对象: item=%s error=%s",
            reason,
            spec.item_id,
            type(cause).__name__ if cause else "-",
            exc_info=cause is not None,
        )
        return self._finalize(base.to_result(), chunks, repair_failed=True)

    @staticmethod
    def _finalize(
        result: ExtractionBatchResult,
        chunks: list[EvidenceChunk],
        *,
        repair_failed: bool = False,
    ) -> ExtractionBatchResult:
        """Add repair/empty-result diagnostics without changing the object contract."""
        extra: list[ReviewReason] = []
        if repair_failed:
            extra.append(ReviewReason(
                code="extraction_repair_failed",
                message="Repair did not produce a usable result; kept already-valid objects",
                severity="warning",
            ))
        if not result.objects and chunks:
            extra.append(ReviewReason(
                code="extraction_empty_result",
                message="No rule objects extracted from this batch",
                severity="warning",
            ))
        if not extra:
            return result
        return replace(result, review_reasons=[*extra, *result.review_reasons])


def _messages(spec: ItemSpec, chunks: list[EvidenceChunk], lang: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_extraction_prompt(spec, chunks, lang=lang)},
    ]


def _schema_name(spec: ItemSpec) -> str:
    return f"rule_objects_{spec.item_id}"


def _error_summary(error: Exception, limit: int = 240) -> str:
    text = str(error).replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def _failure(
    spec: ItemSpec,
    stage: str,
    chunks: list[EvidenceChunk],
    *,
    kind: FailureKind,
    error_text: str = "",
    issues: tuple[ValidationIssue, ...] = (),
    bad_objects: tuple[dict[str, Any], ...] = (),
) -> FailureContext:
    return FailureContext(
        kind=kind,
        stage=stage,
        item_id=spec.item_id,
        issues=issues,
        bad_objects=bad_objects,
        chunk_ids=tuple(chunk.chunk_id for chunk in chunks),
        error_text=error_text,
    )


def _error_kind(error: Exception) -> FailureKind:
    # A rejected batch failed object validation, not the JSON shape.
    return FailureKind.SEMANTIC_SPAN if isinstance(error, BatchRejected) else classify(error)


def _failure_from_error(
    spec: ItemSpec, stage: str, chunks: list[EvidenceChunk], error: Exception,
) -> FailureContext:
    return _failure(
        spec, stage, chunks,
        kind=_error_kind(error),
        error_text=_error_summary(error),
        issues=getattr(error, "issues", ()),
        bad_objects=getattr(error, "dropped", ()),
    )


def _next_failure(
    failure: FailureContext,
    attempt: int,
    *,
    error: Exception | None = None,
    outcome: BatchOutcome | None = None,
) -> FailureContext:
    """Diagnosis for the next resend: the latest problems replace the previous ones."""
    if outcome is not None:
        return replace(
            failure, kind=FailureKind.SEMANTIC_SPAN, attempt=attempt + 1,
            issues=outcome.issues, bad_objects=outcome.dropped, error_text="",
        )
    # A syntax/shape error carries no per-object diagnosis: keep the earlier one.
    return replace(
        failure,
        kind=_error_kind(error),
        attempt=attempt + 1,
        error_text=_error_summary(error),
        issues=getattr(error, "issues", ()) or failure.issues,
        bad_objects=getattr(error, "dropped", ()) or failure.bad_objects,
    )


def _object_key(obj: RuleObject) -> tuple[str, str, str, str]:
    return (
        obj.type,
        normalize_text_for_source_match(obj.value),
        obj.evidence.chunk_id,
        normalize_text_for_source_match(obj.evidence.quote),
    )


def _merge_outcomes(base: BatchOutcome | None, repairs: list[BatchOutcome]) -> BatchOutcome:
    """Union of every validated object (latest repair first); diagnostics from the last repair.

    Each repair response is a complete object list, so earlier issues were either
    fixed or are re-reported by the last repair; keeping them would double-count.
    """
    sources = [*reversed(repairs), *([base] if base is not None else [])]
    seen: set[tuple[str, str, str, str]] = set()
    objects: list[RuleObject] = []
    for outcome in sources:
        for obj in outcome.objects:
            key = _object_key(obj)
            if key not in seen:
                seen.add(key)
                objects.append(obj)
    last = repairs[-1]
    return BatchOutcome(objects=objects, issues=last.issues, dropped=last.dropped)


def _merge_extractions(parts) -> ExtractionBatchResult:
    return ExtractionBatchResult(
        objects=[obj for _, result in parts for obj in result.objects],
        review_reasons=[reason for _, result in parts for reason in result.review_reasons],
        dropped=tuple(obj for _, result in parts for obj in result.dropped),
        issues=tuple(issue for _, result in parts for issue in result.issues),
    )
