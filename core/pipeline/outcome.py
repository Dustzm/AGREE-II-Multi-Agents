"""评价流水线的失败语义：稳定的阶段与失败代码，由各入口映射为自己的协议。"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from enum import StrEnum
from typing import Any

from core.llm.context import ContextWindowNotConfigured
from core.utils.llm_errors import is_timeout_error, is_unavailable_error


class Phase(StrEnum):
    """流水线阶段；取值与前端消息的 phase 字段一致。"""

    ORCHESTRATOR = "orchestrator"
    PARSING = "parsing"
    PREPROCESSING = "preprocessing"
    APPRAISER = "appraiser"
    REPORTER = "reporter"


class FailureCode(StrEnum):
    """与语言无关的失败代码；界面、批量和 MCP 只按代码映射，不解析文字。"""

    CONFIG_INVALID = "config_invalid"
    INPUT_NOT_FOUND = "input_not_found"
    TASK_INIT_FAILED = "task_init_failed"
    PARSER_NOT_CONFIGURED = "parser_not_configured"
    PARSER_UNAVAILABLE = "parser_unavailable"
    PARSE_TIMEOUT = "parse_timeout"
    PARSE_FAILED = "parse_failed"
    EVIDENCE_FAILED = "evidence_failed"
    LLM_TIMEOUT = "llm_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    APPRAISAL_INCOMPLETE = "appraisal_incomplete"
    APPRAISAL_FAILED = "appraisal_failed"
    REPORT_TIMEOUT = "report_timeout"
    REPORT_FAILED = "report_failed"
    INTERNAL_ERROR = "internal_error"


class PipelineFailure(RuntimeError):
    """携带失败代码的流水线异常；消息只用于日志诊断，details 只放业务参数。"""

    def __init__(self, code: FailureCode, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


_PHASE_FAILURES = {
    Phase.PARSING: FailureCode.PARSE_FAILED,
    Phase.PREPROCESSING: FailureCode.EVIDENCE_FAILED,
    Phase.APPRAISER: FailureCode.APPRAISAL_FAILED,
    Phase.REPORTER: FailureCode.REPORT_FAILED,
}
_PHASE_TIMEOUTS = {
    Phase.PARSING: FailureCode.PARSE_TIMEOUT,
    Phase.REPORTER: FailureCode.REPORT_TIMEOUT,
}


def iter_exception_chain(error: BaseException) -> Iterator[BaseException]:
    """依次给出异常本身、因果链和异常组成员，每个异常只出现一次。"""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.append(current.__cause__ or current.__context__)


def _upstream_code(errors: Iterable[BaseException], phase: Phase) -> FailureCode | None:
    chain = [candidate for error in errors for candidate in iter_exception_chain(error)]
    if any(is_timeout_error(candidate) for candidate in chain):
        return _PHASE_TIMEOUTS.get(phase, FailureCode.LLM_TIMEOUT)
    if any(is_unavailable_error(candidate) for candidate in chain):
        return FailureCode.PARSER_UNAVAILABLE if phase is Phase.PARSING else FailureCode.LLM_UNAVAILABLE
    return None


def classify_failure(error: BaseException, phase: Phase = Phase.ORCHESTRATOR) -> FailureCode:
    """显式代码优先，其次按异常类型判断上游故障，最后归入当前阶段的失败。"""
    for candidate in iter_exception_chain(error):
        if isinstance(candidate, PipelineFailure):
            return candidate.code
        if isinstance(candidate, ContextWindowNotConfigured):
            return FailureCode.CONFIG_INVALID
    return _upstream_code([error], phase) or _PHASE_FAILURES.get(phase, FailureCode.INTERNAL_ERROR)


def incomplete_appraisal_code(item_errors: Iterable[BaseException]) -> FailureCode:
    """条目缺失时，若由上游故障导致则报告上游故障，否则为评价不完整。"""
    return _upstream_code(item_errors, Phase.APPRAISER) or FailureCode.APPRAISAL_INCOMPLETE
