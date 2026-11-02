"""异常诊断文本：保留排查信息，去除认证信息与配置凭据。"""
from __future__ import annotations

import os
import re
import traceback
from typing import Any


def sanitize_diagnostic(value: Any, *, limit: int | None = 4000) -> str:
    """保留排查信息，但不把认证信息或配置凭据发送给浏览器。"""
    text = "".join(traceback.format_exception(value)) if isinstance(value, BaseException) else str(value)
    for name in ("LLM_API_KEY", "AMA_AUTH_SECRET", "LLM_CONFIG_SECRET"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r'''(?i)((?:api[_-]?key|password|secret|token|authorization)["']?\s*[:=]\s*)(["'])(.*?)\2''', r'\1\2[redacted]\2', text)
    text = re.sub(r"(?i)(bearer\s+)[^\s,;\"']+", r"\1[redacted]", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|password|secret|token|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s,;&\"'}]+",
        r"\1[redacted]", text,
    )
    text = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted]", text)
    return text[:limit] if limit is not None else text
