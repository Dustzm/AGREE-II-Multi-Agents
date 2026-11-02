"""Application logging setup.

FastMCP installs its own ``RichHandler`` on the root logger as a side effect of
``FastMCP(...)`` construction at import time (``mcp/server/fastmcp/utilities/logging.py``
→ ``configure_logging``). When stdout/stderr are redirected to ``logs/backend.log``
rich falls back to an 80-column virtual terminal, so every record wraps at ~40
characters and the file becomes unreadable and huge.

This module replaces the root handler after all third-party imports so the backend
keeps one plain line per record (grep/awk friendly, no colour codes).
"""
from __future__ import annotations

import logging
import os
import sys

from core.config import app_setting
from core.config.settings import normalize_log_level

DEFAULT_LEVEL = "INFO"
DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def _configured_level(level: str | None) -> str:
    """Explicit argument > AMA_LOG_LEVEL > ``.env`` LOG_LEVEL > INFO."""
    for candidate in (level, os.environ.get("AMA_LOG_LEVEL")):
        if candidate:
            return str(candidate)
    return str(app_setting("log_level", DEFAULT_LEVEL) or DEFAULT_LEVEL)


def configure_logging(level: str | None = None) -> None:
    """Install a single plain StreamHandler on the root logger (idempotent)."""
    configured = _configured_level(level)
    resolved_level = normalize_log_level(configured, default="")
    if not resolved_level:
        logging.getLogger(__name__).warning(
            "未知日志级别 %s，回退到 %s", configured, DEFAULT_LEVEL,
        )
        resolved_level = DEFAULT_LEVEL

    root = logging.getLogger()
    for handler in root.handlers[:]:
        if "rich" in type(handler).__module__:
            root.removeHandler(handler)
            handler.close()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(DEFAULT_FORMAT))
        root.addHandler(handler)
    root.setLevel(resolved_level)
