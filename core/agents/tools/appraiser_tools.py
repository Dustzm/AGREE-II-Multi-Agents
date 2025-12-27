"""
Appraiser 智能体工具函数

提供 AGREE-II 评价相关的工具：
- 加载指南全文
- 加载条目评价配置
- 保存评价结果
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .public_tools import get_task_paths, read_source_document
from core.skills import load_skill_config as _load_skill_config

logger = logging.getLogger(__name__)


def load_source_to_cache(task_id: str) -> dict:
    """
    加载指南全文到内存缓存
    
    Args:
        task_id: 任务 ID
    
    Returns:
        dict: {
            "status": "success/error",
            "content": str,  # 指南全文
            "char_count": int,  # 字符数
            "error": str
        }
    """
    result = read_source_document(task_id)
    
    if result["status"] == "success":
        result["char_count"] = len(result["content"])
        logger.info(f"指南全文已加载: {result['char_count']} 字符")
    
    return result



def save_item_evaluation(
    task_id: str,
    skill_id: str,
    step1_locate: str,
    step2_evidence: list[str],
    step3_reasoning: str,
    score: int
) -> dict:
    """
    保存单个条目的评价结果
    
    结果保存到 .ama/evaluations/{skill_id}.json
    
    Args:
        task_id: 任务 ID
        skill_id: 条目 ID（如 "item_1"）
        step1_locate: 定位信息描述
        step2_evidence: 提取的原文证据列表
        step3_reasoning: 推理过程
        score: 最终评分 (1-7)
    
    Returns:
        dict: {
            "status": "success/error",
            "path": str,
            "error": str
        }
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "path": "",
                "error": f"任务不存在: {task_id}"
            }
        
        # 创建 evaluations 目录
        eval_dir = Path(paths["weaver_dir"]) / "evaluations"
        eval_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载 skill 配置获取元信息
        try:
            config = _load_skill_config(skill_id, check_enabled=False)
            domain = config.get("domain", 0)
            title = config.get("title", skill_id)
        except FileNotFoundError:
            domain = 0
            title = skill_id
        
        # 构建评价结果
        evaluation = {
            "item_id": skill_id,
            "domain": domain,
            "title": title,
            "chain_of_thought": {
                "step1_locate": step1_locate,
                "step2_evidence": step2_evidence,
                "step3_reasoning": step3_reasoning,
                "score": score
            },
            "metadata": {
                "skill_config": f"skills/{skill_id}.yaml",
                "evaluated_at": datetime.now().isoformat(),
            }
        }
        
        # 保存到 JSON 文件
        file_path = eval_dir / f"{skill_id}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(evaluation, f, ensure_ascii=False, indent=2)
        
        logger.info(f"条目评价已保存: {file_path} (分数: {score})")
        return {
            "status": "success",
            "path": str(file_path),
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "path": "",
            "error": str(e)
        }


def list_evaluations(task_id: str) -> dict:
    """
    列出已完成的评价结果
    
    Args:
        task_id: 任务 ID
    
    Returns:
        dict: {
            "status": "success/error",
            "evaluations": list[str],  # 已完成的条目 ID 列表
            "error": str
        }
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "evaluations": [],
                "error": f"任务不存在: {task_id}"
            }
        
        eval_dir = Path(paths["weaver_dir"]) / "evaluations"
        
        if not eval_dir.exists():
            return {
                "status": "success",
                "evaluations": [],
                "error": ""
            }
        
        evaluations = [f.stem for f in sorted(eval_dir.glob("item_*.json"))]
        
        return {
            "status": "success",
            "evaluations": evaluations,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "evaluations": [],
            "error": str(e)
        }


def read_evaluation(task_id: str, skill_id: str) -> dict:
    """
    读取指定条目的评价结果
    
    Args:
        task_id: 任务 ID
        skill_id: 条目 ID
    
    Returns:
        dict: {
            "status": "success/error",
            "evaluation": dict,
            "error": str
        }
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "evaluation": {},
                "error": f"任务不存在: {task_id}"
            }
        
        file_path = Path(paths["weaver_dir"]) / "evaluations" / f"{skill_id}.json"
        
        if not file_path.exists():
            return {
                "status": "error",
                "evaluation": {},
                "error": f"评价结果不存在: {file_path}"
            }
        
        with open(file_path, "r", encoding="utf-8") as f:
            evaluation = json.load(f)
        
        return {
            "status": "success",
            "evaluation": evaluation,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "evaluation": {},
            "error": str(e)
        }
