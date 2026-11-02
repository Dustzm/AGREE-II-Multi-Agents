"""
Shared data models for the rule engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from core.llm.failures import ValidationIssue


ObjectRole = Literal[
    "gate",
    "component",
    "exclusion",
    "qualifier",
    "relationship",
    "format",
    "review_flag",
]

ConditionOp = Literal["fact", "all", "any", "not"]


@dataclass(frozen=True)
class EvidenceChunk:
    """Chunk content passed into rule-engine extraction."""

    chunk_id: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def source_text(self) -> str:
        """Text a quote may be copied from: content plus title/metadata variants."""
        title = self.title.strip()
        content = self.content.strip()
        metadata_text = " ".join(str(value) for value in self.metadata.values() if value)
        title_content = " ".join(part for part in (title, content) if part)
        title_colon_content = ": ".join(part for part in (title, content) if part)
        return "\n".join(
            part
            for part in (title_colon_content, title_content, metadata_text, content)
            if part
        )


def chunk_source_texts(chunks) -> dict[str, str]:
    """``chunk_id -> source_text`` for quote validation."""
    return {chunk.chunk_id: chunk.source_text() for chunk in chunks}


@dataclass(frozen=True)
class EvidenceUnit:
    """Natural evidence unit used by rubric-unit appraisal."""

    unit_id: str
    chunk_id: str
    title: str
    quote: str
    content: str
    unit_type: str
    chunk_order: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


@dataclass(frozen=True)
class EvidenceRef:
    """Original-text reference for one extracted rule object."""

    chunk_id: str
    quote: str


@dataclass(frozen=True)
class RuleObjectSpec:
    """Definition of one object type required by an item rule."""

    type: str
    role: ObjectRole
    description: str
    evidence_required: bool = True
    min_confidence: float = 0.7
    counts_for: str | None = None
    max_objects: int | None = None
    require_value_in_quote: bool = True
    verification: "ObjectVerificationSpec | None" = None


@dataclass(frozen=True)
class ChunkRoutingSpec:
    """Item-level weighted chunk routing configuration."""

    heading_terms: tuple[str, ...] = ()
    include_phrases: tuple[str, ...] = ()
    include_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()
    neighbor_window: int = 1
    max_chunks: int = 24
    max_domain_chunks: int | None = None
    # Extra labels excluded for this item only, merged with the global setting.
    excluded_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupingSpec:
    """Item-level object grouping configuration."""

    group_profile: str
    anchor_object_types: tuple[str, ...] = ()
    member_object_types: tuple[str, ...] = ()
    neighbor_window: int = 1
    same_section: bool = True
    max_groups: int = 12
    llm_required: bool = True
    guidance: str = ""


@dataclass(frozen=True)
class ObjectVerificationSpec:
    """Object-level candidate evidence verification configuration."""

    required_context_terms: tuple[str, ...] = ()
    required_any_terms: tuple[str, ...] = ()
    hard_negative_terms: tuple[str, ...] = ()
    hard_negative_context_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()
    negative_context_terms: tuple[str, ...] = ()
    llm_required: bool = False
    guidance: str = ""


@dataclass(frozen=True)
class RuleCondition:
    """Boolean condition used by rule configurations."""

    op: ConditionOp
    fact: str | None = None
    children: tuple["RuleCondition", ...] = ()


@dataclass(frozen=True)
class ScoreLevel:
    """One milestone score range and the condition required to reach it."""

    score_range: str
    condition: RuleCondition
    description: str = ""


@dataclass(frozen=True)
class BranchCase:
    """One prioritized condition-to-score branch case."""

    name: str
    condition: RuleCondition
    score_range: str
    description: str = ""


@dataclass(frozen=True)
class GateComponentRule:
    """Rule marker for gate-plus-component count scoring."""


@dataclass(frozen=True)
class MilestoneRule:
    """Ordered milestone score levels for milestone-based scoring."""

    levels: tuple[ScoreLevel, ...]


@dataclass(frozen=True)
class WeightedChecklistItem:
    """One weighted item in checklist-based scoring."""

    fact: str
    weight: int = 1
    description: str = ""


@dataclass(frozen=True)
class WeightedChecklistRule:
    """Weighted checklist scoring rule."""

    items: tuple[WeightedChecklistItem, ...]
    base_score: int = 1
    max_score: int = 7
    gate: RuleCondition | None = None
    gate_score_range: str = "1"
    gate_fallback_cases: tuple[BranchCase, ...] = ()


@dataclass(frozen=True)
class BranchingRule:
    """Prioritized branch cases for branch-based scoring."""

    cases: tuple[BranchCase, ...]
    conflict_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class FundingDisclosureRule:
    """Rule marker for AGREE-II item 22 funding disclosure scoring."""

    conflict_groups: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class MonitoringCriteriaRule:
    """Rule marker for AGREE-II item 21 monitoring criteria group scoring."""

    neighbor_window: int = 1


@dataclass(frozen=True)
class ImplementationToolRule:
    """Rule marker for AGREE-II item 19 implementation tool/resource scoring."""


@dataclass(frozen=True)
class ApplicationFactorRule:
    """Rule marker for AGREE-II item 18 application-factor group scoring."""


@dataclass(frozen=True)
class RubricUnitRule:
    """Rule marker for evidence-unit rubric scoring."""


@dataclass(frozen=True)
class RubricAppraiserRule:
    """Rule marker for portfolio-level rubric appraisal."""


@dataclass(frozen=True)
class ManagementOptionsRule:
    """Rule marker for AGREE-II item 16 management-options group scoring."""


@dataclass(frozen=True)
class HealthQuestionRule:
    """Rule marker for AGREE-II item 2 health-question completeness scoring."""


@dataclass(frozen=True)
class TargetUserRule:
    """Rule marker for AGREE-II item 6 target-user scoring."""


@dataclass(frozen=True)
class EvidenceAppraisalRule:
    """Rule marker for AGREE-II item 9 evidence-strength appraisal scoring."""


@dataclass(frozen=True)
class GuidelineUpdateRule:
    """Rule marker for AGREE-II item 14 guideline-update scoring."""


@dataclass(frozen=True)
class BenefitHarmBalanceRule:
    """Rule marker for AGREE-II item 11 benefit-harm balance scoring."""


@dataclass(frozen=True)
class RecommendationEvidenceLinkRule:
    """Rule marker for AGREE-II item 12 recommendation-evidence link scoring."""


@dataclass(frozen=True)
class CompetingInterestDisclosureRule:
    """Rule marker for AGREE-II item 23 competing-interest disclosure scoring."""

    conflict_groups: tuple[tuple[str, ...], ...] = ()


Rule = (
    GateComponentRule
    | MilestoneRule
    | WeightedChecklistRule
    | BranchingRule
    | FundingDisclosureRule
    | MonitoringCriteriaRule
    | ImplementationToolRule
    | ApplicationFactorRule
    | RubricUnitRule
    | RubricAppraiserRule
    | ManagementOptionsRule
    | HealthQuestionRule
    | TargetUserRule
    | EvidenceAppraisalRule
    | GuidelineUpdateRule
    | BenefitHarmBalanceRule
    | RecommendationEvidenceLinkRule
    | CompetingInterestDisclosureRule
)


@dataclass(frozen=True)
class RubricUnitSpec:
    """Configuration for evidence-unit rubric classification."""

    profile: str
    guidance: str = ""
    bands: tuple[str, ...] = ()
    max_units: int = 10
    unit_char_limit: int = 1800
    criteria_descriptions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RubricAppraiserSpec:
    """Configuration for portfolio-level rubric appraisal."""

    profile: str
    guidance: str = ""
    max_units: int = 8
    unit_char_limit: int = 1400
    decision_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ItemSpec:
    """Rule specification for one AGREE-II item."""

    item_id: str
    title: str
    rule_profile: str
    object_specs: dict[str, RuleObjectSpec]
    extraction_guidance: str = ""
    verification_guidance: str = ""
    rule: Rule | None = None
    chunk_routing: ChunkRoutingSpec | None = None
    grouping: GroupingSpec | None = None
    rubric_unit: RubricUnitSpec | None = None
    rubric_appraiser: RubricAppraiserSpec | None = None


def fact(name: str) -> RuleCondition:
    """Return a fact-existence condition."""
    return RuleCondition(op="fact", fact=name)


def all_of(*conditions: RuleCondition | str) -> RuleCondition:
    """Return a condition requiring all child conditions."""
    return RuleCondition(
        op="all",
        children=tuple(_coerce_condition(condition) for condition in conditions),
    )


def any_of(*conditions: RuleCondition | str) -> RuleCondition:
    """Return a condition requiring at least one child condition."""
    return RuleCondition(
        op="any",
        children=tuple(_coerce_condition(condition) for condition in conditions),
    )


def not_of(condition: RuleCondition | str) -> RuleCondition:
    """Return a condition negating one child condition."""
    return RuleCondition(op="not", children=(_coerce_condition(condition),))


def _coerce_condition(condition: RuleCondition | str) -> RuleCondition:
    if isinstance(condition, RuleCondition):
        return condition
    return fact(condition)


@dataclass(frozen=True)
class RuleObject:
    """Atomic object extracted from guideline text for rule inference."""

    type: str
    value: str
    evidence: EvidenceRef
    confidence: float = 1.0
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleObjectGroup:
    """A candidate relation group built from accepted rule objects."""

    group_id: str
    group_type: str
    objects: tuple[RuleObject, ...]
    chunk_ids: tuple[str, ...]
    relation_hints: tuple[str, ...] = ()
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewReason:
    """Machine-readable reason explaining why a result needs review."""

    code: str
    message: str
    object_type: str | None = None
    object_value: str | None = None
    severity: Literal["info", "warning", "error"] = "warning"


@dataclass(frozen=True)
class ExtractionBatchResult:
    """Objects and recoverable quality issues from one extraction batch."""

    objects: list[RuleObject]
    review_reasons: list[ReviewReason] = field(default_factory=list)
    dropped: tuple[dict[str, Any], ...] = ()
    issues: tuple[ValidationIssue, ...] = ()


@dataclass(frozen=True)
class ObjectStoreSnapshot:
    """Immutable view of merged rule objects and validation warnings."""

    item_id: str
    objects: tuple[RuleObject, ...]
    processed_chunks: tuple[str, ...]
    chunk_order: tuple[tuple[str, int], ...] = ()
    review_reasons: tuple[ReviewReason, ...] = ()

    def has_object(self, object_type: str) -> bool:
        """Return whether at least one object of this type exists."""
        return any(obj.type == object_type for obj in self.objects)

    def get_objects(self, object_type: str) -> list[RuleObject]:
        """Return objects of a specific type."""
        return [obj for obj in self.objects if obj.type == object_type]


@dataclass(frozen=True)
class RuleResult:
    """Rule-engine inference result using a unified score range contract."""

    item_id: str
    status: Literal["matched", "no_match", "error"]
    score_range: str
    allowed_scores: list[int]
    max_score: int
    matched_rules: list[str]
    missing: list[str]
    needs_review: bool
    review_reasons: list[ReviewReason] = field(default_factory=list)


VerificationSource = Literal["deterministic", "llm", "skipped"]


@dataclass(frozen=True)
class VerificationDecision:
    """One candidate object verification decision."""

    object_type: str
    object_value: str
    chunk_id: str
    accepted: bool
    confidence: float
    source: VerificationSource
    rejection_code: str = ""
    reason: str = ""
    object_index: int | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationBatchResult:
    """Verified objects and per-candidate verification decisions."""

    accepted_objects: list[RuleObject]
    decisions: list[VerificationDecision]
    review_reasons: list[ReviewReason] = field(default_factory=list)


GroupVerificationSource = Literal["llm", "skipped"]


@dataclass(frozen=True)
class GroupVerificationDecision:
    """One candidate group verification decision."""

    group_id: str
    group_type: str
    accepted: bool
    confidence: float
    source: GroupVerificationSource
    rejection_code: str = ""
    reason: str = ""
    group_index: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleGroupingResult:
    """Candidate and accepted object groups for group-aware scoring."""

    candidate_groups: list[RuleObjectGroup]
    accepted_groups: list[RuleObjectGroup]
    decisions: list[GroupVerificationDecision]
    review_reasons: list[ReviewReason] = field(default_factory=list)


@dataclass(frozen=True)
class RubricDecision:
    """One rubric classification decision for an evidence unit."""

    unit_id: str
    accepted: bool
    band: str
    score_range: str
    confidence: float
    reason_code: str
    reason: str
    unit_index: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RubricUnitResult:
    """Candidate evidence units and rubric decisions for an item."""

    candidate_units: list[EvidenceUnit]
    accepted_units: list[EvidenceUnit]
    decisions: list[RubricDecision]
    best_decision: RubricDecision | None = None
    review_reasons: list[ReviewReason] = field(default_factory=list)


@dataclass(frozen=True)
class RubricAppraiserDecision:
    """One portfolio-level rubric appraisal decision."""

    score: int
    confidence: float
    decision_label: str
    rationale: str
    why_not_higher: str
    evidence: list[EvidenceRef] = field(default_factory=list)


@dataclass(frozen=True)
class RubricAppraiserResult:
    """Candidate evidence units and one item-level rubric decision."""

    candidate_units: list[EvidenceUnit]
    decision: RubricAppraiserDecision
    review_reasons: list[ReviewReason] = field(default_factory=list)


@dataclass(frozen=True)
class RoutedChunkInfo:
    """Debug metadata for one routed chunk."""

    chunk_id: str
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChunkRoutingResult:
    """Selected chunks and routing diagnostics."""

    chunks: tuple[EvidenceChunk, ...]
    routed_chunks: tuple[RoutedChunkInfo, ...] = ()
    input_chunks: int = 0
    domain_chunks: int = 0
    selected_chunks: int = 0
    review_reasons: tuple[ReviewReason, ...] = ()


@dataclass(frozen=True)
class RulePipelineResult:
    """Full independent rule-engine pipeline result."""

    item_id: str
    objects: list[RuleObject]
    snapshot: ObjectStoreSnapshot
    rule_result: RuleResult
    verification_decisions: list[VerificationDecision] = field(default_factory=list)
    grouping_result: RuleGroupingResult | None = None
    rubric_result: RubricUnitResult | None = None
    rubric_appraiser_result: RubricAppraiserResult | None = None


class RuleLLMClient(Protocol):
    """Protocol for JSON-only LLM calls used by extractors."""

    def complete_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
        schema_name: str = "rule_engine_response",
        strict: bool = True,
    ) -> dict[str, Any]:
        """Return a parsed JSON object from an LLM completion."""
