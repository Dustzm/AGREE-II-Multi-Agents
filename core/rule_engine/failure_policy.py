"""Rule-engine failure policy: one place decides fatal vs degraded fallback."""
from __future__ import annotations

from dataclasses import dataclass

from core.llm.failures import FailureKind, classify

DEFAULT_STRICT_ITEMS = frozenset({"item_11", "item_12", "item_18", "item_19", "item_21"})

# Only an explicit code-contract bug is fatal; anything produced by the model
# (JSON, schema, span, truncation, transport, budget) must degrade to the LLM
# appraiser instead of failing the item.
_FATAL_KINDS = frozenset({FailureKind.STAGE_CONTRACT})


@dataclass(frozen=True)
class FailureDecision:
    """What the composition root should do with one rule-engine failure."""

    kind: FailureKind
    fatal: bool
    degraded: bool = False
    reason: str = ""


class RuleFailurePolicy:
    """Decide fatal vs fallback for one item; no logging, no I/O."""

    def __init__(
        self,
        *,
        fallback_enabled: bool = True,
        mode: str = "degraded",
        strict_items: frozenset[str] | set[str] = DEFAULT_STRICT_ITEMS,
    ):
        self.fallback_enabled = fallback_enabled
        self.mode = (mode or "degraded").strip().lower()
        self.strict_items = frozenset(strict_items)

    @classmethod
    def from_settings(cls, settings) -> "RuleFailurePolicy":
        app = settings.app
        raw_items = getattr(app, "rule_strict_items", None)
        strict_items = (
            DEFAULT_STRICT_ITEMS
            if raw_items is None
            else frozenset(part.strip() for part in str(raw_items).split(",") if part.strip())
        )
        return cls(
            fallback_enabled=getattr(app, "rule_engine_fallback_on_error", True),
            mode=getattr(app, "rule_fallback_policy", "degraded"),
            strict_items=strict_items,
        )

    def decide(self, item_id: str, error: BaseException) -> FailureDecision:
        kind = classify(error)
        if not self.fallback_enabled:
            return FailureDecision(kind=kind, fatal=True, reason="fallback_disabled")
        if kind in _FATAL_KINDS:
            return FailureDecision(kind=kind, fatal=True, reason=f"contract_failure:{kind.value}")
        if self.mode == "legacy" and item_id in self.strict_items:
            return FailureDecision(kind=kind, fatal=True, reason="strict_item_legacy_mode")
        return FailureDecision(kind=kind, fatal=False, degraded=True, reason=kind.value)
