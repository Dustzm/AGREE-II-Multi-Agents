"""
SQLite 批量任务执行器。

单个 worker 每次只执行一个 batch job，但 job 内部仍保留原有 PDF 并发。
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from core.utils.cancellation import CancellationToken
from web.backend.app.database import get_db
from web.backend.app.services.agent_runner import AppraisalRunner
from .batch_jobs import (
    ITEM_STATUS_DONE,
    ITEM_STATUS_ERROR,
    ITEM_STATUS_PENDING,
    ITEM_STATUS_RUNNING,
    ITEM_STATUS_STOPPED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
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


class BatchJobRunner:
    """执行单个 job 的业务逻辑。"""

    MAX_RETRY = 1

    def __init__(
        self,
        job_id: str,
        worker_id: str,
        run_token: str,
        cancellation_token: CancellationToken,
        executor: ThreadPoolExecutor,
    ):
        self.job_id = job_id
        self.worker_id = worker_id
        self.run_token = run_token
        self.cancellation_token = cancellation_token
        self.executor = executor
        self._semaphore: asyncio.Semaphore | None = None
        self._active_runners: list[AppraisalRunner] = []
        self._job_failure_reason = STATUS_REASON_NORMAL

    def _guard(self) -> dict[str, str]:
        return {
            "expected_owner_worker_id": self.worker_id,
            "expected_run_token": self.run_token,
        }

    def _normalize_step(self, content: str, *, agent: str | None = None) -> str | None:
        text = (content or "").strip()
        if not text:
            return None
        lowered = text.lower()
        if "重试" in text or "retry" in lowered:
            return "重试中"
        if "report" in lowered or "报告" in text:
            return "生成报告中"
        if "evidence" in lowered or "证据池" in text or "docindex" in lowered or "语义" in text:
            return "构建证据池中"
        if "parse" in lowered or "parsed" in lowered or "解析" in text:
            return "解析 PDF 中"
        if "init" in lowered or "初始化" in text:
            return "初始化中"
        if agent == "Appraiser":
            return "条目评价中"
        if agent == "Reporter":
            return "生成报告中"
        if "failed" in lowered or "error" in lowered or "异常" in text or "失败" in text:
            return "失败"
        return None

    def _update_item_step(self, item_id: str, content: str, *, agent: str | None = None) -> None:
        normalized = self._normalize_step(content, agent=agent)
        if not normalized:
            return
        update_batch_item(
            self.job_id,
            item_id,
            current_step=normalized,
            throttle_step_window_seconds=2.0,
            **self._guard(),
        )

    def _should_stop(self) -> bool:
        return self.cancellation_token.cancelled() or get_stop_requested(self.job_id)

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
            SET status = ?, current_step = ?, retry_count = 0, result_task_id = NULL,
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
                None if all_items else "等待继续",
                now,
                self.job_id,
                self.worker_id,
                self.run_token,
            ),
        )
        conn.commit()

    async def run(self) -> None:
        job = self._current_job()
        concurrency = max(1, int(job["concurrency"]))
        experiment_rounds = max(1, int(job["experiment_rounds"]))
        current_round = self._prepare_items_for_resume(job["current_round"], experiment_rounds)
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

        for round_num in range(current_round, experiment_rounds + 1):
            if self._should_stop():
                final_status = JOB_STATUS_STOPPED
                break

            output_subdir = self._ensure_round_output_subdir(round_num)
            update_job_status(
                self.job_id,
                JOB_STATUS_RUNNING,
                current_round=round_num,
                status_reason=STATUS_REASON_NORMAL,
                **self._guard(),
            )

            if round_num > current_round:
                self._reset_items_for_next_round(all_items=True)

            await asyncio.gather(*[
                self._run_item(item, round_num, output_subdir)
                for item in self._current_items()
                if item["status"] != ITEM_STATUS_DONE
            ])

            items = self._current_items()
            if self._should_stop():
                final_status = JOB_STATUS_STOPPED
                break
            if any(item["status"] == ITEM_STATUS_ERROR for item in items):
                final_status = JOB_STATUS_FAILED
                errors = [item.get("last_error") for item in items if item["status"] == ITEM_STATUS_ERROR and item.get("last_error")]
                last_error = errors[0] if errors else "批量任务存在失败条目"
                break
            if round_num == experiment_rounds:
                final_status = JOB_STATUS_COMPLETED
                break

        finished_at = utcnow_iso()
        update_job_status(
            self.job_id,
            final_status,
            finished_at=finished_at,
            stop_requested=(final_status == JOB_STATUS_STOPPED),
            last_error=last_error if final_status == JOB_STATUS_FAILED else None,
            status_reason=(
                STATUS_REASON_STOP_REQUESTED
                if final_status == JOB_STATUS_STOPPED
                else (self._job_failure_reason if final_status == JOB_STATUS_FAILED else STATUS_REASON_NORMAL)
            ),
            **self._guard(),
        )

    async def _run_item(self, item: dict[str, Any], round_num: int, output_subdir: str) -> None:
        if self._semaphore is None:
            raise RuntimeError("执行器未初始化")
        if self._should_stop():
            return

        async with self._semaphore:
            if self._should_stop():
                return
            await self._run_single_item(item, round_num, output_subdir)

    async def _run_single_item(self, item: dict[str, Any], round_num: int, output_subdir: str, is_retry: bool = False) -> None:
        item_id = item["item_id"]
        update_batch_item(
            self.job_id,
            item_id,
            status=ITEM_STATUS_RUNNING,
            current_step="正在初始化...",
            started_at=utcnow_iso(),
            finished_at=None,
            last_error=None,
            **self._guard(),
        )

        error_occurred = False
        error_message = ""
        result_task_id: str | None = None
        first_round_task_id: str | None = item.get("first_round_task_id")

        def on_status(_msg_type: str, content: str):
            preprocess_keywords = [
                "初始化", "Initializing",
                "解析", "Parsing", "parsed",
                "正在构建", "Building",
                "附件", "attachment",
                "语义", "semantic",
                "证据池", "evidence",
                "文档索引", "docindex",
                "失败", "failed", "异常", "error",
            ]
            if not any(keyword in content for keyword in preprocess_keywords):
                return
            self._update_item_step(item_id, content)

        def on_agent(agent: str, content: str, progress: int, total: int, prompt: Any = None):
            if agent == "Orchestrator":
                return
            self._update_item_step(item_id, content, agent=agent)

        def on_complete(task_id: str):
            nonlocal result_task_id, first_round_task_id
            result_task_id = task_id
            if round_num == 1 and not first_round_task_id:
                first_round_task_id = task_id

        def on_error(err: str):
            nonlocal error_occurred, error_message
            error_occurred = True
            error_message = err

        runner = AppraisalRunner(
            on_status=on_status,
            on_agent=on_agent,
            on_complete=on_complete,
            on_error=on_error,
            output_subdir=output_subdir,
            cancellation_token=self.cancellation_token,
            executor=self.executor,
        )
        self._active_runners.append(runner)

        try:
            reuse_task_id = first_round_task_id if round_num > 1 else None
            await runner.run(item_id, item["pdf_path"], reuse_task_id=reuse_task_id)
        except Exception as exc:
            error_occurred = True
            error_message = str(exc)
            logger.exception("批量任务条目执行异常: job=%s item=%s", self.job_id, item_id)
        finally:
            if runner in self._active_runners:
                self._active_runners.remove(runner)

        latest_item = list_batch_job_items(self.job_id)
        latest = next((entry for entry in latest_item if entry["item_id"] == item_id), item)
        finished_at = utcnow_iso()
        token_usage = runner.get_token_usage()

        runner_reason = runner.get_status_reason() or STATUS_REASON_NORMAL

        if self._should_stop() and not result_task_id:
            update_batch_item(
                self.job_id,
                item_id,
                status=ITEM_STATUS_STOPPED,
                current_step="已停止",
                finished_at=finished_at,
                last_error=None,
                **self._guard(),
            )
            return

        if error_occurred:
            retry_count = int(latest.get("retry_count", 0))
            if retry_count < self.MAX_RETRY and not self._should_stop():
                update_batch_item(
                    self.job_id,
                    item_id,
                    current_step=f"正在重试 ({retry_count + 1}/{self.MAX_RETRY})...",
                    retry_count=retry_count + 1,
                    last_error=error_message,
                    **self._guard(),
                )
                await self._run_single_item(
                    {
                        **latest,
                        "retry_count": retry_count + 1,
                        "first_round_task_id": first_round_task_id,
                    },
                    round_num,
                    output_subdir,
                    is_retry=True,
                )
                return

            if self._should_stop():
                update_batch_item(
                    self.job_id,
                    item_id,
                    status=ITEM_STATUS_STOPPED,
                    current_step="已停止",
                    finished_at=finished_at,
                    last_error=None,
                    result_task_id=result_task_id,
                    first_round_task_id=first_round_task_id,
                    **self._guard(),
                )
                return

            update_batch_item(
                self.job_id,
                item_id,
                status=ITEM_STATUS_ERROR,
                current_step=f"失败: {error_message[:80]}",
                finished_at=finished_at,
                last_error=error_message,
                result_task_id=result_task_id,
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
            current_step="已完成",
            finished_at=finished_at,
            result_task_id=result_task_id,
            first_round_task_id=first_round_task_id,
            token_usage={
                "prompt_token_count": token_usage.get("prompt_token_count", 0),
                "response_token_count": token_usage.get("response_token_count", 0),
                "total_token_count": token_usage.get("total_token_count", 0),
            } if token_usage else {},
            **self._guard(),
        )
