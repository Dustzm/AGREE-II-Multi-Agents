"""
Orchestrator (智能编排器)

使用 Google ADK LlmAgent + AgentTool 实现
通过 AgentTool 将子 Agent 封装为工具，保持状态同步和上下文传递

注：Initiator 已弃用，评价流程直接从 appraise_all 开始
"""
from typing import Optional, Callable, Awaitable

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, FunctionTool

from .base import get_model
from .tools.appraiser_tool_factory import create_appraiser_tool
from .tools.appraiser_batch import create_appraise_all_tool
from .reporter import create_reporter_agent
from core.prompts import load_prompt
from core.skills import list_available_skills


def create_orchestrator(
    lang: Optional[str] = None,
    on_item_progress: Optional[Callable[[str, str, str, Optional[float]], Awaitable[None]]] = None,
    on_summary: Optional[Callable[[int, int, float], Awaitable[None]]] = None,
) -> LlmAgent:
    """
    创建 Orchestrator (智能编排器)

    Args:
        lang: 提示词语言 (en/zh)，默认从配置读取
        on_item_progress: 单条目进度回调
        on_summary: 汇总进度回调

    简化后的流程：
    1. appraise_all - 并发评价所有条目
    2. Reporter - 生成最终报告

    Initiator 已弃用，不再生成 plan.md
    """
    # 创建 Reporter 实例
    reporter = create_reporter_agent(lang)

    # 为每个启用的 skill 创建独立的 Appraiser 工具（使用 IterativeEvaluator）
    enabled_skills = list_available_skills(enabled_only=True)
    appraiser_tools = []
    for skill_id in enabled_skills:
        tool_fn = create_appraiser_tool(skill_id, lang)
        appraiser_tools.append(FunctionTool(tool_fn))

    # 创建批量评价工具（传递回调）
    appraise_all_tool = FunctionTool(create_appraise_all_tool(
        lang=lang,
        on_item_progress=on_item_progress,
        on_summary=on_summary,
    ))

    # 使用 AgentTool 封装 Reporter
    reporter_agent = AgentTool(agent=reporter)

    # 加载提示词
    instruction = load_prompt("orchestrator", lang)

    return LlmAgent(
        name="Orchestrator",
        model=get_model(),
        description="AMA Intelligent Orchestrator - Coordinates agents for AGREE-II guideline appraisal",
        instruction=instruction,
        tools=[
            appraise_all_tool,  # 批量评价工具（主要使用）
            *appraiser_tools,   # 单条目工具（降级/重试使用）
            reporter_agent,
        ],
    )
