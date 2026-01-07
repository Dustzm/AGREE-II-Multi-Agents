"""
AMA 智能体核心支持模块

提供智能体运行所需的基础设施：
- model_config: LLM 模型配置
- schemas: 数据模型定义
- task_context: 任务上下文管理

工具函数已迁移到 agents/tools/ 目录
communication 模块已删除（无调用方）
"""
from .model_config import get_model

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
    # 数据模型
    "InitiatorInput",
    "AppraiserInput",
    "ReporterInput",
    "EvaluationOutput",
]
