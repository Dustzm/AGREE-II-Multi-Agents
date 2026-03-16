import json
import os
from pathlib import Path

from google.adk.models.lite_llm import LiteLlm

from core.config import get_settings


# ===== 从 providers.json 动态加载提供商配置 =====

_PROVIDERS_JSON = Path(__file__).parent.parent.parent / "config" / "providers.json"
_providers_cache: dict | None = None


def load_providers_config() -> dict:
    """加载 providers.json 并缓存"""
    global _providers_cache
    if _providers_cache is not None:
        return _providers_cache
    with open(_PROVIDERS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)
    _providers_cache = data.get("providers", {})
    return _providers_cache


def get_provider_config_for(provider_id: str) -> dict:
    """获取指定提供商的路由配置"""
    providers = load_providers_config()
    return providers.get(provider_id, providers.get("vllm", {}))


# 兼容旧代码的快捷访问
PROVIDER_CONFIG = load_providers_config()


def _get_provider_cfg() -> dict:
    """获取当前设置的提供商配置（便捷函数）"""
    settings = get_settings()
    return get_provider_config_for(settings.llm.provider)

def _setup_llm_env():
    """根据提供商设置 LiteLLM 需要的环境变量"""
    settings = get_settings()
    provider_cfg = get_provider_config_for(settings.llm.provider)
    
    # 1. 设置 API Key 环境变量
    env_key = provider_cfg["env_key"]
    os.environ[env_key] = settings.llm.api_key or "dummy-key"
    
    # 2. 如果提供商需要 base_url，则设置对应的基础地址环境变量
    if provider_cfg.get("need_base_url"):
        # 优先用户配置，其次 providers.json 中的预设地址
        effective_url = settings.llm.base_url or provider_cfg.get("default_base_url", "")
        env_base = provider_cfg.get("env_base")
        if env_base and effective_url:
            os.environ[env_base] = effective_url

# 在模块加载时就设置环境变量
_setup_llm_env()

# 可刷新的模型缓存
_model_instance: LiteLlm | None = None

def get_provider_kwargs() -> dict:
    """获取兼容当前提供商的额外参数 (如自部署专属的关闭 thinking 指令)"""
    settings = get_settings()
    # 仅选了 vllm 才会打上这些强制参数，其余所有原生、兼容官方厂，一律不发！
    if settings.llm.provider == "vllm":
        return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    return {}

def get_model() -> LiteLlm:
    """
    获取配置好的 LiteLLM 模型实例（可刷新的单例）
    
    使用 provider 前缀表示路由模式
    温度设为较低值以提高指令遵循度
    """
    global _model_instance
    if _model_instance is not None:
        return _model_instance
    
    settings = get_settings()
    provider_cfg = get_provider_config_for(settings.llm.provider)
    
    # 动态拼接 model string (如 openai/qwen2.5 或 deepseek/deepseek-chat)
    prefix = provider_cfg["prefix"]
    model_string = f"{prefix}/{settings.llm.model_name}"
    
    kwargs = get_provider_kwargs()
    if settings.llm.api_key:
        kwargs["api_key"] = settings.llm.api_key
    if provider_cfg.get("need_base_url"):
        effective_url = settings.llm.base_url or provider_cfg.get("default_base_url", "")
        if effective_url:
            kwargs["api_base"] = effective_url

    # 设置较低温度以提高工具调用的可靠性
    _model_instance = LiteLlm(
        model=model_string,
        temperature=0.2,
        timeout=settings.llm.timeout,
        **kwargs,
    )
    return _model_instance


def refresh_model() -> LiteLlm:
    """刷新模型实例（配置更新后调用）"""
    global _model_instance
    _model_instance = None
    _setup_llm_env()
    return get_model()


# 预定义的模型常量，方便在 Agent 中使用
GEMINI_2_FLASH = "gemini-2.0-flash"
