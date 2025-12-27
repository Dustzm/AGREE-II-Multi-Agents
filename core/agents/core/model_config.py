"""
KG-Weaver Agent 配置模块

提供 Google ADK 所需的模型配置和工具
"""
import os
from functools import lru_cache

from google.adk.models.lite_llm import LiteLlm

from core.config import get_settings


def _setup_llm_env():
    """设置 LiteLLM 需要的环境变量"""
    settings = get_settings()
    
    # LiteLLM 使用 OPENAI_API_BASE 和 OPENAI_API_KEY 环境变量
    os.environ["OPENAI_API_BASE"] = settings.llm.base_url
    os.environ["OPENAI_API_KEY"] = settings.llm.api_key or "dummy-key"


# 在模块加载时就设置环境变量
_setup_llm_env()


@lru_cache
def get_model() -> LiteLlm:
    """
    获取配置好的 LiteLLM 模型实例
    
    使用 openai/ 前缀表示 OpenAI 兼容格式
    """
    settings = get_settings()
    
    # 使用 openai/ 前缀表示 OpenAI 兼容格式
    model_string = f"openai/{settings.llm.model_name}"
    
    return LiteLlm(model=model_string)


# 预定义的模型常量，方便在 Agent 中使用
GEMINI_2_FLASH = "gemini-2.0-flash"
