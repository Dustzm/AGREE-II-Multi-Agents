"""Composite rule-object verifier."""
from __future__ import annotations

from dataclasses import replace

from core.rule_engine.models import (
    EvidenceChunk,
    ItemSpec,
    ReviewReason,
    RuleObject,
    VerificationBatchResult,
)
from core.rule_engine.verification.deterministic import DeterministicRuleObjectVerifier
from core.rule_engine.verification.llm import LLMRuleObjectVerifier


class CompositeRuleObjectVerifier:
    """Run deterministic verification first, then LLM verification when configured."""

    def __init__(
        self,
        deterministic: DeterministicRuleObjectVerifier,
        llm: LLMRuleObjectVerifier | None = None,
    ):
        self.deterministic = deterministic
        self.llm = llm

    def verify(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunks: list[EvidenceChunk],
        *,
        lang: str = "zh",
    ) -> VerificationBatchResult:
        deterministic_result = self.deterministic.verify(
            spec,
            objects,
            chunks,
            lang=lang,
        )

        decision_by_index = {
            decision.object_index: decision
            for decision in deterministic_result.decisions
        }
        llm_candidates: list[RuleObject] = []
        llm_original_indices: list[int | None] = []
        llm_candidate_decisions = []
        deterministic_only: list[RuleObject] = []
        deterministic_final_decisions = []
        deterministic_review_reasons: list[ReviewReason] = []

        for index, obj in enumerate(objects):
            decision = decision_by_index.get(index)
            if decision is None:
                continue
            obj_spec = spec.object_specs.get(obj.type)
            verification = obj_spec.verification if obj_spec is not None else None
            if verification is not None and verification.llm_required and decision.accepted:
                llm_candidates.append(obj)
                llm_original_indices.append(index)
                llm_candidate_decisions.append(decision)
                continue

            deterministic_final_decisions.append(decision)
            if decision.accepted:
                deterministic_only.append(obj)
            else:
                deterministic_review_reasons.append(
                    ReviewReason(
                        code="verification_rejected",
                        message=decision.reason,
                        object_type=decision.object_type,
                        object_value=decision.object_value,
                        severity="warning",
                    )
                )

        if not llm_candidates:
            return deterministic_result
        if self.llm is None:
            raise ValueError(f"{spec.item_id} requires LLM object verification but no llm verifier is configured")

        llm_result = self.llm.verify(
            spec,
            llm_candidates,
            chunks,
            lang=lang,
            candidate_decisions=llm_candidate_decisions,
        )
        remapped_llm_decisions = [
            replace(
                decision,
                object_index=llm_original_indices[decision.object_index]
                if decision.object_index is not None and decision.object_index < len(llm_original_indices)
                else decision.object_index,
            )
            for decision in llm_result.decisions
        ]
        final_decisions = sorted(
            [*deterministic_final_decisions, *remapped_llm_decisions],
            key=lambda decision: (
                decision.object_index is None,
                decision.object_index if decision.object_index is not None else 0,
            ),
        )
        return VerificationBatchResult(
            accepted_objects=[*deterministic_only, *llm_result.accepted_objects],
            decisions=final_decisions,
            review_reasons=[*deterministic_review_reasons, *llm_result.review_reasons],
        )
