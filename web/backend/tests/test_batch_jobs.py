from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from web.backend.app import database as auth_db
from web.backend.app.api.routes import batch as batch_route
from web.backend.app.services import batch_jobs
from web.backend.app.services.batch_worker_runner import BatchJobRunner, _format_batch_item_progress_step


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


def test_create_batch_job_rejects_invalid_concurrency(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "invalid-concurrency.pdf")

    with pytest.raises(RuntimeError, match="并发数必须"):
        batch_jobs.create_batch_job([pdf1], concurrency=0, experiment_rounds=1)


def test_create_batch_job_rejects_invalid_experiment_rounds(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "invalid-rounds.pdf")

    with pytest.raises(RuntimeError, match="实验轮次必须"):
        batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=5)


def test_claim_and_renew_batch_job_lease(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "claim.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)

    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)

    assert claimed is not None
    assert claimed["job_id"] == created["job_id"]
    assert claimed["owner_worker_id"] == "worker-1"
    assert claimed["run_token"]
    assert batch_jobs.renew_batch_job_lease(created["job_id"], "worker-1", claimed["run_token"], 20) is True


def test_run_token_blocks_late_writes_after_resume(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "late-write.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    assert claimed is not None

    stale_token = claimed["run_token"]
    batch_jobs.request_stop(created["job_id"])
    expired = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
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
    expired = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
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


def test_recover_stale_stopping_job_to_stopped(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "recover-stop.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
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


def test_resume_batch_job_resets_stopped_items(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "resume.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=2)
    batch_jobs.request_stop(created["job_id"])

    payload = batch_jobs.resume_batch_job(created["job_id"], concurrency_override=4)

    assert payload["status"] == batch_jobs.JOB_STATUS_QUEUED
    assert payload["concurrency"] == 4
    assert payload["pending"] == 1
    assert payload["stopped"] == 0


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
    assert item["result_task_id"] is None


def test_batch_status_route_recovers_stale_jobs(isolated_batch_db):
    pdf1 = _create_pdf(isolated_batch_db, "route-recover.pdf")
    created = batch_jobs.create_batch_job([pdf1], concurrency=2, experiment_rounds=1)
    claimed = batch_jobs.claim_next_batch_job("worker-1", lease_seconds=20)
    assert claimed is not None

    expired = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
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


def test_batch_runner_normalizes_appraiser_status_as_i18n_key():
    runner = BatchJobRunner("job-1", "worker-1", "run-1", None, None)

    normalized = runner._normalize_step("正在评价条目", agent="Appraiser")

    assert normalized == "i18n:batch.appraisingItems"
