"""
批量任务持久化与事件仓储。

新批量链路的唯一运行态来源是 SQLite。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from core.config import get_settings
from web.backend.app.database import get_db

logger = logging.getLogger(__name__)
_UNSET = object()

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_STOPPING = "stopping"
JOB_STATUS_STOPPED = "stopped"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_INTERRUPTED = "interrupted"

ITEM_STATUS_PENDING = "pending"
ITEM_STATUS_RUNNING = "running"
ITEM_STATUS_STOPPED = "stopped"
ITEM_STATUS_DONE = "done"
ITEM_STATUS_ERROR = "error"

NON_TERMINAL_JOB_STATUSES = {
    JOB_STATUS_QUEUED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_STOPPING,
}
RESUMABLE_JOB_STATUSES = {
    JOB_STATUS_STOPPED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
}

STATUS_REASON_NORMAL = "normal"
STATUS_REASON_STOP_REQUESTED = "stop_requested"
STATUS_REASON_LEASE_EXPIRED = "lease_expired"
STATUS_REASON_REPORTER_TIMEOUT = "reporter_timeout"
STATUS_REASON_MINERU_TIMEOUT = "mineru_timeout"
STATUS_REASON_LLM_TIMEOUT = "llm_timeout"
STATUS_REASON_WORKER_ERROR = "worker_error"

MIN_BATCH_CONCURRENCY = 1
MAX_BATCH_CONCURRENCY = 10
MIN_EXPERIMENT_ROUNDS = 1
MAX_EXPERIMENT_ROUNDS = 4

_CREATE_BATCH_JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS batch_jobs (
    job_id              TEXT PRIMARY KEY,
    status              TEXT NOT NULL CHECK(status IN ('queued', 'running', 'stopping', 'stopped', 'completed', 'failed', 'interrupted')),
    status_reason       TEXT NOT NULL DEFAULT 'normal',
    current_round       INTEGER NOT NULL DEFAULT 0,
    experiment_rounds   INTEGER NOT NULL DEFAULT 1,
    concurrency         INTEGER NOT NULL DEFAULT 2,
    stop_requested      INTEGER NOT NULL DEFAULT 0,
    owner_worker_id     TEXT DEFAULT '',
    run_token           TEXT NOT NULL DEFAULT '',
    lease_expires_at    TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    last_error          TEXT,
    round_runtime_json  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_status ON batch_jobs(status);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_updated_at ON batch_jobs(updated_at);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_lease ON batch_jobs(lease_expires_at);
"""

_CREATE_BATCH_JOB_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS batch_job_items (
    job_id              TEXT NOT NULL,
    item_id             TEXT NOT NULL,
    pdf_path            TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(status IN ('pending', 'running', 'stopped', 'done', 'error')),
    current_step        TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    result_task_id      TEXT,
    first_round_task_id TEXT,
    token_usage_json    TEXT NOT NULL DEFAULT '{}',
    started_at          TEXT,
    updated_at          TEXT NOT NULL,
    finished_at         TEXT,
    last_error          TEXT,
    PRIMARY KEY (job_id, item_id),
    FOREIGN KEY (job_id) REFERENCES batch_jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_batch_job_items_job_id ON batch_job_items(job_id);
CREATE INDEX IF NOT EXISTS idx_batch_job_items_status ON batch_job_items(status);
"""

_CREATE_BATCH_JOB_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS batch_job_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES batch_jobs(job_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_batch_job_events_job_id_id ON batch_job_events(job_id, id);
"""


def init_batch_job_tables(conn: sqlite3.Connection | None = None) -> None:
    """初始化批量任务表。"""
    owns_connection = conn is None
    conn = conn or get_db()
    conn.executescript(_CREATE_BATCH_JOBS_TABLE)
    conn.executescript(_CREATE_BATCH_JOB_ITEMS_TABLE)
    conn.executescript(_CREATE_BATCH_JOB_EVENTS_TABLE)
    _ensure_batch_job_schema(conn)
    if owns_connection:
        conn.commit()


def _ensure_batch_job_schema(conn: sqlite3.Connection) -> None:
    """为旧库补齐新增字段。"""
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(batch_jobs)").fetchall()
    }
    if "status_reason" not in columns:
        conn.execute(
            "ALTER TABLE batch_jobs ADD COLUMN status_reason TEXT NOT NULL DEFAULT 'normal'"
        )
    if "run_token" not in columns:
        conn.execute(
            "ALTER TABLE batch_jobs ADD COLUMN run_token TEXT NOT NULL DEFAULT ''"
        )


def utcnow_iso() -> str:
    """返回 UTC ISO 时间。"""
    return datetime.now(timezone.utc).isoformat()


def _loads_json(payload: str | None, default: Any) -> Any:
    if not payload:
        return default
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return default


def _dumps_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _normalize_job(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    job = dict(row)
    job["stop_requested"] = bool(job.get("stop_requested", 0))
    job["status_reason"] = job.get("status_reason") or STATUS_REASON_NORMAL
    job["run_token"] = job.get("run_token") or ""
    job["round_runtime"] = _loads_json(job.pop("round_runtime_json", "{}"), {})
    return job


def _normalize_item(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["token_usage"] = _loads_json(item.pop("token_usage_json", "{}"), {})
    return item


def _job_is_active(status: str) -> bool:
    return status in NON_TERMINAL_JOB_STATUSES


def _job_is_resumable(status: str) -> bool:
    return status in RESUMABLE_JOB_STATUSES


def _job_is_terminal(status: str) -> bool:
    return status in {
        JOB_STATUS_STOPPED,
        JOB_STATUS_COMPLETED,
        JOB_STATUS_FAILED,
        JOB_STATUS_INTERRUPTED,
    }


def _job_is_running_like(status: str) -> bool:
    return status in {
        JOB_STATUS_QUEUED,
        JOB_STATUS_RUNNING,
        JOB_STATUS_STOPPING,
    }


def _validate_concurrency(concurrency: int) -> int:
    if concurrency < MIN_BATCH_CONCURRENCY or concurrency > MAX_BATCH_CONCURRENCY:
        raise RuntimeError(f"并发数必须在 {MIN_BATCH_CONCURRENCY} 到 {MAX_BATCH_CONCURRENCY} 之间")
    return concurrency


def _validate_experiment_rounds(experiment_rounds: int) -> int:
    if experiment_rounds < MIN_EXPERIMENT_ROUNDS or experiment_rounds > MAX_EXPERIMENT_ROUNDS:
        raise RuntimeError(f"实验轮次必须在 {MIN_EXPERIMENT_ROUNDS} 到 {MAX_EXPERIMENT_ROUNDS} 之间")
    return experiment_rounds


def _task_id_from_pdf(pdf_path: str, seen: set[str]) -> str:
    base = Path(pdf_path).stem
    candidate = base
    index = 1
    while candidate in seen:
        index += 1
        candidate = f"{base}-{index}"
    seen.add(candidate)
    return candidate


def create_batch_job(pdf_paths: Iterable[str], concurrency: int, experiment_rounds: int) -> dict[str, Any]:
    """创建新的批量任务。"""
    concurrency = _validate_concurrency(concurrency)
    experiment_rounds = _validate_experiment_rounds(experiment_rounds)
    conn = get_db()
    init_batch_job_tables(conn)
    active = get_active_job(conn=conn)
    if active is not None:
        raise RuntimeError("已有批量任务正在运行")

    pdf_list = list(pdf_paths)
    if not pdf_list:
        raise RuntimeError("未选择任何 PDF 文件")

    now = utcnow_iso()
    job_id = f"batch-job-{uuid4().hex[:12]}"
    conn.execute(
        """
        INSERT INTO batch_jobs (
            job_id, status, current_round, experiment_rounds, concurrency,
            stop_requested, owner_worker_id, run_token, lease_expires_at, started_at,
            finished_at, created_at, updated_at, last_error, status_reason, round_runtime_json
        ) VALUES (?, ?, 0, ?, ?, 0, '', '', NULL, NULL, NULL, ?, ?, NULL, ?, ?)
        """,
        (
            job_id,
            JOB_STATUS_QUEUED,
            experiment_rounds,
            concurrency,
            now,
            now,
            STATUS_REASON_NORMAL,
            _dumps_json({"round_output_dirs": {}}),
        ),
    )

    seen_ids: set[str] = set()
    item_rows: list[tuple[Any, ...]] = []
    for pdf_path in pdf_list:
        item_id = _task_id_from_pdf(pdf_path, seen_ids)
        item_rows.append(
            (
                job_id,
                item_id,
                pdf_path,
                ITEM_STATUS_PENDING,
                None,
                0,
                None,
                None,
                "{}",
                None,
                now,
                None,
                None,
            )
        )

    conn.executemany(
        """
        INSERT INTO batch_job_items (
            job_id, item_id, pdf_path, status, current_step, retry_count, result_task_id,
            first_round_task_id, token_usage_json, started_at, updated_at, finished_at, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        item_rows,
    )
    append_batch_job_event(
        job_id,
        "status",
        get_batch_job_status_payload(job_id, conn=conn),
        conn=conn,
    )
    conn.commit()
    return get_batch_job_status_payload(job_id, conn=conn)


def get_batch_job(job_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """获取单个 job。"""
    conn = conn or get_db()
    row = conn.execute(
        "SELECT * FROM batch_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return _normalize_job(row)


def list_batch_job_items(job_id: str, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """列出 job 下所有 item。"""
    conn = conn or get_db()
    rows = conn.execute(
        """
        SELECT * FROM batch_job_items
        WHERE job_id = ?
        ORDER BY item_id ASC
        """,
        (job_id,),
    ).fetchall()
    return [_normalize_item(row) for row in rows]


def _item_to_api_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["item_id"],
        "pdf_path": item["pdf_path"],
        "status": item["status"],
        "retry_count": item["retry_count"],
        "error": item.get("last_error"),
        "start_time": item.get("started_at"),
        "end_time": item.get("finished_at"),
        "current_step": item.get("current_step"),
        "result_task_id": item.get("result_task_id"),
        "first_round_task_id": item.get("first_round_task_id"),
        "token_usage": item.get("token_usage") or {},
    }


def get_batch_job_status_payload(job_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """构造前端状态载荷。"""
    conn = conn or get_db()
    job = get_batch_job(job_id, conn=conn)
    if job is None:
        raise RuntimeError(f"批量任务不存在: {job_id}")
    items = list_batch_job_items(job_id, conn=conn)
    counts = Counter(item["status"] for item in items)
    status = job["status"]
    return {
        "job_id": job_id,
        "session_id": job_id,
        "status": status,
        "status_reason": job["status_reason"],
        "last_error": job.get("last_error"),
        "is_running": _job_is_running_like(status),
        "total": len(items),
        "concurrency": job["concurrency"],
        "experiment_rounds": job["experiment_rounds"],
        "current_round": job["current_round"],
        "pending": counts.get(ITEM_STATUS_PENDING, 0),
        "running": counts.get(ITEM_STATUS_RUNNING, 0),
        "done": counts.get(ITEM_STATUS_DONE, 0),
        "error": counts.get(ITEM_STATUS_ERROR, 0),
        "cancelled": counts.get(ITEM_STATUS_STOPPED, 0),
        "stopped": counts.get(ITEM_STATUS_STOPPED, 0),
        "stop_requested": job["stop_requested"],
        "tasks": [_item_to_api_payload(item) for item in items],
    }


def append_batch_job_event(
    job_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    """追加结构化事件。"""
    conn = conn or get_db()
    row = conn.execute(
        """
        INSERT INTO batch_job_events (job_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        RETURNING id
        """,
        (job_id, event_type, _dumps_json(payload), utcnow_iso()),
    ).fetchone()
    return int(row["id"])


def list_batch_job_events(job_id: str, after_id: int = 0, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """按事件 ID 顺序获取事件。"""
    conn = conn or get_db()
    rows = conn.execute(
        """
        SELECT id, event_type, payload_json, created_at
        FROM batch_job_events
        WHERE job_id = ? AND id > ?
        ORDER BY id ASC
        """,
        (job_id, after_id),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "payload": _loads_json(row["payload_json"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_last_event_id(job_id: str, *, conn: sqlite3.Connection | None = None) -> int:
    """获取 job 的最新事件 ID。"""
    conn = conn or get_db()
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS id FROM batch_job_events WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return int(row["id"]) if row is not None else 0


def get_active_job(*, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """返回当前活跃 job。"""
    conn = conn or get_db()
    row = conn.execute(
        """
        SELECT * FROM batch_jobs
        WHERE status IN ('queued', 'running', 'stopping')
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return _normalize_job(row)


def get_latest_job(*, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """返回最近一个 job。"""
    conn = conn or get_db()
    row = conn.execute(
        """
        SELECT * FROM batch_jobs
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    return _normalize_job(row)


def get_latest_resumable_job(*, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """获取最新 job 的可续跑状态。"""
    conn = conn or get_db()
    row = conn.execute(
        """
        SELECT * FROM batch_jobs
        ORDER BY created_at DESC
        LIMIT 1
        """
    ).fetchone()
    job = _normalize_job(row)
    if job is None or not _job_is_resumable(job["status"]):
        return None
    return job


def request_stop(job_id: str) -> dict[str, Any]:
    """请求停止 job。"""
    conn = get_db()
    job = get_batch_job(job_id, conn=conn)
    if job is None:
        raise RuntimeError(f"批量任务不存在: {job_id}")
    if not _job_is_running_like(job["status"]):
        raise RuntimeError("当前批量任务不在运行中")

    now = utcnow_iso()
    conn.execute(
        """
        UPDATE batch_jobs
        SET stop_requested = 1,
            status = CASE WHEN status = 'queued' THEN 'stopped' ELSE 'stopping' END,
            status_reason = ?,
            updated_at = ?
        WHERE job_id = ?
        """,
        (STATUS_REASON_STOP_REQUESTED, now, job_id),
    )
    if job["status"] == JOB_STATUS_QUEUED:
        conn.execute(
            """
            UPDATE batch_job_items
            SET status = ?, current_step = ?, updated_at = ?, finished_at = ?
            WHERE job_id = ? AND status = ?
            """,
            (ITEM_STATUS_STOPPED, "i18n:batch.stopped", now, now, job_id, ITEM_STATUS_PENDING),
        )
    payload = get_batch_job_status_payload(job_id, conn=conn)
    append_batch_job_event(job_id, "status", payload, conn=conn)
    conn.commit()
    return payload


def _job_can_resume(job: dict[str, Any]) -> bool:
    return _job_is_resumable(job["status"])


def resume_batch_job(job_id: str, concurrency_override: int | None = None) -> dict[str, Any]:
    """恢复已停止/中断的 job。"""
    conn = get_db()
    active = get_active_job(conn=conn)
    if active is not None and active["job_id"] != job_id:
        raise RuntimeError("已有批量任务正在运行")

    job = get_batch_job(job_id, conn=conn)
    if job is None:
        raise RuntimeError(f"批量任务不存在: {job_id}")
    if not _job_can_resume(job):
        raise RuntimeError("该批量任务不可继续")

    now = utcnow_iso()
    all_done = conn.execute(
        """
        SELECT COUNT(*) AS remaining
        FROM batch_job_items
        WHERE job_id = ? AND status != ?
        """,
        (job_id, ITEM_STATUS_DONE),
    ).fetchone()["remaining"] == 0
    next_round = job["current_round"]
    if all_done and job["current_round"] < job["experiment_rounds"]:
        next_round = job["current_round"] + 1
        conn.execute(
            """
            UPDATE batch_job_items
            SET status = ?, current_step = NULL, retry_count = 0, started_at = NULL,
                finished_at = NULL, updated_at = ?, last_error = NULL, result_task_id = NULL
            WHERE job_id = ?
            """,
            (ITEM_STATUS_PENDING, now, job_id),
        )
    else:
        conn.execute(
            """
            UPDATE batch_job_items
            SET status = ?, current_step = NULL, retry_count = 0, started_at = NULL,
                finished_at = NULL, updated_at = ?, last_error = NULL, result_task_id = NULL
            WHERE job_id = ? AND status IN (?, ?, ?)
            """,
            (
                ITEM_STATUS_PENDING,
                now,
                job_id,
                ITEM_STATUS_STOPPED,
                ITEM_STATUS_ERROR,
                ITEM_STATUS_RUNNING,
            ),
        )

    concurrency = _validate_concurrency(concurrency_override or job["concurrency"])
    conn.execute(
        """
        UPDATE batch_jobs
        SET status = ?, stop_requested = 0, owner_worker_id = '', lease_expires_at = NULL,
            run_token = '', finished_at = NULL, updated_at = ?, concurrency = ?, current_round = ?,
            last_error = NULL, status_reason = ?
        WHERE job_id = ?
        """,
        (JOB_STATUS_QUEUED, now, concurrency, next_round, STATUS_REASON_NORMAL, job_id),
    )
    payload = get_batch_job_status_payload(job_id, conn=conn)
    append_batch_job_event(job_id, "status", payload, conn=conn)
    conn.commit()
    return payload


def list_batch_sessions(*, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """列出所有历史 job。"""
    conn = conn or get_db()
    rows = conn.execute(
        """
        SELECT * FROM batch_jobs
        ORDER BY created_at DESC
        """
    ).fetchall()
    sessions: list[dict[str, Any]] = []
    for row in rows:
        job = _normalize_job(row)
        payload = get_batch_job_status_payload(job["job_id"], conn=conn)
        sessions.append({
            "session_id": job["job_id"],
            "job_id": job["job_id"],
            "created_at": job["created_at"],
            "last_updated": job["updated_at"],
            "total_tasks": payload["total"],
            "done": payload["done"],
            "error": payload["error"],
            "running": payload["running"],
            "pending": payload["pending"],
            "is_completed": job["status"] == JOB_STATUS_COMPLETED,
            "status": job["status"],
        })
    return sessions


def delete_batch_job(job_id: str) -> None:
    """删除指定 job。"""
    conn = get_db()
    conn.execute("DELETE FROM batch_job_events WHERE job_id = ?", (job_id,))
    deleted = conn.execute("DELETE FROM batch_jobs WHERE job_id = ?", (job_id,)).rowcount
    conn.commit()
    if deleted == 0:
        raise RuntimeError(f"会话 {job_id} 不存在")


def update_job_status(
    job_id: str,
    status: str,
    *,
    status_reason: str | object = _UNSET,
    stop_requested: bool | object = _UNSET,
    owner_worker_id: str | None | object = _UNSET,
    run_token: str | None | object = _UNSET,
    lease_expires_at: str | None | object = _UNSET,
    started_at: str | None | object = _UNSET,
    finished_at: str | None | object = _UNSET,
    current_round: int | None | object = _UNSET,
    last_error: str | None | object = _UNSET,
    round_runtime: dict[str, Any] | None | object = _UNSET,
    concurrency: int | None | object = _UNSET,
    append_event: bool = True,
    expected_owner_worker_id: str | None = None,
    expected_run_token: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """更新 job 状态并写事件。"""
    owns_connection = conn is None
    conn = conn or get_db()
    updates = ["status = ?", "updated_at = ?"]
    params: list[Any] = [status, utcnow_iso()]
    if status_reason is not _UNSET:
        updates.append("status_reason = ?")
        params.append(status_reason)
    if stop_requested is not _UNSET:
        updates.append("stop_requested = ?")
        params.append(1 if stop_requested else 0)
    if owner_worker_id is not _UNSET:
        updates.append("owner_worker_id = ?")
        params.append(owner_worker_id)
    if run_token is not _UNSET:
        updates.append("run_token = ?")
        params.append(run_token)
    if lease_expires_at is not _UNSET:
        updates.append("lease_expires_at = ?")
        params.append(lease_expires_at)
    if started_at is not _UNSET:
        updates.append("started_at = ?")
        params.append(started_at)
    if finished_at is not _UNSET:
        updates.append("finished_at = ?")
        params.append(finished_at)
    if current_round is not _UNSET:
        updates.append("current_round = ?")
        params.append(current_round)
    if last_error is not _UNSET:
        updates.append("last_error = ?")
        params.append(last_error)
    if round_runtime is not _UNSET:
        updates.append("round_runtime_json = ?")
        params.append(_dumps_json(round_runtime))
    if concurrency is not _UNSET:
        updates.append("concurrency = ?")
        params.append(concurrency)
    where = ["job_id = ?"]
    params.append(job_id)
    if expected_owner_worker_id is not None:
        where.append("owner_worker_id = ?")
        params.append(expected_owner_worker_id)
    if expected_run_token is not None:
        where.append("run_token = ?")
        params.append(expected_run_token)
    updated = conn.execute(
        f"UPDATE batch_jobs SET {', '.join(updates)} WHERE {' AND '.join(where)}",
        params,
    ).rowcount
    payload = get_batch_job_status_payload(job_id, conn=conn)
    if append_event and updated > 0:
        append_batch_job_event(job_id, "status", payload, conn=conn)
    if owns_connection:
        conn.commit()
    return payload


def update_job_runtime(
    job_id: str,
    round_runtime: dict[str, Any],
    *,
    expected_owner_worker_id: str | None = None,
    expected_run_token: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """仅更新 round_runtime_json。"""
    owns_connection = conn is None
    conn = conn or get_db()
    where = ["job_id = ?"]
    params: list[Any] = [_dumps_json(round_runtime), utcnow_iso(), job_id]
    if expected_owner_worker_id is not None:
        where.append("owner_worker_id = ?")
        params.append(expected_owner_worker_id)
    if expected_run_token is not None:
        where.append("run_token = ?")
        params.append(expected_run_token)
    conn.execute(
        f"UPDATE batch_jobs SET round_runtime_json = ?, updated_at = ? WHERE {' AND '.join(where)}",
        params,
    )
    if owns_connection:
        conn.commit()


def update_batch_item(
    job_id: str,
    item_id: str,
    *,
    status: str | object = _UNSET,
    current_step: str | None | object = _UNSET,
    retry_count: int | None | object = _UNSET,
    result_task_id: str | None | object = _UNSET,
    first_round_task_id: str | None | object = _UNSET,
    token_usage: dict[str, Any] | None | object = _UNSET,
    started_at: str | None | object = _UNSET,
    finished_at: str | None | object = _UNSET,
    last_error: str | None | object = _UNSET,
    append_event: bool = True,
    throttle_step_window_seconds: float | None = None,
    expected_owner_worker_id: str | None = None,
    expected_run_token: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """更新 item 状态并写事件。"""
    owns_connection = conn is None
    conn = conn or get_db()
    existing = get_batch_item(job_id, item_id, conn=conn)
    if (
        throttle_step_window_seconds is not None
        and status is _UNSET
        and current_step is not _UNSET
        and current_step == existing.get("current_step")
    ):
        return existing
    if (
        throttle_step_window_seconds is not None
        and status is _UNSET
        and current_step is not _UNSET
        and existing.get("current_step") is not None
        and current_step != existing.get("current_step")
    ):
        updated_at = existing.get("updated_at")
        if updated_at:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at)
            if elapsed.total_seconds() < throttle_step_window_seconds:
                return existing

    updates = ["updated_at = ?"]
    params: list[Any] = [utcnow_iso()]
    if status is not _UNSET:
        updates.append("status = ?")
        params.append(status)
    if current_step is not _UNSET:
        updates.append("current_step = ?")
        params.append(current_step)
    if retry_count is not _UNSET:
        updates.append("retry_count = ?")
        params.append(retry_count)
    if result_task_id is not _UNSET:
        updates.append("result_task_id = ?")
        params.append(result_task_id)
    if first_round_task_id is not _UNSET:
        updates.append("first_round_task_id = ?")
        params.append(first_round_task_id)
    if token_usage is not _UNSET:
        updates.append("token_usage_json = ?")
        params.append(_dumps_json(token_usage))
    if started_at is not _UNSET:
        updates.append("started_at = ?")
        params.append(started_at)
    if finished_at is not _UNSET:
        updates.append("finished_at = ?")
        params.append(finished_at)
    if last_error is not _UNSET:
        updates.append("last_error = ?")
        params.append(last_error)
    where = ["job_id = ?", "item_id = ?"]
    params.extend([job_id, item_id])
    if expected_owner_worker_id is not None:
        where.append(
            "EXISTS (SELECT 1 FROM batch_jobs WHERE batch_jobs.job_id = batch_job_items.job_id AND batch_jobs.owner_worker_id = ?)"
        )
        params.append(expected_owner_worker_id)
    if expected_run_token is not None:
        where.append(
            "EXISTS (SELECT 1 FROM batch_jobs WHERE batch_jobs.job_id = batch_job_items.job_id AND batch_jobs.run_token = ?)"
        )
        params.append(expected_run_token)
    updated = conn.execute(
        f"""
        UPDATE batch_job_items
        SET {', '.join(updates)}
        WHERE {' AND '.join(where)}
        """,
        params,
    ).rowcount
    item = get_batch_item(job_id, item_id, conn=conn)
    payload = {"type": "task_update", "task": _item_to_api_payload(item)}
    if append_event and updated > 0:
        append_batch_job_event(job_id, "task_update", payload, conn=conn)
    if owns_connection:
        conn.commit()
    return item


def get_batch_item(job_id: str, item_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """获取单个 item。"""
    conn = conn or get_db()
    row = conn.execute(
        """
        SELECT * FROM batch_job_items
        WHERE job_id = ? AND item_id = ?
        """,
        (job_id, item_id),
    ).fetchone()
    item = _normalize_item(row)
    if item is None:
        raise RuntimeError(f"批量任务条目不存在: {job_id}/{item_id}")
    return item


def claim_next_batch_job(worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
    """领取下一个可执行的 job。"""
    conn = get_db()
    init_batch_job_tables(conn)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    lease_iso = (now + timedelta(seconds=lease_seconds)).isoformat()
    row = conn.execute(
        """
        SELECT job_id
        FROM batch_jobs
        WHERE status = ?
          AND (owner_worker_id = '' OR owner_worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at < ?)
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (JOB_STATUS_QUEUED, now_iso),
    ).fetchone()
    if row is None:
        return None
    job_id = row["job_id"]
    run_token = f"run-{uuid4().hex[:16]}"
    updated = conn.execute(
        """
        UPDATE batch_jobs
        SET owner_worker_id = ?, run_token = ?, lease_expires_at = ?, status = ?, status_reason = ?, updated_at = ?
        WHERE job_id = ?
          AND status = ?
          AND (owner_worker_id = '' OR owner_worker_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at < ?)
        """,
        (worker_id, run_token, lease_iso, JOB_STATUS_RUNNING, STATUS_REASON_NORMAL, now_iso, job_id, JOB_STATUS_QUEUED, now_iso),
    ).rowcount
    if updated == 0:
        conn.commit()
        return None
    payload = get_batch_job_status_payload(job_id, conn=conn)
    append_batch_job_event(job_id, "status", payload, conn=conn)
    conn.commit()
    return get_batch_job(job_id, conn=conn)


def renew_batch_job_lease(job_id: str, worker_id: str, run_token: str, lease_seconds: int) -> bool:
    """续租 job。"""
    conn = get_db()
    lease_iso = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
    updated = conn.execute(
        """
        UPDATE batch_jobs
        SET lease_expires_at = ?, updated_at = ?
        WHERE job_id = ? AND owner_worker_id = ? AND run_token = ?
        """,
        (lease_iso, utcnow_iso(), job_id, worker_id, run_token),
    ).rowcount
    conn.commit()
    return updated > 0


def clear_batch_job_owner(
    job_id: str,
    worker_id: str,
    *,
    run_token: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """清理 owner。"""
    owns_connection = conn is None
    conn = conn or get_db()
    where = ["job_id = ?", "owner_worker_id = ?"]
    params: list[Any] = [utcnow_iso(), job_id, worker_id]
    if run_token is not None:
        where.append("run_token = ?")
        params.append(run_token)
    conn.execute(
        f"""
        UPDATE batch_jobs
        SET owner_worker_id = '', lease_expires_at = NULL, updated_at = ?
        WHERE {' AND '.join(where)}
        """,
        params,
    )
    if owns_connection:
        conn.commit()


def _set_running_items_to(job_id: str, status: str, step: str | None, *, conn: sqlite3.Connection) -> None:
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE batch_job_items
        SET status = ?, current_step = ?, updated_at = ?, finished_at = ?, started_at = CASE WHEN ? = 'pending' THEN NULL ELSE started_at END
        WHERE job_id = ? AND status = ?
        """,
        (status, step, now, now if status in {ITEM_STATUS_STOPPED, ITEM_STATUS_ERROR} else None, status, job_id, ITEM_STATUS_RUNNING),
    )


def recover_stale_batch_jobs() -> list[str]:
    """恢复租约过期的 job。"""
    conn = get_db()
    init_batch_job_tables(conn)
    recovery_grace_seconds = get_settings().app.batch_worker_recovery_grace_seconds
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=recovery_grace_seconds)
    stale_before_iso = stale_before.isoformat()
    rows = conn.execute(
        """
        SELECT job_id, stop_requested
        FROM batch_jobs
        WHERE status IN ('running', 'stopping')
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at < ?
        """,
        (stale_before_iso,),
    ).fetchall()
    recovered: list[str] = []
    for row in rows:
        job_id = row["job_id"]
        stop_requested = bool(row["stop_requested"])
        final_status = JOB_STATUS_STOPPED if stop_requested else JOB_STATUS_INTERRUPTED
        replacement_item_status = ITEM_STATUS_STOPPED if stop_requested else ITEM_STATUS_PENDING
        step = "i18n:batch.serviceStopWaiting" if stop_requested else "i18n:batch.workerInterrupted"
        now = utcnow_iso()
        conn.execute(
            """
            UPDATE batch_jobs
            SET status = ?, status_reason = ?, owner_worker_id = '', run_token = '', lease_expires_at = NULL, finished_at = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (final_status, STATUS_REASON_LEASE_EXPIRED, now, now, job_id),
        )
        conn.execute(
            """
            UPDATE batch_job_items
            SET status = ?, current_step = ?, updated_at = ?, finished_at = ?, started_at = CASE WHEN ? = 'pending' THEN NULL ELSE started_at END
            WHERE job_id = ? AND status = ?
            """,
            (replacement_item_status, step, now, now, replacement_item_status, job_id, ITEM_STATUS_RUNNING),
        )
        payload = get_batch_job_status_payload(job_id, conn=conn)
        append_batch_job_event(job_id, "status", payload, conn=conn)
        recovered.append(job_id)
    conn.commit()
    return recovered


def get_stop_requested(job_id: str, *, conn: sqlite3.Connection | None = None) -> bool:
    """读取 stop_requested。"""
    conn = conn or get_db()
    row = conn.execute(
        "SELECT stop_requested FROM batch_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return bool(row["stop_requested"]) if row is not None else False
