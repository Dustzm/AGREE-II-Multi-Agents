"""
报告 API - 获取评价报告列表和详情
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.config import get_settings

router = APIRouter()


class ReportSummary(BaseModel):
    """报告摘要"""
    task_id: str
    title: str
    date: str
    score: Optional[float]  # 平均分数
    recommendation: Optional[str]


class ReportListResponse(BaseModel):
    """报告列表响应"""
    reports: List[ReportSummary]


class DomainScore(BaseModel):
    """领域评分"""
    name: str
    score: Optional[float]
    item_count: int
    evaluated_count: int


class ItemEvaluation(BaseModel):
    """条目评价详情"""
    item_id: str
    domain: int
    title: str
    score: Optional[int]
    step1_locate: Optional[str]
    step2_evidence: Optional[List[str]]
    step3_reasoning: Optional[str]
    evaluated_at: Optional[str]


class ReportDetail(BaseModel):
    """报告详情"""
    task_id: str
    guideline_title: str
    evaluation_date: str
    evaluator: str
    domain_scores: Dict[str, DomainScore]
    overall_assessment: Dict[str, Any]
    item_evaluations: List[ItemEvaluation]


def get_output_dir() -> Path:
    """获取 output 目录路径"""
    return get_settings().app.path_output


def parse_report(report_path: Path) -> Optional[dict]:
    """解析报告 JSON 文件"""
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_evaluations(task_dir: Path) -> List[dict]:
    """加载任务目录下的所有评价结果"""
    evaluations = []
    eval_dir = task_dir / ".ama" / "evaluations"
    
    if not eval_dir.exists():
        return evaluations
    
    # 按 item_X.json 格式查找并排序
    for eval_file in sorted(eval_dir.glob("item_*.json")):
        try:
            with open(eval_file, "r", encoding="utf-8") as f:
                evaluations.append(json.load(f))
        except Exception:
            continue
    
    return evaluations


@router.get("/reports", response_model=ReportListResponse)
async def list_reports():
    """
    获取所有评价报告列表
    """
    output_dir = get_output_dir()
    
    if not output_dir.exists():
        return ReportListResponse(reports=[])
    
    reports = []
    
    # 遍历 output 目录下的所有任务目录
    for task_dir in output_dir.iterdir():
        if not task_dir.is_dir():
            continue
        
        report_path = task_dir / "appraisal_report.json"
        if not report_path.exists():
            continue
        
        data = parse_report(report_path)
        if not data:
            continue
        
        # 计算平均分数（只计算有评分的领域）
        domain_scores = data.get("domain_scores", {})
        valid_scores = [d.get("score") for d in domain_scores.values() if d.get("score") is not None]
        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else None
        
        # 获取推荐结论
        recommendation = data.get("overall_assessment", {}).get("recommendation")
        
        reports.append(ReportSummary(
            task_id=data.get("task_id", task_dir.name),
            title=data.get("guideline_title", "未命名指南")[:50],  # 截断过长标题
            date=data.get("evaluation_date", ""),
            score=round(avg_score, 1) if avg_score else None,
            recommendation=recommendation
        ))
    
    # 按日期降序排序
    reports.sort(key=lambda x: x.date, reverse=True)
    
    return ReportListResponse(reports=reports)


@router.get("/reports/{task_id}", response_model=ReportDetail)
async def get_report(task_id: str):
    """
    获取单个报告详情
    """
    output_dir = get_output_dir()
    task_dir = output_dir / task_id
    report_path = task_dir / "appraisal_report.json"
    
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"报告不存在: {task_id}")
    
    data = parse_report(report_path)
    if not data:
        raise HTTPException(status_code=500, detail="解析报告失败")
    
    # 转换领域评分
    domain_scores = {}
    for key, domain in data.get("domain_scores", {}).items():
        domain_scores[key] = DomainScore(
            name=domain.get("name", f"Domain {key}"),
            score=domain.get("score"),
            item_count=domain.get("item_count", 0),
            evaluated_count=domain.get("evaluated_count", 0)
        )
    
    # 从 evaluations 目录加载条目评价详情
    evaluations = load_evaluations(task_dir)
    item_evaluations = []
    for eval_data in evaluations:
        cot = eval_data.get("chain_of_thought", {})
        metadata = eval_data.get("metadata", {})
        item_evaluations.append(ItemEvaluation(
            item_id=eval_data.get("item_id", ""),
            domain=eval_data.get("domain", 0),
            title=eval_data.get("title", ""),
            score=cot.get("score"),
            step1_locate=cot.get("step1_locate"),
            step2_evidence=cot.get("step2_evidence"),
            step3_reasoning=cot.get("step3_reasoning"),
            evaluated_at=metadata.get("evaluated_at")
        ))
    
    return ReportDetail(
        task_id=data.get("task_id", task_id),
        guideline_title=data.get("guideline_title", ""),
        evaluation_date=data.get("evaluation_date", ""),
        evaluator=data.get("evaluator", ""),
        domain_scores=domain_scores,
        overall_assessment=data.get("overall_assessment", {}),
        item_evaluations=item_evaluations
    )

