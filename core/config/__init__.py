"""
KG-Weaver 配置模块

包含两部分配置：
- settings.py: 环境配置 (.env 文件)，包含 LLM、MinerU、App 设置
- loader.py: 系统配置 (settings.yaml)，包含 AMA 专有设置
"""
# 环境配置 (主要使用)
from .settings import (
    Settings,
    LLMSettings,
    MinerUSettings,
    AppSettings,
    get_settings,
    refresh_settings,
    update_env_file,
)

# 系统配置 (AMA 专有)
from .loader import (
    AMASettings,
    load_settings as load_ama_settings,
    get_settings as get_ama_settings,
    CONFIG_DIR,
)


__all__ = [
    # 环境配置 (主要)
    "Settings",
    "LLMSettings",
    "MinerUSettings",
    "AppSettings",
    "get_settings",
    # AMA 系统配置
    "AMASettings",
    "load_ama_settings",
    "get_ama_settings",
    "CONFIG_DIR",
]
