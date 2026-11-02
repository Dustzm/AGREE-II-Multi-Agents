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
from typing import Optional, Any

from .public_tools import get_task_paths
from core.skills import load_skill_config as _load_skill_config
from core.utils.unicode_sanitizer import sanitize_unicode
from core.utils.atomic_json import write_json_atomically

logger = logging.getLogger(__name__)


def save_item_evaluation(
    task_id: str,
    skill_id: str,
    step1_locate: str,
    step2_evidence: list[str],
    step3_reasoning: str,
    score: int,
    evidence_sources: Optional[list[str]] = None,
    rule_engine: Optional[dict[str, Any]] = None,
    hybrid_router: Optional[dict[str, Any]] = None,
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
        evidence_sources: 证据来源 chunk_id 列表 (可选)
        rule_engine: 规则引擎输出详情 (可选)
        hybrid_router: Dynamic-Hybrid 路由结果 (可选)
    
    Returns:
        dict: {"status": "success", "path": str, "error": ""}

    Raises:
        任务不存在、评分无效、结果未通过校验或写入失败时抛出原始异常。
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            raise FileNotFoundError(f"任务不存在: {task_id}")
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 7:
            raise ValueError(f"条目评分必须为 1-7 的整数: {skill_id}")
        
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
        evaluation = sanitize_unicode({
            "item_id": skill_id,
            "domain": domain,
            "title": title,
            "chain_of_thought": {
                "step1_locate": step1_locate,
                "step2_evidence": step2_evidence,
                "step3_reasoning": step3_reasoning,
                "score": score
            },
            "evidence_sources": evidence_sources or [],
            "metadata": {
                "skill_config": f"skills/{skill_id}.yaml",
                "evaluated_at": datetime.now().isoformat(),
            }
        })
        if rule_engine is not None:
            evaluation["rule_engine"] = sanitize_unicode(rule_engine)
        if hybrid_router is not None:
            evaluation["hybrid_router"] = sanitize_unicode(hybrid_router)
        
        if not validate_evaluation(evaluation, skill_id):
            raise ValueError(f"条目包含无效结果或未解决的技术失败: {skill_id}")

        # 保存到 JSON 文件
        file_path = eval_dir / f"{skill_id}.json"
        write_json_atomically(file_path, evaluation)
        
        logger.info(f"条目评价已保存: {file_path} (分数: {score})")
        return {
            "status": "success",
            "path": str(file_path),
            "error": ""
        }
    except Exception:
        logger.exception("条目评价保存失败: task=%s item=%s", task_id, skill_id)
        raise


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
        
        evaluations = [f.stem for f in sorted(eval_dir.glob("item_*.json")) if read_evaluation(task_id, f.stem)["status"] == "success"]
        
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


def validate_evaluation(evaluation: Any, skill_id: str) -> bool:
    """校验可复用条目的身份、领域和基本评分内容。"""
    if not isinstance(evaluation, dict):
        return False
    # Persisted technical failures must never become reusable low scores.
    def has_failure(value):
        if isinstance(value, dict):
            if value.get("severity") == "error" or value.get("code") in {
                "rubric_appraiser_json_failed", "rubric_classifier_missing_decision",
                "verifier_missing_decision", "group_verifier_missing_decision", "classifier_missing_decision",
            }:
                return True
            return any(has_failure(item) for item in value.values())
        return isinstance(value, list) and any(has_failure(item) for item in value)

    if has_failure(evaluation.get("rule_engine")):
        return False
    cot = evaluation.get("chain_of_thought")
    if not isinstance(cot, dict):
        return False
    score = cot.get("score")
    return (
        evaluation.get("item_id") == skill_id
        and evaluation.get("domain") == _load_skill_config(skill_id, check_enabled=False).get("domain")
        and isinstance(score, int)
        and not isinstance(score, bool)
        and 1 <= score <= 7
        and isinstance(cot.get("step2_evidence"), list)
        and isinstance(cot.get("step3_reasoning"), (str, dict))
    )


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
        if not validate_evaluation(evaluation, skill_id):
            raise ValueError(f"条目结果损坏或与条目不匹配: {file_path}")
        
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
