"""LLM-backed rule-object group verifier."""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

from core.llm.json_output import TruncatedModelOutput
from core.llm.recovery import split_on_overflow, try_note_repair
from core.rule_engine.models import (
    EvidenceChunk,
    GroupVerificationDecision,
    ItemSpec,
    ReviewReason,
    RuleGroupingResult,
    RuleLLMClient,
    RuleObjectGroup,
)
from core.rule_engine.prompts.grouping import (
    SYSTEM_PROMPT,
    render_group_verification_prompt,
    render_group_verification_retry_prompt,
)


class LLMRuleObjectGroupVerifier:
    """Verify candidate object groups with a JSON LLM call."""

    def __init__(self, llm_client: RuleLLMClient, max_tokens: int = 12000):
        self.llm_client = llm_client
        self.max_tokens = max_tokens

    def verify(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        chunks: list[EvidenceChunk],
        *,
        lang: str = "zh",
    ) -> RuleGroupingResult:
        if not groups:
            return RuleGroupingResult(candidate_groups=[], accepted_groups=[], decisions=[])

        batch_size = self._batch_size(spec)
        if batch_size and len(groups) > batch_size:
            return self._verify_in_batches(spec, groups, chunks, batch_size=batch_size, lang=lang)

        return split_on_overflow(
            groups, lambda batch: self._verify_once(spec, batch, chunks, lang=lang), _merge_group_results,
            client=self.llm_client,
        )

    def _verify_once(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        chunks: list[EvidenceChunk],
        *,
        lang: str,
        index_offset: int = 0,
    ) -> RuleGroupingResult:
        schema = _decision_schema(spec, len(groups))
        try:
            payload = self.llm_client.complete_json(
                stage=f"RuleObjectGroupVerification:{spec.item_id}",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=render_group_verification_prompt(spec, groups, chunks, lang=lang),
                max_tokens=self.max_tokens,
                json_schema=schema,
                schema_name="rule_object_group_verification",
                list_wrapper="decisions",
            )
            return self._build_result(spec, groups, payload, index_offset=index_offset)
        except TruncatedModelOutput:
            raise
        except ValueError as exc:
            if not try_note_repair(self.llm_client, f"RuleObjectGroupVerificationRetry:{spec.item_id}"):
                raise exc
            payload = self.llm_client.complete_json(
                stage=f"RuleObjectGroupVerificationRetry:{spec.item_id}",
                system_prompt=SYSTEM_PROMPT,
                user_prompt=render_group_verification_retry_prompt(
                    spec,
                    groups,
                    chunks,
                    error_summary=_safe_error_summary(exc),
                    lang=lang,
                ),
                max_tokens=self.max_tokens,
                json_schema=schema,
                schema_name="rule_object_group_verification",
                list_wrapper="decisions",
            )
            return self._build_result(
                spec,
                groups,
                payload,
                retry_used=True,
                index_offset=index_offset,
            )

    def _verify_in_batches(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        chunks: list[EvidenceChunk],
        *,
        batch_size: int,
        lang: str,
    ) -> RuleGroupingResult:
        return _merge_group_results([
            (start, self.verify(spec, groups[start : start + batch_size], chunks, lang=lang))
            for start in range(0, len(groups), batch_size)
        ])

    def _build_result(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        payload: dict[str, Any],
        *,
        retry_used: bool = False,
        index_offset: int = 0,
    ) -> RuleGroupingResult:
        decisions_payload = payload.get("decisions")
        if not isinstance(decisions_payload, list):
            raise ValueError("Group verifier JSON field 'decisions' must be a list")

        decision_by_index = self._parse_decisions(decisions_payload)
        accepted_groups: list[RuleObjectGroup] = []
        decisions: list[GroupVerificationDecision] = []
        review_reasons: list[ReviewReason] = []

        if set(decision_by_index) - set(range(len(groups))):
            raise ValueError("Unknown candidate decision index")

        for index, group in enumerate(groups):
            raw_decision = decision_by_index.get(index)
            if raw_decision is None:
                raise ValueError(f"Group verifier missing decision for candidate index {index}")
            accepted = raw_decision["accepted"]
            attributes = raw_decision["attributes"]
            if accepted and spec.grouping and spec.grouping.group_profile == "health_question":
                required = {"same_question", "question_framing", "scope", "picos_present"}
                if (
                    not required <= attributes.keys()
                    or not isinstance(attributes.get("same_question"), bool)
                    or attributes.get("question_framing") not in {"background", "foreground", "implicit", "not_question"}
                    or attributes.get("scope") not in {"current_guideline", "external_or_cited", "evidence_selection_only", "unclear"}
                    or not isinstance(attributes.get("picos_present"), str)
                ):
                    raise ValueError("Accepted health-question group has invalid item 2 scoring attributes")
            merged_attributes = {**group.attributes, **attributes}
            decision = GroupVerificationDecision(
                group_id=group.group_id,
                group_type=group.group_type,
                accepted=accepted,
                confidence=raw_decision["confidence"],
                source="llm",
                rejection_code="" if accepted else raw_decision["rejection_code"],
                reason=raw_decision["reason"],
                group_index=index + index_offset,
                attributes=merged_attributes,
            )
            decisions.append(decision)
            if accepted:
                accepted_groups.append(replace(group, attributes=merged_attributes))

        if retry_used:
            review_reasons.insert(
                0,
                ReviewReason(
                    code="group_verification_json_retry",
                    message="Group verifier JSON/schema repair retry was used",
                    severity="warning",
                ),
            )

        return RuleGroupingResult(
            candidate_groups=groups,
            accepted_groups=accepted_groups,
            decisions=decisions,
            review_reasons=review_reasons,
        )

    def _batch_size(self, spec: ItemSpec) -> int | None:
        profile = spec.grouping.group_profile if spec.grouping else ""
        if profile in {"implementation_tool", "benefit_harm_balance", "recommendation_evidence_link"}:
            return 4
        return None

    def _parse_decisions(self, decisions_payload: list[Any]) -> dict[int, dict[str, Any]]:
        decisions: dict[int, dict[str, Any]] = {}
        for item in decisions_payload:
            if not isinstance(item, dict):
                raise ValueError("Group verifier decision must be an object")
            try:
                index = item.get("index")
                if type(index) is not int:
                    raise ValueError("Decision index must be an integer")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Group verifier decision has invalid index: {item.get('index')}") from exc
            if index in decisions:
                raise ValueError(f"Group verifier returned duplicate decision index: {index}")
            accepted = item.get("accepted")
            if not isinstance(accepted, bool):
                raise ValueError(f"Group verifier decision {index} accepted must be boolean")
            confidence = self._parse_confidence(item.get("confidence", 1.0), index)
            rejection_code = str(item.get("rejection_code", "")).strip()
            reason = str(item.get("reason", "")).strip()
            attributes = item.get("attributes") or {}
            if not isinstance(attributes, dict):
                raise ValueError(f"Group verifier decision {index} attributes must be an object")
            if not accepted and not rejection_code:
                rejection_code = "group_llm_rejected"
            decisions[index] = {
                "accepted": accepted,
                "confidence": confidence,
                "rejection_code": rejection_code,
                "reason": reason,
                "attributes": attributes,
            }
        return decisions

    def _parse_confidence(self, value: Any, index: int) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Group verifier decision {index} confidence is invalid: {value}") from exc
        if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
            raise ValueError(f"Group verifier decision {index} confidence must be between 0 and 1")
        return confidence


def _decision_schema(spec: ItemSpec, candidate_count: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "minItems": candidate_count,
                "maxItems": candidate_count,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "index",
                        "accepted",
                        "confidence",
                        "rejection_code",
                        "reason",
                        "attributes",
                    ],
                    "properties": {
                        "index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": max(0, candidate_count - 1),
                        },
                        "accepted": {"type": "boolean"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "rejection_code": {"type": "string", "maxLength": 80},
                        "reason": {"type": "string", "maxLength": 240},
                        "attributes": _attributes_schema(spec),
                    },
                },
            }
        },
    }


def _attributes_schema(spec: ItemSpec) -> dict[str, Any]:
    """Return a strict profile-specific schema for group rubric attributes."""
    profile = spec.grouping.group_profile if spec.grouping else ""
    if profile == "health_question":
        return _object_schema(
            {
                "same_question": {"type": "boolean"},
                "question_framing": _enum("background", "foreground", "implicit", "not_question"),
                "scope": _enum("current_guideline", "external_or_cited", "evidence_selection_only", "unclear"),
                "picos_present": {"type": "string"},
            }
        )
    if profile == "monitoring_criteria":
        return _object_schema(
            {
                "criterion_origin": _enum(
                    "current_guideline_recommendation",
                    "audit_or_quality_standard",
                    "external_or_cited",
                    "background_or_evidence",
                    "unclear",
                ),
                "measurement_purpose": _enum(
                    "measure_guideline_application",
                    "ongoing_use_or_care_target",
                    "diagnosis_or_initial_screening",
                    "acute_management",
                    "device_operation",
                    "study_outcome",
                    "guideline_update",
                    "unclear",
                ),
                "measurement_function": _enum(
                    "application_audit_measure",
                    "ongoing_care_target",
                    "recommendation_trigger_standard",
                    "clinical_episode_instruction",
                    "diagnostic_or_screening_definition",
                    "device_operation",
                    "cited_study_outcome",
                    "guideline_update",
                    "unclear",
                ),
                "criterion_kind": _enum(
                    "process_measure",
                    "behavioural_measure",
                    "clinical_or_health_outcome_measure",
                    "diagnostic_definition",
                    "treatment_action_only",
                    "not_applicable",
                ),
                "has_measure_target": {"type": "boolean"},
                "has_threshold_or_standard": {"type": "boolean"},
                "has_timing_or_frequency": {"type": "boolean"},
                "has_measurement_method": {"type": "boolean"},
                "same_measurement_criterion": {"type": "boolean"},
            }
        )
    if profile == "implementation_tool":
        return _object_schema(
            {
                "scope": _enum("current_guideline", "external_only", "unclear", "unrelated"),
                "rubric_role": _enum(
                    "concrete_tool",
                    "implementation_resource_package",
                    "implementation_support",
                    "clinical_content",
                    "not_applicable",
                ),
                "support_form": _enum(
                    "concrete_aid",
                    "implementation_resource_package",
                    "structured_program",
                    "protocol_process_ehr",
                    "implementation_table",
                    "clinical_practice_table",
                    "diagnostic_screening_risk_tool",
                    "drug_dose_table",
                    "evidence_table",
                    "general_discussion",
                    "external_resource",
                    "other",
                ),
                "implementation_connection": _enum(
                    "provided_or_recommended_for_this_guideline",
                    "embedded_or_linked_by_guideline",
                    "mentioned_only",
                    "external_or_cited",
                    "unclear",
                ),
                "implementation_purpose": _enum(
                    "put_guideline_recommendations_into_practice",
                    "clinical_management",
                    "background_or_evidence",
                    "unclear",
                ),
                "actionability": _enum(
                    "clear_tool_use_instruction",
                    "operational_elements",
                    "mentioned_only",
                    "none",
                ),
                "same_support_item": {"type": "boolean"},
            }
        )
    if profile == "application_factor":
        return _object_schema(
            {
                "scope": _enum(
                    "current_guideline_application",
                    "clinical_care_or_patient_management",
                    "background_or_context",
                    "outside_scope",
                    "unclear",
                ),
                "rubric_role": _enum(
                    "application_factor",
                    "clinical_content",
                    "resource_implication_only",
                    "not_applicable",
                ),
                "factor_direction": _enum("facilitator", "barrier", "mixed", "absent"),
                "factor_specificity": _enum("specific", "general", "absent"),
                "identification_basis": _enum(
                    "explicit_collection_or_analysis",
                    "expert_judgment",
                    "implementation_reasoning",
                    "absent",
                ),
                "application_impact": _enum(
                    "affects_recommendations_or_strategy",
                    "affects_implementation_strategy_or_tools",
                    "affects_implementation",
                    "general_context",
                    "none",
                ),
                "same_application_evidence_unit": {"type": "boolean"},
            }
        )
    if profile == "benefit_harm_balance":
        return _object_schema(
            {
                "scope": _enum("current_guideline", "external_or_background", "unclear"),
                "same_decision": {"type": "boolean"},
                "has_recommendation_or_option": {"type": "boolean"},
                "has_benefit": {"type": "boolean"},
                "has_harm_or_risk": {"type": "boolean"},
                "benefit_has_supporting_data": {"type": "boolean"},
                "harm_has_supporting_data": {"type": "boolean"},
                "has_explicit_balance": {"type": "boolean"},
                "balance_affects_recommendation": {"type": "boolean"},
            }
        )
    if profile == "recommendation_evidence_link":
        return _object_schema(
            {
                "scope": _enum("current_guideline", "external_or_background", "unclear"),
                "same_recommendation": {"type": "boolean"},
                "has_recommendation": {"type": "boolean"},
                "has_specific_supporting_evidence": {"type": "boolean"},
                "link_type": _enum(
                    "citation_group",
                    "evidence_level_or_grade",
                    "rationale",
                    "evidence_table_reference",
                    "explicit_link_statement",
                    "generic_method",
                    "none",
                ),
                "mapping_strength": _enum("systematic", "partial", "generic", "none"),
                "same_evidence_unit": {"type": "boolean"},
            }
        )
    return {
        "type": "object",
        "additionalProperties": {
            "type": ["string", "number", "boolean", "null"]
        },
    }


def _object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties.keys()),
        "properties": properties,
    }


def _enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _safe_error_summary(error: Exception) -> str:
    return str(error).replace("\n", " ")[:500]


def _merge_group_results(parts) -> RuleGroupingResult:
    """Concatenate batch results in order and shift decision indexes to the full candidate list."""
    return RuleGroupingResult(
        candidate_groups=[group for _, result in parts for group in result.candidate_groups],
        accepted_groups=[group for _, result in parts for group in result.accepted_groups],
        decisions=[replace(d, group_index=d.group_index + offset) for offset, result in parts for d in result.decisions],
        review_reasons=[reason for _, result in parts for reason in result.review_reasons],
    )
