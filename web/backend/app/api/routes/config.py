"""
配置管理路由

提供 LLM 配置的查询、更新、连接测试和健康验证
"""
import logging
import time
import traceback
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from core.config import get_settings, refresh_settings
from core.config.runtime_store import (
    clear_runtime_llm_base_url,
    clear_runtime_llm_config,
    clear_runtime_llm_context_window,
    get_runtime_llm_config,
    upsert_runtime_llm_config,
)
from core.config.settings import env_llm_context_window
from ...i18n import get_language, t
from ...permissions import require_admin

from ...i18n.messages import LocalizedHTTPException, sanitize_diagnostic, ui_message

logger = logging.getLogger(__name__)

router = APIRouter()


# ========== 请求/响应模型 ==========

class LLMConfigResponse(BaseModel):
    """LLM 配置响应（API Key 脱敏）"""
    provider: str
    base_url: str
    api_key_masked: str  # 脱敏后的 API Key
    model_name: str
    timeout: int
    is_vision_model: bool
    context_window: int | None = None
    # 管理台覆盖层的来源信息：为空表示当前值直接来自 .env
    runtime_updated_at: str | None = None
    runtime_updated_by: str | None = None
    runtime_source_fields: list[str] = Field(default_factory=list)


class LLMConfigUpdate(BaseModel):
    """LLM 配置更新请求"""
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    timeout: int | None = Field(default=None, gt=0, le=3600, strict=True)
    is_vision_model: bool | None = None
    context_window: int | None = Field(default=None, gt=0, le=2**53 - 1, strict=True)


class ConnectionTestResult(BaseModel):
    """连接测试结果"""
    success: bool
    message: str
    i18n: dict | None = None
    diagnostic: str | None = None
    latency_ms: int | None = None
    models: list[str] | None = None


class HealthCheckResult(BaseModel):
    """LLM 健康验证结果"""
    success: bool
    message: str
    i18n: dict | None = None
    diagnostic: str | None = None
    latency_ms: int | None = None
    model_name: str | None = None
    reply: str | None = None  # LLM 的回复内容


@dataclass(frozen=True)
class EffectiveLLMConfig:
    """用于测试/健康检查的临时 LLM 配置视图。"""

    provider: str
    base_url: str
    api_key: str
    model_name: str
    timeout: int
    is_vision_model: bool


# ========== 工具函数 ==========

def _mask_api_key(key: str) -> str:
    """API Key 脱敏显示"""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def _iso_utc(value: str | None) -> str | None:
    """Normalise sqlite ``YYYY-MM-DD HH:MM:SS`` (UTC) timestamps for the UI."""
    if not value:
        return None
    text = str(value).strip()
    if "T" not in text:
        text = text.replace(" ", "T")
    if text.endswith("Z") or "+" in text[10:]:
        return text
    return text + "Z"


def _llm_config_response(settings) -> LLMConfigResponse:
    """构造 LLM 配置响应，并附带管理台覆盖层的来源信息。"""
    try:
        runtime = get_runtime_llm_config(settings.llm_config_secret)
    except Exception as e:  # 读取来源信息失败不应影响配置读取本身
        logger.warning(f"读取运行时 LLM 配置来源失败: {e}")
        runtime = None
    runtime = runtime or {}
    source_fields = [
        key
        for key in ("provider", "base_url", "model_name", "timeout", "is_vision_model", "context_window")
        if runtime.get(key) not in (None, "")
    ]
    return LLMConfigResponse(
        provider=settings.llm.provider,
        base_url=settings.llm.base_url,
        api_key_masked=_mask_api_key(settings.llm.api_key),
        model_name=settings.llm.model_name,
        timeout=settings.llm.timeout,
        is_vision_model=settings.llm.is_vision_model,
        context_window=settings.llm.context_window,
        runtime_updated_at=_iso_utc(runtime.get("updated_at")),
        runtime_updated_by=runtime.get("updated_by") or None,
        runtime_source_fields=source_fields,
    )


def _truncate_log_value(value, limit: int = 2000) -> str:
    """限制日志字段长度，避免上游响应过长压垮日志。"""
    text = sanitize_diagnostic(value, limit=None).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated]"


def _collect_exception_debug_info(exc: Exception) -> str:
    """尽可能提取 LiteLLM / OpenAI / HTTP 异常中的关键信息。"""
    parts: list[str] = [f"type={exc.__class__.__name__}"]

    for attr in ("status_code", "message", "llm_provider", "model"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            parts.append(f"{attr}={_truncate_log_value(value)}")

    body = getattr(exc, "body", None)
    if body not in (None, ""):
        parts.append(f"body={_truncate_log_value(body)}")

    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        if status_code is not None:
            parts.append(f"response.status_code={status_code}")
        url = getattr(response, "url", None)
        if url:
            parts.append(f"response.url={url}")
        text = getattr(response, "text", None)
        if text:
            parts.append(f"response.text={_truncate_log_value(text)}")

    request = getattr(exc, "request", None)
    if request is not None:
        method = getattr(request, "method", None)
        url = getattr(request, "url", None)
        if method or url:
            parts.append(f"request={method or ''} {url or ''}".strip())

    if exc.__cause__ is not None:
        parts.append(
            f"cause={exc.__cause__.__class__.__name__}: {_truncate_log_value(exc.__cause__)}"
        )
    elif exc.__context__ is not None:
        parts.append(
            f"context={exc.__context__.__class__.__name__}: {_truncate_log_value(exc.__context__)}"
        )

    return " | ".join(parts)


def _build_effective_llm_config(config: LLMConfigUpdate | None = None) -> EffectiveLLMConfig:
    """将当前 settings 与前端草稿合并为一份临时配置。"""
    settings = get_settings()
    provider = config.provider if config and config.provider is not None else settings.llm.provider

    from core.agents.base.model_config import get_provider_config_for

    provider_cfg = get_provider_config_for(provider)
    default_base_url = provider_cfg.get("default_base_url", "")

    if config and config.base_url is not None:
        base_url = config.base_url
    elif config and config.provider is not None and provider != settings.llm.provider:
        base_url = default_base_url
    else:
        base_url = settings.llm.base_url

    api_key = settings.llm.api_key
    if config and config.api_key is not None:
        api_key = config.api_key

    return EffectiveLLMConfig(
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        model_name=config.model_name if config and config.model_name is not None else settings.llm.model_name,
        timeout=config.timeout if config and config.timeout is not None else settings.llm.timeout,
        is_vision_model=config.is_vision_model if config and config.is_vision_model is not None else settings.llm.is_vision_model,
    )


# ========== 路由 ==========

@router.get("/config/llm", response_model=LLMConfigResponse)
async def get_llm_config():
    """获取当前 LLM 配置（API Key 脱敏显示）"""
    return _llm_config_response(get_settings())


@router.get("/config/llm/model-limits")
async def get_model_limits(provider: str, model_id: str):
    """Read bundled limits without contacting a model service."""
    from core.config.model_catalog import lookup_model_limits
    return lookup_model_limits(provider, model_id)


@router.put("/config/llm", response_model=LLMConfigResponse)
async def update_llm_config(config: LLMConfigUpdate, _user=Depends(require_admin)):
    """
    更新 LLM 配置
    
    热更新内存配置 + 持久化到 SQLite 运行时配置表
    """
    current = get_settings().llm
    runtime_updates: dict[str, object] = {}
    clear_context_window = False
    clear_base_url = False

    provider_changed = config.provider is not None and config.provider != current.provider
    if config.provider is not None:
        runtime_updates["provider"] = config.provider

    explicit_base_url = config.base_url.strip() if config.base_url is not None else None
    if explicit_base_url:
        runtime_updates["base_url"] = explicit_base_url
    elif provider_changed:
        # 切换提供商时，自动回填该提供商的 default_base_url，
        # 防止旧提供商的 base_url 残留在运行时配置中导致请求打错地址。
        from core.agents.base.model_config import get_provider_config_for
        default_url = get_provider_config_for(config.provider).get("default_base_url", "")
        if default_url:
            runtime_updates["base_url"] = default_url
        else:
            clear_base_url = True
    elif config.base_url is not None:
        # 显式清空 base_url：删除运行时覆盖，回落到 .env
        clear_base_url = True

    if config.api_key is not None:
        runtime_updates["api_key"] = config.api_key
    elif provider_changed:
        # 切换厂商但没重新填写 key 时清除旧厂商密钥，避免旧凭据被发往新端点
        runtime_updates["api_key"] = None

    if config.model_name is not None:
        runtime_updates["model_name"] = config.model_name
    if config.timeout is not None:
        runtime_updates["timeout"] = config.timeout
    if config.is_vision_model is not None:
        runtime_updates["is_vision_model"] = config.is_vision_model

    identity_changed = any(key in runtime_updates and runtime_updates[key] != getattr(current, key)
                           for key in ("provider", "model_name", "base_url"))
    if "context_window" in config.model_fields_set:
        runtime_updates["context_window"] = config.context_window
    elif identity_changed:
        if env_llm_context_window() is None:
            # 没有 .env 基线时写显式 NULL，确保内存中的旧窗口也立即失效
            runtime_updates["context_window"] = None
        else:
            # 有 .env 基线时删除覆盖，让新模型回落到 .env 的窗口
            clear_context_window = True

    if not runtime_updates and not clear_context_window and not clear_base_url:
        raise LocalizedHTTPException(400, "error.config_empty")
    
    try:
        current_settings = get_settings()
        if (
            "api_key" in runtime_updates
            and runtime_updates["api_key"]
            and not current_settings.llm_config_secret
        ):
            raise LocalizedHTTPException(500, "error.config_secret_missing")

        # 1. 持久化到 SQLite 配置表
        if runtime_updates:
            upsert_runtime_llm_config(
                runtime_updates,
                secret=current_settings.llm_config_secret,
                updated_by=getattr(_user, "username", None),
            )
        if clear_context_window:
            clear_runtime_llm_context_window()
        if clear_base_url:
            clear_runtime_llm_base_url()
        
        # 2. 刷新内存配置
        new_settings = refresh_settings()
        
        # 3. 刷新模型实例和环境变量
        try:
            from core.agents.base.model_config import refresh_model
            refresh_model()
        except Exception as e:
            logger.warning(f"刷新模型实例时出错（不影响配置保存）: {e}")
        
        logger.info(f"LLM 配置已更新: {list(runtime_updates.keys())}")
        
        return _llm_config_response(new_settings)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新 LLM 配置失败: {e}")
        raise LocalizedHTTPException(500, "error.config_save_failed", diagnostic=e)


@router.delete("/config/llm", response_model=LLMConfigResponse)
async def reset_llm_config(_user=Depends(require_admin)):
    """清除管理台保存的运行时配置，恢复为 .env 中的配置。"""
    try:
        clear_runtime_llm_config()
        new_settings = refresh_settings()
        try:
            from core.agents.base.model_config import refresh_model
            refresh_model()
        except Exception as e:
            logger.warning(f"刷新模型实例时出错（不影响配置重置）: {e}")

        logger.info(
            "LLM 运行时配置已重置，恢复 .env 配置（操作人=%s）",
            getattr(_user, "username", None),
        )
        return _llm_config_response(new_settings)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"重置 LLM 配置失败: {e}")
        raise LocalizedHTTPException(500, "error.config_save_failed", diagnostic=e)


@router.post("/config/llm/test", response_model=ConnectionTestResult)
async def test_llm_connection(
    config: LLMConfigUpdate | None = None,
    accept_language: str | None = Header(None, alias="Accept-Language"),
    _user=Depends(require_admin),
):
    """
    测试 LLM 连接
    
    对于需要 base_url 的提供商，向 /models 尝试探活；
    对于原生公有云提供商，直接返回可用。
    """
    effective = _build_effective_llm_config(config)
    lang = get_language(accept_language)

    if effective.provider not in ["vllm", "openai_compatible"]:
        return ConnectionTestResult(
            success=True,
            **ui_message("config.connection.official_provider", lang, provider=effective.provider),
            latency_ms=0,
            models=[effective.model_name]
        )

    if not effective.base_url:
        return ConnectionTestResult(
            success=False,
            **ui_message("config.connection.base_url_missing", lang),
        )

    url = f"{effective.base_url.rstrip('/')}/models"
    headers = {}
    if effective.api_key:
        headers["Authorization"] = f"Bearer {effective.api_key}"
    
    start_time = time.time()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url, headers=headers)
        
        latency_ms = int((time.time() - start_time) * 1000)
        
        if response.status_code == 200:
            models = []
            try:
                data = response.json()
                if "data" in data:
                    models = [m.get("id", "") for m in data["data"]]
            except Exception:
                pass
            
            return ConnectionTestResult(
                success=True,
                **ui_message("config.connection.success", lang),
                latency_ms=latency_ms,
                models=models,
            )
        elif response.status_code == 401:
            return ConnectionTestResult(
                success=False,
                **ui_message("config.connection.auth_failed", lang),
                latency_ms=latency_ms,
            )
        elif response.status_code == 403:
            return ConnectionTestResult(
                success=False,
                **ui_message("config.connection.forbidden", lang),
                latency_ms=latency_ms,
            )
        elif response.status_code == 404:
            # 某些兼容网关可能不提供 /models 接口
            return ConnectionTestResult(
                success=True,
                **ui_message("config.connection.models_unsupported", lang),
                latency_ms=latency_ms,
            )
        else:
            return ConnectionTestResult(
                success=False,
                **ui_message("config.connection.http_error", lang, status=response.status_code),
                latency_ms=latency_ms,
            )
    except httpx.ConnectError as e:
        return ConnectionTestResult(
            success=False,
            **ui_message("config.connection.connect_failed", lang, url=url, diagnostic=e),
        )
    except httpx.TimeoutException as e:
        return ConnectionTestResult(
            success=False,
            **ui_message("config.connection.timeout", lang, diagnostic=e),
        )
    except Exception as e:
        return ConnectionTestResult(
            success=False,
            **ui_message("config.connection.error", lang, diagnostic=e),
        )


@router.post("/config/llm/health", response_model=HealthCheckResult)
async def check_llm_health(
    config: LLMConfigUpdate | None = None,
    accept_language: str | None = Header(None, alias="Accept-Language"),
    _user=Depends(require_admin),
):
    """
    LLM 健康验证
    
    统一使用 LiteLLM 路由以兼容各类提供商
    """
    import litellm
    from core.agents.base.model_config import (
        PROVIDER_CONFIG,
        get_compatible_reasoning_kwargs,
        get_compatible_sampling_kwargs,
        get_provider_kwargs_for,
    )

    effective = _build_effective_llm_config(config)
    lang = get_language(accept_language)
    provider_cfg = PROVIDER_CONFIG.get(effective.provider, PROVIDER_CONFIG["vllm"])

    if provider_cfg["need_base_url"] and not effective.base_url:
        return HealthCheckResult(
            success=False,
            **ui_message("config.health.base_url_missing", lang, provider=effective.provider),
        )

    model_string = f"{provider_cfg['prefix']}/{effective.model_name}"

    kwargs = get_provider_kwargs_for(effective.provider)
    if effective.api_key:
        kwargs["api_key"] = effective.api_key
    effective_url = ""
    if provider_cfg.get("need_base_url"):
        effective_url = effective.base_url or provider_cfg.get("default_base_url", "")
        if effective_url:
            kwargs["base_url"] = effective_url

    logger.info(
        "开始 LLM 健康验证: provider=%s model=%s api_base=%s timeout=%s",
        effective.provider,
        model_string,
        effective_url or "<official-default>",
        min(effective.timeout, 30),
    )

    start_time = time.time()
    try:
        response = await litellm.acompletion(
            model=model_string,
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=60,
            timeout=min(effective.timeout, 30),
            **get_compatible_reasoning_kwargs(model_string),
            **get_compatible_sampling_kwargs(model_string, temperature=0.1),
            **kwargs,
        )
        
        latency_ms = int((time.time() - start_time) * 1000)
        
        model_name = getattr(response, "model", effective.model_name)
        choices = getattr(response, "choices", [])
        
        reply = ""
        if choices:
            message_obj = getattr(choices[0], "message", None)
            if message_obj:
                content = getattr(message_obj, "content", "")
                if content:
                    reply = content.strip()
                else:
                    reasoning = getattr(message_obj, "reasoning_content", "")
                    if reasoning:
                        reply = t("config.health.reasoning", lang, reasoning=reasoning[:200].strip())
                    else:
                        reply = t("config.health.empty_reply", lang)
        
        return HealthCheckResult(
            success=True,
            **ui_message("config.health.success", lang),
            latency_ms=latency_ms,
            model_name=model_name,
            reply=reply,
        )
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "LLM 健康验证失败: provider=%s model=%s api_base=%s latency_ms=%s details=%s\n%s",
            effective.provider,
            model_string,
            effective_url or "<official-default>",
            latency_ms,
            _collect_exception_debug_info(e),
            sanitize_diagnostic("".join(traceback.format_exception(e)), limit=None),
        )
        return HealthCheckResult(
            success=False,
            **ui_message("config.health.error", lang, diagnostic=e),
            latency_ms=latency_ms,
        )


@router.get("/config/providers")
async def get_providers():
    """
    获取所有提供商及其推荐模型列表
    
    直接从 providers.json 读取并返回
    """
    from core.agents.base.model_config import load_providers_config
    return load_providers_config()
