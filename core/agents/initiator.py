"""
Initiator (规划师)

使用 Google ADK LlmAgent 实现
负责快速扫描源文档，根据启用的 Skills 生成评价计划

核心职责：
生成评价计划 plan.md - 基于已启用的 AGREE-II 条目的任务列表，供 Orchestrator 调度
"""
from typing import Optional

from google.adk.agents import LlmAgent

from .base import get_model
from .tools import read_source_document, save_plan, get_enabled_skills_for_plan
from .base.schemas import InitiatorInput
from core.prompts import load_prompt


def create_initiator_agent(lang: Optional[str] = None) -> LlmAgent:
    """
    创建 Initiator Agent (规划师)
    
    Args:
        lang: 提示词语言 (en/zh)，默认从配置读取
    
    职责：
    1. 快速扫描源文档
    2. 识别指南类型
    3. 获取已启用的 Skills
    4. 制定评价计划 (plan.md) - 仅包含已启用的条目
    """
    instruction = load_prompt("initiator", lang)
    
    return LlmAgent(
        input_schema=InitiatorInput,
        name="Initiator",
        model=get_model(),
        description="Planner - Scans documents and generates appraisal plan based on enabled Skills",
        instruction=instruction,
        tools=[
            get_enabled_skills_for_plan,
            read_source_document,
            save_plan,
        ],
        disallow_transfer_to_peers=True,
        output_key="init_result",
    )
