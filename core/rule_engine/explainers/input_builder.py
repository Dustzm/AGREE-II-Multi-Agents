"""
Build human-facing explainer input from rule-engine pipeline output.
"""
from __future__ import annotations

from core.rule_engine.explainers.models import (
    CriterionEvidence,
    CriterionSummary,
    EvidenceSnippet,
    RuleExplanationInput,
)
from core.rule_engine.models import EvidenceChunk, ItemSpec, RulePipelineResult


MAX_EVIDENCE_PER_CRITERION = 3
MAX_TOTAL_EVIDENCE = 12


def build_rule_explanation_input(
    spec: ItemSpec,
    pipeline_result: RulePipelineResult,
    *,
    chunk_by_id: dict[str, EvidenceChunk],
    scoring_principle: list[dict[str, str]],
) -> RuleExplanationInput:
    """Convert rule-engine output into an explainer-safe input schema."""
    matched_criteria: list[CriterionEvidence] = []
    total_evidence = 0
    for object_type in pipeline_result.rule_result.matched_rules:
        obj_spec = spec.object_specs.get(object_type)
        if obj_spec is None:
            continue
        snippets = []
        for obj in pipeline_result.snapshot.get_objects(object_type):
            if len(snippets) >= MAX_EVIDENCE_PER_CRITERION or total_evidence >= MAX_TOTAL_EVIDENCE:
                break
            chunk = chunk_by_id.get(obj.evidence.chunk_id)
            snippets.append(
                EvidenceSnippet(
                    chunk_id=obj.evidence.chunk_id,
                    title=chunk.title if chunk else obj.evidence.chunk_id,
                    quote=obj.evidence.quote,
                )
            )
            total_evidence += 1
        matched_criteria.append(
            CriterionEvidence(
                description=obj_spec.description,
                evidence=tuple(snippets),
            )
        )

    missing_criteria = []
    for object_type in pipeline_result.rule_result.missing:
        obj_spec = spec.object_specs.get(object_type)
        if obj_spec is None:
            continue
        missing_criteria.append(
            CriterionSummary(
                description=obj_spec.description,
            )
        )

    return RuleExplanationInput(
        item_id=spec.item_id,
        title=spec.title,
        scoring_principle=scoring_principle,
        score_range=pipeline_result.rule_result.score_range,
        allowed_scores=pipeline_result.rule_result.allowed_scores,
        needs_review=pipeline_result.rule_result.needs_review,
        matched_criteria=tuple(matched_criteria),
        missing_criteria=tuple(missing_criteria),
        internal_terms=tuple(spec.object_specs.keys()),
    )
