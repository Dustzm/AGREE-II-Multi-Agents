"""AMA MCP 稳定错误码。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.pipeline import FailureCode


class MCPErrorCode(StrEnum):
    """MCP HTTP、工具和异步任务共用的错误码。"""

    MCP_DISABLED = "MCP_DISABLED"
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_CONTENT_LENGTH = "INVALID_CONTENT_LENGTH"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
    INVALID_FILE_COUNT = "INVALID_FILE_COUNT"
    INVALID_FILENAME = "INVALID_FILENAME"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    INVALID_IDEMPOTENCY_KEY = "INVALID_IDEMPOTENCY_KEY"
    INVALID_ANALYSIS_ENGINE = "INVALID_ANALYSIS_ENGINE"
    INVALID_UPLOAD_ID = "INVALID_UPLOAD_ID"
    UPLOAD_NOT_FOUND = "UPLOAD_NOT_FOUND"
    UPLOAD_EXPIRED = "UPLOAD_EXPIRED"
    UPLOAD_ALREADY_USED = "UPLOAD_ALREADY_USED"
    PDF_TOO_LARGE = "PDF_TOO_LARGE"
    INVALID_PDF = "INVALID_PDF"
    PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    QUEUE_FULL = "QUEUE_FULL"
    JOB_CREATE_FAILED = "JOB_CREATE_FAILED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_NOT_READY = "JOB_NOT_READY"
    RESULT_INVALID = "RESULT_INVALID"
    RESULT_EXPIRED = "RESULT_EXPIRED"
    LOW_DISK_SPACE = "LOW_DISK_SPACE"
    PARSE_FAILED = "PARSE_FAILED"
    EVIDENCE_BUILD_FAILED = "EVIDENCE_BUILD_FAILED"
    APPRAISAL_INCOMPLETE = "APPRAISAL_INCOMPLETE"
    REPORT_GENERATION_FAILED = "REPORT_GENERATION_FAILED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    STORAGE_ERROR = "STORAGE_ERROR"
    WORKER_INTERRUPTED = "WORKER_INTERRUPTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class MCPErrorSpec:
    """单个错误码的稳定外部解释。"""

    message: str
    retryable: bool


ERROR_SPECS: dict[MCPErrorCode, MCPErrorSpec] = {
    MCPErrorCode.MCP_DISABLED: MCPErrorSpec("AMA MCP 未启用", False),
    MCPErrorCode.UNAUTHORIZED: MCPErrorSpec("API Key 无效", False),
    MCPErrorCode.INVALID_CONTENT_LENGTH: MCPErrorSpec("Content-Length 无效", False),
    MCPErrorCode.REQUEST_TOO_LARGE: MCPErrorSpec("MCP 或上传请求体超过允许上限", False),
    MCPErrorCode.TOO_MANY_REQUESTS: MCPErrorSpec("MCP 或上传请求过多，请稍后重试", True),
    MCPErrorCode.INVALID_FILE_COUNT: MCPErrorSpec("每次只能上传一个 PDF 文件", False),
    MCPErrorCode.INVALID_FILENAME: MCPErrorSpec("PDF 文件名无效", False),
    MCPErrorCode.INVALID_FILE_TYPE: MCPErrorSpec("只允许提交 PDF 文件", False),
    MCPErrorCode.INVALID_IDEMPOTENCY_KEY: MCPErrorSpec("幂等键格式无效", False),
    MCPErrorCode.INVALID_ANALYSIS_ENGINE: MCPErrorSpec(
        "解析方法无效，只允许 llm、rule 或 hybrid",
        False,
    ),
    MCPErrorCode.INVALID_UPLOAD_ID: MCPErrorSpec("PDF 上传 ID 格式无效", False),
    MCPErrorCode.UPLOAD_NOT_FOUND: MCPErrorSpec("PDF 上传记录不存在", False),
    MCPErrorCode.UPLOAD_EXPIRED: MCPErrorSpec("PDF 上传记录已过期，请重新上传", False),
    MCPErrorCode.UPLOAD_ALREADY_USED: MCPErrorSpec("PDF 上传记录已用于其他任务，请重新上传", False),
    MCPErrorCode.PDF_TOO_LARGE: MCPErrorSpec("PDF 文件超过允许上限", False),
    MCPErrorCode.INVALID_PDF: MCPErrorSpec("PDF 已损坏、受密码保护或无法读取", False),
    MCPErrorCode.PDF_TOO_MANY_PAGES: MCPErrorSpec("PDF 页数超过允许上限", False),
    MCPErrorCode.IDEMPOTENCY_CONFLICT: MCPErrorSpec(
        "同一幂等键已用于其他 PDF 或解析方法",
        False,
    ),
    MCPErrorCode.QUEUE_FULL: MCPErrorSpec("MCP 解析队列已满，请稍后重试", True),
    MCPErrorCode.JOB_CREATE_FAILED: MCPErrorSpec("任务创建失败，请稍后重试", True),
    MCPErrorCode.JOB_NOT_FOUND: MCPErrorSpec("任务不存在", False),
    MCPErrorCode.JOB_NOT_READY: MCPErrorSpec("任务尚未完成", True),
    MCPErrorCode.RESULT_INVALID: MCPErrorSpec("任务结果缺失或校验失败", False),
    MCPErrorCode.RESULT_EXPIRED: MCPErrorSpec("任务结果已超过保留期限", False),
    MCPErrorCode.LOW_DISK_SPACE: MCPErrorSpec("服务器存储空间不足，已暂停接收或执行新任务", True),
    MCPErrorCode.PARSE_FAILED: MCPErrorSpec("PDF 解析失败", True),
    MCPErrorCode.EVIDENCE_BUILD_FAILED: MCPErrorSpec("证据池构建失败", True),
    MCPErrorCode.APPRAISAL_INCOMPLETE: MCPErrorSpec("AGREE-II 条目评价不完整", True),
    MCPErrorCode.REPORT_GENERATION_FAILED: MCPErrorSpec("最终报告生成失败", True),
    MCPErrorCode.UPSTREAM_TIMEOUT: MCPErrorSpec("上游模型或解析服务超时", True),
    MCPErrorCode.UPSTREAM_UNAVAILABLE: MCPErrorSpec("上游模型或解析服务暂不可用", True),
    MCPErrorCode.STORAGE_ERROR: MCPErrorSpec("服务器存储暂不可用", True),
    MCPErrorCode.WORKER_INTERRUPTED: MCPErrorSpec("任务执行被中断，请稍后重试", True),
    MCPErrorCode.INTERNAL_ERROR: MCPErrorSpec("AMA MCP 内部错误", True),
}


def error_payload(code: MCPErrorCode) -> dict[str, object]:
    """构造不包含内部实现细节的外部错误结构。"""
    spec = ERROR_SPECS[code]
    return {
        "code": code.value,
        "message": spec.message,
        "retryable": spec.retryable,
    }


class MCPJobError(RuntimeError):
    """携带稳定外部错误码和内部诊断信息的作业异常。"""

    def __init__(
        self,
        code: MCPErrorCode,
        *,
        internal_detail: str = "",
    ):
        self.error_code = code
        self.code = code.value
        self.message = ERROR_SPECS[code].message
        self.retryable = ERROR_SPECS[code].retryable
        self.internal_detail = internal_detail
        super().__init__(f"{self.code}: {self.message}")

    def to_payload(self) -> dict[str, object]:
        """返回可安全发送给外部 Agent 的错误结构。"""
        return error_payload(self.error_code)


_FAILURE_ERROR_CODES: dict[FailureCode, MCPErrorCode] = {
    FailureCode.INPUT_NOT_FOUND: MCPErrorCode.STORAGE_ERROR,
    FailureCode.PARSER_NOT_CONFIGURED: MCPErrorCode.UPSTREAM_UNAVAILABLE,
    FailureCode.PARSER_UNAVAILABLE: MCPErrorCode.UPSTREAM_UNAVAILABLE,
    FailureCode.PARSE_TIMEOUT: MCPErrorCode.UPSTREAM_TIMEOUT,
    FailureCode.PARSE_FAILED: MCPErrorCode.PARSE_FAILED,
    FailureCode.EVIDENCE_FAILED: MCPErrorCode.EVIDENCE_BUILD_FAILED,
    FailureCode.LLM_TIMEOUT: MCPErrorCode.UPSTREAM_TIMEOUT,
    FailureCode.LLM_UNAVAILABLE: MCPErrorCode.UPSTREAM_UNAVAILABLE,
    FailureCode.APPRAISAL_INCOMPLETE: MCPErrorCode.APPRAISAL_INCOMPLETE,
    FailureCode.APPRAISAL_FAILED: MCPErrorCode.APPRAISAL_INCOMPLETE,
    FailureCode.REPORT_TIMEOUT: MCPErrorCode.UPSTREAM_TIMEOUT,
    FailureCode.REPORT_FAILED: MCPErrorCode.REPORT_GENERATION_FAILED,
}


def error_code_for_failure(code: FailureCode | str | None, reason: str | None = None) -> MCPErrorCode:
    """把流水线失败代码映射为稳定的外部错误码；Worker 中断优先。"""
    if reason in {"worker_shutdown", "lease_lost", "heartbeat_failed"}:
        return MCPErrorCode.WORKER_INTERRUPTED
    try:
        return _FAILURE_ERROR_CODES.get(FailureCode(code), MCPErrorCode.INTERNAL_ERROR)
    except ValueError:
        return MCPErrorCode.INTERNAL_ERROR
