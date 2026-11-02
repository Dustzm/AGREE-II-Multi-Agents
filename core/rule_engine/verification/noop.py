"""No-op verifier used when no verification config is present."""
from __future__ import annotations

from core.rule_engine.models import (
    EvidenceChunk,
    ItemSpec,
    RuleObject,
    VerificationBatchResult,
    VerificationDecision,
)


class NoopRuleObjectVerifier:
    """Accept all extractor objects without extra semantic verification."""

    def verify(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunks: list[EvidenceChunk],
        *,
        lang: str = "zh",
    ) -> VerificationBatchResult:
        decisions = [
            VerificationDecision(
                object_type=obj.type,
                object_value=obj.value,
                chunk_id=obj.evidence.chunk_id,
                accepted=True,
                confidence=obj.confidence,
                source="skipped",
                reason="No verification config",
            )
            for obj in objects
        ]
        return VerificationBatchResult(
            accepted_objects=list(objects),
            decisions=decisions,
        )
