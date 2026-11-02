"""Cross-cutting observability helpers (failure reports, structured diagnostics)."""
from core.observability.appraisal import build_failure_report, write_failure_report

__all__ = ["build_failure_report", "write_failure_report"]
