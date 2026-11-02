"""Repair planning: decide the cheapest next step for a classified failure."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.llm.failures import FailureContext, FailureKind


class RepairStep(str, Enum):
    """Ordered, cost-increasing repair steps."""

    LOCAL_REPAIR = "local_repair"
    RESEND_WITH_DIAGNOSIS = "resend_with_diagnosis"
    SCOPE_REDUCTION = "scope_reduction"


@dataclass(frozen=True)
class RepairPlan:
    """Steps to try for one failure, plus why the ladder stopped."""

    steps: tuple[RepairStep, ...] = ()
    reason: str = ""

    @property
    def should_resend(self) -> bool:
        return RepairStep.RESEND_WITH_DIAGNOSIS in self.steps


class RepairPlanner:
    """Map a FailureContext to a repair ladder (no I/O, no prompts)."""

    def __init__(self, max_resends: int = 1):
        self.max_resends = max(0, max_resends)

    def plan(self, failure: FailureContext) -> RepairPlan:
        kind = failure.kind
        if kind in {FailureKind.TRUNCATED, FailureKind.BUDGET_EXCEEDED}:
            return RepairPlan((), "handled_by_batch_split_or_budget")
        if kind is FailureKind.STAGE_CONTRACT:
            return RepairPlan((), "contract_failure_is_not_repairable")
        if self.max_resends <= 0:
            return RepairPlan((RepairStep.LOCAL_REPAIR,), "resend_disabled")
        return RepairPlan((RepairStep.RESEND_WITH_DIAGNOSIS,), "resend_with_validation_issues")
