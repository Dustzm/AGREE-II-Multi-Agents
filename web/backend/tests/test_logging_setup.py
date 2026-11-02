"""Root logger must not keep FastMCP's RichHandler (narrow 80-col file output)."""
from __future__ import annotations

import logging

from rich.logging import RichHandler

from web.backend.app.logging_setup import configure_logging


def test_configure_logging_replaces_rich_handler():
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    rich_handler = RichHandler()
    root.addHandler(rich_handler)
    try:
        configure_logging("WARNING")
        modules = {type(handler).__module__ for handler in root.handlers}
        assert not any("rich" in module for module in modules)
        assert root.level == logging.WARNING
        plain = [handler for handler in root.handlers if isinstance(handler, logging.StreamHandler)]
        assert plain
        assert "%(message)s" in (plain[0].formatter._fmt or "")
    finally:
        for handler in root.handlers[:]:
            if handler not in original_handlers:
                root.removeHandler(handler)
        for handler in original_handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(original_level)
