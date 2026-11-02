"""结构化输出模式选择与降级链路的单测。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.llm.gateway import LLMGateway, _merge_request_extra
from core.llm.structured_output import (
    GUIDED_JSON,
    JSON_OBJECT,
    JSON_SCHEMA,
    build_structured_format,
    is_unsupported_structured_error,
    reset_cache,
    resolve_mode,
)
from core.rule_engine.litellm_client import LiteLLMRuleClient

SCHEMA = {"type": "object", "properties": {"objects": {"type": "array"}}, "required": ["objects"]}


class _Budget:
    def __init__(self) -> None:
        self.checked: list[object] = []

    def check(self, messages, max_tokens, extra=None, stage=None) -> None:
        self.checked.append(extra)

    def output_limit(self, requested: int) -> int:
        return requested


class _UnsupportedEndpoint(Exception):
    status_code = 400


class _FakeGateway:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.model = "openai/Qwen3-30B-A3B-Instruct-2507"
        self.base_url = "http://fake/v1"
        self.budget = _Budget()
        self.failures = list(failures or [])
        self.calls: list[dict] = []

    def output_limit(self, requested: int) -> int:
        return requested

    def complete(self, stage, messages, *, max_tokens, response_format, extra=None):
        self.calls.append({"response_format": response_format, "extra": extra})
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(json=lambda: {"objects": []})


def test_build_structured_format_covers_all_modes():
    response_format, extra = build_structured_format(JSON_SCHEMA, SCHEMA, schema_name="demo", strict=False)
    assert response_format == {
        "type": "json_schema",
        "json_schema": {"name": "demo", "strict": False, "schema": SCHEMA},
    }
    assert extra is None

    response_format, extra = build_structured_format(GUIDED_JSON, SCHEMA)
    assert response_format == JSON_OBJECT
    assert extra == {"extra_body": {"guided_json": SCHEMA}}

    assert build_structured_format("json_object", SCHEMA) == (JSON_OBJECT, None)
    assert build_structured_format(JSON_SCHEMA, None) == (JSON_OBJECT, None)


def test_unsupported_error_detection():
    assert is_unsupported_structured_error(_UnsupportedEndpoint("response_format is not supported"))
    assert is_unsupported_structured_error(_UnsupportedEndpoint("guided_json unsupported"))
    assert not is_unsupported_structured_error(_UnsupportedEndpoint("model overloaded"))
    assert not is_unsupported_structured_error(ValueError("Invalid extracted objects"))


def test_merge_request_extra_merges_extra_body():
    request = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    _merge_request_extra(request, {"extra_body": {"guided_json": SCHEMA}})
    assert request["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False},
        "guided_json": SCHEMA,
    }
    _merge_request_extra(request, None)
    assert "guided_json" in request["extra_body"]


def test_client_degrades_to_guided_json_and_remembers(monkeypatch):
    reset_cache()
    gateway = _FakeGateway([_UnsupportedEndpoint("response_format: json_schema not supported")])
    client = LiteLLMRuleClient(gateway)

    assert client.complete_json(
        stage="S", system_prompt="sys", user_prompt="u", max_tokens=100, json_schema=SCHEMA,
    ) == {"objects": []}

    assert gateway.calls[0]["response_format"]["type"] == "json_schema"
    assert gateway.calls[1]["response_format"] == JSON_OBJECT
    assert gateway.calls[1]["extra"] == {"extra_body": {"guided_json": SCHEMA}}
    assert resolve_mode(gateway.base_url, gateway.model) == GUIDED_JSON

    # 第二次调用直接从 guided_json 开始，不再重复失败的 json_schema。
    second = _FakeGateway()
    client2 = LiteLLMRuleClient(second)
    client2.complete_json(stage="S", system_prompt="sys", user_prompt="u", max_tokens=100, json_schema=SCHEMA)
    assert second.calls[0]["response_format"] == JSON_OBJECT
    assert second.calls[0]["extra"] == {"extra_body": {"guided_json": SCHEMA}}


def test_client_downgrades_to_plain_json_object(monkeypatch):
    reset_cache()
    gateway = _FakeGateway([
        _UnsupportedEndpoint("response_format is not supported"),
        _UnsupportedEndpoint("guided_json is not supported"),
    ])
    client = LiteLLMRuleClient(gateway)
    assert client.complete_json(stage="S", system_prompt="sys", user_prompt="u", max_tokens=100, json_schema=SCHEMA) == {"objects": []}
    assert gateway.calls[-1]["response_format"] == JSON_OBJECT
    assert gateway.calls[-1]["extra"] is None
    assert resolve_mode(gateway.base_url, gateway.model) == "json_object"


def test_client_does_not_swallow_business_errors():
    reset_cache()
    gateway = _FakeGateway([ValueError("Invalid extracted objects: ['value_not_found_in_quote']")])
    client = LiteLLMRuleClient(gateway)
    with pytest.raises(ValueError, match="Invalid extracted objects"):
        client.complete_json(stage="S", system_prompt="sys", user_prompt="u", max_tokens=100, json_schema=SCHEMA)


def test_deepseek_models_keep_json_object(monkeypatch):
    reset_cache()
    gateway = _FakeGateway()
    gateway.model = "openai/deepseek-v3"
    client = LiteLLMRuleClient(gateway)
    client.complete_json(stage="S", system_prompt="sys", user_prompt="u", max_tokens=100, json_schema=SCHEMA)
    assert gateway.calls[0]["response_format"] == JSON_OBJECT
    assert gateway.calls[0]["extra"] is None


def test_env_forced_mode(monkeypatch):
    reset_cache()
    monkeypatch.setenv("LLM_STRUCTURED_OUTPUT", "json_object")
    gateway = _FakeGateway()
    client = LiteLLMRuleClient(gateway)
    client.complete_json(stage="S", system_prompt="sys", user_prompt="u", max_tokens=100, json_schema=SCHEMA)
    assert gateway.calls[0]["response_format"] == JSON_OBJECT
    assert gateway.calls[0]["extra"] is None


def test_gateway_request_carries_guided_json_in_extra_body():
    gateway = LLMGateway(
        model="openai/Qwen",
        base_url="http://fake/v1",
        api_key="k",
        timeout=10,
        provider_kwargs={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        budget=_Budget(),
    )
    request = gateway._request(
        "S",
        [{"role": "user", "content": "hi"}],
        100,
        JSON_OBJECT,
        {"extra_body": {"guided_json": SCHEMA}},
    )
    assert request["response_format"] == JSON_OBJECT
    assert request["extra_body"]["guided_json"] == SCHEMA
    assert request["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_merge_request_extra_ignores_reserved_fields():
    request = {"model": "real", "api_key": "key", "messages": [{"role": "user", "content": "x"}]}
    _merge_request_extra(request, {"model": "evil", "api_key": "evil", "messages": [], "temperature": 0})
    assert request["model"] == "real"
    assert request["api_key"] == "key"
    assert request["messages"] == [{"role": "user", "content": "x"}]
    assert request["temperature"] == 0
