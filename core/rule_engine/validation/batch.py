"""Batch validation: run the validator over a payload and apply the policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.llm.failures import ValidationIssue
from core.rule_engine.models import ExtractionBatchResult, ItemSpec, ReviewReason, RuleObject
from core.rule_engine.validation.validator import ObjectValidator


class BatchRejected(ValueError):
    """A policy rejected the whole batch (repairable, never silently accepted).

    Carries the per-object diagnosis so a repair request can echo it back.
    """

    def __init__(
        self,
        message: str,
        *,
        issues: tuple[ValidationIssue, ...] = (),
        dropped: tuple[dict[str, Any], ...] = (),
    ):
        super().__init__(message)
        self.issues = issues
        self.dropped = dropped


@dataclass(frozen=True)
class BatchOutcome:
    """Result of validating one extraction batch."""

    objects: list[RuleObject]
    issues: tuple[ValidationIssue, ...] = ()
    dropped: tuple[dict[str, Any], ...] = ()

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)

    def to_result(self) -> ExtractionBatchResult:
        """Convert to the extractor-facing result type."""
        return ExtractionBatchResult(
            objects=list(self.objects),
            review_reasons=issues_to_review_reasons(self.issues),
            dropped=tuple(self.dropped),
            issues=self.issues,
        )


def issues_to_review_reasons(issues: tuple[ValidationIssue, ...]) -> list[ReviewReason]:
    """Expose validation issues as human-readable review reasons."""
    reasons: list[ReviewReason] = []
    for issue in issues:
        message = issue.message or issue.code
        if issue.hint:
            message = f"{message}；建议：{issue.hint}"
        reasons.append(
            ReviewReason(
                code=issue.code,
                message=message,
                object_type=issue.object_type,
                object_value=issue.object_value,
                severity="warning",
            )
        )
    return reasons


def validate_batch(
    spec: ItemSpec,
    payload: list[Any],
    policy,
    *,
    sources: Mapping[str, str] | None = None,
) -> BatchOutcome:
    """Validate every object, then let the policy decide the batch outcome."""
    validator = ObjectValidator(spec, sources)
    valid: list[RuleObject] = []
    issues: list[ValidationIssue] = []
    dropped: list[dict[str, Any]] = []
    for index, raw in enumerate(payload):
        obj, obj_issues = validator.validate(raw, index)
        if obj is not None:
            valid.append(obj)
            continue
        issues.extend(obj_issues)
        if isinstance(raw, dict):
            dropped.append(raw)
    return policy.apply(valid, tuple(issues), tuple(dropped))
