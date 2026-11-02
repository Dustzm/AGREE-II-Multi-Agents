"""Planner and tokenizer behaviour: batches always fit the window."""
from __future__ import annotations

import pytest

from core.llm.context import ContextBudgetExceeded, RequestBudget
from core.llm.planner import plan_batches
from core.llm.tokenizer import count_messages_tokens, count_text_tokens
from core.llm.window import plan_window


def _plan(context_window=2000, requested_output=500):
    return plan_window(context_window, requested_output, margin_ratio=0.0)


def test_plan_window_reserves_output_and_margin():
    plan = plan_window(1000, 400, margin_ratio=0.1, max_output_tokens=300)
    assert plan.output_reserve == 300
    assert plan.safety_margin == 256  # MIN_MARGIN floors tiny windows
    assert plan.input_budget == 1000 - 300 - 256
    assert plan.fits(444)
    assert not plan.fits(445)


def test_plan_window_requires_context_window():
    with pytest.raises(ContextBudgetExceeded):
        plan_window(None, 100)


def test_token_counts_are_deterministic_and_monotonic():
    short = count_text_tokens("hello world")
    longer = count_text_tokens("hello world " * 50)
    assert short > 0
    assert longer > short


def test_message_tokens_accept_dict_and_string_payloads():
    as_dict = count_messages_tokens({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    as_list = count_messages_tokens([{"role": "user", "content": "hi"}])
    assert as_dict > 0 and as_list > 0
    assert count_messages_tokens(["plain text", "more text"]) > 0


def test_plan_batches_splits_oversized_batch():
    units = [f"chunk-{index}" for index in range(10)]
    weight = {unit: 300 for unit in units}
    planned = plan_batches(units, lambda batch: sum(weight[u] for u in batch), _plan(2000, 500))
    assert planned.batch_count > 1
    assert [unit for batch in planned.batches for unit in batch] == units
    for batch in planned.batches:
        assert sum(weight[u] for u in batch) <= _plan(2000, 500).input_budget


def test_plan_batches_splits_single_oversized_unit_with_splitter():
    unit = "x" * 5000
    planned = plan_batches(
        [unit],
        lambda batch: sum(len(item) for item in batch),
        _plan(2000, 500),
        split_unit=lambda item: (item[: len(item) // 2], item[len(item) // 2 :]),
    )
    joined = "".join(part for batch in planned.batches for part in batch)
    assert joined == unit
    assert planned.batch_count > 1


def test_plan_batches_rejects_indivisible_oversized_unit():
    unit = "y" * 5000
    with pytest.raises(ContextBudgetExceeded):
        plan_batches([unit], lambda batch: sum(len(item) for item in batch), _plan(2000, 500))


def test_plan_batches_keeps_single_small_batch():
    planned = plan_batches(["a", "b"], lambda batch: 10 * len(batch), _plan(2000, 500))
    assert planned.batch_count == 1
    assert planned.batches == (("a", "b"),)


def test_request_budget_estimate_counts_extra_payload():
    budget = RequestBudget(context_window=100000, model="m")
    base = budget.estimate([{"role": "user", "content": "hi"}])
    with_extra = budget.estimate([{"role": "user", "content": "hi"}], extra={"response_format": {"type": "json_schema"}})
    assert with_extra > base


def test_byte_estimator_counts_wide_chars_per_character():
    from core.llm.tokenizer import estimate_tokens_by_bytes

    assert estimate_tokens_by_bytes("葡萄糖") == 3
    assert estimate_tokens_by_bytes("abcd") == 1
    assert estimate_tokens_by_bytes("葡萄糖abcd") == 4
    # CJK text must not be underestimated by the ASCII bytes/4.2 heuristic.
    assert count_text_tokens("葡萄糖注射液") >= 5


def test_request_budget_derives_missing_window_from_catalog(monkeypatch):
    from core.config.settings import LLMSettings
    import core.config.model_catalog as catalog
    import core.agents.base.model_config as model_config

    monkeypatch.setattr(
        model_config, "get_provider_config_for", lambda provider: {"default_base_url": "https://x/v1"}
    )
    monkeypatch.setattr(
        catalog,
        "lookup_model_limits",
        lambda provider, model: {"max_input_tokens": 120000, "max_output_tokens": 8192},
    )
    llm = LLMSettings(
        _env_file=None,
        provider="vllm",
        base_url="https://x/v1",
        model_name="m",
        context_window=None,
    )
    budget = RequestBudget.from_settings(llm, "m")
    assert budget.context_window == 128192
    budget.require_configured()
