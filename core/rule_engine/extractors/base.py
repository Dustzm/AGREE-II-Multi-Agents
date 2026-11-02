"""
Base protocol for rule-object extractors.
"""
from __future__ import annotations

from typing import Protocol

from core.rule_engine.models import EvidenceChunk, ExtractionBatchResult, ItemSpec


class RuleObjectExtractor(Protocol):
    """Extractor interface for atomic rule objects."""

    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        """Extract rule objects from a batch of chunks."""
