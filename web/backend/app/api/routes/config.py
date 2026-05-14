"""
配置管理路由

提供 LLM 配置的查询、更新、连接测试和健康验证
"""
import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config import get_settings, refresh_settings
from core.config.runtime_store import upsert_runtime_llm_config
from ...permissions import require_admin

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


class LLMConfigUpdate(BaseModel):
    """LLM 配置更新请求"""
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    timeout: int | None = None
    is_vision_model: bool | None = None


class ConnectionTestResult(BaseModel):
    """连接测试结果"""
    success: bool
    message: str
    latency_ms: int | None = None
    models: list[str] | None = None


class HealthCheckResult(BaseModel):
    """LLM 健康验证结果"""
    success: bool
    message: str
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


def _truncate_log_value(value, limit: int = 2000) -> str:
    """限制日志字段长度，避免上游响应过长压垮日志。"""
    text = str(value).strip()
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


def _get_provider_kwargs_for(provider: str) -> dict:
    """返回指定 provider 的兼容扩展参数。"""
    if provider == "vllm":
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return {}


# ========== 路由 ==========

@router.get("/config/llm", response_model=LLMConfigResponse)
async def get_llm_config():
    """获取当前 LLM 配置（API Key 脱敏显示）"""
    settings = get_settings()
    return LLMConfigResponse(
        provider=settings.llm.provider,
        base_url=settings.llm.base_url,
        api_key_masked=_mask_api_key(settings.llm.api_key),
        model_name=settings.llm.model_name,
        timeout=settings.llm.timeout,
        is_vision_model=settings.llm.is_vision_model,
    )


@router.put("/config/llm", response_model=LLMConfigResponse)
async def update_llm_config(config: LLMConfigUpdate, _user=Depends(require_admin)):
    """
    更新 LLM 配置
    
    热更新内存配置 + 持久化到 SQLite 运行时配置表
    """
    runtime_updates: dict[str, object] = {}
    
    if config.provider is not None:
        runtime_updates["provider"] = config.provider
        # 切换提供商时，自动回填该提供商的 default_base_url
        # 防止旧提供商的 base_url 残留在运行时配置中导致请求打错地址
        from core.agents.base.model_config import get_provider_config_for
        new_provider_cfg = get_provider_config_for(config.provider)
        default_url = new_provider_cfg.get("default_base_url", "")
        if config.base_url is None:
            runtime_updates["base_url"] = default_url
        
    if config.base_url is not None:
        runtime_updates["base_url"] = config.base_url
    if config.api_key is not None:
        runtime_updates["api_key"] = config.api_key
    if config.model_name is not None:
        runtime_updates["model_name"] = config.model_name
    if config.timeout is not None:
        runtime_updates["timeout"] = config.timeout
    if config.is_vision_model is not None:
        runtime_updates["is_vision_model"] = config.is_vision_model
    
    if not runtime_updates:
        raise HTTPException(status_code=400, detail="没有提供需要更新的配置项")
    
    try:
        current_settings = get_settings()
        if (
            "api_key" in runtime_updates
            and runtime_updates["api_key"]
            and not current_settings.llm_config_secret
        ):
            raise HTTPException(status_code=500, detail="未配置 LLM_CONFIG_SECRET，无法安全保存 API Key")

        # 1. 持久化到 SQLite 配置表
        upsert_runtime_llm_config(
            runtime_updates,
            secret=current_settings.llm_config_secret,
            updated_by=getattr(_user, "username", None),
        )
        
        # 2. 刷新内存配置
        new_settings = refresh_settings()
        
        # 3. 刷新模型实例和环境变量
        try:
            from core.agents.base.model_config import refresh_model
            refresh_model()
        except Exception as e:
            logger.warning(f"刷新模型实例时出错（不影响配置保存）: {e}")
        
        logger.info(f"LLM 配置已更新: {list(runtime_updates.keys())}")
        
        return LLMConfigResponse(
            provider=new_settings.llm.provider,
            base_url=new_settings.llm.base_url,
            api_key_masked=_mask_api_key(new_settings.llm.api_key),
            model_name=new_settings.llm.model_name,
            timeout=new_settings.llm.timeout,
            is_vision_model=new_settings.llm.is_vision_model,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新 LLM 配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")


@router.post("/config/llm/test", response_model=ConnectionTestResult)
async def test_llm_connection(config: LLMConfigUpdate | None = None, _user=Depends(require_admin)):
    """
    测试 LLM 连接
    
    对于需要 base_url 的提供商，向 /models 尝试探活；
    对于原生公有云提供商，直接返回可用。
    """
    effective = _build_effective_llm_config(config)

    if effective.provider not in ["vllm", "openai_compatible"]:
        return ConnectionTestResult(
            success=True,
            message=f"已选择官方提供商 ({effective.provider})，请运行健康验证测试实际可用性",
            latency_ms=0,
            models=[effective.model_name]
        )

    if not effective.base_url:
        return ConnectionTestResult(
            success=False,
            message="未配置 LLM Base URL",
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
                message="连接成功",
                latency_ms=latency_ms,
                models=models,
            )
        elif response.status_code == 401:
            return ConnectionTestResult(
                success=False,
                message="认证失败 (401)，请检查 API Key",
                latency_ms=latency_ms,
            )
        elif response.status_code == 403:
            return ConnectionTestResult(
                success=False,
                message="访问被拒绝 (403)",
                latency_ms=latency_ms,
            )
        elif response.status_code == 404:
            # 某些兼容网关可能不提供 /models 接口
            return ConnectionTestResult(
                success=True,
                message="服务连通 (但提供商不支持获取模型列表)，请运行健康验证确认推理",
                latency_ms=latency_ms,
            )
        else:
            return ConnectionTestResult(
                success=False,
                message=f"服务返回异常状态码: {response.status_code}",
                latency_ms=latency_ms,
            )
    except httpx.ConnectError:
        return ConnectionTestResult(
            success=False,
            message=f"无法连接到 {url}，请检查 URL 是否正确",
        )
    except httpx.TimeoutException:
        return ConnectionTestResult(
            success=False,
            message="连接超时，服务可能不可用",
        )
    except Exception as e:
        return ConnectionTestResult(
            success=False,
            message=f"连接测试异常: {str(e)}",
        )


@router.post("/config/llm/health", response_model=HealthCheckResult)
async def check_llm_health(config: LLMConfigUpdate | None = None, _user=Depends(require_admin)):
    """
    LLM 健康验证
    
    统一使用 LiteLLM 路由以兼容各类提供商
    """
    import litellm
    from core.agents.base.model_config import (
        PROVIDER_CONFIG,
        get_compatible_reasoning_kwargs,
        get_compatible_sampling_kwargs,
    )

    effective = _build_effective_llm_config(config)
    provider_cfg = PROVIDER_CONFIG.get(effective.provider, PROVIDER_CONFIG["vllm"])

    if provider_cfg["need_base_url"] and not effective.base_url:
        return HealthCheckResult(
            success=False,
            message=f"提供商 '{effective.provider}' 要求配置 LLM Base URL",
        )

    model_string = f"{provider_cfg['prefix']}/{effective.model_name}"

    kwargs = _get_provider_kwargs_for(effective.provider)
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
                        reply = f"[思考模式] {reasoning[:200].strip()}"
                    else:
                        reply = "(模型已响应，但未返回文本内容)"
        
        return HealthCheckResult(
            success=True,
            message="LLM 服务正常，推理可用",
            latency_ms=latency_ms,
            model_name=model_name,
            reply=reply,
        )
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.exception(
            "LLM 健康验证失败: provider=%s model=%s api_base=%s latency_ms=%s details=%s",
            effective.provider,
            model_string,
            effective_url or "<official-default>",
            latency_ms,
            _collect_exception_debug_info(e),
        )
        return HealthCheckResult(
            success=False,
            message=f"健康验证异常: {str(e)}",
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
