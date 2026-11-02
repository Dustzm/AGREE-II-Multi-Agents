"""
Independent rule-engine pipeline runner.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Callable

from core.rule_engine.extractors.base import RuleObjectExtractor
from core.rule_engine.extractors.llm import LLMObjectExtractor
from core.rule_engine.grouping import (
    LLMRuleObjectGroupVerifier,
    NoopRuleObjectGroupVerifier,
    RuleObjectGroupBuilder,
)
from core.rule_engine.models import (
    EvidenceChunk,
    RubricAppraiserRule,
    RubricUnitRule,
    RuleGroupingResult,
    RuleLLMClient,
    RulePipelineResult,
    chunk_source_texts,
)
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.rubric.appraiser_pipeline import run_rubric_appraiser_pipeline
from core.rule_engine.rubric.pipeline import run_rubric_unit_pipeline
from core.rule_engine.stores.object_store import ObjectStore
from core.rule_engine.verification import (
    CompositeRuleObjectVerifier,
    DeterministicRuleObjectVerifier,
    LLMRuleObjectVerifier,
    NoopRuleObjectVerifier,
    RuleObjectVerifier,
)

logger = logging.getLogger(__name__)


def run_rule_pipeline(
    item_id: str,
    chunks: Sequence[EvidenceChunk],
    *,
    llm_client: RuleLLMClient | None = None,
    extractor: RuleObjectExtractor | None = None,
    batch_size: int = 5,
    lang: str = "zh",
    cancellation_check: Callable[[], None] | None = None,
    verifier: RuleObjectVerifier | None = None,
) -> RulePipelineResult:
    """
    Run extraction, object merging, and rule inference for one item.

    Either llm_client or extractor must be provided. Tests can inject a fake extractor.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    spec = get_item_spec(item_id)
    if isinstance(spec.rule, RubricAppraiserRule):
        return run_rubric_appraiser_pipeline(
            item_id,
            chunks,
            llm_client=llm_client,
            lang=lang,
            cancellation_check=cancellation_check,
        )
    if isinstance(spec.rule, RubricUnitRule):
        return run_rubric_unit_pipeline(
            item_id,
            chunks,
            llm_client=llm_client,
            lang=lang,
            cancellation_check=cancellation_check,
        )

    if extractor is None:
        if llm_client is None:
            raise ValueError("llm_client or extractor is required")
        extractor = LLMObjectExtractor(llm_client)

    store = ObjectStore(spec)
    chunk_list = list(chunks)
    object_verifier = verifier or _build_verifier(spec, llm_client)
    verification_decisions = []

    for index in range(0, len(chunk_list), batch_size):
        if cancellation_check is not None:
            cancellation_check()
        batch = chunk_list[index:index + batch_size]
        # execute entity extract
        extraction_result = extractor.extract(spec, batch, lang=lang)
        logger.info(
            "规则引擎抽取批次完成: item=%s, batch=%s-%s, objects=%s, review_reasons=%s",
            spec.item_id,
            index,
            index + len(batch),
            len(extraction_result.objects),
            [reason.code for reason in extraction_result.review_reasons],
        )
        verification_result = object_verifier.verify(
            spec,
            extraction_result.objects,
            batch,
            lang=lang,
        )
        verification_decisions.extend(verification_result.decisions)
        logger.info(
            "规则引擎验证批次完成: item=%s, accepted=%s/%s, rejected=%s",
            spec.item_id,
            len(verification_result.accepted_objects),
            len(extraction_result.objects),
            len([decision for decision in verification_result.decisions if not decision.accepted]),
        )
        # merge all extract results
        store.merge(
            verification_result.accepted_objects,
            processed_chunks=[chunk.chunk_id for chunk in batch],
            review_reasons=[*extraction_result.review_reasons, *verification_result.review_reasons],
            chunk_text_by_id=chunk_source_texts(batch),
        )

    snapshot = store.snapshot()
    grouping_result = None
    if spec.grouping is not None:
        group_builder = RuleObjectGroupBuilder()
        candidate_groups = group_builder.build(spec, snapshot, chunk_list)
        if not candidate_groups:
            grouping_result = RuleGroupingResult(candidate_groups=[], accepted_groups=[], decisions=[])
        else:
            group_verifier = _build_group_verifier(spec, llm_client)
            grouping_result = group_verifier.verify(
                spec,
                candidate_groups,
                chunk_list,
                lang=lang,
            )
        logger.info(
            "规则引擎分组验证完成: item=%s, accepted_groups=%s/%s, rejected=%s",
            spec.item_id,
            len(grouping_result.accepted_groups),
            len(grouping_result.candidate_groups),
            len([decision for decision in grouping_result.decisions if not decision.accepted]),
        )

    engine = get_rule_engine(spec.rule_profile)
    # execute rule engine
    rule_result = engine.execute(spec, snapshot, grouping_result)

    return RulePipelineResult(
        item_id=spec.item_id,
        objects=list(snapshot.objects),
        snapshot=snapshot,
        rule_result=rule_result,
        verification_decisions=verification_decisions,
        grouping_result=grouping_result,
    )


def _build_verifier(
    spec,
    llm_client: RuleLLMClient | None,
) -> RuleObjectVerifier:
    """Build the configured verifier chain for an item."""
    verification_specs = [
        obj_spec.verification
        for obj_spec in spec.object_specs.values()
        if obj_spec.verification is not None
    ]
    if not verification_specs:
        return NoopRuleObjectVerifier()

    llm_verifier = None
    if any(verification.llm_required for verification in verification_specs):
        if llm_client is None:
            raise ValueError(f"{spec.item_id} requires LLM object verification but llm_client is missing")
        llm_verifier = LLMRuleObjectVerifier(llm_client)
    return CompositeRuleObjectVerifier(
        DeterministicRuleObjectVerifier(),
        llm_verifier,
    )


def _build_group_verifier(
    spec,
    llm_client: RuleLLMClient | None,
):
    """Build the configured group verifier for an item."""
    grouping = spec.grouping
    if grouping is None or not grouping.llm_required:
        return NoopRuleObjectGroupVerifier()
    if llm_client is None:
        raise ValueError(f"{spec.item_id} requires LLM group verification but llm_client is missing")
    return LLMRuleObjectGroupVerifier(llm_client)
