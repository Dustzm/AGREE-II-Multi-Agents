"""
Appraiser 工具工厂

为每个 skill_id 创建独立的评价工具函数，
内部调用 IterativeEvaluator 执行评价，并自动保存结果到 item.json

并发优化：使用 async + asyncio.to_thread 将同步 LLM 调用放入线程池，
避免阻塞事件循环，从而支持真正的并发评价
"""
import asyncio
import logging
from typing import Callable, Dict, Any, Coroutine

from core.agents.appraiser import run_iterative_evaluation
from core.agents.tools.appraiser_tools import save_item_evaluation

logger = logging.getLogger(__name__)


def _sync_appraise(task_id: str, skill_id: str, lang: str) -> Dict[str, Any]:
    """
    同步版本的评价函数（在线程池中执行）
    
    Args:
        task_id: 任务 ID
        skill_id: 条目 ID
        lang: 语言
    
    Returns:
        评价结果字典
    """
    logger.info(f"开始评价: task_id={task_id}, skill_id={skill_id}")
    
    # 调用迭代评价器（同步调用）
    result = run_iterative_evaluation(task_id, skill_id, lang)
    
    # 保存评价结果到 .ama/evaluations/{skill_id}.json
    # 无论是 success 还是 no_evidence 状态都需要保存
    status = result.get("status")
    if status in ("success", "no_evidence"):
        cot = result.get("chain_of_thought", {})
        save_result = save_item_evaluation(
            task_id=task_id,
            skill_id=skill_id,
            step1_locate=cot.get("step1_locate", ""),
            step2_evidence=cot.get("step2_evidence", []),
            step3_reasoning=cot.get("step3_reasoning", ""),
            score=cot.get("score", 1),
            evidence_sources=result.get("evidence_sources", [])
        )
        result["save_status"] = save_result["status"]
        result["save_path"] = save_result.get("path", "")
        logger.info(f"评价结果已保存: {save_result.get('path')}")
    else:
        logger.warning(f"评价失败: {result.get('error', '未知错误')}")
    
    return result


def create_appraiser_tool(skill_id: str, lang: str = "en") -> Callable[[str], Coroutine[Any, Any, Dict[str, Any]]]:
    """
    为指定 skill 创建异步评价工具函数
    
    Args:
        skill_id: 条目 ID（如 "item_1"）
        lang: 提示词语言
    
    Returns:
        异步工具函数，签名为 async (task_id: str) -> dict
    
    并发优化：
    - 返回异步函数，Google ADK 的 FunctionTool 支持 async 函数
    - 使用 asyncio.to_thread() 将同步 LLM 调用放入线程池
    - 这样多个评价任务可以真正并发执行，不会阻塞事件循环
    """
    async def appraise(task_id: str) -> Dict[str, Any]:
        """
        使用迭代评价器评价指定条目，并保存结果到 item.json
        
        Args:
            task_id: 任务 ID
        
        Returns:
            评价结果字典
        """
        # 使用 asyncio.to_thread 将同步调用放入线程池执行
        # 这样不会阻塞事件循环，支持真正的并发
        return await asyncio.to_thread(_sync_appraise, task_id, skill_id, lang)
    
    # 设置函数元信息，供 Orchestrator 和 ADK 识别
    appraise.__name__ = f"Appraiser_{skill_id}"
    appraise.__doc__ = f"使用迭代评价器评价 {skill_id} 条目，并自动保存结果"
    
    return appraise
