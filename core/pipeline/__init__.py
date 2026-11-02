"""与入口无关的评价流水线：阶段、失败语义与执行编排。"""

from .outcome import (
    FailureCode,
    Phase,
    PipelineFailure,
    classify_failure,
    incomplete_appraisal_code,
    iter_exception_chain,
)

__all__ = [
    "FailureCode",
    "Phase",
    "PipelineFailure",
    "classify_failure",
    "incomplete_appraisal_code",
    "iter_exception_chain",
]
