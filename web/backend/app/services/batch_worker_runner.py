"""
SQLite 批量任务执行器。

单个 worker 每次只执行一个 batch job，但 job 内部仍保留原有 PDF 并发。
"""
from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from core.agents.base.model_config import refresh_model_config_snapshot
from core.utils.cancellation import CancellationToken
from core.config import get_settings
from core.runtime import ExecutionRuntime
from web.backend.app.database import get_db
from web.backend.app.services.agent_runner import AppraisalRunner
from .batch_identity import batch_run_task_id
from ..i18n.messages import descriptor, sanitize_diagnostic, ui_message
from ..i18n import get_language
from ..i18n.legacy import legacy_error
from .batch_jobs import (
    ITEM_STATUS_DONE,
    ITEM_STATUS_ERROR,
    ITEM_STATUS_PENDING,
    ITEM_STATUS_RUNNING,
    ITEM_STATUS_STOPPED,
    REPORT_STATUS_COMPLETE,
    REPORT_STATUS_NONE,
    REPORT_STATUS_PARTIAL,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_STOPPED,
    STATUS_REASON_LLM_TIMEOUT,
    STATUS_REASON_MINERU_TIMEOUT,
    STATUS_REASON_NORMAL,
    STATUS_REASON_REPORTER_TIMEOUT,
    STATUS_REASON_STOP_REQUESTED,
    STATUS_REASON_WORKER_ERROR,
    get_batch_job,
    get_stop_requested,
    list_batch_job_items,
    update_batch_item,
    update_job_runtime,
    update_job_status,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

BATCH_USAGE_SUMMARY_FILENAME = "batch_usage_summary.json"


def _format_batch_item_progress_step(completed: int, total: int) -> str:
    """构造前端可本地化的条目评价进度状态。"""
    return f"i18n:batch.itemProgress:{completed}:{total}"


def _elapsed_seconds_from_iso(started_at: str | None, finished_at: str | None) -> float:
    """根据 ISO 时间计算耗时秒数。"""
    if not started_at or not finished_at:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return 0.0
    return round(max(0.0, (finished - started).total_seconds()), 3)


def _format_elapsed_time_desc(elapsed_seconds: float) -> str:
    """把秒数转换为中文可读耗时。"""
    try:
        total_seconds = max(0, int(float(elapsed_seconds) + 0.5))
    except (TypeError, ValueError):
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}小时 {minutes}分 {seconds}秒"
    if minutes > 0:
        return f"{minutes}分 {seconds}秒"
    return f"{seconds}秒"


def _usage_optional_int(token_usage: dict[str, Any], key: str) -> int | None:
    """读取可选非负整数 token 计数。"""
    if key not in token_usage or token_usage.get(key) is None:
        return None
    try:
        return max(0, int(token_usage.get(key)))
    except (TypeError, ValueError):
        return None


def _token_usage_payload(token_usage: dict[str, Any], elapsed_seconds: float) -> dict[str, Any]:
    """构造写入 batch item 的 token 使用统计。"""
    input_tokens = _usage_int(token_usage, "prompt_token_count")
    cached_input_tokens = _usage_optional_int(token_usage, "cached_input_token_count")
    uncached_input_tokens = (
        max(0, input_tokens - cached_input_tokens)
        if cached_input_tokens is not None
        else None
    )
    return {
        "prompt_token_count": input_tokens,
        "response_token_count": _usage_int(token_usage, "response_token_count"),
        "total_token_count": _usage_int(token_usage, "total_token_count"),
        "cached_input_token_count": cached_input_tokens,
        "uncached_input_token_count": uncached_input_tokens,
        "elapsed_seconds": elapsed_seconds,
    }


def _usage_int(token_usage: dict[str, Any], key: str) -> int:
    """读取非负整数 token 计数。"""
    try:
        return max(0, int(token_usage.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _usage_float(token_usage: dict[str, Any], key: str) -> float:
    """读取非负浮点耗时。"""
    try:
        return round(max(0.0, float(token_usage.get(key, 0.0) or 0.0)), 3)
    except (TypeError, ValueError):
        return 0.0


def _build_batch_usage_summary(job: dict[str, Any], items: list[dict[str, Any]], output_subdir: str) -> dict[str, Any]:
    """构造 batch 结束后写入输出目录的用量统计。"""
    task_payloads: list[dict[str, Any]] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0
    total_cached_input_tokens = 0
    all_cached_input_known = True

    for item in items:
        token_usage = item.get("token_usage") or {}
        input_tokens = _usage_int(token_usage, "prompt_token_count")
        output_tokens = _usage_int(token_usage, "response_token_count")
        item_total_tokens = _usage_int(token_usage, "total_token_count")
        cached_input_tokens = _usage_optional_int(token_usage, "cached_input_token_count")
        uncached_input_tokens = (
            max(0, input_tokens - cached_input_tokens)
            if cached_input_tokens is not None
            else None
        )
        item_elapsed_seconds = _usage_float(token_usage, "elapsed_seconds")
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_tokens += item_total_tokens
        if cached_input_tokens is None:
            all_cached_input_known = False
        else:
            total_cached_input_tokens += cached_input_tokens
        task_payloads.append({
            "item_id": item.get("item_id"),
            "pdf_path": item.get("pdf_path"),
            "status": item.get("status"),
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at"),
            "elapsed_seconds": item_elapsed_seconds,
            "elapsed_time_desc": _format_elapsed_time_desc(item_elapsed_seconds),
            "input_token_count": input_tokens,
            "cached_input_token_count": cached_input_tokens,
            "uncached_input_token_count": uncached_input_tokens,
            "output_token_count": output_tokens,
            "total_token_count": item_total_tokens,
            "retry_count": item.get("retry_count", 0),
            "result_task_id": item.get("result_task_id"),
            "report_status": item.get("report_status", REPORT_STATUS_NONE),
        })

    elapsed_seconds = _elapsed_seconds_from_iso(job.get("started_at"), job.get("finished_at"))
    total_uncached_input_tokens = (
        max(0, total_input_tokens - total_cached_input_tokens)
        if all_cached_input_known
        else None
    )
    return {
        "job_id": job.get("job_id"),
        "batch_dir": output_subdir,
        "status": job.get("status"),
        "status_reason": job.get("status_reason"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed_seconds": elapsed_seconds,
        "elapsed_time_desc": _format_elapsed_time_desc(elapsed_seconds),
        "total": len(items),
        "token_usage": {
            "input_token_count": total_input_tokens,
            "cached_input_token_count": total_cached_input_tokens if all_cached_input_known else None,
            "uncached_input_token_count": total_uncached_input_tokens,
            "output_token_count": total_output_tokens,
            "total_token_count": total_tokens,
        },
        "tasks": task_payloads,
    }


def _item_time_bounds(items: list[dict[str, Any]], fallback_started_at: str | None, fallback_finished_at: str | None) -> tuple[str | None, str | None]:
    """根据条目时间计算本轮开始和结束时间。"""
    started_values = [item.get("started_at") for item in items if item.get("started_at")]
    finished_values = [item.get("finished_at") for item in items if item.get("finished_at")]
    return (
        min(started_values) if started_values else fallback_started_at,
        max(finished_values) if finished_values else fallback_finished_at,
    )


class BatchJobRunner:
    """执行单个 job 的业务逻辑。"""

    MAX_RETRY = 1

    def __init__(
        self,
        job_id: str,
        worker_id: str,
        run_token: str,
        cancellation_token: CancellationToken,
        executor: ThreadPoolExecutor | None,
        runtime: ExecutionRuntime | None = None,
    ):
        self.job_id = job_id
        self.worker_id = worker_id
        self.run_token = run_token
        self.cancellation_token = cancellation_token
        self.executor = executor
        self.runtime = runtime
        self._semaphore: asyncio.Semaphore | None = None
        self._job_scope = None
        self._active_runners: list[AppraisalRunner] = []
        self._job_failure_reason = STATUS_REASON_NORMAL

    def _guard(self) -> dict[str, str]:
        return {
            "expected_owner_worker_id": self.worker_id,
            "expected_run_token": self.run_token,
        }

    def _should_stop(self) -> bool:
        if self.cancellation_token.cancelled():
            return True
        if get_stop_requested(self.job_id):
            self.cancellation_token.cancel("stop_requested")
            return True
        return self.cancellation_token.cancelled()

    def _is_interrupted(self) -> bool:
        """区分 worker/租约中断与用户主动停止。"""
        return (
            self.cancellation_token.cancelled()
            and self.cancellation_token.reason != "stop_requested"
        )

    def _finish_cancelled_item(
        self,
        item_id: str,
        *,
        result_task_id: str | None = None,
        first_round_task_id: str | None = None,
    ) -> None:
        """按取消来源把条目置为可恢复或已停止。"""
        if self._is_interrupted():
            update_batch_item(
                self.job_id,
                item_id,
                status=ITEM_STATUS_PENDING,
                current_step="i18n:batch.workerInterrupted",
                started_at=None,
                finished_at=None,
                last_error=None,
                result_task_id=result_task_id,
                first_round_task_id=first_round_task_id,
                **self._guard(),
            )
            return
        update_batch_item(
            self.job_id,
            item_id,
            status=ITEM_STATUS_STOPPED,
            current_step="i18n:batch.stopped",
            finished_at=utcnow_iso(),
            last_error=None,
            result_task_id=result_task_id,
            first_round_task_id=first_round_task_id,
            **self._guard(),
        )

    def _current_job(self) -> dict[str, Any]:
        job = get_batch_job(self.job_id)
        if job is None:
            raise RuntimeError(f"批量任务不存在: {self.job_id}")
        return job

    def _current_items(self) -> list[dict[str, Any]]:
        return list_batch_job_items(self.job_id)

    def _round_runtime(self) -> dict[str, Any]:
        return self._current_job().get("round_runtime", {}) or {"round_output_dirs": {}}

    def _ensure_round_output_subdir(self, round_num: int) -> str:
        runtime = self._round_runtime()
        round_output_dirs = runtime.setdefault("round_output_dirs", {})
        output_subdir = round_output_dirs.get(str(round_num))
        if not output_subdir:
            output_subdir = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            round_output_dirs[str(round_num)] = output_subdir
            update_job_runtime(self.job_id, runtime, **self._guard())
        return output_subdir

    def _write_usage_summary(
        self,
        output_subdir: str,
        *,
        status: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> None:
        """向 batch 输出目录写入本次任务用量统计。"""
        try:
            job = self._current_job()
            summary_job = dict(job)
            if status is not None:
                summary_job["status"] = status
            if started_at is not None:
                summary_job["started_at"] = started_at
            if finished_at is not None:
                summary_job["finished_at"] = finished_at
            summary_items = items if items is not None else self._current_items()
            payload = _build_batch_usage_summary(summary_job, summary_items, output_subdir)
            summary_path = Path(get_settings().app.data_root) / output_subdir / BATCH_USAGE_SUMMARY_FILENAME
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = summary_path.with_name(f".{summary_path.name}.tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(summary_path)
        except Exception:
            logger.exception("写入 batch 用量统计失败: job=%s output_subdir=%s", self.job_id, output_subdir)

    def _prepare_items_for_resume(self, current_round: int, experiment_rounds: int) -> int:
        items = self._current_items()
        all_done = all(item["status"] == ITEM_STATUS_DONE for item in items)
        if current_round == 0:
            return 1

        if all_done and current_round < experiment_rounds:
            next_round = current_round + 1
            self._reset_items_for_next_round(all_items=True)
            update_job_status(
                self.job_id,
                JOB_STATUS_RUNNING,
                current_round=next_round,
                status_reason=STATUS_REASON_NORMAL,
                **self._guard(),
            )
            return next_round

        self._reset_items_for_next_round(all_items=False)
        return current_round

    def _reset_items_for_next_round(self, *, all_items: bool) -> None:
        conn = get_db()
        now = utcnow_iso()
        where = "" if all_items else f"AND status != '{ITEM_STATUS_DONE}'"
        conn.execute(
            f"""
            UPDATE batch_job_items
            SET status = ?, current_step = ?, retry_count = 0,
                result_task_id = CASE WHEN ? THEN NULL ELSE result_task_id END,
                active_task_id = CASE WHEN ? THEN NULL ELSE active_task_id END,
                report_status = CASE WHEN ? THEN 'none' ELSE report_status END,
                started_at = NULL, finished_at = NULL, updated_at = ?, last_error = NULL
            WHERE job_id = ? {where}
              AND EXISTS (
                  SELECT 1 FROM batch_jobs
                  WHERE batch_jobs.job_id = batch_job_items.job_id
                    AND batch_jobs.owner_worker_id = ?
                    AND batch_jobs.run_token = ?
              )
            """,
            (
                ITEM_STATUS_PENDING,
                None if all_items else "i18n:batch.waitingContinue",
                int(all_items),
                int(all_items),
                int(all_items),
                now,
                self.job_id,
                self.worker_id,
                self.run_token,
            ),
        )
        conn.commit()

    async def run(self) -> None:
        settings = refresh_model_config_snapshot()
        logger.info(
            "批量任务开始刷新 LLM 配置: job=%s provider=%s model=%s base_url=%s",
            self.job_id,
            settings.llm.provider,
            settings.llm.model_name,
            settings.llm.base_url,
        )
        job = self._current_job()
        concurrency = max(1, int(job["concurrency"]))
        experiment_rounds = max(1, int(job["experiment_rounds"]))
        current_round = self._prepare_items_for_resume(job["current_round"], experiment_rounds)
        if self.runtime is not None:
            self._job_scope = self.runtime.open_job(concurrency)
        else:
            self._semaphore = asyncio.Semaphore(concurrency)

        started_at = job.get("started_at") or utcnow_iso()
        update_job_status(
            self.job_id,
            JOB_STATUS_RUNNING,
            started_at=started_at,
            current_round=current_round,
            status_reason=STATUS_REASON_NORMAL,
            **self._guard(),
        )

        final_status = JOB_STATUS_COMPLETED
        last_error: str | None = None
        last_output_subdir: str | None = None
        last_summary_items: list[dict[str, Any]] | None = None
        last_summary_started_at: str | None = None
        last_summary_finished_at: str | None = None

        for round_num in range(current_round, experiment_rounds + 1):
            if self._should_stop():
                final_status = JOB_STATUS_INTERRUPTED if self._is_interrupted() else JOB_STATUS_STOPPED
                break

            output_subdir = self._ensure_round_output_subdir(round_num)
            last_output_subdir = output_subdir
            update_job_status(
                self.job_id,
                JOB_STATUS_RUNNING,
                current_round=round_num,
                status_reason=STATUS_REASON_NORMAL,
                **self._guard(),
            )

            if round_num > current_round:
                self._reset_items_for_next_round(all_items=True)

            item_results = await asyncio.gather(*[
                self._run_item(item, round_num, output_subdir)
                for item in self._current_items()
                if item["status"] != ITEM_STATUS_DONE
            ], return_exceptions=True)
            for item_result in item_results:
                if isinstance(item_result, BaseException):
                    logger.exception(
                        "批量任务条目协程异常: job=%s round=%s",
                        self.job_id,
                        round_num,
                        exc_info=(type(item_result), item_result, item_result.__traceback__),
                    )
                    if self._job_failure_reason == STATUS_REASON_NORMAL:
                        self._job_failure_reason = STATUS_REASON_WORKER_ERROR

            items = self._current_items()
            if self._should_stop():
                final_status = JOB_STATUS_INTERRUPTED if self._is_interrupted() else JOB_STATUS_STOPPED
                round_started_at, round_finished_at = _item_time_bounds(items, started_at, utcnow_iso())
                last_summary_items = items
                last_summary_started_at = round_started_at
                last_summary_finished_at = round_finished_at
                self._write_usage_summary(
                    output_subdir,
                    status=JOB_STATUS_STOPPED,
                    started_at=round_started_at,
                    finished_at=round_finished_at,
                    items=items,
                )
                break
            if any(item["status"] == ITEM_STATUS_ERROR for item in items):
                final_status = JOB_STATUS_FAILED
                errors = [item.get("last_error") for item in items if item["status"] == ITEM_STATUS_ERROR and item.get("last_error")]
                last_error = errors[0] if errors else "批量任务存在失败条目"
                round_started_at, round_finished_at = _item_time_bounds(items, started_at, utcnow_iso())
                last_summary_items = items
                last_summary_started_at = round_started_at
                last_summary_finished_at = round_finished_at
                self._write_usage_summary(
                    output_subdir,
                    status=JOB_STATUS_FAILED,
                    started_at=round_started_at,
                    finished_at=round_finished_at,
                    items=items,
                )
                break
            round_started_at, round_finished_at = _item_time_bounds(items, started_at, utcnow_iso())
            last_summary_items = items
            last_summary_started_at = round_started_at
            last_summary_finished_at = round_finished_at
            self._write_usage_summary(
                output_subdir,
                status=JOB_STATUS_COMPLETED,
                started_at=round_started_at,
                finished_at=round_finished_at,
                items=items,
            )
            if round_num == experiment_rounds:
                final_status = JOB_STATUS_COMPLETED
                break

        finished_at = utcnow_iso()
        failure_messages = {}
        if final_status == JOB_STATUS_FAILED:
            failed_item = next((item for item in (last_summary_items or []) if item["status"] == ITEM_STATUS_ERROR and (item.get("ui_messages") or {}).get("error")), None)
            failure_messages = failed_item["ui_messages"] if failed_item else legacy_error(last_error)
        update_job_status(
            self.job_id,
            final_status,
            finished_at=finished_at,
            stop_requested=(final_status == JOB_STATUS_STOPPED),
            last_error=last_error if final_status == JOB_STATUS_FAILED else None,
            ui_messages=failure_messages,
            status_reason=(
                STATUS_REASON_STOP_REQUESTED
                if final_status == JOB_STATUS_STOPPED
                else (
                    STATUS_REASON_WORKER_ERROR
                    if final_status == JOB_STATUS_INTERRUPTED
                    else (self._job_failure_reason if final_status == JOB_STATUS_FAILED else STATUS_REASON_NORMAL)
                )
            ),
            **self._guard(),
        )
        if last_output_subdir and last_summary_items is not None:
            self._write_usage_summary(
                last_output_subdir,
                status=final_status,
                started_at=last_summary_started_at,
                finished_at=last_summary_finished_at,
                items=last_summary_items,
            )

    async def _run_item(self, item: dict[str, Any], round_num: int, output_subdir: str) -> None:
        if self._job_scope is None and self._semaphore is None:
            raise RuntimeError("执行器未初始化")
        if self._should_stop():
            return

        slot = self._job_scope.document_slot() if self._job_scope is not None else self._semaphore
        async with slot:
            if self._should_stop():
                return
            await self._run_single_item(item, round_num, output_subdir)

    async def _run_single_item(self, item: dict[str, Any], round_num: int, output_subdir: str, is_retry: bool = False) -> None:
        item_id = item["item_id"]
        item_started_at = utcnow_iso()
        update_batch_item(
            self.job_id,
            item_id,
            status=ITEM_STATUS_RUNNING,
            current_step="i18n:batch.initializing",
            started_at=item_started_at,
            finished_at=None,
            last_error=None,
            **self._guard(),
        )

        error_occurred = False
        error_message = ""
        failure_metadata: dict = {}
        result_task_id: str | None = item.get("result_task_id")
        # 同轮重试保留已经生成的报告；新轮次在重置条目时清空。
        report_status = item.get("report_status") or REPORT_STATUS_NONE
        first_round_task_id: str | None = item.get("first_round_task_id")
        active_task_id: str | None = item.get("active_task_id")

        def on_message(message: dict):
            nonlocal failure_metadata
            if message["type"] == "error":
                # 结构化错误单独到达时也要记为失败；on_error 重复调用是幂等的。
                on_error(message["content"])
                failure_metadata = {"error": message["i18n"], "diagnostic": message.get("diagnostic")}
                return
            # 新消息按阶段标识更新，不再从翻译文字推断状态。
            phase = message.get("phase")
            step = {"parsing": "parsingPdf", "preprocessing": "buildingEvidence", "orchestrator": "initializing", "appraiser": "appraisingItems", "reporter": "generatingReport"}.get(phase)
            if not step:
                return
            update_batch_item(
                self.job_id, item_id,
                current_step="i18n:batch." + step,
                ui_messages={"step": message["i18n"]},
                throttle_step_window_seconds=2.0,
                **self._guard(),
            )

        def on_complete(task_id: str):
            nonlocal result_task_id, first_round_task_id, report_status
            result_task_id = task_id
            report_status = REPORT_STATUS_COMPLETE
            if round_num == 1 and not first_round_task_id:
                first_round_task_id = task_id

        def on_error(err: str):
            nonlocal error_occurred, error_message
            error_occurred = True
            error_message = err

        ui_language = get_language(self._round_runtime().get("ui_language"))

        def on_partial(task_id: str, missing: list[str], failures: dict):
            nonlocal error_occurred, error_message, result_task_id, report_status, failure_metadata
            result_task_id = task_id
            report_status = REPORT_STATUS_PARTIAL
            update_batch_item(self.job_id, item_id, result_task_id=task_id, report_status=report_status, **self._guard())
            error_occurred = True
            message = ui_message("status.partial", ui_language, completed=23 - len(missing), total=23, diagnostic=failures)
            error_message = message["message"]
            failure_metadata = {"error": message["i18n"], "diagnostic": message.get("diagnostic")}

        def on_task_ready(task_id: str):
            nonlocal active_task_id
            active_task_id = task_id
            update_batch_item(self.job_id, item_id, active_task_id=task_id, **self._guard())

        async def send_batch_summary(total: int, completed: int, _elapsed: float):
            update_batch_item(
                self.job_id,
                item_id,
                current_step=_format_batch_item_progress_step(completed, total),
                ui_messages={"step": descriptor("batch.itemProgress", completed=completed, total=total)},
                **self._guard(),
            )

        runner = AppraisalRunner(
            lang=ui_language,
            on_message=on_message,
            on_summary=send_batch_summary,
            on_complete=on_complete,
            on_error=on_error,
            on_partial=on_partial,
            on_task_ready=on_task_ready,
            output_subdir=output_subdir,
            cancellation_token=self.cancellation_token,
            executor=self.executor,
            runtime=self.runtime,
            refresh_model_config_on_start=False,
        )
        self._active_runners.append(runner)

        try:
            reuse_task_id = first_round_task_id if round_num > 1 else None
            first_round_subdir = self._round_runtime().get("round_output_dirs", {}).get("1") if reuse_task_id else None
            run_task_id = active_task_id or batch_run_task_id(self.job_id, round_num, item_id)
            await runner.run(run_task_id, item["pdf_path"], reuse_task_id=None if active_task_id else reuse_task_id, resume=bool(active_task_id), reuse_output_subdir=first_round_subdir)
        except Exception as exc:
            error_occurred = True
            message = ui_message("error.appraisal_failed", ui_language, diagnostic=exc)
            error_message = message["message"]
            failure_metadata = {"error": message["i18n"], "diagnostic": message["diagnostic"]}
            logger.error("批量任务条目执行异常: job=%s item=%s\n%s", self.job_id, item_id, sanitize_diagnostic(exc, limit=None))
        finally:
            if runner in self._active_runners:
                self._active_runners.remove(runner)

        latest_item = list_batch_job_items(self.job_id)
        latest = next((entry for entry in latest_item if entry["item_id"] == item_id), item)
        finished_at = utcnow_iso()
        token_usage = runner.get_token_usage()
        elapsed_seconds = _elapsed_seconds_from_iso(item_started_at, finished_at)

        runner_reason = runner.get_status_reason() or STATUS_REASON_NORMAL

        if self._should_stop() and not result_task_id:
            self._finish_cancelled_item(item_id)
            return

        if error_occurred:
            retry_count = int(latest.get("retry_count", 0))
            if retry_count < self.MAX_RETRY and not self._should_stop():
                update_batch_item(
                    self.job_id,
                    item_id,
                    current_step=f"i18n:batch.retrying:{retry_count + 1}:{self.MAX_RETRY}",
                    retry_count=retry_count + 1,
                    last_error=error_message,
                    ui_messages=failure_metadata or legacy_error(error_message),
                    **self._guard(),
                )
                await self._run_single_item(
                    {
                        **latest,
                        "retry_count": retry_count + 1,
                        "first_round_task_id": first_round_task_id,
                        "active_task_id": active_task_id,
                    },
                    round_num,
                    output_subdir,
                    is_retry=True,
                )
                return

            if self._should_stop():
                self._finish_cancelled_item(
                    item_id,
                    result_task_id=result_task_id,
                    first_round_task_id=first_round_task_id,
                )
                return

            update_batch_item(
                self.job_id,
                item_id,
                status=ITEM_STATUS_ERROR,
                current_step="i18n:batch.failedGeneric",
                finished_at=finished_at,
                last_error=error_message,
                ui_messages=failure_metadata or legacy_error(error_message),
                result_task_id=result_task_id,
                report_status=report_status,
                first_round_task_id=first_round_task_id,
                **self._guard(),
            )
            if self._job_failure_reason == STATUS_REASON_NORMAL:
                if runner_reason in {STATUS_REASON_LLM_TIMEOUT, STATUS_REASON_MINERU_TIMEOUT, STATUS_REASON_REPORTER_TIMEOUT}:
                    self._job_failure_reason = runner_reason
                else:
                    self._job_failure_reason = STATUS_REASON_WORKER_ERROR
            return

        update_batch_item(
            self.job_id,
            item_id,
            status=ITEM_STATUS_DONE,
            current_step="i18n:batch.completed",
            finished_at=finished_at,
            result_task_id=result_task_id,
            report_status=report_status,
            first_round_task_id=first_round_task_id,
            token_usage=_token_usage_payload(token_usage, elapsed_seconds) if token_usage else {},
            **self._guard(),
        )
