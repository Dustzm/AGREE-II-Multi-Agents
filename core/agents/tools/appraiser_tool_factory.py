"""
Appraiser 工具工厂

为每个 skill_id 创建独立的评价工具函数，
内部调用 IterativeEvaluator 执行评价，并自动保存结果到 item.json
"""
import logging
from typing import Callable, Dict, Any

from core.agents.evaluator import run_iterative_evaluation
from core.agents.tools.appraiser_tools import save_item_evaluation

logger = logging.getLogger(__name__)


def create_appraiser_tool(skill_id: str, lang: str = "en") -> Callable[[str], Dict[str, Any]]:
    """
    为指定 skill 创建评价工具函数
    
    Args:
        skill_id: 条目 ID（如 "item_1"）
        lang: 提示词语言
    
    Returns:
        工具函数，签名为 (task_id: str) -> dict
    """
    def appraise(task_id: str) -> Dict[str, Any]:
        """
        使用迭代评价器评价指定条目，并保存结果到 item.json
        
        Args:
            task_id: 任务 ID
        
        Returns:
            评价结果字典
        """
        logger.info(f"开始评价: task_id={task_id}, skill_id={skill_id}")
        
        # 调用迭代评价器
        result = run_iterative_evaluation(task_id, skill_id, lang)
        
        # 保存评价结果到 .ama/evaluations/{skill_id}.json
        if result.get("status") == "success":
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
    
    # 设置函数元信息，供 Orchestrator 和 ADK 识别
    appraise.__name__ = f"Appraiser_{skill_id}"
    appraise.__doc__ = f"使用迭代评价器评价 {skill_id} 条目，并自动保存结果"
    
    return appraise
