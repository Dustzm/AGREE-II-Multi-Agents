"""
AMA (AGREE-Multi-Agents) 智能体模块

使用 Google ADK 实现的多智能体系统，用于 AGREE-II 指南质量评价

目录结构：
- agents/           智能体定义
  - portal          门户智能体 (用户交互)
  - orchestrator    智能编排器
  - initiator       规划师
  - appraiser       评价专家
  - reporter        报告员
- agents/tools/     工具函数
  - public_tools    公共工具
  - initiator_tools Initiator 工具
  - orchestrator_tools Orchestrator 工具
  - appraiser_tools Appraiser 工具
  - reporter_tools  Reporter 工具
- agents/core/      核心支持模块
  - model_config    模型配置
  - communication   通信管理
  - schemas         数据模型
  - preprocessor    文档预处理模块
"""
from .portal import create_portal_agent, list_input_pdfs, check_extraction_intent
from .orchestrator import create_orchestrator
from .initiator import create_initiator_agent
from .appraiser import create_appraiser_agent
from .reporter import create_reporter_agent
from .core import get_model
from .tools import init_task_directory


__all__ = [
    # 门户智能体
    "create_portal_agent",
    "list_input_pdfs",
    "check_extraction_intent",
    # 编排器
    "create_orchestrator",
    # AMA 核心智能体
    "create_initiator_agent",
    "create_appraiser_agent",
    "create_reporter_agent",
    # 核心模块
    "get_model",
    "init_task_directory",
]
