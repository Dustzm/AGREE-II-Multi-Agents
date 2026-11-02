"""Base interfaces for rule-object verification."""
from __future__ import annotations

from typing import Protocol

from core.rule_engine.models import (
    EvidenceChunk,
    ItemSpec,
    RuleObject,
    VerificationBatchResult,
)


class RuleObjectVerifier(Protocol):
    """Protocol for validating extracted rule-object candidates."""

    def verify(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunks: list[EvidenceChunk],
        *,
        lang: str = "zh",
    ) -> VerificationBatchResult:
        """Return verified objects and per-candidate decisions."""
