"""
Reusable rule profile engines.
"""
from __future__ import annotations

from core.rule_engine.models import (
    BenefitHarmBalanceRule,
    BranchingRule,
    CompetingInterestDisclosureRule,
    EvidenceAppraisalRule,
    FundingDisclosureRule,
    GateComponentRule,
    GuidelineUpdateRule,
    HealthQuestionRule,
    ApplicationFactorRule,
    ImplementationToolRule,
    ItemSpec,
    ManagementOptionsRule,
    MilestoneRule,
    MonitoringCriteriaRule,
    ObjectStoreSnapshot,
    ReviewReason,
    RuleCondition,
    RuleGroupingResult,
    RuleObject,
    RuleObjectGroup,
    RuleObjectSpec,
    RuleResult,
    ScoreLevel,
    RecommendationEvidenceLinkRule,
    TargetUserRule,
    WeightedChecklistRule,
)


class GateComponentCountEngine:
    """
    Rule profile with gate objects and countable component objects.

    This engine is intended for items whose scoring rubric has one required
    baseline fact and a small set of independent completeness components.
    The current score mapping is:
    - no gate object: 1
    - gate + 0 components: 2-3
    - gate + 1 component: 4-5
    - gate + 2 components: 6
    - gate + 3 or more components: 7

    The engine is deliberately profile-level rather than item-specific. Item
    specs decide which object types are gates and which are components.
    """

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """
        Infer score range by checking gate presence and component count.

        Args:
            spec: Item specification containing gate/component object roles.
            snapshot: Merged object snapshot produced by ObjectStore.

        Returns:
            A RuleResult whose score range is determined by the gate/component
            scoring table above.
        """
        gate_specs = self._objects_by_role(spec, "gate")
        component_specs = self._objects_by_role(spec, "component")
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, GateComponentRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 GateComponentRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        # A gate-component profile is invalid without at least one gate.
        if not gate_specs:
            review_reasons.append(
                ReviewReason(
                    code="missing_gate_spec",
                    message=f"{spec.item_id} 未定义 gate 对象",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        matched_gates = [obj_spec for obj_spec in gate_specs if snapshot.has_object(obj_spec.type)]
        missing_gates = [obj_spec.type for obj_spec in gate_specs if not snapshot.has_object(obj_spec.type)]

        # Without the baseline fact, the item cannot move beyond score 1.
        if not matched_gates:
            return RuleResult(
                item_id=spec.item_id,
                status="matched",
                score_range="1",
                allowed_scores=[1],
                max_score=1,
                matched_rules=[],
                missing=missing_gates,
                needs_review=bool(review_reasons),
                review_reasons=review_reasons,
            )

        matched_components = [
            obj_spec for obj_spec in component_specs if snapshot.has_object(obj_spec.type)
        ]
        missing_components = [
            obj_spec.type for obj_spec in component_specs if not snapshot.has_object(obj_spec.type)
        ]

        component_count = len(matched_components)
        matched_rules = [obj_spec.type for obj_spec in matched_gates + matched_components]

        # AGREE-II item 1 and item 14 share this exact completeness mapping.
        if component_count == 0:
            score_range = "2-3"
            allowed_scores = [2, 3]
        elif component_count == 1:
            score_range = "4-5"
            allowed_scores = [4, 5]
        elif component_count == 2:
            score_range = "6"
            allowed_scores = [6]
        else:
            score_range = "7"
            allowed_scores = [7]

        return RuleResult(
            item_id=spec.item_id,
            status="matched",
            score_range=score_range,
            allowed_scores=allowed_scores,
            max_score=max(allowed_scores),
            matched_rules=matched_rules,
            missing=missing_components,
            needs_review=bool(review_reasons),
            review_reasons=review_reasons,
        )

    def _objects_by_role(self, spec: ItemSpec, role: str) -> list[RuleObjectSpec]:
        """Return all object specs with the requested role."""
        return [obj_spec for obj_spec in spec.object_specs.values() if obj_spec.role == role]

    def _error_result(self, item_id: str, review_reasons: list[ReviewReason]) -> RuleResult:
        """Return a standard score-1 error result."""
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


class OrderedMilestoneEngine:
    """
    Rule profile that selects the first satisfied ordered milestone level.

    This engine is intended for rubrics where higher scores represent richer
    evidence milestones rather than simple component counts. Each ItemSpec
    provides MilestoneRule levels in high-to-low priority order. The first level
    whose RuleCondition is satisfied becomes the score.

    RuleCondition supports nested fact/all/any/not expressions. In v1, a fact
    means "at least one merged RuleObject of this type exists"; value-level
    comparisons and relationship-level reasoning are intentionally out of scope.
    """

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """
        Infer score range from ordered milestone-level conditions.

        Args:
            spec: Item specification containing ordered ScoreLevel conditions.
            snapshot: Merged object snapshot produced by ObjectStore.

        Returns:
            A RuleResult for the first matching milestone level. Invalid level
            definitions return an error result so spec problems are not hidden as
            low scores.
        """
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, MilestoneRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 MilestoneRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        levels = spec.rule.levels
        # Ordered milestone specs must define explicit levels, including fallback.
        if not levels:
            review_reasons.append(
                ReviewReason(
                    code="missing_score_levels",
                    message=f"{spec.item_id} 未定义 MilestoneRule.levels",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        # Validate before evaluating so configuration bugs do not masquerade as
        # valid low-score outcomes.
        validation_reasons = self._validate_score_levels(spec, levels)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is not None:
            review_reasons.extend(grouping_result.review_reasons)
            fact_sources = self._group_fact_sources(spec, grouping_result, review_reasons)
        else:
            fact_sources = [({obj.type for obj in snapshot.objects}, 0)]

        best_match = self._best_matching_source(levels, fact_sources)
        matched_level_index: int | None = None
        matched_level: ScoreLevel | None = None
        fact_types: set[str] = set()
        if best_match is not None:
            matched_level_index, matched_level, fact_types = best_match

        # A well-formed spec should include an unconditional fallback level. If it
        # does not, fail loudly instead of silently returning the last level.
        if matched_level is None or matched_level_index is None:
            review_reasons.append(
                ReviewReason(
                    code="no_score_level_matched",
                    message=f"{spec.item_id} 未命中任何 MilestoneRule level",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        matched_rules = self._matched_facts(matched_level.condition, fact_types)
        missing = self._missing_for_next_higher_level(
            levels,
            matched_level_index,
            fact_types,
        )
        # NOT conditions can block the next milestone level even when no positive fact
        # is missing. Surface those blockers for explainer/reviewer visibility.
        blocking_facts = self._blocking_for_next_higher_level(
            levels,
            matched_level_index,
            fact_types,
        )
        for fact_type in blocking_facts:
            review_reasons.append(
                ReviewReason(
                    code="blocked_by_excluded_fact",
                    message=f"{fact_type} 阻止进入下一评分档位",
                    object_type=fact_type,
                    severity="warning",
                )
            )
        allowed_scores = self._parse_score_range(matched_level.score_range)

        return RuleResult(
            item_id=spec.item_id,
            status="matched",
            score_range=matched_level.score_range,
            allowed_scores=allowed_scores,
            max_score=max(allowed_scores),
            matched_rules=matched_rules,
            missing=missing,
            needs_review=bool(review_reasons),
            review_reasons=review_reasons,
        )

    def _group_fact_sources(
        self,
        spec: ItemSpec,
        grouping_result: RuleGroupingResult,
        review_reasons: list[ReviewReason],
    ) -> list[tuple[set[str], int]]:
        """Return one fact set per accepted group for group-aware milestone scoring."""
        group_profile = spec.grouping.group_profile if spec.grouping else ""
        accepted_groups: list[RuleObjectGroup] = []
        for group in grouping_result.accepted_groups:
            if group_profile and group.group_type != group_profile:
                continue
            if self._should_exclude_profile_group(group_profile, group):
                review_reasons.append(
                    ReviewReason(
                        code="excluded_group_attributes",
                        message=f"{group.group_id} 的 group attributes 不满足 {group_profile} 评分边界。",
                        object_type=group.group_type,
                        severity="warning",
                    )
                )
                continue
            accepted_groups.append(group)
        if not accepted_groups:
            return [(set(), 0)]
        if group_profile == "application_factor":
            return [(self._application_factor_facts(accepted_groups), 0)]
        return [
            ({obj.type for obj in group.objects}, index)
            for index, group in enumerate(accepted_groups)
        ]

    def _should_exclude_profile_group(self, group_profile: str, group: RuleObjectGroup) -> bool:
        """Apply profile-specific attribute guards before object-type milestone scoring."""
        if group_profile == "recommendation_specificity":
            return (
                self._group_attribute(group, "same_recommendation") == "false"
                or self._group_attribute(group, "is_current_guideline_recommendation") == "false"
            )
        if group_profile == "application_factor":
            scope = self._group_attribute(group, "scope")
            rubric_role = self._group_attribute(group, "rubric_role")
            same_issue = self._group_attribute(group, "same_application_issue")
            if rubric_role == "not_applicable" or same_issue == "false":
                return True
            return bool(
                scope
                and scope
                not in {
                    "current_guideline_application",
                    "guideline_application",
                    "recommendation_implementation",
                }
            )
        return False

    def _group_attribute(self, group: RuleObjectGroup, key: str) -> str:
        return str(group.attributes.get(key, "")).strip().casefold()

    def _application_factor_facts(self, groups: list[RuleObjectGroup]) -> set[str]:
        """Derive item18 milestone facts from rubric attributes."""
        facts: set[str] = set()
        saw_attribute_protocol = False
        for group in groups:
            has_rubric_attrs = self._has_application_factor_rubric_attributes(group)
            has_legacy_attrs = self._has_application_factor_legacy_attributes(group)
            saw_attribute_protocol = saw_attribute_protocol or has_rubric_attrs or has_legacy_attrs
            direction = self._group_attribute(group, "factor_direction")
            if direction == "facilitator":
                facts.add("facilitator")
            elif direction == "barrier":
                facts.add("barrier")
            elif direction == "mixed":
                facts.update({"facilitator", "barrier"})
            if self._group_attribute(group, "has_identification_basis") == "true":
                facts.add("collection_method")
            if self._group_attribute(group, "has_application_link") == "true":
                facts.add("impact_on_guideline_or_recommendations")
            if has_rubric_attrs:
                continue

            old_direction = self._group_attribute(group, "factor_polarity")
            if old_direction == "facilitator":
                facts.add("facilitator")
            elif old_direction == "barrier":
                facts.add("barrier")
            elif old_direction in {"both", "mixed"}:
                facts.update({"facilitator", "barrier"})
            if self._group_attribute(group, "has_collection_method") == "true":
                facts.add("collection_method")
            if self._group_attribute(group, "has_recommendation_impact") == "true":
                facts.add("impact_on_guideline_or_recommendations")
        if not facts and not saw_attribute_protocol:
            facts = {
                obj.type
                for group in groups
                for obj in group.objects
            }
        return facts

    def _has_application_factor_rubric_attributes(self, group: RuleObjectGroup) -> bool:
        return any(
            key in group.attributes
            for key in (
                "rubric_role",
                "factor_direction",
                "has_identification_basis",
                "has_application_link",
                "same_application_issue",
            )
        )

    def _has_application_factor_legacy_attributes(self, group: RuleObjectGroup) -> bool:
        return any(
            key in group.attributes
            for key in (
                "factor_polarity",
                "has_collection_method",
                "has_recommendation_impact",
            )
        )

    def _best_matching_source(
        self,
        levels: tuple[ScoreLevel, ...],
        fact_sources: list[tuple[set[str], int]],
    ) -> tuple[int, ScoreLevel, set[str]] | None:
        """Select the highest milestone reached by any fact source."""
        matches: list[tuple[tuple[int, int, int], int, ScoreLevel, set[str]]] = []
        for source_index, (fact_types, position) in enumerate(fact_sources):
            for level_index, level in enumerate(levels):
                if self._is_condition_met(level.condition, fact_types):
                    allowed_scores = self._parse_score_range(level.score_range)
                    matches.append(
                        (
                            (-max(allowed_scores), -len(self._matched_facts(level.condition, fact_types)), position),
                            level_index,
                            level,
                            fact_types,
                        )
                    )
                    break
        if not matches:
            return None
        _rank, level_index, level, fact_types = min(matches, key=lambda item: item[0])
        return level_index, level, fact_types

    def _is_condition_met(self, condition: RuleCondition, fact_types: set[str]) -> bool:
        """Return whether a condition tree is satisfied by available fact types."""
        if condition.op == "fact":
            return bool(condition.fact and condition.fact in fact_types)
        if condition.op == "all":
            return all(self._is_condition_met(child, fact_types) for child in condition.children)
        if condition.op == "any":
            return any(self._is_condition_met(child, fact_types) for child in condition.children)
        if condition.op == "not":
            if len(condition.children) != 1:
                return False
            return not self._is_condition_met(condition.children[0], fact_types)
        return False

    def _matched_facts(self, condition: RuleCondition, fact_types: set[str]) -> list[str]:
        """Return fact names in the matched milestone level that are present."""
        return sorted(
            fact_type
            for fact_type in self._collect_facts(condition)
            if fact_type in fact_types
        )

    def _missing_for_next_higher_level(
        self,
        levels: tuple[ScoreLevel, ...],
        matched_level_index: int,
        fact_types: set[str],
    ) -> list[str]:
        """Return facts needed to reach the next higher milestone level."""
        if matched_level_index == 0:
            return []

        next_level = levels[matched_level_index - 1]
        return sorted(self._missing_for_condition(next_level.condition, fact_types))

    def _missing_for_condition(self, condition: RuleCondition, fact_types: set[str]) -> set[str]:
        """
        Return positive facts still needed for a condition.

        For OR conditions, this returns the shortest missing branch so explanations
        describe one concrete path to the next level instead of every alternative.
        """
        if self._is_condition_met(condition, fact_types):
            return set()

        if condition.op == "fact":
            return {condition.fact} if condition.fact else set()

        if condition.op == "all":
            missing: set[str] = set()
            for child in condition.children:
                missing.update(self._missing_for_condition(child, fact_types))
            return missing

        if condition.op == "any":
            child_missing = [
                self._missing_for_condition(child, fact_types)
                for child in condition.children
            ]
            if not child_missing:
                return set()
            return min(child_missing, key=lambda missing: (len(missing), sorted(missing)))

        return set()

    def _blocking_for_next_higher_level(
        self,
        levels: tuple[ScoreLevel, ...],
        matched_level_index: int,
        fact_types: set[str],
    ) -> list[str]:
        """Return excluded facts that block the next higher milestone level."""
        if matched_level_index == 0:
            return []

        next_level = levels[matched_level_index - 1]
        return sorted(self._blocking_for_condition(next_level.condition, fact_types))

    def _blocking_for_condition(self, condition: RuleCondition, fact_types: set[str]) -> set[str]:
        """Return present facts that make NOT conditions fail."""
        if self._is_condition_met(condition, fact_types):
            return set()

        if condition.op == "not":
            child = condition.children[0]
            if self._is_condition_met(child, fact_types):
                return self._matched_facts_set(child, fact_types)
            return set()

        blocking: set[str] = set()
        for child in condition.children:
            blocking.update(self._blocking_for_condition(child, fact_types))
        return blocking

    def _matched_facts_set(self, condition: RuleCondition, fact_types: set[str]) -> set[str]:
        """Return present fact names as a set for nested condition helpers."""
        return {
            fact_type
            for fact_type in self._collect_facts(condition)
            if fact_type in fact_types
        }

    def _collect_facts(self, condition: RuleCondition) -> set[str]:
        """Collect all positive fact names referenced by a condition tree."""
        if condition.op == "fact":
            return {condition.fact} if condition.fact else set()

        facts: set[str] = set()
        for child in condition.children:
            facts.update(self._collect_facts(child))
        return facts

    def _parse_score_range(self, score_range: str) -> list[int]:
        """Parse a score range string such as '4-5' into allowed scores."""
        score_range = score_range.strip()
        if "-" not in score_range:
            return [int(score_range)]

        start_text, end_text = score_range.split("-", 1)
        start = int(start_text.strip())
        end = int(end_text.strip())
        return list(range(start, end + 1))

    def _validate_score_levels(self, spec: ItemSpec, levels: tuple[ScoreLevel, ...]) -> list[ReviewReason]:
        """Validate milestone level ranges and condition trees against the item spec."""
        reasons: list[ReviewReason] = []
        fallback_indexes: list[int] = []
        for index, level in enumerate(levels):
            reasons.extend(self._validate_score_range(level.score_range))
            reasons.extend(self._validate_condition(level.condition, spec))
            if self._is_unconditional_fallback(level.condition):
                fallback_indexes.append(index)
        if fallback_indexes and fallback_indexes[-1] != len(levels) - 1:
            reasons.append(
                ReviewReason(
                    code="fallback_not_last",
                    message="MilestoneRule 的无条件 fallback 必须放在最后",
                    severity="error",
                )
            )
        return reasons

    def _validate_condition(self, condition: RuleCondition, spec: ItemSpec) -> list[ReviewReason]:
        """Validate one condition tree and return machine-readable errors."""
        if condition.op == "fact":
            if not condition.fact:
                return [
                    ReviewReason(
                        code="invalid_rule_condition",
                        message="fact condition 缺少 fact 名称",
                        severity="error",
                    )
                ]
            if condition.fact not in spec.object_specs:
                return [
                    ReviewReason(
                        code="unknown_condition_fact",
                        message=f"规则条件引用了未声明的 fact: {condition.fact}",
                        object_type=condition.fact,
                        severity="error",
                    )
                ]
            return []

        if condition.op == "all" and not condition.children:
            return []

        if condition.op == "not" and len(condition.children) != 1:
            return [
                ReviewReason(
                    code="invalid_rule_condition",
                    message="not condition 必须包含且只包含一个子条件",
                    severity="error",
                )
            ]

        if condition.op == "any" and not condition.children:
            return [
                ReviewReason(
                    code="invalid_rule_condition",
                    message="any condition 至少需要一个子条件",
                    severity="error",
                )
            ]

        reasons: list[ReviewReason] = []
        for child in condition.children:
            reasons.extend(self._validate_condition(child, spec))
        return reasons

    def _is_unconditional_fallback(self, condition: RuleCondition) -> bool:
        """Return whether a condition is the explicit empty all_of() fallback."""
        return condition.op == "all" and not condition.children

    def _validate_score_range(self, score_range: str) -> list[ReviewReason]:
        """Validate score range format without throwing out of the pipeline."""
        try:
            allowed_scores = self._parse_score_range(score_range)
        except ValueError:
            return [
                ReviewReason(
                    code="invalid_score_range",
                    message=f"无效评分区间: {score_range}",
                    severity="error",
                )
            ]

        if not allowed_scores or any(score < 1 or score > 7 for score in allowed_scores):
            return [
                ReviewReason(
                    code="invalid_score_range",
                    message=f"评分区间必须位于 1-7 内: {score_range}",
                    severity="error",
                )
            ]
        return []

    def _error_result(self, item_id: str, review_reasons: list[ReviewReason]) -> RuleResult:
        """Return a standard score-1 error result."""
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


class TargetUserEngine:
    """Item6 scorer that aggregates target-user and intended-use facts globally."""

    _USER_CLEAR = "clear_target_user"
    _USER_VAGUE = "vague_target_user"
    _USE_CLEAR = "clear_intended_use"
    _USE_VAGUE = "vague_intended_use"
    _REQUIRED_TYPES = (_USER_CLEAR, _USER_VAGUE, _USE_CLEAR, _USE_VAGUE)
    _EXCLUSION_TYPES = {
        "target_population_or_disease_scope",
        "author_or_developer_group",
        "ordinary_clinical_context",
    }
    _EXCLUDED_SCOPES = {
        "target_population",
        "author_group",
        "disease_scope",
        "ordinary_clinical_context",
        "outside_scope",
        "unclear",
        "not_applicable",
    }

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item6 score from the best full-text user/use facts."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, TargetUserRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 TargetUserRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        missing_specs = [fact for fact in self._REQUIRED_TYPES if fact not in spec.object_specs]
        if missing_specs:
            review_reasons.extend(
                ReviewReason(
                    code="unknown_target_user_fact",
                    message=f"TargetUserRule 引用了未声明的 fact: {fact}",
                    object_type=fact,
                    severity="error",
                )
                for fact in missing_specs
            )
            return self._error_result(spec.item_id, review_reasons)

        facts = self._valid_fact_types(snapshot.objects, review_reasons)
        has_clear_user = self._USER_CLEAR in facts
        has_vague_user = self._USER_VAGUE in facts and not has_clear_user
        has_clear_use = self._USE_CLEAR in facts
        has_vague_use = self._USE_VAGUE in facts and not has_clear_use

        if has_clear_user and has_clear_use:
            score_range = "7"
            matched = [self._USER_CLEAR, self._USE_CLEAR]
            missing: list[str] = []
        elif (has_clear_user and has_vague_use) or (has_vague_user and has_clear_use):
            score_range = "6"
            matched = self._matched(facts)
            missing = []
        elif has_vague_user and has_vague_use:
            score_range = "5"
            matched = [self._USER_VAGUE, self._USE_VAGUE]
            missing = [self._USER_CLEAR, self._USE_CLEAR]
        elif has_clear_user or has_clear_use:
            score_range = "3-4"
            matched = [self._USER_CLEAR] if has_clear_user else [self._USE_CLEAR]
            missing = [self._USE_CLEAR] if has_clear_user else [self._USER_CLEAR]
        elif has_vague_user or has_vague_use:
            score_range = "2"
            matched = [self._USER_VAGUE] if has_vague_user else [self._USE_VAGUE]
            missing = [self._USER_CLEAR, self._USE_CLEAR]
        else:
            score_range = "1"
            matched = []
            missing = [self._USER_CLEAR, self._USE_CLEAR]

        return self._result(spec.item_id, score_range, matched, missing, review_reasons)

    def _valid_fact_types(
        self,
        objects: tuple[RuleObject, ...],
        review_reasons: list[ReviewReason],
    ) -> set[str]:
        facts: set[str] = set()
        for obj in objects:
            if obj.type in self._EXCLUSION_TYPES:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_target_user_object",
                        message=f"{obj.type} 不属于 item6 目标使用者或用途，不参与评分",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )
                continue
            if obj.type not in self._REQUIRED_TYPES:
                continue
            scope = str(obj.attributes.get("scope", "")).strip().casefold()
            if scope in self._EXCLUDED_SCOPES:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_target_user_scope",
                        message=f"{obj.type} 的 scope={scope} 不参与 item6 评分",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )
                continue
            facts.add(obj.type)
        return facts

    def _matched(self, facts: set[str]) -> list[str]:
        return [fact for fact in self._REQUIRED_TYPES if fact in facts]

    def _result(
        self,
        item_id: str,
        score_range: str,
        matched_rules: list[str],
        missing: list[str],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        allowed_scores = OrderedMilestoneEngine()._parse_score_range(score_range)
        return RuleResult(
            item_id=item_id,
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
        return OrderedMilestoneEngine()._error_result(item_id, review_reasons)


class EvidenceAppraisalEngine:
    """Item9 scorer that counts unique evidence-appraisal dimensions with a core-anchor gate."""

    _CORE_FACTS = {
        "evidence_assessment_method",
        "explicit_evidence_level",
        "evidence_grading_system_applied",
        "methodological_limitation",
    }
    _FORMAL_APPRAISAL_FACTS = {
        "evidence_assessment_method",
        "explicit_evidence_level",
        "evidence_grading_system_applied",
    }
    _STRENGTH_FACTS = {
        "evidence_assessment_method",
        "explicit_evidence_level",
        "evidence_grading_system_applied",
        "study_design_description",
        "result_consistency",
        "result_direction",
        "benefit_harm_magnitude",
        "practice_applicability",
    }
    _LIMITATION_FACTS = {
        "methodological_limitation",
    }
    _DIMENSIONS = (
        "evidence_assessment_method",
        "explicit_evidence_level",
        "evidence_grading_system_applied",
        "study_design_description",
        "methodological_limitation",
        "outcome_relevance",
        "result_consistency",
        "result_direction",
        "benefit_harm_magnitude",
        "practice_applicability",
    )
    _EXCLUSION_TYPES = {
        "ordinary_clinical_conclusion",
        "single_study_result_summary",
        "evidence_selection_criteria",
        "recommendation_formulation_process",
        "clinical_background_or_workflow",
    }
    _EXCLUDED_SCOPES = {
        "clinical_conclusion",
        "single_study_result",
        "recommendation_text",
        "disease_background",
        "evidence_selection",
        "recommendation_formulation",
        "clinical_workflow",
        "outside_scope",
        "unclear",
    }

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item9 score from unique appraisal dimensions."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, EvidenceAppraisalRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 EvidenceAppraisalRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        missing_specs = [fact for fact in self._DIMENSIONS if fact not in spec.object_specs]
        if missing_specs:
            review_reasons.extend(
                ReviewReason(
                    code="unknown_evidence_appraisal_fact",
                    message=f"EvidenceAppraisalRule 引用了未声明的 fact: {fact}",
                    object_type=fact,
                    severity="error",
                )
                for fact in missing_specs
            )
            return self._error_result(spec.item_id, review_reasons)

        facts = self._valid_fact_types(snapshot.objects, review_reasons)
        score_range = self._score_range(facts)
        matched = [fact for fact in self._DIMENSIONS if fact in facts]
        missing = self._missing_for_next_band(facts, score_range)
        return self._result(spec.item_id, score_range, matched, missing, review_reasons)

    def _valid_fact_types(
        self,
        objects: tuple[RuleObject, ...],
        review_reasons: list[ReviewReason],
    ) -> set[str]:
        facts: set[str] = set()
        for obj in objects:
            if obj.type in self._EXCLUSION_TYPES:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_evidence_appraisal_object",
                        message=f"{obj.type} 不属于 item9 证据强度/局限性评价，不参与评分",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )
                continue
            if obj.type not in self._DIMENSIONS:
                continue
            scope = str(obj.attributes.get("scope", "")).strip().casefold()
            if scope in self._EXCLUDED_SCOPES:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_evidence_appraisal_scope",
                        message=f"{obj.type} 的 scope={scope} 不参与 item9 评分",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )
                continue
            facts.add(obj.type)
        return facts

    def _score_range(self, facts: set[str]) -> str:
        if not facts:
            return "1"

        core_facts = facts & self._CORE_FACTS
        if not core_facts:
            return "2" if len(facts) == 1 else "3"

        supplemental_count = len(facts - self._CORE_FACTS)
        has_strength = bool(facts & self._STRENGTH_FACTS)
        has_limitation = bool(facts & self._LIMITATION_FACTS)
        formal_appraisal_count = len(facts & self._FORMAL_APPRAISAL_FACTS)
        has_applied_grading_system = "evidence_grading_system_applied" in facts
        has_formal_support = bool(facts & {"evidence_assessment_method", "explicit_evidence_level"})
        formal_supplemental_count = len(facts - self._FORMAL_APPRAISAL_FACTS)
        if has_strength and has_limitation and supplemental_count >= 2:
            return "7"
        if has_applied_grading_system and has_formal_support and formal_supplemental_count >= 2:
            return "7"
        if supplemental_count >= 3:
            return "6"
        if has_applied_grading_system and has_formal_support and formal_supplemental_count >= 1:
            return "6"
        if supplemental_count >= 1:
            return "5"
        return "4"

    def _missing_for_next_band(self, facts: set[str], score_range: str) -> list[str]:
        if score_range == "7":
            return []
        if not facts:
            return sorted(self._CORE_FACTS)
        if not (facts & self._CORE_FACTS):
            return sorted(self._CORE_FACTS)
        missing: set[str] = set()
        formal_appraisal_count = len(facts & self._FORMAL_APPRAISAL_FACTS)
        has_applied_grading_system = "evidence_grading_system_applied" in facts
        if not (facts & self._LIMITATION_FACTS) and not has_applied_grading_system:
            missing.add("methodological_limitation")
            missing.add("evidence_grading_system_applied")
        elif not (facts & self._LIMITATION_FACTS) and formal_appraisal_count < 2:
            missing.add("methodological_limitation")
        if len(facts - self._CORE_FACTS) < 2:
            missing.update(
                fact
                for fact in self._DIMENSIONS
                if fact not in facts and fact not in self._CORE_FACTS
            )
        return sorted(missing)

    def _result(
        self,
        item_id: str,
        score_range: str,
        matched_rules: list[str],
        missing: list[str],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        allowed_scores = OrderedMilestoneEngine()._parse_score_range(score_range)
        return RuleResult(
            item_id=item_id,
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
        return OrderedMilestoneEngine()._error_result(item_id, review_reasons)


class GuidelineUpdateEngine:
    """Item14 scorer that aggregates future guideline-update mechanism facts globally."""

    _GENERIC_FACTS = {
        "update_mention",
        "future_update_mention",
        "current_document_future_update_mechanism",
        "series_or_living_guideline_update_mechanism",
    }
    _CURRENT_MECHANISM_FACTS = {"current_document_future_update_mechanism"}
    _SERIES_MECHANISM_FACTS = {"series_or_living_guideline_update_mechanism"}
    _INTERVAL_FACTS = {"update_interval", "update_interval_or_review_date"}
    _TRIGGER_FACTS = {"update_criteria", "update_trigger_or_criteria"}
    _PROCESS_FACTS = {
        "update_condition",
        "update_responsible_body",
        "update_surveillance_or_process",
    }
    _EXCLUSION_FACTS = {
        "current_revision_only",
        "clinical_review_or_followup",
        "search_date_only",
        "external_guideline_update",
        "generic_methodology_manual_requirement",
    }
    _EXCLUDED_SCOPES = {
        "current_revision_only",
        "search_date_only",
        "clinical_followup",
        "clinical_follow_up",
        "external_guideline",
        "external_guideline_update",
        "generic_methodology_manual_requirement",
        "literature_search_date",
        "unclear",
    }
    _SERIES_SCOPES = {
        "series_or_living_guideline",
        "guideline_series",
        "living_guideline",
        "standards_programme",
        "standards_program",
        "standing_programme",
        "standing_program",
    }

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item14 score from future-update mechanism categories."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, GuidelineUpdateRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 GuidelineUpdateRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        facts, has_current_mechanism, has_series_mechanism = self._valid_fact_types(snapshot.objects, review_reasons)
        categories = self._matched_categories(facts)
        category_count = len(categories)
        generic_only = bool(facts & self._GENERIC_FACTS)

        if category_count == 3:
            score_range = "7"
        elif category_count == 2:
            score_range = "6"
        elif category_count == 1:
            score_range = "4-5"
        elif generic_only:
            score_range = "2-3"
        else:
            score_range = "1"
        if not has_current_mechanism and score_range == "7":
            score_range = "5-6"

        matched = self._matched_rules(facts)
        missing = self._missing_categories(categories, score_range)
        return self._result(spec.item_id, score_range, matched, missing, review_reasons)

    def _valid_fact_types(
        self,
        objects: tuple[RuleObject, ...],
        review_reasons: list[ReviewReason],
    ) -> tuple[set[str], bool, bool]:
        all_fact_types = (
            self._GENERIC_FACTS
            | self._INTERVAL_FACTS
            | self._TRIGGER_FACTS
            | self._PROCESS_FACTS
            | self._EXCLUSION_FACTS
        )
        facts: set[str] = set()
        has_current_mechanism = False
        has_series_mechanism = False
        for obj in objects:
            if obj.type not in all_fact_types:
                continue
            scope = str(obj.attributes.get("scope", "")).strip().casefold()
            if obj.type in self._EXCLUSION_FACTS or scope in self._EXCLUDED_SCOPES:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_guideline_update_scope",
                        message=f"{obj.type} 不属于当前指南未来更新机制，不参与 item14 评分",
                        object_type=obj.type,
                        object_value=obj.value,
                        severity="warning",
                    )
                )
                continue
            facts.add(obj.type)
            if obj.type in self._CURRENT_MECHANISM_FACTS:
                has_current_mechanism = True
            if obj.type in self._SERIES_MECHANISM_FACTS or scope in self._SERIES_SCOPES:
                has_series_mechanism = True
        return facts, has_current_mechanism, has_series_mechanism

    def _matched_categories(self, facts: set[str]) -> set[str]:
        categories: set[str] = set()
        if facts & self._INTERVAL_FACTS:
            categories.add("update_interval_or_review_date")
        if facts & self._TRIGGER_FACTS:
            categories.add("update_trigger_or_criteria")
        if facts & self._PROCESS_FACTS:
            categories.add("update_process_or_responsible_body")
        return categories

    def _matched_rules(self, facts: set[str]) -> list[str]:
        preferred = (
            "update_mention",
            "future_update_mention",
            "current_document_future_update_mechanism",
            "series_or_living_guideline_update_mechanism",
            "update_interval",
            "update_interval_or_review_date",
            "update_criteria",
            "update_trigger_or_criteria",
            "update_condition",
            "update_responsible_body",
            "update_surveillance_or_process",
        )
        return [fact for fact in preferred if fact in facts]

    def _missing_categories(self, categories: set[str], score_range: str) -> list[str]:
        if score_range == "7":
            return []
        needed = {
            "update_interval_or_review_date",
            "update_trigger_or_criteria",
            "update_process_or_responsible_body",
        }
        if score_range == "1":
            return ["future_update_mention"]
        return sorted(needed - categories)

    def _result(
        self,
        item_id: str,
        score_range: str,
        matched_rules: list[str],
        missing: list[str],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        allowed_scores = OrderedMilestoneEngine()._parse_score_range(score_range)
        return RuleResult(
            item_id=item_id,
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
        return OrderedMilestoneEngine()._error_result(item_id, review_reasons)


class MonitoringCriteriaEngine:
    """
    Item21 group scorer for accepted monitoring/auditing criteria groups.
    """

    _INDICATOR = "indicator_or_standard"
    _FREQUENCY = "measurement_frequency_or_interval"
    _METHOD = "measurement_method"
    _VAGUE = "vague_monitoring_mention"
    _VALID_CRITERION_ORIGINS = {
        "current_guideline_recommendation",
        "audit_or_quality_standard",
    }
    _VALID_MEASUREMENT_PURPOSES = {
        "measure_guideline_application",
        "ongoing_use_or_care_target",
    }
    _VALID_MEASUREMENT_FUNCTIONS = {
        "application_audit_measure",
        "ongoing_care_target",
        "recommendation_trigger_standard",
    }
    _EXCLUDED_MEASUREMENT_FUNCTIONS = {
        "clinical_episode_instruction",
        "diagnostic_or_screening_definition",
        "device_operation",
        "cited_study_outcome",
        "guideline_update",
        "unclear",
    }
    _EXCLUDED_CRITERION_KINDS = {
        "diagnostic_definition",
        "treatment_action_only",
        "not_applicable",
    }
    _SCORE_RANK = {"1": 1, "2": 2, "3-4": 3, "5-6": 4, "7": 5}
    _PREFERRED_ORDER = (_INDICATOR, _FREQUENCY, _METHOD, _VAGUE)

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item21 score from the best local group of accepted objects."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, MonitoringCriteriaRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 MonitoringCriteriaRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_rule(spec)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is not None:
            review_reasons.extend(grouping_result.review_reasons)
            return self._score_verified_groups(
                spec,
                [
                    group
                    for group in grouping_result.accepted_groups
                    if group.group_type == "monitoring_criteria"
                ],
                review_reasons,
            )

        return self._result(
            spec=spec,
            score_range="1",
            matched_rules=[],
            missing=[self._INDICATOR, self._FREQUENCY],
            review_reasons=review_reasons,
        )

    def _score_verified_groups(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        scoreable_groups = self._scoreable_groups(groups, review_reasons)
        if not scoreable_groups:
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=[],
                missing=[self._INDICATOR, self._FREQUENCY],
                review_reasons=review_reasons,
            )

        best_group = self._best_verified_group(scoreable_groups)
        score_range, matched_rules, missing = self._score_verified_group(best_group)
        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=matched_rules,
            missing=missing,
            review_reasons=review_reasons,
        )

    def _scoreable_groups(
        self,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> list[RuleObjectGroup]:
        scoreable: list[RuleObjectGroup] = []
        for group in groups:
            missing_attributes = self._missing_measurement_rubric_attributes(group)
            if missing_attributes:
                review_reasons.append(
                    ReviewReason(
                        code="incomplete_monitoring_group_attributes",
                        message=(
                            f"{group.group_id} 的 item21 rubric attributes 不完整: "
                            f"{', '.join(missing_attributes)}"
                        ),
                        object_type=group.group_type,
                        severity="warning",
                    )
                )
                continue
            exclusion_reason = self._measurement_group_exclusion_reason(group)
            if exclusion_reason:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_monitoring_group_attributes",
                        message=f"{group.group_id} 不满足 item21 评分边界: {exclusion_reason}",
                        object_type=group.group_type,
                        severity="warning",
                    )
                )
                continue
            scoreable.append(group)
        return scoreable

    def _best_verified_group(self, groups: list[RuleObjectGroup]) -> RuleObjectGroup:
        ranked = []
        for pos, group in enumerate(groups):
            score_range, _matched, _missing = self._score_verified_group(group)
            rank = self._SCORE_RANK[score_range]
            confidence = sum(obj.confidence for obj in group.objects)
            ranked.append(((-rank, -len(group.objects), -confidence, pos), group))
        return min(ranked, key=lambda item: item[0])[1]

    def _score_verified_group(self, group: RuleObjectGroup) -> tuple[str, list[str], list[str]]:
        """Score a verified group from complete item21 rubric attributes."""
        return self._score_measurement_rubric_group(group)

    def _score_measurement_rubric_group(self, group: RuleObjectGroup) -> tuple[str, list[str], list[str]]:
        fact_types = {obj.type for obj in group.objects}
        has_measure_attribute = (
            self._attribute_bool(group, "has_measure_target")
            or self._attribute_bool(group, "has_threshold_or_standard")
        )
        has_frequency_attribute = self._attribute_bool(group, "has_timing_or_frequency")
        has_method_attribute = self._attribute_bool(group, "has_measurement_method")
        has_measure = self._INDICATOR in fact_types and has_measure_attribute
        has_frequency = self._FREQUENCY in fact_types and has_frequency_attribute
        has_method = self._METHOD in fact_types and has_method_attribute
        has_vague = self._VAGUE in fact_types

        if has_measure and has_frequency and has_method:
            return "7", [self._INDICATOR, self._FREQUENCY, self._METHOD], []
        if has_measure and has_frequency:
            return "5-6", [self._INDICATOR, self._FREQUENCY], [self._METHOD]
        if has_measure or has_frequency:
            matched = [self._INDICATOR] if has_measure else [self._FREQUENCY]
            missing = [self._FREQUENCY] if has_measure else [self._INDICATOR]
            return "3-4", matched, missing
        if has_vague:
            return "2", [self._VAGUE], [self._INDICATOR, self._FREQUENCY]
        return "1", [], [self._INDICATOR, self._FREQUENCY]

    def _missing_measurement_rubric_attributes(self, group: RuleObjectGroup) -> list[str]:
        required = [
            "criterion_origin",
            "measurement_purpose",
            "criterion_kind",
            "has_measure_target",
            "has_threshold_or_standard",
            "has_timing_or_frequency",
            "has_measurement_method",
            "same_measurement_criterion",
        ]
        return [key for key in required if key not in group.attributes]

    def _measurement_group_exclusion_reason(self, group: RuleObjectGroup) -> str:
        origin = str(group.attributes.get("criterion_origin", "")).strip().casefold()
        purpose = str(group.attributes.get("measurement_purpose", "")).strip().casefold()
        measurement_function = self._measurement_function(group)
        kind = str(group.attributes.get("criterion_kind", "")).strip().casefold()
        same_measurement = str(group.attributes.get("same_measurement_criterion", "")).strip().casefold()
        if origin not in self._VALID_CRITERION_ORIGINS:
            return f"criterion_origin={origin}"
        if purpose not in self._VALID_MEASUREMENT_PURPOSES:
            return f"measurement_purpose={purpose}"
        if measurement_function not in self._VALID_MEASUREMENT_FUNCTIONS:
            return f"measurement_function={measurement_function}"
        if kind in self._EXCLUDED_CRITERION_KINDS:
            return f"criterion_kind={kind}"
        if same_measurement != "true":
            return f"same_measurement_criterion={same_measurement}"
        return ""

    def _attribute_bool(self, group: RuleObjectGroup, key: str) -> bool:
        value = group.attributes.get(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() == "true"

    def _measurement_function(self, group: RuleObjectGroup) -> str:
        value = str(group.attributes.get("measurement_function", "")).strip().casefold()
        if value:
            return value
        purpose = str(group.attributes.get("measurement_purpose", "")).strip().casefold()
        kind = str(group.attributes.get("criterion_kind", "")).strip().casefold()
        if purpose == "measure_guideline_application":
            return "application_audit_measure"
        if purpose == "ongoing_use_or_care_target":
            return "ongoing_care_target"
        if purpose == "diagnosis_or_initial_screening" or kind == "diagnostic_definition":
            return "diagnostic_or_screening_definition"
        if purpose == "acute_management":
            return "clinical_episode_instruction"
        if purpose == "device_operation":
            return "device_operation"
        if purpose == "study_outcome":
            return "cited_study_outcome"
        if purpose == "guideline_update":
            return "guideline_update"
        return "unclear"

    def _matched(self, fact_types: set[str], candidates: tuple[str, ...]) -> list[str]:
        return [fact_type for fact_type in candidates if fact_type in fact_types]

    def _validate_rule(self, spec: ItemSpec) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for fact_type in self._PREFERRED_ORDER:
            if fact_type not in spec.object_specs:
                reasons.append(
                    ReviewReason(
                        code="unknown_monitoring_fact",
                        message=f"MonitoringCriteriaRule 引用了未声明的 fact: {fact_type}",
                        object_type=fact_type,
                        severity="error",
                    )
                )
        return reasons

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

    def _parse_score_range(self, score_range: str) -> list[int]:
        return OrderedMilestoneEngine()._parse_score_range(score_range)

    def _error_result(self, item_id: str, review_reasons: list[ReviewReason]) -> RuleResult:
        return OrderedMilestoneEngine()._error_result(item_id, review_reasons)


class WeightedChecklistEngine:
    """
    Rule profile that computes a score from independent weighted facts.

    This engine is intended for checklist-style items. Each checklist item
    contributes its weight when the corresponding fact exists. An optional gate can
    keep the item at fallback score ranges until a baseline fact is present. This is
    useful for rubrics such as item 8, where a clear inclusion/exclusion criterion
    unlocks the higher weighted range, while a vague mention still deserves a lower
    non-zero range.
    """

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer score by adding weights for satisfied checklist facts."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, WeightedChecklistRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 WeightedChecklistRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_rule(spec, spec.rule)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        fact_types = {obj.type for obj in snapshot.objects}
        if spec.rule.gate and not self._is_condition_met(spec.rule.gate, fact_types):
            fallback_score_range = spec.rule.gate_score_range
            matched_rules = self._matched_facts(spec.rule.gate, fact_types)

            # Gate fallback cases are lower-range branches evaluated only before
            # the main checklist is unlocked. They let a spec distinguish "not
            # mentioned" from "mentioned but not clear enough" without switching
            # to a completely different rule profile.
            for case in spec.rule.gate_fallback_cases:
                if self._is_condition_met(case.condition, fact_types):
                    fallback_score_range = case.score_range
                    matched_rules = self._matched_facts(case.condition, fact_types)
                    break

            allowed_scores = self._parse_score_range(fallback_score_range)
            return RuleResult(
                item_id=spec.item_id,
                status="matched",
                score_range=fallback_score_range,
                allowed_scores=allowed_scores,
                max_score=max(allowed_scores),
                matched_rules=matched_rules,
                missing=sorted(self._missing_for_condition(spec.rule.gate, fact_types)),
                needs_review=bool(review_reasons),
                review_reasons=review_reasons,
            )

        matched_rules: list[str] = []
        if spec.rule.gate:
            matched_rules.extend(self._matched_facts(spec.rule.gate, fact_types))
        score = spec.rule.base_score
        for item in spec.rule.items:
            if item.fact in fact_types:
                score += item.weight
                if item.fact not in matched_rules:
                    matched_rules.append(item.fact)

        score = min(score, spec.rule.max_score)
        score_range = str(score)
        allowed_scores = [score]
        missing = [
            item.fact
            for item in spec.rule.items
            if item.fact not in fact_types
        ]

        return RuleResult(
            item_id=spec.item_id,
            status="matched",
            score_range=score_range,
            allowed_scores=allowed_scores,
            max_score=score,
            matched_rules=matched_rules,
            missing=missing,
            needs_review=bool(review_reasons),
            review_reasons=review_reasons,
        )

    def _validate_rule(self, spec: ItemSpec, rule: WeightedChecklistRule) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        if rule.base_score < 1 or rule.base_score > 7:
            reasons.append(
                ReviewReason(
                    code="invalid_base_score",
                    message=f"base_score 必须位于 1-7 内: {rule.base_score}",
                    severity="error",
                )
            )
        if rule.max_score < 1 or rule.max_score > 7 or rule.max_score < rule.base_score:
            reasons.append(
                ReviewReason(
                    code="invalid_max_score",
                    message=f"max_score 必须位于 1-7 内且不小于 base_score: {rule.max_score}",
                    severity="error",
                )
            )
        reasons.extend(self._validate_score_range(rule.gate_score_range))
        for item in rule.items:
            if item.fact not in spec.object_specs:
                reasons.append(
                    ReviewReason(
                        code="unknown_checklist_fact",
                        message=f"checklist 引用了未声明的 fact: {item.fact}",
                        object_type=item.fact,
                        severity="error",
                    )
                )
            if item.weight < 1:
                reasons.append(
                    ReviewReason(
                        code="invalid_checklist_weight",
                        message=f"{item.fact} 权重必须大于等于 1",
                        object_type=item.fact,
                        severity="error",
                    )
                )
        if rule.gate:
            reasons.extend(self._validate_condition(rule.gate, spec))
        fallback_indexes: list[int] = []
        for index, case in enumerate(rule.gate_fallback_cases):
            reasons.extend(self._validate_score_range(case.score_range))
            reasons.extend(self._validate_condition(case.condition, spec))
            if self._is_unconditional_fallback(case.condition):
                fallback_indexes.append(index)
        if fallback_indexes and fallback_indexes[-1] != len(rule.gate_fallback_cases) - 1:
            reasons.append(
                ReviewReason(
                    code="fallback_not_last",
                    message="WeightedChecklistRule 的 gate fallback 必须放在最后",
                    severity="error",
                )
            )
        return reasons

    def _is_condition_met(self, condition: RuleCondition, fact_types: set[str]) -> bool:
        return OrderedMilestoneEngine()._is_condition_met(condition, fact_types)

    def _matched_facts(self, condition: RuleCondition, fact_types: set[str]) -> list[str]:
        return OrderedMilestoneEngine()._matched_facts(condition, fact_types)

    def _missing_for_condition(self, condition: RuleCondition, fact_types: set[str]) -> set[str]:
        return OrderedMilestoneEngine()._missing_for_condition(condition, fact_types)

    def _validate_condition(self, condition: RuleCondition, spec: ItemSpec) -> list[ReviewReason]:
        return OrderedMilestoneEngine()._validate_condition(condition, spec)

    def _parse_score_range(self, score_range: str) -> list[int]:
        return OrderedMilestoneEngine()._parse_score_range(score_range)

    def _validate_score_range(self, score_range: str) -> list[ReviewReason]:
        return OrderedMilestoneEngine()._validate_score_range(score_range)

    def _is_unconditional_fallback(self, condition: RuleCondition) -> bool:
        return OrderedMilestoneEngine()._is_unconditional_fallback(condition)

    def _error_result(self, item_id: str, review_reasons: list[ReviewReason]) -> RuleResult:
        return OrderedMilestoneEngine()._error_result(item_id, review_reasons)


class _DisclosureEngineMixin:
    """Shared helpers for disclosure-completeness engines."""

    def _validate_conflict_groups(
        self,
        spec: ItemSpec,
        conflict_groups: tuple[tuple[str, ...], ...],
    ) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for group in conflict_groups:
            if len(group) < 2:
                reasons.append(
                    ReviewReason(
                        code="invalid_conflict_group",
                        message="conflict_group 至少需要两个 fact",
                        severity="error",
                    )
                )
            for fact_type in group:
                if fact_type not in spec.object_specs:
                    reasons.append(
                        ReviewReason(
                            code="unknown_conflict_fact",
                            message=f"conflict_group 引用了未声明的 fact: {fact_type}",
                            object_type=fact_type,
                            severity="error",
                        )
                    )
        return reasons

    def _conflict_review_reasons(
        self,
        spec: ItemSpec,
        conflict_groups: tuple[tuple[str, ...], ...],
        fact_types: set[str],
    ) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for group in conflict_groups:
            matched = sorted(fact_type for fact_type in group if fact_type in fact_types)
            if len(matched) > 1:
                reasons.append(
                    ReviewReason(
                        code="conflicting_facts",
                        message=f"{spec.item_id} 同时命中互斥事实: {', '.join(matched)}",
                        object_type=",".join(matched),
                        severity="warning",
                    )
                )
        return reasons

    def _matched(self, fact_types: set[str], candidates: tuple[str, ...]) -> list[str]:
        return [fact_type for fact_type in candidates if fact_type in fact_types]

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

    def _parse_score_range(self, score_range: str) -> list[int]:
        return OrderedMilestoneEngine()._parse_score_range(score_range)

    def _error_result(self, item_id: str, review_reasons: list[ReviewReason]) -> RuleResult:
        return OrderedMilestoneEngine()._error_result(item_id, review_reasons)


def _merged_candidate_groups(grouping_result: RuleGroupingResult, group_type: str) -> list[RuleObjectGroup]:
    """Return candidate groups with verifier attributes merged for portfolio scoring."""
    decision_by_group_id = {decision.group_id: decision for decision in grouping_result.decisions}
    groups: list[RuleObjectGroup] = []
    for group in grouping_result.candidate_groups:
        if group.group_type != group_type:
            continue
        decision = decision_by_group_id.get(group.group_id)
        attributes = dict(group.attributes)
        if decision is not None:
            attributes.update(decision.attributes)
            attributes["verifier_accepted"] = decision.accepted
            attributes["verifier_rejection_code"] = decision.rejection_code
        groups.append(
            RuleObjectGroup(
                group_id=group.group_id,
                group_type=group.group_type,
                objects=group.objects,
                chunk_ids=group.chunk_ids,
                relation_hints=group.relation_hints,
                attributes=attributes,
            )
        )
    return groups


def _portfolio_capped(groups: list[RuleObjectGroup]) -> bool:
    """Return whether builder capped a recommendation portfolio."""
    return any(bool(group.attributes.get("portfolio_anchor_cap_exceeded")) for group in groups)


def _portfolio_anchor_total(groups: list[RuleObjectGroup], anchor_groups: list[RuleObjectGroup]) -> int:
    """Return the best-known recommendation-anchor denominator for a portfolio."""
    totals = [_safe_int(group.attributes.get("portfolio_anchor_total")) for group in groups]
    return max([len(anchor_groups), *totals], default=len(anchor_groups))


def _safe_int(value: object) -> int:
    """Parse an integer metadata value, returning 0 when absent or invalid."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class BenefitHarmBalanceEngine(_DisclosureEngineMixin):
    """Item11 group scorer for benefit-harm balance in recommendation formation."""

    _GROUP_TYPE = "benefit_harm_balance"
    _BENEFIT = "health_benefit"
    _HARM = "harm_side_effect_or_risk"
    _BENEFIT_DATA = "benefit_supporting_data"
    _HARM_DATA = "harm_supporting_data"
    _BALANCE = "benefit_harm_balance_statement"
    _RECOMMENDATION = "recommendation_or_option"
    _IMPACT = "recommendation_impact_statement"

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, BenefitHarmBalanceRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 BenefitHarmBalanceRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is None:
            review_reasons.append(
                ReviewReason(
                    code="missing_grouping_result",
                    message=f"{spec.item_id} 需要 benefit-harm group 才能评分",
                    severity="error",
                )
            )
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=[],
                missing=[self._BENEFIT, self._HARM, self._BALANCE],
                review_reasons=review_reasons,
            )

        groups = _merged_candidate_groups(grouping_result, self._GROUP_TYPE)
        capped = _portfolio_capped(groups)
        if capped:
            review_reasons.append(
                ReviewReason(
                    code="portfolio_anchor_cap_exceeded",
                    message="item11 recommendation portfolio exceeded the configured group cap; score 7 is disabled.",
                    severity="warning",
                )
            )
        anchor_groups = [group for group in groups if self._portfolio_role(group) == "anchor"]
        generic_groups = [group for group in groups if self._is_generic_group(group)]
        global_balance_groups = [
            group
            for group in groups
            if self._is_global_balance_group(group)
            and not self._scope_exclusion_reason(group)
        ]
        systematic_groups = [
            group
            for group in global_balance_groups
            if self._is_systematic_consideration_group(group)
        ]
        scoped_anchors: list[RuleObjectGroup] = []
        for group in anchor_groups:
            exclusion = self._scope_exclusion_reason(group)
            if exclusion:
                review_reasons.append(
                    ReviewReason(
                        code="benefit_harm_group_excluded",
                        message=f"item11 group excluded: {exclusion}",
                        severity="info",
                    )
                )
                continue
            scoped_anchors.append(group)

        omitted_anchor_count = max(0, _portfolio_anchor_total(groups, anchor_groups) - len(anchor_groups))
        retained_partial_count = len(
            [
                group
                for group in scoped_anchors
                if group.attributes.get("verifier_accepted") is False
                and self._same_decision(group)
                and self._group_status(group)["partial"]
            ]
        )
        if retained_partial_count:
            review_reasons.append(
                ReviewReason(
                    code="item11_partial_evidence_retained",
                    message=(
                        "item11 retained verifier-rejected groups as partial evidence because "
                        "same_decision=true and current-guideline attributes were present."
                    ),
                    severity="info",
                )
            )
        if scoped_anchors or omitted_anchor_count:
            statuses = [
                *[self._group_status(group) for group in scoped_anchors],
                *[self._uncovered_status() for _ in range(omitted_anchor_count)],
            ]
            complete = [status for status in statuses if status["complete"]]
            balanced = [status for status in statuses if status["balanced"]]
            explicit_balance = [status for status in statuses if status["explicit_balance"]]
            both_sides = [status for status in statuses if status["both_sides"]]
            both_sides_data = [status for status in statuses if status["both_sides_data"]]
            partial = [status for status in statuses if status["partial"]]
            matched = self._matched_from_statuses(statuses)
            denominator = len(statuses)
            coverage = len(complete) / denominator if denominator else 0.0
            if len(complete) == denominator and not capped:
                best_range = "7"
                best_missing: list[str] = []
            elif coverage >= 0.8 or len(balanced) == denominator:
                best_range = "6"
                best_missing = ["complete_benefit_harm_portfolio"]
            elif systematic_groups and len(partial) >= 2:
                best_range = "6"
                best_missing = ["complete_benefit_harm_portfolio"]
                matched = self._merge_unique(matched, [self._BALANCE])
            elif global_balance_groups and len(partial) >= 2:
                best_range = "4-5"
                best_missing = self._merge_unique(
                    ["complete_benefit_harm_portfolio"],
                    self._portfolio_missing(statuses),
                )
                matched = self._merge_unique(matched, [self._BALANCE])
            elif both_sides_data:
                best_range = "4-5"
                best_missing = self._portfolio_missing(
                    statuses,
                    default=["complete_benefit_harm_portfolio"],
                )
            elif both_sides or explicit_balance:
                best_range = "4-5"
                best_missing = self._portfolio_missing(statuses)
            elif partial:
                best_range = "2-3"
                best_missing = self._portfolio_missing(statuses)
            else:
                best_range = "1"
                best_missing = [self._BENEFIT, self._HARM]
            best_matched = matched
        elif systematic_groups:
            best_range = "4-5" if len(systematic_groups) >= 2 else "2-3"
            best_matched = [self._BALANCE]
            best_missing = [self._RECOMMENDATION, self._BENEFIT, self._HARM]
        elif generic_groups:
            best_range = "2-3"
            best_matched = [self._BALANCE]
            best_missing = [self._RECOMMENDATION, self._BENEFIT, self._HARM]
        else:
            best_range = "1"
            best_matched = []
            best_missing = [self._BENEFIT, self._HARM, self._BALANCE]

        return self._result(
            spec=spec,
            score_range=best_range,
            matched_rules=best_matched,
            missing=best_missing,
            review_reasons=[*review_reasons, *grouping_result.review_reasons],
        )

    def _portfolio_role(self, group: RuleObjectGroup) -> str:
        role = str(group.attributes.get("portfolio_role", "")).strip().casefold()
        if role:
            return role
        if any(obj.type == self._RECOMMENDATION for obj in group.objects):
            return "anchor"
        return "global"

    def _is_generic_group(self, group: RuleObjectGroup) -> bool:
        fact_types = {obj.type for obj in group.objects}
        return (
            "generic_benefit_harm_method_statement" in fact_types
            or self._portfolio_role(group) == "global"
            and any(obj.type == self._BALANCE for obj in group.objects)
        )

    def _is_systematic_consideration_group(self, group: RuleObjectGroup) -> bool:
        if not self._is_global_balance_group(group):
            return False
        return self._has_systematic_balance_context(group)

    def _is_global_balance_group(self, group: RuleObjectGroup) -> bool:
        fact_types = {obj.type for obj in group.objects}
        if not (
            "generic_benefit_harm_method_statement" in fact_types
            or
            self._BALANCE in fact_types
            or self._attribute_bool_or_object(group, "has_explicit_balance", self._BALANCE)
        ):
            return False
        if self._portfolio_role(group) != "global":
            return False
        if not self._accepted_or_same_decision(group):
            return False
        return True

    def _has_systematic_balance_context(self, group: RuleObjectGroup) -> bool:
        if "generic_benefit_harm_method_statement" in {obj.type for obj in group.objects}:
            return True
        text_parts = []
        for obj in group.objects:
            text_parts.extend([obj.value, obj.evidence.quote])
        text = " ".join(part for part in text_parts if part).casefold()
        formulation_terms = (
            "recommendation formulation",
            "formulating recommendations",
            "formulate recommendations",
            "developing recommendations",
            "developed recommendations",
            "making recommendations",
            "recommendation development",
            "guideline development",
            "guideline panel",
            "expert panel",
            "working group",
            "guideline committee",
            "committee considered",
            "panel considered",
            "considered in developing",
            "considered when developing",
            "considered in formulating",
            "considered when formulating",
            "evidence to decision",
            "evidence-to-decision",
            "etd framework",
            "decision framework",
            "pros and cons for each",
            "pros/cons for each",
            "推荐制定",
            "制定推荐",
            "形成推荐",
            "指南制定",
            "专家组",
            "工作组",
            "委员会",
            "证据到决策",
        )
        balance_terms = (
            "pros and cons",
            "pros/cons",
            "benefits and harms",
            "benefits and risks",
            "benefit-harm",
            "benefit-risk",
            "benefit/risk",
            "balance of benefit",
            "balance between benefit",
            "权衡利弊",
            "利弊权衡",
            "获益和风险",
            "获益与风险",
            "获益和危害",
            "获益与危害",
        )
        method_terms = (
            "method",
            "methodology",
            "framework",
            "process",
            "approach",
            "criteria",
            "方法",
            "框架",
            "流程",
            "标准",
        )
        if any(term in text for term in formulation_terms):
            return True
        has_method_context = any(term in text for term in method_terms) and (
            "recommendation" in text
            or "guideline" in text
            or "推荐" in text
            or "指南" in text
        )
        return has_method_context and any(term in text for term in balance_terms)

    def _group_status(self, group: RuleObjectGroup) -> dict[str, object]:
        if self._relation_excluded(group):
            return self._uncovered_status()
        score_range, matched, missing = self._score_group(group)
        has_benefit = self._attribute_bool_or_object(group, "has_benefit", self._BENEFIT)
        has_harm = self._attribute_bool_or_object(group, "has_harm_or_risk", self._HARM)
        benefit_data = self._attribute_bool_or_object(group, "benefit_has_supporting_data", self._BENEFIT_DATA)
        harm_data = self._attribute_bool_or_object(group, "harm_has_supporting_data", self._HARM_DATA)
        explicit_balance = self._attribute_bool_or_object(group, "has_explicit_balance", self._BALANCE)
        affects_recommendation = self._attribute_bool_or_object(group, "balance_affects_recommendation", self._IMPACT)
        verifier_rejected = group.attributes.get("verifier_accepted") is False
        both_sides = has_benefit and has_harm
        both_sides_data = has_benefit and has_harm and benefit_data and harm_data
        balanced = both_sides_data and explicit_balance and not verifier_rejected
        complete = balanced and affects_recommendation and not verifier_rejected
        partial = has_benefit or has_harm or explicit_balance
        return {
            "score_range": score_range,
            "matched": matched,
            "missing": missing,
            "both_sides": both_sides,
            "both_sides_data": both_sides_data,
            "balanced": balanced,
            "complete": complete,
            "partial": partial,
            "explicit_balance": explicit_balance,
        }

    def _uncovered_status(self) -> dict[str, object]:
        return {
            "score_range": "1",
            "matched": [],
            "missing": [self._BENEFIT, self._HARM, self._BALANCE],
            "both_sides": False,
            "both_sides_data": False,
            "balanced": False,
            "complete": False,
            "partial": False,
            "explicit_balance": False,
        }

    def _matched_from_statuses(self, statuses: list[dict[str, object]]) -> list[str]:
        matched: list[str] = []
        for status in statuses:
            for item in status["matched"]:  # type: ignore[index]
                if item not in matched:
                    matched.append(item)
        return matched

    def _portfolio_missing(
        self,
        statuses: list[dict[str, object]],
        *,
        default: list[str] | None = None,
    ) -> list[str]:
        missing: list[str] = []
        for status in statuses:
            for item in status["missing"]:  # type: ignore[index]
                if item not in missing:
                    missing.append(item)
        return missing or default or [self._BENEFIT, self._HARM]

    def _merge_unique(self, first: list[str], second: list[str]) -> list[str]:
        merged = list(first)
        for item in second:
            if item not in merged:
                merged.append(item)
        return merged

    def _score_group(self, group: RuleObjectGroup) -> tuple[str, list[str], list[str]]:
        fact_types = {obj.type for obj in group.objects}
        has_recommendation = self._attribute_bool_or_object(group, "has_recommendation_or_option", self._RECOMMENDATION)
        has_benefit = self._attribute_bool_or_object(group, "has_benefit", self._BENEFIT)
        has_harm = self._attribute_bool_or_object(group, "has_harm_or_risk", self._HARM)
        benefit_data = self._attribute_bool_or_object(group, "benefit_has_supporting_data", self._BENEFIT_DATA)
        harm_data = self._attribute_bool_or_object(group, "harm_has_supporting_data", self._HARM_DATA)
        explicit_balance = self._attribute_bool_or_object(group, "has_explicit_balance", self._BALANCE)
        affects_recommendation = self._attribute_bool_or_object(group, "balance_affects_recommendation", self._IMPACT)

        matched = self._matched(
            fact_types,
            (
                self._RECOMMENDATION,
                self._BENEFIT,
                self._HARM,
                self._BENEFIT_DATA,
                self._HARM_DATA,
                self._BALANCE,
                self._IMPACT,
            ),
        )
        if has_benefit and has_harm and benefit_data and harm_data and explicit_balance:
            if has_recommendation and affects_recommendation:
                return "7", matched, []
            return "6-7", matched, [self._IMPACT]
        if has_benefit and has_harm and benefit_data and harm_data:
            return "4-5", matched, [self._BALANCE]
        if has_benefit or has_harm or explicit_balance:
            missing = []
            if not has_benefit:
                missing.append(self._BENEFIT)
            if not has_harm:
                missing.append(self._HARM)
            if has_benefit and not benefit_data:
                missing.append(self._BENEFIT_DATA)
            if has_harm and not harm_data:
                missing.append(self._HARM_DATA)
            return "2-3", matched, missing
        return "1", [], [self._BENEFIT, self._HARM]

    def _scope_exclusion_reason(self, group: RuleObjectGroup) -> str:
        scope = str(group.attributes.get("scope", "")).strip().casefold()
        if scope and scope != "current_guideline":
            return f"scope={scope}"
        return ""

    def _relation_excluded(self, group: RuleObjectGroup) -> bool:
        if group.attributes.get("verifier_accepted") is False and not self._same_decision(group):
            return True
        same_decision = group.attributes.get("same_decision")
        if same_decision is not None and not self._attribute_bool(group, "same_decision"):
            return True
        return False

    def _same_decision(self, group: RuleObjectGroup) -> bool:
        same_decision = group.attributes.get("same_decision")
        return same_decision is not None and self._attribute_bool(group, "same_decision")

    def _accepted_or_same_decision(self, group: RuleObjectGroup) -> bool:
        if group.attributes.get("verifier_accepted") is False:
            return self._same_decision(group)
        same_decision = group.attributes.get("same_decision")
        return same_decision is None or self._attribute_bool(group, "same_decision")

    def _attribute_bool(self, group: RuleObjectGroup, key: str) -> bool:
        value = group.attributes.get(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() == "true"

    def _attribute_bool_or_object(self, group: RuleObjectGroup, key: str, object_type: str) -> bool:
        if key in group.attributes:
            return self._attribute_bool(group, key)
        return any(obj.type == object_type for obj in group.objects)


class RecommendationEvidenceLinkEngine(_DisclosureEngineMixin):
    """Item12 group scorer for recommendation-level links to supporting evidence."""

    _GROUP_TYPE = "recommendation_evidence_link"
    _RECOMMENDATION = "recommendation_statement"
    _IDENTIFIER = "recommendation_identifier"
    _EVIDENCE = "supporting_evidence_description"
    _CITATION = "citation_group"
    _GRADE = "evidence_level_or_grade"
    _RATIONALE = "rationale_or_evidence_summary"
    _TABLE_REF = "evidence_table_or_figure_reference"
    _LINK = "recommendation_evidence_link_statement"

    _SPECIFIC_EVIDENCE_TYPES = (
        _EVIDENCE,
        _CITATION,
        _GRADE,
        _RATIONALE,
        _TABLE_REF,
        _LINK,
    )

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, RecommendationEvidenceLinkRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 RecommendationEvidenceLinkRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is None:
            review_reasons.append(
                ReviewReason(
                    code="missing_grouping_result",
                    message=f"{spec.item_id} 需要 recommendation-evidence group 才能评分",
                    severity="error",
                )
            )
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=[],
                missing=[self._RECOMMENDATION, self._EVIDENCE],
                review_reasons=review_reasons,
            )

        groups = _merged_candidate_groups(grouping_result, self._GROUP_TYPE)
        capped = _portfolio_capped(groups)
        if capped:
            review_reasons.append(
                ReviewReason(
                    code="portfolio_anchor_cap_exceeded",
                    message="item12 recommendation portfolio exceeded the configured group cap; score 7 is disabled.",
                    severity="warning",
                )
            )
        anchor_groups = [group for group in groups if self._portfolio_role(group) == "anchor"]
        generic_groups = [group for group in groups if self._is_generic_link(group)]
        valid_groups: list[RuleObjectGroup] = []
        systematic_groups: list[RuleObjectGroup] = []
        partial_groups: list[RuleObjectGroup] = []
        for group in anchor_groups:
            exclusion = self._group_exclusion_reason(group)
            if exclusion:
                review_reasons.append(
                    ReviewReason(
                        code="recommendation_evidence_group_excluded",
                        message=f"item12 group excluded: {exclusion}",
                        severity="info",
                    )
                )
                continue
            if self._is_specific_link(group):
                valid_groups.append(group)
                if self._mapping_strength(group) == "systematic":
                    systematic_groups.append(group)
                else:
                    partial_groups.append(group)
            elif self._is_generic_link(group):
                generic_groups.append(group)

        if not anchor_groups:
            if generic_groups:
                return self._result(
                    spec=spec,
                    score_range="2-3",
                    matched_rules=[self._LINK],
                    missing=[self._RECOMMENDATION, self._EVIDENCE],
                    review_reasons=[*review_reasons, *grouping_result.review_reasons],
                )
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=[],
                missing=[self._RECOMMENDATION, self._EVIDENCE],
                review_reasons=[*review_reasons, *grouping_result.review_reasons],
            )

        matched_rules = self._matched_link_rules(valid_groups)
        explicit_systematic_statement = any(
            any(obj.type == self._LINK for obj in group.objects)
            and self._mapping_strength(group) == "systematic"
            for group in [*valid_groups, *generic_groups]
        )
        denominator = _portfolio_anchor_total(groups, anchor_groups)
        coverage = len(valid_groups) / denominator if denominator else 0.0
        systematic_coverage = len(systematic_groups) / denominator if denominator else 0.0
        if valid_groups and denominator >= 2 and coverage == 1.0 and systematic_coverage == 1.0 and not capped:
            score_range = "7"
            missing: list[str] = []
        elif (
            valid_groups
            and len(valid_groups) >= 2
            and (
                coverage >= 0.8
                or systematic_coverage >= 0.8
                or (explicit_systematic_statement and len(valid_groups) >= 2)
            )
        ):
            score_range = "6"
            missing = ["complete_recommendation_evidence_portfolio"]
        elif valid_groups or partial_groups:
            score_range = "4-5"
            missing = ["systematic_recommendation_evidence_mapping"]
        elif generic_groups:
            score_range = "2-3"
            missing = [self._RECOMMENDATION, self._EVIDENCE]
            matched_rules = [self._LINK]
        else:
            score_range = "1"
            missing = [self._RECOMMENDATION, self._EVIDENCE]

        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=matched_rules,
            missing=missing,
            review_reasons=[*review_reasons, *grouping_result.review_reasons],
        )

    def _portfolio_role(self, group: RuleObjectGroup) -> str:
        role = str(group.attributes.get("portfolio_role", "")).strip().casefold()
        if role:
            return role
        if any(obj.type in {self._RECOMMENDATION, self._IDENTIFIER} for obj in group.objects):
            return "anchor"
        return "global"

    def _group_exclusion_reason(self, group: RuleObjectGroup) -> str:
        scope = str(group.attributes.get("scope", "")).strip().casefold()
        same_recommendation = group.attributes.get("same_recommendation")
        same_unit = group.attributes.get("same_evidence_unit")
        if scope and scope != "current_guideline":
            return f"scope={scope}"
        if same_recommendation is not None and not self._attribute_bool(group, "same_recommendation"):
            return "same_recommendation=false"
        if same_unit is not None and not self._attribute_bool(group, "same_evidence_unit"):
            return "same_evidence_unit=false"
        return ""

    def _is_specific_link(self, group: RuleObjectGroup) -> bool:
        if group.attributes.get("verifier_accepted") is False:
            return False
        has_recommendation = self._attribute_bool_or_objects(
            group,
            "has_recommendation",
            (self._RECOMMENDATION, self._IDENTIFIER),
        )
        has_evidence = self._attribute_bool_or_objects(
            group,
            "has_specific_supporting_evidence",
            self._SPECIFIC_EVIDENCE_TYPES,
        )
        link_type = str(group.attributes.get("link_type", "")).strip().casefold()
        mapping_strength = self._mapping_strength(group)
        return (
            has_recommendation
            and has_evidence
            and link_type not in {"generic_method", "none"}
            and mapping_strength in {"partial", "systematic"}
        )

    def _is_generic_link(self, group: RuleObjectGroup) -> bool:
        fact_types = {obj.type for obj in group.objects}
        link_type = str(group.attributes.get("link_type", "")).strip().casefold()
        return self._LINK in fact_types or link_type == "generic_method"

    def _mapping_strength(self, group: RuleObjectGroup) -> str:
        return str(group.attributes.get("mapping_strength", "")).strip().casefold()

    def _link_richness(self, group: RuleObjectGroup) -> int:
        fact_types = {obj.type for obj in group.objects}
        return len(set(self._SPECIFIC_EVIDENCE_TYPES) & fact_types)

    def _matched_link_rules(self, groups: list[RuleObjectGroup]) -> list[str]:
        matched: list[str] = []
        for group in groups:
            for object_type in (
                self._RECOMMENDATION,
                self._IDENTIFIER,
                *self._SPECIFIC_EVIDENCE_TYPES,
            ):
                if any(obj.type == object_type for obj in group.objects) and object_type not in matched:
                    matched.append(object_type)
        return matched

    def _attribute_bool(self, group: RuleObjectGroup, key: str) -> bool:
        value = group.attributes.get(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() == "true"

    def _attribute_bool_or_objects(self, group: RuleObjectGroup, key: str, object_types: tuple[str, ...]) -> bool:
        if key in group.attributes:
            return self._attribute_bool(group, key)
        fact_types = {obj.type for obj in group.objects}
        return bool(set(object_types) & fact_types)


class ApplicationFactorEngine(_DisclosureEngineMixin):
    """Item18 group scorer for accepted application-factor groups."""

    _GROUP_TYPE = "application_factor"
    _FACILITATOR = "facilitator"
    _BARRIER = "barrier"
    _COLLECTION = "collection_method"
    _IMPACT = "impact_on_guideline_or_recommendations"
    _MATCH_ORDER = (_FACILITATOR, _BARRIER, _COLLECTION, _IMPACT)
    _RUBRIC_ATTRIBUTES = (
        "scope",
        "rubric_role",
        "factor_direction",
        "factor_specificity",
        "identification_basis",
        "application_impact",
        "application_link",
        "same_application_issue",
    )
    _MID_BASIS = {
        "explicit_collection_or_analysis",
    }
    _MID_LINKS = {
        "affects_recommendations_or_strategy",
        "affects_implementation_strategy_or_tools",
    }

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item18 score from verified application-factor groups."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, ApplicationFactorRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 ApplicationFactorRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_rule(spec)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is not None:
            review_reasons.extend(grouping_result.review_reasons)
            groups = [
                group
                for group in grouping_result.accepted_groups
                if group.group_type == self._GROUP_TYPE
            ]
            return self._score_groups(spec, groups, review_reasons)

        return self._result(
            spec=spec,
            score_range="1",
            matched_rules=[],
            missing=[self._FACILITATOR, self._BARRIER],
            review_reasons=review_reasons,
        )

    def _score_groups(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        valid_groups = self._valid_groups(groups, review_reasons)
        if not valid_groups:
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=[],
                missing=[self._FACILITATOR, self._BARRIER],
                review_reasons=review_reasons,
            )

        best_group, score_range, missing = self._best_group_score(valid_groups)
        return self._group_result(
            spec,
            score_range,
            [best_group],
            missing=missing,
            review_reasons=review_reasons,
        )

    def _valid_groups(
        self,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> list[RuleObjectGroup]:
        valid: list[RuleObjectGroup] = []
        for group in groups:
            missing_attributes = self._missing_rubric_attributes(group)
            if missing_attributes:
                review_reasons.append(
                    ReviewReason(
                        code="incomplete_application_factor_attributes",
                        message=(
                            f"{group.group_id} 的 item18 rubric attributes 不完整: "
                            f"{', '.join(missing_attributes)}"
                        ),
                        object_type=group.group_type,
                        severity="warning",
                    )
                )
                continue
            exclusion_reason = self._exclusion_reason(group)
            if exclusion_reason:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_application_factor_group",
                        message=f"{group.group_id} 不满足 item18 评分边界: {exclusion_reason}",
                        object_type=group.group_type,
                        severity="warning",
                    )
                )
                continue
            if self._directions(group):
                valid.append(group)
        return valid

    def _exclusion_reason(self, group: RuleObjectGroup) -> str:
        scope = self._attribute(group, "scope")
        role = self._attribute(group, "rubric_role")
        same_unit = self._same_application_unit(group)
        if scope != "current_guideline_application":
            return f"scope={scope}"
        if role != "application_factor":
            return f"rubric_role={role}"
        if not same_unit:
            return "same_application_evidence_unit=false"
        return ""

    def _best_group_score(self, groups: list[RuleObjectGroup]) -> tuple[RuleObjectGroup, str, list[str]]:
        ranked: list[tuple[tuple[int, int, int, int], RuleObjectGroup, str, list[str]]] = []
        for position, group in enumerate(groups):
            score_range, missing = self._score_group(group)
            rank = {"1": 1, "2-3": 2, "4": 3, "5-6": 4, "7": 5}[score_range]
            matched_count = len(self._matched_rules_from_groups([group]))
            ranked.append(((-rank, -matched_count, -len(group.objects), position), group, score_range, missing))
        _sort_key, group, score_range, missing = min(ranked, key=lambda item: item[0])
        return group, score_range, missing

    def _score_group(self, group: RuleObjectGroup) -> tuple[str, list[str]]:
        fact_types = self._fact_types(group)
        directions = self._directions(group)
        has_facilitator = self._FACILITATOR in fact_types and self._FACILITATOR in directions
        has_barrier = self._BARRIER in fact_types and self._BARRIER in directions
        has_both_directions = has_facilitator and has_barrier
        has_collection_basis = (
            self._COLLECTION in fact_types
            and self._attribute(group, "identification_basis") in self._MID_BASIS
        )
        has_application_impact = (
            self._IMPACT in fact_types
            and self._application_impact(group) in self._MID_LINKS
        )

        if has_both_directions and has_collection_basis and has_application_impact:
            return "7", []
        if has_both_directions and (has_collection_basis or has_application_impact):
            return "5-6", [self._COLLECTION, self._IMPACT]
        if has_both_directions:
            return "4", [self._COLLECTION, self._IMPACT]
        if has_facilitator or has_barrier:
            return "2-3", [self._BARRIER, self._COLLECTION, self._IMPACT]
        return "1", [self._FACILITATOR, self._BARRIER]

    def _directions(self, group: RuleObjectGroup) -> set[str]:
        direction = self._attribute(group, "factor_direction")
        if direction == "mixed":
            return {self._FACILITATOR, self._BARRIER}
        if direction == self._FACILITATOR:
            return {self._FACILITATOR}
        if direction == self._BARRIER:
            return {self._BARRIER}
        return set()

    def _group_result(
        self,
        spec: ItemSpec,
        score_range: str,
        groups: list[RuleObjectGroup],
        *,
        missing: list[str],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        matched_rules = self._matched_rules_from_groups(groups)
        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=matched_rules,
            missing=missing,
            review_reasons=review_reasons,
        )

    def _matched_rules_from_groups(self, groups: list[RuleObjectGroup]) -> list[str]:
        fact_types: set[str] = set()
        for group in groups:
            group_fact_types = self._fact_types(group)
            fact_types.update(group_fact_types & {self._FACILITATOR, self._BARRIER})
            directions = self._directions(group)
            fact_types.update(directions & group_fact_types)
            if self._COLLECTION in group_fact_types and self._attribute(group, "identification_basis") in self._MID_BASIS:
                fact_types.add(self._COLLECTION)
            if self._IMPACT in group_fact_types and self._application_impact(group) in self._MID_LINKS:
                fact_types.add(self._IMPACT)
        return self._matched(fact_types, self._MATCH_ORDER)

    def _fact_types(self, group: RuleObjectGroup) -> set[str]:
        return {obj.type for obj in group.objects}

    def _attribute(self, group: RuleObjectGroup, key: str) -> str:
        return str(group.attributes.get(key, "")).strip().casefold()

    def _application_impact(self, group: RuleObjectGroup) -> str:
        return self._attribute(group, "application_impact") or self._attribute(group, "application_link")

    def _same_application_unit(self, group: RuleObjectGroup) -> bool:
        if "same_application_evidence_unit" in group.attributes:
            return self._attribute(group, "same_application_evidence_unit") == "true"
        return self._attribute(group, "same_application_issue") == "true"

    def _missing_rubric_attributes(self, group: RuleObjectGroup) -> list[str]:
        required = [
            "scope",
            "rubric_role",
            "factor_direction",
            "factor_specificity",
            "identification_basis",
        ]
        missing = [key for key in required if key not in group.attributes]
        if (
            "same_application_evidence_unit" not in group.attributes
            and "same_application_issue" not in group.attributes
        ):
            missing.append("same_application_evidence_unit")
        if "application_impact" not in group.attributes and "application_link" not in group.attributes:
            missing.append("application_impact")
        return missing

    def _validate_rule(self, spec: ItemSpec) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for fact_type in self._MATCH_ORDER:
            if fact_type not in spec.object_specs:
                reasons.append(
                    ReviewReason(
                        code="unknown_application_factor_fact",
                        message=f"ApplicationFactorRule 引用了未声明的 fact: {fact_type}",
                        object_type=fact_type,
                        severity="error",
                    )
                )
        return reasons


class HealthQuestionEngine(_DisclosureEngineMixin):
    """AGREE-II item 2 health-question completeness scoring by verified groups."""

    _GROUP_TYPE = "health_question"
    _P = "question_population_or_problem"
    _I = "question_intervention_or_exposure"
    _C = "question_comparator"
    _O = "question_outcome"
    _S = "question_study_design"
    _MATCH_ORDER = (
        "health_question_statement",
        "implicit_question_scope",
        _P,
        _I,
        _C,
        _O,
        _S,
    )
    _PICOS_BY_TYPE = {
        _P: "P",
        _I: "I",
        _C: "C",
        _O: "O",
        _S: "S",
    }

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item2 score from the best current-guideline health-question group."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, HealthQuestionRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 HealthQuestionRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_rule(spec)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is not None:
            review_reasons.extend(grouping_result.review_reasons)
            groups = [
                group
                for group in grouping_result.accepted_groups
                if group.group_type == self._GROUP_TYPE
            ]
            return self._score_groups(spec, snapshot, groups, review_reasons)

        return self._score_snapshot(spec, snapshot, review_reasons)

    def _score_groups(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        scored = [
            (*self._score_group(group), group)
            for group in groups
        ]
        valid = [
            item
            for item in scored
            if item[0] != "1"
        ]
        if not valid:
            matched = self._matched({obj.type for obj in snapshot.objects}, self._MATCH_ORDER)
            if matched:
                review_reasons.append(
                    ReviewReason(
                        code="no_valid_health_question_group",
                        message="未形成当前指南同一卫生问题的有效 PICO/PICOS 证据组。",
                        severity="warning",
                    )
                )
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=matched,
                missing=[self._P, self._I],
                review_reasons=review_reasons,
            )

        score_range, matched_rules, missing, _group = max(
            valid,
            key=lambda item: (
                max(self._parse_score_range(item[0])),
                len(item[1]),
                -len(item[2]),
            ),
        )
        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=matched_rules,
            missing=missing,
            review_reasons=review_reasons,
        )

    def _score_group(self, group: RuleObjectGroup) -> tuple[str, list[str], list[str]]:
        fact_types = {obj.type for obj in group.objects}
        matched_rules = self._matched(fact_types, self._MATCH_ORDER)
        if self._attribute(group, "same_question") == "false":
            return "1", matched_rules, ["same_question"]
        scope = self._attribute(group, "scope")
        if scope in {"external_or_cited", "evidence_selection_only", "unclear"}:
            return "1", matched_rules, ["scope"]

        picos = self._picos_present(group, fact_types)
        framing = self._attribute(group, "question_framing")
        if framing == "background":
            return self._score_background(picos, matched_rules)
        if framing == "foreground":
            return self._score_foreground(picos, matched_rules)
        if framing == "implicit":
            return self._score_implicit(picos, matched_rules)
        return "1", matched_rules, ["question_framing"]

    def _score_snapshot(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        fact_types = {obj.type for obj in snapshot.objects}
        score_range, matched_rules, missing = self._score_implicit(
            {self._PICOS_BY_TYPE[obj_type] for obj_type in fact_types if obj_type in self._PICOS_BY_TYPE},
            self._matched(fact_types, self._MATCH_ORDER),
        )
        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=matched_rules,
            missing=missing,
            review_reasons=review_reasons,
        )

    def _score_background(self, picos: set[str], matched_rules: list[str]) -> tuple[str, list[str], list[str]]:
        has_p = "P" in picos
        has_i = "I" in picos
        if has_p and has_i:
            return "5-7", matched_rules, []
        if has_p or has_i:
            return "2-4", matched_rules, [self._P, self._I]
        return "1", matched_rules, [self._P, self._I]

    def _score_foreground(self, picos: set[str], matched_rules: list[str]) -> tuple[str, list[str], list[str]]:
        count = len(picos)
        if count >= 5:
            return "7", matched_rules, []
        if count == 4:
            return "6", matched_rules, self._missing_picos(picos)
        if count == 3:
            return "4-5", matched_rules, self._missing_picos(picos)
        if count == 2:
            return "2-3", matched_rules, self._missing_picos(picos)
        return "1", matched_rules, [self._P, self._I]

    def _score_implicit(self, picos: set[str], matched_rules: list[str]) -> tuple[str, list[str], list[str]]:
        pico = picos & {"P", "I", "C", "O"}
        count = len(pico)
        if count >= 4:
            return "6-7", matched_rules, []
        if count == 3:
            return "4-5", matched_rules, self._missing_pico(pico)
        if {"P", "I"} <= pico:
            return "4-5", matched_rules, self._missing_pico(pico)
        if count == 2:
            return "2-3", matched_rules, self._missing_pico(pico)
        return "1", matched_rules, [self._P, self._I]

    def _picos_present(self, group: RuleObjectGroup, fact_types: set[str]) -> set[str]:
        values = group.attributes.get("picos_present")
        if isinstance(values, (list, tuple)):
            parsed = {str(value).strip().upper() for value in values}
            return parsed & {"P", "I", "C", "O", "S"}
        if isinstance(values, str):
            normalized = values.upper()
            for separator in (";", "|", "/", " "):
                normalized = normalized.replace(separator, ",")
            parsed = {token.strip() for token in normalized.split(",") if token.strip()}
            if len(parsed) == 1:
                token = next(iter(parsed))
                parsed = set(token) if set(token) <= {"P", "I", "C", "O", "S"} else parsed
            return parsed & {"P", "I", "C", "O", "S"}
        return {self._PICOS_BY_TYPE[obj_type] for obj_type in fact_types if obj_type in self._PICOS_BY_TYPE}

    def _missing_picos(self, picos: set[str]) -> list[str]:
        return [self._object_type_for_letter(letter) for letter in ("P", "I", "C", "O", "S") if letter not in picos]

    def _missing_pico(self, pico: set[str]) -> list[str]:
        return [self._object_type_for_letter(letter) for letter in ("P", "I", "C", "O") if letter not in pico]

    def _object_type_for_letter(self, letter: str) -> str:
        for object_type, pico_letter in self._PICOS_BY_TYPE.items():
            if pico_letter == letter:
                return object_type
        return letter

    def _attribute(self, group: RuleObjectGroup, key: str) -> str:
        return str(group.attributes.get(key, "")).strip().casefold()

    def _validate_rule(self, spec: ItemSpec) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for fact_type in self._MATCH_ORDER:
            if fact_type not in spec.object_specs:
                reasons.append(
                    ReviewReason(
                        code="unknown_health_question_fact",
                        message=f"HealthQuestionRule 引用了未声明的 fact: {fact_type}",
                        object_type=fact_type,
                        severity="error",
                    )
                )
        return reasons


class FundingDisclosureEngine(_DisclosureEngineMixin):
    """AGREE-II item 22 funding disclosure completeness scoring."""

    _FUNDING_FACTS = ("funding_disclosed", "commission_or_sponsorship_statement")

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, FundingDisclosureRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 FundingDisclosureRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_conflict_groups(spec, spec.rule.conflict_groups)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        fact_types = {obj.type for obj in snapshot.objects}
        review_reasons.extend(self._conflict_review_reasons(spec, spec.rule.conflict_groups, fact_types))

        if grouping_result is not None:
            review_reasons.extend(grouping_result.review_reasons)
            funding_groups = [
                group
                for group in grouping_result.accepted_groups
                if group.group_type == "funding_disclosure"
            ]
            if not funding_groups:
                return self._result(
                    spec=spec,
                    score_range="1",
                    matched_rules=[],
                    missing=["no_funding_statement", "funding_disclosed"],
                    review_reasons=review_reasons,
                )
            best_score, best_rules, best_missing, best_reasons = self._best_funding_group_score(funding_groups)
            review_reasons.extend(best_reasons)
            return self._result(
                spec=spec,
                score_range=best_score,
                matched_rules=best_rules,
                missing=best_missing,
                review_reasons=review_reasons,
            )

        if "no_funding_statement" in fact_types:
            return self._result(
                spec=spec,
                score_range="7",
                matched_rules=["no_funding_statement"],
                missing=[],
                review_reasons=review_reasons,
            )

        has_funding_disclosure = (
            any(fact_type in fact_types for fact_type in self._FUNDING_FACTS)
            or "funding_body_independence_statement" in fact_types
        )
        if not has_funding_disclosure:
            if "related_meeting_or_convening_funding" in fact_types:
                review_reasons.append(
                    ReviewReason(
                        code="related_funding_not_guideline_specific",
                        message="仅命中会议、workshop 或 convening 资助信息，未明确指向本指南资助状态，按未披露处理。",
                        object_type="related_meeting_or_convening_funding",
                        severity="warning",
                    )
                )
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=self._matched(fact_types, ("related_meeting_or_convening_funding",)),
                missing=["no_funding_statement", "funding_disclosed"],
                review_reasons=review_reasons,
            )

        funding_matched = self._matched(
            fact_types,
            (*self._FUNDING_FACTS, "direct_no_influence_statement", "funding_body_independence_statement"),
        )

        if "direct_no_influence_statement" in fact_types or "funding_body_independence_statement" in fact_types:
            return self._result(
                spec=spec,
                score_range="7",
                matched_rules=funding_matched,
                missing=[],
                review_reasons=review_reasons,
            )

        if "sponsor_influence_statement" in fact_types:
            return self._result(
                spec=spec,
                score_range="5-6",
                matched_rules=[*funding_matched, "sponsor_influence_statement"],
                missing=["direct_no_influence_statement"],
                review_reasons=review_reasons,
            )

        if "for_profit_funding" in fact_types:
            return self._result(
                spec=spec,
                score_range="2-4",
                matched_rules=[*funding_matched, "for_profit_funding"],
                missing=["direct_no_influence_statement"],
                review_reasons=review_reasons,
            )

        if "no_industry_support_statement" in fact_types:
            return self._result(
                spec=spec,
                score_range="5-6",
                matched_rules=[*funding_matched, "no_industry_support_statement"],
                missing=["direct_no_influence_statement"],
                review_reasons=review_reasons,
            )

        if "non_profit_or_government_funding" in fact_types:
            matched_rules = [*funding_matched, "non_profit_or_government_funding"]
            if "generic_editorial_independence_statement" in fact_types:
                matched_rules.append("generic_editorial_independence_statement")
            return self._result(
                spec=spec,
                score_range="5-6",
                matched_rules=matched_rules,
                missing=["direct_no_influence_statement"],
                review_reasons=review_reasons,
            )

        if "generic_editorial_independence_statement" in fact_types:
            return self._result(
                spec=spec,
                score_range="2-4",
                matched_rules=[*funding_matched, "generic_editorial_independence_statement"],
                missing=[
                    "for_profit_funding",
                    "non_profit_or_government_funding",
                    "direct_no_influence_statement",
                ],
                review_reasons=review_reasons,
            )

        review_reasons.append(
            ReviewReason(
                code="missing_funder_type",
                message="已披露本指南资助状态，但资助方类型无法确认，按较低披露完整性评分。",
                object_type="funding_disclosed",
                severity="warning",
            )
        )
        return self._result(
            spec=spec,
            score_range="2-4",
            matched_rules=funding_matched,
            missing=["for_profit_funding", "non_profit_or_government_funding", "direct_no_influence_statement"],
            review_reasons=review_reasons,
        )

    def _best_funding_group_score(
        self,
        groups: list[RuleObjectGroup],
    ) -> tuple[str, list[str], list[str], list[ReviewReason]]:
        scored = [
            (*self._score_funding_group(group), group)
            for group in groups
        ]
        ranked = sorted(
            scored,
            key=lambda item: (
                -self._score_rank(item[0]),
                -len(item[1]),
                len(item[2]),
            ),
        )
        score_range, matched_rules, missing, reasons, _group = ranked[0]
        return score_range, matched_rules, missing, reasons

    def _score_funding_group(
        self,
        group: RuleObjectGroup,
    ) -> tuple[str, list[str], list[str], list[ReviewReason]]:
        fact_types = {obj.type for obj in group.objects}
        attributes = group.attributes
        status = str(attributes.get("status", "")).strip()
        scope = str(attributes.get("scope", "")).strip()
        funder_type = str(attributes.get("funder_type", "")).strip()
        influence = str(attributes.get("influence", "")).strip()
        matched_rules = self._matched(
            fact_types,
            (
                "no_funding_statement",
                "funding_disclosed",
                "commission_or_sponsorship_statement",
                "acknowledged_financial_support",
                "development_meeting_funding_statement",
                "related_meeting_or_convening_funding",
                "funding_section_heading",
                "funding_section_value",
                "direct_no_influence_statement",
                "funding_body_independence_statement",
                "sponsor_influence_statement",
                "no_industry_support_statement",
                "generic_editorial_independence_statement",
                "for_profit_funding",
                "non_profit_or_government_funding",
            ),
        )
        reasons: list[ReviewReason] = []

        if "no_funding_statement" in fact_types or status == "none":
            return "7", matched_rules or ["no_funding_statement"], [], reasons

        if "related_meeting_or_convening_funding" in fact_types or scope == "related_only":
            reasons.append(
                ReviewReason(
                    code="related_funding_not_guideline_specific",
                    message="仅命中相关 meeting、workshop 或 convening 资助信息，未明确指向本指南资助状态。",
                    object_type="related_meeting_or_convening_funding",
                    severity="warning",
                )
            )
            return "1", matched_rules, ["no_funding_statement", "funding_disclosed"], reasons

        if "development_meeting_funding_statement" in fact_types or status == "meeting_only" or scope == "development_meeting":
            reasons.append(
                ReviewReason(
                    code="development_meeting_funding_only",
                    message="仅确认当前指南制定会议或 convening 的资助状态，未完整披露指南本身资助影响。",
                    object_type="development_meeting_funding_statement",
                    severity="warning",
                )
            )
            return "2-4", matched_rules, ["funding_disclosed", "direct_no_influence_statement"], reasons

        has_funding = (
            any(fact_type in fact_types for fact_type in self._FUNDING_FACTS)
            or "acknowledged_financial_support" in fact_types
            or "funding_section_value" in fact_types
            or "funding_body_independence_statement" in fact_types
            or status in {"funded", "commissioned"}
            or influence == "funding_body_independence"
        )
        if not has_funding:
            return "1", matched_rules, ["no_funding_statement", "funding_disclosed"], reasons

        if (
            "direct_no_influence_statement" in fact_types
            or "funding_body_independence_statement" in fact_types
            or influence in {"direct_no_influence", "funding_body_independence"}
        ):
            return "7", matched_rules, [], reasons

        if "sponsor_influence_statement" in fact_types or influence == "sponsor_influence":
            return "5-6", matched_rules, ["direct_no_influence_statement"], reasons

        if "for_profit_funding" in fact_types or funder_type == "for_profit":
            return "2-4", matched_rules, ["direct_no_influence_statement"], reasons

        if (
            "non_profit_or_government_funding" in fact_types
            or funder_type == "non_profit_or_government"
            or "no_industry_support_statement" in fact_types
            or influence == "no_industry_support"
        ):
            return "5-6", matched_rules, ["direct_no_influence_statement"], reasons

        if "generic_editorial_independence_statement" in fact_types or influence == "generic_independence":
            return (
                "2-4",
                matched_rules,
                ["for_profit_funding", "non_profit_or_government_funding", "direct_no_influence_statement"],
                reasons,
            )

        reasons.append(
            ReviewReason(
                code="missing_funder_type",
                message="已披露本指南资助状态，但资助方类型无法确认，按较低披露完整性评分。",
                object_type="funding_disclosed",
                severity="warning",
            )
        )
        return (
            "2-4",
            matched_rules,
            ["for_profit_funding", "non_profit_or_government_funding", "direct_no_influence_statement"],
            reasons,
        )

    def _score_rank(self, score_range: str) -> int:
        return max(self._parse_score_range(score_range))


class ImplementationToolEngine(_DisclosureEngineMixin):
    """Item19 group scorer for accepted implementation-support/tool groups."""

    _GROUP_TYPE = "implementation_tool"
    _ADVICE = "implementation_advice_or_discussion"
    _SECTION = "implementation_section_or_module"
    _TOOL_LIKE = "tool_like_content"
    _ALGORITHM = "clinical_algorithm_or_pathway"
    _MATERIAL = "patient_or_clinician_material"
    _APPENDIX = "appendix_or_template"
    _ELIGIBLE = "eligible_implementation_tool"
    _CONNECTION = "current_guideline_tool_connection"
    _USAGE = "tool_use_instruction"
    _INELIGIBLE = "ineligible_tool_or_resource"
    _TOOL_CANDIDATES = (_TOOL_LIKE, _ALGORITHM, _MATERIAL, _APPENDIX, _ELIGIBLE)
    _LOW_FACTS = (_ADVICE, _SECTION, _TOOL_LIKE, _ALGORITHM, _MATERIAL, _APPENDIX, _INELIGIBLE)
    _MATCH_ORDER = (
        _ADVICE,
        _SECTION,
        _TOOL_LIKE,
        _ALGORITHM,
        _MATERIAL,
        _APPENDIX,
        _ELIGIBLE,
        _CONNECTION,
        _USAGE,
        _INELIGIBLE,
    )
    _RUBRIC_ATTRIBUTES = (
        "scope",
        "rubric_role",
        "support_form",
        "implementation_connection",
        "implementation_purpose",
        "actionability",
        "same_support_item",
    )
    _EXCLUDED_RUBRIC_ROLES = {"clinical_content", "not_applicable"}
    _EXCLUDED_SUPPORT_FORMS = {
        "clinical_practice_table",
        "diagnostic_screening_risk_tool",
        "drug_dose_table",
        "evidence_table",
    }
    _HIGH_SUPPORT_FORMS = {
        "implementation_resource_package",
    }

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item19 score from verified implementation-tool groups."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, ImplementationToolRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 ImplementationToolRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_rule(spec)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is not None:
            review_reasons.extend(grouping_result.review_reasons)
            groups = [
                group
                for group in grouping_result.accepted_groups
                if group.group_type == self._GROUP_TYPE
            ]
            return self._score_groups(spec, groups, review_reasons)

        return self._empty_result(spec, review_reasons)

    def _score_groups(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        scoreable_groups = self._scoreable_groups(groups, review_reasons)
        concrete_groups = [
            group
            for group in scoreable_groups
            if self._is_current_concrete_tool_group(group)
        ]
        if not scoreable_groups:
            return self._empty_result(spec, review_reasons)

        with_connection_and_usage = [
            group
            for group in concrete_groups
            if self._has_connection_fact(group)
            and self._has_usage_fact(group)
            and self._has_clear_actionability(group)
        ]
        if with_connection_and_usage:
            return self._group_result(
                spec,
                "7",
                with_connection_and_usage,
                missing=[],
                review_reasons=review_reasons,
            )

        resource_package_groups = [
            group
            for group in scoreable_groups
            if self._is_current_resource_package_group(group)
            and self._has_operational_actionability(group)
        ]
        concrete_with_section = [
            group
            for group in concrete_groups
            if self._has_connection_fact(group) and self._has_implementation_section(group)
        ]
        if resource_package_groups or concrete_with_section:
            return self._group_result(
                spec,
                "5-6",
                resource_package_groups or concrete_with_section,
                missing=[self._USAGE],
                review_reasons=review_reasons,
            )

        if len(concrete_groups) >= 2:
            return self._group_result(
                spec,
                "4-5",
                concrete_groups,
                missing=[self._SECTION, self._USAGE],
                review_reasons=review_reasons,
            )

        if concrete_groups:
            return self._group_result(
                spec,
                "2-3",
                concrete_groups,
                missing=[self._SECTION, self._USAGE],
                review_reasons=review_reasons,
            )

        return self._low_or_empty_result(
            spec,
            scoreable_groups,
            review_reasons,
        )

    def _low_or_empty_result(
        self,
        spec: ItemSpec,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        matched_rules = self._matched_low_rules_from_groups(groups)
        if matched_rules:
            review_reasons.append(
                ReviewReason(
                    code="no_eligible_implementation_tool",
                    message="仅命中实施讨论或工具样内容，未形成可进入中高分的实施支持/工具组。",
                    severity="warning",
                )
            )
            return self._result(
                spec=spec,
                score_range="2-4",
                matched_rules=matched_rules,
                missing=[self._ELIGIBLE],
                review_reasons=review_reasons,
            )

        return self._empty_result(spec, review_reasons)

    def _empty_result(
        self,
        spec: ItemSpec,
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        return self._result(
            spec=spec,
            score_range="1",
            matched_rules=[],
            missing=[self._ADVICE, self._TOOL_LIKE, self._ELIGIBLE],
            review_reasons=review_reasons,
        )

    def _group_result(
        self,
        spec: ItemSpec,
        score_range: str,
        groups: list[RuleObjectGroup],
        *,
        missing: list[str],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        matched_rules = self._matched_rules_from_groups(groups)
        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=matched_rules,
            missing=missing,
            review_reasons=review_reasons,
        )

    def _scoreable_groups(
        self,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> list[RuleObjectGroup]:
        scoreable: list[RuleObjectGroup] = []
        for group in groups:
            missing_attributes = self._missing_rubric_attributes(group)
            if missing_attributes:
                review_reasons.append(
                    ReviewReason(
                        code="incomplete_implementation_group_attributes",
                        message=(
                            f"{group.group_id} 的 item19 rubric attributes 不完整: "
                            f"{', '.join(missing_attributes)}"
                        ),
                        object_type=group.group_type,
                        severity="warning",
                    )
                )
                continue
            excluded_reason = self._group_exclusion_reason(group)
            if excluded_reason:
                review_reasons.append(
                    ReviewReason(
                        code="excluded_implementation_group_attributes",
                        message=f"{group.group_id} 不满足 item19 评分边界: {excluded_reason}",
                        object_type=group.group_type,
                        severity="warning",
                    )
                )
                continue
            scoreable.append(group)
        return scoreable

    def _group_exclusion_reason(self, group: RuleObjectGroup) -> str:
        scope = self._attribute(group, "scope")
        role = self._attribute(group, "rubric_role")
        support_form = self._attribute(group, "support_form")
        same_support_item = self._attribute(group, "same_support_item")
        if scope != "current_guideline":
            return f"scope={scope}"
        if same_support_item != "true":
            return f"same_support_item={same_support_item}"
        if role in self._EXCLUDED_RUBRIC_ROLES:
            return f"rubric_role={role}"
        if support_form in self._EXCLUDED_SUPPORT_FORMS:
            return f"support_form={support_form}"
        connection = self._attribute(group, "implementation_connection")
        if connection in {"external_or_cited", "unclear"}:
            return f"implementation_connection={connection}"
        purpose = self._attribute(group, "implementation_purpose")
        if purpose in {"clinical_management", "background_or_evidence"}:
            return f"implementation_purpose={purpose}"
        return ""

    def _is_current_concrete_tool_group(self, group: RuleObjectGroup) -> bool:
        return (
            self._has_concrete_tool_fact(group)
            and self._has_current_guideline_connection(group)
            and self._has_implementation_purpose(group)
            and self._attribute(group, "rubric_role") == "concrete_tool"
            and self._attribute(group, "support_form") not in self._EXCLUDED_SUPPORT_FORMS
        )

    def _is_current_implementation_support_group(self, group: RuleObjectGroup) -> bool:
        return (
            self._attribute(group, "rubric_role") == "implementation_support"
            and self._has_current_support_connection(group)
            and self._has_implementation_purpose(group)
            and self._attribute(group, "support_form") not in self._EXCLUDED_SUPPORT_FORMS
        )

    def _is_current_resource_package_group(self, group: RuleObjectGroup) -> bool:
        return (
            self._has_resource_package_fact(group)
            and self._attribute(group, "rubric_role") == "implementation_resource_package"
            and self._has_current_support_connection(group)
            and self._has_implementation_purpose(group)
            and self._attribute(group, "support_form") in self._HIGH_SUPPORT_FORMS
        )

    def _is_high_support_form(self, group: RuleObjectGroup) -> bool:
        return self._attribute(group, "support_form") in self._HIGH_SUPPORT_FORMS

    def _has_current_guideline_connection(self, group: RuleObjectGroup) -> bool:
        implementation_connection = self._attribute(group, "implementation_connection")
        return implementation_connection in {
            "provided_or_recommended_for_this_guideline",
            "embedded_or_linked_by_guideline",
        }

    def _has_current_support_connection(self, group: RuleObjectGroup) -> bool:
        implementation_connection = self._attribute(group, "implementation_connection")
        return implementation_connection in {
            "provided_or_recommended_for_this_guideline",
            "embedded_or_linked_by_guideline",
            "mentioned_only",
        }

    def _has_clear_actionability(self, group: RuleObjectGroup) -> bool:
        actionability = self._attribute(group, "actionability")
        return actionability in {"clear_tool_use_instruction", "clear_use_instruction"}

    def _has_operational_actionability(self, group: RuleObjectGroup) -> bool:
        actionability = self._attribute(group, "actionability")
        return actionability in {"clear_tool_use_instruction", "clear_use_instruction", "operational_elements"}

    def _has_implementation_purpose(self, group: RuleObjectGroup) -> bool:
        purpose = self._attribute(group, "implementation_purpose")
        return purpose == "put_guideline_recommendations_into_practice"

    def _has_implementation_section(self, group: RuleObjectGroup) -> bool:
        fact_types = {obj.type for obj in group.objects}
        return self._SECTION in fact_types or self._attribute(group, "section") == "implementation_section"

    def _has_concrete_tool_fact(self, group: RuleObjectGroup) -> bool:
        fact_types = self._fact_types(group)
        return bool(
            fact_types
            & {
                self._TOOL_LIKE,
                self._MATERIAL,
                self._APPENDIX,
                self._ELIGIBLE,
            }
        )

    def _has_resource_package_fact(self, group: RuleObjectGroup) -> bool:
        fact_types = self._fact_types(group)
        return bool(fact_types & {self._SECTION, self._ADVICE, self._TOOL_LIKE, self._ELIGIBLE})

    def _has_connection_fact(self, group: RuleObjectGroup) -> bool:
        return self._CONNECTION in self._fact_types(group)

    def _has_usage_fact(self, group: RuleObjectGroup) -> bool:
        return self._USAGE in self._fact_types(group)

    def _has_low_group_evidence(self, group: RuleObjectGroup) -> bool:
        return self._attribute(group, "rubric_role") in {
            "concrete_tool",
            "implementation_resource_package",
            "implementation_support",
        }

    def _matched_low_rules_from_groups(self, groups: list[RuleObjectGroup]) -> list[str]:
        fact_types: set[str] = set()
        for group in groups:
            if self._has_low_group_evidence(group):
                fact_types.update(obj.type for obj in group.objects)
        matched = self._matched(fact_types, self._LOW_FACTS)
        if not matched and fact_types:
            matched = self._matched(fact_types, (*self._TOOL_CANDIDATES, self._INELIGIBLE))
        return matched

    def _matched_rules_from_groups(self, groups: list[RuleObjectGroup]) -> list[str]:
        fact_types: set[str] = set()
        for group in groups:
            fact_types.update(obj.type for obj in group.objects)
        return self._matched(fact_types, self._MATCH_ORDER)

    def _fact_types(self, group: RuleObjectGroup) -> set[str]:
        return {obj.type for obj in group.objects}

    def _attribute(self, group: RuleObjectGroup, key: str) -> str:
        return str(group.attributes.get(key, "")).strip().casefold()

    def _missing_rubric_attributes(self, group: RuleObjectGroup) -> list[str]:
        return [key for key in self._RUBRIC_ATTRIBUTES if key not in group.attributes]

    def _validate_rule(self, spec: ItemSpec) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for fact_type in self._MATCH_ORDER:
            if fact_type not in spec.object_specs:
                reasons.append(
                    ReviewReason(
                        code="unknown_implementation_tool_fact",
                        message=f"ImplementationToolRule 引用了未声明的 fact: {fact_type}",
                        object_type=fact_type,
                        severity="error",
                    )
                )
        return reasons


class ManagementOptionsEngine(_DisclosureEngineMixin):
    """AGREE-II item 16 management-options scoring by verified groups."""

    _GROUP_TYPE = "management_options"
    _ISSUE = "clinical_issue_or_decision_point"
    _OPTION = "management_option"
    _CONDITION = "option_condition_or_population"
    _PREFERENCE = "preference_order"
    _SELECTION = "selection_criteria"
    _PATHWAY = "decision_pathway_or_algorithm"
    _SINGLE = "single_option_only"
    _MATCH_ORDER = (
        _ISSUE,
        _OPTION,
        _CONDITION,
        _PREFERENCE,
        _SELECTION,
        _PATHWAY,
        _SINGLE,
    )

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer item16 score from the best same-issue management-options group."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, ManagementOptionsRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 ManagementOptionsRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_rule(spec)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        if grouping_result is not None:
            review_reasons.extend(grouping_result.review_reasons)
            groups = [
                group
                for group in grouping_result.accepted_groups
                if group.group_type == self._GROUP_TYPE
            ]
            return self._score_groups(spec, snapshot, groups, review_reasons)

        return self._score_snapshot(spec, snapshot, review_reasons)

    def _score_groups(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        groups: list[RuleObjectGroup],
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        scored = [
            (*self._score_group(group), group)
            for group in groups
        ]
        valid = [
            item
            for item in scored
            if item[0] != "1"
        ]
        if not valid:
            fact_types = {obj.type for obj in snapshot.objects}
            matched = self._matched(fact_types, (self._SINGLE, self._OPTION))
            if matched:
                review_reasons.append(
                    ReviewReason(
                        code="no_same_issue_management_options",
                        message="未形成同一临床问题下的多个管理选项组。",
                        severity="warning",
                    )
                )
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=matched,
                missing=[self._OPTION, self._CONDITION],
                review_reasons=review_reasons,
            )

        score_range, matched_rules, missing, _group = max(
            valid,
            key=lambda item: (
                max(self._parse_score_range(item[0])),
                len(item[1]),
                -len(item[2]),
            ),
        )
        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=matched_rules,
            missing=missing,
            review_reasons=review_reasons,
        )

    def _score_group(self, group: RuleObjectGroup) -> tuple[str, list[str], list[str]]:
        fact_types = {obj.type for obj in group.objects}
        scope = self._attribute(group, "scope")
        if scope and scope != "current_guideline":
            return "1", self._matched(fact_types, self._MATCH_ORDER), [self._OPTION]
        if self._attribute(group, "same_issue") == "false":
            return "1", self._matched(fact_types, self._MATCH_ORDER), [self._ISSUE]

        option_count = max(self._option_count(group), self._int_attribute(group, "option_count"))
        if option_count < 2:
            return "1", self._matched(fact_types, self._MATCH_ORDER), [self._OPTION]

        has_conditions = (
            self._CONDITION in fact_types
            or self._bool_attribute(group, "has_conditions")
        )
        has_preference_or_pathway = (
            self._PREFERENCE in fact_types
            or self._SELECTION in fact_types
            or self._PATHWAY in fact_types
            or self._bool_attribute(group, "has_preference_or_pathway")
        )
        matched_rules = self._matched(fact_types, self._MATCH_ORDER)
        if has_preference_or_pathway:
            return "6-7", matched_rules, []
        if has_conditions:
            return "4-5", matched_rules, [self._PREFERENCE, self._SELECTION, self._PATHWAY]
        return "2-3", matched_rules, [self._CONDITION]

    def _score_snapshot(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        review_reasons: list[ReviewReason],
    ) -> RuleResult:
        fact_types = {obj.type for obj in snapshot.objects}
        option_count = len({obj.value.strip().casefold() for obj in snapshot.get_objects(self._OPTION)})
        if option_count < 2:
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=self._matched(fact_types, (self._SINGLE, self._OPTION)),
                missing=[self._OPTION],
                review_reasons=review_reasons,
            )
        if self._PREFERENCE in fact_types or self._SELECTION in fact_types or self._PATHWAY in fact_types:
            score_range = "6-7"
            missing: list[str] = []
        elif self._CONDITION in fact_types:
            score_range = "4-5"
            missing = [self._PREFERENCE, self._SELECTION, self._PATHWAY]
        else:
            score_range = "2-3"
            missing = [self._CONDITION]
        return self._result(
            spec=spec,
            score_range=score_range,
            matched_rules=self._matched(fact_types, self._MATCH_ORDER),
            missing=missing,
            review_reasons=review_reasons,
        )

    def _option_count(self, group: RuleObjectGroup) -> int:
        values = {
            obj.value.strip().casefold()
            for obj in group.objects
            if obj.type == self._OPTION and obj.value.strip()
        }
        return len(values)

    def _attribute(self, group: RuleObjectGroup, key: str) -> str:
        return str(group.attributes.get(key, "")).strip().casefold()

    def _bool_attribute(self, group: RuleObjectGroup, key: str) -> bool:
        value = group.attributes.get(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"true", "yes", "1"}

    def _int_attribute(self, group: RuleObjectGroup, key: str) -> int:
        value = group.attributes.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _validate_rule(self, spec: ItemSpec) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for fact_type in self._MATCH_ORDER:
            if fact_type not in spec.object_specs:
                reasons.append(
                    ReviewReason(
                        code="unknown_management_option_fact",
                        message=f"ManagementOptionsRule 引用了未声明的 fact: {fact_type}",
                        object_type=fact_type,
                        severity="error",
                    )
                )
        return reasons


class CompetingInterestDisclosureEngine(_DisclosureEngineMixin):
    """AGREE-II item 23 competing-interest disclosure completeness scoring."""

    _COI_FACTS = (
        "coi_mention",
        "coi_collection_process",
        "coi_disclosure_result",
        "no_conflicts_statement",
        "conflicts_present",
        "coi_reviewed_or_discussed",
        "coi_management_action",
        "coi_no_influence_statement",
    )
    _REPORT_FACTS = ("coi_disclosure_result", "no_conflicts_statement", "conflicts_present")

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, CompetingInterestDisclosureRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 CompetingInterestDisclosureRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_conflict_groups(spec, spec.rule.conflict_groups)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        fact_types = {obj.type for obj in snapshot.objects}
        review_reasons.extend(self._conflict_review_reasons(spec, spec.rule.conflict_groups, fact_types))

        matched_coi_facts = self._matched(fact_types, self._COI_FACTS)
        if not matched_coi_facts:
            return self._result(
                spec=spec,
                score_range="1",
                matched_rules=[],
                missing=["coi_mention"],
                review_reasons=review_reasons,
            )

        has_collection = "coi_collection_process" in fact_types
        has_report = any(fact_type in fact_types for fact_type in self._REPORT_FACTS)
        has_no_conflicts = "no_conflicts_statement" in fact_types
        has_disclosure_result = "coi_disclosure_result" in fact_types
        has_conflicts_present = "conflicts_present" in fact_types
        has_addressed = (
            "coi_no_influence_statement" in fact_types
            or "coi_management_action" in fact_types
        )

        if not has_collection:
            if has_report:
                return self._result(
                    spec=spec,
                    score_range="5",
                    matched_rules=matched_coi_facts,
                    missing=["coi_collection_process"],
                    review_reasons=review_reasons,
                )
            return self._result(
                spec=spec,
                score_range="2-4",
                matched_rules=matched_coi_facts,
                missing=["coi_collection_process"],
                review_reasons=review_reasons,
            )

        if has_no_conflicts:
            return self._result(
                spec=spec,
                score_range="7",
                matched_rules=self._matched(
                    fact_types,
                    (
                        "coi_collection_process",
                        "no_conflicts_statement",
                        "coi_no_influence_statement",
                    ),
                ),
                missing=[],
                review_reasons=review_reasons,
            )

        if has_conflicts_present and has_addressed:
            return self._result(
                spec=spec,
                score_range="7",
                matched_rules=self._matched(
                    fact_types,
                    (
                        "coi_collection_process",
                        "coi_disclosure_result",
                        "conflicts_present",
                        "coi_management_action",
                        "coi_no_influence_statement",
                    ),
                ),
                missing=[],
                review_reasons=review_reasons,
            )

        if has_conflicts_present or has_disclosure_result:
            return self._result(
                spec=spec,
                score_range="5-6",
                matched_rules=self._matched(
                    fact_types,
                    (
                        "coi_collection_process",
                        "coi_disclosure_result",
                        "conflicts_present",
                        "coi_reviewed_or_discussed",
                    ),
                ),
                missing=["coi_management_action", "coi_no_influence_statement"],
                review_reasons=review_reasons,
            )

        missing = ["coi_disclosure_result", "no_conflicts_statement", "conflicts_present"]
        if has_report:
            missing = []
        return self._result(
            spec=spec,
            score_range="2-4",
            matched_rules=matched_coi_facts,
            missing=missing,
            review_reasons=review_reasons,
        )


class BranchingPathEngine:
    """
    Rule profile that selects the first satisfied branch case.

    This engine is intended for items whose score depends on mutually exclusive
    or priority-ordered paths rather than cumulative evidence completeness.
    No registered ItemSpec currently uses this profile; it is retained for
    future genuinely mutually exclusive branch scenarios.
    """

    def execute(
        self,
        spec: ItemSpec,
        snapshot: ObjectStoreSnapshot,
        grouping_result: RuleGroupingResult | None = None,
    ) -> RuleResult:
        """Infer score by selecting the first matching branch case."""
        review_reasons = list(snapshot.review_reasons)
        if not isinstance(spec.rule, BranchingRule):
            review_reasons.append(
                ReviewReason(
                    code="invalid_rule_type",
                    message=f"{spec.item_id} 未配置 BranchingRule",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        if not spec.rule.cases:
            review_reasons.append(
                ReviewReason(
                    code="missing_branch_cases",
                    message=f"{spec.item_id} 未定义 branch cases",
                    severity="error",
                )
            )
            return self._error_result(spec.item_id, review_reasons)

        validation_reasons = self._validate_rule(spec, spec.rule)
        if validation_reasons:
            review_reasons.extend(validation_reasons)
            return self._error_result(spec.item_id, review_reasons)

        fact_types = {obj.type for obj in snapshot.objects}
        review_reasons.extend(self._conflict_review_reasons(spec, spec.rule, fact_types))
        for case in spec.rule.cases:
            if self._is_condition_met(case.condition, fact_types):
                allowed_scores = self._parse_score_range(case.score_range)
                return RuleResult(
                    item_id=spec.item_id,
                    status="matched",
                    score_range=case.score_range,
                    allowed_scores=allowed_scores,
                    max_score=max(allowed_scores),
                    matched_rules=self._matched_facts(case.condition, fact_types),
                    missing=[],
                    needs_review=bool(review_reasons),
                    review_reasons=review_reasons,
                )

        review_reasons.append(
            ReviewReason(
                code="no_branch_case_matched",
                message=f"{spec.item_id} 未命中任何 branch case",
                severity="error",
            )
        )
        return self._error_result(spec.item_id, review_reasons)

    def _validate_rule(self, spec: ItemSpec, rule: BranchingRule) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        fallback_indexes: list[int] = []
        for index, case in enumerate(rule.cases):
            reasons.extend(self._validate_score_range(case.score_range))
            reasons.extend(self._validate_condition(case.condition, spec))
            if self._is_unconditional_fallback(case.condition):
                fallback_indexes.append(index)
        if fallback_indexes and fallback_indexes[-1] != len(rule.cases) - 1:
            reasons.append(
                ReviewReason(
                    code="fallback_not_last",
                    message="BranchingRule 的无条件 fallback 必须放在最后",
                    severity="error",
                )
            )
        for group in rule.conflict_groups:
            if len(group) < 2:
                reasons.append(
                    ReviewReason(
                        code="invalid_conflict_group",
                        message="conflict_group 至少需要两个 fact",
                        severity="error",
                    )
                )
            for fact_type in group:
                if fact_type not in spec.object_specs:
                    reasons.append(
                        ReviewReason(
                            code="unknown_conflict_fact",
                            message=f"conflict_group 引用了未声明的 fact: {fact_type}",
                            object_type=fact_type,
                            severity="error",
                        )
                    )
        return reasons

    def _conflict_review_reasons(
        self,
        spec: ItemSpec,
        rule: BranchingRule,
        fact_types: set[str],
    ) -> list[ReviewReason]:
        reasons: list[ReviewReason] = []
        for group in rule.conflict_groups:
            matched = sorted(fact_type for fact_type in group if fact_type in fact_types)
            if len(matched) > 1:
                reasons.append(
                    ReviewReason(
                        code="conflicting_facts",
                        message=f"{spec.item_id} 同时命中互斥事实: {', '.join(matched)}",
                        object_type=",".join(matched),
                        severity="warning",
                    )
                )
        return reasons

    def _is_condition_met(self, condition: RuleCondition, fact_types: set[str]) -> bool:
        return OrderedMilestoneEngine()._is_condition_met(condition, fact_types)

    def _matched_facts(self, condition: RuleCondition, fact_types: set[str]) -> list[str]:
        return OrderedMilestoneEngine()._matched_facts(condition, fact_types)

    def _validate_condition(self, condition: RuleCondition, spec: ItemSpec) -> list[ReviewReason]:
        return OrderedMilestoneEngine()._validate_condition(condition, spec)

    def _parse_score_range(self, score_range: str) -> list[int]:
        return OrderedMilestoneEngine()._parse_score_range(score_range)

    def _validate_score_range(self, score_range: str) -> list[ReviewReason]:
        return OrderedMilestoneEngine()._validate_score_range(score_range)

    def _is_unconditional_fallback(self, condition: RuleCondition) -> bool:
        return OrderedMilestoneEngine()._is_unconditional_fallback(condition)

    def _error_result(self, item_id: str, review_reasons: list[ReviewReason]) -> RuleResult:
        return OrderedMilestoneEngine()._error_result(item_id, review_reasons)
