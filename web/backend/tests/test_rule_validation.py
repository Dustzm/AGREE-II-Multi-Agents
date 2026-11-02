"""Phase-1 reliability tests: local JSON repair, validation policies and the extractor repair ladder."""
from __future__ import annotations

import pytest

from core.llm.failures import FailureContext, FailureKind, classify
from core.llm.json_output import InvalidModelJSON, TruncatedModelOutput, parse_json_object
from core.llm.json_repair import repair_json_text
from core.llm.repair import RepairPlanner, RepairStep
from core.rule_engine.extractors.llm import ExtractionPayloadShapeError, LLMObjectExtractor
from core.rule_engine.models import EvidenceChunk
from core.rule_engine.registry import get_item_spec
from core.rule_engine.validation import StrictAllOrNothing, build_batch_policy, validate_batch


def _chunk() -> EvidenceChunk:
    return EvidenceChunk(chunk_id="chunk_001", title="目的", content="本指南针对 2 型糖尿病患者的治疗管理。")


VALID_OBJECT = {
    "type": "health_topic",
    "value": "2 型糖尿病",
    "chunk_id": "chunk_001",
    "quote": "本指南针对 2 型糖尿病患者的治疗管理。",
    "confidence": 0.95,
    "attributes": {},
}

BAD_SPAN_OBJECT = {
    "type": "health_topic",
    "value": "糖尿病的治疗",
    "chunk_id": "chunk_001",
    "quote": "本指南针对 2 型糖尿病患者的治疗管理。",
    "confidence": 0.9,
    "attributes": {},
}


# ---------------------------------------------------------------- json repair

@pytest.mark.parametrize(
    ("raw", "expected_objects"),
    [
        ('{"objects": []}', 0),
        ('```json\n{"objects": [{"a": 1}]}\n```', 1),
        ('好的，结果如下：{"objects": [{"a": 1}]}', 1),
        ('{"objects": [{"a": 1},]}', 1),
        ('{"objects": [{"a": 1}, {"b": 2}', 2),
        ('{"objects": [{"note": "C:\\path\\x"}]}', 1),
        ('{"objects": [{"note": "line1\nline2"}]}', 1),
    ],
)
def test_repair_json_text_salvages_common_model_mistakes(raw, expected_objects):
    result = repair_json_text(raw)
    assert result.value is not None
    assert len(result.value["objects"]) == expected_objects


def test_repair_json_text_reports_diagnostic_instead_of_guessing():
    result = repair_json_text('{"objects": [{"value": ')
    assert result.value is None
    assert result.error is not None


# ---------------------------------------------------------------- validation

def test_strict_policy_rejects_batch_and_keep_valid_keeps_objects():
    spec = get_item_spec("item_1")
    payload = [BAD_SPAN_OBJECT, VALID_OBJECT]

    strict_outcome_rejected = False
    try:
        validate_batch(spec, payload, StrictAllOrNothing())
    except ValueError as exc:
        strict_outcome_rejected = "value_not_found_in_quote" in str(exc)
    assert strict_outcome_rejected

    outcome = validate_batch(spec, payload, build_batch_policy("keep_valid"))
    assert [obj.value for obj in outcome.objects] == ["2 型糖尿病"]
    assert {issue.code for issue in outcome.issues} == {"value_not_found_in_quote"}
    assert outcome.dropped == (BAD_SPAN_OBJECT,)


def test_classify_maps_failure_kinds():
    assert classify(TruncatedModelOutput("Model output truncated")) is FailureKind.TRUNCATED
    assert classify(InvalidModelJSON("bad json")) is FailureKind.JSON_SYNTAX


def test_repair_planner_never_resends_for_truncation():
    planner = RepairPlanner(max_resends=1)
    plan = planner.plan(FailureContext(kind=FailureKind.TRUNCATED, stage="s"))
    assert RepairStep.RESEND_WITH_DIAGNOSIS not in plan.steps
    plan = planner.plan(FailureContext(kind=FailureKind.SEMANTIC_SPAN, stage="s"))
    assert RepairStep.RESEND_WITH_DIAGNOSIS in plan.steps


# ---------------------------------------------------------------- repair ladder


class _ScriptedClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, *, stage, system_prompt, user_prompt, max_tokens, **_: object):
        self.calls.append((stage, user_prompt))
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_extractor_repair_fixes_span_mismatch():
    valid_payload = {"objects": [VALID_OBJECT]}
    client = _ScriptedClient([{"objects": [BAD_SPAN_OBJECT]}, valid_payload])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert [stage for stage, _ in client.calls] == [
        "RuleObjectExtraction:item_1",
        "RuleObjectExtractionRepair:item_1",
    ]
    # The repair prompt echoes the concrete problem instead of re-asking blindly.
    repair_prompt = client.calls[1][1]
    assert "value_not_found_in_quote" in repair_prompt
    assert "Invalid Objects" in repair_prompt


def test_extractor_keeps_valid_objects_when_repair_fails():
    client = _ScriptedClient([{"objects": [BAD_SPAN_OBJECT, VALID_OBJECT]}, InvalidModelJSON("boom")])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert "extraction_repair_failed" in {reason.code for reason in result.review_reasons}


def test_extractor_keeps_valid_objects_when_repair_crashes():
    client = _ScriptedClient(
        [{"objects": [BAD_SPAN_OBJECT, VALID_OBJECT]}, RuntimeError("transport died")]
    )
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert "extraction_repair_failed" in {reason.code for reason in result.review_reasons}


def test_extractor_retries_repair_up_to_configured_attempts():
    client = _ScriptedClient([
        {"objects": [BAD_SPAN_OBJECT]},
        {"objects": [BAD_SPAN_OBJECT]},
        {"objects": [VALID_OBJECT]},
    ])
    extractor = LLMObjectExtractor(client, max_resends=2)

    result = extractor.extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert [stage for stage, _ in client.calls] == [
        "RuleObjectExtraction:item_1",
        "RuleObjectExtractionRepair:item_1",
        "RuleObjectExtractionRepair:item_1#2",
    ]


def test_extractor_propagates_truncation_for_batch_split():
    client = _ScriptedClient([TruncatedModelOutput("Model output truncated")])
    extractor = LLMObjectExtractor(client)

    with pytest.raises(TruncatedModelOutput):
        # split_on_overflow owns the split-then-retry loop, so the single-batch
        # call must surface truncation instead of swallowing it.
        extractor._extract_once(get_item_spec("item_1"), [_chunk()])


# ------------------------------------------------- quota / context hardening


class _QuotaClient(_ScriptedClient):
    """Scripted client whose shared recovery ledger can already be exhausted."""

    def __init__(self, payloads, *, repair_quota: int = 0):
        super().__init__(payloads)
        from core.llm.recovery import LedgerQuotas, RecoveryBudget

        self.recovery = RecoveryBudget(
            quotas=LedgerQuotas(transport=3, truncation=3, repair=repair_quota, total=8)
        )


def test_classify_walks_mro_and_keeps_model_errors_degradable():
    from core.llm.context import ContextBudgetExceeded, ContextWindowNotConfigured

    class SubclassJSON(InvalidModelJSON):
        pass

    class SubclassBudget(ContextBudgetExceeded):
        pass

    assert classify(SubclassJSON("bad json")) is FailureKind.JSON_SYNTAX
    assert classify(SubclassBudget("too big")) is FailureKind.BUDGET_EXCEEDED
    assert classify(ContextWindowNotConfigured("unset")) is FailureKind.BUDGET_EXCEEDED
    assert classify(InvalidModelJSON("Repair:item_1: repair allowance exhausted")) is FailureKind.BUDGET_EXCEEDED
    assert classify(ValueError("Verifier JSON field 'decisions' must be a list")) is FailureKind.SCHEMA_VIOLATION


def test_extractor_keeps_valid_objects_when_repair_quota_is_exhausted():
    client = _QuotaClient([{"objects": [BAD_SPAN_OBJECT, VALID_OBJECT]}])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert "extraction_repair_failed" in {reason.code for reason in result.review_reasons}
    assert len(client.calls) == 1  # no repair request was issued


def test_extractor_raises_original_error_when_quota_is_used_up_without_base():
    client = _QuotaClient([{"objects": "not-a-list"}])
    extractor = LLMObjectExtractor(client)

    with pytest.raises(ExtractionPayloadShapeError):
        extractor.extract(get_item_spec("item_1"), [_chunk()])


def test_extractor_keeps_valid_objects_when_repair_overflows_context():
    from core.llm.context import ContextBudgetExceeded

    client = _ScriptedClient([
        {"objects": [BAD_SPAN_OBJECT, VALID_OBJECT]},
        ContextBudgetExceeded("repair prompt too large"),
    ])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert "extraction_repair_failed" in {reason.code for reason in result.review_reasons}


# ------------------------------------------------- fullwidth + lossy salvage

@pytest.mark.parametrize(
    ("raw", "quote"),
    [
        ('｛"quote": "推荐意见：强烈推荐"｝', "推荐意见：强烈推荐"),
        ('｛"quote": "他说“好”，然后走了"｝', "他说“好”，然后走了"),
        ('{"quote": "推荐意见：强烈推荐", "x": 1} trailing ［不相关］', "推荐意见：强烈推荐"),
    ],
)
def test_repair_preserves_fullwidth_punctuation_inside_strings(raw, quote):
    result = repair_json_text(raw)
    assert result.value is not None
    assert result.value["quote"] == quote


def test_repair_skips_prose_citations_before_the_object():
    result = repair_json_text("依据 [1] 与 [2, 3]，结果如下：{\"objects\": []}")
    assert result.value == {"objects": []}


def test_repair_ignores_citation_immediately_before_payload():
    result = repair_json_text('依据 [1]\n{"objects": [{"type": "x"}]}')
    assert result.value == {"objects": [{"type": "x"}]}


def test_repair_prefers_the_trailing_payload_over_an_example():
    result = repair_json_text(
        '例如输出格式 {"objects": []}，实际结果：{"objects": [{"a": 1}]}'
    )
    assert result.value == {"objects": [{"a": 1}]}


def test_repair_does_not_turn_citations_into_an_empty_payload():
    result = repair_json_text("依据 [1] 与 [2, 3] 但没有答案")
    assert result.value is None


def test_repair_marks_lossy_salvage_and_parse_refuses_it():
    result = repair_json_text('{"score": 4, "evidence": ["a", "b')
    assert result.value is not None
    assert result.lossy is True
    assert "salvage_truncated:lossy" in result.repairs

    from core.llm.json_output import TruncatedModelOutput

    with pytest.raises(TruncatedModelOutput):
        parse_json_object('{"score": 4, "evidence": ["a", "b')


def test_parse_accepts_lossless_salvage():
    assert parse_json_object('{"objects": [{"a": 1}, {"b": 2}') == {
        "objects": [{"a": 1}, {"b": 2}],
    }


def test_repair_keeps_escaped_quote_inside_string():
    result = repair_json_text('{"a": "x\\"y\\q"}')
    assert result.value == {"a": 'x"y\\q'}


def test_empty_chunks_do_not_send_llm_request():
    class NoCallClient:
        calls = 0

        def complete_json(self, **_kwargs):
            NoCallClient.calls += 1
            raise AssertionError("must not call the model without evidence")

    extractor = LLMObjectExtractor(NoCallClient())
    result = extractor.extract(get_item_spec("item_1"), [])
    assert result.objects == []
    assert NoCallClient.calls == 0


def test_repair_prompt_keeps_chunk_label_marker():
    from core.llm.failures import FailureContext, FailureKind
    from core.rule_engine.prompts.repair import render_object_repair_prompt

    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="目的",
        content="本指南针对 2 型糖尿病患者的治疗管理。",
        metadata={"label": "Overview"},
    )
    prompt = render_object_repair_prompt(
        get_item_spec("item_1"),
        [chunk],
        FailureContext(kind=FailureKind.JSON_SYNTAX, stage="RuleObjectExtraction:item_1", error_text="bad"),
    )
    assert "[chunk_001][Overview]" in prompt


# ------------------------------------------------- stage quotas + quote re-anchoring


def test_repair_quota_is_capped_per_stage():
    from core.llm.failures import FailureKind
    from core.llm.recovery import LedgerQuotas, RecoveryLedger

    ledger = RecoveryLedger(quotas=LedgerQuotas(repair=6, repair_stage=1, total=8))
    ledger.reserve(FailureKind.SEMANTIC_SPAN, "RuleObjectExtractionRepair:item_2")
    with pytest.raises(InvalidModelJSON, match="allowance exhausted"):
        ledger.reserve(FailureKind.SEMANTIC_SPAN, "RuleObjectExtractionRepair:item_2#2")
    # Other stages keep their own allowance instead of being starved by extraction.
    ledger.reserve(FailureKind.SEMANTIC_SPAN, "RuleExplanationRetry:item_2")
    assert ledger.used(FailureKind.SEMANTIC_SPAN) == 2


def test_extractor_reanchors_paraphrased_quote_without_repair():
    chunk = EvidenceChunk(
        chunk_id="chunk_001",
        title="recommendation",
        content=(
            "The guideline is relevant to people at high risk of developing Type 2 diabetes. "
            "It also covers people living with Type 1 and Type 2 diabetes."
        ),
    )
    payload = {
        "objects": [
            {
                "type": "health_topic",
                "value": "people at high risk of developing Type 2 diabetes",
                "chunk_id": "chunk_001",
                "quote": "The guideline covers people at high risk of Type 2 diabetes.",
                "confidence": 0.9,
                "attributes": {},
            }
        ]
    }
    client = _ScriptedClient([payload])
    extractor = LLMObjectExtractor(client)

    result = extractor.extract(get_item_spec("item_1"), [chunk])

    assert [obj.value for obj in result.objects] == ["people at high risk of developing Type 2 diabetes"]
    assert len(client.calls) == 1  # re-anchored locally, no repair request
    assert result.objects[0].evidence.quote.startswith("The guideline is relevant")


def test_source_span_normalization_handles_pdf_artifacts():
    from core.rule_engine.text_normalization import (
        contains_source_span,
        find_source_span,
        normalize_text_for_source_match,
    )

    assert contains_source_span("The nutri-\nents are important", "the nutrients are important")
    assert contains_source_span("- nutrient\n1. content", "nutrient content")
    assert normalize_text_for_source_match("zero\u200bwidth") == "zerowidth"
    container = "Recommendations were graded. First line of body. Second line of body."
    assert find_source_span(container, "First line of body") == "First line of body."


# ------------------------------------------------- review follow-up regressions


def test_list_quotes_joined_on_one_line_still_match_source():
    from core.rule_engine.text_normalization import contains_source_span

    assert contains_source_span("1. 每日一次口服\n2. 餐后服用", "1. 每日一次口服 2. 餐后服用")
    assert contains_source_span("剂量\n2) 每日一次", "剂量 2) 每日一次")
    assert contains_source_span("- 推荐A\n- 推荐B", "推荐A 推荐B")
    # Sentence-final numbers are content, not list markers.
    assert contains_source_span("Patients aged 65. Others were excluded.", "aged 65")


def test_specific_value_is_reanchored_without_a_repair_call():
    paraphrased = {**VALID_OBJECT, "value": "2 型糖尿病患者", "quote": "指南关注糖尿病人群"}
    client = _ScriptedClient([{"objects": [paraphrased]}])

    result = LLMObjectExtractor(client).extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.evidence.quote for obj in result.objects] == ["本指南针对 2 型糖尿病患者的治疗管理。"]
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("content", "value"),
    [
        ("本指南针对 2 型糖尿病患者的治疗管理。", "糖尿"),  # too short to pin down
        ("糖尿病患者应筛查。糖尿病患者应随访。", "糖尿病患者"),  # occurs twice
    ],
)
def test_ambiguous_values_are_not_reanchored(content, value):
    from core.rule_engine.validation import reanchor_quote

    assert reanchor_quote(content, value) is None


def test_reanchored_quote_never_spans_more_than_one_unit():
    from core.rule_engine.validation.reanchor import MAX_QUOTE_CHARS, reanchor_quote

    long_line = "甲" * (MAX_QUOTE_CHARS + 10) + "唯一的推荐内容" + "乙" * 10
    assert reanchor_quote(long_line, "唯一的推荐内容") is None


def test_successful_repair_drops_stale_validation_issues():
    fixed = {**VALID_OBJECT, "value": "治疗管理"}
    client = _ScriptedClient([{"objects": [BAD_SPAN_OBJECT, VALID_OBJECT]}, {"objects": [VALID_OBJECT, fixed]}])

    result = LLMObjectExtractor(client).extract(get_item_spec("item_1"), [_chunk()])

    assert sorted(obj.value for obj in result.objects) == ["2 型糖尿病", "治疗管理"]
    assert "value_not_found_in_quote" not in {reason.code for reason in result.review_reasons}
    assert result.dropped == ()


def test_second_repair_echoes_the_latest_invalid_objects():
    still_bad = {**BAD_SPAN_OBJECT, "value": "糖尿病的长期管理"}
    client = _ScriptedClient([
        {"objects": [BAD_SPAN_OBJECT]},
        {"objects": [still_bad]},
        {"objects": [VALID_OBJECT]},
    ])

    LLMObjectExtractor(client, max_resends=2).extract(get_item_spec("item_1"), [_chunk()])

    assert "糖尿病的长期管理" in client.calls[2][1]


def test_lossy_salvage_of_a_closed_payload_is_a_syntax_error_not_truncation():
    with pytest.raises(InvalidModelJSON) as excinfo:
        parse_json_object('{"a": ["x", "y"z"]}', "stop")
    assert not isinstance(excinfo.value, TruncatedModelOutput)


def test_reported_kind_prefers_live_exception_and_never_raises():
    from core.llm.failures import reported_kind
    from core.observability.appraisal import build_failure_report
    from core.utils.cancellation import CancellationRequested

    class SubclassJSON(InvalidModelJSON):
        pass

    assert reported_kind(SubclassJSON("bad")) == "json_syntax"
    assert reported_kind(CancellationRequested("stop")) == "cancelled"
    assert reported_kind(None, "TruncatedModelOutput", "x") == "truncated"
    report = build_failure_report("t", {
        "failed": {"item_2": "x"},
        "failed_details": {"item_2": {"type": "SubclassJSON", "failure_kind": "json_syntax"}},
    })
    assert report["failed"][0]["kind"] == "json_syntax"


def test_extraction_estimate_counts_the_response_schema():
    from types import SimpleNamespace

    from core.llm.context import RequestBudget
    from core.llm.tokenizer import count_messages_tokens
    from core.rule_engine.extractors.llm import _messages

    spec = get_item_spec("item_1")
    extractor = LLMObjectExtractor(SimpleNamespace(budget=RequestBudget(131072, "m")))

    estimate = extractor._estimate(spec, [_chunk()], "zh")

    assert estimate > count_messages_tokens(_messages(spec, [_chunk()], "zh")) + 64


# ------------------------------------------------ batch-run follow-up regressions


def test_quote_missing_from_chunk_is_reanchored_before_verification():
    stitched = {**VALID_OBJECT, "value": "2 型糖尿病患者", "quote": "本指南针对 2 型糖尿病患者的长期随访。"}
    client = _ScriptedClient([{"objects": [stitched]}])

    result = LLMObjectExtractor(client).extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.evidence.quote for obj in result.objects] == ["本指南针对 2 型糖尿病患者的治疗管理。"]
    assert len(client.calls) == 1


def test_unanchorable_quote_is_sent_to_repair_with_its_diagnosis():
    invented = {**VALID_OBJECT, "value": "长期随访", "quote": "本指南建议长期随访。"}
    client = _ScriptedClient([{"objects": [invented, VALID_OBJECT]}, {"objects": [VALID_OBJECT]}])

    result = LLMObjectExtractor(client).extract(get_item_spec("item_1"), [_chunk()])

    assert [obj.value for obj in result.objects] == ["2 型糖尿病"]
    assert "quote_not_found_in_chunk" in client.calls[1][1]


def test_quote_citing_a_chunk_outside_the_batch_is_rejected():
    outcome = validate_batch(
        get_item_spec("item_1"),
        [{**VALID_OBJECT, "chunk_id": "chunk_404"}],
        build_batch_policy("keep_valid"),
        sources={"chunk_001": _chunk().source_text()},
    )

    assert outcome.objects == []
    assert [issue.code for issue in outcome.issues] == ["unknown_quote_chunk"]


def test_inline_latex_symbols_match_unicode_quotes():
    from core.rule_engine.text_normalization import contains_source_span

    assert contains_source_span(
        "the receptor-  $\\alpha$  agonist fenofibrate", "receptor- α agonist fenofibrate",
    )
    assert contains_source_span("triglycerides  $\\geq$  150 mg/dL", "triglycerides ≥150 mg/dL")
    assert contains_source_span("BMI 30 kg/m  $^{2}$  or higher", "BMI 30 kg/m² or higher")
    assert contains_source_span("vitamin  $B_{12}$  deficiency", "vitamin B12 deficiency")
    assert not contains_source_span("receptor  $\\beta$  agonist", "receptor α agonist")


def test_request_limits_fit_one_response_without_changing_item_limits():
    from core.rule_engine.prompts.extraction import (
        TARGET_OUTPUT_TOKENS,
        TOKENS_PER_OBJECT,
        render_extraction_prompt,
        request_object_limits,
    )

    spec = get_item_spec("item_12")
    limits = request_object_limits(spec)

    assert sum(limits.values()) * TOKENS_PER_OBJECT <= TARGET_OUTPUT_TOKENS
    assert min(limits.values()) >= 1
    assert spec.object_specs["recommendation_statement"].max_objects == 10
    assert f"at most {limits['recommendation_statement']} representative" in render_extraction_prompt(
        spec, [_chunk()],
    )
    small = get_item_spec("item_4")
    assert request_object_limits(small) == {
        obj_type: obj_spec.max_objects for obj_type, obj_spec in small.object_specs.items()
    }
