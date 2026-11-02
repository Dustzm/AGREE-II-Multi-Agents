import asyncio
from types import SimpleNamespace

from core.attachment.url import classifier as url_classifier_module
from core.attachment.url.classifier import URLClassificationOutput, URLClassifier
from core.agents.base.model_config import (
    get_compatible_reasoning_kwargs,
    get_compatible_sampling_kwargs,
    get_provider_kwargs_for,
    load_providers_config,
)


def test_deepseek_provider_disables_thinking():
    kwargs = get_provider_kwargs_for("deepseek")

    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_zhipu_provider_disables_thinking():
    kwargs = get_provider_kwargs_for("zhipu")

    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_vllm_provider_keeps_chat_template_thinking_disabled():
    kwargs = get_provider_kwargs_for("vllm")

    assert kwargs == {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}


def test_dashscope_provider_disables_qwen_thinking():
    kwargs = get_provider_kwargs_for("dashscope")

    assert kwargs == {"extra_body": {"enable_thinking": False}}


def test_openai_compatible_provider_has_no_extra_thinking_kwargs():
    kwargs = get_provider_kwargs_for("openai_compatible")

    assert kwargs == {}


def test_gemini3_model_uses_low_reasoning_effort_by_default():
    kwargs = get_compatible_reasoning_kwargs("gemini/gemini-3.1-pro-preview")

    assert kwargs == {
        "reasoning_effort": "low",
        "allowed_openai_params": ["reasoning_effort"],
    }


def test_gemini3_model_omits_custom_temperature_and_top_p():
    kwargs = get_compatible_sampling_kwargs(
        "gemini/gemini-3.1-pro-preview",
        temperature=0,
        top_p=0.1,
        seed=42,
    )

    assert kwargs == {"seed": 42}


def test_gemini_provider_uses_configurable_base_url():
    providers = load_providers_config()

    gemini = providers["gemini"]

    assert gemini["need_base_url"] is True
    assert gemini["default_base_url"] == "https://generativelanguage.googleapis.com"
    assert gemini["env_base"] == "GEMINI_API_BASE"


def test_deepseek_provider_models_are_current_v4_only():
    providers = load_providers_config()

    model_ids = [model["id"] for model in providers["deepseek"]["models"]]

    assert model_ids == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert "deepseek-chat" not in model_ids
    assert "deepseek-reasoner" not in model_ids


def test_dashscope_provider_models_include_qwen37():
    providers = load_providers_config()

    models_by_id = {
        model["id"]: model
        for model in providers["dashscope"]["models"]
    }

    assert list(models_by_id) == ["qwen3.7-max", "qwen3.7-plus"]
    assert models_by_id["qwen3.7-max"]["vision"] is False
    assert models_by_id["qwen3.7-plus"]["vision"] is True


def test_url_classifier_uses_provider_kwargs(monkeypatch):
    captured_kwargs = {}

    def fake_completion(**kwargs):
        captured_kwargs.update(kwargs)
        parsed = URLClassificationOutput(
            is_attachment=True,
            confidence=0.9,
            reasoning="URL points to a PDF attachment.",
            attachment_type="pdf",
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))])

    monkeypatch.setattr(url_classifier_module.litellm, "completion", fake_completion)
    monkeypatch.setattr(
        url_classifier_module,
        "get_provider_kwargs",
        lambda: {"extra_body": {"thinking": {"type": "disabled"}}},
    )

    classifier = URLClassifier.__new__(URLClassifier)
    classifier.lang = "en"
    classifier.model = "deepseek/deepseek-v4-pro"
    classifier.api_base = ""
    classifier.api_key = "test-key"
    classifier.system_prompt = URLClassifier.SYSTEM_PROMPT_EN

    result = asyncio.run(
        classifier.classify(
            "https://example.com/search_strategy.pdf",
            "For detailed search strategy, visit the linked PDF.",
            "Methodology",
        )
    )

    assert result["is_attachment"] is True
    assert captured_kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
