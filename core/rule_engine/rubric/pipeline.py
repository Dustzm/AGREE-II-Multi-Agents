"""Pipeline runner for rubric-unit rule appraisal."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from core.rule_engine.models import (
    EvidenceChunk,
    ObjectStoreSnapshot,
    ReviewReason,
    RuleLLMClient,
    RulePipelineResult,
    RubricUnitResult,
    RubricUnitRule,
)
from core.rule_engine.registry import get_item_spec
from core.rule_engine.rubric.builder import EvidenceUnitBuilder
from core.rule_engine.rubric.classifier import RubricClassifier
from core.rule_engine.rubric.engine import RubricUnitEngine


def run_rubric_unit_pipeline(
    item_id: str,
    chunks: Sequence[EvidenceChunk],
    *,
    llm_client: RuleLLMClient | None,
    lang: str = "zh",
    cancellation_check: Callable[[], None] | None = None,
) -> RulePipelineResult:
    """Run evidence-unit classification and rubric scoring for one item."""
    spec = get_item_spec(item_id)
    if not isinstance(spec.rule, RubricUnitRule):
        raise ValueError(f"{spec.item_id} is not configured with RubricUnitRule")
    if spec.rubric_unit is None:
        raise ValueError(f"{spec.item_id} is missing RubricUnitSpec")
    if llm_client is None:
        raise ValueError(f"{spec.item_id} requires LLM rubric classification but llm_client is missing")

    chunk_list = list(chunks)
    snapshot = _empty_snapshot(spec.item_id, chunk_list)
    if cancellation_check is not None:
        cancellation_check()

    units = EvidenceUnitBuilder().build(spec, chunk_list)
    if not units:
        review_reasons = [
            ReviewReason(
                code="no_rubric_candidate_units",
                message="未生成可供 rubric 分类的证据单元",
                severity="info",
            )
        ]
        rubric_result = RubricUnitResult(
            candidate_units=[],
            accepted_units=[],
            decisions=[],
            review_reasons=review_reasons,
        )
        rule_result = RubricUnitEngine().score(spec, rubric_result)
        return RulePipelineResult(
            item_id=spec.item_id,
            objects=[],
            snapshot=snapshot,
            rule_result=rule_result,
            rubric_result=rubric_result,
        )

    rubric_result = RubricClassifier(llm_client).classify(spec, units, lang=lang)
    if cancellation_check is not None:
        cancellation_check()
    rule_result = RubricUnitEngine().score(spec, rubric_result)

    return RulePipelineResult(
        item_id=spec.item_id,
        objects=[],
        snapshot=snapshot,
        rule_result=rule_result,
        rubric_result=rubric_result,
    )


def _empty_snapshot(item_id: str, chunks: list[EvidenceChunk]) -> ObjectStoreSnapshot:
    return ObjectStoreSnapshot(
        item_id=item_id,
        objects=(),
        processed_chunks=tuple(chunk.chunk_id for chunk in chunks),
        chunk_order=tuple((chunk.chunk_id, index) for index, chunk in enumerate(chunks)),
        review_reasons=(),
    )
