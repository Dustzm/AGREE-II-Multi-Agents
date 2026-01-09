"""
Reporter (报告员)

使用 Google ADK LlmAgent 实现
负责汇总所有条目评价，计算维度分数，生成最终评价报告

输出：
- appraisal_report.json: 结构化评价报告
- appraisal_report.md: Markdown 格式报告（便于阅读）
"""
from typing import Optional

from google.adk.agents import LlmAgent

from .base import get_model
from .tools import get_guideline_title, load_all_evaluations, generate_appraisal_report
from .base.schemas import ReporterInput
from core.prompts import load_prompt


def create_reporter_agent(lang: Optional[str] = None) -> LlmAgent:
    """
    创建 Reporter Agent (报告员)
    
    Args:
        lang: 提示词语言 (en/zh)，默认从配置读取
    
    职责：
    1. 读取所有条目评价结果
    2. 计算维度标准化分数
    3. 生成最终评价报告
    """
    instruction = load_prompt("reporter", lang)
    
    return LlmAgent(
        name="Reporter",
        model=get_model(),
        input_schema=ReporterInput,
        description="Reporter - Aggregates evaluation results and generates final report",
        instruction=instruction,
        tools=[
            get_guideline_title,
            load_all_evaluations,
            generate_appraisal_report,
        ],
        disallow_transfer_to_peers=True,
        output_key="report_result",
    )
