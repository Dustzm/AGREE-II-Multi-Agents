"""
KG-Weaver Core Package

智能体核心模块，包含：
- agents: 智能体定义
- parsers: 文档解析器
- prompts: 提示词模板
- skills: 技能配置
- config: 配置管理
"""
from .agents import (
    create_portal_agent,
    create_orchestrator,
    create_initiator_agent,
    create_appraiser_agent,
    create_reporter_agent,
    list_input_pdfs,
    check_extraction_intent,
    get_model,
    init_task_directory,
)
from .config import get_settings
from .parsers import MinerUParser

__all__ = [
    # 智能体
    "create_portal_agent",
    "create_orchestrator",
    "create_initiator_agent",
    "create_appraiser_agent",
    "create_reporter_agent",
    "list_input_pdfs",
    "check_extraction_intent",
    "get_model",
    "init_task_directory",
    # 配置
    "get_settings",
    # 解析器
    "MinerUParser",
]
