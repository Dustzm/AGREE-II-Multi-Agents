"""
Models used by rule-engine explainers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceSnippet:
    """Human-facing evidence snippet with its original chunk reference."""

    chunk_id: str
    title: str
    quote: str


@dataclass(frozen=True)
class CriterionEvidence:
    """Matched human-facing criterion and supporting evidence."""

    description: str
    evidence: tuple[EvidenceSnippet, ...]


@dataclass(frozen=True)
class CriterionSummary:
    """Missing human-facing criterion summary."""

    description: str


@dataclass(frozen=True)
class RuleExplanationInput:
    """Input contract passed from rule inference to the explainer LLM."""

    item_id: str
    title: str
    scoring_principle: list[dict[str, str]]
    score_range: str
    allowed_scores: list[int]
    needs_review: bool
    matched_criteria: tuple[CriterionEvidence, ...]
    missing_criteria: tuple[CriterionSummary, ...]
    internal_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleExplanationResult:
    """Appraiser-compatible explanation generated from rule-engine output."""

    step1_locate: str
    step2_evidence: list[str]
    step3_reasoning: dict[str, Any]
    score: int
