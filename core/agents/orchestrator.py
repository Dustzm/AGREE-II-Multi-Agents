"""
Orchestrator (智能编排器)

使用 Google ADK LlmAgent + AgentTool 实现
通过 AgentTool 将子 Agent 封装为工具，保持状态同步和上下文传递
"""
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool, FunctionTool

from .base import get_model
from .tools import read_plan, update_plan_item
from .tools.appraiser_tool_factory import create_appraiser_tool
from .tools.appraiser_batch import create_appraise_all_tool
from .initiator import create_initiator_agent
from .reporter import create_reporter_agent
from core.prompts import load_prompt
from core.skills import list_available_skills


def create_orchestrator(lang: Optional[str] = None) -> LlmAgent:
    """
    创建 Orchestrator (智能编排器)
    
    Args:
        lang: 提示词语言 (en/zh)，默认从配置读取
    
    使用 ADK 的 AgentTool 将子 Agent 封装为工具
    Orchestrator 调用这些工具时，子 Agent 的响应和状态会自动同步回来
    
    改进（方案 A - 预创建模式）：
    使用 FunctionTool 包装 IterativeEvaluator 替代 LlmAgent Appraiser
    工具名格式：Appraiser_item_1, Appraiser_item_2, ...
    
    新增 appraise_all 工具：并发评价所有条目，提升效率
    """
    # 创建 Initiator 和 Reporter 实例
    initiator = create_initiator_agent(lang)
    reporter = create_reporter_agent(lang)
    
    # 为每个启用的 skill 创建独立的 Appraiser 工具（使用 IterativeEvaluator）
    enabled_skills = list_available_skills(enabled_only=True)
    appraiser_tools = []
    for skill_id in enabled_skills:
        tool_fn = create_appraiser_tool(skill_id, lang)
        appraiser_tools.append(FunctionTool(tool_fn))
    
    # 创建批量评价工具
    appraise_all_tool = FunctionTool(create_appraise_all_tool(lang))
    
    # 使用 AgentTool 封装 Initiator 和 Reporter
    initiator_agent = AgentTool(agent=initiator)
    reporter_agent = AgentTool(agent=reporter)
    
    # 加载提示词
    instruction = load_prompt("orchestrator", lang)
    
    return LlmAgent(
        name="Orchestrator",
        model=get_model(),
        description="AMA Intelligent Orchestrator - Coordinates multiple agents for AGREE-II guideline appraisal",
        instruction=instruction,
        tools=[
            initiator_agent,
            appraise_all_tool,  # 批量评价工具（推荐使用）
            *appraiser_tools,   # 单条目工具（降级/重试使用）
            reporter_agent,
            read_plan,
            update_plan_item,
        ],
    )

