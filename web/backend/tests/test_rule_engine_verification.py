from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.rule_engine.models import (
    EvidenceChunk,
    EvidenceUnit,
    EvidenceRef,
    ExtractionBatchResult,
    GroupingSpec,
    GroupVerificationDecision,
    ItemSpec,
    ObjectVerificationSpec,
    RuleGroupingResult,
    RuleObject,
    RuleObjectGroup,
    RuleObjectSpec,
    VerificationDecision,
    WeightedChecklistItem,
    WeightedChecklistRule,
)
from core.rule_engine.rubric.builder import EvidenceUnitBuilder
from core.rule_engine.rubric.appraiser import RubricPortfolioAppraiser
from core.rule_engine.rubric.classifier import RubricClassifier
from core.rule_engine.rubric.prompts import render_rubric_classification_prompt
from core.rule_engine.extractors.llm import LLMObjectExtractor
from core.rule_engine.grouping import LLMRuleObjectGroupVerifier, RuleObjectGroupBuilder
from core.rule_engine.prompts.grouping import render_group_verification_prompt
from core.rule_engine.prompts.verification import render_verification_prompt
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.runner import run_rule_pipeline
from core.rule_engine.stores.object_store import ObjectStore
from core.llm.context import RequestBudget

from core.rule_engine.verification import (
    CompositeRuleObjectVerifier,
    DeterministicRuleObjectVerifier,
    LLMRuleObjectVerifier,
    NoopRuleObjectVerifier,
)


def test_litellm_rule_client_uses_json_schema_response_format(monkeypatch):
    from core.llm import gateway as gateway_module
    from core.llm.gateway import LLMGateway
    from core.rule_engine.litellm_client import LiteLLMRuleClient

    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"decisions":[]}')
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(gateway_module.litellm, "completion", fake_completion)

    from core.llm.context import RequestBudget
    client = LiteLLMRuleClient(LLMGateway(model="m", base_url="http://local", api_key="k", timeout=10, provider_kwargs={}, budget=RequestBudget(131072, "m")))
    client.complete_json(
        stage="test",
        system_prompt="system",
        user_prompt="user",
        max_tokens=32,
        json_schema={
            "type": "object",
            "required": ["decisions"],
            "properties": {"decisions": {"type": "array"}},
        },
        schema_name="decision_test",
    )

    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "decision_test",
            "strict": True,
            "schema": {
                "type": "object",
                "required": ["decisions"],
                "properties": {"decisions": {"type": "array"}},
            },
        },
    }


@pytest.mark.parametrize(
    "model",
    ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash", "deepseek-v4-pro"],
)
def test_litellm_rule_client_downgrades_deepseek_json_schema_to_json_object(monkeypatch, model):
    from core.llm import gateway as gateway_module
    from core.llm.gateway import LLMGateway
    from core.rule_engine.litellm_client import LiteLLMRuleClient

    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"decisions":[]}')
                )
            ],
            usage=None,
        )

    monkeypatch.setattr(gateway_module.litellm, "completion", fake_completion)

    client = LiteLLMRuleClient(LLMGateway(model=model, base_url="", api_key="k", timeout=10, provider_kwargs={}, budget=RequestBudget(131072, model)))
    client.complete_json(
        stage="test",
        system_prompt="system",
        user_prompt="user",
        max_tokens=32,
        json_schema={
            "type": "object",
            "required": ["decisions"],
            "properties": {"decisions": {"type": "array"}},
        },
        schema_name="decision_test",
    )

    assert captured["response_format"] == {"type": "json_object"}


def test_litellm_rule_client_keeps_json_object_without_schema(monkeypatch):
    from core.llm import gateway as gateway_module
    from core.llm.gateway import LLMGateway
    from core.rule_engine.litellm_client import LiteLLMRuleClient

    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"objects":[]}'))],
            usage=None,
        )

    monkeypatch.setattr(gateway_module.litellm, "completion", fake_completion)

    from core.llm.context import RequestBudget
    client = LiteLLMRuleClient(LLMGateway(model="m", base_url="http://local", api_key="k", timeout=10, provider_kwargs={}, budget=RequestBudget(131072, "m")))
    client.complete_json(
        stage="test",
        system_prompt="system",
        user_prompt="user",
        max_tokens=32,
    )

    assert captured["response_format"] == {"type": "json_object"}


def _object(
    object_type: str = "A",
    value: str = "good",
    quote: str = "good",
    *,
    chunk_id: str = "chunk_001",
) -> RuleObject:
    return RuleObject(
        type=object_type,
        value=value,
        evidence=EvidenceRef(chunk_id=chunk_id, quote=quote),
        confidence=0.95,
    )


def _chunk(content: str = "good implementation context") -> EvidenceChunk:
    return EvidenceChunk(chunk_id="chunk_001", title="Methods", content=content)


class _FakeLLMClient:
    def __init__(self, payload: dict):
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.prompts: list[tuple[str, str]] = []
        self.calls: list[dict] = []

    def complete_json(
        self,
        *,
        stage: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        json_schema: dict | None = None,
        schema_name: str = "rule_engine_response",
        strict: bool = True,
        list_wrapper: str | None = None,
    ) -> dict:
        self.prompts.append((stage, user_prompt))
        self.calls.append(
            {
                "stage": stage,
                "json_schema": json_schema,
                "schema_name": schema_name,
                "strict": strict,
            }
        )
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


def test_noop_verifier_accepts_objects_without_config():
    spec = ItemSpec(
        item_id="item_noop",
        title="Noop",
        rule_profile="weighted_checklist",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
    )

    result = NoopRuleObjectVerifier().verify(spec, [_object()], [_chunk()])

    assert result.accepted_objects == [_object()]
    assert result.decisions[0].accepted is True
    assert result.decisions[0].source == "skipped"


def test_deterministic_verifier_rejects_negative_candidate():
    spec = ItemSpec(
        item_id="item_verify",
        title="Verify",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(negative_terms=("bad",)),
            )
        },
    )

    result = DeterministicRuleObjectVerifier().verify(
        spec,
        [_object(value="bad", quote="bad")],
        [_chunk()],
    )

    assert result.accepted_objects == []
    assert result.decisions[0].accepted is False
    assert result.decisions[0].rejection_code == "negative_term_matched"
    assert result.review_reasons[0].code == "verification_rejected"


def test_item_19_deterministic_verifier_does_not_apply_lexical_gates():
    spec = get_item_spec("item_19")
    obj = _object(
        "implementation_advice_or_discussion",
        "Successful implementation requires protocols and EHR changes",
        "Successful implementation requires protocols and EHR changes",
    )

    result = DeterministicRuleObjectVerifier().verify(spec, [obj], [_chunk()])

    assert result.accepted_objects == [obj]
    assert result.decisions[0].accepted is True
    assert result.decisions[0].tags == ()


def test_llm_verifier_accepts_and_rejects_candidates():
    spec = ItemSpec(
        item_id="item_llm",
        title="LLM",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(llm_required=True, guidance="Only accept current guideline evidence."),
            )
        },
    )
    verifier = LLMRuleObjectVerifier(
        _FakeLLMClient(
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": True,
                        "confidence": 0.9,
                        "rejection_code": "",
                        "reason": "current guideline",
                    },
                    {
                        "index": 1,
                        "accepted": False,
                        "confidence": 0.8,
                        "rejection_code": "external_source",
                        "reason": "cited external document",
                    },
                ]
            }
        )
    )

    result = verifier.verify(
        spec,
        [
            _object(value="current", quote="current"),
            _object(value="external", quote="external"),
        ],
        [_chunk()],
    )

    assert [obj.value for obj in result.accepted_objects] == ["current"]
    assert [decision.accepted for decision in result.decisions] == [True, False]
    assert result.review_reasons[0].code == "verification_rejected"
    assert verifier.llm_client.calls[0]["schema_name"] == "rule_object_verification"
    assert verifier.llm_client.calls[0]["json_schema"]["properties"]["decisions"]["minItems"] == 2


def test_llm_verifier_invalid_batch_raises():
    spec = ItemSpec(
        item_id="item_llm_invalid",
        title="LLM invalid",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(llm_required=True),
            )
        },
    )

    with pytest.raises(ValueError, match="decisions"):
        LLMRuleObjectVerifier(
            _FakeLLMClient([
                {"decisions": "bad"},
                {"decisions": "bad"},
            ])
        ).verify(
            spec,
            [_object()],
            [_chunk()],
        )


def test_llm_verifier_retries_missing_index_and_recovers():
    spec = ItemSpec(
        item_id="item_llm_retry",
        title="LLM retry",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(llm_required=True),
            )
        },
    )
    client = _FakeLLMClient(
        [
            {"decisions": []},
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": True,
                        "confidence": 0.9,
                        "rejection_code": "",
                        "reason": "accepted on retry",
                    }
                ]
            },
        ]
    )

    result = LLMRuleObjectVerifier(client).verify(spec, [_object()], [_chunk()])

    assert [call["stage"] for call in client.calls] == [
        "RuleObjectVerification:item_llm_retry",
        "RuleObjectVerificationRetry:item_llm_retry",
    ]
    assert result.accepted_objects
    assert result.review_reasons[0].code == "verification_json_retry"


def test_llm_verifier_fails_candidate_still_missing_after_retry():
    spec = ItemSpec(
        item_id="item_llm_retry_missing",
        title="LLM retry missing",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(llm_required=True),
            )
        },
    )
    client = _FakeLLMClient([{"decisions": []}, {"decisions": []}])

    with pytest.raises(ValueError, match="missing decision"):
        LLMRuleObjectVerifier(client).verify(spec, [_object()], [_chunk()])


def test_composite_verifier_runs_llm_after_deterministic_filter():
    spec = ItemSpec(
        item_id="item_composite",
        title="Composite",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(
                    required_any_terms=("good",),
                    llm_required=True,
                ),
            )
        },
    )
    verifier = CompositeRuleObjectVerifier(
        DeterministicRuleObjectVerifier(),
        LLMRuleObjectVerifier(
            _FakeLLMClient(
                {
                    "decisions": [
                        {
                            "index": 0,
                            "accepted": True,
                            "confidence": 0.9,
                            "rejection_code": "",
                            "reason": "accepted",
                        },
                        {
                            "index": 1,
                            "accepted": False,
                            "confidence": 0.8,
                            "rejection_code": "insufficient_support",
                            "reason": "missing required semantic support",
                        }
                    ]
                }
            )
        ),
    )

    result = verifier.verify(
        spec,
        [
            _object(value="good", quote="good"),
            _object(value="bad", quote="bad"),
        ],
        [_chunk()],
    )

    assert [obj.value for obj in result.accepted_objects] == ["good"]
    assert [decision.source for decision in result.decisions] == ["llm", "llm"]
    assert [decision.accepted for decision in result.decisions] == [True, False]
    assert result.decisions[1].rejection_code == "insufficient_support"
    assert result.decisions[1].tags == ("required_any_term_missing",)


def test_composite_verifier_sends_llm_required_negative_candidates_to_llm():
    spec = ItemSpec(
        item_id="item_composite_soft_negative",
        title="Composite soft negative",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(
                    required_any_terms=("current",),
                    negative_terms=("funding",),
                    llm_required=True,
                ),
            )
        },
    )
    client = _FakeLLMClient(
        {
            "decisions": [
                {
                    "index": 0,
                    "accepted": True,
                    "confidence": 0.9,
                    "rejection_code": "",
                    "reason": "accepted after semantic verification",
                }
            ]
        }
    )
    verifier = CompositeRuleObjectVerifier(
        DeterministicRuleObjectVerifier(),
        LLMRuleObjectVerifier(client),
    )

    result = verifier.verify(
        spec,
        [_object(value="current funding", quote="current funding")],
        [_chunk("current funding")],
    )

    assert [obj.value for obj in result.accepted_objects] == ["current funding"]
    assert [decision.source for decision in result.decisions] == ["llm"]
    assert result.decisions[0].tags == ("negative_term_matched:funding",)
    assert client.prompts


def test_verification_prompt_includes_item_guidance_object_guidance_and_tags():
    spec = ItemSpec(
        item_id="item_prompt_guidance",
        title="Prompt guidance",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A object description",
                verification=ObjectVerificationSpec(
                    llm_required=True,
                    guidance="Object-specific verification boundary.",
                ),
            )
        },
        verification_guidance="Item-level AGREE-II standard for this verifier.",
    )
    prompt = render_verification_prompt(
        spec,
        [_object()],
        [_chunk()],
        candidate_decisions=[
            VerificationDecision(
                object_type="A",
                object_value="good",
                chunk_id="chunk_001",
                accepted=True,
                confidence=0.95,
                source="deterministic",
                reason="tagged",
                object_index=0,
                tags=("required_any_term_missing",),
            )
        ],
    )

    assert "Item-level AGREE-II standard for this verifier." in prompt
    assert "Object-specific verification boundary." in prompt
    assert "deterministic_tags" in prompt
    assert "required_any_term_missing" in prompt
    assert "current-guideline methodology" not in prompt


def test_hard_negative_rejects_before_llm_review():
    spec = ItemSpec(
        item_id="item_hard_negative",
        title="Hard negative",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(
                    required_any_terms=("guideline-audit",),
                    hard_negative_context_terms=("perioperative",),
                    llm_required=True,
                ),
            )
        },
    )
    client = _FakeLLMClient(
        {
            "decisions": [
                {
                    "index": 0,
                    "accepted": True,
                    "confidence": 0.9,
                    "rejection_code": "",
                    "reason": "would have accepted",
                }
            ]
        }
    )
    verifier = CompositeRuleObjectVerifier(
        DeterministicRuleObjectVerifier(),
        LLMRuleObjectVerifier(client),
    )

    result = verifier.verify(
        spec,
        [_object(value="current target", quote="current target")],
        [_chunk("Perioperative current target")],
    )

    assert result.accepted_objects == []
    assert result.decisions[0].source == "deterministic"
    assert result.decisions[0].rejection_code == "hard_negative_context_term_matched"
    assert result.decisions[0].tags == ("hard_negative_context_term_matched:perioperative",)
    assert client.prompts == []


def test_extractor_can_skip_value_in_quote_for_title_style_disclosures():
    spec = ItemSpec(
        item_id="item_title_disclosure",
        title="Title disclosure",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                require_value_in_quote=False,
            )
        },
    )
    extractor = LLMObjectExtractor(
        _FakeLLMClient(
            {
                "objects": [
                    {
                        "type": "A",
                        "value": "None",
                        "chunk_id": "chunk_001",
                        "quote": "Conflict of interest",
                        "confidence": 0.95,
                        "attributes": {},
                    }
                ]
            }
        )
    )

    result = extractor.extract(spec, [_chunk("Conflict of interest")])

    assert [obj.value for obj in result.objects] == ["None"]


def test_deterministic_verifier_uses_local_quote_context_for_required_context():
    spec = ItemSpec(
        item_id="item_verify_local_context",
        title="Verify local context",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(required_context_terms=("inclusion criteria",)),
            )
        },
    )
    far_context = "inclusion criteria " + ("filler " * 120) + "diagnostic threshold"

    result = DeterministicRuleObjectVerifier().verify(
        spec,
        [_object(value="diagnostic threshold", quote="diagnostic threshold")],
        [_chunk(far_context)],
    )

    assert result.accepted_objects == []
    assert result.decisions[0].rejection_code == "required_context_term_missing"


class _Extractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        return ExtractionBatchResult(
            objects=[
                _object(value="good", quote="good"),
                _object(value="bad", quote="bad"),
            ]
        )


class _EmptyExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        return ExtractionBatchResult(objects=[])


class _PipelineMonitoringExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        return ExtractionBatchResult(
            objects=[
                RuleObject(
                    type="indicator_or_standard",
                    value="HbA1c",
                    evidence=EvidenceRef(chunk_id="chunk_001", quote="HbA1c"),
                    confidence=0.95,
                ),
                RuleObject(
                    type="measurement_frequency_or_interval",
                    value="every 6 months",
                    evidence=EvidenceRef(chunk_id="chunk_001", quote="every 6 months"),
                    confidence=0.95,
                ),
                RuleObject(
                    type="measurement_method",
                    value="audit tool",
                    evidence=EvidenceRef(chunk_id="chunk_001", quote="audit tool"),
                    confidence=0.95,
                ),
            ]
        )


class _TitleStyleFundingExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        return ExtractionBatchResult(
            objects=[
                RuleObject(
                    type="no_funding_statement",
                    value="None",
                    evidence=EvidenceRef(chunk_id="chunk_001", quote="Funding: None"),
                    confidence=0.95,
                )
            ]
        )


class _TitleStyleCOIExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        return ExtractionBatchResult(
            objects=[
                RuleObject(
                    type="no_conflicts_statement",
                    value="None",
                    evidence=EvidenceRef(chunk_id="chunk_001", quote="Conflict of interest: None"),
                    confidence=0.95,
                )
            ]
        )


def test_run_rule_pipeline_merges_only_verified_objects():
    spec = ItemSpec(
        item_id="item_pipeline_verify",
        title="Pipeline verify",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(negative_terms=("bad",)),
            )
        },
        rule=WeightedChecklistRule(items=(WeightedChecklistItem(fact="A"),)),
    )

    from core.rule_engine import registry

    registry.ITEM_SPECS[spec.item_id] = spec
    try:
        result = run_rule_pipeline(
            spec.item_id,
            [EvidenceChunk(chunk_id="chunk_001", title="Verify", content="good bad")],
            extractor=_Extractor(),
        )
    finally:
        registry.ITEM_SPECS.pop(spec.item_id, None)

    assert [obj.value for obj in result.snapshot.objects] == ["good"]
    assert result.rule_result.score_range == "2"
    assert [decision.accepted for decision in result.verification_decisions] == [True, False]
    assert [reason.code for reason in result.snapshot.review_reasons] == ["verification_rejected"]


def test_run_rule_pipeline_validates_title_style_quote_with_chunk_title():
    result = run_rule_pipeline(
        "item_22",
        [EvidenceChunk(chunk_id="chunk_001", title="Funding", content="None")],
        llm_client=_FakeLLMClient(
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": True,
                        "confidence": 0.95,
                        "rejection_code": "",
                        "reason": "current no-funding disclosure",
                        "attributes": {"status": "none", "scope": "current_manuscript"},
                    }
                ]
            }
        ),
        extractor=_TitleStyleFundingExtractor(),
        verifier=NoopRuleObjectVerifier(),
    )

    assert [obj.type for obj in result.snapshot.objects] == ["no_funding_statement"]
    assert result.grouping_result is not None
    assert len(result.grouping_result.accepted_groups) == 1
    assert result.rule_result.score_range == "7"


def test_run_rule_pipeline_validates_title_style_coi_quote_with_chunk_title():
    result = run_rule_pipeline(
        "item_23",
        [EvidenceChunk(chunk_id="chunk_001", title="Conflict of interest", content="None.")],
        extractor=_TitleStyleCOIExtractor(),
        verifier=NoopRuleObjectVerifier(),
    )

    assert [obj.type for obj in result.snapshot.objects] == ["no_conflicts_statement"]
    assert result.rule_result.score_range == "5"


def test_verification_prompt_uses_quote_neighbor_context_for_long_chunks():
    spec = ItemSpec(
        item_id="item_prompt_context",
        title="Prompt context",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(
                type="A",
                role="component",
                description="A",
                verification=ObjectVerificationSpec(llm_required=True),
            )
        },
    )
    prompt = render_verification_prompt(
        spec,
        [_object(value="important quote", quote="important quote")],
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Declarations",
                content=f"{'irrelevant opening ' * 200} nearby before important quote nearby after",
            )
        ],
    )

    assert "important quote nearby after" in prompt
    assert "irrelevant opening " * 40 not in prompt


def test_item_23_verification_guidance_accepts_result_without_collection_method():
    spec = get_item_spec("item_23")
    prompt = render_verification_prompt(
        spec,
        [
            RuleObject(
                type="no_conflicts_statement",
                value="None",
                evidence=EvidenceRef(chunk_id="chunk_001", quote="There are no conflicts of interest."),
                confidence=0.95,
            )
        ],
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Competing interests",
                content="There are no conflicts of interest.",
            )
        ],
    )

    assert "must not reject a no-conflicts statement" in prompt
    assert "merely because the collection method is absent" in prompt
    assert "Competing interests" in prompt


def test_rubric_unit_builder_builds_monitoring_evidence_units():
    spec = get_item_spec("item_21")

    units = EvidenceUnitBuilder().build(
        spec,
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="AUDIT STANDARDS",
                content=(
                    "3B.1 Proportion regularly undertake SMBG.\n"
                    "3B.3 Proportion undergone CGM over the preceding year."
                ),
            )
        ],
    )

    assert units
    assert units[0].chunk_id == "chunk_001"
    assert units[0].quote in units[0].content
    assert any("3B.1" in unit.quote for unit in units)


def test_rubric_unit_builder_ranks_relevant_monitoring_units_first():
    spec = get_item_spec("item_21")

    units = EvidenceUnitBuilder().build(
        spec,
        [
            EvidenceChunk(chunk_id="chunk_001", title="Background", content="General background."),
            EvidenceChunk(
                chunk_id="chunk_002",
                title="Monitoring",
                content="HbA1c target should be measured every 3 months.",
            ),
        ],
    )

    assert units[0].chunk_id == "chunk_002"


def test_group_builder_builds_funding_heading_value_group():
    spec = get_item_spec("item_22")
    objects = [
        _object(
            "funding_section_heading",
            "Financial support and sponsorship",
            "Financial support and sponsorship",
            chunk_id="chunk_001",
        ),
        _object("funding_section_value", "Sanofi India", "Sanofi India", chunk_id="chunk_002"),
        _object("for_profit_funding", "Sanofi India", "Sanofi India", chunk_id="chunk_002"),
    ]
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001", "chunk_002"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(chunk_id="chunk_001", title="Financial support and sponsorship", content=""),
            EvidenceChunk(chunk_id="chunk_002", title="", content="Sanofi India"),
        ],
    )

    assert groups
    assert "heading_value_pair" in groups[0].relation_hints
    assert {obj.type for obj in groups[0].objects} >= {
        "funding_section_heading",
        "funding_section_value",
    }


def test_group_builder_builds_recommendation_formulation_groups():
    spec = get_item_spec("item_10")
    objects = [
        _object(
            "formulation_method",
            "formal consensus",
            "formal consensus",
            chunk_id="chunk_001",
        ),
        _object(
            "process_detail",
            "two rounds of voting",
            "two rounds of voting",
            chunk_id="chunk_001",
        ),
        _object(
            "result_detail",
            "90% agreement",
            "90% agreement",
            chunk_id="chunk_001",
        ),
        _object(
            "recommendation_impact",
            "the vote determined final recommendations",
            "the vote determined final recommendations",
            chunk_id="chunk_001",
        ),
    ]
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Guideline development",
                content=(
                    "Recommendations were developed by formal consensus with two rounds of voting. "
                    "Ninety percent agreement was required and the vote determined final recommendations."
                ),
            )
        ],
    )

    assert groups
    assert groups[0].group_type == "recommendation_formulation"
    assert ["formulation_method"] in [
        [obj.type for obj in group.objects]
        for group in groups
    ]
    assert any(
        {obj.type for obj in group.objects}
        == {"formulation_method", "process_detail", "result_detail", "recommendation_impact"}
        for group in groups
    )


def test_group_builder_builds_item_11_benefit_harm_group_by_evidence_unit():
    spec = get_item_spec("item_11")
    objects = [
        _object("recommendation_or_option", "use lower HbA1c target", "use lower HbA1c target"),
        _object("health_benefit", "reduced microvascular disease", "reduced microvascular disease"),
        _object("harm_side_effect_or_risk", "hypoglycaemia", "hypoglycaemia"),
        _object("benefit_harm_balance_statement", "balance benefits against hypoglycaemia", "balance benefits against hypoglycaemia"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendation rationale",
                content=(
                    "Use lower HbA1c target when reduced microvascular disease benefit is expected; "
                    "balance benefits against hypoglycaemia."
                ),
            )
        ],
    )

    assert groups
    assert groups[0].group_type == "benefit_harm_balance"
    assert "same_evidence_unit" in groups[0].relation_hints
    assert {obj.type for obj in groups[0].objects} >= {
        "recommendation_or_option",
        "health_benefit",
        "harm_side_effect_or_risk",
    }


def test_group_builder_builds_item_12_recommendation_evidence_link_group():
    spec = get_item_spec("item_12")
    objects = [
        _object("recommendation_statement", "Offer treatment A", "Offer treatment A"),
        _object("citation_group", "[12-15]", "[12-15]"),
        _object("evidence_level_or_grade", "GRADE A", "GRADE A"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="Offer treatment A. [12-15] GRADE A.",
            )
        ],
    )

    assert groups
    assert groups[0].group_type == "recommendation_evidence_link"
    assert {obj.type for obj in groups[0].objects} >= {
        "recommendation_statement",
        "citation_group",
        "evidence_level_or_grade",
    }
    assert groups[0].attributes["portfolio_role"] == "anchor"


def test_group_builder_keeps_item_12_uncovered_recommendation_statement_anchor():
    spec = get_item_spec("item_12")
    obj = _object("recommendation_statement", "Offer treatment A", "Offer treatment A")
    store = ObjectStore(spec)
    store.merge([obj], processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="Offer treatment A.",
            )
        ],
    )

    assert groups
    assert groups[0].attributes["portfolio_role"] == "anchor"
    assert groups[0].attributes["has_candidate_evidence"] is False
    assert {obj.type for obj in groups[0].objects} == {"recommendation_statement"}


def test_group_builder_treats_item_12_citation_identifier_as_auxiliary_member():
    spec = get_item_spec("item_12")
    objects = [
        _object("recommendation_statement", "Offer treatment A", "Offer treatment A"),
        _object("recommendation_identifier", "[42]", "[42]"),
        _object("citation_group", "[42]", "[42]"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="Offer treatment A. [42]",
            )
        ],
    )

    anchors = [group for group in groups if group.attributes["portfolio_role"] == "anchor"]
    assert len(anchors) == 1
    assert anchors[0].attributes["anchor_type"] == "recommendation_statement"
    assert "recommendation_identifier" in {obj.type for obj in anchors[0].objects}


def test_group_builder_allows_formal_item_12_identifier_anchor_with_evidence_link():
    spec = get_item_spec("item_12")
    objects = [
        _object("recommendation_identifier", "Recommendation 5B.1", "Recommendation 5B.1"),
        _object("evidence_level_or_grade", "Grade 1B", "Grade 1B"),
        _object("rationale_or_evidence_summary", "supported by cohort studies", "supported by cohort studies"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="Recommendation 5B.1. Grade 1B. Supported by cohort studies.",
            )
        ],
    )

    assert groups
    assert groups[0].attributes["portfolio_role"] == "anchor"
    assert groups[0].attributes["anchor_type"] == "recommendation_identifier"
    assert groups[0].attributes["has_candidate_evidence"] is True


def test_group_builder_demotes_citation_like_item_12_identifier_without_statement():
    spec = get_item_spec("item_12")
    objects = [
        _object("recommendation_identifier", "[42]", "[42]"),
        _object("evidence_level_or_grade", "Grade A", "Grade A"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Evidence",
                content="[42] Grade A.",
            )
        ],
    )

    assert groups
    assert all(group.attributes["portfolio_role"] == "global" for group in groups)


def test_group_builder_demotes_item_12_generic_evidence_method_from_denominator():
    spec = get_item_spec("item_12")
    objects = [
        _object("general_evidence_method", "recommendations were based on evidence", "recommendations were based on evidence"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Methods",
                content="Recommendations were based on evidence.",
            )
        ],
    )

    assert groups
    assert all(group.attributes["portfolio_role"] == "global" for group in groups)


def test_group_builder_keeps_item_11_table_option_with_candidate_evidence_as_anchor():
    spec = get_item_spec("item_11")
    objects = [
        _object("recommendation_or_option", "Drug A", "Drug A"),
        _object("health_benefit", "lowers HbA1c", "lowers HbA1c"),
        _object("harm_side_effect_or_risk", "hypoglycaemia", "hypoglycaemia"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Drug table",
                content=(
                    "<table><tr><td>Drug A</td><td>lowers HbA1c</td>"
                    "<td>hypoglycaemia</td></tr></table>"
                ),
            )
        ],
    )

    assert groups
    assert groups[0].attributes["portfolio_role"] == "anchor"
    assert groups[0].attributes["has_candidate_evidence"] is True
    assert {obj.type for obj in groups[0].objects} >= {
        "recommendation_or_option",
        "health_benefit",
        "harm_side_effect_or_risk",
    }


def test_group_builder_demotes_item_11_anchor_only_multi_options_to_global():
    spec = get_item_spec("item_11")
    objects = [
        _object("recommendation_or_option", "Drug A", "Drug A"),
        _object("recommendation_or_option", "Drug B", "Drug B"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Drug table",
                content="Drug A. Drug B.",
            )
        ],
    )

    assert groups
    assert all(group.attributes["portfolio_role"] == "global" for group in groups)
    assert all(group.attributes["has_candidate_evidence"] is False for group in groups)


def test_group_builder_demotes_item_11_anchor_only_single_option_to_global():
    spec = get_item_spec("item_11")
    obj = _object("recommendation_or_option", "Drug A", "Drug A")
    store = ObjectStore(spec)
    store.merge([obj], processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Drug table",
                content="Drug A.",
            )
        ],
    )

    assert groups
    assert groups[0].attributes["portfolio_role"] == "global"
    assert groups[0].attributes["has_candidate_evidence"] is False


def test_group_builder_keeps_item_11_single_sided_evidence_as_partial_anchor():
    spec = get_item_spec("item_11")
    objects = [
        _object("recommendation_or_option", "Use treatment A", "Use treatment A"),
        _object("health_benefit", "reduces admissions", "reduces admissions"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendation rationale",
                content="Use treatment A because it reduces admissions.",
            )
        ],
    )

    assert groups
    assert groups[0].attributes["portfolio_role"] == "anchor"
    assert groups[0].attributes["has_candidate_evidence"] is True


def test_group_builder_demotes_item_11_global_balance_statement_from_denominator():
    spec = get_item_spec("item_11")
    obj = _object(
        "benefit_harm_balance_statement",
        "the panel considered benefits and harms",
        "the panel considered benefits and harms",
    )
    store = ObjectStore(spec)
    store.merge([obj], processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Methods",
                content="The panel considered benefits and harms when developing recommendations.",
            )
        ],
    )

    assert groups
    assert all(group.attributes["portfolio_role"] == "global" for group in groups)


def test_group_verification_prompt_contains_item_11_and_item_12_attributes():
    item11_prompt = render_group_verification_prompt(
        get_item_spec("item_11"),
        [],
        [],
    )
    item12_prompt = render_group_verification_prompt(
        get_item_spec("item_12"),
        [],
        [],
    )

    assert "same_decision" in item11_prompt
    assert "benefit_has_supporting_data" in item11_prompt
    assert "mapping_strength" in item12_prompt
    assert "same_recommendation" in item12_prompt
    assert "Accept incomplete groups" in item11_prompt
    assert "set missing components to false" in item11_prompt
    assert "do not reject because benefit, harm/risk, supporting data" in item11_prompt
    assert "citation numbers like [42]" in item12_prompt
    assert "not recommendation identifiers" in item12_prompt
    assert "mapping_strength=partial" in item12_prompt


def test_group_verification_prompt_includes_portfolio_attributes():
    spec = get_item_spec("item_12")
    group = RuleObjectGroup(
        group_id="item_12:recommendation_evidence_link:1",
        group_type="recommendation_evidence_link",
        objects=(
            _object("recommendation_statement", "Offer treatment A", "Offer treatment A"),
        ),
        chunk_ids=("chunk_001",),
        relation_hints=("same_chunk",),
        attributes={
            "portfolio_role": "anchor",
            "portfolio_anchor_key": "rec-a",
            "has_candidate_evidence": False,
        },
    )

    prompt = render_group_verification_prompt(
        spec,
        [group],
        [EvidenceChunk(chunk_id="chunk_001", title="Recommendations", content="Offer treatment A.")],
    )

    assert "portfolio_anchor_key" in prompt
    assert "has_candidate_evidence" in prompt


def test_group_verifier_batches_item_11_and_item_12_portfolios():
    verifier = LLMRuleObjectGroupVerifier(_FakeLLMClient({"decisions": []}))

    assert verifier._batch_size(get_item_spec("item_11")) == 4
    assert verifier._batch_size(get_item_spec("item_12")) == 4


def test_group_builder_keeps_item_10_singleton_method_group_when_truncated():
    spec = get_item_spec("item_10")
    objects = [
        _object("formulation_method", "formal consensus", "formal consensus", chunk_id="chunk_001"),
    ]
    for index in range(2):
        objects.extend(
            [
                _object(
                    "process_detail",
                    f"round {index + 1} of voting",
                    f"round {index + 1} of voting",
                    chunk_id="chunk_001",
                ),
                _object(
                    "result_detail",
                    f"{90 + index}% agreement",
                    f"{90 + index}% agreement",
                    chunk_id="chunk_001",
                ),
                _object(
                    "recommendation_impact",
                    f"vote {index + 1} shaped final recommendations",
                    f"vote {index + 1} shaped final recommendations",
                    chunk_id="chunk_001",
                ),
            ]
        )
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Guideline development",
                content="Recommendations were developed by formal consensus with voting rounds and agreement thresholds.",
            )
        ],
    )

    assert len(groups) == spec.grouping.max_groups
    assert ["formulation_method"] in [
        [obj.type for obj in group.objects]
        for group in groups
    ]


def test_rubric_unit_builder_builds_implementation_units_from_local_text():
    spec = get_item_spec("item_19")

    units = EvidenceUnitBuilder().build(
        spec,
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Implementation tools",
                content=(
                    "Appendix A provides a patient checklist.\n"
                    "Complete the checklist before discharge."
                ),
                metadata={"section": "Implementation"},
            )
        ],
    )

    assert units
    assert units[0].chunk_id == "chunk_001"
    assert "checklist" in units[0].quote.casefold()


def test_rubric_unit_builder_ranks_implementation_units_before_background():
    spec = get_item_spec("item_19")

    units = EvidenceUnitBuilder().build(
        spec,
        [
            EvidenceChunk(chunk_id="chunk_001", title="Background", content="General treatment background."),
            EvidenceChunk(
                chunk_id="chunk_002",
                title="Implementation resources",
                content="The guideline includes a checklist and patient leaflet.",
            ),
        ],
    )

    assert units[0].chunk_id == "chunk_002"


def test_group_builder_builds_recommendation_specificity_groups_by_evidence_unit():
    spec = get_item_spec("item_15")
    objects = [
        _object("directive_language", "should", "1. Adults should receive metformin.", chunk_id="chunk_001"),
        _object("population_or_condition", "Adults", "1. Adults should receive metformin.", chunk_id="chunk_001"),
        _object("intervention_or_action", "receive metformin", "1. Adults should receive metformin.", chunk_id="chunk_001"),
        _object("directive_language", "should", "2. Children should receive insulin.", chunk_id="chunk_001"),
        _object("population_or_condition", "Children", "2. Children should receive insulin.", chunk_id="chunk_001"),
        _object("intervention_or_action", "receive insulin", "2. Children should receive insulin.", chunk_id="chunk_001"),
    ]
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="1. Adults should receive metformin.\n2. Children should receive insulin.",
            )
        ],
    )

    assert groups
    for group in groups:
        quotes = {obj.evidence.quote for obj in group.objects}
        assert not (
            any("Adults" in quote for quote in quotes)
            and any("Children" in quote for quote in quotes)
        )


def test_group_builder_builds_recommendation_specificity_group_for_population_intervention_only():
    spec = get_item_spec("item_15")
    objects = [
        _object("population_or_condition", "Adults with type 2 diabetes", "Adults with type 2 diabetes: metformin.", chunk_id="chunk_001"),
        _object("intervention_or_action", "metformin", "Adults with type 2 diabetes: metformin.", chunk_id="chunk_001"),
    ]
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Recommendations",
                content="Adults with type 2 diabetes: metformin.",
            )
        ],
    )

    assert len(groups) == 1
    assert groups[0].group_type == "recommendation_specificity"
    assert {obj.type for obj in groups[0].objects} == {
        "population_or_condition",
        "intervention_or_action",
    }
    assert "same_evidence_unit" in groups[0].relation_hints


def test_group_builder_builds_health_question_groups_by_evidence_unit():
    spec = get_item_spec("item_2")
    objects = [
        _object("health_question_statement", "Question 1", "1. In adults, does metformin compared with usual care improve HbA1c?", chunk_id="chunk_001"),
        _object("question_population_or_problem", "adults", "1. In adults, does metformin compared with usual care improve HbA1c?", chunk_id="chunk_001"),
        _object("question_intervention_or_exposure", "metformin", "1. In adults, does metformin compared with usual care improve HbA1c?", chunk_id="chunk_001"),
        _object("question_comparator", "usual care", "1. In adults, does metformin compared with usual care improve HbA1c?", chunk_id="chunk_001"),
        _object("question_outcome", "HbA1c", "1. In adults, does metformin compared with usual care improve HbA1c?", chunk_id="chunk_001"),
        _object("health_question_statement", "Question 2", "2. In children, does insulin improve hypoglycaemia?", chunk_id="chunk_001"),
        _object("question_population_or_problem", "children", "2. In children, does insulin improve hypoglycaemia?", chunk_id="chunk_001"),
        _object("question_intervention_or_exposure", "insulin", "2. In children, does insulin improve hypoglycaemia?", chunk_id="chunk_001"),
    ]
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Clinical questions",
                content=(
                    "1. In adults, does metformin compared with usual care improve HbA1c?\n"
                    "2. In children, does insulin improve hypoglycaemia?"
                ),
            )
        ],
    )

    assert groups
    assert all(group.group_type == "health_question" for group in groups)
    for group in groups:
        quotes = {obj.evidence.quote for obj in group.objects}
        assert not (
            any("adults" in quote for quote in quotes)
            and any("children" in quote for quote in quotes)
        )
    assert "health_question_heading" in groups[0].relation_hints


def test_group_builder_builds_health_question_group_for_implicit_pico_elements():
    spec = get_item_spec("item_2")
    objects = [
        _object("question_population_or_problem", "adults with diabetes", "Adults with diabetes; metformin versus usual care.", chunk_id="chunk_001"),
        _object("question_intervention_or_exposure", "metformin", "Adults with diabetes; metformin versus usual care.", chunk_id="chunk_001"),
        _object("question_comparator", "usual care", "Adults with diabetes; metformin versus usual care.", chunk_id="chunk_001"),
    ]
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Scope",
                content="Adults with diabetes; metformin versus usual care.",
            )
        ],
    )

    assert len(groups) == 1
    assert groups[0].group_type == "health_question"
    assert {obj.type for obj in groups[0].objects} == {
        "question_population_or_problem",
        "question_intervention_or_exposure",
        "question_comparator",
    }


def test_health_question_engine_scores_implicit_population_and_intervention_as_mid_high():
    spec = get_item_spec("item_2")
    group = RuleObjectGroup(
        group_id="item_2:health_question:1",
        group_type="health_question",
        objects=(
            _object("question_population_or_problem", "adults with diabetes", "adults with diabetes"),
            _object("question_intervention_or_exposure", "metformin", "metformin"),
        ),
        chunk_ids=("chunk_001",),
        relation_hints=("same_evidence_unit",),
        attributes={
            "same_question": "true",
            "question_framing": "implicit",
            "scope": "current_guideline",
            "picos_present": "P,I",
        },
    )
    grouping = RuleGroupingResult(candidate_groups=[group], accepted_groups=[group], decisions=[])
    store = ObjectStore(spec)
    store.merge(list(group.objects), processed_chunks=["chunk_001"])

    result = get_rule_engine(spec.rule_profile).execute(spec, store.snapshot(), grouping)

    assert result.score_range == "4-5"


def test_group_builder_skips_item_6_after_target_user_grouping_removed():
    spec = get_item_spec("item_6")
    objects = [
        _object("clear_target_user", "primary care clinicians", "primary care clinicians", chunk_id="chunk_001"),
        _object("clear_intended_use", "inform clinical decision-making", "inform clinical decision-making", chunk_id="chunk_001"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Purpose",
                content="This guideline is intended for primary care clinicians to inform clinical decision-making.",
            )
        ],
    )

    assert spec.grouping is None
    assert groups == []


def test_group_builder_builds_management_option_groups_from_same_section():
    spec = get_item_spec("item_16")
    objects = [
        _object("clinical_issue_or_decision_point", "initial treatment", "initial treatment", chunk_id="chunk_001"),
        _object("management_option", "metformin", "metformin", chunk_id="chunk_001"),
        _object("management_option", "insulin", "insulin", chunk_id="chunk_001"),
        _object("preference_order", "first-line", "first-line", chunk_id="chunk_001"),
    ]
    from core.rule_engine.stores.object_store import ObjectStore

    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Treatment options",
                content="For initial treatment, metformin is first-line and insulin is an alternative option.",
                metadata={"section": "Recommendations"},
            )
        ],
    )

    assert groups
    assert groups[0].group_type == "management_options"
    assert {obj.type for obj in groups[0].objects} >= {
        "clinical_issue_or_decision_point",
        "management_option",
        "preference_order",
    }
    assert "management_options_heading" in groups[0].relation_hints


def test_group_builder_builds_formulation_groups_from_weak_anchor():
    spec = get_item_spec("item_10")
    objects = [
        _object("consensus_process_heading", "Consensus process", "Consensus process", chunk_id="chunk_001"),
        _object("process_detail", "two voting rounds", "two voting rounds", chunk_id="chunk_001"),
        _object("result_detail", "90% agreement", "90% agreement", chunk_id="chunk_001"),
    ]
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])

    groups = RuleObjectGroupBuilder().build(
        spec,
        store.snapshot(),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Consensus process",
                content="Consensus process used two voting rounds and reached 90% agreement.",
            )
        ],
    )

    assert groups
    assert groups[0].group_type == "recommendation_formulation"
    assert "consensus_process_heading" in {obj.type for obj in groups[0].objects}


def test_rubric_unit_builder_preserves_chunk_metadata_for_citations():
    spec = get_item_spec("item_19")

    units = EvidenceUnitBuilder().build(
        spec,
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Implementation tools",
                content="The appendix includes a patient checklist.",
                metadata={"section": "Implementation", "start_page": 10},
            )
        ],
    )

    assert units
    assert units[0].metadata["section"] == "Implementation"
    assert units[0].metadata["start_page"] == 10


def test_group_verification_prompt_includes_grouping_guidance_and_relation_hints():
    spec = get_item_spec("item_22")
    group = RuleObjectGroup(
        group_id="item_22:funding_disclosure:1",
        group_type="funding_disclosure",
        objects=(
            _object("funding_section_heading", "Financial support", "Financial support"),
            _object("funding_section_value", "Sanofi India", "Sanofi India"),
        ),
        chunk_ids=("chunk_001",),
        relation_hints=("heading_value_pair",),
    )

    prompt = render_group_verification_prompt(
        spec,
        [group],
        [EvidenceChunk(chunk_id="chunk_001", title="Financial support", content="Sanofi India")],
    )

    assert "Grouping Standard" in prompt
    assert "heading_value_pair" in prompt
    assert "status = none|funded|commissioned|meeting_only|unknown" in prompt
    assert "Do not score the item" not in prompt


def test_rubric_prompt_requires_item_21_bands_and_attributes():
    spec = get_item_spec("item_21")
    unit = EvidenceUnit(
        unit_id="item_21:item21_monitoring_criteria:1",
        chunk_id="chunk_001",
        title="Monitoring",
        quote="Blood pressure target",
        content="Blood pressure target",
        unit_type="paragraph",
    )

    prompt = render_rubric_classification_prompt(
        spec,
        [unit],
    )

    assert "criterion_plus_frequency_plus_method" in prompt
    assert "excluded_clinical_episode" in prompt
    assert "has_indicator_or_standard" in prompt
    assert "has_frequency_or_interval" in prompt
    assert "has_measurement_method" in prompt
    assert '"accepted": false' in prompt


def test_rubric_prompt_lists_item_18_skill_contract():
    spec = get_item_spec("item_18")
    unit = EvidenceUnit(
        unit_id="item_18:item18_application_factors:1",
        chunk_id="chunk_001",
        title="Implementation",
        quote="Staff shortage limits implementation.",
        content="Staff shortage limits implementation.",
        unit_type="paragraph",
    )

    prompt = render_rubric_classification_prompt(
        spec,
        [unit],
    )

    assert "single_direction_factor" in prompt
    assert "both_facilitator_and_barrier" in prompt
    assert "current_guideline_application" in prompt
    assert "clinical_care_or_patient_management" in prompt
    assert "has_collection_method" in prompt
    assert "has_recommendation_or_development_impact" in prompt


def test_group_verification_prompt_includes_item_15_and_item_16_boundaries():
    item15 = get_item_spec("item_15")
    item15_group = RuleObjectGroup(
        group_id="item_15:recommendation_specificity:1",
        group_type="recommendation_specificity",
        objects=(
            _object("directive_language", "should", "Adults should receive metformin."),
            _object("population_or_condition", "Adults", "Adults should receive metformin."),
            _object("intervention_or_action", "metformin", "Adults should receive metformin."),
        ),
        chunk_ids=("chunk_001",),
        relation_hints=("same_evidence_unit",),
    )
    item16 = get_item_spec("item_16")
    item16_group = RuleObjectGroup(
        group_id="item_16:management_options:1",
        group_type="management_options",
        objects=(
            _object("management_option", "metformin", "metformin"),
            _object("management_option", "insulin", "insulin"),
        ),
        chunk_ids=("chunk_001",),
        relation_hints=("same_section",),
    )

    prompt15 = render_group_verification_prompt(
        item15,
        [item15_group],
        [EvidenceChunk(chunk_id="chunk_001", title="Recommendations", content="Adults should receive metformin.")],
    )
    prompt16 = render_group_verification_prompt(
        item16,
        [item16_group],
        [EvidenceChunk(chunk_id="chunk_001", title="Treatment options", content="Metformin or insulin may be used.")],
    )

    assert "For item15 recommendation-specificity groups" in prompt15
    assert "same current-guideline recommendation" in prompt15
    assert "same_recommendation" in prompt15
    assert "specificity_components" in prompt15
    assert "For item16 management-options groups" in prompt16
    assert "same_issue" in prompt16
    assert "option_count" in prompt16
    assert "has_conditions" in prompt16
    assert "has_preference_or_pathway" in prompt16
    assert "external_or_evidence_only" in prompt16


def test_rubric_prompt_requires_item_19_bands_and_attributes():
    spec = get_item_spec("item_19")
    unit = EvidenceUnit(
        unit_id="item_19:item19_implementation_support:1",
        chunk_id="chunk_001",
        title="Implementation tools",
        quote="Complete the checklist.",
        content="Complete the checklist.",
        unit_type="paragraph",
    )

    prompt = render_rubric_classification_prompt(
        spec,
        [unit],
    )

    assert "eligible_tool_with_use_instruction" in prompt
    assert "complete_tool_with_use_instruction" in prompt
    assert "tool_like_but_not_eligible" in prompt
    assert "is_current_guideline_resource" in prompt
    assert "is_concrete_usable_aid" in prompt
    assert "has_clear_tool_use_instruction" in prompt
    assert "Clinical management instructions are not tool-use instructions" in prompt


def test_group_verification_prompt_requires_item_2_attributes():
    spec = get_item_spec("item_2")
    group = RuleObjectGroup(
        group_id="item_2:health_question:1",
        group_type="health_question",
        objects=(
            _object("health_question_statement", "clinical question", "clinical question"),
            _object("question_population_or_problem", "adults", "adults"),
            _object("question_intervention_or_exposure", "metformin", "metformin"),
        ),
        chunk_ids=("chunk_001",),
        relation_hints=("same_evidence_unit", "health_question_heading"),
    )

    prompt = render_group_verification_prompt(
        spec,
        [group],
        [EvidenceChunk(chunk_id="chunk_001", title="Clinical questions", content="Adults and metformin.")],
    )

    assert "For item2 health-question groups" in prompt
    assert "same_question" in prompt
    assert "question_framing" in prompt
    assert "picos_present" in prompt
    assert "comma-separated string" in prompt
    assert "evidence-selection criteria" in prompt


def test_group_verification_prompt_no_longer_has_item_6_profile_rules():
    spec = get_item_spec("item_6")
    group = RuleObjectGroup(
        group_id="item_6:target_user:1",
        group_type="target_user",
        objects=(
            _object("clear_target_user", "primary care clinicians", "primary care clinicians"),
            _object("clear_intended_use", "inform clinical decision-making", "inform clinical decision-making"),
        ),
        chunk_ids=("chunk_001",),
        relation_hints=("same_evidence_unit", "target_user_heading"),
    )

    prompt = render_group_verification_prompt(
        spec,
        [group],
        [EvidenceChunk(chunk_id="chunk_001", title="Purpose", content="For primary care clinicians.")],
    )

    assert "For item6 target-user groups" not in prompt
    assert "rubric_role" not in prompt
    assert "user_definition" not in prompt
    assert "use_definition" not in prompt
    assert "Set only attributes directly supported by the candidate evidence" in prompt


def test_llm_group_verifier_merges_decision_attributes_into_accepted_group():
    spec = get_item_spec("item_22")
    group = RuleObjectGroup(
        group_id="item_22:funding_disclosure:1",
        group_type="funding_disclosure",
        objects=(_object("funding_disclosed", "Sanofi India", "Sanofi India"),),
        chunk_ids=("chunk_001",),
    )
    verifier = LLMRuleObjectGroupVerifier(
        _FakeLLMClient(
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": True,
                        "confidence": 0.9,
                        "rejection_code": "",
                        "reason": "current manuscript funding",
                        "attributes": {
                            "status": "funded",
                            "scope": "current_manuscript",
                            "funder_type": "for_profit",
                            "influence": "none",
                        },
                    }
                ]
            }
        )
    )

    result = verifier.verify(
        spec,
        [group],
        [EvidenceChunk(chunk_id="chunk_001", title="Funding", content="Sanofi India")],
    )

    assert len(result.accepted_groups) == 1
    assert result.accepted_groups[0].attributes["funder_type"] == "for_profit"
    assert result.decisions[0].attributes["scope"] == "current_manuscript"
    assert verifier.llm_client.calls[0]["schema_name"] == "rule_object_group_verification"
    assert verifier.llm_client.calls[0]["json_schema"]["properties"]["decisions"]["minItems"] == 1


def test_item_2_group_verifier_retries_missing_scoring_attributes():
    spec = get_item_spec("item_2")
    group = RuleObjectGroup(
        group_id="item_2:health_question:1",
        group_type="health_question",
        objects=(_object("implicit_question_scope", "scope", "scope"),),
        chunk_ids=("chunk_001",),
    )
    decision = {
        "index": 0,
        "accepted": True,
        "confidence": 0.9,
        "rejection_code": "",
        "reason": "current guideline question",
        "attributes": {},
    }
    client = _FakeLLMClient([
        {"decisions": [decision]},
        {"decisions": [{
            **decision,
            "attributes": {
                "same_question": True,
                "question_framing": "implicit",
                "scope": "current_guideline",
                "picos_present": "P,I",
            },
        }]},
    ])

    result = LLMRuleObjectGroupVerifier(client).verify(spec, [group], [_chunk("scope")])

    attributes_schema = client.calls[0]["json_schema"]["properties"]["decisions"]["items"]["properties"]["attributes"]
    assert set(attributes_schema["required"]) == {
        "same_question", "question_framing", "scope", "picos_present"
    }
    assert [call["stage"] for call in client.calls] == [
        "RuleObjectGroupVerification:item_2",
        "RuleObjectGroupVerificationRetry:item_2",
    ]
    assert result.accepted_groups[0].attributes["question_framing"] == "implicit"


def test_rubric_classifier_uses_profile_specific_schema_for_item_21():
    spec = get_item_spec("item_21")
    unit = EvidenceUnit(
        unit_id="item_21:item21_monitoring_criteria:1",
        chunk_id="chunk_001",
        title="Hypoglycemia",
        quote="recheck after 15 minutes",
        content="recheck after 15 minutes",
        unit_type="paragraph",
    )
    client = _FakeLLMClient(
        {
            "decisions": [
                {
                    "index": 0,
                    "accepted": False,
                    "band": "excluded_clinical_episode",
                    "score_range": "1",
                    "confidence": 0.9,
                    "reason_code": "acute_management",
                    "reason": "acute treatment recheck, not item21 monitoring criteria",
                    "attributes": {
                        "is_current_guideline_criterion": False,
                        "has_indicator_or_standard": False,
                        "has_frequency_or_interval": True,
                        "has_measurement_method": False,
                        "measurement_function": "clinical_episode_instruction",
                        "exclusion_reason": "acute recheck",
                    },
                }
            ]
        }
    )

    result = RubricClassifier(client).classify(spec, [unit])

    assert result.accepted_units == []
    assert result.decisions[0].accepted is False
    assert result.review_reasons == []
    attributes_schema = client.calls[0]["json_schema"]["properties"]["decisions"]["items"]["properties"]["attributes"]
    assert "measurement_function" in attributes_schema["properties"]
    assert "clinical_episode_instruction" in attributes_schema["properties"]["measurement_function"]["enum"]


def test_rubric_classifier_retries_when_no_evidence_is_marked_accepted():
    spec = get_item_spec("item_21")
    unit = EvidenceUnit(
        unit_id="item_21:item21_monitoring_criteria:1",
        chunk_id="chunk_001",
        title="Monitoring",
        quote="No monitoring criteria here.",
        content="No monitoring criteria here.",
        unit_type="paragraph",
    )
    client = _FakeLLMClient(
        [
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": True,
                        "band": "no_evidence",
                        "score_range": "1",
                        "confidence": 0.8,
                        "reason_code": "bad_contract",
                        "reason": "invalid accepted flag",
                        "attributes": {
                            "is_current_guideline_criterion": False,
                            "has_indicator_or_standard": False,
                            "has_frequency_or_interval": False,
                            "has_measurement_method": False,
                            "measurement_function": "unclear",
                            "exclusion_reason": "",
                        },
                    }
                ]
            },
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": False,
                        "band": "no_evidence",
                        "score_range": "1",
                        "confidence": 0.8,
                        "reason_code": "no_monitoring",
                        "reason": "no scoreable monitoring criteria",
                        "attributes": {
                            "is_current_guideline_criterion": False,
                            "has_indicator_or_standard": False,
                            "has_frequency_or_interval": False,
                            "has_measurement_method": False,
                            "measurement_function": "unclear",
                            "exclusion_reason": "",
                        },
                    }
                ]
            },
        ]
    )

    result = RubricClassifier(client).classify(spec, [unit])

    assert [call["stage"] for call in client.calls] == [
        "RubricUnitClassification:item_21",
        "RubricUnitClassificationRetry:item_21",
    ]
    assert result.decisions[0].accepted is False
    assert result.review_reasons[0].code == "rubric_classification_json_retry"


def test_rubric_classifier_retries_missing_index_and_recovers():
    spec = get_item_spec("item_21")
    unit = EvidenceUnit(
        unit_id="item_21:item21_monitoring_criteria:1",
        chunk_id="chunk_001",
        title="Monitoring",
        quote="HbA1c target",
        content="HbA1c target",
        unit_type="paragraph",
    )
    client = _FakeLLMClient(
        [
            {"decisions": []},
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": True,
                        "band": "criterion_or_frequency_only",
                        "score_range": "3-4",
                        "confidence": 0.9,
                        "reason_code": "accepted_on_retry",
                        "reason": "accepted on retry",
                        "attributes": {
                            "is_current_guideline_criterion": True,
                            "has_indicator_or_standard": True,
                            "has_frequency_or_interval": False,
                            "has_measurement_method": False,
                            "measurement_function": "ongoing_care_target",
                            "exclusion_reason": "",
                        },
                    }
                ]
            },
        ]
    )

    result = RubricClassifier(client).classify(spec, [unit])

    assert [call["stage"] for call in client.calls] == [
        "RubricUnitClassification:item_21",
        "RubricUnitClassificationRetry:item_21",
    ]
    assert len(result.accepted_units) == 1
    assert result.review_reasons[0].code == "rubric_classification_json_retry"


def test_rubric_classifier_fails_unit_still_missing_after_retry():
    spec = get_item_spec("item_21")
    unit = EvidenceUnit(
        unit_id="item_21:item21_monitoring_criteria:1",
        chunk_id="chunk_001",
        title="Monitoring",
        quote="HbA1c target",
        content="HbA1c target",
        unit_type="paragraph",
    )
    client = _FakeLLMClient([{"decisions": []}, {"decisions": []}])

    with pytest.raises(ValueError, match="missing decision"):
        RubricClassifier(client).classify(spec, [unit])


def test_run_rule_pipeline_uses_object_group_result_for_item_21():
    class _DynamicGroupClient:
        def complete_json(
            self,
            *,
            stage: str,
            system_prompt: str,
            user_prompt: str,
            max_tokens: int,
            json_schema: dict | None = None,
            schema_name: str = "rule_engine_response",
            strict: bool = True,
            list_wrapper: str | None = None,
        ) -> dict:
            count = json_schema["properties"]["decisions"]["maxItems"]
            attrs = {
                "criterion_origin": "audit_or_quality_standard",
                "measurement_purpose": "measure_guideline_application",
                "measurement_function": "application_audit_measure",
                "criterion_kind": "process_measure",
                "has_measure_target": True,
                "has_threshold_or_standard": True,
                "has_timing_or_frequency": True,
                "has_measurement_method": True,
                "same_measurement_criterion": True,
            }
            decisions = [
                {
                    "index": 0,
                    "accepted": True,
                    "confidence": 0.9,
                    "rejection_code": "",
                    "reason": "Same audit criterion.",
                    "attributes": attrs,
                }
            ]
            decisions.extend(
                {
                    "index": index,
                    "accepted": False,
                    "confidence": 0.8,
                    "rejection_code": "duplicate_or_less_complete_group",
                    "reason": "Less complete duplicate candidate.",
                    "attributes": attrs,
                }
                for index in range(1, count)
            )
            return {"decisions": decisions}

    result = run_rule_pipeline(
        "item_21",
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Audit standards",
                content="HbA1c should be checked every 6 months using the audit tool.",
            )
        ],
        llm_client=_DynamicGroupClient(),
        extractor=_PipelineMonitoringExtractor(),
        verifier=NoopRuleObjectVerifier(),
    )

    assert result.grouping_result is not None
    assert result.grouping_result.accepted_groups
    assert result.rubric_result is None
    assert result.rubric_appraiser_result is None
    assert result.rule_result.score_range == "7"


def test_rubric_portfolio_appraiser_retries_with_minimal_schema_after_json_error():
    spec = get_item_spec("item_21")
    unit = EvidenceUnit(
        unit_id="item_21:item21_monitoring_criteria_appraiser:1",
        chunk_id="chunk_001",
        title="Audit standards",
        quote="Audit standards: HbA1c should be checked every 6 months.",
        content="Audit standards: HbA1c should be checked every 6 months.",
        unit_type="paragraph",
    )
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="Audit standards",
        content="Audit standards: HbA1c should be checked every 6 months.",
    )
    client = _FakeLLMClient(
        [
            ValueError("bad json"),
            {
                "score": 5,
                "decision_label": "clear_monitoring_criteria",
                "evidence": [
                    {
                        "chunk_id": "chunk_001",
                        "quote": "Audit standards: HbA1c should be checked every 6 months.",
                    }
                ],
            },
        ]
    )

    result = RubricPortfolioAppraiser(client).appraise(spec, [unit], [chunk])

    assert result.decision.score == 5
    assert result.decision.evidence[0].chunk_id == "chunk_001"
    assert [call["stage"] for call in client.calls] == [
        "RubricPortfolioAppraisal:item_21",
        "RubricPortfolioAppraisalRetry:item_21",
    ]
    assert client.calls[1]["schema_name"] == "rubric_portfolio_appraisal_minimal"
    assert result.review_reasons[0].code == "rubric_appraiser_json_retry"


def test_rubric_portfolio_appraiser_two_json_failures_raise():
    spec = get_item_spec("item_19")
    unit = EvidenceUnit(
        unit_id="item_19:item19_implementation_support_appraiser:1",
        chunk_id="chunk_001",
        title="Implementation",
        quote="Implementation content.",
        content="Implementation content.",
        unit_type="paragraph",
    )
    chunk = EvidenceChunk(chunk_id="chunk_001", title="Implementation", content="Implementation content.")
    client = _FakeLLMClient([ValueError("bad json"), ValueError("still bad")])

    with pytest.raises(ValueError, match="failed after JSON"):
        RubricPortfolioAppraiser(client).appraise(spec, [unit], [chunk])


def test_rubric_portfolio_appraiser_propagates_non_json_llm_errors():
    spec = get_item_spec("item_19")
    unit = EvidenceUnit(
        unit_id="item_19:item19_implementation_support_appraiser:1",
        chunk_id="chunk_001",
        title="Implementation",
        quote="Implementation content.",
        content="Implementation content.",
        unit_type="paragraph",
    )
    chunk = EvidenceChunk(chunk_id="chunk_001", title="Implementation", content="Implementation content.")
    client = _FakeLLMClient([RuntimeError("request timed out")])

    with pytest.raises(RuntimeError, match="request timed out"):
        RubricPortfolioAppraiser(client).appraise(spec, [unit], [chunk])


def test_rubric_portfolio_appraiser_fails_when_quote_cannot_be_located():
    spec = get_item_spec("item_18")
    unit = EvidenceUnit(
        unit_id="item_18:item18_application_factors_appraiser:1",
        chunk_id="chunk_001",
        title="Implementation",
        quote="Implementation barriers are described.",
        content="Implementation barriers are described.",
        unit_type="paragraph",
    )
    chunk = EvidenceChunk(chunk_id="chunk_001", title="Implementation", content="Implementation barriers are described.")
    client = _FakeLLMClient(
        {
            "score": 4,
            "confidence": 0.8,
            "decision_label": "facilitator_and_barrier_described",
            "rationale": "Application factors are described.",
            "why_not_higher": "No collection method.",
            "evidence": [{"chunk_id": "chunk_001", "quote": "This quote is not in the chunk."}],
        }
    )

    client.payloads.append(dict(client.payloads[0]))
    with pytest.raises(ValueError, match="failed after JSON"):
        RubricPortfolioAppraiser(client).appraise(spec, [unit], [chunk])


def test_run_rule_pipeline_uses_grouping_result_for_item_22():
    class _FundingExtractor:
        def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
            return ExtractionBatchResult(
                objects=[
                    RuleObject(
                        type="funding_section_heading",
                        value="Financial support and sponsorship",
                        evidence=EvidenceRef(chunk_id="chunk_001", quote="Financial support and sponsorship"),
                        confidence=0.95,
                    ),
                    RuleObject(
                        type="funding_section_value",
                        value="Sanofi India",
                        evidence=EvidenceRef(chunk_id="chunk_002", quote="Sanofi India"),
                        confidence=0.95,
                    ),
                ]
            )

    result = run_rule_pipeline(
        "item_22",
        [
            EvidenceChunk(chunk_id="chunk_001", title="Financial support and sponsorship", content=""),
            EvidenceChunk(chunk_id="chunk_002", title="", content="Sanofi India"),
        ],
        llm_client=_FakeLLMClient(
            {
                "decisions": [
                    {
                        "index": 0,
                        "accepted": True,
                        "confidence": 0.9,
                        "rejection_code": "",
                        "reason": "commercial support disclosure",
                        "attributes": {
                            "status": "funded",
                            "scope": "current_manuscript",
                            "funder_type": "for_profit",
                            "influence": "none",
                        },
                    }
                ]
            }
        ),
        extractor=_FundingExtractor(),
        verifier=NoopRuleObjectVerifier(),
    )

    assert result.grouping_result is not None
    assert len(result.grouping_result.accepted_groups) == 1
    assert result.rule_result.score_range == "2-4"
