from types import SimpleNamespace
import asyncio

import pytest

from core.preprocessor.labeler import SemanticLabeler
from core.preprocessor.label_checkpoint import LabelCheckpoint
from core.preprocessor.pojo import SemanticLabel, TitleContentBlock
from core.config.settings import LLMSettings
from core.llm.context import ContextBudgetExceeded
from core.llm.gateway import LLMReply
from core.llm.json_output import TruncatedModelOutput
from core.utils.llm_errors import InvalidModelJSON


def _build_response(*, content=None, reasoning=None, reasoning_content=None, finish_reason=None):
    message = SimpleNamespace(
        content=content,
        reasoning=reasoning,
        reasoning_content=reasoning_content,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    monkeypatch.setattr("core.llm.recovery.backoff_seconds", lambda retry: 0)


@pytest.mark.parametrize("response,expected", [
    (_build_response(content='  {"tag":"Overview"}  '), '{"tag":"Overview"}'),
    (_build_response(content=None, reasoning='{"tag":"Recommendation"}'), '{"tag":"Recommendation"}'),
    (_build_response(content=[
        {'type': 'text', 'text': '{"tag":"Conclusion",'},
        {'type': 'text', 'text': '"reason":"ok"}'},
    ]), '{"tag":"Conclusion",\n"reason":"ok"}'),
    (_build_response(content='<think>hidden</think>{"tag":"Appendix"}'), '{"tag":"Appendix"}'),
])
def test_reply_text_prefers_content_and_falls_back_to_reasoning(response, expected):
    assert LLMReply.from_response(response).checked_text() == expected


def test_invalid_semantic_label_is_not_irrelevant():
    labeler = SemanticLabeler.__new__(SemanticLabeler)
    with pytest.raises(InvalidModelJSON):
        labeler._parse_json_response('{"tag":"unrecognized"}')
    assert labeler._parse_json_response('{"tag":"Irrelevant"}')[0] is SemanticLabel.IRRELEVANT


@pytest.mark.parametrize("response", [
    '{"tag":"Overview","tag":"Method"}',
    '{"tag":"Method","reason":NaN}',
])
def test_ambiguous_semantic_json_is_rejected(response):
    labeler = SemanticLabeler.__new__(SemanticLabeler)
    with pytest.raises(InvalidModelJSON):
        labeler._parse_json_response(response)


@pytest.fixture
def isolated_labeler_settings(monkeypatch):
    llm = LLMSettings(
        _env_file=None, provider="vllm", model_name="offline-review-model",
        base_url="http://test.invalid/v1", api_key="test", context_window=131072,
    )
    monkeypatch.setattr("core.llm.gateway.current_llm_settings", lambda: llm)
    monkeypatch.setattr("core.llm.context.current_llm_settings", lambda: llm)
    return llm


def _labeling_input(tmp_path, labeler):
    source = tmp_path / "guide_content_list.json"
    source.write_text("[]", encoding="utf-8")
    chunk = TitleContentBlock(title="Methods", title_page_idx=0, contents=[], raw_text="x" * 9000)
    checkpoint = LabelCheckpoint(str(source), str(tmp_path / "evidence.json"), labeler.prompt_template, "en")
    return chunk, checkpoint


def test_semantic_budget_rejects_full_prompt_before_request(tmp_path, monkeypatch, isolated_labeler_settings):
    isolated_labeler_settings.context_window = 4000
    labeler = SemanticLabeler(lang="en")
    chunk, checkpoint = _labeling_input(tmp_path, labeler)
    # Template + truncated content (~1.8k tokens) cannot fit a 4k window together
    # with the 2.5k output reserve, so the request is rejected before any model call.
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        return _build_response(content='{"tag":"Method"}', finish_reason="stop")

    monkeypatch.setattr("litellm.acompletion", complete)
    with pytest.raises(ContextBudgetExceeded):
        asyncio.run(labeler.label_chunks([chunk], checkpoint=checkpoint))
    assert calls == []
    assert checkpoint.get(0, chunk) is None
    assert not checkpoint.path.exists()


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "MAX_TOKENS"])
def test_truncated_semantic_json_never_becomes_checkpoint(
    tmp_path, monkeypatch, isolated_labeler_settings, finish_reason,
):
    labeler = SemanticLabeler(lang="en")
    chunk, checkpoint = _labeling_input(tmp_path, labeler)
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        return _build_response(content='{"tag":"Method"}', finish_reason=finish_reason)

    monkeypatch.setattr("litellm.acompletion", complete)
    with pytest.raises(TruncatedModelOutput):
        asyncio.run(labeler.label_chunks([chunk], checkpoint=checkpoint))
    assert len(calls) == 2
    assert checkpoint.get(0, chunk) is None
    assert not checkpoint.path.exists()


def test_semantic_retry_checkpoints_only_complete_response(tmp_path, monkeypatch, isolated_labeler_settings):
    labeler = SemanticLabeler(lang="en")
    chunk, checkpoint = _labeling_input(tmp_path, labeler)
    responses = [
        _build_response(content='{"tag":"Overview"}', finish_reason="length"),
        _build_response(content=None, reasoning='{"tag":"Method","reason":"ok"}', finish_reason="stop"),
    ]
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("litellm.acompletion", complete)
    result = asyncio.run(labeler.label_chunks([chunk], checkpoint=checkpoint))
    assert len(calls) == 2
    assert result[0].label is SemanticLabel.METHOD
    assert checkpoint.get(0, chunk) == (SemanticLabel.METHOD, "ok")
    assert checkpoint.path.exists()


def test_semantic_checkpoint_reuses_successful_chunks_after_failure(tmp_path, monkeypatch):
    source = tmp_path / "guide_content_list.json"
    source.write_text("[]", encoding="utf-8")
    output = tmp_path / "guide_evidence_pool.json"
    prompt = "{title} {content} {section} {page} {total_pages}"
    chunks = [
        TitleContentBlock(title="First", title_page_idx=0, contents=[], raw_text="first"),
        TitleContentBlock(title="Second", title_page_idx=1, contents=[], raw_text="second"),
    ]
    labeler = SemanticLabeler.__new__(SemanticLabeler)
    labeler.prompt_template = prompt
    calls = []

    async def failing_call(text):
        calls.append(text)
        if "Second" in text:
            raise TimeoutError("model timed out")
        return '{"tag":"Overview"}'

    monkeypatch.setattr(labeler, "_call_llm", failing_call)
    checkpoint = LabelCheckpoint(str(source), str(output), prompt, "zh")
    with pytest.raises(TimeoutError):
        asyncio.run(labeler.label_chunks(chunks, max_concurrency=1, checkpoint=checkpoint))
    assert checkpoint.get(0, chunks[0]) == (SemanticLabel.OVERVIEW, "")
    assert checkpoint.get(1, chunks[1]) is None
    assert len(calls) == 3

    async def succeeding_call(text):
        calls.append(text)
        return '{"tag":"Method"}'

    monkeypatch.setattr(labeler, "_call_llm", succeeding_call)
    resumed = LabelCheckpoint(str(source), str(output), prompt, "zh")
    result = asyncio.run(labeler.label_chunks(chunks, max_concurrency=1, checkpoint=resumed))
    assert [chunk.label for chunk in result] == [SemanticLabel.OVERVIEW, SemanticLabel.METHOD]
    assert len(calls) == 4


def test_checkpoint_journal_skips_torn_last_line(tmp_path, monkeypatch):
    llm = LLMSettings(_env_file=None, provider="vllm", model_name="m", base_url="http://test.invalid/v1", context_window=131072)
    monkeypatch.setattr("core.llm.context.current_llm_settings", lambda: llm)
    source = tmp_path / "guide_content_list.json"
    source.write_text("[]", encoding="utf-8")
    output = tmp_path / "guide_evidence_pool.json"
    chunk = TitleContentBlock(title="Methods", title_page_idx=0, contents=[], raw_text="text")
    result = SimpleNamespace(label=SemanticLabel.METHOD, reasoning="ok")
    checkpoint = LabelCheckpoint(str(source), str(output), "prompt", "en")
    checkpoint.record(0, chunk, result)
    with checkpoint.path.open("a", encoding="utf-8") as journal:
        journal.write('{"index": 1, "label": "Meth')
    resumed = LabelCheckpoint(str(source), str(output), "prompt", "en")
    assert resumed.get(0, chunk) == (SemanticLabel.METHOD, "ok")
    assert resumed.labels.keys() == {"0"}


def test_appendix_blocks_get_appendix_label_but_index_content_is_irrelevant():
    labeler = SemanticLabeler.__new__(SemanticLabeler)
    body = TitleContentBlock(
        title="[附件] Methods", title_page_idx=0, contents=[], raw_text="panel method",
        is_appendix=True,
    )
    refs = TitleContentBlock(
        title="[附件] References", title_page_idx=1, contents=[], raw_text="doi list",
        is_appendix=True,
    )
    body_chunk = asyncio.run(labeler._process_single_chunk(0, body, 2))
    refs_chunk = asyncio.run(labeler._process_single_chunk(1, refs, 2))
    assert body_chunk.label is SemanticLabel.APPENDIX
    assert refs_chunk.label is SemanticLabel.IRRELEVANT


def test_appendix_preferences_attachment_is_not_mistaken_for_references():
    labeler = SemanticLabeler.__new__(SemanticLabeler)
    block = TitleContentBlock(
        title="[附件] Patient preferences survey", title_page_idx=0, contents=[], raw_text="survey",
        is_appendix=True,
    )
    chunk = asyncio.run(labeler._process_single_chunk(0, block, 1))
    assert chunk.label is SemanticLabel.APPENDIX
