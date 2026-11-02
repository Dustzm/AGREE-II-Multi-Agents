"""Strict JSON normalization; incomplete or ambiguous responses remain failures."""
import json
import logging
import re

from core.llm.json_repair import repair_json_text
from core.utils.llm_errors import InvalidModelJSON

logger = logging.getLogger(__name__)


class TruncatedModelOutput(InvalidModelJSON):
    """Generation exhausted its output allowance; split the requested output."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidModelJSON(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise InvalidModelJSON(f"Invalid JSON number: {value}")


def _remove_trailing_commas(text: str) -> str:
    """Remove only commas before closing brackets outside quoted strings."""
    quoted = escaped = False
    output = []
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == ',':
            previous = index - 1
            while previous >= 0 and text[previous].isspace():
                previous -= 1
            if previous < 0 or text[previous] in '[{,:':
                output.append(char)
                continue
            rest = index + 1
            while rest < len(text) and text[rest].isspace():
                rest += 1
            if rest < len(text) and text[rest] in '}]':
                continue
        output.append(char)
    return ''.join(output)


def check_finish_reason(finish_reason) -> None:
    """Reject interrupted generations before callers accept or checkpoint content."""
    if finish_reason in {"length", "max_tokens", "MAX_TOKENS"}:
        raise TruncatedModelOutput("Model output truncated at token limit")
    if finish_reason in {"content_filter", "error"}:
        raise InvalidModelJSON(f"Model did not complete response: {finish_reason}")


def parse_json_object(content: str, finish_reason=None, *, list_wrapper: str | None = None) -> dict:
    """Parse one JSON object; ``list_wrapper`` accepts a bare top-level array.

    Some stages ask for ``{"decisions": [...]}`` and the model occasionally
    returns only the array. When the caller declares ``list_wrapper``, such an
    array is wrapped under that key instead of failing the whole request.
    """
    check_finish_reason(finish_reason)
    text = re.sub(r"^\s*<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()
    if text.startswith("```") and text.endswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)[:-3].strip()
    try:
        value = json.loads(_remove_trailing_commas(text), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        return _parse_repaired(content, exc, list_wrapper=list_wrapper)
    if isinstance(value, list) and list_wrapper is not None:
        return {list_wrapper: value}
    if not isinstance(value, dict):
        raise InvalidModelJSON("Model response must be a JSON object")
    return value


def _ends_closed(content: str) -> bool:
    """True when the text ends with a closing bracket (after an optional fence)."""
    tail = re.sub(r"```\s*$", "", content.rstrip()).rstrip()
    return tail.endswith(("}", "]"))


def _parse_repaired(
    content: str,
    cause: json.JSONDecodeError,
    *,
    list_wrapper: str | None = None,
) -> dict:
    """Run the local syntax-repair ladder before declaring the payload invalid."""
    repaired = repair_json_text(content)
    if repaired.value is None:
        raise InvalidModelJSON("Model returned invalid JSON") from cause
    if repaired.lossy:
        # Closing a half-written string/element would silently drop content, so a
        # lossy salvage is never accepted. A payload the model closed itself is a
        # syntax error (e.g. a stray quote) and goes to repair; only an abrupt end
        # is truncation, which callers answer by splitting the batch.
        if _ends_closed(content):
            raise InvalidModelJSON("Model returned invalid JSON; refusing lossy repair") from cause
        raise TruncatedModelOutput(
            "Model output ended mid-value; refusing lossy repair",
        ) from cause
    try:
        value = json.loads(repaired.text, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise InvalidModelJSON("Model returned invalid JSON") from exc
    if isinstance(value, list):
        if list_wrapper is None:
            raise InvalidModelJSON("Model response must be a JSON object")
        value = {list_wrapper: value}
    if not isinstance(value, dict):
        raise InvalidModelJSON("Model response must be a JSON object")
    logger.warning(
        "模型输出本地修复成功: repairs=%s salvaged=%s", list(repaired.repairs), repaired.salvaged,
    )
    return value
