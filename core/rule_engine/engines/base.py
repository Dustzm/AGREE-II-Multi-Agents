"""
Base protocol for rule inference engines.
"""
from __future__ import annotations

from typing import Protocol

from core.rule_engine.models import ItemSpec, ObjectStoreSnapshot, RuleGroupingResult, RuleResult


class RuleEngine(Protocol):
    """
    Structural interface for rule inference engines.

    Implementations do not need to explicitly inherit from this Protocol.
    A class is considered a RuleEngine as long as it provides an execute()
    method with this signature.
    """

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """
        Infer a rule result from merged objects.

        Args:
            spec: Item-level rule configuration and scoring profile.
            snapshot: Deduplicated objects and quality review reasons.
            grouping_result: Optional group-level evidence verification result.

        Returns:
            Rule result containing score range, matched facts, missing facts, and
            review reasons for downstream explanation.
        """
