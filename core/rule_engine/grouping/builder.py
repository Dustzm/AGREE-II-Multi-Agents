"""Deterministic candidate group builder for accepted rule objects."""
from __future__ import annotations

import re
from collections.abc import Iterable
from itertools import product

from core.rule_engine.models import (
    EvidenceChunk,
    ItemSpec,
    ObjectStoreSnapshot,
    RuleObject,
    RuleObjectGroup,
)
from core.rule_engine.text_normalization import normalize_text_for_source_match


class RuleObjectGroupBuilder:
    """Build high-recall candidate groups without deciding semantic validity."""

    _MONITORING_TYPES = (
        "indicator_or_standard",
        "measurement_frequency_or_interval",
        "measurement_method",
        "vague_monitoring_mention",
    )
    _FUNDING_STATUS_TYPES = (
        "no_funding_statement",
        "funding_disclosed",
        "commission_or_sponsorship_statement",
        "acknowledged_financial_support",
        "development_meeting_funding_statement",
        "related_meeting_or_convening_funding",
        "funding_section_heading",
        "funding_section_value",
    )
    _FUNDING_DETAIL_TYPES = (
        "direct_no_influence_statement",
        "funding_body_independence_statement",
        "sponsor_influence_statement",
        "no_industry_support_statement",
        "generic_editorial_independence_statement",
        "for_profit_funding",
        "non_profit_or_government_funding",
    )
    _FORMULATION_TYPES = (
        "formulation_method",
        "consensus_process_heading",
        "approval_or_finalization_statement",
        "voting_or_agreement_statement",
        "process_detail",
        "result_detail",
        "recommendation_impact",
    )
    _IMPLEMENTATION_ANCHOR_TYPES = (
        "eligible_implementation_tool",
        "clinical_algorithm_or_pathway",
        "patient_or_clinician_material",
        "appendix_or_template",
        "tool_like_content",
        "ineligible_tool_or_resource",
        "implementation_section_or_module",
        "implementation_advice_or_discussion",
    )
    _IMPLEMENTATION_DETAIL_TYPES = (
        "current_guideline_tool_connection",
        "tool_use_instruction",
        "implementation_section_or_module",
        "implementation_advice_or_discussion",
    )
    _RECOMMENDATION_SPECIFICITY_TYPES = (
        "recommendation_statement",
        "directive_language",
        "population_or_condition",
        "intervention_or_action",
        "purpose_or_context_detail",
        "dose_duration_or_operational_detail",
        "vague_or_unclear_recommendation",
    )
    _MANAGEMENT_OPTION_TYPES = (
        "clinical_issue_or_decision_point",
        "management_option",
        "option_condition_or_population",
        "preference_order",
        "selection_criteria",
        "decision_pathway_or_algorithm",
        "single_option_only",
    )
    _HEALTH_QUESTION_TYPES = (
        "health_question_statement",
        "implicit_question_scope",
        "question_population_or_problem",
        "question_intervention_or_exposure",
        "question_comparator",
        "question_outcome",
        "question_study_design",
    )
    _TARGET_USER_TYPES = (
        "clear_target_user",
        "vague_target_user",
        "clear_intended_use",
        "vague_intended_use",
    )
    _GUIDELINE_UPDATE_TYPES = (
        "update_mention",
        "future_update_mention",
        "update_interval",
        "update_interval_or_review_date",
        "update_criteria",
        "update_trigger_or_criteria",
        "update_condition",
        "update_responsible_body",
        "update_surveillance_or_process",
        "current_revision_only",
        "clinical_review_or_followup",
    )
    _APPLICATION_FACTOR_TYPES = (
        "facilitator",
        "barrier",
        "collection_method",
        "impact_on_guideline_or_recommendations",
    )
    _BENEFIT_HARM_TYPES = (
        "recommendation_or_option",
        "health_benefit",
        "harm_side_effect_or_risk",
        "benefit_supporting_data",
        "harm_supporting_data",
        "benefit_harm_balance_statement",
        "recommendation_impact_statement",
        "generic_benefit_harm_method_statement",
    )
    _RECOMMENDATION_EVIDENCE_LINK_TYPES = (
        "recommendation_statement",
        "recommendation_identifier",
        "supporting_evidence_description",
        "citation_group",
        "evidence_level_or_grade",
        "rationale_or_evidence_summary",
        "evidence_table_or_figure_reference",
        "recommendation_evidence_link_statement",
        "general_evidence_method",
    )

    def build(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        chunks: list[EvidenceChunk],
    ) -> list[RuleObjectGroup]:
        """Return deterministic candidate groups for the configured item."""
        grouping = spec.grouping
        if grouping is None or not snapshot.objects:
            return []

        chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        chunk_order = dict(snapshot.chunk_order)
        objects = [
            obj
            for obj in snapshot.objects
            if obj.type in self._eligible_object_types(spec)
        ]
        if not objects:
            return []

        if grouping.group_profile == "funding_disclosure":
            groups = self._build_funding_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "monitoring_criteria":
            groups = self._build_monitoring_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "recommendation_formulation":
            groups = self._build_formulation_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "implementation_tool":
            groups = self._build_implementation_tool_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "recommendation_specificity":
            groups = self._build_recommendation_specificity_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "management_options":
            groups = self._build_management_option_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "health_question":
            groups = self._build_health_question_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "application_factor":
            groups = self._build_application_factor_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "benefit_harm_balance":
            groups = self._build_benefit_harm_groups(spec, objects, chunk_by_id, chunk_order)
        elif grouping.group_profile == "recommendation_evidence_link":
            groups = self._build_recommendation_evidence_link_groups(spec, objects, chunk_by_id, chunk_order)
        else:
            groups = self._build_local_groups(spec, objects, chunk_by_id, chunk_order)

        if grouping.group_profile in {"benefit_harm_balance", "recommendation_evidence_link"}:
            groups = sorted(groups, key=self._portfolio_dedupe_priority)
        deduped = self._dedupe(groups)
        if grouping.group_profile == "recommendation_formulation":
            return self._rank_formulation_groups(deduped, chunk_order, grouping.max_groups)
        if grouping.group_profile in {"benefit_harm_balance", "recommendation_evidence_link"}:
            return self._rank_portfolio_groups(deduped, chunk_order, grouping.max_groups)
        ranked = sorted(
            deduped,
            key=lambda group: self._rank_key(group, chunk_order),
        )
        return ranked[: max(1, grouping.max_groups)]

    def _eligible_object_types(self, spec: ItemSpec) -> tuple[str, ...]:
        grouping = spec.grouping
        if grouping is None:
            return ()
        configured = (*grouping.anchor_object_types, *grouping.member_object_types)
        if configured:
            return tuple(dict.fromkeys(configured))
        return tuple(spec.object_specs)

    def _build_monitoring_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item21 groups from evidence units before falling back to local windows."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or tuple({obj.type for obj in objects})
        unit_by_object = {
            self._object_key(obj): self._evidence_unit_key(obj, chunk_by_id.get(obj.evidence.chunk_id))
            for obj in objects
        }
        objects_by_unit: dict[tuple[str, str], list[RuleObject]] = {}
        for obj in objects:
            objects_by_unit.setdefault(unit_by_object[self._object_key(obj)], []).append(obj)

        groups: list[RuleObjectGroup] = []
        group_index = 0
        for unit_key, unit_objects in objects_by_unit.items():
            for anchor in unit_objects:
                if anchor.type not in anchor_types:
                    continue
                members = list(unit_objects)
                members.extend(
                    self._monitoring_auxiliary_neighbors(
                        anchor,
                        objects,
                        unit_by_object=unit_by_object,
                        current_unit=unit_key,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
                candidate_members = self._monitoring_candidate_members(anchor, members, chunk_order)
                for member_group in candidate_members:
                    group_index += 1
                    groups.append(
                        self._make_group(
                            spec,
                            group_index,
                            member_group,
                            anchor=anchor,
                            chunk_by_id=chunk_by_id,
                            chunk_order=chunk_order,
                            extra_hints=("same_evidence_unit",),
                            extra_attributes={"evidence_unit": unit_key[1][:160]},
                        )
                    )

        return groups

    def _build_local_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or tuple({obj.type for obj in objects})
        groups: list[RuleObjectGroup] = []
        group_index = 0

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
            )
            candidate_members = self._generic_candidate_members(anchor, members, chunk_order)
            if not candidate_members:
                continue
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
        return groups

    def _build_formulation_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item10 groups around one current-recommendation formulation method."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or self._FORMULATION_TYPES
        groups: list[RuleObjectGroup] = []
        group_index = 0

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
            )
            candidate_members = self._formulation_candidate_members(anchor, members, chunk_order)
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
        return groups

    def _build_implementation_tool_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item19 groups around concrete or tool-like implementation aids."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or self._IMPLEMENTATION_ANCHOR_TYPES
        groups: list[RuleObjectGroup] = []
        group_index = 0

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
            )
            candidate_members = self._implementation_candidate_members(anchor, members, chunk_order)
            if not candidate_members:
                continue
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )

        return groups

    def _build_target_user_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item6 groups around one target-user or intended-use statement."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or self._TARGET_USER_TYPES
        groups: list[RuleObjectGroup] = []
        group_index = 0

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
            )
            candidate_members = self._target_user_candidate_members(anchor, members, chunk_order)
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
        return groups

    def _build_guideline_update_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item14 groups around one guideline-update procedure unit."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or self._GUIDELINE_UPDATE_TYPES
        groups: list[RuleObjectGroup] = []
        group_index = 0

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
            )
            candidate_members = self._guideline_update_candidate_members(anchor, members, chunk_order)
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
        return groups

    def _build_application_factor_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item18 groups around one guideline-application factor."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or ("facilitator", "barrier")
        groups: list[RuleObjectGroup] = []
        group_index = 0

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
            )
            candidate_members = self._application_factor_candidate_members(anchor, members, chunk_order)
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
        return groups

    def _build_benefit_harm_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item11 groups around one recommendation/option benefit-harm unit."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or (
            "recommendation_or_option",
            "benefit_harm_balance_statement",
        )
        groups: list[RuleObjectGroup] = []
        group_index = 0

        unit_by_object = {
            self._object_key(obj): self._evidence_unit_key(obj, chunk_by_id.get(obj.evidence.chunk_id))
            for obj in objects
        }
        objects_by_unit: dict[tuple[str, str], list[RuleObject]] = {}
        for obj in objects:
            objects_by_unit.setdefault(unit_by_object[self._object_key(obj)], []).append(obj)

        for unit_key, unit_objects in objects_by_unit.items():
            anchors = [obj for obj in unit_objects if obj.type in anchor_types]
            recommendation_anchor_count = len(
                [obj for obj in anchors if obj.type == "recommendation_or_option"]
            )
            if not anchors and any(obj.type in {"health_benefit", "harm_side_effect_or_risk"} for obj in unit_objects):
                anchors = [obj for obj in unit_objects if obj.type in {"health_benefit", "harm_side_effect_or_risk"}][:1]
            if not anchors and any(obj.type == "generic_benefit_harm_method_statement" for obj in unit_objects):
                anchors = [obj for obj in unit_objects if obj.type == "generic_benefit_harm_method_statement"][:1]
            for anchor in anchors:
                members = list(unit_objects)
                members.extend(
                    self._local_heading_neighbors(
                        anchor,
                        objects,
                        unit_by_object=unit_by_object,
                        current_unit=unit_key,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                        allowed_types=self._BENEFIT_HARM_TYPES,
                    )
                )
                members.extend(
                    self._same_table_row_neighbors(
                        anchor,
                        objects,
                        chunk_by_id=chunk_by_id,
                        allowed_types=self._BENEFIT_HARM_TYPES,
                    )
                )
                candidate_members = self._benefit_harm_candidate_members(anchor, members, chunk_order)
                for member_group in candidate_members:
                    group_index += 1
                    has_candidate_evidence = any(
                        obj.type
                        in {
                            "health_benefit",
                            "harm_side_effect_or_risk",
                            "benefit_supporting_data",
                            "harm_supporting_data",
                            "benefit_harm_balance_statement",
                            "recommendation_impact_statement",
                        }
                        for obj in member_group
                    )
                    portfolio_role = "global"
                    if anchor.type == "recommendation_or_option":
                        portfolio_role = "anchor" if has_candidate_evidence else "global"
                    groups.append(
                        self._make_group(
                            spec,
                            group_index,
                            member_group,
                            anchor=anchor,
                            chunk_by_id=chunk_by_id,
                            chunk_order=chunk_order,
                            extra_hints=("same_evidence_unit",),
                            extra_attributes={
                                "evidence_unit": unit_key[1][:160],
                                "portfolio_anchor_key": self._portfolio_anchor_key(anchor, chunk_order),
                                "portfolio_role": portfolio_role,
                                "has_candidate_evidence": has_candidate_evidence,
                            },
                        )
                    )
        return groups

    def _build_recommendation_evidence_link_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item12 groups around one recommendation-to-evidence mapping unit."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or (
            "recommendation_statement",
            "recommendation_identifier",
        )
        groups: list[RuleObjectGroup] = []
        group_index = 0

        unit_by_object = {
            self._object_key(obj): self._evidence_unit_key(obj, chunk_by_id.get(obj.evidence.chunk_id))
            for obj in objects
        }
        objects_by_unit: dict[tuple[str, str], list[RuleObject]] = {}
        for obj in objects:
            objects_by_unit.setdefault(unit_by_object[self._object_key(obj)], []).append(obj)

        for unit_key, unit_objects in objects_by_unit.items():
            recommendation_anchors = [obj for obj in unit_objects if obj.type == "recommendation_statement"]
            identifier_anchors = [obj for obj in unit_objects if obj.type == "recommendation_identifier"]
            if "recommendation_statement" in anchor_types and recommendation_anchors:
                anchors = recommendation_anchors
            elif "recommendation_identifier" in anchor_types:
                anchors = identifier_anchors
            else:
                anchors = []
            if not anchors:
                anchors = [
                    obj
                    for obj in unit_objects
                    if obj.type in {"recommendation_evidence_link_statement", "general_evidence_method"}
                ][:1]
            for anchor in anchors:
                members = list(unit_objects)
                members.extend(
                    self._local_heading_neighbors(
                        anchor,
                        objects,
                        unit_by_object=unit_by_object,
                        current_unit=unit_key,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                        allowed_types=self._RECOMMENDATION_EVIDENCE_LINK_TYPES,
                    )
                )
                candidate_members = self._recommendation_evidence_candidate_members(anchor, members, chunk_order)
                for member_group in candidate_members:
                    group_index += 1
                    has_candidate_evidence = any(
                        obj.type
                        in {
                            "supporting_evidence_description",
                            "citation_group",
                            "evidence_level_or_grade",
                            "rationale_or_evidence_summary",
                            "evidence_table_or_figure_reference",
                            "recommendation_evidence_link_statement",
                        }
                        for obj in member_group
                    )
                    portfolio_role = "global"
                    if anchor.type == "recommendation_statement":
                        portfolio_role = "anchor"
                    elif (
                        anchor.type == "recommendation_identifier"
                        and has_candidate_evidence
                        and self._is_formal_recommendation_identifier(anchor)
                    ):
                        portfolio_role = "anchor"
                    groups.append(
                        self._make_group(
                            spec,
                            group_index,
                            member_group,
                            anchor=anchor,
                            chunk_by_id=chunk_by_id,
                            chunk_order=chunk_order,
                            extra_hints=("same_evidence_unit",),
                            extra_attributes={
                                "evidence_unit": unit_key[1][:160],
                                "portfolio_anchor_key": self._portfolio_anchor_key(anchor, chunk_order),
                                "portfolio_role": portfolio_role,
                                "has_candidate_evidence": has_candidate_evidence,
                            },
                        )
                    )
        return groups

    def _build_recommendation_specificity_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item15 groups around one recommendation evidence unit."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or ("recommendation_statement", "directive_language")
        unit_by_object = {
            self._object_key(obj): self._evidence_unit_key(obj, chunk_by_id.get(obj.evidence.chunk_id))
            for obj in objects
        }
        objects_by_unit: dict[tuple[str, str], list[RuleObject]] = {}
        for obj in objects:
            objects_by_unit.setdefault(unit_by_object[self._object_key(obj)], []).append(obj)

        groups: list[RuleObjectGroup] = []
        group_index = 0
        for unit_key, unit_objects in objects_by_unit.items():
            anchors = [obj for obj in unit_objects if obj.type in anchor_types]
            if not anchors and any(obj.type == "vague_or_unclear_recommendation" for obj in unit_objects):
                anchors = [obj for obj in unit_objects if obj.type == "vague_or_unclear_recommendation"]
            if not anchors:
                unit_types = {obj.type for obj in unit_objects}
                if {"population_or_condition", "intervention_or_action"} <= unit_types:
                    anchors = [
                        obj
                        for obj in unit_objects
                        if obj.type in {"population_or_condition", "intervention_or_action"}
                    ][:1]
            for anchor in anchors:
                members = list(unit_objects)
                if self._is_recommendation_heading(chunk_by_id.get(anchor.evidence.chunk_id)):
                    nearby = self._nearby_objects(
                        anchor,
                        objects,
                        neighbor_window=grouping.neighbor_window,
                        same_section=grouping.same_section,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                    members.extend(
                        obj
                        for obj in nearby
                        if unit_by_object.get(self._object_key(obj)) != unit_key
                        and obj.evidence.chunk_id != anchor.evidence.chunk_id
                        and obj.type in self._RECOMMENDATION_SPECIFICITY_TYPES
                    )
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        members,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                        extra_hints=("same_evidence_unit",),
                        extra_attributes={"evidence_unit": unit_key[1][:160]},
                    )
                )
        return groups

    def _build_management_option_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item16 groups around one clinical decision point or local option cluster."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or (
            "clinical_issue_or_decision_point",
            "management_option",
            "decision_pathway_or_algorithm",
        )
        groups: list[RuleObjectGroup] = []
        group_index = 0

        unit_by_object = {
            self._object_key(obj): self._evidence_unit_key(obj, chunk_by_id.get(obj.evidence.chunk_id))
            for obj in objects
        }
        objects_by_unit: dict[tuple[str, str], list[RuleObject]] = {}
        for obj in objects:
            objects_by_unit.setdefault(unit_by_object[self._object_key(obj)], []).append(obj)

        for unit_key, unit_objects in objects_by_unit.items():
            unit_types = {obj.type for obj in unit_objects}
            option_count = len([obj for obj in unit_objects if obj.type == "management_option"])
            if option_count >= 2 or {
                "clinical_issue_or_decision_point",
                "decision_pathway_or_algorithm",
            } & unit_types:
                anchors = [obj for obj in unit_objects if obj.type in anchor_types] or unit_objects[:1]
                for anchor in anchors[:2]:
                    group_index += 1
                    groups.append(
                        self._make_group(
                            spec,
                            group_index,
                            unit_objects,
                            anchor=anchor,
                            chunk_by_id=chunk_by_id,
                            chunk_order=chunk_order,
                            extra_hints=("same_evidence_unit",),
                            extra_attributes={"evidence_unit": unit_key[1][:160]},
                        )
                    )

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
            )
            candidate_members = self._management_option_candidate_members(anchor, members, chunk_order)
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
        return groups

    def _build_health_question_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        """Build item2 groups around one explicit or implicit health-question unit."""
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or ("health_question_statement", "implicit_question_scope")
        explicit_anchor_types = {"health_question_statement", "implicit_question_scope"}
        unit_by_object = {
            self._object_key(obj): self._evidence_unit_key(obj, chunk_by_id.get(obj.evidence.chunk_id))
            for obj in objects
        }
        objects_by_unit: dict[tuple[str, str], list[RuleObject]] = {}
        for obj in objects:
            objects_by_unit.setdefault(unit_by_object[self._object_key(obj)], []).append(obj)

        groups: list[RuleObjectGroup] = []
        group_index = 0
        for unit_key, unit_objects in objects_by_unit.items():
            anchors = [
                obj
                for obj in unit_objects
                if obj.type in explicit_anchor_types and obj.type in anchor_types
            ]
            if not anchors:
                picos = [
                    obj
                    for obj in unit_objects
                    if obj.type in self._HEALTH_QUESTION_TYPES
                    and obj.type not in {"health_question_statement", "implicit_question_scope"}
                ]
                if len({obj.type for obj in picos}) >= 2 and any(obj.type in anchor_types for obj in picos):
                    anchors = picos[:1]
            for anchor in anchors:
                members = list(unit_objects)
                members.extend(
                    self._health_question_heading_neighbors(
                        anchor,
                        objects,
                        unit_by_object=unit_by_object,
                        current_unit=unit_key,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        members,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                        extra_hints=("same_evidence_unit",),
                        extra_attributes={"evidence_unit": unit_key[1][:160]},
                    )
                )
        return groups

    def _build_funding_groups(
        self,
        spec: ItemSpec,
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObjectGroup]:
        grouping = spec.grouping
        if grouping is None:
            return []
        anchor_types = grouping.anchor_object_types or self._FUNDING_STATUS_TYPES
        groups: list[RuleObjectGroup] = []
        group_index = 0

        for anchor in objects:
            if anchor.type not in anchor_types:
                continue
            members = self._nearby_objects(
                anchor,
                objects,
                neighbor_window=grouping.neighbor_window,
                same_section=grouping.same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
                allow_heading_value_exception=True,
            )
            members = self._include_heading_value_pair(anchor, members, objects, chunk_by_id, chunk_order, grouping.neighbor_window)
            candidate_members = self._funding_candidate_members(anchor, members, chunk_order)
            if not candidate_members:
                continue
            for member_group in candidate_members:
                group_index += 1
                groups.append(
                    self._make_group(
                        spec,
                        group_index,
                        member_group,
                        anchor=anchor,
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )

        if not groups:
            detail_only = [
                obj
                for obj in objects
                if obj.type in self._FUNDING_DETAIL_TYPES
            ]
            if detail_only:
                groups.append(
                    self._make_group(
                        spec,
                        1,
                        detail_only,
                        anchor=detail_only[0],
                        chunk_by_id=chunk_by_id,
                        chunk_order=chunk_order,
                    )
                )
        return groups

    def _nearby_objects(
        self,
        anchor: RuleObject,
        objects: list[RuleObject],
        *,
        neighbor_window: int,
        same_section: bool,
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
        allow_heading_value_exception: bool = False,
    ) -> list[RuleObject]:
        anchor_index = self._chunk_index(anchor.evidence.chunk_id, chunk_order)
        members = [
            obj
            for obj in objects
            if abs(self._chunk_index(obj.evidence.chunk_id, chunk_order) - anchor_index) <= neighbor_window
            and self._is_groupable_with_anchor(
                anchor,
                obj,
                same_section=same_section,
                chunk_by_id=chunk_by_id,
                chunk_order=chunk_order,
                allow_heading_value_exception=allow_heading_value_exception,
            )
        ]
        return self._sort_objects(members, chunk_order)

    def _include_heading_value_pair(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        objects: list[RuleObject],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
        neighbor_window: int,
    ) -> list[RuleObject]:
        if anchor.type not in {"funding_section_heading", "funding_section_value"}:
            return members
        anchor_index = self._chunk_index(anchor.evidence.chunk_id, chunk_order)
        paired = [
            obj
            for obj in objects
            if obj.type in {"funding_section_heading", "funding_section_value"}
            and abs(self._chunk_index(obj.evidence.chunk_id, chunk_order) - anchor_index) <= max(1, neighbor_window)
            and self._is_heading_value_pair(anchor, obj, chunk_by_id, chunk_order)
        ]
        return self._sort_objects([*members, *paired], chunk_order)

    def _monitoring_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item21 candidate groups around one anchor object."""
        if anchor.type == "vague_monitoring_mention":
            return [[anchor]]

        indicators = self._nearest_by_type(anchor, members, self._MONITORING_TYPES[0], chunk_order)
        frequencies = self._nearest_by_type(anchor, members, self._MONITORING_TYPES[1], chunk_order)
        methods = self._nearest_by_type(anchor, members, self._MONITORING_TYPES[2], chunk_order)

        if anchor.type == self._MONITORING_TYPES[0]:
            indicators = [anchor]
        if anchor.type == self._MONITORING_TYPES[1]:
            frequencies = [anchor]

        groups: list[list[RuleObject]] = []
        if indicators and frequencies and methods:
            groups.extend([self._dedupe_objects(items) for items in product(indicators, frequencies, methods)])
        if indicators and frequencies:
            groups.extend([self._dedupe_objects(items) for items in product(indicators, frequencies)])
        if indicators and methods:
            groups.extend([self._dedupe_objects(items) for items in product(indicators, methods)])
        if indicators:
            groups.extend([[indicator] for indicator in indicators])
        if frequencies:
            groups.extend([[frequency] for frequency in frequencies])
        return self._dedupe_member_groups(groups)

    def _monitoring_auxiliary_neighbors(
        self,
        anchor: RuleObject,
        objects: list[RuleObject],
        *,
        unit_by_object: dict[tuple[str, str, str, str], tuple[str, str]],
        current_unit: tuple[str, str],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObject]:
        neighbor_window = 1
        same_section = True
        nearby = self._nearby_objects(
            anchor,
            objects,
            neighbor_window=neighbor_window,
            same_section=same_section,
            chunk_by_id=chunk_by_id,
            chunk_order=chunk_order,
        )
        return [
            obj
            for obj in nearby
            if unit_by_object.get(self._object_key(obj)) != current_unit
            and (
                (
                    obj.type == "measurement_method"
                    and self._is_auxiliary_monitoring_method(obj)
                )
                or (
                    obj.type in self._MONITORING_TYPES
                    and obj.evidence.chunk_id != anchor.evidence.chunk_id
                    and (
                        self._is_monitoring_heading(chunk_by_id.get(anchor.evidence.chunk_id))
                        or self._is_monitoring_heading(chunk_by_id.get(obj.evidence.chunk_id))
                    )
                )
            )
        ]

    def _health_question_heading_neighbors(
        self,
        anchor: RuleObject,
        objects: list[RuleObject],
        *,
        unit_by_object: dict[tuple[str, str, str, str], tuple[str, str]],
        current_unit: tuple[str, str],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> list[RuleObject]:
        if not self._is_health_question_heading(chunk_by_id.get(anchor.evidence.chunk_id)):
            return []
        nearby = self._nearby_objects(
            anchor,
            objects,
            neighbor_window=1,
            same_section=True,
            chunk_by_id=chunk_by_id,
            chunk_order=chunk_order,
        )
        return [
            obj
            for obj in nearby
            if unit_by_object.get(self._object_key(obj)) != current_unit
            and obj.evidence.chunk_id != anchor.evidence.chunk_id
            and obj.type in self._HEALTH_QUESTION_TYPES
        ]

    def _local_heading_neighbors(
        self,
        anchor: RuleObject,
        objects: list[RuleObject],
        *,
        unit_by_object: dict[tuple[str, str, str, str], tuple[str, str]],
        current_unit: tuple[str, str],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
        allowed_types: tuple[str, ...],
    ) -> list[RuleObject]:
        anchor_chunk = chunk_by_id.get(anchor.evidence.chunk_id)
        if not (
            self._is_recommendation_heading(anchor_chunk)
            or self._is_evidence_link_heading(anchor_chunk)
            or self._is_benefit_harm_heading(anchor_chunk)
        ):
            return []
        nearby = self._nearby_objects(
            anchor,
            objects,
            neighbor_window=1,
            same_section=True,
            chunk_by_id=chunk_by_id,
            chunk_order=chunk_order,
        )
        return [
            obj
            for obj in nearby
            if unit_by_object.get(self._object_key(obj)) != current_unit
            and obj.evidence.chunk_id != anchor.evidence.chunk_id
            and obj.type in allowed_types
        ]

    def _same_table_row_neighbors(
        self,
        anchor: RuleObject,
        objects: list[RuleObject],
        *,
        chunk_by_id: dict[str, EvidenceChunk],
        allowed_types: tuple[str, ...],
    ) -> list[RuleObject]:
        chunk = chunk_by_id.get(anchor.evidence.chunk_id)
        if chunk is None or "<tr" not in (chunk.content or "").casefold():
            return []
        anchor_row = self._table_row_unit(chunk.content or "", anchor.evidence.quote)
        if not anchor_row:
            return []
        return [
            obj
            for obj in objects
            if obj.type in allowed_types
            and obj.evidence.chunk_id == anchor.evidence.chunk_id
            and not self._same_object(anchor, obj)
            and self._contains_normalized(anchor_row, obj.evidence.quote)
        ]

    def _funding_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item22 candidate disclosure groups around one anchor object."""
        detail_types = (
            "funding_section_heading",
            "funding_section_value",
            "direct_no_influence_statement",
            "funding_body_independence_statement",
            "sponsor_influence_statement",
            "no_industry_support_statement",
            "generic_editorial_independence_statement",
            "for_profit_funding",
            "non_profit_or_government_funding",
        )
        selected = [anchor]
        for object_type in detail_types:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        selected = self._dedupe_objects(selected)
        if not selected:
            return []
        return [selected]

    def _formulation_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item10 groups without mixing distant process fragments."""
        method_like_types = (
            "formulation_method",
            "consensus_process_heading",
            "approval_or_finalization_statement",
            "voting_or_agreement_statement",
        )
        method_like = [anchor] if anchor.type in method_like_types else []
        for object_type in method_like_types:
            if object_type == anchor.type:
                continue
            method_like.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        if not method_like:
            return []

        processes = self._nearest_by_type(anchor, members, "process_detail", chunk_order)
        results = self._nearest_by_type(anchor, members, "result_detail", chunk_order)
        impacts = self._nearest_by_type(anchor, members, "recommendation_impact", chunk_order)

        groups: list[list[RuleObject]] = [[method] for method in method_like]
        for method in method_like:
            groups.extend(self._dedupe_objects((method, process)) for process in processes)
            groups.extend(self._dedupe_objects((method, result)) for result in results)
            for process, result in product(processes, results):
                groups.append(self._dedupe_objects((method, process, result)))
            for process, result, impact in product(processes, results, impacts):
                groups.append(self._dedupe_objects((method, process, result, impact)))
        return self._dedupe_member_groups(groups)

    def _implementation_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item19 candidate groups around one tool or support statement."""
        selected = [anchor]
        detail_types = list(self._IMPLEMENTATION_DETAIL_TYPES)
        if anchor.type in {"implementation_section_or_module", "implementation_advice_or_discussion"}:
            detail_types.extend(
                object_type
                for object_type in self._IMPLEMENTATION_ANCHOR_TYPES
                if object_type not in detail_types
            )
        for object_type in detail_types:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        return [self._dedupe_objects(selected)] if selected else []

    def _target_user_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item6 groups around guideline audience/use evidence."""
        selected = [anchor]
        for object_type in self._TARGET_USER_TYPES:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        return [self._dedupe_objects(selected)]

    def _guideline_update_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item14 candidates without combining distant update fragments."""
        selected = [anchor]
        for object_type in self._GUIDELINE_UPDATE_TYPES:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        return [self._dedupe_objects(selected)]

    def _application_factor_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item18 candidates around one facilitator or barrier."""
        selected = [anchor]
        for object_type in self._APPLICATION_FACTOR_TYPES:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        return [self._dedupe_objects(selected)]

    def _benefit_harm_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item11 candidate groups around one decision point."""
        selected = [anchor]
        for object_type in self._BENEFIT_HARM_TYPES:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        selected = self._dedupe_objects(selected)
        types = {obj.type for obj in selected}
        if (
            "recommendation_or_option" in types
            or "benefit_harm_balance_statement" in types
            or "generic_benefit_harm_method_statement" in types
            or {"health_benefit", "harm_side_effect_or_risk"} & types
        ):
            return [selected]
        return []

    def _recommendation_evidence_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build compact item12 candidate groups around one recommendation."""
        selected = [anchor]
        for object_type in self._RECOMMENDATION_EVIDENCE_LINK_TYPES:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        selected = self._dedupe_objects(selected)
        evidence_types = set(self._RECOMMENDATION_EVIDENCE_LINK_TYPES) - {
            "recommendation_statement",
            "recommendation_identifier",
        }
        types = {obj.type for obj in selected}
        if {"recommendation_statement", "recommendation_identifier"} & types:
            return [selected]
        if {"recommendation_evidence_link_statement", "general_evidence_method"} & types:
            return [selected]
        return []

    def _is_formal_recommendation_identifier(self, obj: RuleObject) -> bool:
        """Return whether an identifier can stand in for a recommendation statement."""
        formal_patterns = (
            r"(recommendation|recommendations|statement|statements|rec)\s*[#:.\- ]*\s*[a-z]?[0-9]+[a-z]?(?:\.[0-9a-z]+)*",
            r"[0-9]+[a-z]\.[0-9]+(?:\.[0-9a-z]+)*",
            r"[a-z][0-9]+(?:\.[0-9a-z]+)+",
            r"r[0-9]+(?:\.[0-9a-z]+)*",
        )
        candidates = [
            normalize_text_for_source_match(obj.value),
            normalize_text_for_source_match(obj.evidence.quote),
        ]
        for text in candidates:
            if not text:
                continue
            if re.fullmatch(r"\[[0-9,\-\s;]+\]", text):
                continue
            if re.fullmatch(r"(grade|level|class|certainty|quality)\s+[a-z0-9.+-]+", text):
                continue
            if re.fullmatch(r"[0-9]+", text):
                continue
            if any(re.fullmatch(pattern, text) for pattern in formal_patterns):
                return True
        return False

    def _rank_portfolio_groups(
        self,
        groups: list[RuleObjectGroup],
        chunk_order: dict[str, int],
        max_groups: int,
    ) -> list[RuleObjectGroup]:
        """Rank portfolio groups while preserving uncovered recommendation anchors."""
        ranked = sorted(groups, key=lambda group: self._rank_key(group, chunk_order))
        limit = max(1, max_groups)
        if len(ranked) <= limit:
            return ranked

        kept = ranked[:limit]
        anchor_keys = {
            self._group_portfolio_anchor_key(group)
            for group in ranked
            if self._group_portfolio_anchor_key(group)
        }
        kept_anchor_keys = {
            self._group_portfolio_anchor_key(group)
            for group in kept
            if self._group_portfolio_anchor_key(group)
        }
        capped = []
        for group in kept:
            attributes = {
                **group.attributes,
                "portfolio_anchor_cap_exceeded": True,
                "portfolio_candidate_total": len(ranked),
                "portfolio_candidate_kept": limit,
                "portfolio_anchor_total": len(anchor_keys),
                "portfolio_anchor_kept": len(kept_anchor_keys),
                "portfolio_anchor_omitted": max(0, len(anchor_keys) - len(kept_anchor_keys)),
            }
            capped.append(
                RuleObjectGroup(
                    group_id=group.group_id,
                    group_type=group.group_type,
                    objects=group.objects,
                    chunk_ids=group.chunk_ids,
                    relation_hints=group.relation_hints,
                    attributes=attributes,
                )
            )
        return capped

    def _group_portfolio_anchor_key(self, group: RuleObjectGroup) -> str:
        """Return the recommendation-anchor key represented by one portfolio group."""
        role = str(group.attributes.get("portfolio_role", "")).strip().casefold()
        if role != "anchor":
            return ""
        key = str(group.attributes.get("portfolio_anchor_key", "")).strip()
        return key or group.group_id

    def _management_option_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build item16 groups around a same-issue option cluster."""
        selected = [anchor]
        issue_types = (
            "clinical_issue_or_decision_point",
            "option_condition_or_population",
            "preference_order",
            "selection_criteria",
            "decision_pathway_or_algorithm",
            "single_option_only",
        )
        for object_type in issue_types:
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))

        options = self._nearest_by_type(anchor, members, "management_option", chunk_order, limit=4)
        if anchor.type == "management_option":
            options = [anchor, *options]
        selected.extend(options)
        selected = self._dedupe_objects(selected)
        if anchor.type == "single_option_only" or len([obj for obj in selected if obj.type == "management_option"]) >= 1:
            return [selected]
        return []

    def _generic_candidate_members(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        chunk_order: dict[str, int],
    ) -> list[list[RuleObject]]:
        """Build a neutral local group for profiles without specialized logic."""
        selected = [anchor]
        for object_type in dict.fromkeys(obj.type for obj in members):
            if object_type == anchor.type:
                continue
            selected.extend(self._nearest_by_type(anchor, members, object_type, chunk_order, limit=1))
        return [self._dedupe_objects(selected)] if selected else []

    def _nearest_by_type(
        self,
        anchor: RuleObject,
        members: list[RuleObject],
        object_type: str,
        chunk_order: dict[str, int],
        *,
        limit: int = 2,
    ) -> list[RuleObject]:
        anchor_index = self._chunk_index(anchor.evidence.chunk_id, chunk_order)
        candidates = [
            obj
            for obj in members
            if obj.type == object_type
            and not self._same_object(anchor, obj)
        ]
        ranked = sorted(
            candidates,
            key=lambda obj: (
                abs(self._chunk_index(obj.evidence.chunk_id, chunk_order) - anchor_index),
                self._chunk_index(obj.evidence.chunk_id, chunk_order),
                -obj.confidence,
                len(obj.evidence.quote),
            ),
        )
        return ranked[:limit]

    def _dedupe_member_groups(self, groups: list[list[RuleObject]]) -> list[list[RuleObject]]:
        seen: set[tuple[tuple[str, str, str], ...]] = set()
        deduped: list[list[RuleObject]] = []
        for group in groups:
            key = tuple(
                sorted(
                    (obj.type, obj.evidence.chunk_id, obj.evidence.quote.strip())
                    for obj in group
                )
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(group)
        return deduped

    def _same_object(self, left: RuleObject, right: RuleObject) -> bool:
        return (
            left.type == right.type
            and left.value == right.value
            and left.evidence.chunk_id == right.evidence.chunk_id
            and left.evidence.quote == right.evidence.quote
        )

    def _object_key(self, obj: RuleObject) -> tuple[str, str, str, str]:
        return (
            obj.type,
            obj.value.strip(),
            obj.evidence.chunk_id,
            obj.evidence.quote.strip(),
        )

    def _portfolio_anchor_key(self, anchor: RuleObject, chunk_order: dict[str, int]) -> str:
        """Return a stable key for one recommendation-level portfolio anchor."""
        index = self._chunk_index(anchor.evidence.chunk_id, chunk_order)
        value = normalize_text_for_source_match(anchor.value)[:80]
        quote = normalize_text_for_source_match(anchor.evidence.quote)[:80]
        return f"{anchor.type}:{anchor.evidence.chunk_id}:{index}:{value or quote}"

    def _is_groupable_with_anchor(
        self,
        anchor: RuleObject,
        obj: RuleObject,
        *,
        same_section: bool,
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
        allow_heading_value_exception: bool,
    ) -> bool:
        if anchor.evidence.chunk_id == obj.evidence.chunk_id:
            return True
        if not same_section:
            return True
        if allow_heading_value_exception and self._is_heading_value_pair(anchor, obj, chunk_by_id, chunk_order):
            return True
        anchor_section = self._section(chunk_by_id.get(anchor.evidence.chunk_id))
        obj_section = self._section(chunk_by_id.get(obj.evidence.chunk_id))
        return bool(anchor_section and obj_section and anchor_section == obj_section)

    def _is_heading_value_pair(
        self,
        anchor: RuleObject,
        obj: RuleObject,
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> bool:
        object_types = {anchor.type, obj.type}
        if object_types != {"funding_section_heading", "funding_section_value"}:
            return False
        distance = abs(
            self._chunk_index(anchor.evidence.chunk_id, chunk_order)
            - self._chunk_index(obj.evidence.chunk_id, chunk_order)
        )
        if distance > 1:
            return False
        return (
            self._is_funding_heading(chunk_by_id.get(anchor.evidence.chunk_id))
            or self._is_funding_heading(chunk_by_id.get(obj.evidence.chunk_id))
        )

    def _make_group(
        self,
        spec: ItemSpec,
        index: int,
        objects: Iterable[RuleObject],
        *,
        anchor: RuleObject,
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
        extra_hints: tuple[str, ...] = (),
        extra_attributes: dict[str, object] | None = None,
    ) -> RuleObjectGroup:
        sorted_objects = tuple(self._sort_objects(self._dedupe_objects(objects), chunk_order))
        chunk_ids = tuple(dict.fromkeys(obj.evidence.chunk_id for obj in sorted_objects))
        relation_hints = tuple(
            dict.fromkeys(
                (*extra_hints, *self._relation_hints(anchor, sorted_objects, chunk_by_id, chunk_order))
            )
        )
        attributes = {
            "anchor_type": anchor.type,
            "object_types": tuple(dict.fromkeys(obj.type for obj in sorted_objects)),
        }
        if extra_attributes:
            attributes.update(extra_attributes)
        return RuleObjectGroup(
            group_id=f"{spec.item_id}:{spec.grouping.group_profile if spec.grouping else 'group'}:{index}",
            group_type=spec.grouping.group_profile if spec.grouping else spec.rule_profile,
            objects=sorted_objects,
            chunk_ids=chunk_ids,
            relation_hints=relation_hints,
            attributes=attributes,
        )

    def _relation_hints(
        self,
        anchor: RuleObject,
        objects: tuple[RuleObject, ...],
        chunk_by_id: dict[str, EvidenceChunk],
        chunk_order: dict[str, int],
    ) -> tuple[str, ...]:
        hints: list[str] = []
        anchor_chunk = anchor.evidence.chunk_id
        if any(obj.evidence.chunk_id == anchor_chunk for obj in objects if obj is not anchor):
            hints.append("same_chunk")
        if any(obj.evidence.chunk_id != anchor_chunk for obj in objects):
            hints.append("neighbor_chunk")

        sections = {
            self._section(chunk_by_id.get(obj.evidence.chunk_id))
            for obj in objects
            if self._section(chunk_by_id.get(obj.evidence.chunk_id))
        }
        if len(sections) == 1:
            hints.append("same_section")

        titles = {
            normalize_text_for_source_match(chunk_by_id[obj.evidence.chunk_id].title)
            for obj in objects
            if obj.evidence.chunk_id in chunk_by_id and chunk_by_id[obj.evidence.chunk_id].title
        }
        if len(titles) == 1 and titles:
            hints.append("same_title")

        object_types = {obj.type for obj in objects}
        if {"funding_section_heading", "funding_section_value"} & object_types:
            hints.append("heading_value_pair")
        if any(self._is_funding_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("funding_heading")
        if any(self._is_monitoring_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("monitoring_heading")
        if any(self._is_implementation_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("implementation_heading")
        if any(self._is_recommendation_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("recommendation_heading")
        if any(self._is_management_options_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("management_options_heading")
        if any(self._is_health_question_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("health_question_heading")
        if any(self._is_target_user_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("target_user_heading")
        if any(self._is_guideline_update_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("guideline_update_heading")
        if any(self._is_application_factor_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("application_factor_heading")
        if any(self._is_benefit_harm_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("benefit_harm_heading")
        if any(self._is_evidence_link_heading(chunk_by_id.get(obj.evidence.chunk_id)) for obj in objects):
            hints.append("evidence_link_heading")

        if not hints:
            hints.append("local_window")
        return tuple(dict.fromkeys(hints))

    def _dedupe_objects(self, objects: Iterable[RuleObject]) -> list[RuleObject]:
        seen: set[tuple[str, str, str, str]] = set()
        deduped: list[RuleObject] = []
        for obj in objects:
            key = (
                obj.type,
                obj.value.strip(),
                obj.evidence.chunk_id,
                obj.evidence.quote.strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(obj)
        return deduped

    def _rank_key(self, group: RuleObjectGroup, chunk_order: dict[str, int]) -> tuple[int, int, int, float, int]:
        chunk_indices = [self._chunk_index(chunk_id, chunk_order) for chunk_id in group.chunk_ids]
        span = max(chunk_indices) - min(chunk_indices) if chunk_indices else 0
        coverage = len({obj.type for obj in group.objects})
        confidence = sum(obj.confidence for obj in group.objects) / max(1, len(group.objects))
        earliest = min(chunk_indices) if chunk_indices else 0
        same_unit_rank = 0 if "same_evidence_unit" in group.relation_hints else 1
        return (-coverage, same_unit_rank, span, -confidence, earliest)

    def _rank_formulation_groups(
        self,
        groups: list[RuleObjectGroup],
        chunk_order: dict[str, int],
        max_groups: int,
    ) -> list[RuleObjectGroup]:
        """Keep low-score method-only groups visible before filling with richer groups."""
        limit = max(1, max_groups)
        singleton_groups = [
            group
            for group in groups
            if {obj.type for obj in group.objects} <= {
                "formulation_method",
                "consensus_process_heading",
                "approval_or_finalization_statement",
                "voting_or_agreement_statement",
            }
        ]
        ranked_singletons = sorted(singleton_groups, key=lambda group: self._rank_key(group, chunk_order))
        selected: list[RuleObjectGroup] = ranked_singletons[:limit]
        if len(selected) >= limit:
            return selected

        selected_ids = {group.group_id for group in selected}
        ranked_remaining = sorted(
            [group for group in groups if group.group_id not in selected_ids],
            key=lambda group: self._rank_key(group, chunk_order),
        )
        return [*selected, *ranked_remaining[: limit - len(selected)]]

    def _dedupe(self, groups: list[RuleObjectGroup]) -> list[RuleObjectGroup]:
        seen: set[tuple[str, tuple[tuple[str, str, str], ...]]] = set()
        deduped: list[RuleObjectGroup] = []
        for group in groups:
            key = (
                group.group_type,
                tuple(
                    sorted(
                        (
                            obj.type,
                            obj.evidence.chunk_id,
                            obj.evidence.quote.strip(),
                        )
                        for obj in group.objects
                    )
                ),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(group)
        return deduped

    def _portfolio_dedupe_priority(self, group: RuleObjectGroup) -> tuple[int, int, int]:
        anchor_type = str(group.attributes.get("anchor_type", "")).strip()
        portfolio_role = str(group.attributes.get("portfolio_role", "")).strip()
        return (
            0 if portfolio_role == "anchor" else 1,
            0 if anchor_type in {"recommendation_or_option", "recommendation_statement", "recommendation_identifier"} else 1,
            int(group.group_id.rsplit(":", 1)[-1]) if group.group_id.rsplit(":", 1)[-1].isdigit() else 0,
        )

    def _sort_objects(self, objects: list[RuleObject], chunk_order: dict[str, int]) -> list[RuleObject]:
        return sorted(
            objects,
            key=lambda obj: (
                self._chunk_index(obj.evidence.chunk_id, chunk_order),
                obj.type,
                -obj.confidence,
                len(obj.evidence.quote),
            ),
        )

    def _chunk_index(self, chunk_id: str, chunk_order: dict[str, int]) -> int:
        if chunk_id in chunk_order:
            return chunk_order[chunk_id]
        digits = "".join(char for char in chunk_id if char.isdigit())
        return int(digits) if digits else 0

    def _section(self, chunk: EvidenceChunk | None) -> str:
        if chunk is None:
            return ""
        section = chunk.metadata.get("section") or chunk.metadata.get("section_title") or chunk.title
        return normalize_text_for_source_match(str(section))

    def _is_funding_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(term in title for term in ("funding", "financial support", "sponsor", "acknowledg"))

    def _is_monitoring_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(term in title for term in ("audit", "monitor", "indicator", "measure", "quality"))

    def _is_implementation_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "implementation",
                "implementing",
                "tool",
                "tools",
                "resource",
                "resources",
                "appendix",
                "checklist",
                "leaflet",
                "template",
                "questionnaire",
                "flowchart",
                "manual",
                "audit",
                "patient material",
                "patient information",
                "patient materials",
                "clinician material",
                "implementation flowchart",
                "resource package",
            )
        )

    def _is_recommendation_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "recommendation",
                "recommendations",
                "summary of recommendations",
                "key recommendations",
                "executive summary",
                "algorithm",
                "推荐",
                "建议",
            )
        )

    def _is_management_options_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "management",
                "option",
                "options",
                "treatment",
                "screening",
                "diagnosis",
                "algorithm",
                "pathway",
                "first-line",
                "second-line",
                "alternative",
                "管理",
                "选择",
                "方案",
                "治疗",
                "路径",
                "算法",
            )
        )

    def _is_health_question_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "health question",
                "health questions",
                "clinical question",
                "clinical questions",
                "key question",
                "key questions",
                "research question",
                "review question",
                "pico",
                "picos",
                "scope",
                "objective",
                "objectives",
                "abstract",
                "executive summary",
                "introduction",
                "method",
                "methods",
                "methodology",
                "卫生问题",
                "临床问题",
                "关键问题",
                "范围",
                "目的",
            )
        )

    def _is_target_user_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "target audience",
                "intended audience",
                "intended users",
                "target users",
                "audience",
                "intended use",
                "purpose",
                "scope",
                "preface",
                "introduction",
                "standards of care",
                "position statement",
                "使用者",
                "目标用户",
                "目的",
                "范围",
            )
        )

    def _is_guideline_update_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "update",
                "updating",
                "maintenance",
                "review date",
                "expiry",
                "expiration",
                "surveillance",
                "guideline review",
                "更新",
                "修订",
                "复审",
                "有效期",
            )
        )

    def _is_application_factor_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "implementation",
                "applicability",
                "facilitator",
                "facilitators",
                "barrier",
                "barriers",
                "feasibility",
                "resource implications",
                "uptake",
                "adoption",
                "dissemination",
                "实施",
                "应用",
                "障碍",
                "促进",
                "可行性",
            )
        )

    def _is_benefit_harm_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "benefits and harms",
                "benefit-risk",
                "benefit risk",
                "balance",
                "rationale",
                "evidence to decision",
                "safety",
                "adverse",
                "side effects",
                "risks",
                "获益",
                "危害",
                "风险",
                "安全性",
                "权衡",
                "推荐依据",
            )
        )

    def _is_evidence_link_heading(self, chunk: EvidenceChunk | None) -> bool:
        if chunk is None:
            return False
        title = normalize_text_for_source_match(chunk.title)
        return any(
            term in title
            for term in (
                "recommendations",
                "rationale",
                "evidence summary",
                "supporting evidence",
                "evidence profile",
                "summary of findings",
                "level of evidence",
                "grade",
                "appendix",
                "推荐",
                "推荐依据",
                "证据总结",
                "支持证据",
                "证据等级",
                "附录",
            )
        )

    def _evidence_unit_key(self, obj: RuleObject, chunk: EvidenceChunk | None) -> tuple[str, str]:
        if chunk is None:
            return (obj.evidence.chunk_id, normalize_text_for_source_match(obj.evidence.quote)[:240])
        unit = self._evidence_unit_text(chunk.content or "", obj.evidence.quote)
        return (obj.evidence.chunk_id, normalize_text_for_source_match(unit)[:500])

    def _evidence_unit_text(self, content: str, quote: str) -> str:
        if not content:
            return quote
        for row_match in re.finditer(r"<tr[\s\S]*?</tr>", content, flags=re.IGNORECASE):
            row = row_match.group(0)
            if self._contains_normalized(row, quote):
                if self._has_multiple_numbered_units(row):
                    numbered = self._numbered_unit(row, self._quote_index(row, quote))
                    if numbered:
                        return numbered
                cell = self._table_cell_unit(row, quote)
                if cell and self._has_multiple_numbered_units(cell):
                    numbered = self._numbered_unit(cell, self._quote_index(cell, quote))
                    if numbered:
                        return numbered
                if cell:
                    return cell
                return row

        quote_index = self._quote_index(content, quote)
        if quote_index < 0:
            for line in content.splitlines():
                if self._contains_normalized(line, quote):
                    return line
            return quote

        line_start = content.rfind("\n", 0, quote_index) + 1
        line_end = content.find("\n", quote_index)
        if line_end < 0:
            line_end = len(content)
        line = content[line_start:line_end].strip()
        if line and len(line) <= 900 and not self._has_multiple_numbered_units(line):
            return line

        numbered = self._numbered_unit(content, quote_index)
        if numbered:
            return numbered
        return self._sentence_unit(content, quote_index)

    def _table_row_unit(self, content: str, quote: str) -> str:
        for row_match in re.finditer(r"<tr[\s\S]*?</tr>", content, flags=re.IGNORECASE):
            row = row_match.group(0)
            if self._contains_normalized(row, quote):
                return row
        return ""

    def _numbered_unit(self, content: str, quote_index: int) -> str:
        if quote_index < 0:
            return ""
        matches = list(
            re.finditer(
                r"(?:(?<=^)|(?<=\n)|(?<=\s))"
                r"(?:\d+[A-Za-z]?\.\d+|\d+\.|[A-Za-z]\.|[A-Za-z]\)|[-*•])\s+",
                content,
            )
        )
        if not matches:
            return ""
        start_match = None
        for match in matches:
            if match.start() <= quote_index:
                start_match = match
            else:
                break
        if start_match is None:
            return ""
        next_start = len(content)
        for match in matches:
            if match.start() > quote_index:
                next_start = match.start()
                break
        unit = content[start_match.start():next_start].strip()
        return unit if len(unit) <= 1200 else unit[:1200]

    def _table_cell_unit(self, row: str, quote: str) -> str:
        for cell_match in re.finditer(r"<td[^>]*>[\s\S]*?</td>", row, flags=re.IGNORECASE):
            cell = cell_match.group(0)
            if self._contains_normalized(cell, quote):
                return cell
        return ""

    def _has_multiple_numbered_units(self, text: str) -> bool:
        return (
            len(
                re.findall(
                    r"(?:(?<=^)|(?<=\n)|(?<=\s))"
                    r"(?:\d+[A-Za-z]?\.\d+|\d+\.|[A-Za-z]\.|[A-Za-z]\)|[-*•])\s+",
                    text,
                )
            )
            > 1
        )

    def _sentence_unit(self, content: str, quote_index: int) -> str:
        start_candidates = [
            content.rfind(".", 0, quote_index),
            content.rfind(";", 0, quote_index),
            content.rfind("\n", 0, quote_index),
        ]
        start = max(start_candidates) + 1
        end_candidates = [
            index
            for index in (
                content.find(".", quote_index),
                content.find(";", quote_index),
                content.find("\n", quote_index),
            )
            if index >= 0
        ]
        end = min(end_candidates) + 1 if end_candidates else min(len(content), quote_index + 500)
        return content[start:end].strip()

    def _contains_normalized(self, text: str, quote: str) -> bool:
        normalized_text = normalize_text_for_source_match(text)
        normalized_quote = normalize_text_for_source_match(quote)
        return bool(normalized_quote and normalized_quote in normalized_text)

    def _quote_index(self, content: str, quote: str) -> int:
        quote_index = content.find(quote)
        if quote_index < 0:
            quote_index = content.casefold().find(quote.casefold())
        return quote_index

    def _is_auxiliary_monitoring_method(self, obj: RuleObject) -> bool:
        text = normalize_text_for_source_match(f"{obj.value} {obj.evidence.quote}")
        return any(
            term in text
            for term in (
                "audit tool",
                "collection of data",
                "data collection",
                "data should be collected",
                "collected using",
                "measure",
                "measurement",
                "record",
                "document",
                "calculate",
                "assess",
                "assessment",
            )
        )
