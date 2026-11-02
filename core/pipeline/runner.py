"""单文档评价流水线：解析 → 证据池 → 条目评价 → 报告。

与入口无关：Web、批量和 MCP 只通过 PipelineSink 接收事件，取消统一以 CancellationRequested 结束。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional
from uuid import uuid4

from core.agents.base.model_config import refresh_model_config_snapshot
from core.config import get_settings
from core.preprocessor import process_content_list
from core.runtime import ExecutionRuntime, ExecutionStage, bind_execution_runtime, reset_execution_runtime
from core.utils.cancellation import CancellationRequested, CancellationToken
from core.utils.diagnostics import sanitize_diagnostic
from core.utils.llm_errors import is_retryable_llm_error
from core.utils.unicode_sanitizer import strip_surrogates

from .events import PipelineEvent, PipelineFailureReport, PipelineSink
from .outcome import (
    FailureCode,
    Phase,
    PipelineFailure,
    classify_failure,
    incomplete_appraisal_code,
    iter_exception_chain,
)
from .workspace import Workspace, prepare_workspace

logger = logging.getLogger(__name__)


class ReportNotProduced(RuntimeError):
    """Reporter 本次尝试没有产出有效报告，可以重试。"""


class AppraisalPipeline:
    """单个文档的评价流水线；入口差异只体现在 sink。"""

    REPORTER_MAX_RETRIES = 3
    REPORTER_RETRY_BASE_DELAY = 1.0
    # 工作流提示词固定为英文，避免被界面语言带偏。
    WORKFLOW_LANG = "en"
    CANCEL_POLL_INTERVAL = 0.2

    def __init__(
        self,
        sink: PipelineSink | None = None,
        *,
        output_subdir: Optional[str] = None,
        cancellation_token: Optional[CancellationToken] = None,
        executor: Optional[ThreadPoolExecutor] = None,
        runtime: Optional[ExecutionRuntime] = None,
        refresh_model_config_on_start: bool = True,
        strict_workflow: bool = False,
        analysis_engine: str | None = None,
    ):
        """
        Args:
            output_subdir: 输出子目录 (如 "batch_1", "batch_2")
            refresh_model_config_on_start: 是否在任务开始时刷新 LLM 运行时配置
            strict_workflow: 是否将部分条目失败视为任务失败
            analysis_engine: 本任务使用的评价引擎；为空时沿用全局配置
        """
        self.sink = sink or PipelineSink()
        self.output_subdir = output_subdir
        self.cancellation_token = cancellation_token
        self.executor = executor
        self.runtime = runtime
        self.refresh_model_config_on_start = refresh_model_config_on_start
        self.strict_workflow = strict_workflow
        self.analysis_engine = analysis_engine
        self.workflow_lang = self.WORKFLOW_LANG

        self._cancelled = False
        self._cancel_notified = False
        self._phase = Phase.ORCHESTRATOR
        self._token_tracker = None
        self._failed_details: dict = {}
        self._failed_exceptions: dict = {}
        self._load_timeouts(get_settings())

    def _load_timeouts(self, settings) -> None:
        self.reporter_timeout = settings.app.batch_reporter_timeout
        self.mineru_health_timeout = settings.app.batch_mineru_health_timeout
        self.mineru_parse_timeout = settings.app.batch_mineru_parse_timeout

    # ------------------------------------------------------------------ events

    def _status(self, phase: Phase, key: str, state: str = "running", **params) -> None:
        self._phase = phase
        self.sink.emit(PipelineEvent("status", phase, key, state, params))

    def _agent(self, agent: str, key: str, progress: int, total: int, state: str = "running", **params) -> None:
        self._phase = Phase(agent.lower())
        self.sink.emit(PipelineEvent("agent", self._phase, key, state, params, agent=agent, progress=progress, total=total))

    async def _item_progress(self, skill_id: str, title: str, status: str, duration: float = None) -> None:
        try:
            await self.sink.item_progress(skill_id, title, status, duration)
        except Exception as error:
            logger.warning("发送条目进度失败: %s", error)

    async def _summary(self, total: int, completed: int, elapsed: float) -> None:
        try:
            await self.sink.summary(total, completed, elapsed)
        except Exception as error:
            logger.warning("发送汇总进度失败: %s", error)

    # ------------------------------------------------------------ cancellation

    def cancel(self) -> None:
        self._cancelled = True
        if self.cancellation_token:
            self.cancellation_token.cancel("stop_requested")

    def is_cancelled(self) -> bool:
        return self._cancelled or bool(self.cancellation_token and self.cancellation_token.cancelled())

    def _raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            self._cancelled = True
            raise CancellationRequested("任务已取消")

    def _notify_cancelled(self, task_id: str) -> None:
        """确保取消终态只通知一次。"""
        if self.is_cancelled() and not self._cancel_notified:
            self._cancelled = True
            self._cancel_notified = True
            self.sink.cancelled(task_id)

    async def _sleep_with_cancel(self, delay: float) -> bool:
        """带取消检查的等待。"""
        remaining = max(delay, 0.0)
        while remaining > 0:
            if self.is_cancelled():
                return False
            interval = min(0.1, remaining)
            await asyncio.sleep(interval)
            remaining -= interval
        return not self.is_cancelled()

    async def _await_unless_cancelled(self, awaitable):
        """等待不会自行检查取消的长耗时阶段，收到停止请求时立即中断。"""
        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.CANCEL_POLL_INTERVAL)
                if done:
                    return task.result()
                self._raise_if_cancelled()
        finally:
            if not task.done():
                task.cancel()

    def get_token_usage(self) -> dict:
        return self._token_tracker.to_dict() if self._token_tracker else {}

    # ---------------------------------------------------------------- running

    async def run(self, task_id: str, pdf_path: str, reuse_task_id: Optional[str] = None,
                  resume: bool = False, reuse_output_subdir: Optional[str] = None) -> None:
        """在统一执行运行时与任务级模型配置快照中运行单个文档。"""
        if self.refresh_model_config_on_start:
            settings = refresh_model_config_snapshot()
            self._load_timeouts(settings)
            logger.info("任务开始刷新 LLM 配置: provider=%s model=%s base_url=%s",
                        settings.llm.provider, settings.llm.model_name, settings.llm.base_url)
        owns_runtime = self.runtime is None
        if owns_runtime:
            self.runtime = ExecutionRuntime.from_settings(
                get_settings(), executor=self.executor, owns_executor=self.executor is None,
            )
        from core.llm.context import bind_llm_settings, reset_llm_settings
        llm_token = bind_llm_settings()
        runtime_token = bind_execution_runtime(self.runtime)
        try:
            await self._execute(task_id, pdf_path, reuse_task_id=reuse_task_id, resume=resume,
                                reuse_output_subdir=reuse_output_subdir)
        finally:
            reset_llm_settings(llm_token)
            reset_execution_runtime(runtime_token)
            if owns_runtime:
                runtime, self.runtime = self.runtime, None
                await runtime.aclose()

    async def _execute(self, task_id, pdf_path, *, reuse_task_id, resume, reuse_output_subdir) -> None:
        current_task_id = task_id
        started_at = time.monotonic()
        self._cancelled = False
        self._cancel_notified = False
        self._phase = Phase.ORCHESTRATOR
        self._failed_details, self._failed_exceptions = {}, {}
        try:
            self._raise_if_cancelled()
            from core.llm.context import RequestBudget, current_llm_settings
            llm = current_llm_settings()
            RequestBudget(llm.context_window, llm.model_name).require_configured()

            pdf_file = Path(pdf_path)
            if not pdf_file.exists():
                raise PipelineFailure(FailureCode.INPUT_NOT_FOUND, f"PDF 文件不存在: {pdf_path}", path=pdf_path)

            workspace = await self._prepare(task_id, pdf_file, resume=resume, reuse_task_id=reuse_task_id,
                                            reuse_output_subdir=reuse_output_subdir)
            current_task_id = workspace.task_id
            self._raise_if_cancelled()
            await self._parse(workspace, pdf_file)
            self._raise_if_cancelled()
            await self._build_evidence(workspace, pdf_file)

            # 显式执行两阶段流程（批量评价 -> 报告生成）
            self._status(Phase.ORCHESTRATOR, "status.starting_agents")
            self._agent("Orchestrator", "agent.orchestrator_start", 0, 3)
            from core.utils.token_tracker import TokenUsageTracker, set_global_tracker
            self._token_tracker = TokenUsageTracker(task_id=workspace.task_id)
            set_global_tracker(self._token_tracker)

            await self._run_batch_appraisal(workspace.task_id)
            self._raise_if_cancelled()
            await self._run_reporter_with_retries(workspace.task_id)
            self._raise_if_cancelled()
            self._agent("Reporter", "agent.report_done", 3, 3, state="done")

            self._token_tracker.set_elapsed_seconds(time.monotonic() - started_at)
            self._token_tracker.save(workspace.weaver_dir / "token_usage.json")
            self._finish(workspace.task_id)
        except Exception as error:
            if self.is_cancelled():
                logger.info("[%s] 任务已按停止请求终止", current_task_id)
                self._notify_cancelled(current_task_id)
                return
            logger.error("评价任务失败\n%s", sanitize_diagnostic(error, limit=None))
            self.sink.failed(PipelineFailureReport(
                code=classify_failure(error, self._phase),
                phase=self._phase,
                error=error,
                details=error.details if isinstance(error, PipelineFailure) else {},
                item_failures=dict(self._failed_details),
            ))
        finally:
            from core.utils.token_tracker import set_global_tracker
            set_global_tracker(None)

    async def _prepare(self, task_id, pdf_file, *, resume, reuse_task_id, reuse_output_subdir) -> Workspace:
        from core.agents.tools.task_manifest import ensure_task_manifest

        self._status(Phase.ORCHESTRATOR, "status.init_task_dir")
        workspace = await prepare_workspace(
            self.runtime, pdf_file, task_id, output_subdir=self.output_subdir, analysis_engine=self.analysis_engine,
            resume=resume, reuse_task_id=reuse_task_id, reuse_output_subdir=reuse_output_subdir,
        )
        if workspace.copied_from_round:
            self._status(Phase.ORCHESTRATOR, "status.reusing_task", task_id=workspace.task_id)
        self.sink.task_ready(workspace.task_id)
        await self.runtime.run_blocking(
            ExecutionStage.BLOCKING, ensure_task_manifest, str(workspace.task_dir), pdf_file, self.analysis_engine,
            serialization_key=("manifest", workspace.task_id),
        )
        self._status(Phase.ORCHESTRATOR, "status.task_id", task_id=workspace.task_id)
        return workspace

    # ---------------------------------------------------------------- parsing

    async def _parse(self, workspace: Workspace, pdf_file: Path) -> None:
        from core.parsers.mineru_cache import restore_document_cache

        if workspace.source_path.exists():
            self._status(Phase.PARSING, "status.doc_found_skip", state="done")
            return
        cached = await self.runtime.run_blocking(
            ExecutionStage.BLOCKING, restore_document_cache, pdf_file.stem,
            target_source_path=workspace.source_path, target_prep_dir=str(workspace.prep_dir),
            serialization_key=("mineru-cache", pdf_file.stem),
        )
        if cached:
            self._status(Phase.PARSING, "status.reusing_mineru_cache", state="done", pages=cached.pages)
            logger.info("[%s] 已复用 MinerU 缓存，跳过解析: guideline_id=%s cache_dir=%s",
                        workspace.task_id, pdf_file.stem, cached.cache_dir)
        if not workspace.source_path.exists():
            await self._parse_with_mineru(workspace, pdf_file)

    async def _parse_with_mineru(self, workspace: Workspace, pdf_file: Path) -> None:
        """健康检查后带重试解析；停止请求不等解析超时即生效。"""
        from core.parsers import MinerUParser

        mineru = get_settings().mineru
        if not mineru.server_url:
            raise PipelineFailure(FailureCode.PARSER_NOT_CONFIGURED, "MinerU 服务未配置 (MINERU_SERVER_URL)")

        self._status(Phase.PARSING, "status.parsing_mineru")
        parser = MinerUParser()
        try:
            service_ok = await asyncio.wait_for(parser.health_check(), timeout=self.mineru_health_timeout)
        except asyncio.TimeoutError as exc:
            raise PipelineFailure(
                FailureCode.PARSE_TIMEOUT,
                f"MinerU 健康检查超时 ({self.mineru_health_timeout}s): {parser.server_url}",
                seconds=self.mineru_health_timeout,
            ) from exc
        if not service_ok:
            self._raise_if_cancelled()
            raise PipelineFailure(FailureCode.PARSER_UNAVAILABLE, f"MinerU health check returned false: {parser.server_url}")

        attempts = mineru.parse_retry_attempts
        last_error: BaseException | None = None
        for attempt in range(1, attempts + 1):
            self._raise_if_cancelled()
            try:
                result = await self._await_unless_cancelled(parser.parse_pdf_to_file(
                    pdf_path=str(pdf_file),
                    source_path=str(workspace.source_path),
                    prep_dir=str(workspace.prep_dir),
                    timeout=self.mineru_parse_timeout,
                ))
                if result.get("status") == "success":
                    self._status(Phase.PARSING, "status.parse_done_mineru", state="done", pages=result.get("pages", 0))
                    return
                last_error = RuntimeError(result.get("error", "Unknown error"))
            except CancellationRequested:
                raise
            except Exception as error:
                last_error = error
            logger.warning("MinerU 解析失败 (尝试 %s/%s): %r", attempt, attempts, last_error)
            if attempt < attempts:
                self._status(Phase.PARSING, "status.parse_retry", delay=mineru.parse_retry_delay,
                             attempt=attempt, max_retries=attempts)
                await self._sleep_with_cancel(mineru.parse_retry_delay)

        timed_out = isinstance(last_error, TimeoutError)
        raise PipelineFailure(
            FailureCode.PARSE_TIMEOUT if timed_out else FailureCode.PARSE_FAILED,
            f"MinerU 解析失败，已尝试 {attempts} 次",
            **({"seconds": self.mineru_parse_timeout} if timed_out else {}),
        ) from last_error

    # --------------------------------------------------------------- evidence

    def _reusable_evidence_pool(self, evidence_pool: Path, content_list: Path, task_id: str) -> bool:
        """续跑时只复用与当前解析结果一致的证据池；无效文件保留后重建。"""
        from core.preprocessor import EvidencePool
        from core.preprocessor.label_checkpoint import digest_file

        try:
            if not content_list.is_file():
                raise FileNotFoundError(f"无法校验证据池，缺少解析结果: {content_list}")
            EvidencePool.load_file(evidence_pool, expected_source_digest=digest_file(content_list))
            return True
        except (OSError, ValueError, KeyError, TypeError):
            logger.exception("现有证据池无效，将从原始解析结果重建: task=%s", task_id)
            preserved = evidence_pool.with_name(f"{evidence_pool.stem}.invalid-{uuid4().hex[:8]}.json")
            evidence_pool.rename(preserved)
            logger.warning("无效证据池已保留: %s", preserved)
            return False

    async def _build_evidence(self, workspace: Workspace, pdf_file: Path) -> None:
        content_list = workspace.prep_dir / f"{pdf_file.stem}_content_list.json"
        evidence_pool = workspace.prep_dir / f"{pdf_file.stem}_evidence_pool.json"
        if (workspace.may_reuse_evidence and evidence_pool.exists()
                and self._reusable_evidence_pool(evidence_pool, content_list, workspace.task_id)):
            self._status(Phase.PREPROCESSING, "status.reusing_evidence", state="done")
        else:
            self._status(Phase.PREPROCESSING, "status.building_evidence")
            if not content_list.exists():
                raise FileNotFoundError(f"MinerU 未生成 content_list.json: {content_list}")
            try:
                result = await process_content_list(
                    content_list_path=str(content_list),
                    output_path=str(evidence_pool),
                    pdf_path=str(pdf_file),  # 用于构建 docindex
                    lang=self.workflow_lang,
                    progress_callback=lambda _message: self._status(Phase.PREPROCESSING, "status.building_evidence"),
                )
            except Exception:
                logger.exception("预处理过程异常: task=%s", workspace.task_id)
                self._status(Phase.PREPROCESSING, "status.preprocess_error", state="error")
                raise
            if result["status"] != "success":
                self._status(Phase.PREPROCESSING, "status.evidence_failed", state="error")
                raise RuntimeError(f"证据池构建失败: {result.get('error') or 'Unknown error'}")
            self._status(Phase.PREPROCESSING, "status.evidence_done", state="done")
        if not evidence_pool.is_file() or evidence_pool.stat().st_size == 0:
            raise RuntimeError("证据池文件缺失或为空")

    # -------------------------------------------------------------- appraisal

    async def _run_batch_appraisal(self, task_id: str) -> None:
        """只评价缺失条目；全部失败时抛出原始异常，严格模式下缺项即失败。"""
        from core.agents.tools.appraiser_batch import appraise_all
        from core.agents.tools.appraiser_tools import list_evaluations
        from core.skills import list_available_skills

        self._agent("Appraiser", "agent.batch_appraise_start", 1, 3)
        existing = list_evaluations(task_id)
        if existing["status"] != "success":
            raise RuntimeError(existing["error"])
        all_items = list_available_skills(enabled_only=True)
        missing = [item for item in all_items if item not in existing["evaluations"]]

        async def report_total_progress(_total: int, completed: int, elapsed: float) -> None:
            await self._summary(len(all_items), len(existing["evaluations"]) + completed, elapsed)

        result = await appraise_all(
            task_id,
            skill_ids=missing,
            lang=self.workflow_lang,
            on_item_progress=self._item_progress,
            on_summary=report_total_progress,
            cancellation_token=self.cancellation_token,
            executor=self.executor,
            runtime=self.runtime,
            analysis_engine=self.analysis_engine,
            collect_exceptions=True,
        )
        self._raise_if_cancelled()

        status = result.get("status", "error")
        failed = result.get("failed", {})
        self._failed_details = result.get("failed_details", {})
        self._failed_exceptions = result.get("exceptions", {})
        available = list_evaluations(task_id)
        if available["status"] != "success":
            raise RuntimeError(available["error"])
        item_errors = list(self._failed_exceptions.values())
        if status == "error" and not available["evaluations"]:
            if len(item_errors) == 1:
                raise item_errors[0]
            if item_errors:
                raise ExceptionGroup("批量条目评价失败", item_errors)
            raise PipelineFailure(FailureCode.APPRAISAL_FAILED, f"批量条目评价失败: {failed or 'Unknown error'}")
        if status in {"partial", "error"}:
            if self.strict_workflow:
                error = PipelineFailure(incomplete_appraisal_code(item_errors), f"批量条目评价不完整: {failed or '条目结果缺失'}")
                if item_errors:
                    raise error from ExceptionGroup("条目评价失败", item_errors)
                raise error
            logger.warning("[%s] 批量条目评价部分失败，将继续进入报告阶段: %s", task_id, failed)

        self._agent("Appraiser", "agent.batch_appraise_done", 1, 3, state="done")
        self._status(Phase.REPORTER, "status.entering_report_phase")

    # ---------------------------------------------------------------- reports

    def _report_paths(self, task_id: str) -> tuple[Optional[Path], Optional[Path]]:
        from core.agents.tools.public_tools import get_task_paths

        paths = get_task_paths(task_id)
        if not paths:
            return None, None
        task_dir = Path(paths["task_dir"])
        return task_dir / "appraisal_report.json", task_dir / "appraisal_report.md"

    def _report_exists(self, task_id: str) -> bool:
        """报告 JSON 与 Markdown 都存在，且 JSON 属于本任务。"""
        json_path, md_path = self._report_paths(task_id)
        if not (json_path and md_path and json_path.exists() and md_path.exists()):
            return False
        try:
            report = json.loads(json_path.read_text(encoding="utf-8"))
            return report.get("task_id") == task_id and report.get("status", "complete") in {"complete", "partial"}
        except (OSError, ValueError, TypeError):
            return False

    @staticmethod
    def _is_retryable_reporter_error(error: Exception) -> bool:
        """传输故障（含阶段超时）、无效模型输出和未产出报告可重试；配置错误不重试。"""
        return any(
            isinstance(candidate, (ReportNotProduced, ValueError, TypeError)) or is_retryable_llm_error(candidate)
            for candidate in iter_exception_chain(error)
        )

    def _build_reporter_prompt(self, task_id: str) -> str:
        """构造固定英文的 Reporter 用户提示，避免被界面语言带偏。"""
        return f"""Please start the AGREE-II final report generation task.

Task information:
- task_id: {task_id}

Follow the Reporter workflow strictly:
1. Call get_guideline_title(task_id)
2. Call load_all_evaluations(task_id)
3. Analyze the results and call generate_appraisal_report(...) to save the report
4. Return a concise report summary
"""

    async def _run_reporter_once(self, task_id: str) -> None:
        """执行一次 Reporter；只有报告工具返回成功才算产出报告。"""
        from core.agents import create_reporter_agent
        from core.utils.llm_limiter import llm_async_slot
        from google.adk import runners
        from google.genai import types

        runner = runners.InMemoryRunner(agent=create_reporter_agent(self.workflow_lang), app_name="ama_web_reporter")
        session = await runner.session_service.create_session(app_name="ama_web_reporter", user_id="web_user")
        message = types.Content(role="user", parts=[types.Part(text=strip_surrogates(self._build_reporter_prompt(task_id)))])
        succeeded = False
        tool_error: str | None = None
        try:
            async with self.runtime.stage_slot(ExecutionStage.REPORTER):
                async with asyncio.timeout(self.reporter_timeout):
                    async with llm_async_slot("reporter:run_async"):
                        async for event in runner.run_async(user_id="web_user", session_id=session.id, new_message=message):
                            if getattr(event, "usage_metadata", None) and self._token_tracker:
                                self._token_tracker.add_usage(event.usage_metadata)
                            for part in (event.content.parts if getattr(event, "content", None) else []):
                                if getattr(part, "function_call", None):
                                    logger.info("[%s] Reporter 工具调用: %s", task_id, part.function_call.name or "")
                                elif getattr(part, "function_response", None):
                                    response = part.function_response
                                    name = getattr(response, "name", "") or ""
                                    logger.info("[%s] Reporter 工具完成: %s", task_id, name)
                                    if "generate_appraisal_report" in name.lower():
                                        payload = getattr(response, "response", None)
                                        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
                                            payload = payload["result"]
                                        succeeded = isinstance(payload, dict) and payload.get("status") == "success"
                                        if succeeded:
                                            tool_error = None
                                        elif isinstance(payload, dict):
                                            tool_error = str(payload.get("error") or payload)
                                elif getattr(part, "text", None) and part.text.strip():
                                    logger.info("[%s] Reporter 输出: %s", task_id, part.text.strip()[:300])
                            if self.is_cancelled():
                                logger.info("[%s] Reporter 当前尝试结束后检测到取消", task_id)
                                break
        except TimeoutError as exc:
            raise PipelineFailure(
                FailureCode.REPORT_TIMEOUT, f"Reporter 执行超时 ({self.reporter_timeout}s)", seconds=self.reporter_timeout,
            ) from exc
        if self.is_cancelled():
            return
        if not succeeded:
            raise ReportNotProduced(f"Reporter 本次请求未成功生成报告: {tool_error or '未收到成功的报告工具响应'}")

    async def _run_reporter_with_retries(self, task_id: str) -> None:
        """执行 Reporter 阶段；本次尝试必须写出新的报告文件。"""
        self._agent("Reporter", "agent.reporter_working", 2, 3)
        for attempt in range(1, self.REPORTER_MAX_RETRIES + 1):
            if self.is_cancelled():
                return
            logger.info("[%s] 开始 Reporter 尝试 attempt=%s/%s", task_id, attempt, self.REPORTER_MAX_RETRIES)
            try:
                json_path, _ = self._report_paths(task_id)
                previous_mtime = json_path.stat().st_mtime_ns if json_path and json_path.exists() else None
                await self._run_reporter_once(task_id)
                if self.is_cancelled():
                    return
                if not self._report_exists(task_id) or (previous_mtime is not None and json_path.stat().st_mtime_ns == previous_mtime):
                    raise ReportNotProduced("Reporter 执行成功，但缺少 appraisal_report.json 或 appraisal_report.md")
                logger.info("[%s] Reporter 阶段完成", task_id)
                return
            except Exception as error:
                retryable = self._is_retryable_reporter_error(error)
                logger.warning("[%s] Reporter 失败 attempt=%s/%s retryable=%s error=%s: %s",
                               task_id, attempt, self.REPORTER_MAX_RETRIES, retryable, type(error).__name__, error)
                if not retryable or attempt >= self.REPORTER_MAX_RETRIES:
                    raise
                self._status(Phase.REPORTER, "status.report_retry", attempt=attempt, max_retries=self.REPORTER_MAX_RETRIES)
                if not await self._sleep_with_cancel(self.REPORTER_RETRY_BASE_DELAY * (2 ** (attempt - 1))):
                    return

    def _finish(self, task_id: str) -> None:
        """完整报告才算完成；缺项时严格模式失败，否则发布部分结果。"""
        report_path, _ = self._report_paths(task_id)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") == "complete" and not self._failed_details:
            self.sink.completed(task_id)
            return
        for item_id, error in self._failed_exceptions.items():
            logger.error("[%s] 条目 %s 失败\n%s", task_id, item_id, sanitize_diagnostic(error, limit=None))
        missing = report.get("missing_items", [])
        if self.strict_workflow:
            raise PipelineFailure(
                incomplete_appraisal_code(self._failed_exceptions.values()),
                f"批量条目评价不完整: 缺失 {', '.join(missing)}",
            )
        self.sink.partial(task_id, missing, self._failed_details)
