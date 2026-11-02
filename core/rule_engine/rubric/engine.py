"""RuleResult generation for rubric-unit decisions."""
from __future__ import annotations

from core.rule_engine.models import (
    ItemSpec,
    ObjectStoreSnapshot,
    ReviewReason,
    RubricDecision,
    RubricUnitResult,
    RubricUnitRule,
    RuleGroupingResult,
    RuleResult,
)
from core.rule_engine.rubric.schemas import score_range_for_band


class RubricUnitEngine:
    """Score an item from accepted evidence-unit rubric decisions."""

    _MATCHED = {
        "item18_application_factors": {
            "single_direction_factor": ["application_factor"],
            "both_facilitator_and_barrier": ["application_factor", "both_facilitator_and_barrier"],
            "both_plus_collection_or_impact": [
                "application_factor",
                "both_facilitator_and_barrier",
                "collection_or_impact",
            ],
            "factors_plus_collection_and_impact": [
                "application_factor",
                "both_facilitator_and_barrier",
                "collection_method",
                "impact_on_guideline_or_recommendations",
            ],
        },
        "item19_implementation_support": {
            "implementation_discussion_only": ["implementation_advice_or_discussion"],
            "tool_like_but_not_eligible": ["tool_like_content"],
            "one_eligible_tool_no_instruction": ["eligible_implementation_tool"],
            "multiple_eligible_tools_no_instruction": ["eligible_implementation_tool", "multiple_tools"],
            "implementation_section_plus_tool_no_instruction": [
                "implementation_section_or_module",
                "eligible_implementation_tool",
            ],
            "eligible_tool_with_use_instruction": [
                "eligible_implementation_tool",
                "tool_use_instruction",
            ],
            "complete_tool_with_use_instruction": [
                "eligible_implementation_tool",
                "tool_use_instruction",
                "complete_practical_instruction",
            ],
        },
        "item21_monitoring_criteria": {
            "vague_monitoring": ["vague_monitoring_mention"],
            "criterion_or_frequency_only": ["indicator_or_frequency"],
            "criterion_plus_frequency": [
                "indicator_or_standard",
                "measurement_frequency_or_interval",
            ],
            "criterion_plus_frequency_plus_method": [
                "indicator_or_standard",
                "measurement_frequency_or_interval",
                "measurement_method",
            ],
        },
    }
    _MISSING = {
        "item18_application_factors": {
            "single_direction_factor": [
                "both_facilitator_and_barrier",
                "collection_method",
                "impact_on_guideline_or_recommendations",
            ],
            "both_facilitator_and_barrier": [
                "collection_method",
                "impact_on_guideline_or_recommendations",
            ],
            "both_plus_collection_or_impact": [
                "collection_method_and_impact_on_guideline_or_recommendations",
            ],
            "factors_plus_collection_and_impact": [],
        },
        "item19_implementation_support": {
            "implementation_discussion_only": ["eligible_implementation_tool"],
            "tool_like_but_not_eligible": ["eligible_implementation_tool"],
            "one_eligible_tool_no_instruction": [
                "implementation_section_or_module",
                "tool_use_instruction",
            ],
            "multiple_eligible_tools_no_instruction": [
                "implementation_section_or_module",
                "tool_use_instruction",
            ],
            "implementation_section_plus_tool_no_instruction": ["tool_use_instruction"],
            "eligible_tool_with_use_instruction": [],
            "complete_tool_with_use_instruction": [],
        },
        "item21_monitoring_criteria": {
            "vague_monitoring": ["indicator_or_standard", "measurement_frequency_or_interval"],
            "criterion_or_frequency_only": ["indicator_or_standard_or_frequency_counterpart"],
            "criterion_plus_frequency": ["measurement_method"],
            "criterion_plus_frequency_plus_method": [],
        },
    }

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Reject direct execution because rubric-unit scoring requires classifier decisions."""
        reasons = list(snapshot.review_reasons)
        reasons.append(
            ReviewReason(
                code="rubric_unit_direct_execute_forbidden",
                message=(
                    f"{spec.item_id} uses rubric_unit and must be scored through "
                    "run_rubric_unit_pipeline with RubricClassifier decisions."
                ),
                severity="error",
            )
        )
        return self._error_result(spec.item_id, reasons)

    def score(
        self,
        spec: ItemSpec,
        rubric_result: RubricUnitResult,
        *,
        review_reasons: list[ReviewReason] | None = None,
    ) -> RuleResult:
        """Return the best score from rubric decisions."""
        reasons = list(review_reasons or [])
        reasons.extend(rubric_result.review_reasons)
        if not isinstance(spec.rule, RubricUnitRule):
            reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 RubricUnitRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, reasons)
        if spec.rubric_unit is None:
            reasons.append(
                ReviewReason(
                    code="missing_rubric_unit_spec",
                    message=f"{spec.item_id} 缺少 RubricUnitSpec",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, reasons)

        scoreable = [
            decision
            for decision in rubric_result.decisions
            if decision.accepted and decision.score_range != "1"
        ]
        if not scoreable:
            return self.empty_result(spec, reasons)

        best = max(
            scoreable,
            key=lambda decision: (
                max(self._parse_score_range(decision.score_range)),
                decision.confidence,
                -(decision.unit_index or 0),
            ),
        )
        expected_score_range = score_range_for_band(spec.rubric_unit.profile, best.band)
        if best.score_range != expected_score_range:
            reasons.append(
                ReviewReason(
                    code="rubric_score_range_mismatch",
                    message=(
                        f"{best.band} 返回 {best.score_range}，"
                        f"应为 {expected_score_range}"
                    ),
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, reasons)

        return self._result(
            spec=spec,
            score_range=expected_score_range,
            matched_rules=self._matched_rules(spec, best),
            missing=self._missing_rules(spec, best),
            review_reasons=reasons,
        )

    def empty_result(self, spec: ItemSpec, review_reasons: list[ReviewReason] | None = None) -> RuleResult:
        """Return score 1 for rubric-unit items with no valid evidence unit."""
        profile = spec.rubric_unit.profile if spec.rubric_unit else ""
        missing_defaults = {
            "item18_application_factors": ["application_factor"],
            "item19_implementation_support": ["implementation_advice_or_discussion", "eligible_implementation_tool"],
            "item21_monitoring_criteria": ["indicator_or_standard", "measurement_frequency_or_interval"],
        }
        return self._result(
            spec=spec,
            score_range="1",
            matched_rules=[],
            missing=missing_defaults.get(profile, []),
            review_reasons=list(review_reasons or []),
        )

    def _matched_rules(self, spec: ItemSpec, decision: RubricDecision) -> list[str]:
        profile = spec.rubric_unit.profile if spec.rubric_unit else ""
        return list(self._MATCHED.get(profile, {}).get(decision.band, []))

    def _missing_rules(self, spec: ItemSpec, decision: RubricDecision) -> list[str]:
        profile = spec.rubric_unit.profile if spec.rubric_unit else ""
        return list(self._MISSING.get(profile, {}).get(decision.band, []))

    def _result(
        self,
        *,
        spec: ItemSpec,
        score_range: str,
        matched_rules: list[str],
        missing: list[str],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        allowed_scores = self._parse_score_range(score_range)
        return RuleResult(
            item_id=spec.item_id,
            status="matched",
            score_range=score_range,
            allowed_scores=allowed_scores,
            max_score=max(allowed_scores),
            matched_rules=matched_rules,
            missing=missing,
            needs_review=bool(review_reasons),
            review_reasons=review_reasons,
        )

    def _error_result(self, item_id: str, review_reasons: list[ReviewReason]) -> RuleResult:
        return RuleResult(
            item_id=item_id,
            status="error",
            score_range="1",
            allowed_scores=[1],
            max_score=1,
            matched_rules=[],
            missing=[],
            needs_review=True,
            review_reasons=review_reasons,
        )

    def _parse_score_range(self, score_range: str) -> list[int]:
        if "-" not in score_range:
            return [int(score_range)]
        start, end = score_range.split("-", 1)
        return list(range(int(start), int(end) + 1))
