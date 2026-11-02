"""
Adapter that lets the independent rule engine replace appraiser scoring.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from core.config import get_settings
from core.llm.gateway import LLMGateway
from core.llm.recovery import RecoveryBudget
from core.preprocessor.iterator import EvidenceIterator
from core.preprocessor.pojo import SemanticChunk
from core.rule_engine.explainers.input_builder import build_rule_explanation_input
from core.rule_engine.explainers.llm import LLMRuleExplainer
from core.rule_engine.explainers.models import RuleExplanationInput, RuleExplanationResult
from core.rule_engine.litellm_client import LiteLLMRuleClient
from core.rule_engine.models import (
    ChunkRoutingResult,
    EvidenceChunk,
    ReviewReason,
    RoutedChunkInfo,
    RubricAppraiserRule,
    RubricUnitRule,
    RulePipelineResult,
)
from core.rule_engine.registry import ITEM_SPECS, get_item_spec
from core.rule_engine.routing import WeightedChunkRouter
from core.rule_engine.runner import run_rule_pipeline
from core.utils.cancellation import CancellationRequested
from core.utils.unicode_sanitizer import sanitize_unicode, strip_surrogates

logger = logging.getLogger(__name__)


def _resolve_analysis_engine(analysis_engine: str | None = None) -> str:
    """Resolve a task-scoped engine without mutating process settings."""
    return analysis_engine or get_settings().app.analysis_engine


def get_enabled_rule_engine_items(analysis_engine: str | None = None) -> set[str]:
    """Return configured rule-engine item IDs."""
    if _resolve_analysis_engine(analysis_engine) not in {"rule", "hybrid"}:
        return set()
    return set(ITEM_SPECS)


def is_rule_engine_enabled_for_item(
    skill_id: str,
    analysis_engine: str | None = None,
) -> bool:
    """Return whether Rule-only mode should evaluate this item."""
    resolved_engine = _resolve_analysis_engine(analysis_engine)
    enabled = resolved_engine == "rule" and skill_id in ITEM_SPECS
    logger.info(
        "规则引擎模式判断: item=%s, analysis_engine=%s, registered=%s, enabled=%s",
        skill_id,
        resolved_engine,
        skill_id in ITEM_SPECS,
        enabled,
    )
    return enabled


def _rule_path_available(analysis_engine: str | None = None) -> bool:
    """Return whether the configured analysis mode may execute Rule."""
    return _resolve_analysis_engine(analysis_engine) in {"rule", "hybrid"}


def append_rule_engine_title_suffix(
    title: str,
    skill_id: str,
    lang: str = "en",
    analysis_engine: str | None = None,
) -> str:
    """Append a localized progress suffix when the item uses rule engine."""
    if not is_rule_engine_enabled_for_item(skill_id, analysis_engine):
        return title
    suffix = "（规则引擎解析）" if lang.startswith("zh") else " (Rule engine)"
    if title.endswith(suffix):
        return title
    return f"{title}{suffix}"


class RuleAppraiserAdapter:
    """Run rule-engine evaluation and return appraiser-compatible output."""

    def __init__(
        self,
        *,
        skill_id: str,
        skill_config: dict[str, Any],
        domain_id: int,
        lang: str,
        batch_size: int,
        cancellation_check=None,
        analysis_engine: str | None = None,
        gateway: LLMGateway | None = None,
        recovery: RecoveryBudget | None = None,
    ):
        self.gateway = gateway
        self.recovery = recovery
        self.skill_id = skill_id
        self.skill_config = skill_config
        self.domain_id = domain_id
        self.lang = lang
        self.batch_size = batch_size
        self.cancellation_check = cancellation_check
        self.analysis_engine = _resolve_analysis_engine(analysis_engine)

    @classmethod
    def from_evaluator(cls, evaluator) -> "RuleAppraiserAdapter":
        """Create an adapter from IterativeEvaluator without importing it here."""
        return cls(
            skill_id=evaluator.skill_id,
            skill_config=evaluator.skill_config,
            domain_id=evaluator.domain_id,
            lang=evaluator.lang,
            batch_size=get_settings().app.rule_engine_batch_size,
            cancellation_check=evaluator._raise_if_cancelled,
            analysis_engine=evaluator.analysis_engine,
            gateway=evaluator.gateway,
            recovery=evaluator.recovery,
        )

    def evaluate(self, semantic_chunks: list[SemanticChunk]) -> dict[str, Any]:
        """Run the rule-engine path for current item."""
        self._raise_if_cancelled()
        spec = get_item_spec(self.skill_id)
        iterator = EvidenceIterator(semantic_chunks, self.domain_id)
        domain_chunks = list(iterator)
        routing_result = WeightedChunkRouter().route(
            spec=spec,
            all_chunks=semantic_chunks,
            domain_chunks=domain_chunks,
        )
        if not routing_result.chunks:
            if isinstance(spec.rule, (RubricUnitRule, RubricAppraiserRule)) and semantic_chunks:
                fallback_limit = max(
                    1,
                    spec.rubric_appraiser.max_units
                    if spec.rubric_appraiser
                    else spec.rubric_unit.max_units
                    if spec.rubric_unit
                    else 10,
                )
                fallback_chunks = tuple(
                    self._to_evidence_chunk(chunk)
                    for chunk in semantic_chunks[:fallback_limit]
                )
                recovery_code = (
                    "rubric_appraiser_empty_route_recovery"
                    if isinstance(spec.rule, RubricAppraiserRule)
                    else "rubric_unit_empty_route_recovery"
                )
                routing_result = ChunkRoutingResult(
                    chunks=fallback_chunks,
                    routed_chunks=tuple(
                        RoutedChunkInfo(
                            chunk_id=chunk.chunk_id,
                            score=0.0,
                            reasons=(recovery_code,),
                        )
                        for chunk in fallback_chunks
                    ),
                    input_chunks=len(semantic_chunks),
                    domain_chunks=len(domain_chunks),
                    selected_chunks=len(fallback_chunks),
                    review_reasons=(
                        *routing_result.review_reasons,
                        ReviewReason(
                            code=recovery_code,
                            message="Rubric item had no routed chunks; passed leading chunks to rubric appraisal instead of returning no-evidence before classification.",
                            severity="warning",
                        ),
                    ),
                )
            else:
                logger.warning(
                    "规则引擎无可评价 chunks: item=%s, domain=%s, total=%s, domain_chunks=%s, routing=%s",
                    self.skill_id,
                    self.domain_id,
                    len(semantic_chunks),
                    len(domain_chunks),
                    spec.chunk_routing is not None,
                )
                return self._no_evidence_result(routing_result=routing_result)

        evidence_chunks = list(routing_result.chunks)
        chunk_by_id = {chunk.chunk_id: chunk for chunk in evidence_chunks}
        logger.info(
            "规则引擎开始评价: item=%s, profile=%s, domain=%s, total_chunks=%s, domain_chunks=%s, "
            "selected_chunks=%s, batch_size=%s, routing=%s",
            self.skill_id,
            spec.rule_profile,
            self.domain_id,
            len(semantic_chunks),
            len(domain_chunks),
            len(evidence_chunks),
            self.batch_size,
            spec.chunk_routing is not None,
        )

        llm_client = self._build_llm_client()
        pipeline_result = run_rule_pipeline(
            self.skill_id,
            evidence_chunks,
            llm_client=llm_client,
            batch_size=self.batch_size,
            lang=self.lang,
            cancellation_check=self.cancellation_check,
        )
        self._log_pipeline_result(spec.rule_profile, pipeline_result)
        if pipeline_result.rule_result.status == "error":
            raise ValueError(f"规则引擎推理失败: {pipeline_result.rule_result.review_reasons}")

        engine_score = self._engine_score(pipeline_result)
        if pipeline_result.rubric_appraiser_result is not None:
            explanation = self._deterministic_rubric_appraiser_explanation(pipeline_result, engine_score)
        elif pipeline_result.rubric_result is not None:
            explanation = self._deterministic_rubric_explanation(pipeline_result, engine_score)
        else:
            explanation_input = build_rule_explanation_input(
                spec,
                pipeline_result,
                chunk_by_id=chunk_by_id,
                scoring_principle=self._scoring_principle(),
            )
            logger.info(
                "规则引擎 explainer 输入: item=%s, matched=%s, missing=%s, needs_review=%s",
                self.skill_id,
                len(explanation_input.matched_criteria),
                len(explanation_input.missing_criteria),
                explanation_input.needs_review,
            )
            explanation = self._explain_rule_result(
                llm_client,
                explanation_input,
                lang=self.lang,
            )
        step2_evidence = self._step2_evidence_from_pipeline(pipeline_result)
        evidence_sources = self._evidence_sources_from_pipeline(pipeline_result)
        result = {
            "status": "success",
            "skill_id": self.skill_id,
            "domain": self.domain_id,
            "title": self.skill_config.get("title", ""),
            "chain_of_thought": {
                "step1_locate": explanation.step1_locate,
                "step2_evidence": step2_evidence,
                "step3_reasoning": explanation.step3_reasoning,
                "score": engine_score,
            },
            "evidence_sources": evidence_sources,
            "iteration_stats": {
                "total_chunks": len(evidence_chunks),
                "evidence_count": len(step2_evidence),
                "early_stopped": False,
                "rule_engine": True,
            },
            "rule_engine": self._serialize_rule_engine_output(
                pipeline_result,
                profile=spec.rule_profile,
                used=True,
                fallback_used=False,
                routing_result=routing_result,
            ),
        }
        return sanitize_unicode(result)

    def _explain_rule_result(
        self,
        llm_client: LiteLLMRuleClient,
        explanation_input: RuleExplanationInput,
        *,
        lang: str,
    ) -> RuleExplanationResult:
        """Generate an explanation without allowing explainer failure to alter scoring."""
        try:
            return LLMRuleExplainer(llm_client).explain(explanation_input, lang=lang)
        except CancellationRequested:
            raise
        except Exception:
            logger.exception("规则引擎 explainer 失败，使用确定性说明: item=%s", self.skill_id)
            return self._deterministic_explanation(explanation_input)

    def _engine_score(self, pipeline_result: RulePipelineResult) -> int:
        """Return the deterministic score selected by the rule engine."""
        allowed_scores = pipeline_result.rule_result.allowed_scores
        return max(allowed_scores) if allowed_scores else 1

    def _deterministic_explanation(self, payload: RuleExplanationInput) -> RuleExplanationResult:
        """Build a minimal appraiser-compatible explanation from rule output."""
        score = max(payload.allowed_scores) if payload.allowed_scores else 1
        evidence_quotes = [
            evidence.quote
            for criterion in payload.matched_criteria
            for evidence in criterion.evidence
            if evidence.quote
        ]
        evidence_chunks = sorted({
            evidence.chunk_id
            for criterion in payload.matched_criteria
            for evidence in criterion.evidence
        })
        if payload.matched_criteria:
            locate = "规则引擎定位到与本条目评分条件相关的证据。"
            summary = "规则引擎根据已验证证据完成结构化评分。"
        else:
            locate = "规则引擎未定位到满足本条目评分条件的有效证据。"
            summary = "未形成可计分的结构化证据。"

        return RuleExplanationResult(
            step1_locate=locate,
            step2_evidence=evidence_quotes,
            step3_reasoning={
                "summary": summary,
                "reasons": [
                    {
                        "point": "结构化规则判断",
                        "explanation": (
                            f"规则引擎匹配到 {len(payload.matched_criteria)} 项评分条件，"
                            f"缺失 {len(payload.missing_criteria)} 项关键条件，"
                            f"因此评分范围为 {payload.score_range}。"
                        ),
                        "evidence_chunks": evidence_chunks,
                    }
                ],
                "score_justification": f"最终分数由规则引擎评分范围 {payload.score_range} 决定。",
            },
            score=score,
        )

    def _deterministic_rubric_explanation(
        self,
        pipeline_result: RulePipelineResult,
        score: int,
    ) -> RuleExplanationResult:
        """Build an appraiser-compatible explanation from rubric-unit decisions."""
        rubric_result = pipeline_result.rubric_result
        rule_result = pipeline_result.rule_result
        if rubric_result is None or rubric_result.best_decision is None:
            return RuleExplanationResult(
                step1_locate="规则引擎未定位到满足本条目 rubric 的有效证据单元。",
                step2_evidence=[],
                step3_reasoning={
                    "summary": "未形成可计分的 rubric 证据单元。",
                    "reasons": [
                        {
                            "point": "Rubric 单元判断",
                            "explanation": f"未找到可支持本条目评分的证据单元，因此评分范围为 {rule_result.score_range}。",
                            "evidence_chunks": [],
                        }
                    ],
                    "score_justification": f"最终分数由规则引擎评分范围 {rule_result.score_range} 决定。",
                },
                score=score,
            )

        decision = rubric_result.best_decision
        unit = self._rubric_unit_by_id(pipeline_result).get(decision.unit_id)
        evidence_chunks = [unit.chunk_id] if unit is not None else []
        quote = unit.quote if unit is not None else ""
        return RuleExplanationResult(
            step1_locate="规则引擎定位到与本条目 rubric 相关的证据单元。",
            step2_evidence=[quote] if quote else [],
            step3_reasoning={
                "summary": "规则引擎基于完整证据单元完成 rubric 分类并映射评分。",
                "reasons": [
                    {
                        "point": decision.band,
                        "explanation": decision.reason,
                        "evidence_chunks": evidence_chunks,
                    }
                ],
                "score_justification": (
                    f"Rubric 分类为 {decision.band}，"
                    f"对应规则评分范围 {rule_result.score_range}。"
                ),
            },
            score=score,
        )

    def _deterministic_rubric_appraiser_explanation(
        self,
        pipeline_result: RulePipelineResult,
        score: int,
    ) -> RuleExplanationResult:
        """Build an appraiser-compatible explanation from a portfolio-level rubric decision."""
        rubric_result = pipeline_result.rubric_appraiser_result
        rule_result = pipeline_result.rule_result
        if rubric_result is None:
            return RuleExplanationResult(
                step1_locate="规则引擎未形成 portfolio rubric 判断。",
                step2_evidence=[],
                step3_reasoning={
                    "summary": "未形成 portfolio rubric 判断。",
                    "reasons": [],
                    "score_justification": "未形成有效判断，评分为 1。",
                },
                score=score,
            )

        decision = rubric_result.decision
        evidence_chunks = sorted({evidence.chunk_id for evidence in decision.evidence})
        locate = (
            "规则引擎定位到与本条目 rubric 相关的证据组合。"
            if decision.evidence
            else "规则引擎未定位到可支持本条目得分的有效引用证据。"
        )
        summary = decision.rationale or "规则引擎基于证据组合完成 item-level rubric 判断。"
        return RuleExplanationResult(
            step1_locate=locate,
            step2_evidence=[evidence.quote for evidence in decision.evidence],
            step3_reasoning={
                "summary": summary,
                "reasons": [
                    {
                        "point": decision.decision_label,
                        "explanation": summary,
                        "evidence_chunks": evidence_chunks,
                    }
                ],
                "score_justification": decision.why_not_higher
                or f"Rubric appraiser 直接给出 {rule_result.score_range} 分。",
            },
            score=score,
        )

    def _build_llm_client(self) -> LiteLLMRuleClient:
        return LiteLLMRuleClient(
            self.gateway or LLMGateway.from_settings(),
            recovery=self.recovery,
            cancellation_check=self.cancellation_check,
        )

    def _to_evidence_chunk(self, chunk: SemanticChunk) -> EvidenceChunk:
        return EvidenceChunk(
            chunk_id=strip_surrogates(chunk.chunk_id),
            title=strip_surrogates(chunk.title),
            content=strip_surrogates(chunk.content),
            metadata={
                "label": getattr(chunk.label, "value", str(chunk.label)),
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "section": chunk.section,
            },
        )

    def _scoring_principle(self) -> list[dict[str, str]]:
        rows = []
        for item in self.skill_config.get("scoring_principle", []):
            rows.append({
                "range": str(item.get("range", "")),
                "condition": str(item.get("condition", item.get("condition_zh", ""))),
            })
        return rows

    def _step2_evidence_from_pipeline(self, pipeline_result: RulePipelineResult) -> list[str]:
        """Build saved evidence from rubric units or verified rule-object quotes."""
        if pipeline_result.rubric_appraiser_result is not None:
            return [
                evidence.quote
                for evidence in pipeline_result.rubric_appraiser_result.decision.evidence
                if evidence.quote
            ]
        if pipeline_result.rubric_result is not None:
            seen: set[tuple[str, str]] = set()
            evidence: list[str] = []
            for unit in self._rubric_evidence_units(pipeline_result):
                quote = unit.quote.strip()
                if not quote:
                    continue
                key = (unit.chunk_id, quote)
                if key in seen:
                    continue
                seen.add(key)
                evidence.append(quote)
            return evidence
        return self._step2_evidence_from_rule_objects(pipeline_result)

    def _step2_evidence_from_rule_objects(self, pipeline_result: RulePipelineResult) -> list[str]:
        """Build saved evidence deterministically from verified rule-object quotes."""
        seen: set[tuple[str, str]] = set()
        evidence: list[str] = []
        for obj in pipeline_result.snapshot.objects:
            quote = obj.evidence.quote.strip()
            if not quote:
                continue
            key = (obj.evidence.chunk_id, quote)
            if key in seen:
                continue
            seen.add(key)
            evidence.append(quote)
        return evidence

    def _evidence_sources_from_pipeline(self, pipeline_result: RulePipelineResult) -> list[str]:
        """Return chunk IDs used by the scoring evidence."""
        if pipeline_result.rubric_appraiser_result is not None:
            return sorted({
                evidence.chunk_id
                for evidence in pipeline_result.rubric_appraiser_result.decision.evidence
            })
        if pipeline_result.rubric_result is not None:
            return sorted({unit.chunk_id for unit in self._rubric_evidence_units(pipeline_result)})
        return sorted({
            obj.evidence.chunk_id
            for obj in pipeline_result.snapshot.objects
        })

    def _rubric_unit_by_id(self, pipeline_result: RulePipelineResult) -> dict[str, Any]:
        rubric_result = pipeline_result.rubric_result
        if rubric_result is None:
            return {}
        return {unit.unit_id: unit for unit in rubric_result.candidate_units}

    def _rubric_evidence_units(self, pipeline_result: RulePipelineResult) -> list[Any]:
        rubric_result = pipeline_result.rubric_result
        if rubric_result is None:
            return []
        unit_by_id = self._rubric_unit_by_id(pipeline_result)
        ordered_units = []
        if rubric_result.best_decision is not None:
            best_unit = unit_by_id.get(rubric_result.best_decision.unit_id)
            if best_unit is not None:
                ordered_units.append(best_unit)
        for decision in rubric_result.decisions:
            if not decision.accepted or decision.score_range == "1":
                continue
            unit = unit_by_id.get(decision.unit_id)
            if unit is not None and unit not in ordered_units:
                ordered_units.append(unit)
        return ordered_units[:6]

    def _raise_if_cancelled(self) -> None:
        if self.cancellation_check is not None:
            self.cancellation_check()

    def _no_evidence_result(self, routing_result: ChunkRoutingResult | None = None) -> dict[str, Any]:
        reason_code = "no_domain_chunks"
        reason_message = "证据池中无该领域相关内容"
        if routing_result is not None and routing_result.domain_chunks > 0:
            reason_code = "no_routed_chunks"
            reason_message = "证据路由未选出可评价内容"

        result = {
            "status": "no_evidence",
            "skill_id": self.skill_id,
            "domain": self.domain_id,
            "title": self.skill_config.get("title", ""),
            "chain_of_thought": {
                "step1_locate": f"无法定位：{reason_message}",
                "step2_evidence": [],
                "step3_reasoning": f"由于{reason_message}，无法提取有效证据进行评价。根据 AGREE-II 评分原则，信息完全缺失时应评为最低分。",
                "score": 1,
            },
            "evidence_sources": [],
            "iteration_stats": {
                "total_chunks": 0,
                "evidence_count": 0,
                "early_stopped": False,
                "rule_engine": False,
            },
            "rule_engine": {
                "enabled": _rule_path_available(self.analysis_engine),
                "used": False,
                "fallback_used": False,
                "profile": get_item_spec(self.skill_id).rule_profile if self.skill_id in ITEM_SPECS else None,
                "score_range": "1",
                "allowed_scores": [1],
                "matched_rules": [],
                "missing": [],
                "needs_review": False,
                "review_reasons": [
                    {
                        "code": reason_code,
                        "message": reason_message,
                        "severity": "info",
                    }
                ],
                "objects": [],
            },
        }
        if routing_result is not None:
            result["rule_engine"]["routing"] = self._serialize_routing_result(routing_result)
        return sanitize_unicode(result)

    def _log_pipeline_result(self, profile: str, pipeline_result: RulePipelineResult) -> None:
        review_codes = [reason.code for reason in pipeline_result.rule_result.review_reasons]
        logger.info(
            "规则引擎推理完成: item=%s, profile=%s, objects=%s, valid_objects=%s, processed_chunks=%s, "
            "score_range=%s, allowed_scores=%s, matched=%s, missing=%s, needs_review=%s, review_codes=%s",
            self.skill_id,
            profile,
            len(pipeline_result.objects),
            len(pipeline_result.snapshot.objects),
            len(pipeline_result.snapshot.processed_chunks),
            pipeline_result.rule_result.score_range,
            pipeline_result.rule_result.allowed_scores,
            len(pipeline_result.rule_result.matched_rules),
            len(pipeline_result.rule_result.missing),
            pipeline_result.rule_result.needs_review,
            review_codes,
        )

    def _serialize_rule_engine_output(
        self,
        pipeline_result: RulePipelineResult | None,
        *,
        profile: str | None,
        used: bool,
        fallback_used: bool,
        error: str | None = None,
        routing_result: ChunkRoutingResult | None = None,
    ) -> dict[str, Any]:
        if pipeline_result is None:
            payload = {
                "enabled": _rule_path_available(self.analysis_engine),
                "used": used,
                "fallback_used": fallback_used,
                "profile": profile,
                "score_range": None,
                "allowed_scores": [],
                "matched_rules": [],
                "missing": [],
                "needs_review": True,
                "review_reasons": [],
                "objects": [],
                "error": error,
            }
            if routing_result is not None:
                payload["routing"] = self._serialize_routing_result(routing_result)
            return payload

        rule_result = pipeline_result.rule_result
        payload = {
            "enabled": _rule_path_available(self.analysis_engine),
            "used": used,
            "fallback_used": fallback_used,
            "profile": profile,
            "score_range": rule_result.score_range,
            "allowed_scores": rule_result.allowed_scores,
            "matched_rules": rule_result.matched_rules,
            "missing": rule_result.missing,
            "needs_review": rule_result.needs_review,
            "review_reasons": [asdict(reason) for reason in rule_result.review_reasons],
            "objects": [
                {
                    "type": obj.type,
                    "value": obj.value,
                    "chunk_id": obj.evidence.chunk_id,
                    "quote": obj.evidence.quote,
                    "confidence": obj.confidence,
                    "attributes": obj.attributes,
                }
                for obj in pipeline_result.snapshot.objects
            ],
            "verification": self._serialize_verification(pipeline_result),
        }
        if pipeline_result.grouping_result is not None:
            payload["grouping"] = self._serialize_grouping(pipeline_result)
        if pipeline_result.rubric_appraiser_result is not None:
            payload["rubric"] = self._serialize_rubric_appraiser(pipeline_result)
        elif pipeline_result.rubric_result is not None:
            payload["rubric"] = self._serialize_rubric(pipeline_result)
        if routing_result is not None:
            payload["routing"] = self._serialize_routing_result(routing_result)
        if error:
            payload["error"] = error
        return payload

    def _serialize_routing_result(self, routing_result: ChunkRoutingResult) -> dict[str, Any]:
        return {
            "input_chunks": routing_result.input_chunks,
            "domain_chunks": routing_result.domain_chunks,
            "selected_chunks": routing_result.selected_chunks,
            "chunks": [
                {
                    "chunk_id": info.chunk_id,
                    "score": info.score,
                    "reasons": list(info.reasons),
                }
                for info in routing_result.routed_chunks
            ],
            "review_reasons": [asdict(reason) for reason in routing_result.review_reasons],
        }

    def _serialize_verification(self, pipeline_result: RulePipelineResult) -> dict[str, Any]:
        decisions = pipeline_result.verification_decisions
        rejected = [decision for decision in decisions if not decision.accepted]
        return {
            "candidate_total": len(decisions),
            "candidate_accepted": len(decisions) - len(rejected),
            "candidate_rejected": len(rejected),
            "decisions": [asdict(decision) for decision in decisions],
        }

    def _serialize_grouping(self, pipeline_result: RulePipelineResult) -> dict[str, Any]:
        grouping_result = pipeline_result.grouping_result
        if grouping_result is None:
            return {
                "candidate_total": 0,
                "candidate_accepted": 0,
                "candidate_rejected": 0,
                "groups": [],
                "decisions": [],
                "review_reasons": [],
            }
        rejected = [decision for decision in grouping_result.decisions if not decision.accepted]
        decision_by_group_id = {decision.group_id: decision for decision in grouping_result.decisions}
        payload = {
            "candidate_total": len(grouping_result.candidate_groups),
            "candidate_accepted": len(grouping_result.accepted_groups),
            "candidate_rejected": len(rejected),
            "candidate_groups": [
                self._serialize_group(group, decision_by_group_id.get(group.group_id))
                for group in grouping_result.candidate_groups
            ],
            "groups": [
                self._serialize_group(group, decision_by_group_id.get(group.group_id))
                for group in grouping_result.accepted_groups
            ],
            "decisions": [asdict(decision) for decision in grouping_result.decisions],
            "review_reasons": [asdict(reason) for reason in grouping_result.review_reasons],
        }
        portfolio = self._serialize_portfolio_summary(grouping_result, decision_by_group_id)
        if portfolio is not None:
            payload["portfolio"] = portfolio
        return payload

    def _serialize_group(self, group, decision) -> dict[str, Any]:
        payload = {
            "group_id": group.group_id,
            "group_type": group.group_type,
            "chunk_ids": list(group.chunk_ids),
            "relation_hints": list(group.relation_hints),
            "attributes": group.attributes,
            "objects": [
                {
                    "type": obj.type,
                    "value": obj.value,
                    "chunk_id": obj.evidence.chunk_id,
                    "quote": obj.evidence.quote,
                    "confidence": obj.confidence,
                }
                for obj in group.objects
            ],
        }
        if decision is not None:
            payload["accepted"] = decision.accepted
            payload["rejection_code"] = decision.rejection_code
            payload["reason"] = decision.reason
        return payload

    def _serialize_portfolio_summary(self, grouping_result, decision_by_group_id: dict[str, Any]) -> dict[str, Any] | None:
        """Serialize recommendation-level coverage diagnostics for portfolio-scored items."""
        if self.skill_id == "item_11":
            return self._serialize_item11_portfolio(grouping_result, decision_by_group_id)
        if self.skill_id == "item_12":
            return self._serialize_item12_portfolio(grouping_result, decision_by_group_id)
        return None

    def _merged_group_attributes(self, group, decision_by_group_id: dict[str, Any]) -> dict[str, Any]:
        attrs = dict(group.attributes)
        decision = decision_by_group_id.get(group.group_id)
        if decision is not None:
            attrs.update(decision.attributes)
            attrs["verifier_accepted"] = decision.accepted
            attrs["verifier_rejection_code"] = decision.rejection_code
        return attrs

    def _serialize_item11_portfolio(self, grouping_result, decision_by_group_id: dict[str, Any]) -> dict[str, Any]:
        anchors: list[dict[str, Any]] = []
        generic_count = 0
        capped = False
        anchor_total = 0
        for group in grouping_result.candidate_groups:
            if group.group_type != "benefit_harm_balance":
                continue
            attrs = self._merged_group_attributes(group, decision_by_group_id)
            capped = capped or bool(attrs.get("portfolio_anchor_cap_exceeded"))
            anchor_total = max(anchor_total, self._safe_int(attrs.get("portfolio_anchor_total")))
            types = {obj.type for obj in group.objects}
            role = str(attrs.get("portfolio_role", "")).strip().casefold()
            if not role:
                role = "anchor" if "recommendation_or_option" in types else "global"
            if role != "anchor":
                if "generic_benefit_harm_method_statement" in types or "benefit_harm_balance_statement" in types:
                    generic_count += 1
                continue
            anchors.append(self._item11_anchor_status(group, attrs))
        omitted_count = max(0, anchor_total - len(anchors))
        return self._portfolio_counts(
            anchors,
            generic_count=generic_count,
            capped=capped,
            omitted_count=omitted_count,
        )

    def _serialize_item12_portfolio(self, grouping_result, decision_by_group_id: dict[str, Any]) -> dict[str, Any]:
        anchors: list[dict[str, Any]] = []
        generic_count = 0
        capped = False
        anchor_total = 0
        for group in grouping_result.candidate_groups:
            if group.group_type != "recommendation_evidence_link":
                continue
            attrs = self._merged_group_attributes(group, decision_by_group_id)
            capped = capped or bool(attrs.get("portfolio_anchor_cap_exceeded"))
            anchor_total = max(anchor_total, self._safe_int(attrs.get("portfolio_anchor_total")))
            types = {obj.type for obj in group.objects}
            role = str(attrs.get("portfolio_role", "")).strip().casefold()
            if not role:
                role = "anchor" if {"recommendation_statement", "recommendation_identifier"} & types else "global"
            if role != "anchor":
                if "general_evidence_method" in types or "recommendation_evidence_link_statement" in types:
                    generic_count += 1
                continue
            anchors.append(self._item12_anchor_status(group, attrs))
        omitted_count = max(0, anchor_total - len(anchors))
        return self._portfolio_counts(
            anchors,
            generic_count=generic_count,
            capped=capped,
            omitted_count=omitted_count,
        )

    def _portfolio_counts(
        self,
        anchors: list[dict[str, Any]],
        *,
        generic_count: int,
        capped: bool,
        omitted_count: int = 0,
    ) -> dict[str, Any]:
        return {
            "denominator": len(anchors) + omitted_count,
            "complete_count": len([item for item in anchors if item["status"] == "complete"]),
            "partial_count": len([item for item in anchors if item["status"] == "partial"]),
            "uncovered_count": len([item for item in anchors if item["status"] == "uncovered"]) + omitted_count,
            "generic_count": generic_count,
            "capped": capped,
            "omitted_count": omitted_count,
            "anchors": anchors,
        }

    def _item11_anchor_status(self, group, attrs: dict[str, Any]) -> dict[str, Any]:
        if self._item11_anchor_rejected(attrs):
            return self._anchor_payload(
                group,
                attrs,
                status="uncovered",
                missing=[
                    "health_benefit",
                    "harm_side_effect_or_risk",
                    "benefit_harm_balance_statement",
                ],
            )
        checks = {
            "health_benefit": self._group_bool_or_object(group, attrs, "has_benefit", {"health_benefit"}),
            "harm_side_effect_or_risk": self._group_bool_or_object(group, attrs, "has_harm_or_risk", {"harm_side_effect_or_risk"}),
            "benefit_supporting_data": self._group_bool_or_object(group, attrs, "benefit_has_supporting_data", {"benefit_supporting_data"}),
            "harm_supporting_data": self._group_bool_or_object(group, attrs, "harm_has_supporting_data", {"harm_supporting_data"}),
            "benefit_harm_balance_statement": self._group_bool_or_object(group, attrs, "has_explicit_balance", {"benefit_harm_balance_statement"}),
            "recommendation_impact_statement": self._group_bool_or_object(group, attrs, "balance_affects_recommendation", {"recommendation_impact_statement"}),
        }
        verifier_rejected = attrs.get("verifier_accepted") is False
        missing = [key for key, present in checks.items() if not present]
        if not missing and not verifier_rejected:
            status = "complete"
        elif checks["health_benefit"] or checks["harm_side_effect_or_risk"] or checks["benefit_harm_balance_statement"]:
            status = "partial"
        else:
            status = "uncovered"
        return self._anchor_payload(group, attrs, status=status, missing=missing)

    def _item12_anchor_status(self, group, attrs: dict[str, Any]) -> dict[str, Any]:
        if self._item12_anchor_rejected(attrs):
            return self._anchor_payload(
                group,
                attrs,
                status="uncovered",
                missing=["supporting_evidence_description"],
            )
        has_evidence = self._group_bool_or_object(
            group,
            attrs,
            "has_specific_supporting_evidence",
            {
                "supporting_evidence_description",
                "citation_group",
                "evidence_level_or_grade",
                "rationale_or_evidence_summary",
                "evidence_table_or_figure_reference",
                "recommendation_evidence_link_statement",
            },
        )
        link_type = str(attrs.get("link_type", "")).strip().casefold()
        same_unit = self._group_bool(attrs.get("same_evidence_unit", True))
        mapping_strength = str(attrs.get("mapping_strength", "")).strip().casefold()
        linked = (
            has_evidence
            and same_unit
            and link_type not in {"generic_method", "none"}
            and mapping_strength in {"partial", "systematic"}
        )
        if linked and mapping_strength == "systematic":
            status = "complete"
            missing: list[str] = []
        elif linked:
            status = "partial"
            missing = ["systematic_recommendation_evidence_mapping"]
        else:
            status = "uncovered"
            missing = ["supporting_evidence_description"]
        return self._anchor_payload(group, attrs, status=status, missing=missing)

    def _anchor_payload(self, group, attrs: dict[str, Any], *, status: str, missing: list[str]) -> dict[str, Any]:
        return {
            "group_id": group.group_id,
            "anchor_key": attrs.get("portfolio_anchor_key"),
            "status": status,
            "missing": missing,
            "chunk_ids": list(group.chunk_ids),
            "accepted": bool(attrs.get("verifier_accepted", False)),
        }

    def _item11_anchor_rejected(self, attrs: dict[str, Any]) -> bool:
        scope = str(attrs.get("scope", "")).strip().casefold()
        if scope and scope != "current_guideline":
            return True
        if attrs.get("verifier_accepted") is False and not self._group_bool(attrs.get("same_decision")):
            return True
        same_decision = attrs.get("same_decision")
        return same_decision is not None and not self._group_bool(same_decision)

    def _item12_anchor_rejected(self, attrs: dict[str, Any]) -> bool:
        scope = str(attrs.get("scope", "")).strip().casefold()
        if scope and scope != "current_guideline":
            return True
        if attrs.get("verifier_accepted") is False:
            return True
        same_recommendation = attrs.get("same_recommendation")
        if same_recommendation is not None and not self._group_bool(same_recommendation):
            return True
        same_unit = attrs.get("same_evidence_unit")
        return same_unit is not None and not self._group_bool(same_unit)

    def _group_bool_or_object(self, group, attrs: dict[str, Any], key: str, object_types: set[str]) -> bool:
        if key in attrs:
            return self._group_bool(attrs.get(key))
        return bool({obj.type for obj in group.objects} & object_types)

    def _group_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() == "true"

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _serialize_rubric(self, pipeline_result: RulePipelineResult) -> dict[str, Any]:
        rubric_result = pipeline_result.rubric_result
        if rubric_result is None:
            return {
                "candidate_total": 0,
                "candidate_accepted": 0,
                "candidate_rejected": 0,
                "best_unit_id": None,
                "units": [],
                "decisions": [],
                "review_reasons": [],
            }
        rejected = [decision for decision in rubric_result.decisions if not decision.accepted]
        return {
            "candidate_total": len(rubric_result.candidate_units),
            "candidate_accepted": len(rubric_result.accepted_units),
            "candidate_rejected": len(rejected),
            "best_unit_id": rubric_result.best_decision.unit_id if rubric_result.best_decision else None,
            "units": [asdict(unit) for unit in rubric_result.candidate_units],
            "decisions": [asdict(decision) for decision in rubric_result.decisions],
            "review_reasons": [asdict(reason) for reason in rubric_result.review_reasons],
        }

    def _serialize_rubric_appraiser(self, pipeline_result: RulePipelineResult) -> dict[str, Any]:
        rubric_result = pipeline_result.rubric_appraiser_result
        if rubric_result is None:
            return {
                "mode": "portfolio_appraiser",
                "candidate_total": 0,
                "candidate_accepted": 0,
                "candidate_rejected": 0,
                "decision_label": None,
                "confidence": 0.0,
                "evidence": [],
                "units": [],
                "review_reasons": [],
            }
        decision = rubric_result.decision
        status = (
            "error_scored"
            if any(reason.severity == "error" for reason in rubric_result.review_reasons)
            else "scored"
        )
        return {
            "mode": "portfolio_appraiser",
            "status": status,
            "candidate_total": len(rubric_result.candidate_units),
            "candidate_accepted": 1 if decision.score > 1 else 0,
            "candidate_rejected": 0 if decision.score > 1 else len(rubric_result.candidate_units),
            "decision_label": decision.decision_label,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "why_not_higher": decision.why_not_higher,
            "evidence": [asdict(evidence) for evidence in decision.evidence],
            "units": [asdict(unit) for unit in rubric_result.candidate_units],
            "review_reasons": [asdict(reason) for reason in rubric_result.review_reasons],
        }


def build_rule_engine_error_payload(
    skill_id: str,
    error: str,
    *,
    fallback_used: bool,
    analysis_engine: str | None = None,
    degraded: bool = False,
    failure_kind: str | None = None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """Build rule_engine JSON payload for failed rule path."""
    profile = ITEM_SPECS[skill_id].rule_profile if skill_id in ITEM_SPECS else None
    return {
        "enabled": _rule_path_available(analysis_engine),
        "used": False,
        "fallback_used": fallback_used,
        "profile": profile,
        "score_range": None,
        "allowed_scores": [],
        "matched_rules": [],
        "missing": [],
        "needs_review": True,
        "review_reasons": [],
        "objects": [],
        "error": error,
        "degraded": degraded,
        "failure_kind": failure_kind,
        "fallback_reason": fallback_reason,
    }
