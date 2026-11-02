"""No-op group verifier."""
from __future__ import annotations

from core.rule_engine.models import (
    EvidenceChunk,
    GroupVerificationDecision,
    ItemSpec,
    RuleGroupingResult,
    RuleObjectGroup,
)


class NoopRuleObjectGroupVerifier:
    """Accept all candidate groups when semantic group verification is disabled."""

    def verify(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        chunks: list[EvidenceChunk] | None = None,
        *,
        lang: str = "zh",
    ) -> RuleGroupingResult:
        decisions = [
            GroupVerificationDecision(
                group_id=group.group_id,
                group_type=group.group_type,
                accepted=True,
                confidence=1.0,
                source="skipped",
                reason="No group verification config",
                group_index=index,
                attributes=dict(group.attributes),
            )
            for index, group in enumerate(groups)
        ]
        return RuleGroupingResult(
            candidate_groups=groups,
            accepted_groups=groups,
            decisions=decisions,
        )
