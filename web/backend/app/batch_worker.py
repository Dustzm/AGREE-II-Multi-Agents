"""
独立批量任务 Worker 入口。
"""
from __future__ import annotations

import atexit
import asyncio
import faulthandler
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from uuid import uuid4

from core.config import get_settings
from core.runtime import ExecutionRuntime
from core.utils.llm_limiter import get_llm_concurrency_snapshot
from core.utils.cancellation import CancellationToken
from web.backend.app.auth import validate_auth_configuration
from web.backend.app.database import close_db
from web.backend.app.services.batch_jobs import (
    claim_next_batch_job,
    clear_batch_job_owner,
    get_batch_job,
    get_stop_requested,
    list_batch_job_items,
    recover_stale_batch_jobs,
    renew_batch_job_lease,
    update_job_status,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_STOPPED,
)
from web.backend.app.services.batch_worker_runner import BatchJobRunner

from .logging_setup import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 2
HEARTBEAT_MAX_CONSECUTIVE_FAILURES = 3
HEARTBEAT_SUCCESS_LOG_INTERVAL = 6
WATCHDOG_INTERVAL_SECONDS = 60
WATCHDOG_STALL_SECONDS = 300

_STATE_LOCK = threading.Lock()
_WORKER_STATE: dict[str, object] = {
    "pid": os.getpid(),
    "ppid": os.getppid(),
    "worker_id": "",
    "active_job_id": "",
    "active_run_token": "",
    "active_job_started_at": 0.0,
    "last_claimed_job_id": "",
    "last_claimed_run_token": "",
    "last_job_exit_reason": "",
    "last_base_exception": "",
    "last_signal": "",
    "heartbeat_successes": 0,
    "last_heartbeat_at": 0.0,
    "last_heartbeat_job_id": "",
    "last_heartbeat_error": "",
    "shutdown_reason": "",
}


def _update_worker_state(**updates: object) -> None:
    """更新 worker 运行态，供退出审计日志使用。"""
    with _STATE_LOCK:
        _WORKER_STATE.update(updates)


def _worker_state_snapshot() -> dict[str, object]:
    """获取 worker 运行态快照。"""
    with _STATE_LOCK:
        snapshot = dict(_WORKER_STATE)
    started_at = float(snapshot.get("active_job_started_at") or 0.0)
    snapshot["active_job_elapsed_seconds"] = round(max(0.0, time.time() - started_at), 3) if started_at else 0.0
    last_heartbeat_at = float(snapshot.get("last_heartbeat_at") or 0.0)
    snapshot["last_heartbeat_age_seconds"] = (
        round(max(0.0, time.time() - last_heartbeat_at), 3)
        if last_heartbeat_at
        else None
    )
    return snapshot


def _log_worker_state(message: str, *, level: int = logging.INFO) -> None:
    """打印 worker 运行态快照，便于定位容器主进程退出原因。"""
    logger.log(level, "%s state=%s", message, _worker_state_snapshot())


def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
    """记录未捕获线程异常，包括心跳线程异常。"""
    logger.critical(
        "批量 Worker 线程未捕获异常: thread=%s state=%s",
        args.thread.name if args.thread else "<unknown>",
        _worker_state_snapshot(),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def _sys_excepthook(exc_type, exc_value, exc_traceback) -> None:
    """记录主线程未捕获异常。"""
    logger.critical(
        "批量 Worker 主线程未捕获异常: state=%s",
        _worker_state_snapshot(),
        exc_info=(exc_type, exc_value, exc_traceback),
    )


def _unraisablehook(unraisable) -> None:
    """记录析构或回调中的 unraisable 异常。"""
    logger.critical(
        "批量 Worker 捕获 unraisable 异常: object=%r err_msg=%r state=%s",
        unraisable.object,
        unraisable.err_msg,
        _worker_state_snapshot(),
        exc_info=(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
    )


def _install_process_diagnostics() -> None:
    """安装进程级诊断钩子。"""
    try:
        faulthandler.enable()
    except Exception:
        logger.exception("启用 faulthandler 失败")
    threading.excepthook = _thread_excepthook
    sys.excepthook = _sys_excepthook
    sys.unraisablehook = _unraisablehook
    atexit.register(_log_worker_state, "批量 Worker 进程 atexit")


def _lease_belongs_to_current_worker(job_id: str, worker_id: str, run_token: str) -> bool:
    """确认当前 worker 仍持有 job 租约。"""
    job = get_batch_job(job_id)
    if job is None:
        logger.error("批量任务租约检查失败，job 不存在: job=%s worker=%s", job_id, worker_id)
        return False

    owner = job.get("owner_worker_id") or ""
    current_run_token = job.get("run_token") or ""
    if owner != worker_id or current_run_token != run_token:
        logger.error(
            "批量任务租约归属已变更，取消当前执行: job=%s status=%s owner=%s token_match=%s worker=%s",
            job_id,
            job.get("status"),
            owner or "<empty>",
            current_run_token == run_token,
            worker_id,
        )
        return False
    return True


def _heartbeat_worker(
    job_id: str,
    worker_id: str,
    run_token: str,
    token: CancellationToken,
    stop_event: threading.Event,
    *,
    lease_seconds: int,
    heartbeat_seconds: int,
    max_failures: int = HEARTBEAT_MAX_CONSECUTIVE_FAILURES,
) -> None:
    """在独立线程中续租，避免主 asyncio event loop 被解析任务阻塞后误过期。"""
    consecutive_failures = 0
    try:
        while not token.cancelled() and not stop_event.wait(heartbeat_seconds):
            try:
                if get_stop_requested(job_id):
                    logger.info("批量任务收到停止请求: job=%s worker=%s", job_id, worker_id)
                    token.cancel("stop_requested")
                    return

                renewed = renew_batch_job_lease(job_id, worker_id, run_token, lease_seconds)
                if renewed:
                    with _STATE_LOCK:
                        heartbeat_successes = int(_WORKER_STATE.get("heartbeat_successes") or 0) + 1
                        _WORKER_STATE.update(
                            heartbeat_successes=heartbeat_successes,
                            last_heartbeat_at=time.time(),
                            last_heartbeat_job_id=job_id,
                            last_heartbeat_error="",
                        )
                    if consecutive_failures:
                        logger.info(
                            "批量任务租约续期恢复: job=%s worker=%s previous_failures=%s",
                            job_id,
                            worker_id,
                            consecutive_failures,
                        )
                    consecutive_failures = 0
                    if heartbeat_successes == 1 or heartbeat_successes % HEARTBEAT_SUCCESS_LOG_INTERVAL == 0:
                        logger.info(
                            "批量任务租约续期成功: job=%s worker=%s successes=%s lease_seconds=%s",
                            job_id,
                            worker_id,
                            heartbeat_successes,
                            lease_seconds,
                        )
                    logger.debug("批量任务租约续期成功: job=%s worker=%s", job_id, worker_id)
                    continue

                if not _lease_belongs_to_current_worker(job_id, worker_id, run_token):
                    token.cancel("lease_lost")
                    return

                consecutive_failures += 1
                logger.warning(
                    "批量任务租约续期未生效: job=%s worker=%s failures=%s/%s",
                    job_id,
                    worker_id,
                    consecutive_failures,
                    max_failures,
                )
            except sqlite3.OperationalError as exc:
                _update_worker_state(last_heartbeat_error=str(exc))
                consecutive_failures += 1
                logger.warning(
                    "批量任务租约续期遇到 SQLite 暂时错误: job=%s worker=%s failures=%s/%s error=%s",
                    job_id,
                    worker_id,
                    consecutive_failures,
                    max_failures,
                    exc,
                )
            except Exception as exc:
                _update_worker_state(last_heartbeat_error=str(exc))
                consecutive_failures += 1
                logger.exception(
                    "批量任务租约续期异常: job=%s worker=%s failures=%s/%s error=%s",
                    job_id,
                    worker_id,
                    consecutive_failures,
                    max_failures,
                    exc,
                )

            if consecutive_failures >= max_failures:
                logger.error(
                    "批量任务租约连续续期失败，取消当前 job: job=%s worker=%s failures=%s",
                    job_id,
                    worker_id,
                    consecutive_failures,
                )
                token.cancel("heartbeat_failed")
                return
    finally:
        logger.info("批量任务心跳线程退出: job=%s worker=%s state=%s", job_id, worker_id, _worker_state_snapshot())
        close_db()


def _watchdog_signature(items: list[dict[str, object]]) -> tuple[tuple[object, ...], ...]:
    """构造 batch item 业务进度签名。"""
    return tuple(
        sorted(
            (
                item.get("item_id"),
                item.get("status"),
                item.get("current_step"),
                item.get("updated_at"),
                item.get("finished_at"),
            )
            for item in items
        )
    )


def _watchdog_worker(
    job_id: str,
    worker_id: str,
    run_token: str,
    token: CancellationToken,
    stop_event: threading.Event,
    runtime: ExecutionRuntime,
) -> None:
    """独立线程监控 batch 业务进度，避免只剩 lease 日志时失去诊断信号。"""
    last_signature: tuple[tuple[object, ...], ...] | None = None
    last_progress_at = time.time()
    try:
        while not token.cancelled() and not stop_event.wait(WATCHDOG_INTERVAL_SECONDS):
            try:
                job = get_batch_job(job_id)
                if job is None:
                    logger.warning("批量 Watchdog 未找到 job: job=%s worker=%s", job_id, worker_id)
                    return

                owner = job.get("owner_worker_id") or ""
                current_run_token = job.get("run_token") or ""
                if owner != worker_id or current_run_token != run_token:
                    logger.warning(
                        "批量 Watchdog 检测到租约归属变更，退出监控: job=%s owner=%s token_match=%s worker=%s",
                        job_id,
                        owner or "<empty>",
                        current_run_token == run_token,
                        worker_id,
                    )
                    return

                items = list_batch_job_items(job_id)
                signature = _watchdog_signature(items)
                if signature != last_signature:
                    last_signature = signature
                    last_progress_at = time.time()
                    continue

                stalled_seconds = time.time() - last_progress_at
                if stalled_seconds < WATCHDOG_STALL_SECONDS:
                    continue

                running_items = [item for item in items if item.get("status") == "running"]
                llm_snapshot = get_llm_concurrency_snapshot()
                logger.warning(
                    "批量 Watchdog 检测到业务进度停滞: job=%s worker=%s stalled_seconds=%.1f "
                    "job_status=%s running_items=%s llm_pool=%s llm_global=%s worker_state=%s",
                    job_id,
                    worker_id,
                    stalled_seconds,
                    job.get("status"),
                    [
                        {
                            "item_id": item.get("item_id"),
                            "step": item.get("current_step"),
                            "updated_at": item.get("updated_at"),
                            "started_at": item.get("started_at"),
                        }
                        for item in running_items
                    ],
                    runtime.snapshot(),
                    {
                        "max": llm_snapshot.max_concurrency,
                        "active": llm_snapshot.active,
                        "waiting": llm_snapshot.waiting,
                    },
                    _worker_state_snapshot(),
                )
            except Exception:
                logger.exception("批量 Watchdog 检查异常: job=%s worker=%s", job_id, worker_id)
    finally:
        logger.info("批量 Watchdog 线程退出: job=%s worker=%s", job_id, worker_id)
        close_db()


async def _run_claimed_job(
    job_id: str,
    worker_id: str,
    run_token: str,
    token: CancellationToken,
    runtime: ExecutionRuntime,
    *,
    lease_seconds: int,
    heartbeat_seconds: int,
) -> None:
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat_worker,
        name=f"batch-heartbeat-{job_id[:8]}",
        args=(job_id, worker_id, run_token, token, heartbeat_stop),
        kwargs={
            "lease_seconds": lease_seconds,
            "heartbeat_seconds": heartbeat_seconds,
        },
        daemon=True,
    )
    heartbeat.start()
    logger.info("批量任务心跳线程已启动: job=%s worker=%s run_token=%s", job_id, worker_id, run_token)
    watchdog = threading.Thread(
        target=_watchdog_worker,
        name=f"batch-watchdog-{job_id[:8]}",
        args=(job_id, worker_id, run_token, token, heartbeat_stop, runtime),
        daemon=True,
    )
    watchdog.start()
    logger.info("批量任务 Watchdog 线程已启动: job=%s worker=%s run_token=%s", job_id, worker_id, run_token)
    runner = BatchJobRunner(
        job_id,
        worker_id,
        run_token,
        token,
        runtime.executor,
        runtime=runtime,
    )
    try:
        _update_worker_state(
            active_job_id=job_id,
            active_run_token=run_token,
            active_job_started_at=time.time(),
            last_claimed_job_id=job_id,
            last_claimed_run_token=run_token,
            last_job_exit_reason="running",
            last_base_exception="",
            heartbeat_successes=0,
            last_heartbeat_at=0.0,
            last_heartbeat_job_id="",
            last_heartbeat_error="",
        )
        _log_worker_state("批量 Worker 开始执行已领取任务")
        await runner.run()
        _update_worker_state(last_job_exit_reason="completed")
        _log_worker_state("批量 Worker 已完成已领取任务")
    except asyncio.CancelledError:
        _update_worker_state(shutdown_reason="asyncio_cancelled")
        _log_worker_state("批量 Worker 执行任务收到 asyncio.CancelledError", level=logging.WARNING)
        token.cancel("worker_shutdown")
        raise
    except Exception as exc:
        logger.exception("Worker 执行批量任务失败: job=%s", job_id)
        token.cancel("worker_error")
        final_status = JOB_STATUS_STOPPED if get_stop_requested(job_id) else JOB_STATUS_INTERRUPTED
        update_job_status(
            job_id,
            final_status,
            last_error=str(exc),
            status_reason="worker_error",
            expected_owner_worker_id=worker_id,
            expected_run_token=run_token,
        )
        _update_worker_state(last_job_exit_reason="handled_exception", last_base_exception=repr(exc))
    except BaseException as exc:
        _update_worker_state(
            shutdown_reason=f"job_base_exception:{type(exc).__name__}",
            last_job_exit_reason=f"base_exception:{type(exc).__name__}",
            last_base_exception=repr(exc),
        )
        logger.critical(
            "Worker 执行批量任务时发生 BaseException: job=%s state=%s",
            job_id,
            _worker_state_snapshot(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        token.cancel("worker_error")
        raise
    finally:
        token.cancel("job_finished")
        heartbeat_stop.set()
        heartbeat.join(timeout=max(5, heartbeat_seconds + 5))
        if heartbeat.is_alive():
            logger.warning("批量任务心跳线程未及时退出: job=%s worker=%s", job_id, worker_id)
        watchdog.join(timeout=max(5, WATCHDOG_INTERVAL_SECONDS // 2))
        if watchdog.is_alive():
            logger.warning("批量任务 Watchdog 线程未及时退出: job=%s worker=%s", job_id, worker_id)
        clear_batch_job_owner(job_id, worker_id, run_token=run_token)
        _log_worker_state("批量 Worker 清理已领取任务")
        _update_worker_state(active_job_id="", active_run_token="", active_job_started_at=0.0)


async def run_worker() -> None:
    worker_id = f"batch-worker-{uuid4().hex[:8]}"
    _update_worker_state(worker_id=worker_id)
    logger.info(
        "批量 Worker 启动: worker=%s pid=%s ppid=%s argv=%s",
        worker_id,
        os.getpid(),
        os.getppid(),
        sys.argv,
    )
    validate_auth_configuration()
    recovered = recover_stale_batch_jobs()
    if recovered:
        logger.info("已恢复过期批量任务: %s", recovered)
    settings = get_settings()
    lease_seconds = settings.app.batch_worker_lease_seconds
    heartbeat_seconds = min(settings.app.batch_worker_heartbeat_seconds, max(1, lease_seconds // 2))
    logger.info(
        "批量 Worker 租约配置: lease_seconds=%s heartbeat_seconds=%s",
        lease_seconds,
        heartbeat_seconds,
    )
    runtime = ExecutionRuntime.from_settings(settings)

    stop_event = asyncio.Event()
    active_token: CancellationToken | None = None
    loop = asyncio.get_running_loop()

    def request_shutdown(signum: int) -> None:
        signal_name = signal.Signals(signum).name
        _update_worker_state(last_signal=signal_name, shutdown_reason=f"signal:{signal_name}")
        _log_worker_state(f"批量 Worker 收到退出信号: {signal_name}", level=logging.WARNING)
        if active_token is not None:
            active_token.cancel("worker_shutdown")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown, sig)
        except NotImplementedError:
            signal.signal(sig, lambda signum, _frame: request_shutdown(signum))
            pass

    try:
        while not stop_event.is_set():
            recover_stale_batch_jobs()
            claimed = claim_next_batch_job(worker_id, lease_seconds)
            if claimed is None:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            logger.info(
                "批量 Worker 已领取任务: job=%s worker=%s run_token=%s",
                claimed["job_id"],
                worker_id,
                claimed.get("run_token"),
            )
            job = get_batch_job(claimed["job_id"])
            if job is None:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue
            active_token = CancellationToken()
            try:
                await _run_claimed_job(
                    job["job_id"],
                    worker_id,
                    job["run_token"],
                    active_token,
                    runtime,
                    lease_seconds=lease_seconds,
                    heartbeat_seconds=heartbeat_seconds,
                )
            finally:
                active_token = None
    finally:
        if not _worker_state_snapshot().get("shutdown_reason"):
            _update_worker_state(shutdown_reason="run_worker_finally")
        _log_worker_state("批量 Worker 进入退出清理")
        await runtime.aclose()
        close_db()
        _log_worker_state("批量 Worker 已退出")


def main() -> None:
    _install_process_diagnostics()
    try:
        asyncio.run(run_worker())
    except SystemExit as exc:
        _update_worker_state(shutdown_reason=f"system_exit:{exc.code}")
        _log_worker_state("批量 Worker 捕获 SystemExit", level=logging.CRITICAL)
        raise
    except KeyboardInterrupt:
        _update_worker_state(shutdown_reason="keyboard_interrupt")
        _log_worker_state("批量 Worker 捕获 KeyboardInterrupt", level=logging.WARNING)
        raise
    except BaseException:
        _update_worker_state(shutdown_reason="unhandled_base_exception")
        _log_worker_state("批量 Worker 捕获未处理 BaseException", level=logging.CRITICAL)
        logger.exception("批量 Worker 因未处理异常退出")
        raise
    else:
        if not _worker_state_snapshot().get("shutdown_reason"):
            _update_worker_state(shutdown_reason="main_returned")
        _log_worker_state("批量 Worker main 正常返回")


if __name__ == "__main__":
    main()
