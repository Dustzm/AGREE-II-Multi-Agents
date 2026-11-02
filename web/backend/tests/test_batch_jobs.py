from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from core.utils.cancellation import CancellationToken
from core.utils.token_tracker import TokenUsageTracker
from web.backend.app import database as auth_db
from web.backend.app.api.routes import batch as batch_route
from core.config import refresh_settings
from web.backend.app.services import batch_jobs
from web.backend.app import batch_worker
from web.backend.app.services import batch_worker_runner
from web.backend.app.services.batch_identity import batch_run_task_id
from web.backend.app.services.batch_worker_runner import (
    BatchJobRunner,
    _build_batch_usage_summary,
    _elapsed_seconds_from_iso,
    _format_elapsed_time_desc,
    _format_batch_item_progress_step,
    _token_usage_payload,
)


@pytest.fixture
def isolated_batch_db(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_db, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth_db, "DB_DIR", tmp_path / "data")
    monkeypatch.setattr(auth_db, "DB_PATH", tmp_path / "data" / "ama.db")
    auth_db.close_db()
    auth_db.init_db()
    yield tmp_path
    auth_db.close_db()


def _create_pdf(tmp_path, name: str) -> str:
    pdf_path = tmp_path / name
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    return str(pdf_path)


def test_create_batch_job_persists_items(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "a.pdf")
    pdf2 = _create_pdf(isolated_batch_db, "b.pdf")

    payload = batch_jobs.create_batch_job([pdf1, pdf2], concurrency=3, experiment_rounds=2)

    assert payload["job_id"]
    assert payload["status"] == batch_jobs.JOB_STATUS_QUEUED
    assert payload["is_running"] is True
    assert payload["total"] == 2
    assert payload["pending"] == 2


def test_batch_item_persists_current_round_task_id(isolated_batch_db):
    pdf = _create_pdf(isolated_batch_db, "representative-guide.pdf")
    created = batch_jobs.create_batch_job([pdf], concurrency=1, experiment_rounds=1)
    item = batch_jobs.list_batch_job_items(created["job_id"])[0]
    batch_jobs.update_batch_item(created["job_id"], item["item_id"], active_task_id="representative-guide-run-1")
    assert batch_jobs.get_batch_item(created["job_id"], item["item_id"])["active_task_id"] == "representative-guide-run-1"
    assert batch_jobs.get_batch_job_status_payload(created["job_id"])["tasks"][0]["active_task_id"] == "representative-guide-run-1"


def test_batch_retry_resumes_same_task_directory(isolated_batch_db, monkeypatch):
    pdf = _create_pdf(isolated_batch_db, "representative-guide.pdf")
    created = batch_jobs.create_batch_job([pdf], concurrency=1, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    item = batch_jobs.list_batch_job_items(created["job_id"])[0]
    calls = []

    class FakeAppraisalRunner:
        def __init__(self, **callbacks):
            self.callbacks = callbacks

        async def run(self, task_id, pdf_path, *, reuse_task_id=None, resume=False, reuse_output_subdir=None):
            calls.append((task_id, resume))
            self.callbacks["on_task_ready"](task_id)
            if len(calls) == 1:
                self.callbacks["on_partial"](task_id, ["item_21"], {"item_21": {"type": "Timeout", "stage": "dynamic_scoring", "message": "timeout"}})
            else:
                self.callbacks["on_complete"](task_id)

        def get_token_usage(self):
            return {}

        def get_status_reason(self):
            return "normal"

    monkeypatch.setattr(batch_worker_runner, "AppraisalRunner", FakeAppraisalRunner)
    runner = BatchJobRunner(created["job_id"], "worker-1", claimed["run_token"], CancellationToken(), None)
    asyncio.run(runner._run_single_item(item, 1, "batch_representative"))

    expected_task_id = batch_run_task_id(created["job_id"], 1, item["item_id"])
    assert calls == [(expected_task_id, False), (expected_task_id, True)]
    saved = batch_jobs.get_batch_item(created["job_id"], item["item_id"])
    assert saved["active_task_id"] == expected_task_id
    assert saved["status"] == batch_jobs.ITEM_STATUS_DONE
    assert saved["report_status"] == batch_jobs.REPORT_STATUS_COMPLETE


def test_partial_report_status_is_persisted_without_parsing_error_text(isolated_batch_db, monkeypatch):
    pdf = _create_pdf(isolated_batch_db, "partial-guide.pdf")
    created = batch_jobs.create_batch_job([pdf], concurrency=1, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    item = batch_jobs.list_batch_job_items(created["job_id"])[0]

    class PartialAppraisalRunner:
        def __init__(self, **callbacks):
            self.callbacks = callbacks

        async def run(self, task_id, pdf_path, **kwargs):
            self.callbacks["on_task_ready"](task_id)
            self.callbacks["on_partial"](task_id, ["item_21"], {"item_21": {"type": "TimeoutError"}})

        def get_token_usage(self):
            return {}

        def get_status_reason(self):
            return "normal"

    monkeypatch.setattr(batch_worker_runner, "AppraisalRunner", PartialAppraisalRunner)
    runner = BatchJobRunner(created["job_id"], "worker-1", claimed["run_token"], CancellationToken(), None)
    asyncio.run(runner._run_single_item(item, 1, "batch_partial"))

    saved = batch_jobs.get_batch_item(created["job_id"], item["item_id"])
    assert saved["status"] == batch_jobs.ITEM_STATUS_ERROR
    assert saved["report_status"] == batch_jobs.REPORT_STATUS_PARTIAL
    assert batch_jobs.get_batch_job_status_payload(created["job_id"])["tasks"][0]["report_status"] == "partial"


def test_retry_failure_preserves_existing_partial_report(isolated_batch_db, monkeypatch):
    pdf = _create_pdf(isolated_batch_db, "partial-then-error.pdf")
    calls = []

    class PartialThenErrorRunner:
        def __init__(self, **callbacks):
            self.callbacks = callbacks

        async def run(self, task_id, pdf_path, **kwargs):
            calls.append((task_id, kwargs.get("resume")))
            self.callbacks["on_task_ready"](task_id)
            if len(calls) == 1:
                self.callbacks["on_partial"](task_id, ["item_23"], {"item_23": {"type": "TimeoutError"}})
            else:
                self.callbacks["on_error"]("Reporter timed out")

        def get_status_reason(self):
            return "reporter_timeout"

        def get_token_usage(self):
            return {}

    created = batch_jobs.create_batch_job([pdf], concurrency=1, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    item = batch_jobs.list_batch_job_items(created["job_id"])[0]
    monkeypatch.setattr(batch_worker_runner, "AppraisalRunner", PartialThenErrorRunner)
    runner = BatchJobRunner(created["job_id"], "worker-1", claimed["run_token"], CancellationToken(), None)
    asyncio.run(runner._run_single_item(item, 1, "batch_partial"))
    saved = batch_jobs.get_batch_item(created["job_id"], item["item_id"])
    assert saved["status"] == batch_jobs.ITEM_STATUS_ERROR
    assert saved["report_status"] == batch_jobs.REPORT_STATUS_PARTIAL
    assert saved["result_task_id"] == calls[0][0]
    assert saved["last_error"] == "Reporter timed out"
    runner._reset_items_for_next_round(all_items=True)
    reset = batch_jobs.get_batch_item(created["job_id"], item["item_id"])
    assert reset["report_status"] == batch_jobs.REPORT_STATUS_NONE
    assert reset["result_task_id"] is None
    assert reset["active_task_id"] is None
    assert calls == [(calls[0][0], False), (calls[0][0], True)]


def test_same_stem_pdfs_have_unique_run_ids_in_every_round(isolated_batch_db):
    first_dir = isolated_batch_db / "first"
    second_dir = isolated_batch_db / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    paths = [_create_pdf(first_dir, "guide.pdf"), _create_pdf(second_dir, "guide.pdf")]
    created = batch_jobs.create_batch_job(paths, concurrency=1, experiment_rounds=2)
    items = batch_jobs.list_batch_job_items(created["job_id"])
    assert [item["item_id"] for item in items] == ["guide", "guide-2"]
    run_ids = {
        batch_run_task_id(created["job_id"], round_number, item["item_id"])
        for round_number in (1, 2)
        for item in items
    }
    assert len(run_ids) == 4


def test_create_batch_job_ignores_expired_mcp_lease(isolated_batch_db):
    """过期 MCP 运行记录不会永久阻塞 Web 批任务。"""
    conn = auth_db.get_db()
    conn.execute(
        """
        CREATE TABLE mcp_jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            lease_expires_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO mcp_jobs VALUES (?, ?, ?)",
        (
            "mcp-stale",
            "running",
            (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        ),
    )
    conn.commit()

    created = batch_jobs.create_batch_job(
        [_create_pdf(isolated_batch_db, "after-stale.pdf")],
        concurrency=1,
        experiment_rounds=1,
    )

    assert created["status"] == batch_jobs.JOB_STATUS_QUEUED


def test_create_batch_job_rolls_back_event_failure(isolated_batch_db, monkeypatch):
    """事件写入失败时回滚任务和条目，并释放写事务。"""
    conn = auth_db.get_db()

    def fail_event(*_args, **_kwargs):
        raise sqlite3.OperationalError("event write failed")

    monkeypatch.setattr(batch_jobs, "append_batch_job_event", fail_event)
    with pytest.raises(sqlite3.OperationalError, match="event write failed"):
        batch_jobs.create_batch_job(
            [_create_pdf(isolated_batch_db, "rollback.pdf")],
            concurrency=1,
            experiment_rounds=1,
        )

    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) FROM batch_jobs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM batch_job_items").fetchone()[0] == 0


def test_create_batch_job_accepts_one_hundred_documents(isolated_batch_db):
    pdf_paths = [
        _create_pdf(isolated_batch_db, f"guide-{index:03d}.pdf")
        for index in range(100)
    ]

    created = batch_jobs.create_batch_job(
        pdf_paths,
        concurrency=10,
        experiment_rounds=1,
    )
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert payload["total"] == 100
    assert payload["pending"] == 100
    assert payload["concurrency"] == 10


def test_create_batch_job_rejects_invalid_concurrency(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "invalid-concurrency.pdf")

    with pytest.raises(RuntimeError, match="Concurrency must be between") as caught:
        batch_jobs.create_batch_job([pdf1], concurrency=0, experiment_rounds=1)
    assert caught.value.i18n["key"] == "batch.invalid_concurrency"


def test_create_batch_job_rejects_invalid_experiment_rounds(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "invalid-rounds.pdf")

    with pytest.raises(RuntimeError, match="Experiment rounds must be between") as caught:
        batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=5)
    assert caught.value.i18n["key"] == "batch.invalid_rounds"


def test_claim_and_renew_batch_job_lease(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "claim.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)

    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)

    assert claimed is not None
    assert claimed["job_id"] == created["job_id"]
    assert claimed["owner_worker_id"] == "worker-1"
    assert claimed["run_token"]
    assert batch_jobs.renew_batch_job_lease(created["job_id"], "worker-1", claimed["run_token"], 20) is True


def test_sqlite_connection_uses_busy_timeout(isolated_batch_db):
    row = auth_db.get_db().execute("PRAGMA busy_timeout").fetchone()

    assert row[0] == 30000


def test_heartbeat_tolerates_transient_sqlite_failures(monkeypatch):
    token = CancellationToken()
    stop_event = threading.Event()
    calls = 0

    def fake_stop_requested(_job_id):
        return False

    def fake_renew(_job_id, _worker_id, _run_token, _lease_seconds):
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise sqlite3.OperationalError("database is locked")
        return True

    monkeypatch.setattr(batch_worker, "get_stop_requested", fake_stop_requested)
    monkeypatch.setattr(batch_worker, "renew_batch_job_lease", fake_renew)
    monkeypatch.setattr(batch_worker, "close_db", lambda: None)

    thread = threading.Thread(
        target=batch_worker._heartbeat_worker,
        args=("job-1", "worker-1", "run-token", token, stop_event),
        kwargs={
            "lease_seconds": 60,
            "heartbeat_seconds": 0.01,
            "max_failures": 3,
        },
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    while calls < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_event.set()
    thread.join(timeout=1.0)

    assert calls >= 3
    assert token.cancelled() is False


def test_heartbeat_cancels_after_consecutive_transient_failures(monkeypatch):
    token = CancellationToken()
    stop_event = threading.Event()
    calls = 0

    def fake_stop_requested(_job_id):
        return False

    def fake_renew(_job_id, _worker_id, _run_token, _lease_seconds):
        nonlocal calls
        calls += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(batch_worker, "get_stop_requested", fake_stop_requested)
    monkeypatch.setattr(batch_worker, "renew_batch_job_lease", fake_renew)
    monkeypatch.setattr(batch_worker, "close_db", lambda: None)

    thread = threading.Thread(
        target=batch_worker._heartbeat_worker,
        args=("job-1", "worker-1", "run-token", token, stop_event),
        kwargs={
            "lease_seconds": 60,
            "heartbeat_seconds": 0.01,
            "max_failures": 3,
        },
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    while not token.cancelled() and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_event.set()
    thread.join(timeout=1.0)

    assert calls == 3
    assert token.cancelled() is True
    assert token.reason == "heartbeat_failed"


def test_run_token_blocks_late_writes_after_resume(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "late-write.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    assert claimed is not None

    stale_token = claimed["run_token"]
    batch_jobs.request_stop(created["job_id"])
    expired = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_STOPPING,
        stop_requested=True,
        owner_worker_id="worker-1",
        lease_expires_at=expired,
        run_token=stale_token,
        expected_owner_worker_id="worker-1",
        expected_run_token=stale_token,
    )
    batch_jobs.recover_stale_batch_jobs()
    resumed = batch_jobs.resume_batch_job(created["job_id"])

    assert resumed["status"] == batch_jobs.JOB_STATUS_QUEUED

    batch_jobs.update_batch_item(
        created["job_id"],
        "late-write",
        status=batch_jobs.ITEM_STATUS_DONE,
        current_step="旧 worker 晚归",
        expected_owner_worker_id="worker-1",
        expected_run_token=stale_token,
    )

    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert payload["pending"] == 1
    assert payload["done"] == 0


def test_request_stop_marks_queued_items_stopped(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "stop.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)

    payload = batch_jobs.request_stop(created["job_id"])

    assert payload["status"] == batch_jobs.JOB_STATUS_STOPPED
    assert payload["stopped"] == 1
    assert payload["stop_requested"] is True


def test_recover_stale_running_job_to_interrupted(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "recover.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_RUNNING,
        owner_worker_id="worker-1",
        lease_expires_at=expired,
    )
    batch_jobs.update_batch_item(
        created["job_id"],
        "recover",
        status=batch_jobs.ITEM_STATUS_RUNNING,
        current_step="处理中",
    )

    recovered = batch_jobs.recover_stale_batch_jobs()
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert created["job_id"] in recovered
    assert payload["status"] == batch_jobs.JOB_STATUS_INTERRUPTED
    assert payload["pending"] == 1


def test_recover_stale_running_job_respects_grace_period(isolated_batch_db, monkeypatch):
    monkeypatch.setenv("BATCH_WORKER_RECOVERY_GRACE_SECONDS", "60")
    refresh_settings()
    pdf1 = _create_pdf(isolated_batch_db, "recover-grace.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    recently_expired = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_RUNNING,
        owner_worker_id="worker-1",
        lease_expires_at=recently_expired,
    )
    batch_jobs.update_batch_item(
        created["job_id"],
        "recover-grace",
        status=batch_jobs.ITEM_STATUS_RUNNING,
        current_step="处理中",
    )

    recovered = batch_jobs.recover_stale_batch_jobs()
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert recovered == []
    assert payload["status"] == batch_jobs.JOB_STATUS_RUNNING
    assert payload["running"] == 1
    refresh_settings()


def test_recover_stale_stopping_job_to_stopped(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "recover-stop.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_STOPPING,
        stop_requested=True,
        owner_worker_id="worker-1",
        lease_expires_at=expired,
    )
    batch_jobs.update_batch_item(
        created["job_id"],
        "recover-stop",
        status=batch_jobs.ITEM_STATUS_RUNNING,
        current_step="处理中",
    )

    recovered = batch_jobs.recover_stale_batch_jobs()
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert created["job_id"] in recovered
    assert payload["status"] == batch_jobs.JOB_STATUS_STOPPED
    assert payload["stopped"] == 1


def test_stale_recovery_does_not_clear_a_renewed_lease(isolated_batch_db, monkeypatch):
    """恢复扫描后发生的续租必须让旧快照的条件更新失效。"""
    pdf1 = _create_pdf(isolated_batch_db, "recover-race.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    conn = auth_db.get_db()
    expired = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    future_lease = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_RUNNING,
        owner_worker_id="worker-1",
        run_token=claimed["run_token"],
        lease_expires_at=expired,
    )

    class RenewBeforeRecoveryUpdate:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.renewed = False

        def execute(self, sql, parameters=()):
            if "AND COALESCE(owner_worker_id, '') = ?" in sql and not self.renewed:
                self.renewed = True
                self.wrapped.execute(
                    """
                    UPDATE batch_jobs
                    SET owner_worker_id = ?, run_token = ?, lease_expires_at = ?
                    WHERE job_id = ?
                    """,
                    ("worker-2", "fresh-token", future_lease, created["job_id"]),
                )
            return self.wrapped.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self.wrapped, name)

    proxy = RenewBeforeRecoveryUpdate(conn)
    monkeypatch.setattr(batch_jobs, "get_db", lambda: proxy)

    recovered = batch_jobs.recover_stale_batch_jobs()
    job = batch_jobs.get_batch_job(created["job_id"])

    assert recovered == []
    assert proxy.renewed is True
    assert job["status"] == batch_jobs.JOB_STATUS_RUNNING
    assert job["owner_worker_id"] == "worker-2"
    assert job["run_token"] == "fresh-token"
    assert job["lease_expires_at"] == future_lease


def test_recover_stopping_jobs_on_startup_preserves_active_lease(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "startup-stop.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.claim_next_batch_job("worker-1", lease_seconds=120)
    active_lease = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_STOPPING,
        status_reason=batch_jobs.STATUS_REASON_STOP_REQUESTED,
        stop_requested=True,
        owner_worker_id="worker-1",
        lease_expires_at=active_lease,
    )
    batch_jobs.update_batch_item(
        created["job_id"],
        "startup-stop",
        status=batch_jobs.ITEM_STATUS_RUNNING,
        current_step="处理中",
    )

    recovered = batch_jobs.recover_stopping_batch_jobs_on_startup()
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert recovered == []
    assert payload["status"] == batch_jobs.JOB_STATUS_STOPPING
    assert payload["stop_requested"] is True
    assert payload["running"] == 1


def test_recover_running_jobs_on_startup_preserves_active_lease(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "startup-running.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.claim_next_batch_job("worker-1", lease_seconds=120)
    batch_jobs.update_batch_item(
        created["job_id"],
        "startup-running",
        status=batch_jobs.ITEM_STATUS_RUNNING,
        current_step="处理中",
    )

    recovered = batch_jobs.recover_active_batch_jobs_on_startup()
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert recovered == []
    assert payload["status"] == batch_jobs.JOB_STATUS_RUNNING
    assert payload["stop_requested"] is False
    assert payload["running"] == 1


def test_recover_running_job_without_owner(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "startup-unowned.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_RUNNING,
        owner_worker_id="",
        run_token="",
        lease_expires_at=None,
    )
    batch_jobs.update_batch_item(
        created["job_id"],
        "startup-unowned",
        status=batch_jobs.ITEM_STATUS_RUNNING,
        current_step="处理中",
    )

    recovered = batch_jobs.recover_stale_batch_jobs()
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert recovered == [created["job_id"]]
    assert payload["status"] == batch_jobs.JOB_STATUS_INTERRUPTED
    assert payload["pending"] == 1
    assert payload["running"] == 0


def test_worker_cancellation_marks_job_interrupted_for_resume(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "worker-shutdown.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=120)
    token = CancellationToken()
    token.cancel()
    runner = BatchJobRunner(
        created["job_id"],
        "worker-1",
        claimed["run_token"],
        token,
        None,
    )

    asyncio.run(runner.run())
    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert payload["status"] == batch_jobs.JOB_STATUS_INTERRUPTED
    assert payload["stop_requested"] is False
    assert payload["pending"] == 1


def test_resume_batch_job_resets_stopped_items(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "resume.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=2)
    batch_jobs.request_stop(created["job_id"])

    payload = batch_jobs.resume_batch_job(created["job_id"], concurrency_override=4)

    assert payload["status"] == batch_jobs.JOB_STATUS_QUEUED
    assert payload["concurrency"] == 4
    assert payload["pending"] == 1
    assert payload["stopped"] == 0


@pytest.mark.parametrize("item_status", [batch_jobs.ITEM_STATUS_DONE, batch_jobs.ITEM_STATUS_ERROR])
def test_resume_preserves_task_id_only_within_same_round(isolated_batch_db, item_status):
    pdf = _create_pdf(isolated_batch_db, "guide.pdf")
    created = batch_jobs.create_batch_job([pdf], concurrency=1, experiment_rounds=2)
    job_id = created["job_id"]
    previous_task_id = batch_run_task_id(job_id, 1, "guide")
    batch_jobs.update_batch_item(
        job_id, "guide", status=item_status, active_task_id=previous_task_id,
        result_task_id=previous_task_id, first_round_task_id=previous_task_id,
        report_status="complete" if item_status == batch_jobs.ITEM_STATUS_DONE else "partial",
    )
    batch_jobs.update_job_status(job_id, batch_jobs.JOB_STATUS_INTERRUPTED, current_round=1)

    resumed = batch_jobs.resume_batch_job(job_id)
    expected_round = 2 if item_status == batch_jobs.ITEM_STATUS_DONE else 1
    assert resumed["current_round"] == expected_round
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    runner = BatchJobRunner(job_id, "worker-1", claimed["run_token"], CancellationToken(), None)
    assert runner._prepare_items_for_resume(expected_round, 2) == expected_round
    item = batch_jobs.get_batch_item(job_id, "guide")
    assert item["active_task_id"] == (None if expected_round == 2 else previous_task_id)
    assert item["result_task_id"] == (None if expected_round == 2 else previous_task_id)
    assert item["report_status"] == ("none" if expected_round == 2 else "partial")
    assert item["first_round_task_id"] == previous_task_id


def test_latest_resumable_job_prefers_latest_created_failed_job(isolated_batch_db):
    older_pdf = _create_pdf(isolated_batch_db, "older-stopped.pdf")
    newer_pdf = _create_pdf(isolated_batch_db, "newer-failed.pdf")
    older = batch_jobs.create_batch_job([older_pdf], concurrency=2, experiment_rounds=1)
    batch_jobs.request_stop(older["job_id"])
    newer = batch_jobs.create_batch_job([newer_pdf], concurrency=2, experiment_rounds=1)
    batch_jobs.update_job_status(
        newer["job_id"],
        batch_jobs.JOB_STATUS_FAILED,
        status_reason=batch_jobs.STATUS_REASON_WORKER_ERROR,
        last_error="worker failed",
    )
    batch_jobs.update_batch_item(
        newer["job_id"],
        "newer-failed",
        status=batch_jobs.ITEM_STATUS_ERROR,
        retry_count=2,
        last_error="item failed",
    )

    latest = batch_jobs.get_latest_resumable_job()

    assert latest is not None
    assert latest["job_id"] == newer["job_id"]


def test_latest_resumable_job_ignores_old_resumable_after_completed_job(isolated_batch_db):
    older_pdf = _create_pdf(isolated_batch_db, "older-stopped.pdf")
    newer_pdf = _create_pdf(isolated_batch_db, "newer-completed.pdf")
    older = batch_jobs.create_batch_job([older_pdf], concurrency=2, experiment_rounds=1)
    batch_jobs.request_stop(older["job_id"])
    newer = batch_jobs.create_batch_job([newer_pdf], concurrency=2, experiment_rounds=1)
    batch_jobs.update_batch_item(
        newer["job_id"],
        "newer-completed",
        status=batch_jobs.ITEM_STATUS_DONE,
    )
    batch_jobs.update_job_status(newer["job_id"], batch_jobs.JOB_STATUS_COMPLETED)

    latest = batch_jobs.get_latest_resumable_job()

    assert latest is None


def test_resume_batch_job_resets_error_items(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "resume-error.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_FAILED,
        status_reason=batch_jobs.STATUS_REASON_WORKER_ERROR,
        last_error="worker failed",
    )
    batch_jobs.update_batch_item(
        created["job_id"],
        "resume-error",
        status=batch_jobs.ITEM_STATUS_ERROR,
        current_step="处理失败",
        retry_count=3,
        result_task_id="task-old",
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=datetime.now(timezone.utc).isoformat(),
        last_error="item failed",
    )

    payload = batch_jobs.resume_batch_job(created["job_id"])
    item = payload["tasks"][0]

    assert payload["status"] == batch_jobs.JOB_STATUS_QUEUED
    assert payload["pending"] == 1
    assert payload["error"] == 0
    assert payload["last_error"] is None
    assert item["status"] == batch_jobs.ITEM_STATUS_PENDING
    assert item["retry_count"] == 0
    assert item["error"] is None
    assert item["current_step"] is None
    assert item["result_task_id"] == "task-old"


def test_batch_status_route_recovers_stale_jobs(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "route-recover.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    assert claimed is not None

    expired = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    batch_jobs.update_job_status(
        created["job_id"],
        batch_jobs.JOB_STATUS_RUNNING,
        owner_worker_id="worker-1",
        lease_expires_at=expired,
        run_token=claimed["run_token"],
        expected_owner_worker_id="worker-1",
        expected_run_token=claimed["run_token"],
    )
    batch_jobs.update_batch_item(
        created["job_id"],
        "route-recover",
        status=batch_jobs.ITEM_STATUS_RUNNING,
        current_step="处理中",
    )

    payload = asyncio.run(batch_route.get_batch_status())

    assert payload.job_id == created["job_id"]
    assert payload.status == batch_jobs.JOB_STATUS_INTERRUPTED


def test_batch_runner_formats_item_progress_as_i18n_status():
    assert _format_batch_item_progress_step(7, 23) == "i18n:batch.itemProgress:7:23"


def test_batch_runner_token_usage_payload_includes_elapsed_seconds():
    started_at = datetime(2026, 6, 2, 8, 0, 0, tzinfo=timezone.utc).isoformat()
    finished_at = datetime(2026, 6, 2, 8, 0, 12, 345000, tzinfo=timezone.utc).isoformat()

    elapsed_seconds = _elapsed_seconds_from_iso(started_at, finished_at)
    payload = _token_usage_payload(
        {
            "prompt_token_count": 10,
            "response_token_count": 20,
            "total_token_count": 30,
            "cached_input_token_count": 4,
            "uncached_input_token_count": 6,
        },
        elapsed_seconds,
    )

    assert payload == {
        "prompt_token_count": 10,
        "response_token_count": 20,
        "total_token_count": 30,
        "cached_input_token_count": 4,
        "uncached_input_token_count": 6,
        "elapsed_seconds": 12.345,
    }


def test_batch_status_payload_includes_item_elapsed_seconds(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "token-elapsed.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)

    batch_jobs.update_batch_item(
        created["job_id"],
        "token-elapsed",
        status=batch_jobs.ITEM_STATUS_DONE,
        token_usage={
            "prompt_token_count": 11,
            "response_token_count": 22,
            "total_token_count": 33,
            "cached_input_token_count": None,
            "uncached_input_token_count": None,
            "elapsed_seconds": 44.5,
        },
    )

    payload = batch_jobs.get_batch_job_status_payload(created["job_id"])

    assert payload["tasks"][0]["token_usage"] == {
        "prompt_token_count": 11,
        "response_token_count": 22,
        "total_token_count": 33,
        "cached_input_token_count": None,
        "uncached_input_token_count": None,
        "elapsed_seconds": 44.5,
    }


def test_token_tracker_reads_vllm_usage_without_cache_details():
    tracker = TokenUsageTracker(task_id="vllm-task")

    tracker.add_openai_usage(SimpleNamespace(
        prompt_tokens=328,
        completion_tokens=2176,
        total_tokens=2504,
        prompt_tokens_details=None,
    ))

    payload = tracker.to_dict()
    assert payload["prompt_token_count"] == 328
    assert payload["response_token_count"] == 2176
    assert payload["total_token_count"] == 2504
    assert payload["cached_input_token_count"] is None
    assert payload["uncached_input_token_count"] is None


def test_token_tracker_reads_litellm_cached_input_tokens():
    tracker = TokenUsageTracker(task_id="cached-task")

    tracker.add_openai_usage(SimpleNamespace(
        prompt_tokens=328,
        completion_tokens=2176,
        total_tokens=2504,
        prompt_tokens_details=SimpleNamespace(cached_tokens=120),
    ))

    payload = tracker.to_dict()
    assert payload["prompt_token_count"] == 328
    assert payload["response_token_count"] == 2176
    assert payload["cached_input_token_count"] == 120
    assert payload["uncached_input_token_count"] == 208


def test_token_tracker_reads_google_adk_candidates_as_output_tokens():
    tracker = TokenUsageTracker(task_id="adk-task")

    tracker.add_usage(SimpleNamespace(
        prompt_token_count=10,
        candidates_token_count=20,
        thoughts_token_count=3,
        total_token_count=33,
        cached_content_token_count=4,
    ))

    payload = tracker.to_dict()
    assert payload["prompt_token_count"] == 10
    assert payload["response_token_count"] == 23
    assert payload["total_token_count"] == 33
    assert payload["cached_input_token_count"] == 4
    assert payload["uncached_input_token_count"] == 6


def test_format_elapsed_time_desc():
    assert _format_elapsed_time_desc(0) == "0秒"
    assert _format_elapsed_time_desc(44.6) == "45秒"
    assert _format_elapsed_time_desc(492) == "8分 12秒"
    assert _format_elapsed_time_desc(47579.996) == "13小时 13分 0秒"


def test_build_batch_usage_summary_aggregates_tokens():
    job = {
        "job_id": "batch-job-test",
        "status": batch_jobs.JOB_STATUS_COMPLETED,
        "status_reason": batch_jobs.STATUS_REASON_NORMAL,
        "started_at": "2026-06-03T01:00:00+00:00",
        "finished_at": "2026-06-03T01:02:03.456000+00:00",
    }
    items = [
        {
            "item_id": "guide-a",
            "pdf_path": "/tmp/a.pdf",
            "status": batch_jobs.ITEM_STATUS_DONE,
            "started_at": "2026-06-03T01:00:00+00:00",
            "finished_at": "2026-06-03T01:01:00+00:00",
            "retry_count": 0,
            "result_task_id": "guide-a",
            "token_usage": {
                "prompt_token_count": 10,
                "response_token_count": 20,
                "total_token_count": 30,
                "cached_input_token_count": 2,
                "elapsed_seconds": 60,
            },
        },
        {
            "item_id": "guide-b",
            "pdf_path": "/tmp/b.pdf",
            "status": batch_jobs.ITEM_STATUS_DONE,
            "started_at": "2026-06-03T01:01:00+00:00",
            "finished_at": "2026-06-03T01:02:00+00:00",
            "retry_count": 1,
            "result_task_id": "guide-b",
            "token_usage": {
                "prompt_token_count": 3,
                "response_token_count": 4,
                "total_token_count": 7,
                "cached_input_token_count": 1,
                "elapsed_seconds": 60,
            },
        },
    ]

    payload = _build_batch_usage_summary(job, items, "batch_test")

    assert payload["batch_dir"] == "batch_test"
    assert payload["elapsed_seconds"] == 123.456
    assert payload["elapsed_time_desc"] == "2分 3秒"
    assert payload["token_usage"] == {
        "input_token_count": 13,
        "cached_input_token_count": 3,
        "uncached_input_token_count": 10,
        "output_token_count": 24,
        "total_token_count": 37,
    }
    assert payload["tasks"][0]["input_token_count"] == 10
    assert payload["tasks"][0]["cached_input_token_count"] == 2
    assert payload["tasks"][0]["uncached_input_token_count"] == 8
    assert payload["tasks"][0]["elapsed_time_desc"] == "1分 0秒"
    assert payload["tasks"][1]["retry_count"] == 1


def test_build_batch_usage_summary_marks_cache_totals_unknown_when_any_item_unknown():
    job = {
        "job_id": "batch-job-test",
        "status": batch_jobs.JOB_STATUS_COMPLETED,
        "status_reason": batch_jobs.STATUS_REASON_NORMAL,
        "started_at": "2026-06-03T01:00:00+00:00",
        "finished_at": "2026-06-03T01:01:00+00:00",
    }
    items = [
        {
            "item_id": "guide-a",
            "pdf_path": "/tmp/a.pdf",
            "status": batch_jobs.ITEM_STATUS_DONE,
            "started_at": "2026-06-03T01:00:00+00:00",
            "finished_at": "2026-06-03T01:01:00+00:00",
            "token_usage": {
                "prompt_token_count": 10,
                "response_token_count": 20,
                "total_token_count": 30,
                "cached_input_token_count": None,
                "elapsed_seconds": 60,
            },
        },
    ]

    payload = _build_batch_usage_summary(job, items, "batch_test")

    assert payload["token_usage"]["cached_input_token_count"] is None
    assert payload["token_usage"]["uncached_input_token_count"] is None
    assert payload["tasks"][0]["cached_input_token_count"] is None
    assert payload["tasks"][0]["uncached_input_token_count"] is None


def test_batch_runner_final_summary_keeps_last_round_time_bounds(isolated_batch_db, monkeypatch):
    pdf1 = _create_pdf(isolated_batch_db, "two-round.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=1, experiment_rounds=2)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=120)
    assert claimed is not None

    monkeypatch.setattr(
        batch_worker_runner,
        "get_settings",
        lambda: SimpleNamespace(app=SimpleNamespace(data_root=str(isolated_batch_db))),
    )
    refreshed_models: list[str] = []
    monkeypatch.setattr(
        batch_worker_runner,
        "refresh_model_config_snapshot",
        lambda: refreshed_models.append("huatuo3-32b") or SimpleNamespace(
            app=SimpleNamespace(),
            llm=SimpleNamespace(
                provider="vllm",
                model_name="huatuo3-32b",
                base_url="http://llm.example/v1",
            ),
        ),
    )

    def fake_output_subdir(_self, round_num: int) -> str:
        return f"batch_round_{round_num}"

    async def fake_run_item(self, item, round_num: int, _output_subdir: str):
        started_at = f"2026-06-03T0{round_num}:00:00+00:00"
        finished_at = f"2026-06-03T0{round_num}:02:00+00:00"
        batch_jobs.update_batch_item(
            self.job_id,
            item["item_id"],
            status=batch_jobs.ITEM_STATUS_DONE,
            current_step="i18n:batch.completed",
            started_at=started_at,
            finished_at=finished_at,
            token_usage={
                "prompt_token_count": 10 * round_num,
                "response_token_count": 20 * round_num,
                "total_token_count": 30 * round_num,
                "cached_input_token_count": 0,
                "uncached_input_token_count": 10 * round_num,
                "elapsed_seconds": 120,
            },
            **self._guard(),
        )

    monkeypatch.setattr(BatchJobRunner, "_ensure_round_output_subdir", fake_output_subdir)
    monkeypatch.setattr(BatchJobRunner, "_run_item", fake_run_item)
    runner = BatchJobRunner(
        created["job_id"],
        "worker-1",
        claimed["run_token"],
        CancellationToken(),
        None,
    )

    asyncio.run(runner.run())

    summary_path = isolated_batch_db / "batch_round_2" / "batch_usage_summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert refreshed_models == ["huatuo3-32b"]
    assert payload["started_at"] == "2026-06-03T02:00:00+00:00"
    assert payload["finished_at"] == "2026-06-03T02:02:00+00:00"
    assert payload["elapsed_seconds"] == 120
    assert payload["elapsed_time_desc"] == "2分 0秒"
    assert payload["token_usage"]["input_token_count"] == 20


def test_batch_item_step_transition_bypasses_throttle_window(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "step-transition.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=1, experiment_rounds=1)
    item_id = created["tasks"][0]["id"]

    batch_jobs.update_batch_item(
        created["job_id"],
        item_id,
        current_step="i18n:batch.reusingMineruCache",
        throttle_step_window_seconds=2.0,
    )
    updated = batch_jobs.update_batch_item(
        created["job_id"],
        item_id,
        current_step="i18n:batch.buildingEvidence",
        throttle_step_window_seconds=2.0,
    )

    assert updated["current_step"] == "i18n:batch.buildingEvidence"
