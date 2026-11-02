"""
Web 入口的评价运行器

流水线本身在 core.pipeline；这里只把流水线事件翻译成界面消息，并兼容旧回调。
"""
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable, Optional

from core.pipeline import FailureCode, Phase
from core.pipeline.events import PipelineEvent, PipelineFailureReport, PipelineSink
from core.pipeline.runner import AppraisalPipeline
from core.runtime import ExecutionRuntime
from core.utils.cancellation import CancellationToken
from web.backend.app.i18n import get_language
from web.backend.app.i18n.messages import ui_message

logger = logging.getLogger(__name__)

# 失败代码到界面文案；未列出的代码使用通用评价失败提示。
_FAILURE_MESSAGE_KEYS = {
    FailureCode.INPUT_NOT_FOUND: "error.pdf_not_found",
    FailureCode.TASK_INIT_FAILED: "error.init_failed",
    FailureCode.PARSER_NOT_CONFIGURED: "error.mineru_not_configured",
    FailureCode.PARSER_UNAVAILABLE: "error.mineru_unavailable",
    FailureCode.PARSE_TIMEOUT: "error.mineru_timeout",
    FailureCode.PARSE_FAILED: "error.parse_failed",
    FailureCode.LLM_TIMEOUT: "error.llm_timeout",
    FailureCode.REPORT_TIMEOUT: "error.reporter_timeout",
}
# 批量任务按状态原因区分超时类型。
_FAILURE_STATUS_REASONS = {
    FailureCode.PARSE_TIMEOUT: "mineru_timeout",
    FailureCode.REPORT_TIMEOUT: "reporter_timeout",
    FailureCode.LLM_TIMEOUT: "llm_timeout",
    FailureCode.APPRAISAL_INCOMPLETE: "partial",
}

ItemProgress = Callable[[str, str, str, Optional[float]], Awaitable[None]]
Summary = Callable[[int, int, float], Awaitable[None]]


def _noop(*_args) -> None:
    return None


class AppraisalRunner(PipelineSink):
    """
    评价任务运行器（Web 适配层）

    每条事件同时发送结构化消息（on_message）和旧文字回调（on_status/on_agent/on_error）。
    """

    def __init__(
        self,
        on_status: Optional[Callable[[str, str], None]] = None,
        on_agent: Optional[Callable[[str, str, int, int, Optional[str]], None]] = None,
        on_complete: Optional[Callable[[str], None]] = None,
        on_cancelled: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_partial: Optional[Callable[[str, list[str], dict], None]] = None,
        on_task_ready: Optional[Callable[[str], None]] = None,
        lang: Optional[str] = None,
        output_subdir: Optional[str] = None,
        cancellation_token: Optional[CancellationToken] = None,
        executor: Optional[ThreadPoolExecutor] = None,
        runtime: Optional[ExecutionRuntime] = None,
        refresh_model_config_on_start: bool = True,
        strict_workflow: bool = False,
        analysis_engine: str | None = None,
        on_message: Optional[Callable[[dict], None]] = None,
        on_item_progress: Optional[ItemProgress] = None,
        on_summary: Optional[Summary] = None,
    ):
        """
        Args:
            on_status: 状态回调 (type, content)
            on_agent: 智能体回调 (agent, content, progress, total, prompt)
            on_message: 结构化界面消息回调 (message)
            on_item_progress / on_summary: 条目与汇总进度；未提供汇总回调时以代理进度消息代替
            lang: 界面语言 (zh/en)，默认英语
            其余参数见 AppraisalPipeline
        """
        self.on_status = on_status or _noop
        self.on_agent = on_agent or _noop
        self.on_complete = on_complete or _noop
        self.on_cancelled = on_cancelled or _noop
        self.on_error = on_error or _noop
        self.on_partial = on_partial or _noop
        self.on_task_ready = on_task_ready or _noop
        self.on_message = on_message
        self.on_item_progress = on_item_progress
        self.on_summary = on_summary
        self.lang = get_language(lang)
        self._status_reason = "normal"
        self.failed_details: dict = {}
        self.pipeline = AppraisalPipeline(
            self,
            output_subdir=output_subdir,
            cancellation_token=cancellation_token,
            executor=executor,
            runtime=runtime,
            refresh_model_config_on_start=refresh_model_config_on_start,
            strict_workflow=strict_workflow,
            analysis_engine=analysis_engine,
        )

    # ----------------------------------------------------------- entry points

    async def run(self, task_id: str, pdf_path: str, reuse_task_id: Optional[str] = None,
                  resume: bool = False, reuse_output_subdir: Optional[str] = None) -> None:
        self._status_reason = "normal"
        self.failed_details = {}
        await self.pipeline.run(task_id, pdf_path, reuse_task_id=reuse_task_id, resume=resume,
                                reuse_output_subdir=reuse_output_subdir)

    def cancel(self) -> None:
        self.pipeline.cancel()

    def get_token_usage(self) -> dict:
        return self.pipeline.get_token_usage()

    def get_status_reason(self) -> str:
        """返回最近一次失败或部分完成的原因。"""
        return self._status_reason

    # ------------------------------------------------------------------- sink

    def _publish(self, message: dict) -> None:
        if self.on_message:
            self.on_message(message)

    def emit(self, event: PipelineEvent) -> None:
        message = ui_message(event.key, self.lang, field="content", **event.params)
        if event.kind == "agent":
            self.on_agent(event.agent, message["content"], event.progress, event.total, None)
            self._publish({"type": "agent", "agent": event.agent, "phase": event.phase, "state": event.state,
                           "progress": event.progress, "total": event.total, "prompt": None, **message})
        else:
            self.on_status(event.kind, message["content"])
            self._publish({"type": event.kind, "phase": event.phase, "state": event.state, **message})

    def task_ready(self, task_id: str) -> None:
        self.on_task_ready(task_id)

    async def item_progress(self, skill_id: str, title: str, status: str, duration: float | None) -> None:
        if self.on_item_progress:
            await self.on_item_progress(skill_id, title, status, duration)

    async def summary(self, total: int, completed: int, elapsed: float) -> None:
        if self.on_summary:
            await self.on_summary(total, completed, elapsed)
        else:
            self.emit(PipelineEvent("agent", Phase.APPRAISER, "agent.batch_progress",
                                    params={"completed": completed, "total": total},
                                    agent="Appraiser", progress=1, total=3))

    def completed(self, task_id: str) -> None:
        self.on_complete(task_id)

    def partial(self, task_id: str, missing: list[str], failures: dict) -> None:
        self._status_reason = "partial"
        self.failed_details = failures
        self.on_partial(task_id, missing, failures)

    def cancelled(self, task_id: str) -> None:
        self.on_cancelled(task_id)

    def failed(self, report: PipelineFailureReport) -> None:
        """按失败代码选择文案；条目失败摘要放在诊断开头，避免被截断。"""
        self._status_reason = _FAILURE_STATUS_REASONS.get(report.code, self._status_reason)
        self.failed_details = report.item_failures
        items = report.item_summary()
        diagnostic = (f"item errors: {items}\n" if items else "") + "".join(traceback.format_exception(report.error))
        message = ui_message(_FAILURE_MESSAGE_KEYS.get(report.code, "error.appraisal_failed"), self.lang,
                             field="content", diagnostic=diagnostic, **report.details)
        self.on_error(message["content"])
        self._publish({"type": "error", "phase": report.phase, "state": "error", "code": report.code, **message})
