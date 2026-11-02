from __future__ import annotations

import pytest

from core.rule_engine.extractors.llm import LLMObjectExtractor
from core.rule_engine.models import (
    EvidenceChunk,
    EvidenceRef,
    ExtractionBatchResult,
    ItemSpec,
    ReviewReason,
    RuleObject,
    RuleObjectSpec,
    WeightedChecklistItem,
    WeightedChecklistRule,
)
from core.rule_engine.registry import get_item_spec, get_rule_engine
from core.rule_engine.prompts.extraction import render_extraction_prompt
from core.rule_engine.runner import run_rule_pipeline
from core.rule_engine.validation import StrictAllOrNothing
from core.rule_engine.stores.object_store import ObjectStore


def _rule_object(
    object_type: str,
    value: str | None = None,
    *,
    quote: str = "direct quote",
    chunk_id: str = "chunk_001",
    confidence: float = 0.95,
) -> RuleObject:
    return RuleObject(
        type=object_type,
        value=value or object_type,
        evidence=EvidenceRef(chunk_id=chunk_id, quote=quote),
        confidence=confidence,
    )


def _evaluate_item_1(objects: list[RuleObject]):
    spec = get_item_spec("item_1")
    store = ObjectStore(spec)
    store.merge(objects, processed_chunks=["chunk_001"])
    snapshot = store.snapshot()
    engine = get_rule_engine(spec.rule_profile)
    return engine.execute(spec, snapshot)


def test_item_1_without_health_topic_scores_one():
    result = _evaluate_item_1([])

    assert result.score_range == "1"
    assert result.allowed_scores == [1]
    assert result.max_score == 1
    assert result.missing == ["health_topic"]
    assert result.needs_review is False


def test_item_1_health_topic_only_scores_two_to_three():
    result = _evaluate_item_1([
        _rule_object("health_topic", "二甲双胍"),
    ])

    assert result.score_range == "2-3"
    assert result.allowed_scores == [2, 3]
    assert result.max_score == 3
    assert result.matched_rules == ["health_topic"]
    assert result.missing == ["health_intent", "expected_outcome", "target_population"]


def test_item_1_one_component_scores_four_to_five():
    result = _evaluate_item_1([
        _rule_object("health_topic", "2 型糖尿病"),
        _rule_object("health_intent", "治疗管理"),
    ])

    assert result.score_range == "4-5"
    assert result.allowed_scores == [4, 5]
    assert result.max_score == 5
    assert result.matched_rules == ["health_topic", "health_intent"]
    assert result.missing == ["expected_outcome", "target_population"]


def test_item_1_two_components_score_six():
    result = _evaluate_item_1([
        _rule_object("health_topic", "2 型糖尿病"),
        _rule_object("health_intent", "治疗管理"),
        _rule_object("target_population", "成人 2 型糖尿病患者"),
    ])

    assert result.score_range == "6"
    assert result.allowed_scores == [6]
    assert result.max_score == 6
    assert result.matched_rules == ["health_topic", "health_intent", "target_population"]
    assert result.missing == ["expected_outcome"]


def test_item_1_three_components_score_seven():
    result = _evaluate_item_1([
        _rule_object("health_topic", "2 型糖尿病"),
        _rule_object("health_intent", "治疗管理"),
        _rule_object("expected_outcome", "减少并发症"),
        _rule_object("target_population", "成人 2 型糖尿病患者"),
    ])

    assert result.score_range == "7"
    assert result.allowed_scores == [7]
    assert result.max_score == 7
    assert result.missing == []


def test_item_1_extraction_prompt_includes_functional_and_social_outcomes():
    prompt = render_extraction_prompt(
        get_item_spec("item_1"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Conclusion",
                content="People with diabetes can safely and effectively perform jobs with reasonable accommodations.",
            )
        ],
    )

    assert "安全有效工作" in prompt
    assert "维持就业资格" in prompt
    assert "公平就业" in prompt
    assert "社会参与" in prompt


def test_rule_extraction_prompt_requires_source_language_value_span():
    prompt = render_extraction_prompt(
        get_item_spec("item_1"),
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="Scope",
                content="This guideline addresses type 2 diabetes.",
            )
        ],
    )

    assert "short source-language span copied verbatim from the \"quote\"" in prompt
    assert "Preserve the source document language exactly" in prompt
    assert "Do not translate" in prompt
    assert '"value": "<short span copied verbatim from the quote>"' in prompt
    assert '"quote": "<sentence copied verbatim from that chunk>"' in prompt


def test_item_1_low_confidence_marks_needs_review():
    result = _evaluate_item_1([
        _rule_object("health_topic", "2 型糖尿病"),
        _rule_object("expected_outcome", "提高诊疗质量", confidence=0.4),
    ])

    assert result.score_range == "4-5"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["low_confidence_object"]


def test_item_1_missing_quote_marks_needs_review():
    result = _evaluate_item_1([
        _rule_object("health_topic", "2 型糖尿病"),
        _rule_object("target_population", "成人 2 型糖尿病患者", quote=""),
    ])

    assert result.score_range == "2-3"
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["missing_quote"]
    assert result.missing == ["health_intent", "expected_outcome", "target_population"]


def test_item_1_store_deduplicates_objects():
    duplicate = _rule_object("health_topic", "2 型糖尿病")
    spec = get_item_spec("item_1")
    store = ObjectStore(spec)

    store.merge([duplicate, duplicate], processed_chunks=["chunk_001"])
    snapshot = store.snapshot()

    assert len(snapshot.objects) == 1
    assert snapshot.has_object("health_topic") is True


class _ChunkAwareExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        objects: list[RuleObject] = []
        for chunk in chunks:
            if "主题" in chunk.content:
                objects.append(_rule_object("health_topic", "2 型糖尿病", chunk_id=chunk.chunk_id, quote="主题"))
            if "治疗" in chunk.content:
                objects.append(_rule_object("health_intent", "治疗", chunk_id=chunk.chunk_id, quote="治疗"))
            if "患者" in chunk.content:
                objects.append(_rule_object("target_population", "成人患者", chunk_id=chunk.chunk_id, quote="患者"))
        return ExtractionBatchResult(objects=objects)


def test_rule_pipeline_merges_objects_across_chunks():
    chunks = [
        EvidenceChunk(chunk_id="chunk_001", title="摘要", content="主题：2 型糖尿病"),
        EvidenceChunk(chunk_id="chunk_002", title="目的", content="用于治疗管理"),
        EvidenceChunk(chunk_id="chunk_003", title="范围", content="适用于成人患者"),
    ]

    result = run_rule_pipeline(
        "item_1",
        chunks,
        extractor=_ChunkAwareExtractor(),
        batch_size=1,
    )

    assert result.rule_result.score_range == "6"
    assert result.rule_result.allowed_scores == [6]
    assert result.snapshot.processed_chunks == ("chunk_001", "chunk_002", "chunk_003")


def test_rule_pipeline_calls_cancellation_check_before_each_batch():
    chunks = [
        EvidenceChunk(chunk_id="chunk_001", title="摘要", content="主题：2 型糖尿病"),
        EvidenceChunk(chunk_id="chunk_002", title="目的", content="用于治疗管理"),
        EvidenceChunk(chunk_id="chunk_003", title="范围", content="适用于成人患者"),
    ]
    calls = 0

    def cancellation_check():
        nonlocal calls
        calls += 1

    run_rule_pipeline(
        "item_1",
        chunks,
        extractor=_ChunkAwareExtractor(),
        batch_size=1,
        cancellation_check=cancellation_check,
    )

    assert calls == 3


def test_rule_pipeline_propagates_cancellation():
    chunks = [
        EvidenceChunk(chunk_id="chunk_001", title="摘要", content="主题：2 型糖尿病"),
        EvidenceChunk(chunk_id="chunk_002", title="目的", content="用于治疗管理"),
    ]

    def cancellation_check():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        run_rule_pipeline(
            "item_1",
            chunks,
            extractor=_ChunkAwareExtractor(),
            batch_size=1,
            cancellation_check=cancellation_check,
        )


class _FakeLLMClient:
    def __init__(self, payload: dict | list[dict | Exception]):
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.prompts: list[tuple[str, str]] = []

    def complete_json(self, *, stage: str, system_prompt: str, user_prompt: str, max_tokens: int, **_: object) -> dict:
        self.prompts.append((stage, user_prompt))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


def _extract_with_payload(
    payload: dict, content: str = "本指南针对 2 型糖尿病患者的治疗管理。",
) -> ExtractionBatchResult:
    extractor = LLMObjectExtractor(_FakeLLMClient(payload))
    return extractor.extract(get_item_spec("item_1"), [
        EvidenceChunk(chunk_id="chunk_001", title="目的", content=content)
    ])


_BAD_OBJECT_CASES = [
        ({"type": "unknown", "value": "x", "chunk_id": "chunk_001", "quote": "x"}, "unknown_object_type"),
        ({"type": "health_topic", "value": "2 型糖尿病", "chunk_id": "chunk_001", "quote": ""}, "missing_quote"),
        ({"type": "health_topic", "value": "2 型糖尿病", "chunk_id": "", "quote": "2 型糖尿病"}, "missing_chunk_id"),
        ({"type": "health_topic", "value": "", "chunk_id": "chunk_001", "quote": "2 型糖尿病"}, "missing_value"),
        ({"type": "health_topic", "value": "diabetes", "chunk_id": "chunk_001", "quote": "2 型糖尿病"}, "value_not_found_in_quote"),
        ({"type": "health_topic", "value": "2 型糖尿病", "chunk_id": "chunk_001", "quote": "2 型糖尿病", "confidence": "bad"}, "invalid_confidence"),
        ({"type": "health_topic", "value": "2 型糖尿病", "chunk_id": "chunk_001", "quote": "2 型糖尿病", "confidence": 1.2}, "invalid_confidence"),
        ({"type": "health_topic", "value": "2 型糖尿病", "chunk_id": "chunk_001", "quote": "2 型糖尿病", "attributes": []}, "invalid_attributes"),
        ("not an object", "invalid_object_shape"),
]


def _payload_with_bad_object(bad_object) -> dict:
    return {
        "objects": [
            bad_object,
            {
                "type": "health_topic",
                "value": "2 型糖尿病",
                "chunk_id": "chunk_001",
                "quote": "2 型糖尿病",
                "confidence": 0.95,
                "attributes": {},
            },
        ]
    }


def _chunk_001():
    return EvidenceChunk(chunk_id="chunk_001", title="目的", content="本指南针对 2 型糖尿病患者的治疗管理。")


@pytest.mark.parametrize(("bad_object", "expected_code"), _BAD_OBJECT_CASES)
def test_llm_extractor_strict_policy_rejects_bad_object(bad_object, expected_code):
    payload = _payload_with_bad_object(bad_object)
    extractor = LLMObjectExtractor(_FakeLLMClient([payload, payload]), policy=StrictAllOrNothing())
    with pytest.raises(ValueError, match=expected_code):
        extractor.extract(get_item_spec("item_1"), [_chunk_001()])


@pytest.mark.parametrize(("bad_object", "expected_code"), _BAD_OBJECT_CASES)
def test_llm_extractor_default_policy_keeps_valid_objects(bad_object, expected_code):
    payload = _payload_with_bad_object(bad_object)
    client = _FakeLLMClient([payload, payload])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(get_item_spec("item_1"), [_chunk_001()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert expected_code in {reason.code for reason in result.review_reasons}
    if isinstance(bad_object, dict):
        assert bad_object in result.dropped
    assert [stage for stage, _ in client.prompts] == [
        "RuleObjectExtraction:item_1",
        "RuleObjectExtractionRepair:item_1",
    ]


def test_llm_extractor_accepts_value_with_normalized_whitespace_in_quote():
    result = _extract_with_payload(
        {
            "objects": [
                {
                    "type": "health_topic",
                    "value": "type 2 diabetes",
                    "chunk_id": "chunk_001",
                    "quote": "type 2\n diabetes",
                    "confidence": 0.95,
                    "attributes": {},
                }
            ]
        },
        content="This guideline addresses type 2\n diabetes.",
    )

    assert [obj.value for obj in result.objects] == ["type 2 diabetes"]
    assert result.review_reasons == []


def test_llm_extractor_accepts_value_with_formatting_normalization_in_quote():
    result = _extract_with_payload(
        {
            "objects": [
                {
                    "type": "health_topic",
                    "value": "patient's type-2 diabetes",
                    "chunk_id": "chunk_001",
                    "quote": "patient’s&nbsp;type\u20112 diabetes",
                    "confidence": 0.95,
                    "attributes": {},
                }
            ]
        },
        content="This guideline addresses patient’s type\u20112 diabetes.",
    )

    assert [obj.value for obj in result.objects] == ["patient's type-2 diabetes"]
    assert result.review_reasons == []


def test_llm_extractor_rejects_invalid_top_level_objects_field():
    client = _FakeLLMClient([{"objects": {}}, {"objects": {}}])
    extractor = LLMObjectExtractor(client)

    with pytest.raises(ValueError, match="objects"):
        extractor.extract(get_item_spec("item_1"), [
            EvidenceChunk(chunk_id="chunk_001", title="目的", content="本指南针对 2 型糖尿病患者的治疗管理。")
        ])

    assert [stage for stage, _ in client.prompts] == [
        "RuleObjectExtraction:item_1",
        "RuleObjectExtractionRepair:item_1",
    ]


def test_llm_extractor_repairs_payload_with_invalid_objects_shape():
    client = _FakeLLMClient([
        {"objects": {}},
        {
            "objects": [
                {
                    "type": "health_topic",
                    "value": "2 型糖尿病",
                    "chunk_id": "chunk_001",
                    "quote": "2 型糖尿病",
                    "confidence": 0.95,
                    "attributes": {},
                }
            ]
        },
    ])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(
        get_item_spec("item_1"),
        [EvidenceChunk(chunk_id="chunk_001", title="目的", content="本指南针对 2 型糖尿病患者。")],
    )

    assert [obj.type for obj in result.objects] == ["health_topic"]
    assert result.review_reasons == []
    assert [stage for stage, _ in client.prompts] == [
        "RuleObjectExtraction:item_1",
        "RuleObjectExtractionRepair:item_1",
    ]
    assert "# Extraction Repair" in client.prompts[1][1]
    assert "objects" in client.prompts[1][1]


def test_llm_extractor_retries_when_objects_field_is_missing():
    client = _FakeLLMClient([
        {"result": []},
        {"objects": []},
    ])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(
        get_item_spec("item_1"),
        [EvidenceChunk(chunk_id="chunk_001", title="目的", content="本指南针对 2 型糖尿病患者。")],
    )

    assert result.objects == []
    assert [reason.code for reason in result.review_reasons] == ["extraction_empty_result"]
    assert [stage for stage, _ in client.prompts] == [
        "RuleObjectExtraction:item_1",
        "RuleObjectExtractionRepair:item_1",
    ]


def test_store_excludes_quote_not_found_in_chunk():
    spec = get_item_spec("item_1")
    store = ObjectStore(spec)
    store.merge(
        [
            _rule_object("health_topic", "2 型糖尿病", quote="2 型糖尿病"),
            _rule_object("target_population", "成人患者", quote="不存在的原文"),
        ],
        processed_chunks=["chunk_001"],
        chunk_text_by_id={"chunk_001": "本指南针对 2 型糖尿病。"},
    )

    snapshot = store.snapshot()
    result = get_rule_engine(spec.rule_profile).execute(spec, snapshot)

    assert result.score_range == "2-3"
    assert snapshot.has_object("target_population") is False
    assert result.needs_review is True
    assert [reason.code for reason in result.review_reasons] == ["quote_not_found_in_chunk"]


def test_store_accepts_quote_with_formatting_normalization():
    spec = get_item_spec("item_1")
    store = ObjectStore(spec)
    store.merge(
        [
            _rule_object(
                "health_topic",
                "patient's type-2 diabetes",
                quote="patient's type-2 diabetes",
            ),
        ],
        processed_chunks=["chunk_001"],
        chunk_text_by_id={"chunk_001": "This guideline addresses patient’s&nbsp;type\u20112 diabetes."},
    )

    snapshot = store.snapshot()

    assert snapshot.has_object("health_topic") is True
    assert snapshot.review_reasons == ()


def test_store_limits_objects_by_type_with_stable_representative_sorting():
    spec = ItemSpec(
        item_id="item_limit",
        title="Limit",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(type="A", role="component", description="A", max_objects=2),
        },
    )
    store = ObjectStore(spec)
    store.merge(
        [
            _rule_object("A", "late high", chunk_id="chunk_002", quote="late high", confidence=0.95),
            _rule_object("A", "early high short", chunk_id="chunk_001", quote="early high", confidence=0.95),
            _rule_object("A", "early high long", chunk_id="chunk_001", quote="early high with a longer quote", confidence=0.95),
            _rule_object("A", "higher confidence", chunk_id="chunk_003", quote="higher confidence", confidence=0.99),
        ],
        processed_chunks=["chunk_001", "chunk_002", "chunk_003"],
        chunk_text_by_id={
            "chunk_001": "early high early high with a longer quote",
            "chunk_002": "late high",
            "chunk_003": "higher confidence",
        },
    )

    snapshot = store.snapshot()

    assert [obj.value for obj in snapshot.objects] == ["higher confidence", "early high short"]
    assert [reason.code for reason in snapshot.review_reasons] == ["object_limit_exceeded"]
    assert snapshot.review_reasons[0].object_type == "A"


class _MixedQualityExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        return ExtractionBatchResult(
            objects=[
                _rule_object("health_topic", "2 型糖尿病", chunk_id="chunk_001", quote="2 型糖尿病"),
                _rule_object("target_population", "成人患者", chunk_id="chunk_001", quote="成人患者"),
            ],
            review_reasons=[
                ReviewReason(
                    code="unknown_object_type",
                    message="bad object skipped",
                    object_type="unknown",
                )
            ],
        )


def test_pipeline_keeps_good_objects_when_bad_object_review_reason_exists():
    result = run_rule_pipeline(
        "item_1",
        [
            EvidenceChunk(
                chunk_id="chunk_001",
                title="目的",
                content="本指南针对 2 型糖尿病成人患者。",
            )
        ],
        extractor=_MixedQualityExtractor(),
    )

    assert result.rule_result.score_range == "4-5"
    assert result.rule_result.needs_review is True
    assert [reason.code for reason in result.rule_result.review_reasons] == ["unknown_object_type"]


class _LimitExtractor:
    def extract(self, spec: ItemSpec, chunks: list[EvidenceChunk], lang: str = "zh") -> ExtractionBatchResult:
        return ExtractionBatchResult(
            objects=[
                RuleObject(
                    type="A",
                    value=f"value {index}",
                    evidence=EvidenceRef(chunk_id="chunk_001", quote=f"value {index}"),
                    confidence=0.95,
                )
                for index in range(3)
            ]
        )


def test_pipeline_result_objects_use_limited_snapshot_objects():
    spec = ItemSpec(
        item_id="item_limit_pipeline",
        title="Limit pipeline",
        rule_profile="weighted_checklist",
        object_specs={
            "A": RuleObjectSpec(type="A", role="component", description="A", max_objects=1),
        },
        rule=WeightedChecklistRule(items=(WeightedChecklistItem(fact="A"),)),
    )

    from core.rule_engine import registry

    registry.ITEM_SPECS[spec.item_id] = spec
    try:
        result = run_rule_pipeline(
            spec.item_id,
            [EvidenceChunk(chunk_id="chunk_001", title="Limit", content="value 0 value 1 value 2")],
            extractor=_LimitExtractor(),
        )
    finally:
        registry.ITEM_SPECS.pop(spec.item_id, None)

    assert len(result.snapshot.objects) == 1
    assert len(result.objects) == 1
    assert [reason.code for reason in result.snapshot.review_reasons] == ["object_limit_exceeded"]


def test_extraction_prompt_example_uses_items_first_allowed_type():
    from core.rule_engine.registry import get_item_spec

    chunk = EvidenceChunk(chunk_id="chunk_001", title="范围", content="本指南适用于 2 型糖尿病成人患者。")
    item1_prompt = render_extraction_prompt(get_item_spec("item_1"), [chunk])
    assert '"type": "health_topic"' in item1_prompt

    item7_prompt = render_extraction_prompt(get_item_spec("item_7"), [chunk])
    assert '"type": "search_activity"' in item7_prompt
    assert '"type": "health_topic"' not in item7_prompt
