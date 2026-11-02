"""AMA MCP Streamable HTTP 服务和 Bearer 鉴权入口。"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator, Awaitable, Callable

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from access import MCPAccessMiddleware
from errors import MCPErrorCode, MCPJobError
from service import MCPJobService

logger = logging.getLogger(__name__)


mcp_server = FastMCP(
    "AMA PDF Appraisal",
    instructions="""
AMA appraises one clinical practice guideline PDF and produces a complete AGREE II quality appraisal report. Use this server when a user asks to analyze a specific PDF, assess guideline quality, extract appraisal evidence, perform an AGREE II appraisal, or generate an AMA report. The raw PDF must first be uploaded to the companion HTTP upload endpoint to obtain an upload_id. Never pass Base64, extracted text, a local path, or a URL as upload_id.

When to use this server:
1. Use it only for a specific PDF that the user has provided or identified and explicitly wants appraised.
2. Do not create a job for general questions about AMA, AGREE II, or appraisal methodology that do not require analysis of a specific PDF.
3. If the Agent cannot upload a local attachment over HTTP, tell the user that the PDF must be uploaded first. Never invent an upload_id, PDF content, job progress, or appraisal result.

Input requirements:
- Upload exactly one original PDF per request. The PDF must be no larger than 10 MiB and must contain no more than 1,000 pages.
- The upload endpoint is /ama/mcp/uploads/ on the same AMA host as this MCP endpoint. Use the same Authorization: Bearer API key.
- Upload with multipart/form-data using a single file field named file. A successful response includes upload_id, the file digest, page count, and expires_at.
- submit_pdf_appraisal accepts only an upload_id returned by that endpoint. It does not accept file bytes, Base64, a local path, a URL, or extracted PDF text.
- An upload_id can create only one new job before it expires. A network retry using the same idempotency_key can retrieve the original job.
- An idempotency_key identifies one logical submission. Reuse it for network retries, but generate a new value when the PDF or analysis method changes.
- analysis_engine accepts llm, rule, or hybrid. Omit it when the user has no preference so that the server records its configured default.
- llm emphasizes general semantic reasoning. rule uses registered rule paths where available, while unregistered items and some rule-processing steps may still use an LLM. hybrid selects a path per item and is normally the default.

Required workflow:
1. POST the user's original PDF to /ama/mcp/uploads/ and save the returned upload_id. An upload response is not an appraisal job.
2. Call submit_pdf_appraisal once with that upload_id and save the returned job_id. Submission is asynchronous and does not return the final report.
3. Poll get_appraisal_status for the same job_id every 5-10 seconds. For queued, report the queue state; for running, report phase and progress; for stopping, wait for a terminal state.
4. Call get_appraisal_result only when status is completed and result_available is true, then answer from the returned report.
5. Stop polling when status is failed or cancelled and explain the returned error. completed with result_available=false means the retained result has expired.
6. Call cancel_appraisal only when the user explicitly asks to stop the job. A cancelled job cannot resume.

Error handling:
- Tool errors contain code, message, and retryable. Wait and retry only when retryable is true; otherwise correct the input or inform the user.
- JOB_NOT_READY means the appraisal is still unfinished. Resume status polling instead of submitting another job.
- UPLOAD_NOT_FOUND or UPLOAD_EXPIRED requires a fresh PDF upload and a new upload_id.
- UPLOAD_ALREADY_USED means that upload_id is bound to another job. Unless this is the same idempotent retry, upload the PDF again.
- IDEMPOTENCY_CONFLICT means the key was reused for a different PDF or analysis method. Generate a new key.
- A failed job requires a new submission and idempotency_key. Continue tracking the original job while it is queued, running, or stopping.

Response requirements:
- State progress and appraisal conclusions only from tool responses and the final report.
- After retrieving the report, summarize the main scores, evidence, limitations, and material uncertainty. Never present unfinished or missing items as complete.
""".strip(),
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
)
_service = MCPJobService()
_submit_slots = asyncio.Semaphore(2)
_registered_tools: list[tuple[Callable, str, str]] = []


def _register_tool(*, name: str, description: str) -> Callable:
    """注册工具并保存创建新运行实例所需的公开元数据。"""
    def decorator(function: Callable) -> Callable:
        _registered_tools.append((function, name, description))
        return mcp_server.tool(name=name, description=description)(function)

    return decorator


def initialize_mcp() -> None:
    """在 Web 服务启动时校验配置并初始化队列。"""
    _service.initialize()


def _tool_call(function: Callable[[], dict]) -> Awaitable[dict]:
    """把 PDF/SQLite 文件操作移出 Web 事件循环。"""
    return asyncio.to_thread(function)


def _error_result(error: MCPJobError) -> CallToolResult:
    """把业务异常转换为 MCP 协议级失败结果。"""
    payload = {"error": error.to_payload()}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
        isError=True,
    )


async def _run_tool(function: Callable[[], dict]) -> dict | CallToolResult:
    """执行工具并保证任何异常都以稳定结构通知 Agent。"""
    try:
        return await _tool_call(function)
    except MCPJobError as exc:
        if exc.internal_detail:
            logger.warning("AMA MCP 工具失败: code=%s detail=%s", exc.code, exc.internal_detail)
        return _error_result(exc)
    except Exception as exc:
        logger.exception("AMA MCP 工具发生未预期异常")
        return _error_result(
            MCPJobError(MCPErrorCode.INTERNAL_ERROR, internal_detail=str(exc))
        )


@_register_tool(
    name="submit_pdf_appraisal",
    description="""
Create an asynchronous AMA appraisal job when the user asks to analyze one specific PDF, perform an AGREE II quality appraisal, extract appraisal evidence, or generate an appraisal report.

Before calling:
- Upload exactly one original PDF with POST /ama/mcp/uploads/ and obtain upload_id.
- Pass only that upload_id. Do not pass PDF bytes, Base64, extracted text, a local path, a URL, or multiple files.
- An upload_id can create only one new job before it expires. Reuse the same upload_id and idempotency_key only for a network retry of the same logical request.
- Generate a stable idempotency_key for one logical submission. The same key, PDF, and analysis method returns the original job. Reusing the key for a different PDF or method returns IDEMPOTENCY_CONFLICT.
- analysis_engine may be llm, rule, or hybrid. llm uses the LLM appraisal path; rule uses registered rule paths where available; hybrid selects a path per item. Omit the parameter when the user has no preference so the server uses its configured default.

After calling:
- A successful response immediately returns job_id, status, queue_position, analysis_engine, file metadata, and timestamps. It does not contain the final appraisal report.
- Save job_id and continue with get_appraisal_status. Do not create a duplicate job or infer appraisal conclusions from the submission response.
- UPLOAD_NOT_FOUND, UPLOAD_EXPIRED, and UPLOAD_ALREADY_USED mean the upload credential cannot create a new job. Follow the error to resume the original job or upload the PDF again.
- Input, queue-capacity, and storage failures are returned through error.code, error.message, and error.retryable.
""".strip(),
)
async def submit_pdf_appraisal(
    upload_id: Annotated[
        str,
        Field(
            description=(
                "The upload_id returned by a successful POST /ama/mcp/uploads/ response. "
                "It has the form mcp-upload- followed by 32 lowercase hexadecimal characters. "
                "Pass only this ID, not a filename, path, URL, Base64 value, or PDF text."
            ),
            examples=["mcp-upload-0123456789abcdef0123456789abcdef"],
        ),
    ],
    idempotency_key: Annotated[
        str,
        Field(
            description=(
                "A unique idempotency key generated by the caller for one logical submission. "
                "It must contain 1-128 letters, digits, or . _ : - characters. Reuse the original "
                "value for network retries of the same PDF and analysis method; generate a new "
                "value when either changes."
            ),
            examples=["agent-request-20260822-001"],
        ),
    ],
    analysis_engine: Annotated[
        str | None,
        Field(
            description=(
                "Optional analysis method. llm appraises every item with the LLM path. rule uses "
                "registered rule paths, while unregistered items and some rule-processing steps may "
                "still use an LLM. hybrid dynamically selects Rule or LLM per item from the evidence. "
                "Omit this parameter when the user has no preference; the server records its default "
                "method on the job."
            ),
            json_schema_extra={"enum": ["llm", "rule", "hybrid", None]},
            examples=["hybrid"],
        ),
    ] = None,
) -> dict:
    """提交单个 PDF AMA 评价任务，可为任务选择评价引擎。"""
    async with _submit_slots:
        return await _run_tool(
            lambda: _service.submit(
                upload_id,
                idempotency_key,
                analysis_engine,
            )
        )


@_register_tool(
    name="get_appraisal_status",
    description="""
Track the queue position, execution phase, and item progress for a submitted job_id. Poll every 5-10 seconds and avoid a tight polling loop.

Handle each status as follows:
- queued: The job is waiting in the FIFO queue. queue_position is its current position. Continue waiting and polling.
- running: The job is parsing the PDF, building evidence, appraising items, or generating the report. Report phase and progress.completed/progress.total accurately to the user.
- stopping: A cancellation request is being processed. Continue polling until completed, failed, or cancelled.
- completed: The job finished. Call get_appraisal_result only when result_available is true. false means the retained result has expired.
- failed: Stop polling and read error.code, error.message, and error.retryable. If the user chooses to retry, create a new submission with a new idempotency_key.
- cancelled: Stop polling. Create a new job if the user wants another appraisal.

The response also includes upload_id, filename, analysis_engine, page_count, message, deduplicated, and creation, start, and finish timestamps. Never infer appraisal conclusions from progress fields.
""".strip(),
)
async def get_appraisal_status(
    job_id: Annotated[
        str,
        Field(
            description=(
                "The job_id returned by submit_pdf_appraisal. Reuse it for every status poll after "
                "submission. Do not pass result_task_id, a filename, or an idempotency key."
            ),
            examples=["mcp-job-0123456789abcdef"],
        ),
    ],
) -> dict:
    """查询任务状态。"""
    return await _run_tool(lambda: _service.status(job_id))


@_register_tool(
    name="cancel_appraisal",
    description="""
Request cancellation only when the user explicitly asks to stop a specific AMA job.

Behavior by current status:
- queued: The job immediately becomes cancelled and will not be claimed by a worker.
- running: The job becomes stopping and the active appraisal is asked to stop cooperatively. Cancellation may take a short time; continue polling get_appraisal_status until a terminal state.
- stopping: Cancellation remains in progress and this call is safe to repeat.
- completed, failed, or cancelled: The terminal state is preserved. Existing final results and failure details are not deleted or rewritten.

The tool returns the latest job status. A cancelled job cannot resume. To run the appraisal again, call submit_pdf_appraisal with a new idempotency_key.
""".strip(),
)
async def cancel_appraisal(
    job_id: Annotated[
        str,
        Field(
            description=(
                "The AMA job_id returned by submit_pdf_appraisal. Before cancelling, confirm that "
                "this ID identifies the job the user wants to stop."
            ),
            examples=["mcp-job-0123456789abcdef"],
        ),
    ],
) -> dict:
    """取消任务。"""
    return await _run_tool(lambda: _service.cancel(job_id))


@_register_tool(
    name="get_appraisal_result",
    description="""
Retrieve the final AMA AGREE II appraisal only after get_appraisal_status returns status=completed and result_available=true.

A successful response contains:
- report: the complete appraisal_report.json with AGREE II item appraisals, domain results, evidence, and report metadata.
- token_usage: the model token usage recorded for this job.
- Job metadata including job_id, upload_id, result_task_id, filename, analysis_engine, input_sha256, and page_count.
- No server-local input or output paths.

Error handling:
- queued, running, stopping, or cancelled returns JOB_NOT_READY. Resume get_appraisal_status polling and do not submit another job.
- failed returns the job's stable error code and retryable value. No partial report is returned.
- A completed job whose retained result has expired returns RESULT_EXPIRED.
- A missing, corrupt, or checksum-mismatched report returns RESULT_INVALID.

After receiving report, summarize only its formal scores, key evidence, limitations, and uncertainty. Never invent appraisal content that the tool did not return.
""".strip(),
)
async def get_appraisal_result(
    job_id: Annotated[
        str,
        Field(
            description=(
                "An AMA job_id returned by submit_pdf_appraisal whose status is completed and "
                "result_available is true. Do not pass result_task_id, a filename, or an "
                "idempotency key."
            ),
            examples=["mcp-job-0123456789abcdef"],
        ),
    ],
) -> dict:
    """返回最终评价报告。"""
    return await _run_tool(lambda: _service.result(job_id))


http_app = MCPAccessMiddleware(mcp_server.streamable_http_app())


@asynccontextmanager
async def run_mcp_session() -> AsyncIterator[FastMCP]:
    """为每次 Web 应用生命周期创建独立的 MCP Session Manager。"""
    global _submit_slots

    runtime_server = FastMCP(
        mcp_server.name,
        instructions=mcp_server.instructions,
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
    )
    for function, name, description in _registered_tools:
        runtime_server.add_tool(function, name=name, description=description)

    http_app.app = runtime_server.streamable_http_app()
    _submit_slots = asyncio.Semaphore(2)
    async with runtime_server.session_manager.run():
        yield runtime_server
