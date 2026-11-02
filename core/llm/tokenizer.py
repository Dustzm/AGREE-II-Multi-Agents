"""Token counting for whole-request budgeting.

The deployment tokenizer is tiktoken (offline wheel). When it is unavailable we fall
back to the measured bytes-per-token ratio of the production extraction prompts.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Iterable

logger = logging.getLogger(__name__)

DEFAULT_ENCODING = "cl100k_base"
# Measured on run 1-2010-008E: byte estimates over-counted by ~4.2x.
BYTES_PER_TOKEN = 4.2
MESSAGE_OVERHEAD = 4

_encodings: dict[str, object | None] = {}


def _encoding(name: str = DEFAULT_ENCODING):
    if name in _encodings:
        return _encodings[name]
    encoding = None
    try:
        import tiktoken

        encoding = tiktoken.get_encoding(name)
    except Exception:  # pragma: no cover - depends on deployment
        logger.debug("tiktoken encoding %s unavailable; using byte ratio", name)
    _encodings[name] = encoding
    return encoding


def _is_wide_char(char: str) -> bool:
    """CJK / full-width characters usually cost close to one token per character."""
    code = ord(char)
    return (
        0x1100 <= code <= 0x11FF
        or 0x2E80 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7AF
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
    )


def estimate_tokens_by_bytes(text: str) -> int:
    """Fallback estimate: wide chars ~1 token, remaining bytes ~4.2 bytes/token."""
    wide = sum(1 for char in text if _is_wide_char(char))
    ascii_bytes = sum(len(char.encode("utf-8")) for char in text if not _is_wide_char(char))
    return max(1, math.ceil(wide + ascii_bytes / BYTES_PER_TOKEN))


def count_text_tokens(text: str, model: str | None = None) -> int:
    """Count tokens for one text blob; conservative when the tokenizer is missing."""
    if not text:
        return 0
    encoding = _encoding()
    if encoding is not None:
        return len(encoding.encode(text, disallowed_special=()))
    return estimate_tokens_by_bytes(text)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image_url":
                parts.append("<image>")
            else:
                parts.append(_content_text(item))
        return "\n".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return "" if content is None else str(content)


def count_messages_tokens(messages: Iterable[dict]) -> int:
    """Count chat messages including per-message framing overhead."""
    if isinstance(messages, dict):
        return count_text_tokens(json.dumps(messages, ensure_ascii=False, sort_keys=True))
    total = 0
    for message in messages:
        total += MESSAGE_OVERHEAD
        if isinstance(message, str):
            total += count_text_tokens(message)
            continue
        if not isinstance(message, dict):
            total += count_text_tokens(str(message))
            continue
        total += count_text_tokens(_content_text(message.get("content")))
        for key, value in message.items():
            if key in {"content", "role"}:
                continue
            total += count_text_tokens(json.dumps({key: value}, ensure_ascii=False))
        total += count_text_tokens(str(message.get("role", "")))
    return total
