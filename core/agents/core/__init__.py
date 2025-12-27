"""
AMA 智能体核心支持模块

提供智能体运行所需的基础设施：
- model_config: LLM 模型配置
- communication: 智能体间通信管理
- schemas: 数据模型定义

工具函数已迁移到 agents/tools/ 目录
"""
from .model_config import get_model

from .communication import (
    AgentMessage,
    AgentCommunicationManager,
    get_communication_manager,
    send_agent_message,
    get_agent_messages,
)

from .task_context import (
    TaskContext,
    create_task_context,
    get_task_context,
)

from .schemas import (
    InitiatorInput,
    AppraiserInput,
    ReporterInput,
    EvaluationOutput,
)


__all__ = [
    # 模型配置
    "get_model",
    # 任务上下文
    "TaskContext",
    "create_task_context",
    "get_task_context",
    # 通信管理
    "AgentMessage",
    "AgentCommunicationManager",
    "get_communication_manager",
    "send_agent_message",
    "get_agent_messages",
    # 数据模型
    "InitiatorInput",
    "AppraiserInput",
    "ReporterInput",
    "EvaluationOutput",
]
