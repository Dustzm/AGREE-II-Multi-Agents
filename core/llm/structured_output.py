"""结构化输出（JSON Schema）能力管理与兼容降级。

不同 OpenAI 兼容部署对 `response_format` 的支持差异很大：

* 新版 vLLM / SGLang / OpenAI：`{"type": "json_schema", ...}` 直接生效；
* 旧版 vLLM：只认 `extra_body={"guided_json": <schema>}`；
* 更老的部署：两者都忽略，只能用 `{"type": "json_object"}` + 本地校验。

这里不做启动探测（会额外花钱、且在单测里会打到假客户端），而是
"先按最佳模式请求，遇到能力类 4xx 再降级并记住结果"，同一进程内
(base_url, model) 只降级一次。也可用环境变量 `LLM_STRUCTURED_OUTPUT`
强制指定模式（auto/json_schema/guided_json/json_object）。
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

JSON_OBJECT = {"type": "json_object"}
JSON_SCHEMA = "json_schema"
GUIDED_JSON = "guided_json"
MODES = (JSON_SCHEMA, GUIDED_JSON, "json_object")
ENV_KEY = "LLM_STRUCTURED_OUTPUT"

_lock = threading.Lock()
_modes: dict[tuple[str, str], str] = {}

_KIND_MARKERS = (
    "response_format",
    "json_schema",
    "guided_json",
    "guided decoding",
    "structured output",
    "structured_output",
)
_UNSUPPORTED_MARKERS = (
    "not supported",
    "unsupported",
    "not implemented",
    "unrecognized",
    "unknown parameter",
    "unknown field",
    "not allowed",
    "does not support",
    "invalid parameter",
)
# 这些措辞说明是 schema 本身写错（我方 bug），而不是部署不支持该能力，
# 不能据此降级，否则会把真实错误吞成 json_object 静默重试。
_SCHEMA_DEFECT_MARKERS = (
    "invalid schema",
    "schema validation",
    "jsonschema",
    "not a valid json schema",
    "schema is invalid",
    "validation failed for schema",
)


def _configured_raw() -> str:
    explicit = os.getenv(ENV_KEY)
    if explicit:
        return explicit
    try:
        from core.config import get_settings

        value = getattr(get_settings().llm, "structured_output", None)
    except Exception:  # pragma: no cover - settings are optional for unit tests
        value = None
    return str(value or "auto")


def configured_mode() -> str | None:
    """配置强制模式（环境变量优先，其次 .env）；auto/空返回 None。"""
    raw = _configured_raw().strip().lower()
    if raw in {"", "auto", "default"}:
        return None
    if raw in {"off", "none", "disabled"}:
        return "json_object"
    if raw in MODES:
        return raw
    logger.warning("未知的 %s=%s，按 auto 处理", ENV_KEY, raw)
    return None


def mode_for(base_url: str, model: str) -> str | None:
    """返回已探测到的模式（未探测返回 None）。"""
    with _lock:
        return _modes.get((base_url or "", model or ""))


def remember_mode(base_url: str, model: str, mode: str) -> None:
    with _lock:
        _modes[(base_url or "", model or "")] = mode


def resolve_mode(base_url: str, model: str) -> str:
    """决定本次请求优先使用的模式。"""
    forced = configured_mode()
    if forced:
        return forced
    return mode_for(base_url, model) or JSON_SCHEMA


def next_mode(mode: str) -> str | None:
    """能力降级阶梯：json_schema → guided_json → json_object。"""
    if mode == JSON_SCHEMA:
        return GUIDED_JSON
    if mode == GUIDED_JSON:
        return "json_object"
    return None


def is_unsupported_structured_error(error: BaseException) -> bool:
    """判断异常是否属于"该部署不支持这种结构化输出形态"。"""
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    text = f"{type(error).__name__} {error}".lower()
    if not any(marker in text for marker in _KIND_MARKERS):
        return False
    if any(marker in text for marker in _SCHEMA_DEFECT_MARKERS):
        return False
    if status is None:
        return any(marker in text for marker in _UNSUPPORTED_MARKERS)
    code = int(status)
    if code in {404, 415, 501}:
        # 路径/媒体类型/未实现类错误，只要提到结构化输出相关字段即可降级
        return True
    if code in {400, 422}:
        return any(marker in text for marker in _UNSUPPORTED_MARKERS)
    return False


def build_structured_format(
    mode: str,
    json_schema: dict[str, Any] | None,
    *,
    schema_name: str = "rule_engine_response",
    strict: bool = True,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """按模式构造 (response_format, extra request kwargs)。"""
    if json_schema is None or mode == "json_object":
        return JSON_OBJECT, None
    if mode == GUIDED_JSON:
        return JSON_OBJECT, {"extra_body": {"guided_json": json_schema}}
    return (
        {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": strict,
                "schema": json_schema,
            },
        },
        None,
    )


def reset_cache() -> None:
    """单测辅助：清空已探测模式。"""
    with _lock:
        _modes.clear()
