"""基于 SQLite 的 MCP 单 PDF 作业队列。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from web.backend.app.database import DB_PATH, configure_sqlite_connection

from errors import ERROR_SPECS, MCPErrorCode, MCPJobError

QUEUED = "queued"
RUNNING = "running"
STOPPING = "stopping"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL_STATUSES = {COMPLETED, FAILED, CANCELLED}

class MCPJobRepository:
    """持久化 MCP 作业并提供带租约的 FIFO 领取。"""

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """打开一个短生命周期 WAL 连接。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        """幂等创建 MCP 作业表和查询索引。"""
        with self._connection() as conn:
            configure_sqlite_connection(conn)
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_jobs (
                    job_id              TEXT PRIMARY KEY,
                    idempotency_key     TEXT NOT NULL UNIQUE,
                    analysis_engine     TEXT NOT NULL
                                        CHECK(analysis_engine IN ('llm', 'rule', 'hybrid')),
                    status              TEXT NOT NULL
                                        CHECK(status IN ('queued', 'running', 'stopping', 'completed', 'failed', 'cancelled')),
                    phase               TEXT NOT NULL DEFAULT 'queued',
                    original_filename   TEXT NOT NULL,
                    upload_id           TEXT,
                    input_path          TEXT NOT NULL,
                    input_sha256        TEXT NOT NULL,
                    input_bytes         INTEGER NOT NULL,
                    page_count          INTEGER NOT NULL,
                    owner_worker_id     TEXT NOT NULL DEFAULT '',
                    run_token           TEXT NOT NULL DEFAULT '',
                    lease_expires_at    TEXT,
                    stop_requested      INTEGER NOT NULL DEFAULT 0,
                    recovery_count      INTEGER NOT NULL DEFAULT 0,
                    progress_completed  INTEGER NOT NULL DEFAULT 0,
                    progress_total      INTEGER NOT NULL DEFAULT 23,
                    result_task_id      TEXT,
                    result_dir          TEXT,
                    token_usage_json    TEXT NOT NULL DEFAULT '{}',
                    last_error          TEXT,
                    last_error_code     TEXT,
                    last_error_detail   TEXT,
                    input_deleted_at    TEXT,
                    stale_runs_deleted_at TEXT,
                    output_deleted_at   TEXT,
                    result_expired_at   TEXT,
                    created_at          TEXT NOT NULL,
                    started_at          TEXT,
                    finished_at         TEXT,
                    updated_at          TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_mcp_jobs_status_created
                    ON mcp_jobs(status, created_at, job_id);
                CREATE INDEX IF NOT EXISTS idx_mcp_jobs_lease
                    ON mcp_jobs(lease_expires_at);
                CREATE TABLE IF NOT EXISTS mcp_uploads (
                    upload_id            TEXT PRIMARY KEY,
                    original_filename    TEXT NOT NULL,
                    stored_path          TEXT NOT NULL,
                    input_sha256         TEXT NOT NULL,
                    input_bytes          INTEGER NOT NULL,
                    page_count           INTEGER NOT NULL,
                    created_at           TEXT NOT NULL,
                    expires_at           TEXT NOT NULL,
                    consumed_job_id      TEXT,
                    consumed_at          TEXT,
                    file_deleted_at      TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_mcp_uploads_cleanup
                    ON mcp_uploads(file_deleted_at, expires_at, consumed_at);
                """
            )
            conn.execute("BEGIN IMMEDIATE")
            existing_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(mcp_jobs)").fetchall()
            }
            if "analysis_engine" not in existing_columns:
                conn.execute("ALTER TABLE mcp_jobs ADD COLUMN analysis_engine TEXT")
            for name in (
                "upload_id",
                "last_error_code",
                "last_error_detail",
                "input_deleted_at",
                "stale_runs_deleted_at",
                "output_deleted_at",
                "result_expired_at",
            ):
                if name not in existing_columns:
                    conn.execute(f"ALTER TABLE mcp_jobs ADD COLUMN {name} TEXT")
            conn.commit()

    @staticmethod
    def _now() -> str:
        """返回可排序的 UTC ISO 时间。"""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """将数据库行转换为业务字典。"""
        if row is None:
            return None
        job = dict(row)
        job["stop_requested"] = bool(job["stop_requested"])
        try:
            job["token_usage"] = json.loads(job.pop("token_usage_json") or "{}")
        except json.JSONDecodeError:
            job["token_usage"] = {}
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        """按 ID 查询作业。"""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM mcp_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._normalize(row)

    def get_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        """按幂等键查询作业。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_jobs WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        return self._normalize(row)

    def create_upload(
        self,
        *,
        upload_id: str,
        original_filename: str,
        stored_path: str,
        input_sha256: str,
        input_bytes: int,
        page_count: int,
        retention_hours: int,
    ) -> dict[str, Any]:
        """保存一个已完成校验的短期 PDF 上传记录。"""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires_at = (now + timedelta(hours=retention_hours)).isoformat()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO mcp_uploads (
                    upload_id, original_filename, stored_path, input_sha256,
                    input_bytes, page_count, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload_id,
                    original_filename,
                    stored_path,
                    input_sha256,
                    input_bytes,
                    page_count,
                    now_iso,
                    expires_at,
                ),
            )
            conn.commit()
        upload = self.get_upload(upload_id)
        if upload is None:
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR)
        return upload

    def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        """按上传 ID 查询短期文件元数据。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM mcp_uploads WHERE upload_id = ?",
                (upload_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def create_job(
        self,
        *,
        job_id: str,
        idempotency_key: str,
        original_filename: str,
        input_path: str,
        input_sha256: str,
        input_bytes: int,
        page_count: int,
        upload_id: str,
        max_queued_jobs: int,
        analysis_engine: str = "hybrid",
    ) -> tuple[dict[str, Any], bool]:
        """原子检查幂等性和队列容量后创建作业。"""
        now = self._now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM mcp_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                conn.commit()
                normalized = self._normalize(existing)
                if normalized and (
                    normalized["input_sha256"] != input_sha256
                    or normalized.get("analysis_engine") not in (None, analysis_engine)
                ):
                    raise MCPJobError(MCPErrorCode.IDEMPOTENCY_CONFLICT)
                return normalized or {}, False

            upload = conn.execute(
                "SELECT consumed_job_id, expires_at FROM mcp_uploads WHERE upload_id = ?",
                (upload_id,),
            ).fetchone()
            if upload is None:
                conn.rollback()
                raise MCPJobError(MCPErrorCode.UPLOAD_NOT_FOUND)
            if upload["expires_at"] <= now:
                conn.rollback()
                raise MCPJobError(MCPErrorCode.UPLOAD_EXPIRED)
            if upload["consumed_job_id"]:
                conn.rollback()
                raise MCPJobError(MCPErrorCode.UPLOAD_ALREADY_USED)

            queued_count = conn.execute(
                "SELECT COUNT(*) FROM mcp_jobs WHERE status = ?",
                (QUEUED,),
            ).fetchone()[0]
            if queued_count >= max_queued_jobs:
                conn.rollback()
                raise MCPJobError(MCPErrorCode.QUEUE_FULL)

            conn.execute(
                """
                INSERT INTO mcp_jobs (
                    job_id, idempotency_key, analysis_engine, status, phase, original_filename, upload_id,
                    input_path, input_sha256, input_bytes, page_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    analysis_engine,
                    QUEUED,
                    QUEUED,
                    original_filename,
                    upload_id,
                    input_path,
                    input_sha256,
                    input_bytes,
                    page_count,
                    now,
                    now,
                ),
            )
            consumed = conn.execute(
                """
                UPDATE mcp_uploads
                SET consumed_job_id = ?, consumed_at = ?
                WHERE upload_id = ? AND consumed_job_id IS NULL AND expires_at > ?
                """,
                (job_id, now, upload_id, now),
            ).rowcount
            if consumed != 1:
                conn.rollback()
                raise MCPJobError(MCPErrorCode.UPLOAD_ALREADY_USED)
            conn.commit()
        created = self.get_job(job_id)
        if created is None:
            raise MCPJobError(MCPErrorCode.JOB_CREATE_FAILED)
        return created, True

    def list_upload_cleanup_candidates(
        self,
        now: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """返回已消费或过期且仍有暂存文件的上传记录。"""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mcp_uploads
                WHERE file_deleted_at IS NULL
                  AND (consumed_at IS NOT NULL OR expires_at <= ?)
                ORDER BY COALESCE(consumed_at, expires_at), upload_id
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_upload_file_deleted(self, upload_id: str) -> None:
        """记录上传暂存文件已经清理。"""
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE mcp_uploads
                SET file_deleted_at = COALESCE(file_deleted_at, ?)
                WHERE upload_id = ?
                """,
                (self._now(), upload_id),
            )
            conn.commit()

    def queue_position(self, job: dict[str, Any]) -> int | None:
        """返回从 1 开始的近似 FIFO 排队位置。"""
        if job["status"] != QUEUED:
            return None
        with self._connection() as conn:
            ahead = conn.execute(
                """
                SELECT COUNT(*) FROM mcp_jobs
                WHERE status = ?
                  AND (created_at < ? OR (created_at = ? AND job_id < ?))
                """,
                (QUEUED, job["created_at"], job["created_at"], job["job_id"]),
            ).fetchone()[0]
        return int(ahead) + 1

    def claim_next(
        self,
        worker_id: str,
        lease_seconds: int,
        max_active_jobs: int = 4,
    ) -> dict[str, Any] | None:
        """在数据库全局容量内领取最早的排队作业。"""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._has_active_web_batch(conn):
                conn.commit()
                return None
            active_count = conn.execute(
                """
                SELECT COUNT(*) FROM mcp_jobs
                WHERE status IN (?, ?)
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at >= ?
                """,
                (RUNNING, STOPPING, now_iso),
            ).fetchone()[0]
            if active_count >= max_active_jobs:
                conn.commit()
                return None
            row = conn.execute(
                "SELECT job_id FROM mcp_jobs WHERE status = ? ORDER BY created_at, job_id LIMIT 1",
                (QUEUED,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            run_token = f"mcp-run-{uuid4().hex}"
            updated = conn.execute(
                """
                UPDATE mcp_jobs
                SET status = ?, phase = 'initializing', owner_worker_id = ?, run_token = ?,
                    lease_expires_at = ?, started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (RUNNING, worker_id, run_token, lease_expires, now_iso, now_iso, row["job_id"], QUEUED),
            ).rowcount
            conn.commit()
        return self.get_job(row["job_id"]) if updated == 1 else None

    @staticmethod
    def _has_active_web_batch(conn: sqlite3.Connection) -> bool:
        """检查同库 Web 批量作业，旧库未建表时视为没有。"""
        try:
            row = conn.execute(
                """
                SELECT 1 FROM batch_jobs
                WHERE status IN ('queued', 'running', 'stopping') LIMIT 1
                """
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return False
        return row is not None

    def renew_lease(self, job_id: str, worker_id: str, run_token: str, lease_seconds: int) -> bool:
        """续租当前 Worker 持有的运行作业。"""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._connection() as conn:
            updated = conn.execute(
                """
                UPDATE mcp_jobs SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND owner_worker_id = ? AND run_token = ?
                  AND status IN (?, ?)
                  AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                """,
                (
                    lease_expires,
                    now_iso,
                    job_id,
                    worker_id,
                    run_token,
                    RUNNING,
                    STOPPING,
                    now_iso,
                ),
            ).rowcount
            conn.commit()
        return updated == 1

    def update_progress(
        self,
        job_id: str,
        worker_id: str,
        run_token: str,
        *,
        phase: str,
        completed: int | None = None,
        total: int | None = None,
    ) -> bool:
        """在租约防护下更新阶段和条目进度。"""
        updates = ["phase = ?", "updated_at = ?"]
        params: list[Any] = [phase, self._now()]
        if completed is not None:
            updates.append("progress_completed = ?")
            params.append(max(0, completed))
        if total is not None:
            updates.append("progress_total = ?")
            params.append(max(0, total))
        params.extend([job_id, worker_id, run_token, RUNNING, STOPPING])
        with self._connection() as conn:
            updated = conn.execute(
                f"""
                UPDATE mcp_jobs SET {', '.join(updates)}
                WHERE job_id = ? AND owner_worker_id = ? AND run_token = ?
                  AND status IN (?, ?)
                """,
                params,
            ).rowcount
            conn.commit()
        return updated == 1

    def stop_requested(self, job_id: str) -> bool:
        """检查作业是否收到取消请求。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT stop_requested FROM mcp_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return bool(row and row[0])

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        """取消排队作业或请求运行作业协作式停止。"""
        now = self._now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM mcp_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                conn.rollback()
                raise MCPJobError(MCPErrorCode.JOB_NOT_FOUND)
            status = row["status"]
            if status in TERMINAL_STATUSES:
                conn.commit()
                job = self.get_job(job_id)
                return job or {}
            if status == QUEUED:
                conn.execute(
                    """
                    UPDATE mcp_jobs SET status = ?, phase = ?, stop_requested = 1,
                        finished_at = ?, updated_at = ? WHERE job_id = ? AND status = ?
                    """,
                    (CANCELLED, CANCELLED, now, now, job_id, QUEUED),
                )
            else:
                conn.execute(
                    """
                    UPDATE mcp_jobs SET status = ?, phase = ?, stop_requested = 1, updated_at = ?
                    WHERE job_id = ? AND status IN (?, ?)
                    """,
                    (STOPPING, STOPPING, now, job_id, RUNNING, STOPPING),
                )
            conn.commit()
        return self.get_job(job_id) or {}

    def complete_job(
        self,
        job_id: str,
        worker_id: str,
        run_token: str,
        *,
        result_task_id: str,
        result_dir: str,
        token_usage: dict[str, Any],
    ) -> bool:
        """发布已校验的最终结果。"""
        now = self._now()
        with self._connection() as conn:
            updated = conn.execute(
                """
                UPDATE mcp_jobs
                SET status = ?, phase = ?, result_task_id = ?, result_dir = ?,
                    token_usage_json = ?, finished_at = ?, updated_at = ?,
                    owner_worker_id = '', run_token = '', lease_expires_at = NULL,
                    last_error = NULL, last_error_code = NULL, last_error_detail = NULL
                WHERE job_id = ? AND owner_worker_id = ? AND run_token = ?
                  AND status = ? AND stop_requested = 0
                  AND lease_expires_at IS NOT NULL AND lease_expires_at >= ?
                """,
                (
                    COMPLETED,
                    COMPLETED,
                    result_task_id,
                    result_dir,
                    json.dumps(token_usage, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    job_id,
                    worker_id,
                    run_token,
                    RUNNING,
                    now,
                ),
            ).rowcount
            conn.commit()
        return updated == 1

    def finish_unsuccessful(
        self,
        job_id: str,
        worker_id: str,
        run_token: str,
        *,
        error_code: MCPErrorCode,
        error_detail: str = "",
        interrupted: bool = False,
        recovery_limit: int = 1,
        lease_expired_before: str | None = None,
    ) -> bool:
        """将失败、取消或中断作业安全地转换到下一状态。"""
        now = self._now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            query = """
                SELECT status, stop_requested, recovery_count FROM mcp_jobs
                WHERE job_id = ? AND owner_worker_id = ? AND run_token = ?
            """
            params: list[Any] = [job_id, worker_id, run_token]
            if lease_expired_before is not None:
                query += (
                    " AND status IN (?, ?)"
                    " AND lease_expires_at IS NOT NULL AND lease_expires_at < ?"
                )
                params.extend([RUNNING, STOPPING, lease_expired_before])
            row = conn.execute(query, params).fetchone()
            if row is None:
                conn.rollback()
                return False
            if row["stop_requested"]:
                status, phase, finished_at, next_recovery = CANCELLED, CANCELLED, now, row["recovery_count"]
            elif interrupted and row["recovery_count"] < recovery_limit:
                status, phase, finished_at = QUEUED, QUEUED, None
                next_recovery = row["recovery_count"] + 1
            else:
                status, phase, finished_at, next_recovery = FAILED, FAILED, now, row["recovery_count"]
            conn.execute(
                """
                UPDATE mcp_jobs
                SET status = ?, phase = ?, last_error = ?, last_error_code = ?,
                    last_error_detail = ?, recovery_count = ?,
                    finished_at = ?, updated_at = ?, owner_worker_id = '', run_token = '',
                    lease_expires_at = NULL
                WHERE job_id = ? AND owner_worker_id = ? AND run_token = ?
                """,
                (
                    status,
                    phase,
                    ERROR_SPECS[error_code].message,
                    error_code.value,
                    error_detail[:4000],
                    next_recovery,
                    finished_at,
                    now,
                    job_id,
                    worker_id,
                    run_token,
                ),
            )
            conn.commit()
        return True

    def recover_stale(self, recovery_limit: int) -> list[str]:
        """回收租约过期作业；每个作业最多自动重排指定次数。"""
        now = self._now()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT job_id, owner_worker_id, run_token FROM mcp_jobs
                WHERE status IN (?, ?) AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                """,
                (RUNNING, STOPPING, now),
            ).fetchall()
        recovered: list[str] = []
        for row in rows:
            if self.finish_unsuccessful(
                row["job_id"],
                row["owner_worker_id"],
                row["run_token"],
                error_code=MCPErrorCode.WORKER_INTERRUPTED,
                error_detail="MCP Worker 租约过期",
                interrupted=True,
                recovery_limit=recovery_limit,
                lease_expired_before=now,
            ):
                recovered.append(row["job_id"])
        return recovered

    def list_cleanup_candidates(
        self,
        *,
        input_before: str,
        incomplete_before: str,
        result_before: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """返回已到期且仍有文件待清理的终态作业。"""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mcp_jobs
                WHERE finished_at IS NOT NULL
                  AND status IN (?, ?, ?)
                  AND (
                    (input_deleted_at IS NULL AND finished_at < ?)
                    OR (stale_runs_deleted_at IS NULL AND status = ? AND finished_at < ?)
                    OR (output_deleted_at IS NULL AND status IN (?, ?) AND finished_at < ?)
                    OR (output_deleted_at IS NULL AND status = ? AND finished_at < ?)
                  )
                ORDER BY finished_at, job_id
                LIMIT ?
                """,
                (
                    COMPLETED,
                    FAILED,
                    CANCELLED,
                    input_before,
                    COMPLETED,
                    incomplete_before,
                    FAILED,
                    CANCELLED,
                    incomplete_before,
                    COMPLETED,
                    result_before,
                    limit,
                ),
            ).fetchall()
        return [job for row in rows if (job := self._normalize(row)) is not None]

    def mark_artifacts_deleted(
        self,
        job_id: str,
        *,
        input_deleted: bool = False,
        stale_runs_deleted: bool = False,
        output_deleted: bool = False,
    ) -> None:
        """记录已完成的文件清理；完成任务输出删除时同步标记结果过期。"""
        now = self._now()
        updates: list[str] = []
        params: list[Any] = []
        if input_deleted:
            updates.append("input_deleted_at = COALESCE(input_deleted_at, ?)")
            params.append(now)
        if stale_runs_deleted:
            updates.append("stale_runs_deleted_at = COALESCE(stale_runs_deleted_at, ?)")
            params.append(now)
        if output_deleted:
            updates.extend(
                [
                    "output_deleted_at = COALESCE(output_deleted_at, ?)",
                    "stale_runs_deleted_at = COALESCE(stale_runs_deleted_at, ?)",
                    (
                        "result_expired_at = CASE WHEN status = ? "
                        "THEN COALESCE(result_expired_at, ?) ELSE result_expired_at END"
                    ),
                ]
            )
            params.extend([now, now, COMPLETED, now])
        if not updates:
            return
        params.append(job_id)
        with self._connection() as conn:
            conn.execute(
                f"UPDATE mcp_jobs SET {', '.join(updates)}, updated_at = ? WHERE job_id = ?",
                [*params[:-1], now, params[-1]],
            )
            conn.commit()
