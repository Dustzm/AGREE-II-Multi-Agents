"""Pipeline runner for portfolio-level rubric appraisal."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from core.rule_engine.models import (
    EvidenceChunk,
    ObjectStoreSnapshot,
    ReviewReason,
    RubricAppraiserDecision,
    RubricAppraiserResult,
    RubricAppraiserRule,
    RuleLLMClient,
    RulePipelineResult,
)
from core.rule_engine.registry import get_item_spec
from core.rule_engine.rubric.appraiser import RubricPortfolioAppraiser
from core.rule_engine.rubric.appraiser_builder import EvidencePortfolioBuilder
from core.rule_engine.rubric.appraiser_engine import RubricAppraiserEngine


def run_rubric_appraiser_pipeline(
    item_id: str,
    chunks: Sequence[EvidenceChunk],
    *,
    llm_client: RuleLLMClient | None,
    lang: str = "zh",
    cancellation_check: Callable[[], None] | None = None,
) -> RulePipelineResult:
    """Run evidence-portfolio appraisal and scoring for one item."""
    spec = get_item_spec(item_id)
    if not isinstance(spec.rule, RubricAppraiserRule):
        raise ValueError(f"{spec.item_id} is not configured with RubricAppraiserRule")
    if spec.rubric_appraiser is None:
        raise ValueError(f"{spec.item_id} is missing RubricAppraiserSpec")
    if llm_client is None:
        raise ValueError(f"{spec.item_id} requires LLM rubric appraisal but llm_client is missing")

    chunk_list = list(chunks)
    snapshot = _empty_snapshot(spec.item_id, chunk_list)
    if cancellation_check is not None:
        cancellation_check()

    units = EvidencePortfolioBuilder().build(spec, chunk_list)
    if units:
        rubric_result = RubricPortfolioAppraiser(llm_client).appraise(spec, units, chunk_list, lang=lang)
    else:
        rubric_result = RubricAppraiserResult(
            candidate_units=[],
            decision=RubricAppraiserDecision(
                score=1,
                confidence=0.0,
                decision_label="no_relevant_evidence",
                rationale="未生成可供 rubric appraiser 判断的证据组合。",
                why_not_higher="未生成可供 rubric appraiser 判断的证据组合。",
                evidence=[],
            ),
            review_reasons=[
                ReviewReason(
                    code="no_rubric_portfolio_units",
                    message="未生成可供 rubric appraiser 判断的证据组合。",
                    severity="info",
                )
            ],
        )
    if cancellation_check is not None:
        cancellation_check()

    rule_result = RubricAppraiserEngine().score(spec, rubric_result)
    return RulePipelineResult(
        item_id=spec.item_id,
        objects=[],
        snapshot=snapshot,
        rule_result=rule_result,
        rubric_appraiser_result=rubric_result,
    )


def _empty_snapshot(item_id: str, chunks: list[EvidenceChunk]) -> ObjectStoreSnapshot:
    return ObjectStoreSnapshot(
        item_id=item_id,
        objects=(),
        processed_chunks=tuple(chunk.chunk_id for chunk in chunks),
        chunk_order=tuple((chunk.chunk_id, index) for index, chunk in enumerate(chunks)),
        review_reasons=(),
    )
