from types import SimpleNamespace

from core.preprocessor.labeler import SemanticLabeler


def _build_response(*, content=None, reasoning=None, reasoning_content=None):
    message = SimpleNamespace(
        content=content,
        reasoning=reasoning,
        reasoning_content=reasoning_content,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_extract_response_text_prefers_content_string():
    labeler = SemanticLabeler.__new__(SemanticLabeler)

    response = _build_response(content='  {"tag":"Overview"}  ')

    assert labeler._extract_response_text(response) == '{"tag":"Overview"}'


def test_extract_response_text_falls_back_to_reasoning_when_content_is_none():
    labeler = SemanticLabeler.__new__(SemanticLabeler)

    response = _build_response(content=None, reasoning='{"tag":"Recommendation"}')

    assert labeler._extract_response_text(response) == '{"tag":"Recommendation"}'


def test_extract_response_text_supports_segmented_content_list():
    labeler = SemanticLabeler.__new__(SemanticLabeler)

    response = _build_response(
        content=[
            {'type': 'text', 'text': '{"tag":"Conclusion",'},
            {'type': 'text', 'text': '"reason":"ok"}'},
        ]
    )

    assert labeler._extract_response_text(response) == '{"tag":"Conclusion",\n"reason":"ok"}'


def test_extract_response_text_strips_think_tags():
    labeler = SemanticLabeler.__new__(SemanticLabeler)

    response = _build_response(content='<think>hidden</think>{"tag":"Appendix"}')

    assert labeler._extract_response_text(response) == '{"tag":"Appendix"}'
