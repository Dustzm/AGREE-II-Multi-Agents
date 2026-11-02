"""Offline contracts for model limits, split coverage, JSON recovery and task isolation."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.config import model_catalog, runtime_store
from core.config.settings import LLMSettings, _apply_runtime_llm_overrides
from core.llm import context
from core.llm.context import ContextBudgetExceeded, RequestBudget
from core.llm.json_output import parse_json_object, TruncatedModelOutput
from core.llm.failures import FailureKind
from core.llm.recovery import RecoveryBudget, cancellable_delay
from core.rule_engine.litellm_client import LiteLLMRuleClient
from core.rule_engine.models import EvidenceChunk, ExtractionBatchResult
from core.rule_engine.extractors.llm import LLMObjectExtractor
from core.rule_engine.registry import get_item_spec
from core.utils.cancellation import CancellationToken, CancellationRequested
from core.utils.llm_errors import InvalidModelJSON


def response(value, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)), finish_reason=finish)], usage=None)


def test_catalog_exact_identity_and_ambiguity(monkeypatch):
    monkeypatch.setattr(model_catalog, "load_catalog", lambda: {
        "source": "models.dev", "snapshot_at": "2026-09-28", "models": [
            {"provider": "openai", "id": "same", "context": 128000},
            {"provider": "other", "id": "same", "context": 32000},
        ]})
    assert model_catalog.lookup_model_limits("openai", "same")["context_window"] == 128000
    assert model_catalog.lookup_model_limits("vllm", "same")["status"] == "ambiguous"
    assert model_catalog.lookup_model_limits("openai", "same-quantized")["status"] == "unknown"


@pytest.mark.parametrize("legacy_window", ["missing", None, 64000])
def test_runtime_schema_upgrade_and_null(tmp_path, monkeypatch, legacy_window):
    monkeypatch.setattr(runtime_store, "DB_DIR", tmp_path)
    monkeypatch.setattr(runtime_store, "DB_PATH", tmp_path / "runtime.db")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "131072")
    schema = runtime_store._CREATE_LLM_RUNTIME_CONFIG_TABLE.replace(
        "    context_window_overridden INTEGER NOT NULL DEFAULT 0,\n", ""
    )
    if legacy_window == "missing":
        schema = schema.replace("    context_window    INTEGER,\n", "")
    with sqlite3.connect(runtime_store.DB_PATH) as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO llm_runtime_config (id, timeout) VALUES (1, 120)")
        if legacy_window != "missing":
            conn.execute("UPDATE llm_runtime_config SET context_window = ?", (legacy_window,))

    def effective_window():
        settings = SimpleNamespace(llm=LLMSettings(_env_file=None), llm_config_secret="test")
        _apply_runtime_llm_overrides(settings)
        return settings.llm.context_window

    expected = legacy_window if isinstance(legacy_window, int) else 131072
    assert effective_window() == expected
    runtime_store.upsert_runtime_llm_config({"timeout": 10}, secret="test")
    assert effective_window() == expected
    runtime_store.upsert_runtime_llm_config({"model_name": "m", "context_window": 131072}, secret="test")
    runtime_store.upsert_runtime_llm_config({"timeout": 10}, secret="test")
    assert runtime_store.get_runtime_llm_config("test")["context_window"] == 131072
    runtime_store.upsert_runtime_llm_config({"context_window": None}, secret="test")
    assert runtime_store.get_runtime_llm_config("test")["context_window"] is None
    assert effective_window() is None
    runtime_store.upsert_runtime_llm_config({"timeout": 20}, secret="test")
    assert effective_window() is None
    with pytest.raises(ValueError):
        runtime_store.upsert_runtime_llm_config({"context_window": True}, secret="test")


def test_config_api_persists_manual_limit_and_clears_on_identity_change(tmp_path, monkeypatch):
    from web.backend.app.api.routes import config
    from core.agents.base import model_config
    from web.backend.app.permissions import require_admin
    monkeypatch.setattr(runtime_store, "DB_DIR", tmp_path)
    monkeypatch.setattr(runtime_store, "DB_PATH", tmp_path / "runtime.db")
    llm = LLMSettings(_env_file=None, provider="openai", model_name="gpt-4o", base_url="", context_window=None)
    settings = SimpleNamespace(llm=llm, llm_config_secret="test")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    def refresh():
        _apply_runtime_llm_overrides(settings)
        return settings
    monkeypatch.setattr(config, "refresh_settings", refresh)
    monkeypatch.setattr(model_config, "refresh_model", lambda: None)
    app = FastAPI()
    app.include_router(config.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(username="test")
    with TestClient(app) as client:
        for invalid in (0, -1, 1.5, True, "12000"):
            assert client.put("/config/llm", json={"context_window": invalid}).status_code == 422
        assert client.put("/config/llm", json={"context_window": 64000}).json()["context_window"] == 64000
        assert client.get("/config/llm/model-limits", params={"provider": "openai", "model_id": "gpt-4o"}).json()["status"] == "matched"
        assert client.get("/config/llm").json()["context_window"] == 64000
        assert client.put("/config/llm", json={"model_name": "new"}).json()["context_window"] is None
        assert client.put("/config/llm", json={"model_name": "other", "context_window": 32000}).json()["context_window"] == 32000


def test_budget_includes_schema_output_and_unknown_images():
    budget = RequestBudget(1000, "unknown-model")
    budget.check([{"role": "user", "content": "x"}], 100)
    with pytest.raises(ContextBudgetExceeded):
        budget.check([{"role": "user", "content": "x"}], 100, extra={"schema": " ".join(f"field_{i}: string" for i in range(200))})
    with pytest.raises(ContextBudgetExceeded):
        budget.check([], 900)
    image = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 100000}}]
    assert budget.estimate(image) < 20000
    with pytest.raises(ContextBudgetExceeded):
        RequestBudget(None, "m").check([], 1)


def test_json_safe_normalization_and_truncation():
    assert parse_json_object('```json\n{"score":4,"text":",}",}\n```') == {"score": 4, "text": ",}"}
    # The local repair ladder recovers prose prefixes and unterminated objects,
    # but refuses to accept a value the model may not have finished writing.
    assert parse_json_object('prefix {"score":4}') == {"score": 4}
    with pytest.raises(InvalidModelJSON):
        parse_json_object('{"score":4')
    # Empty arrays left by a stray comma are repaired strictly (no invented content).
    assert parse_json_object('{"evidence":[,]}') == {"evidence": []}
    # Ambiguity is still a hard failure: duplicate keys, invalid numbers, non-objects.
    for invalid in ('{"score":1,"score":7}', '{"x":NaN}', '[]'):
        with pytest.raises(InvalidModelJSON):
            parse_json_object(invalid)
    with pytest.raises(TruncatedModelOutput):
        parse_json_object('{"score":4}', "length")


def test_task_snapshot_survives_global_config_changes_and_threads(monkeypatch):
    settings = SimpleNamespace(llm=LLMSettings(_env_file=None, model_name="initial", context_window=32000))
    monkeypatch.setattr(context, "get_settings", lambda: settings)
    token = context.bind_llm_settings()
    try:
        settings.llm.model_name = "changed"
        settings.llm.context_window = 64000
        async def read():
            return await asyncio.to_thread(lambda: (context.current_llm_settings().model_name, context.current_llm_settings().context_window))
        assert asyncio.run(read()) == ("initial", 32000)
    finally:
        context.reset_llm_settings(token)
    assert context.current_llm_settings().model_name == "changed"


def test_rule_missing_decisions_preserves_existing_and_uses_shared_quota(monkeypatch):
    from core.llm import gateway as gateway_module
    from core.llm.gateway import LLMGateway
    calls = []
    outputs = iter([{"decisions": [{"index": 0, "accepted": True}]}, {"decisions": [{"index": 1, "accepted": False}]}])
    def completion(**kwargs):
        calls.append(kwargs)
        return response(next(outputs))
    monkeypatch.setattr(gateway_module.litellm, "completion", completion)
    budget = RecoveryBudget(3)
    client = LiteLLMRuleClient(LLMGateway(model="m", base_url="", api_key="test", timeout=1, provider_kwargs={}, budget=RequestBudget(32000, "m")), recovery=budget)
    schema = {"properties": {"decisions": {"minItems": 2, "maxItems": 2, "items": {"properties": {"index": {"type": "integer"}}}}}}
    result = client.complete_json(stage="verify", system_prompt="system", user_prompt="candidates", max_tokens=100, json_schema=schema)
    assert [d["index"] for d in result["decisions"]] == [0, 1]
    assert "ONLY missing decision indexes: [1]" in calls[1]["messages"][1]["content"]
    assert all(call["num_retries"] == 0 for call in calls)
    # A missing-decision repair spends the repair bucket, not transport retries.
    assert budget.remaining == 3
    assert budget.ledger.used(FailureKind.SEMANTIC_SPAN) == 1
    monkeypatch.setattr(gateway_module.litellm, "completion", lambda **kw: response({"decisions": []}))
    with pytest.raises(InvalidModelJSON, match="repair recovery allowance exhausted"):
        client.complete_json(stage="verify", system_prompt="system", user_prompt="candidates", max_tokens=100, json_schema=schema)
    # The per-stage repair cap stops a single stage from consuming the whole bucket.
    assert budget.ledger.used(FailureKind.SEMANTIC_SPAN) == 2
    assert budget.remaining == 3


def test_extraction_splits_all_source_text_without_spending_retry_budget(monkeypatch):
    quota = RecoveryBudget()
    client = SimpleNamespace(recovery=quota, budget=RequestBudget(131072, "m"))
    extractor = LLMObjectExtractor(client)
    seen = []
    def extract_once(spec, chunks, lang):
        if sum(len(chunk.content) for chunk in chunks) > 12:
            raise ContextBudgetExceeded("too much source")
        seen.extend((chunk.chunk_id, chunk.content) for chunk in chunks)
        return ExtractionBatchResult(objects=[], review_reasons=[])
    monkeypatch.setattr(extractor, "_extract_once", extract_once)
    chunks = [EvidenceChunk("source", "title", "first paragraph\nsecond paragraph\nthird paragraph")]
    extractor.extract(get_item_spec("item_1"), chunks)
    assert ''.join(text for _, text in seen) == chunks[0].content
    assert {source for source, _ in seen} == {"source"}
    assert quota.remaining == 3


def test_parse_json_object_accepts_declared_list_wrapper():
    payload = [{"index": 0, "accepted": True}, {"index": 1, "accepted": False}]
    assert parse_json_object(json.dumps(payload), "stop", list_wrapper="decisions") == {"decisions": payload}
    # Without an explicit wrapper a bare array is still ambiguous and rejected.
    with pytest.raises(InvalidModelJSON):
        parse_json_object(json.dumps(payload), "stop")


def test_rule_client_wraps_bare_decision_array(monkeypatch):
    from core.llm import gateway as gateway_module
    from core.llm.gateway import LLMGateway
    monkeypatch.setattr(
        gateway_module.litellm,
        "completion",
        lambda **kw: response([{"index": 0, "accepted": True}]),
    )
    budget = RecoveryBudget(3)
    client = LiteLLMRuleClient(
        LLMGateway(model="m", base_url="", api_key="test", timeout=1, provider_kwargs={}, budget=RequestBudget(32000, "m")),
        recovery=budget,
    )
    result = client.complete_json(
        stage="verify",
        system_prompt="system",
        user_prompt="candidates",
        max_tokens=100,
        list_wrapper="decisions",
    )
    assert result == {"decisions": [{"index": 0, "accepted": True}]}
    assert budget.ledger.used(FailureKind.SEMANTIC_SPAN) == 0


def test_extraction_truncation_split_does_not_spend_truncation_allowance(monkeypatch):
    from core.llm.recovery import LedgerQuotas
    quota = RecoveryBudget(quotas=LedgerQuotas(transport=3, truncation=0, repair=3, total=8))
    client = SimpleNamespace(recovery=quota, budget=RequestBudget(131072, "m"))
    extractor = LLMObjectExtractor(client)
    seen = []
    def extract_once(spec, chunks, lang):
        if sum(len(chunk.content) for chunk in chunks) > 12:
            raise TruncatedModelOutput("truncated")
        seen.extend((chunk.chunk_id, chunk.content) for chunk in chunks)
        return ExtractionBatchResult(objects=[], review_reasons=[])
    monkeypatch.setattr(extractor, "_extract_once", extract_once)
    chunks = [EvidenceChunk("source", "title", "first paragraph\nsecond paragraph\nthird paragraph")]
    extractor.extract(get_item_spec("item_1"), chunks)
    assert ''.join(text for _, text in seen) == chunks[0].content
    assert quota.ledger.used(FailureKind.TRUNCATED) == 0
    assert quota.remaining == 3


def test_extraction_truncation_split_is_bounded(monkeypatch):
    import core.rule_engine.extractors.llm as extractor_module
    from core.llm.recovery import LedgerQuotas
    monkeypatch.setattr(extractor_module, "_MAX_TRUNCATION_SPLITS", 1)
    quota = RecoveryBudget(quotas=LedgerQuotas(transport=3, truncation=0, repair=3, total=8))
    client = SimpleNamespace(recovery=quota, budget=RequestBudget(131072, "m"))
    extractor = LLMObjectExtractor(client)
    monkeypatch.setattr(
        extractor,
        "_extract_once",
        lambda spec, chunks, lang: (_ for _ in ()).throw(TruncatedModelOutput("truncated")),
    )
    with pytest.raises(TruncatedModelOutput, match="split budget exhausted"):
        extractor.extract(get_item_spec("item_1"), [EvidenceChunk("source", "title", "aaaa\nbbbb\ncccc")])


def test_cancel_stops_backoff_without_another_request():
    token = CancellationToken()
    token.cancel()
    with pytest.raises(CancellationRequested):
        cancellable_delay(30, token.raise_if_cancelled)


def test_item17_empty_evidence_is_not_context_overflow():
    from core.agents.item17_evidence import Item17EvidencePacker
    packer = Item17EvidencePacker(budget=RequestBudget(131072, "m"), evidence=[], chunk_ids=[], chunk_lookup={})
    selection = packer.report_selection(lambda evidence, ids: "empty evidence", output_tokens=1000)
    assert selection.prompt == "empty evidence"
    assert selection.evidence == []


def test_item17_overflow_raises_instead_of_retrying():
    from core.agents.item17_evidence import Item17EvidencePacker
    packer = Item17EvidencePacker(budget=RequestBudget(4000, "m"), evidence=["x" * 50], chunk_ids=["c1"], chunk_lookup={})
    with pytest.raises(ContextBudgetExceeded, match="最终报告"):
        packer.report_selection(lambda evidence, ids: "p" * 7000 + "".join(evidence), output_tokens=1000)


def test_reporter_checks_tool_history_and_finish_reason(monkeypatch):
    from core.agents import reporter
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    monkeypatch.setattr(reporter, "current_llm_settings", lambda: SimpleNamespace(context_window=16000, model_name="unknown", provider="vllm", base_url="http://local"))
    agent = reporter.create_reporter_agent("en")
    long_text = " ".join(f"evidence{i}" for i in range(20000))
    request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text=long_text)])])
    with pytest.raises(ContextBudgetExceeded):
        agent.before_model_callback(None, request)
    with pytest.raises(TruncatedModelOutput):
        agent.after_model_callback(None, LlmResponse(finish_reason=types.FinishReason.MAX_TOKENS))


def test_official_output_caps_never_apply_to_custom_endpoint(monkeypatch):
    monkeypatch.setattr(model_catalog, "lookup_model_limits", lambda provider, model: {"max_input_tokens": 10000, "max_output_tokens": 500})
    llm = LLMSettings(_env_file=None, provider="openai", model_name="m", base_url="", context_window=32000)
    budget = RequestBudget.from_settings(llm, "openai/m")
    assert budget.output_limit(2000) == 500
    with pytest.raises(ContextBudgetExceeded):
        budget.check([{"content": " ".join(f"word{i}" for i in range(12000))}], 100)
    llm.base_url = "https://custom.invalid/v1"
    assert RequestBudget.from_settings(llm, "openai/m").output_limit(2000) == 2000


def test_saved_technical_failure_is_not_reusable(monkeypatch):
    from core.agents.tools import appraiser_tools
    monkeypatch.setattr(appraiser_tools, "_load_skill_config", lambda *a, **kw: {"domain": 1})
    evaluation = {"item_id": "item_1", "domain": 1, "chain_of_thought": {"score": 1, "step2_evidence": [], "step3_reasoning": "reason"},
                  "rule_engine": {"review_reasons": [{"code": "rubric_appraiser_json_failed", "severity": "error"}]}}
    assert not appraiser_tools.validate_evaluation(evaluation, "item_1")
    evaluation["rule_engine"]["review_reasons"] = [{"code": "verification_rejected", "severity": "warning"}]
    assert appraiser_tools.validate_evaluation(evaluation, "item_1")


def test_split_on_overflow_merges_parts_with_offsets():
    from core.llm.recovery import split_on_overflow
    quota = RecoveryBudget()
    client = SimpleNamespace(recovery=quota, budget=RequestBudget(131072, "m"))

    def run(batch):
        if len(batch) > 1:
            raise ContextBudgetExceeded("too many")
        return [(0, batch[0])]

    def merge(parts):
        return [(index + offset, value) for offset, result in parts for index, value in result]

    assert split_on_overflow(list("abcde"), run, merge, client=client) == list(enumerate("abcde"))
    assert quota.remaining == 3

    def truncated(batch):
        raise TruncatedModelOutput("cut")

    with pytest.raises(TruncatedModelOutput):
        split_on_overflow(["only"], truncated, merge, client=client)
    assert quota.remaining == 3


def test_item17_selection_covers_each_chunk_before_repeating():
    from core.agents.item17_evidence import Item17EvidencePacker
    chunks = {cid: SimpleNamespace(start_page=page) for cid, page in [("a", 1), ("b", 1), ("c", 5)]}
    packer = Item17EvidencePacker(
        budget=RequestBudget(131072, "m"), evidence=["a1", "a2", "b1", "c1"],
        chunk_ids=["a", "a", "b", "c"], chunk_lookup=chunks,
    )
    assert packer.target_pages() == [1, 5]
    ordered = packer.candidates(100, prefer_unique_chunks=True)
    # 最近的证据在前，每个 chunk 先取一条，再补充同 chunk 的其余证据。
    assert [c.evidence for c in ordered] == ["c1", "b1", "a2", "a1"]
    selection = packer.fit(ordered, lambda evidence, ids: " ".join(evidence), label="test",
                           token_budget=100000, max_items=3, output_tokens=100)
    assert selection.evidence == ["c1", "b1", "a2"]
    assert selection.pages == [5, 1, 1]
