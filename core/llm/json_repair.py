"""Local JSON syntax repair ladder.

Principle: repair SYNTAX only. Never invent a missing field, value, quote or
object; if the payload is not recoverable, report why with a precise diagnostic
so the repair request can be actionable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

_STRUCT_OPEN = "{["
_STRUCT_CLOSE = "]}"
_VALID_ESCAPES = set('"\\/bfnrtu')


@dataclass(frozen=True)
class RepairResult:
    """Outcome of one local repair attempt.

    ``lossy`` marks a salvage that had to drop a partially written value or
    string: the model never finished writing it, so callers must not accept the
    payload silently.
    """

    value: dict | list | None
    text: str = ""
    repairs: tuple[str, ...] = ()
    salvaged: bool = False
    lossy: bool = False
    error: dict | None = None


_CITATION = re.compile(r"\[\s*\d{1,4}(?:\s*[,;–—-]\s*\d{1,4})*\s*\]")
_TRAILING_CITATION = re.compile(
    r"\[\s*\d{1,4}(?:\s*[,;–—-]\s*\d{1,4})*\s*\]\s*$"
)
_TRAILING_PUNCTUATION = " \t\r\n。，；：、！？,.;:!?)]）】」’”'\""


def _scan_balanced(text: str, start: int) -> int | None:
    """End offset (exclusive) of the balanced JSON value starting at *start*."""
    stack: list[str] = []
    in_str = esc = False
    for index in range(start, len(text)):
        char = text[index]
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char in _STRUCT_OPEN:
            stack.append(char)
        elif char in _STRUCT_CLOSE:
            if not stack:
                return index + 1
            stack.pop()
            if not stack:
                return index + 1
    return None


def _top_level_spans(text: str) -> tuple[list[tuple[int, int]], int | None]:
    """Complete top-level payload spans plus the start of a trailing truncated one.

    Prose citations (``[1]`` / ``[2, 3]``) are skipped instead of being treated
    as array starts. A value that never closes marks the text as truncated and
    only the payload from that point on is returned by the caller.
    """
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in _STRUCT_OPEN:
            if char == "[" and _CITATION.match(text, index):
                index += 1
                continue
            end = _scan_balanced(text, index)
            if end is None:
                return spans, index
            spans.append((index, end))
            index = end
        else:
            index += 1
    return spans, None


def _trailing_is_noise(tail: str) -> bool:
    """True when *tail* holds only whitespace, a closing fence and prose citations."""
    rest = tail.strip()
    while rest:
        if rest.startswith("```"):
            rest = re.sub(r"^```[a-zA-Z]*", "", rest).strip()
            continue
        match = _TRAILING_CITATION.search(rest)
        if match is not None:
            rest = rest[: match.start()].rstrip()
            continue
        if rest[-1] in _TRAILING_PUNCTUATION:
            rest = rest[:-1].rstrip()
            continue
        return False
    return True


def strip_prose_and_fence(text: str) -> str:
    """Drop reasoning blocks, prose prefixes and markdown fences around the JSON.

    Several top-level values may appear in the text (reasoning examples,
    citation lists, the actual answer). The value that ends the text (modulo
    closing fences and trailing citations) wins; earlier examples and
    ``[1]``/``[2, 3]`` citations are never mistaken for the answer. A payload
    that never closes is returned from its start so the salvage ladder can
    finish it.
    """
    text = text.strip()
    text = re.sub(r"^\s*<think>.*?</think>\s*", "", text, flags=re.DOTALL)
    spans, truncated = _top_level_spans(text)
    if truncated is not None and (not spans or truncated > spans[-1][1]):
        return text[truncated:]
    if spans:
        trailing = [span for span in spans if _trailing_is_noise(text[span[1]:])]
        chosen = (trailing or spans)[-1]
        return text[chosen[0]:chosen[1]]
    return text


def fix_invalid_escapes(text: str) -> tuple[str, int]:
    out: list[str] = []
    fixed = 0
    in_str = False
    index = 0
    while index < len(text):
        char = text[index]
        if not in_str:
            if char == '"':
                in_str = True
            out.append(char)
            index += 1
            continue
        if char == "\\":
            nxt = text[index + 1] if index + 1 < len(text) else ""
            if nxt in _VALID_ESCAPES:
                # Keep the escaped char in the same output pass; otherwise a \"
                # would be re-read as a closing quote and desync the string state.
                out.append(char)
                out.append(nxt)
                index += 2
                continue
            out.append("\\\\")
            fixed += 1
            if nxt:
                out.append(nxt)
                index += 2
            else:
                index += 1
            continue
        if char == '"':
            in_str = False
        out.append(char)
        index += 1
    return "".join(out), fixed


def _escaped_tail(out: list[str]) -> bool:
    backslashes = 0
    for char in reversed(out):
        if char != "\\":
            break
        backslashes += 1
    return backslashes % 2 == 1


def fix_control_chars(text: str) -> tuple[str, int]:
    out: list[str] = []
    fixed = 0
    in_str = False
    for char in text:
        if char == '"' and not _escaped_tail(out):
            in_str = not in_str
            out.append(char)
            continue
        if in_str and ord(char) < 0x20 and char not in "\n\r\t":
            out.append(json.dumps(char)[1:-1])
            fixed += 1
            continue
        if in_str and char in "\n\r\t":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[char])
            fixed += 1
            continue
        out.append(char)
    return "".join(out), fixed


_FULLWIDTH_STRUCTURAL = {
    "｛": "{", "｝": "}", "［": "[", "］": "]", "：": ":", "，": ",",
}
_FULLWIDTH_OPEN_QUOTES = {"＂", "“"}
_FULLWIDTH_CLOSE_QUOTES = {"”"}


def fix_fullwidth(text: str) -> tuple[str, int]:
    """Normalize fullwidth syntax characters WITHOUT rewriting string contents.

    Fullwidth punctuation inside a Chinese quote is evidence text: rewriting
    ``推荐意见：`` or ``他说“好”`` would corrupt the quote that later has to match
    the source chunk character-for-character. Only structural positions are
    converted, and a string opened by a curly quote is closed by the matching
    curly quote.
    """
    out: list[str] = []
    fixed = 0
    in_str = esc = False
    curly_opened = False
    for char in text:
        if in_str:
            if esc:
                esc = False
                out.append(char)
                continue
            if char == "\\":
                esc = True
                out.append(char)
                continue
            if char == '"':
                in_str = False
                out.append(char)
                continue
            if curly_opened and char in _FULLWIDTH_CLOSE_QUOTES:
                in_str = False
                curly_opened = False
                fixed += 1
                out.append('"')
                continue
            out.append(char)
            continue
        if char == '"':
            in_str = True
            out.append(char)
            continue
        if char in _FULLWIDTH_OPEN_QUOTES:
            in_str = True
            curly_opened = True
            fixed += 1
            out.append('"')
            continue
        mapped = _FULLWIDTH_STRUCTURAL.get(char)
        if mapped is not None:
            fixed += 1
            out.append(mapped)
            continue
        out.append(char)
    return "".join(out), fixed


def fix_trailing_commas(text: str) -> tuple[str, int]:
    out: list[str] = []
    fixed = 0
    in_str = esc = False
    for index, char in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            out.append(char)
            continue
        if char == '"':
            in_str = True
        elif char == ",":
            follower = index + 1
            while follower < len(text) and text[follower].isspace():
                follower += 1
            if follower < len(text) and text[follower] in "}]":
                fixed += 1
                continue
        out.append(char)
    return "".join(out), fixed


def fix_python_literals(text: str) -> tuple[str, int]:
    out: list[str] = []
    fixed = 0
    in_str = esc = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            out.append(char)
            index += 1
            continue
        if char == '"':
            in_str = True
            out.append(char)
            index += 1
            continue
        replaced = False
        for literal, target in (("True", "true"), ("False", "false"), ("None", "null")):
            if text.startswith(literal, index):
                out.append(target)
                fixed += 1
                index += len(literal)
                replaced = True
                break
        if not replaced:
            out.append(char)
            index += 1
    return "".join(out), fixed


def salvage_truncated(text: str) -> tuple[str, int, bool]:
    """Close an unterminated object/array at the last complete element.

    Returns ``(text, changed, lossy)``: ``lossy`` is True when closing the
    payload silently drops a value or string the model was still writing, so
    callers can refuse to treat the result as complete model output.
    """
    stack: list[str] = []
    in_str = esc = False
    last_safe = -1
    for index, char in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char in _STRUCT_OPEN:
            stack.append(char)
        elif char in _STRUCT_CLOSE:
            if stack:
                stack.pop()
            if not stack:
                last_safe = index
        elif char == "," and stack:
            last_safe = index
    if not stack and not in_str:
        return text, 0, False
    if not in_str:
        # Prefer keeping every complete element: just terminate the open containers.
        candidate = text + "".join("}" if char == "{" else "]" for char in reversed(stack))
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            pass
        else:
            tail = text.rstrip()
            lossy = bool(tail) and tail[-1] not in '}"]'
            return candidate, 1, lossy
    cut = last_safe if last_safe > 0 else len(text)
    dropped = text[cut:].strip().lstrip(",").strip()
    prefix = text[:cut].rstrip().rstrip(",")
    stack = []
    in_str = esc = False
    for char in prefix:
        if in_str:
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char in _STRUCT_OPEN:
            stack.append(char)
        elif char in _STRUCT_CLOSE and stack:
            stack.pop()
    closing = "".join("}" if char == "{" else "]" for char in reversed(stack))
    if in_str:
        prefix += '"'
    lossy = in_str or bool(dropped)
    return prefix + closing, 1, lossy


def _reject_constant(value):
    raise ValueError(f"NaN/Infinity not allowed: {value}")


def _container(value) -> dict | list | None:
    # Top-level arrays are returned as-is: wrapping them is the caller's decision
    # (``parse_json_object(list_wrapper=...)``), not a syntax repair.
    return value if isinstance(value, (dict, list)) else None


def _diagnostic(text: str) -> dict:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "message": exc.msg,
            "line": exc.lineno,
            "column": exc.colno,
            "offset": exc.pos,
            "snippet": text[max(0, exc.pos - 80): exc.pos + 120],
        }
    return {"message": "not an object"}


_SYNTAX_STEPS = (
    ("fix_invalid_escapes", fix_invalid_escapes),
    ("fix_control_chars", fix_control_chars),
    ("fix_fullwidth", fix_fullwidth),
    ("fix_trailing_commas", fix_trailing_commas),
    ("fix_python_literals", fix_python_literals),
)


def repair_json_text(text: str) -> RepairResult:
    """Try the repair ladder and return either a JSON object or a diagnostic."""
    repairs: list[str] = []
    work = strip_prose_and_fence(text)
    if work != text.strip():
        repairs.append("strip_prose_or_fence")

    def attempt(source: str) -> dict | list | None:
        try:
            return _container(json.loads(source, parse_constant=_reject_constant))
        except (json.JSONDecodeError, ValueError):
            return None

    result = attempt(work)
    if result is not None:
        return RepairResult(result, work, tuple(repairs))

    for name, fixer in _SYNTAX_STEPS:
        new_text, count = fixer(work)
        if not count:
            continue
        repairs.append(f"{name}:{count}")
        work = new_text
        result = attempt(work)
        if result is not None:
            return RepairResult(result, work, tuple(repairs))

    salvaged, count, lossy = salvage_truncated(work)
    if count:
        repairs.append("salvage_truncated" + (":lossy" if lossy else ""))
        result = attempt(salvaged)
        if result is not None:
            return RepairResult(
                result, salvaged, tuple(repairs), salvaged=True, lossy=lossy,
            )

    return RepairResult(None, work, tuple(repairs), error=_diagnostic(work))
