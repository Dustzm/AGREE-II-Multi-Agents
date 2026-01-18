"""
报告 API - 获取评价报告列表和详情
"""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import get_settings

router = APIRouter()


class ReportSummary(BaseModel):
    """报告摘要"""
    task_id: str
    title: str
    date: str
    quality_score: Optional[int]  # 总体质量评分 (1-7)
    recommendation: Optional[str]


class BatchFolderInfo(BaseModel):
    """批量文件夹信息"""
    name: str
    report_count: int


class ReportListResponse(BaseModel):
    """报告列表响应 - 支持层级结构"""
    folders: List[BatchFolderInfo] = []
    reports: List[ReportSummary]
    current_folder: Optional[str] = None


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
    total_raw_score: Optional[int] = None  # 总分（所有条目原始分数总和）
    overall_quality_score: Optional[int] = None  # 总体质量评分 (1-7)


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
async def list_reports(folder: Optional[str] = None):
    """
    获取评价报告列表
    
    Args:
        folder: 可选的文件夹名称 (如 "batch_1", "batch_2")
    """
    output_dir = get_output_dir()
    
    if not output_dir.exists():
        return ReportListResponse(folders=[], reports=[], current_folder=folder)
    
    folders = []
    reports = []
    
    # 确定要扫描的目录
    target_dir = output_dir / folder if folder else output_dir
    
    if folder and not target_dir.exists():
        return ReportListResponse(folders=[], reports=[], current_folder=folder)
    
    # 遍历目标目录
    for item in target_dir.iterdir():
        if not item.is_dir():
            continue
        
        # 检查是否是 batch_N 文件夹（仅在根目录时）
        if not folder and item.name.startswith("batch_"):
            # 统计此文件夹下的报告数量
            report_count = len(list(item.glob("*/appraisal_report.json")))
            if report_count > 0:
                folders.append(BatchFolderInfo(
                    name=item.name,
                    report_count=report_count
                ))
            continue
        
        # 检查是否是任务目录（包含 appraisal_report.json）
        report_path = item / "appraisal_report.json"
        if not report_path.exists():
            continue
        
        data = parse_report(report_path)
        if not data:
            continue
        
        # 获取质量评分（优先使用新字段，兼容旧格式）
        quality_score = data.get("overall_quality_score")
        
        if quality_score is None:
            # 旧格式：计算平均分数转换为质量评分
            domain_scores = data.get("domain_scores", {})
            valid_scores = []
            for d in domain_scores.values():
                if isinstance(d, dict):
                    score = d.get("score")
                    if score is not None:
                        valid_scores.append(score)
                elif isinstance(d, (int, float)):
                    valid_scores.append(d)
            avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else None
            quality_score = round(avg_score / 14.3) if avg_score else None  # 100/7 ≈ 14.3
        
        # 获取推荐结论
        recommendation = data.get("overall_assessment", {}).get("recommendation")
        
        reports.append(ReportSummary(
            task_id=data.get("task_id", item.name),
            title=data.get("guideline_title", "未命名指南")[:50],
            date=data.get("evaluation_date", ""),
            quality_score=quality_score,
            recommendation=recommendation
        ))
    
    # 排序：文件夹按名称，报告按日期降序
    folders.sort(key=lambda x: x.name)
    reports.sort(key=lambda x: x.date, reverse=True)
    
    return ReportListResponse(
        folders=folders,
        reports=reports,
        current_folder=folder
    )


@router.get("/reports/{task_id}", response_model=ReportDetail)
async def get_report(task_id: str, folder: Optional[str] = None):
    """
    获取单个报告详情
    
    Args:
        task_id: 任务ID
        folder: 可选的文件夹名称 (如 "batch_1")
    """
    output_dir = get_output_dir()
    
    # 如果指定了文件夹，在该文件夹下查找
    if folder:
        task_dir = output_dir / folder / task_id
    else:
        task_dir = output_dir / task_id
    
    report_path = task_dir / "appraisal_report.json"
    
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"报告不存在: {task_id}")
    
    data = parse_report(report_path)
    if not data:
        raise HTTPException(status_code=500, detail="解析报告失败")
    
    # 转换领域评分 - 兼容两种格式
    # 新格式: {"1": {"name": "...", "score": 66.7, ...}}
    # 旧格式: {"1": 66.7}
    domain_scores = {}
    domain_names = {
        "1": "Scope and Purpose",
        "2": "Stakeholder Involvement", 
        "3": "Rigour of Development",
        "4": "Clarity of Presentation",
        "5": "Applicability",
        "6": "Editorial Independence",
    }
    for key, domain in data.get("domain_scores", {}).items():
        if isinstance(domain, dict):
            # 新格式
            domain_scores[key] = DomainScore(
                name=domain.get("name", domain_names.get(key, f"Domain {key}")),
                score=domain.get("score"),
                item_count=domain.get("item_count", 0),
                evaluated_count=domain.get("evaluated_count", 0)
            )
        else:
            # 旧格式 - domain 直接是分数
            domain_scores[key] = DomainScore(
                name=domain_names.get(key, f"Domain {key}"),
                score=domain if isinstance(domain, (int, float)) else None,
                item_count=0,
                evaluated_count=0
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
        item_evaluations=item_evaluations,
        total_raw_score=data.get("total_raw_score"),
        overall_quality_score=data.get("overall_quality_score")
    )


@router.get("/reports/{task_id}/visual_evidence")
async def get_visual_evidence(task_id: str, folder: Optional[str] = None):
    """
    获取可视化证据数据
    
    Args:
        task_id: 任务ID
        folder: 可选的文件夹名称 (如 "batch_1")
    """
    output_dir = get_output_dir()
    
    # 确定任务目录
    if folder:
        task_dir = output_dir / folder / task_id
    else:
        task_dir = output_dir / task_id
        # 如果根目录不存在，搜索 batch_N 子目录
        if not task_dir.exists():
            for subdir in output_dir.iterdir():
                if subdir.is_dir() and subdir.name.startswith("batch_"):
                    candidate = subdir / task_id
                    if candidate.exists():
                        task_dir = candidate
                        break
    
    prep_dir = task_dir / ".ama" / "prep"
    
    # 从 task_id 中提取基础名称（去掉时间戳）
    base_name = task_id.rsplit("-", 1)[0]
    visual_evidence_path = prep_dir / f"{base_name}_visual_evidence.json"
    
    if not visual_evidence_path.exists():
        raise HTTPException(status_code=404, detail=f"可视化证据不存在: {task_id}")
    
    try:
        with open(visual_evidence_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取可视化证据失败: {e}")


@router.get("/reports/{task_id}/pdf")
async def get_pdf_file(task_id: str):
    """
    获取原始 PDF 文件
    """
    settings = get_settings()
    input_dir = settings.app.path_input
    
    # 从 task_id 中提取 PDF 文件名（去掉时间戳）
    base_name = task_id.rsplit("-", 1)[0]
    pdf_filename = f"{base_name}.pdf"
    
    # 首先在根目录查找
    pdf_path = input_dir / pdf_filename
    
    # 如果根目录没有，递归搜索子目录
    if not pdf_path.exists():
        for sub_path in input_dir.rglob(pdf_filename):
            pdf_path = sub_path
            break
    
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF 文件不存在: {base_name}.pdf")
    
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=pdf_filename
    )

