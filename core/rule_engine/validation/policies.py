"""Batch policies: the only place that decides what happens to invalid objects."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from core.llm.failures import ValidationIssue
from core.rule_engine.models import RuleObject
from core.rule_engine.validation.batch import BatchOutcome, BatchRejected


@runtime_checkable
class BatchPolicy(Protocol):
    """Decide the batch outcome after per-object validation."""

    name: str

    def apply(
        self,
        valid: list[RuleObject],
        issues: tuple[ValidationIssue, ...],
        dropped: tuple[dict[str, Any], ...],
    ) -> BatchOutcome:
        """Return the batch outcome; raise only for contract-level decisions."""


class StrictAllOrNothing:
    """Legacy behaviour: one invalid object rejects the whole batch."""

    name = "strict"

    def apply(
        self,
        valid: list[RuleObject],
        issues: tuple[ValidationIssue, ...],
        dropped: tuple[dict[str, Any], ...],
    ) -> BatchOutcome:
        if issues:
            codes = sorted({issue.code for issue in issues})
            raise BatchRejected(f"Invalid extracted objects: {codes}", issues=issues, dropped=dropped)
        return BatchOutcome(objects=list(valid))


class KeepValidDropInvalid:
    """Default behaviour: keep valid objects, record invalid ones as review data."""

    name = "keep_valid"

    def apply(
        self,
        valid: list[RuleObject],
        issues: tuple[ValidationIssue, ...],
        dropped: tuple[dict[str, Any], ...],
    ) -> BatchOutcome:
        return BatchOutcome(objects=list(valid), issues=tuple(issues), dropped=tuple(dropped))


class Quarantine(KeepValidDropInvalid):
    """兼容别名：与 keep_valid 行为相同，非法对象已通过 issues/dropped 记录供报告展示。"""

    name = "quarantine"


def build_batch_policy(name: str | None) -> BatchPolicy:
    """Resolve a configured policy name, defaulting to keep_valid."""
    normalized = (name or "").strip().lower()
    if normalized in {"strict", "all_or_nothing", "all-or-nothing"}:
        return StrictAllOrNothing()
    if normalized in {"quarantine", "quarantined"}:
        return Quarantine()
    return KeepValidDropInvalid()
