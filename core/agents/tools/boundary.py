"""ADK 工具边界：核心函数失败时抛异常，交给模型的工具返回结构化错误，便于模型修正参数。"""
import functools
import inspect
import logging

from core.utils.cancellation import CancellationRequested

logger = logging.getLogger(__name__)


def _error_result(function, error: Exception) -> dict:
    logger.exception("工具调用失败: %s", function.__name__)
    return {"status": "error", "error": f"{type(error).__name__}: {error}"}


def as_tool(function):
    """包装为 ADK 工具；签名和文档保持不变，取消请求继续向上传递。"""
    if inspect.iscoroutinefunction(function):
        @functools.wraps(function)
        async def async_tool(*args, **kwargs):
            try:
                return await function(*args, **kwargs)
            except CancellationRequested:
                raise
            except Exception as error:
                return _error_result(function, error)
        return async_tool

    @functools.wraps(function)
    def tool(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CancellationRequested:
            raise
        except Exception as error:
            return _error_result(function, error)
    return tool
