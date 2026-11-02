"""Unified failure model shared by the LLM and rule-engine layers.

Every layer classifies its own errors with :func:`classify`, records the reason as
data instead of embedding it in exception strings, and lets the composition root
decide what to do with it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class FailureKind(str, Enum):
    """Single taxonomy for every recoverable model failure."""

    TRANSPORT = "transport"
    TRUNCATED = "truncated"
    JSON_SYNTAX = "json_syntax"
    SCHEMA_VIOLATION = "schema_violation"
    SEMANTIC_SPAN = "semantic_span"
    EMPTY_RESULT = "empty_result"
    BUDGET_EXCEEDED = "budget_exceeded"
    STAGE_CONTRACT = "stage_contract"


class StageContractError(ValueError):
    """A bug in our own data/prompt contract, never a model-capability limit.

    Only this explicit type is treated as fatal by the rule-engine failure
    policy; plain ValueError/KeyError coming out of model-output validation
    means "the model answered something we cannot use" and must degrade.
    """


@dataclass(frozen=True)
class ValidationIssue:
    """One machine-readable problem found in one extracted object."""

    code: str
    object_index: int
    field: str | None = None
    severity: Literal["warning", "error"] = "warning"
    message: str = ""
    object_type: str | None = None
    object_value: str | None = None
    quote: str | None = None
    hint: str | None = None


@dataclass(frozen=True)
class BudgetSnapshot:
    """Point-in-time view of the recovery allowance."""

    remaining: int = 0
    consumed: int = 0


@dataclass(frozen=True)
class FailureContext:
    """Everything the repair layer needs to explain a failure to the model."""

    kind: FailureKind
    stage: str
    item_id: str = ""
    attempt: int = 1
    issues: tuple[ValidationIssue, ...] = ()
    bad_objects: tuple[dict[str, Any], ...] = ()
    chunk_ids: tuple[str, ...] = ()
    error_text: str = ""
    budget: BudgetSnapshot | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def render_for_prompt(self, limit: int = 8) -> str:
        """Render a compact, actionable problem list for a repair prompt."""
        lines: list[str] = []
        for issue in self.issues[:limit]:
            label = f"object #{issue.object_index}"
            if issue.object_type:
                label += f" type={issue.object_type}"
            if issue.field:
                label += f" field={issue.field}"
            text = f"- {label}: {issue.code}"
            if issue.message:
                text += f" — {issue.message}"
            if issue.hint:
                text += f" (hint: {issue.hint})"
            lines.append(text)
        if not lines and self.error_text:
            lines.append(f"- {self.error_text}")
        return "\n".join(lines)


_BUDGET_NAMES = frozenset({
    "ContextBudgetExceeded",
    "ContextWindowNotConfigured",
    "ContextWindowExceededError",
})
_SCHEMA_NAMES = frozenset({
    "BatchRejected",
    "ExtractionPayloadShapeError",
    "ValidationError",
    "BatchOutcomeRejected",
})
# An exception of these types is a plain ValueError-family model-output error:
# the payload is well-formed enough to reach validation but violates our contract.
_MODEL_OUTPUT_VALUE_ERRORS = frozenset({"ValueError"})
# These only come from our own data structures being wrong (missing key, wrong
# type, missing attribute, bad index) — a code/prompt contract bug, not a model
# limitation, so they must stay visible instead of being degraded silently.
_PROGRAMMING_ERROR_NAMES = frozenset({"KeyError", "TypeError", "AttributeError", "IndexError"})


def _mro_names(error: BaseException) -> set[str]:
    """Names of the exception's full MRO, so subclasses classify like their parents."""
    return {cls.__name__ for cls in type(error).__mro__}


def _classify_names(names: set[str], text: str) -> FailureKind:
    if "StageContractError" in names:
        return FailureKind.STAGE_CONTRACT
    if "TruncatedModelOutput" in names:
        return FailureKind.TRUNCATED
    if names & _BUDGET_NAMES or "allowance exhausted" in text or "context window" in text:
        return FailureKind.BUDGET_EXCEEDED
    if "InvalidModelJSON" in names:
        return FailureKind.JSON_SYNTAX
    if names & _SCHEMA_NAMES or names & _MODEL_OUTPUT_VALUE_ERRORS:
        return FailureKind.SCHEMA_VIOLATION
    if names & _PROGRAMMING_ERROR_NAMES:
        return FailureKind.STAGE_CONTRACT
    # Network/timeout/rate-limit and anything unrecognised: environment problems.
    return FailureKind.TRANSPORT


def classify(error: BaseException) -> FailureKind:
    """Map an exception to the shared failure taxonomy without importing layers.

    Matching walks the exception MRO instead of comparing the exact class name:
    a subclass of ``InvalidModelJSON`` or of a context/budget error must map like
    its parent, and a plain ``ValueError`` from model-output validation is a
    schema violation (degradable), not a stage-contract bug. Cancellation is not a
    failure and is re-raised.
    """
    names = _mro_names(error)
    if "CancellationRequested" in names:
        raise error
    return _classify_names(names, str(error).lower())


def reported_kind(error: BaseException | None, type_name: str = "", message: str = "") -> str:
    """Failure kind for reports; never raises, cancellation is reported as ``cancelled``.

    Pass the exception when it is still available (its MRO is classified); a recorded
    failure that only kept its type name and message is classified from those.
    """
    names = _mro_names(error) if error is not None else {type_name}
    text = (str(error) if error is not None else message).lower()
    if "CancellationRequested" in names or "cancelled" in text:
        return "cancelled"
    return _classify_names(names, text).value
