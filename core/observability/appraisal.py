"""Structured per-run failure report (L9 observability).

Writes ``failure_report.json`` beside the task artefacts so degraded/failed items are
inspectable without grepping backend.log.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import app_setting
from core.llm.failures import reported_kind

logger = logging.getLogger(__name__)

REPORT_NAME = "failure_report.json"


def _kind_from_detail(detail: dict[str, Any]) -> str:
    """Kind recorded at failure time, else re-derived by the shared classifier."""
    recorded = detail.get("failure_kind")
    if recorded:
        return str(recorded)
    return reported_kind(None, str(detail.get("type") or ""), str(detail.get("message") or ""))


def build_failure_report(task_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Summarise failures and degraded fallbacks for one item-batch run."""
    failed_details = summary.get("failed_details") or {}
    failed_messages = summary.get("failed") or {}
    items: list[dict[str, Any]] = []
    for item_id in sorted(set(failed_messages) | set(failed_details)):
        detail = dict(failed_details.get(item_id) or {})
        items.append(
            {
                "item_id": item_id,
                "kind": _kind_from_detail(detail),
                "stage": detail.get("stage"),
                "type": detail.get("type"),
                "message": str(failed_messages.get(item_id, detail.get("message", "")))[:2000],
            }
        )
    degraded = {
        item_id: {"reason": reason}
        for item_id, reason in (summary.get("degraded") or {}).items()
    }
    report_degraded = {
        item_id: {"reason": reason}
        for item_id, reason in (summary.get("report_degraded") or {}).items()
    }
    # ``completed`` may include items that only passed through a degraded fallback;
    # count them once, under degraded, so counts add up to total.
    succeeded = [item for item in (summary.get("completed") or []) if item not in degraded]
    return {
        "task_id": task_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": summary.get("status"),
        "total": summary.get("total"),
        "counts": {
            "succeeded": len(succeeded),
            "degraded": len(degraded),
            "failed": len(items),
        },
        "succeeded": succeeded,
        "degraded": degraded,
        "report_degraded": report_degraded,
        "durations": {
            item_id: round(float(duration), 3)
            for item_id, duration in (summary.get("durations") or {}).items()
            if isinstance(duration, (int, float))
        },
        "failed": items,
    }


def write_failure_report(task_id: str, summary: dict[str, Any], *, task_dir: str | Path | None = None) -> Path | None:
    """Persist the report under the task directory; never fail the run on I/O errors."""
    if not app_setting("rule_report_failures", True):
        return None

    if task_dir is None:
        try:
            from core.agents.tools.public_tools import get_task_paths

            task_dir = (get_task_paths(task_id) or {}).get("task_dir")
        except Exception:
            task_dir = None
    if not task_dir:
        logger.debug("failure_report skipped: no task_dir for %s", task_id)
        return None

    try:
        path = Path(task_dir) / REPORT_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = build_failure_report(task_id, summary)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("failure_report 已写出: %s (failed=%s degraded=%s)", path, len(payload["failed"]), len(payload["degraded"]))
        return path
    except OSError as exc:  # pragma: no cover - filesystem dependent
        logger.warning("failure_report 写出失败: %s", exc)
        return None
