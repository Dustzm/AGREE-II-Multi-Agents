"""
独立批量任务 Worker 入口。
"""
from __future__ import annotations

import asyncio
import logging
import signal
from uuid import uuid4

from core.config import get_settings
from core.agents.tools.appraiser_batch import create_llm_executor, shutdown_llm_executor
from core.utils.cancellation import CancellationToken
from web.backend.app.auth import validate_auth_configuration
from web.backend.app.database import close_db
from web.backend.app.services.batch_jobs import (
    claim_next_batch_job,
    clear_batch_job_owner,
    get_batch_job,
    get_stop_requested,
    recover_stale_batch_jobs,
    renew_batch_job_lease,
    update_job_status,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_STOPPED,
)
from web.backend.app.services.batch_worker_runner import BatchJobRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

LEASE_SECONDS = 20
HEARTBEAT_SECONDS = 5
POLL_INTERVAL_SECONDS = 2


async def _heartbeat_loop(job_id: str, worker_id: str, run_token: str, token: CancellationToken) -> None:
    while not token.cancelled():
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if not renew_batch_job_lease(job_id, worker_id, run_token, LEASE_SECONDS):
            logger.error(
                "批量任务租约续期失败，主动取消当前 job: job=%s worker=%s run_token=%s",
                job_id,
                worker_id,
                run_token,
            )
            token.cancel()
            return
        if get_stop_requested(job_id):
            logger.info("批量任务收到停止请求: job=%s worker=%s", job_id, worker_id)
            token.cancel()


async def _run_claimed_job(job_id: str, worker_id: str, run_token: str, executor) -> None:
    token = CancellationToken(lambda: get_stop_requested(job_id))
    heartbeat = asyncio.create_task(_heartbeat_loop(job_id, worker_id, run_token, token))
    runner = BatchJobRunner(job_id, worker_id, run_token, token, executor)
    try:
        await runner.run()
    except asyncio.CancelledError:
        token.cancel()
        raise
    except Exception as exc:
        logger.exception("Worker 执行批量任务失败: job=%s", job_id)
        token.cancel()
        final_status = JOB_STATUS_STOPPED if get_stop_requested(job_id) else JOB_STATUS_INTERRUPTED
        update_job_status(
            job_id,
            final_status,
            last_error=str(exc),
            status_reason="worker_error",
            expected_owner_worker_id=worker_id,
            expected_run_token=run_token,
        )
    finally:
        token.cancel()
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        clear_batch_job_owner(job_id, worker_id, run_token=run_token)


async def run_worker() -> None:
    worker_id = f"batch-worker-{uuid4().hex[:8]}"
    logger.info("批量 Worker 启动: %s", worker_id)
    validate_auth_configuration()
    recovered = recover_stale_batch_jobs()
    if recovered:
        logger.info("已恢复过期批量任务: %s", recovered)
    settings = get_settings()
    executor = create_llm_executor(settings.app.appraisal_pool_max_concurrency)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        while not stop_event.is_set():
            recover_stale_batch_jobs()
            claimed = claim_next_batch_job(worker_id, LEASE_SECONDS)
            if claimed is None:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            job = get_batch_job(claimed["job_id"])
            if job is None:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            await _run_claimed_job(job["job_id"], worker_id, job["run_token"], executor)
    finally:
        shutdown_llm_executor(executor)
        close_db()
        logger.info("批量 Worker 已退出: %s", worker_id)


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
