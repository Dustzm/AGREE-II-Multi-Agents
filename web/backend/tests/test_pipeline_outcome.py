"""流水线失败代码只由异常类型和阶段决定，不依赖错误文字。"""
import pytest

from core.llm.context import ContextWindowNotConfigured
from core.pipeline import FailureCode, Phase, PipelineFailure, classify_failure, incomplete_appraisal_code


class APIConnectionError(Exception):
    """模拟 SDK 以类名区分的连接错误。"""


@pytest.mark.parametrize("error,phase,expected", [
    (TimeoutError("超时"), Phase.PARSING, FailureCode.PARSE_TIMEOUT),
    (TimeoutError("timed out"), Phase.REPORTER, FailureCode.REPORT_TIMEOUT),
    (TimeoutError("timed out"), Phase.APPRAISER, FailureCode.LLM_TIMEOUT),
    (APIConnectionError("报告"), Phase.APPRAISER, FailureCode.LLM_UNAVAILABLE),
    (ConnectionError("reset"), Phase.PARSING, FailureCode.PARSER_UNAVAILABLE),
    (ValueError("timeout in text only"), Phase.PREPROCESSING, FailureCode.EVIDENCE_FAILED),
    (RuntimeError("证据池"), Phase.ORCHESTRATOR, FailureCode.INTERNAL_ERROR),
    (ContextWindowNotConfigured("missing"), Phase.APPRAISER, FailureCode.CONFIG_INVALID),
])
def test_classify_failure_uses_types_and_phase(error, phase, expected):
    assert classify_failure(error, phase) is expected


def test_explicit_code_wins_over_wrapped_cause():
    try:
        try:
            raise TimeoutError("model timed out")
        except TimeoutError as cause:
            raise PipelineFailure(FailureCode.PARSE_FAILED, "parse failed") from cause
    except PipelineFailure as error:
        assert classify_failure(error, Phase.REPORTER) is FailureCode.PARSE_FAILED


def test_classify_failure_reads_exception_group_members():
    group = ExceptionGroup("items", [ValueError("bad json"), TimeoutError("slow")])
    assert classify_failure(group, Phase.APPRAISER) is FailureCode.LLM_TIMEOUT


def test_incomplete_appraisal_reports_upstream_cause():
    assert incomplete_appraisal_code([]) is FailureCode.APPRAISAL_INCOMPLETE
    assert incomplete_appraisal_code([ValueError("invalid score")]) is FailureCode.APPRAISAL_INCOMPLETE
    assert incomplete_appraisal_code([ValueError("x"), TimeoutError("y")]) is FailureCode.LLM_TIMEOUT
