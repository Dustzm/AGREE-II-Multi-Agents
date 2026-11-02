"""历史任务文本的读取适配；新消息不依赖文案判断状态。"""
import re

from . import t
from .messages import descriptor, sanitize_diagnostic

LEGACY_STEPS = {
    "正在初始化...": "initializing", "初始化中": "initializing",
    "解析 PDF 中": "parsingPdf", "构建证据池中": "buildingEvidence",
    "条目评价中": "appraisingItems", "生成报告中": "generatingReport",
    "重试中": "retryingStep", "失败": "failedGeneric", "等待继续": "waitingContinue",
    "服务停止，等待继续": "serviceStopWaiting", "Worker 中断，等待继续": "workerInterrupted",
    "已停止": "stopped", "已完成": "completed",
}


def legacy_step(value: str | None) -> dict | None:
    """仅解析既有持久化格式，无法识别时交给界面状态兜底。"""
    if not value:
        return None
    if value in LEGACY_STEPS:
        return descriptor("batch." + LEGACY_STEPS[value])
    progress = re.fullmatch(r"(?:评价进度|Progress):\s*(\d+)/(\d+)", value)
    if progress:
        return descriptor("batch.itemProgress", completed=int(progress[1]), total=int(progress[2]))
    if not value.startswith("i18n:batch."):
        return None
    parts = value.split(":")
    key = parts[1]
    if key == "batch.failed":
        return descriptor("batch.failedGeneric")
    if key in {"batch.retrying", "batch.itemProgress"} and len(parts) == 4:
        names = ("current", "max") if key == "batch.retrying" else ("completed", "total")
        return descriptor(key, **dict(zip(names, parts[2:])))
    return descriptor(key)


def legacy_error(value: str | None) -> dict:
    """历史未知异常保留诊断，主提示可随语言切换。"""
    if not value:
        return {}
    timeout = re.search(r"(MinerU|Reporter).*?(?:超时|timed out).*?\((\d+)s\)", value, re.I)
    key = "error.appraisal_failed"
    params = {}
    if timeout:
        key = "error.mineru_timeout" if timeout[1].lower() == "mineru" else "error.reporter_timeout"
        params["seconds"] = int(timeout[2])
    return {"error": descriptor(key, **params), "diagnostic": sanitize_diagnostic(value)}


def adapt_batch_payload(payload: dict) -> dict:
    """为快照和历史事件补消息描述，保持数据库中的原始记录不变。"""
    result = dict(payload)
    if isinstance(result.get("task"), dict):
        result["task"] = adapt_batch_payload(result["task"])
    if isinstance(result.get("tasks"), list):
        result["tasks"] = [adapt_batch_payload(task) for task in result["tasks"]]
    step = result.get("step_i18n") or legacy_step(result.get("current_step"))
    if step:
        result["step_i18n"] = step
        result["current_step"] = t(step["key"], **step.get("params", {}))
    error_field = "last_error" if "last_error" in result else "error"
    raw_error = result.get(error_field)
    if isinstance(raw_error, str) and raw_error:
        failure = legacy_error(raw_error)
        error = result.get("error_i18n") or failure["error"]
        result["error_i18n"] = error
        result[error_field] = t(error["key"], **error.get("params", {}))
        result["diagnostic"] = sanitize_diagnostic(result.get("diagnostic") or failure["diagnostic"])
    elif result.get("diagnostic"):
        result["diagnostic"] = sanitize_diagnostic(result["diagnostic"])
    return result
