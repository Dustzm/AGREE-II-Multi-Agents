"""
AMA (AGREE-Multi-Agents) 智能体模块

评价编排见 core.pipeline；这里提供各阶段使用的智能体与工具：
- portal          门户智能体（Web 普通对话）
- appraiser       迭代评价器 (LiteLLM)
- item17_evidence item_17 证据打包
- reporter        报告员（ADK）
- explorer        附件探索（预处理阶段）
- tools/          工具函数
- base/           模型配置、数据模型与任务上下文
"""
from .portal import create_portal_agent, list_input_pdfs, check_extraction_intent
from .reporter import create_reporter_agent
from .base import get_model
from .tools import init_task_directory


__all__ = [
    # 门户智能体
    "create_portal_agent",
    "list_input_pdfs",
    "check_extraction_intent",
    # AMA 核心智能体
    "create_reporter_agent",
    # 核心模块
    "get_model",
    "init_task_directory",
]
