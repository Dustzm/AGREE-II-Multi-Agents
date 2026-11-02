"""
Rule-engine explainer package.
"""

from .llm import LLMRuleExplainer
from .models import (
    CriterionEvidence,
    CriterionSummary,
    EvidenceSnippet,
    RuleExplanationInput,
    RuleExplanationResult,
)

__all__ = [
    "CriterionEvidence",
    "CriterionSummary",
    "EvidenceSnippet",
    "LLMRuleExplainer",
    "RuleExplanationInput",
    "RuleExplanationResult",
]
