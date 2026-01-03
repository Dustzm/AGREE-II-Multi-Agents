"""
Appraiser (评价专家)

使用 Google ADK LlmAgent 实现
负责根据 Orchestrator 传入的 skill_id，对指南进行 AGREE-II 条目评价

核心方法：Chain of Thought (CoT) 四步法
1. 定位信息 (Locate)
2. 证据提取 (Evidence Extraction)
3. 标准对比推理 (Reasoning)
4. 最终评分 (Scoring)
"""
from functools import partial
from typing import Optional

from google.adk.agents import LlmAgent

from .core import get_model
from .tools import load_source_to_cache, save_item_evaluation
from .core.schemas import AppraiserInput
from core.prompts.renderer import render_appraiser_prompt


def create_appraiser_agent(skill_id: str, lang: Optional[str] = None) -> LlmAgent:
    """
    创建 Appraiser Agent (评价专家)
    
    Args:
        skill_id: 条目 ID（如 "item_1", "item_2"）
        lang: 提示词语言 (en/zh)，默认从配置读取
    
    职责：
    1. 根据预渲染的提示词（包含评价配置）进行评价
    2. 按 CoT 四步法进行评价
    3. 保存评价结果到 .weaver/evaluations/
    
    改进：
    - 提示词已包含完整的评价标准，无需调用 load_skill_config
    - 评价配置在 Python 层面预处理，LLM 直接执行
    - 使用证据池提供领域相关的上下文，而非全文
    """
    # 动态渲染提示词（包含具体评价标准）
    instruction = render_appraiser_prompt(skill_id, lang)
    
    # 创建绑定了 skill_id 的 load_source_to_cache 版本
    load_source_with_skill = partial(load_source_to_cache, skill_id=skill_id)
    # 设置函数名称，以便 ADK 正确识别
    load_source_with_skill.__name__ = "load_source_to_cache"
    
    return LlmAgent(
        name=f"Appraiser_{skill_id}",
        model=get_model(),
        input_schema=AppraiserInput,
        description=f"Evaluation Expert for {skill_id} - Evaluates guideline quality using AGREE-II criteria",
        instruction=instruction,
        tools=[
            load_source_with_skill,
            save_item_evaluation,
        ],
        disallow_transfer_to_peers=True,
        output_key="appraise_result",
    )
