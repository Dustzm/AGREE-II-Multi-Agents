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
from .tools.boundary import as_tool
from core.prompts import load_prompt
from core.llm.context import RequestBudget, current_llm_settings
from core.llm.json_output import TruncatedModelOutput


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
    llm = current_llm_settings()
    budget = RequestBudget.from_settings(llm, llm.model_name)

    def check_request(callback_context, llm_request):
        # ADK includes system instructions, schemas and tool response history here.
        llm_request.config.max_output_tokens = budget.output_limit(8192)
        budget.check(llm_request.model_dump(mode="json", exclude={"tools_dict"}, exclude_none=True),
                     llm_request.config.max_output_tokens, stage="reporter")

    def check_response(callback_context, llm_response):
        if str(llm_response.finish_reason).split(".")[-1] == "MAX_TOKENS":
            raise TruncatedModelOutput("Reporter output truncated")
    
    return LlmAgent(
        name="Reporter",
        model=get_model(num_retries=0),
        input_schema=ReporterInput,
        before_model_callback=check_request,
        after_model_callback=check_response,
        description="Reporter - Aggregates evaluation results and generates final report",
        instruction=instruction,
        tools=[
            get_guideline_title,
            load_all_evaluations,
            as_tool(generate_appraisal_report),
        ],
        disallow_transfer_to_peers=True,
        output_key="report_result",
    )
