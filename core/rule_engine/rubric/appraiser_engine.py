"""RuleResult generation for portfolio-level rubric appraisals."""
from __future__ import annotations

from core.rule_engine.models import (
    ItemSpec,
    ObjectStoreSnapshot,
    ReviewReason,
    RubricAppraiserResult,
    RubricAppraiserRule,
    RuleGroupingResult,
    RuleResult,
)


class RubricAppraiserEngine:
    """Score an item from one portfolio-level rubric appraisal decision."""

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Reject direct execution because rubric-appraiser scoring requires an appraisal decision."""
        reasons = list(snapshot.review_reasons)
        reasons.append(
            ReviewReason(
                code="rubric_appraiser_direct_execute_forbidden",
                message=f"{spec.item_id} must be scored through run_rubric_appraiser_pipeline.",
                severity="error",
            )
        )
        return self._result(spec.item_id, score=1, matched=[], missing=[], review_reasons=reasons, status="error")

    def score(self, spec: ItemSpec, rubric_result: RubricAppraiserResult) -> RuleResult:
        """Return the score selected by the rubric appraiser."""
        reasons = list(rubric_result.review_reasons)
        if not isinstance(spec.rule, RubricAppraiserRule):
            reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 RubricAppraiserRule",
                    severity="error",
                )
            )
            return self._result(spec.item_id, score=1, matched=[], missing=[], review_reasons=reasons, status="error")

        score = rubric_result.decision.score
        matched = [rubric_result.decision.decision_label] if score > 1 else []
        missing = [] if score > 1 else ["scoreable_rubric_evidence"]
        return self._result(
            spec.item_id,
            score=score,
            matched=matched,
            missing=missing,
            review_reasons=reasons,
            status="matched",
        )

    def _result(
        self,
        item_id: str,
        *,
        score: int,
        matched: list[str],
        missing: list[str],
        review_reasons: list[ReviewReason],
        status: str,
    ) -> RuleResult:
        return RuleResult(
            item_id=item_id,
            status=status,  # type: ignore[arg-type]
            score_range=str(score),
            allowed_scores=[score],
            max_score=score,
            matched_rules=matched,
            missing=missing,
            needs_review=bool(review_reasons),
            review_reasons=review_reasons,
        )
