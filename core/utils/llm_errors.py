"""共享的模型输出与可重试错误判定。"""

import json

_TIMEOUT_NAMES = {"timeout", "apitimeouterror", "readtimeout", "connecttimeout"}
_UNAVAILABLE_NAMES = {
    "apiconnectionerror", "internalservererror", "serviceunavailableerror", "ratelimiterror",
}


class InvalidModelJSON(ValueError):
    """模型输出不能用于当前调用。"""


def is_timeout_error(error: BaseException) -> bool:
    """上游请求超时，包括各 SDK 自定义的超时类型。"""
    return isinstance(error, TimeoutError) or type(error).__name__.lower() in _TIMEOUT_NAMES


def is_unavailable_error(error: BaseException) -> bool:
    """上游连接失败、限流或服务端故障。"""
    if isinstance(error, ConnectionError):
        return True
    status = getattr(error, "status_code", None)
    if status == 429 or isinstance(status, int) and status >= 500:
        return True
    return type(error).__name__.lower() in _UNAVAILABLE_NAMES


def is_transport_error(error: BaseException) -> bool:
    """请求未得到可用响应：超时、连接失败、限流或服务端故障。"""
    return is_timeout_error(error) or is_unavailable_error(error)


def is_retryable_llm_error(error: BaseException) -> bool:
    """仅重试传输故障、限流、服务端故障与无效模型 JSON。"""
    return is_transport_error(error) or isinstance(error, (json.JSONDecodeError, InvalidModelJSON))


def tag_stage(error: BaseException, stage: str) -> None:
    """记录最先失败的调用阶段；外层不覆盖更具体的内层阶段。"""
    if getattr(error, "appraisal_stage", None):
        return
    try:
        error.appraisal_stage = stage
    except (AttributeError, TypeError):
        pass
