"""最多并发处理四个单 PDF 作业的 AMA MCP Worker。"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import signal
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from core.agents.base.model_config import refresh_model_config_snapshot
from core.agents.tools.public_tools import get_task_paths
from core.config import get_settings
from core.pipeline import Phase, classify_failure
from core.pipeline.events import PipelineEvent, PipelineFailureReport, PipelineSink
from core.pipeline.runner import AppraisalPipeline
from core.runtime import ExecutionRuntime
from core.utils.cancellation import CancellationToken
from core.utils.diagnostics import sanitize_diagnostic

from config import MCPSettings, get_mcp_settings
from errors import MCPErrorCode, MCPJobError, error_code_for_failure
from repository import CANCELLED, COMPLETED, FAILED, MCPJobRepository
from service import available_disk_bytes

logger = logging.getLogger(__name__)

# 流水线阶段到 MCP 公开进度阶段；编排阶段不覆盖已记录的进度。
_PROGRESS_PHASES = {
    Phase.PARSING: "parsing",
    Phase.PREPROCESSING: "building_evidence",
    Phase.APPRAISER: "appraising",
    Phase.REPORTER: "reporting",
}


class _JobSink(PipelineSink):
    """把流水线事件转为作业进度和终态；MCP 不需要界面文案。"""

    def __init__(self, progress: "ProgressState"):
        self.progress = progress
        self.result_task_id: str | None = None
        self.was_cancelled = False
        self.failure: PipelineFailureReport | None = None

    def emit(self, event: PipelineEvent) -> None:
        phase = _PROGRESS_PHASES.get(event.phase)
        if phase:
            self.progress.record(phase=phase)

    async def summary(self, total: int, completed: int, elapsed: float) -> None:
        self.progress.record(phase="appraising", completed=completed, total=total)

    def completed(self, task_id: str) -> None:
        self.result_task_id = task_id

    def cancelled(self, task_id: str) -> None:
        self.was_cancelled = True

    def failed(self, report: PipelineFailureReport) -> None:
        self.failure = report

    def failure_detail(self) -> str:
        """条目失败摘要在前，完整异常链在后，均已脱敏。"""
        items = self.failure.item_summary()
        chain = "".join(traceback.format_exception(self.failure.error))
        return sanitize_diagnostic((f"item errors: {items}\n" if items else "") + chain, limit=None)


@dataclass
class ActiveJob:
    """Worker 内单个活跃作业的任务和取消状态。"""

    task: asyncio.Task[None]
    token: CancellationToken


@dataclass
class ProgressState:
    """合并频繁回调，交由心跳线程批量写入 SQLite。"""

    phase: str = "initializing"
    completed: int | None = None
    total: int | None = None
    dirty: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(
        self,
        *,
        phase: str,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        """记录最新进度，覆盖尚未落库的旧状态。"""
        with self.lock:
            self.phase = phase
            if completed is not None:
                self.completed = completed
            if total is not None:
                self.total = total
            self.dirty = True

    def take(self) -> tuple[str, int | None, int | None] | None:
        """取走一次待写入快照。"""
        with self.lock:
            if not self.dirty:
                return None
            self.dirty = False
            return self.phase, self.completed, self.total


class MCPWorker:
    """共享一个执行运行时的 FIFO 作业监督器。"""

    def __init__(
        self,
        repository: MCPJobRepository | None = None,
        settings: MCPSettings | None = None,
        runtime: ExecutionRuntime | None = None,
    ):
        self.repository = repository or MCPJobRepository()
        self.settings = settings or get_mcp_settings()
        self.runtime = runtime
        self.worker_id = f"mcp-worker-{uuid4().hex[:12]}"
        self.active: dict[str, ActiveJob] = {}
        self.stop_event = asyncio.Event()
        self._last_cleanup_monotonic = float("-inf")
        self._capacity_error: MCPErrorCode | None = None

    async def run(self) -> None:
        """持续领取任务，直到收到退出信号。"""
        self.settings.validate_runtime()
        self.repository.initialize()

        refreshed = refresh_model_config_snapshot()
        if self.runtime is None:
            self.runtime = ExecutionRuntime.from_settings(refreshed)
        self._install_signal_handlers()
        logger.info(
            "AMA MCP Worker 启动: worker=%s max_jobs=%s",
            self.worker_id,
            self.settings.max_concurrent_jobs,
        )

        try:
            scheduler_delay = self.settings.poll_interval_seconds
            while not self.stop_event.is_set():
                self._reap_finished()
                try:
                    recovered = await asyncio.to_thread(
                        self.repository.recover_stale,
                        self.settings.recovery_limit,
                    )
                    if recovered:
                        logger.warning("已恢复租约过期的 MCP 任务: %s", recovered)
                    await asyncio.to_thread(self._cleanup_expired_artifacts_if_due)
                    await self._claim_available_jobs()
                    scheduler_delay = self.settings.poll_interval_seconds
                except sqlite3.Error:
                    logger.exception("MCP Worker 调度数据库暂不可用，将继续重试")
                    scheduler_delay = min(max(scheduler_delay * 2, 1.0), 30.0)
                await self._wait_for_activity(scheduler_delay)
        finally:
            await self._shutdown_active_jobs()
            if self.runtime is not None:
                await self.runtime.aclose()
                self.runtime = None
            logger.info("AMA MCP Worker 已退出: worker=%s", self.worker_id)

    def request_shutdown(self) -> None:
        """停止领取新任务，并协作式取消当前作业。"""
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        for active in self.active.values():
            active.token.cancel("worker_shutdown")

    def _install_signal_handlers(self) -> None:
        """把 SIGINT/SIGTERM 转换成协作式停止。"""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_shutdown)
            except NotImplementedError:
                signal.signal(sig, lambda *_: self.request_shutdown())

    async def _claim_available_jobs(self) -> None:
        """按剩余容量领取 FIFO 作业。"""
        if await asyncio.to_thread(self._claim_capacity_error) is not None:
            return
        while not self.stop_event.is_set() and len(self.active) < self.settings.max_concurrent_jobs:
            job = await asyncio.to_thread(
                self.repository.claim_next,
                self.worker_id,
                self.settings.lease_seconds,
                self.settings.max_concurrent_jobs,
            )
            if job is None:
                return
            token = CancellationToken()
            task = asyncio.create_task(
                self._run_claimed_job(job, token),
                name=f"mcp-job:{job['job_id']}",
            )
            self.active[job["job_id"]] = ActiveJob(task=task, token=token)

    def _reap_finished(self) -> None:
        """读取已结束任务异常，避免单个作业终止监督器。"""
        for job_id, active in list(self.active.items()):
            if not active.task.done():
                continue
            self.active.pop(job_id, None)
            try:
                active.task.result()
            except asyncio.CancelledError:
                logger.warning("MCP 作业协程被取消: job=%s", job_id)
            except Exception:
                logger.exception("MCP 作业协程异常已隔离: job=%s", job_id)

    async def _wait_for_activity(self, timeout: float | None = None) -> None:
        """等待任务完成或短轮询间隔。"""
        timeout = timeout or self.settings.poll_interval_seconds
        if not self.active:
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return
            return
        try:
            await asyncio.wait(
                [active.task for active in self.active.values()],
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            self.request_shutdown()
            raise

    async def _run_claimed_job(self, job: dict, token: CancellationToken) -> None:
        """运行一个已领取作业并在校验报告后发布结果。"""
        job_id = job["job_id"]
        run_token = job["run_token"]
        progress = ProgressState()
        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat,
            args=(job_id, run_token, token, progress, heartbeat_stop),
            name=f"mcp-heartbeat-{job_id[-8:]}",
            daemon=True,
        )
        heartbeat.start()

        sink = _JobSink(progress)
        output_subdir = f"mcp/{job_id}/{run_token}"
        runner = AppraisalPipeline(
            sink,
            output_subdir=output_subdir,
            cancellation_token=token,
            executor=self.runtime.executor if self.runtime else None,
            runtime=self.runtime,
            refresh_model_config_on_start=False,
            strict_workflow=True,
            analysis_engine=(
                job.get("analysis_engine") or get_settings().app.analysis_engine
            ),
        )

        try:
            await runner.run(job_id, job["input_path"])
            if token.cancelled() or sink.was_cancelled:
                self.repository.finish_unsuccessful(
                    job_id,
                    self.worker_id,
                    run_token,
                    error_code=MCPErrorCode.WORKER_INTERRUPTED,
                    error_detail="任务已取消" if token.reason == "stop_requested" else "Worker 执行被中断",
                    interrupted=token.reason != "stop_requested",
                    recovery_limit=self.settings.recovery_limit,
                )
                return
            if sink.failure:
                self.repository.finish_unsuccessful(
                    job_id,
                    self.worker_id,
                    run_token,
                    error_code=error_code_for_failure(sink.failure.code, token.reason),
                    error_detail=sink.failure_detail(),
                    recovery_limit=self.settings.recovery_limit,
                )
                return
            result_task_id = sink.result_task_id
            if not result_task_id:
                raise MCPJobError(
                    MCPErrorCode.RESULT_INVALID,
                    internal_detail="AMA 未返回结果任务 ID",
                )

            self._flush_progress(job_id, run_token, progress)
            result_dir = self._validate_result(result_task_id, job_id, run_token)
            published = self.repository.complete_job(
                job_id,
                self.worker_id,
                run_token,
                result_task_id=result_task_id,
                result_dir=str(result_dir),
                token_usage=runner.get_token_usage(),
            )
            if not published:
                if self.repository.stop_requested(job_id):
                    token.cancel("stop_requested")
                    self.repository.finish_unsuccessful(
                        job_id,
                        self.worker_id,
                        run_token,
                        error_code=MCPErrorCode.WORKER_INTERRUPTED,
                        error_detail="任务已取消",
                        recovery_limit=self.settings.recovery_limit,
                    )
                    return
                raise MCPJobError(
                    MCPErrorCode.WORKER_INTERRUPTED,
                    internal_detail="结果发布失败，任务租约已失效",
                )
        except Exception as exc:
            logger.exception("AMA MCP 作业执行失败: job=%s", job_id)
            if isinstance(exc, MCPJobError):
                error_code = exc.error_code
                error_detail = exc.internal_detail or str(exc)
            elif isinstance(exc, (sqlite3.Error, OSError)):
                error_code = MCPErrorCode.STORAGE_ERROR
                error_detail = str(exc)
            else:
                error_code = error_code_for_failure(classify_failure(exc), token.reason)
                error_detail = str(exc) or exc.__class__.__name__
            self.repository.finish_unsuccessful(
                job_id,
                self.worker_id,
                run_token,
                error_code=error_code,
                error_detail=error_detail,
                interrupted=token.reason in {"worker_shutdown", "lease_lost", "heartbeat_failed"},
                recovery_limit=self.settings.recovery_limit,
            )
        finally:
            token.cancel("job_finished")
            heartbeat_stop.set()
            heartbeat.join(timeout=max(5, self.settings.heartbeat_seconds + 2))
            if heartbeat.is_alive():
                logger.error("MCP 心跳线程未及时退出: job=%s", job_id)

    def _heartbeat(
        self,
        job_id: str,
        run_token: str,
        token: CancellationToken,
        progress: ProgressState,
        stop_event: threading.Event,
    ) -> None:
        """在独立线程中续租并转发数据库取消请求。"""
        consecutive_failures = 0
        while not token.cancelled() and not stop_event.wait(self.settings.heartbeat_seconds):
            try:
                if self.repository.stop_requested(job_id):
                    token.cancel("stop_requested")
                    return
                if not self.repository.renew_lease(
                    job_id,
                    self.worker_id,
                    run_token,
                    self.settings.lease_seconds,
                ):
                    token.cancel("lease_lost")
                    return
                self._flush_progress(job_id, run_token, progress)
                consecutive_failures = 0
            except sqlite3.Error:
                consecutive_failures += 1
                logger.exception(
                    "MCP 作业心跳数据库异常: job=%s consecutive=%s",
                    job_id,
                    consecutive_failures,
                )
                if consecutive_failures >= 3:
                    token.cancel("heartbeat_failed")
                    return
            except Exception:
                logger.exception("MCP 作业心跳异常: job=%s", job_id)
                token.cancel("heartbeat_failed")
                return

    def _flush_progress(self, job_id: str, run_token: str, progress: ProgressState) -> None:
        """把最近一次进度快照写入当前租约。"""
        snapshot = progress.take()
        if snapshot is None:
            return
        phase, completed, total = snapshot
        self.repository.update_progress(
            job_id,
            self.worker_id,
            run_token,
            phase=phase,
            completed=completed,
            total=total,
        )

    @staticmethod
    def _validate_result(result_task_id: str, job_id: str, run_token: str) -> Path:
        """校验报告完整性和本次尝试的目录边界。"""
        paths = get_task_paths(result_task_id)
        if not paths:
            raise MCPJobError(MCPErrorCode.RESULT_INVALID, internal_detail="无法定位 AMA 结果目录")
        result_dir = Path(paths["task_dir"]).resolve()
        expected_root = (Path(get_settings().app.data_root) / "mcp" / job_id / run_token).resolve()
        if not result_dir.is_relative_to(expected_root):
            raise MCPJobError(
                MCPErrorCode.RESULT_INVALID,
                internal_detail="AMA 结果目录不属于当前任务尝试",
            )
        report_path = result_dir / "appraisal_report.json"
        markdown_path = result_dir / "appraisal_report.md"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPJobError(
                MCPErrorCode.RESULT_INVALID,
                internal_detail="appraisal_report.json 缺失或损坏",
            ) from exc
        if report.get("task_id") != result_task_id:
            raise MCPJobError(
                MCPErrorCode.RESULT_INVALID,
                internal_detail="AMA 报告 task_id 与任务不一致",
            )
        if report.get("metadata", {}).get("total_items_evaluated") != 23:
            raise MCPJobError(
                MCPErrorCode.APPRAISAL_INCOMPLETE,
                internal_detail="AMA 报告未包含完整的 23 个 AGREE-II 条目",
            )
        evaluations_dir = result_dir / ".ama" / "evaluations"
        for item_number in range(1, 24):
            item_id = f"item_{item_number}"
            item_path = evaluations_dir / f"{item_id}.json"
            try:
                item = json.loads(item_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MCPJobError(
                    MCPErrorCode.APPRAISAL_INCOMPLETE,
                    internal_detail=f"AMA 条目结果缺失或损坏: {item_id}",
                ) from exc
            if item.get("item_id") != item_id:
                raise MCPJobError(
                    MCPErrorCode.APPRAISAL_INCOMPLETE,
                    internal_detail=f"AMA 条目结果归属不一致: {item_id}",
                )
        if not markdown_path.is_file() or markdown_path.stat().st_size == 0:
            raise MCPJobError(
                MCPErrorCode.RESULT_INVALID,
                internal_detail="appraisal_report.md 缺失或为空",
            )
        return result_dir

    def _claim_capacity_error(self) -> MCPErrorCode | None:
        """在存储不足或容量不可探测时暂停领取新任务。"""
        error: MCPErrorCode | None = None
        detail = ""
        if self.settings.min_free_disk_bytes:
            app = get_settings().app
            try:
                free = available_disk_bytes(app.path_input, app.path_output, self.repository.db_path)
                if free < self.settings.min_free_disk_bytes:
                    error = MCPErrorCode.LOW_DISK_SPACE
                    detail = f"free_bytes={free} threshold={self.settings.min_free_disk_bytes}"
            except OSError as exc:
                error = MCPErrorCode.STORAGE_ERROR
                detail = str(exc)
        if error != self._capacity_error:
            if error is None:
                logger.info("AMA MCP 存储容量已恢复，继续领取任务")
            else:
                logger.error("AMA MCP 暂停领取任务: code=%s detail=%s", error.value, detail)
            self._capacity_error = error
        return error

    def _cleanup_expired_artifacts_if_due(self) -> None:
        """按保留策略清理终态任务文件，数据库元数据继续保留。"""
        monotonic_now = time.monotonic()
        if monotonic_now - self._last_cleanup_monotonic < self.settings.cleanup_interval_seconds:
            return
        now = datetime.now(timezone.utc)
        input_before = now - timedelta(hours=self.settings.input_retention_hours)
        incomplete_before = now - timedelta(days=self.settings.incomplete_retention_days)
        result_before = now - timedelta(days=self.settings.result_retention_days)
        candidates = self.repository.list_cleanup_candidates(
            input_before=input_before.isoformat(),
            incomplete_before=incomplete_before.isoformat(),
            result_before=result_before.isoformat(),
        )
        uploads = self.repository.list_upload_cleanup_candidates(now.isoformat())
        if len(candidates) < 100 and len(uploads) < 100:
            self._last_cleanup_monotonic = monotonic_now
        app = get_settings().app
        upload_parent = app.path_input / "mcp" / "uploads"
        for upload in uploads:
            upload_id = upload["upload_id"]
            try:
                if self._remove_path(upload_parent / upload_id, upload_parent):
                    self.repository.mark_upload_file_deleted(upload_id)
            except sqlite3.Error:
                raise
            except Exception:
                logger.exception("AMA MCP 清理上传文件异常: upload_id=%s", upload_id)
        for job in candidates:
            try:
                self._cleanup_job_artifacts(
                    job,
                    input_root=app.path_input,
                    output_root=app.path_output,
                    input_before=input_before,
                    incomplete_before=incomplete_before,
                    result_before=result_before,
                )
            except sqlite3.Error:
                raise
            except Exception:
                logger.exception("AMA MCP 清理任务元数据异常: job=%s", job.get("job_id"))

    def _cleanup_job_artifacts(
        self,
        job: dict,
        *,
        input_root: Path,
        output_root: Path,
        input_before: datetime,
        incomplete_before: datetime,
        result_before: datetime,
    ) -> None:
        """清理一个任务到期的输入、旧尝试或完整输出目录。"""
        finished_at = datetime.fromisoformat(job["finished_at"])
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        job_id = job["job_id"]
        input_job_root = input_root / "mcp" / job_id
        output_job_root = output_root / "mcp" / job_id

        if not job.get("input_deleted_at") and finished_at < input_before:
            input_path = Path(job["input_path"]).resolve()
            if input_path.is_relative_to(input_job_root.resolve()) and self._remove_path(input_job_root, input_root / "mcp"):
                self.repository.mark_artifacts_deleted(job_id, input_deleted=True)

        output_due = (
            job["status"] in {FAILED, CANCELLED} and finished_at < incomplete_before
        ) or (job["status"] == COMPLETED and finished_at < result_before)
        if not job.get("output_deleted_at") and output_due:
            if self._remove_path(output_job_root, output_root / "mcp"):
                self.repository.mark_artifacts_deleted(job_id, output_deleted=True)
            return

        if (
            job["status"] == COMPLETED
            and not job.get("stale_runs_deleted_at")
            and finished_at < incomplete_before
            and self._remove_stale_runs(output_job_root, job.get("result_dir"))
        ):
            self.repository.mark_artifacts_deleted(job_id, stale_runs_deleted=True)

    @staticmethod
    def _remove_path(path: Path, allowed_parent: Path) -> bool:
        """删除指定根目录的直接子路径，拒绝越界和符号链接逃逸。"""
        try:
            if path.is_symlink():
                raise ValueError(f"拒绝清理符号链接任务根目录: {path}")
            resolved_parent = allowed_parent.resolve()
            resolved_path = path.resolve()
            if resolved_path.parent != resolved_parent:
                raise ValueError(f"清理路径越界: {resolved_path}")
            if resolved_path.exists():
                shutil.rmtree(resolved_path)
            return True
        except (OSError, ValueError):
            logger.exception("AMA MCP 文件清理失败: path=%s", path)
            return False

    @staticmethod
    def _remove_stale_runs(job_root: Path, raw_result_dir: str | None) -> bool:
        """删除完成任务中不包含正式结果的旧运行目录。"""
        try:
            if job_root.is_symlink():
                raise ValueError(f"拒绝清理符号链接任务根目录: {job_root}")
            if not job_root.exists():
                return True
            if not raw_result_dir:
                raise ValueError("完成任务缺少结果目录")
            result_dir = Path(raw_result_dir).resolve()
            relative_result = result_dir.relative_to(job_root.resolve())
            keep_run = (job_root / relative_result.parts[0]).resolve()
            for child in job_root.iterdir():
                if child.is_symlink():
                    child.unlink()
                    continue
                if child.resolve() == keep_run:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            return True
        except (OSError, ValueError, IndexError):
            logger.exception("AMA MCP 旧运行目录清理失败: job_root=%s", job_root)
            return False

    async def _shutdown_active_jobs(self) -> None:
        """协作式停止活跃作业并限制退出等待时间。"""
        for active in self.active.values():
            active.token.cancel("worker_shutdown")
        if not self.active:
            return
        tasks = [active.task for active in self.active.values()]
        done, pending = await asyncio.wait(tasks, timeout=min(30, self.settings.lease_seconds))
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except BaseException:
                logger.exception("MCP Worker 退出时作业清理异常")
        self.active.clear()


async def run_worker() -> None:
    """启动默认 MCP Worker。"""
    settings = get_mcp_settings()
    if not settings.enabled:
        logger.warning("AMA MCP 未启用，Worker 不启动")
        return
    await MCPWorker(settings=settings).run()


def main() -> None:
    """MCP Worker 命令行入口。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(run_worker())
