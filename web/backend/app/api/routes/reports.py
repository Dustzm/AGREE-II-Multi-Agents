"""
报告 API - 获取评价报告列表和详情
"""
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import get_settings
from core.agents.tools.appraiser_tools import validate_evaluation

from ...i18n import request_language, t
from ...i18n.messages import LocalizedHTTPException, sanitize_diagnostic

logger = logging.getLogger(__name__)

router = APIRouter()


class ReportSummary(BaseModel):
    """报告摘要"""
    task_id: str
    title: str
    date: str
    quality_score: Optional[int]  # 总体质量评分 (1-7)
    recommendation: Optional[str]
    status: str = "complete"
    missing_items: List[str] = []


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


class ReasonItem(BaseModel):
    """单条评分理由"""
    point: str
    explanation: str
    evidence_refs: List[int] = []


class StructuredReasoning(BaseModel):
    """结构化推理"""
    summary: str
    reasons: List[ReasonItem]
    score_justification: str


class ScoringPrincipleItem(BaseModel):
    """评分准则条目"""
    range: str
    condition: str


class CitationRegion(BaseModel):
    """引用原句在原文中的一处位置"""
    page_idx: int
    bbox: List[float]  # [x1, y1, x2, y2] 归一化坐标 0-1
    block_id: Optional[str] = None
    chunk_id: Optional[str] = None


class Citation(BaseModel):
    """一条引用：step2_evidence 中逐字摘录的原句及其定位"""
    text: str
    match: Literal["exact", "span", "partial", "none"]
    regions: List[CitationRegion] = []


_LOCATED_MATCHES = frozenset({"exact", "span", "partial"})


def _citation_region(raw: Any) -> Optional[CitationRegion]:
    if not isinstance(raw, dict):
        return None
    page_idx, bbox = raw.get("page_idx"), raw.get("bbox")
    if isinstance(page_idx, bool) or not isinstance(page_idx, int) or page_idx < 0:
        return None
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in bbox):
        return None
    x1, y1, x2, y2 = (float(v) for v in bbox)
    if x2 < x1 or y2 < y1:
        return None
    block_id, chunk_id = raw.get("block_id"), raw.get("chunk_id")
    return CitationRegion(
        page_idx=page_idx,
        bbox=[x1, y1, x2, y2],
        block_id=block_id if isinstance(block_id, str) else None,
        chunk_id=chunk_id if isinstance(chunk_id, str) else None,
    )


def parse_citations(raw: Any) -> Optional[List[Citation]]:
    """校验条目结果中的 `citations`：旧结果没有该字段时返回 None（前端沿用按 chunk 定位）；
    丢弃格式错误的条目与区域，没有有效区域或匹配方式未知的条目按未定位（none）处理。"""
    if not isinstance(raw, list):
        return None
    citations = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"].strip():
            continue
        regions = [region for region in map(_citation_region, entry.get("regions") or []) if region]
        match = entry.get("match")
        if not regions or match not in _LOCATED_MATCHES:
            citations.append(Citation(text=entry["text"].strip(), match="none", regions=[]))
        else:
            citations.append(Citation(text=entry["text"].strip(), match=match, regions=regions))
    return citations


class ItemEvaluation(BaseModel):
    """条目评价详情"""
    item_id: str
    domain: int
    title: str
    score: Optional[int]
    step1_locate: Optional[str]
    step2_evidence: Optional[List[str]]
    # TODO [COMPAT] step3_reasoning 支持新旧两种格式，后续清理
    step3_reasoning: Optional[Any] = None  # str | StructuredReasoning
    evaluated_at: Optional[str]
    scoring_principle: Optional[List[ScoringPrincipleItem]] = None  # 评分准则
    evidence_sources: Optional[List[str]] = None  # 喂给模型阅读的全部 chunk_id，不是引用列表
    citations: Optional[List[Citation]] = None  # 逐句定位的引用；旧结果为 None
    rule_engine: Optional[Dict[str, Any]] = None  # 规则引擎输出详情


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
    status: str = "complete"
    missing_items: List[str] = []
    assessment_limitations: List[str] = []


def get_output_dir() -> Path:
    """获取 output 目录路径"""
    return get_settings().app.path_output


def parse_report(report_path: Path, *, strict: bool = False) -> Optional[dict]:
    """解析报告 JSON 文件"""
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        if strict:
            raise
        logger.error("解析报告失败: %s\n%s", report_path, sanitize_diagnostic(exc, limit=None))
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
                evaluation = json.load(f)
            if validate_evaluation(evaluation, eval_file.stem):
                evaluations.append(evaluation)
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
        
        if quality_score is None and data.get("status") != "partial":
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
        if data.get("status") == "partial":
            quality_score = None
            recommendation = None
        
        reports.append(ReportSummary(
            task_id=data.get("task_id", item.name),
            title=data.get("guideline_title", t("report.untitled"))[:50],
            date=data.get("evaluation_date", ""),
            quality_score=quality_score,
            recommendation=recommendation,
            status=data.get("status", "complete"),
            missing_items=data.get("missing_items", []),
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
        raise LocalizedHTTPException(404, "error.report_not_found", id=task_id)
    
    try:
        data = parse_report(report_path, strict=True)
    except Exception as exc:
        raise LocalizedHTTPException(500, "error.report_parse_failed", diagnostic=exc) from exc
    if not data:
        raise LocalizedHTTPException(500, "error.report_parse_failed")
    
    # 转换领域评分 - 兼容两种格式
    # 新格式: {"1": {"name": "...", "score": 66.7, ...}}
    # 旧格式: {"1": 66.7}
    domain_scores = {}
    lang = request_language.get()
    domain_names = {
        str(number): names[0 if lang == "en" else 1]
        for number, names in enumerate([
            ("Scope and Purpose", "范围与目的"),
            ("Stakeholder Involvement", "参与人员"),
            ("Rigour of Development", "制定的严谨性"),
            ("Clarity of Presentation", "表达的清晰性"),
            ("Applicability", "应用性"),
            ("Editorial Independence", "编辑独立性"),
        ], start=1)
    }
    for key, domain in data.get("domain_scores", {}).items():
        if isinstance(domain, dict):
            # 新格式
            item_count = domain.get("item_count", 0)
            evaluated_count = domain.get("evaluated_count", 0)
            domain_scores[key] = DomainScore(
                name=domain_names.get(key, domain.get("name", key)),
                score=None if item_count and evaluated_count < item_count else domain.get("score"),
                item_count=item_count,
                evaluated_count=evaluated_count,
            )
        else:
            # 旧格式 - domain 直接是分数
            domain_scores[key] = DomainScore(
                name=domain_names.get(key, key),
                score=domain if isinstance(domain, (int, float)) else None,
                item_count=0,
                evaluated_count=0
            )
    
    # 从 evaluations 目录加载条目评价详情
    evaluations = load_evaluations(task_dir)
    item_evaluations = []
    
    # 延迟导入 skill 加载器
    from core.skills import load_skill_config
    
    for eval_data in evaluations:
        cot = eval_data.get("chain_of_thought", {})
        metadata = eval_data.get("metadata", {})
        item_id = eval_data.get("item_id", "")
        
        # 加载该 item 的评分准则
        scoring_principle = None
        display_title = eval_data.get("title", "")
        try:
            skill_config = load_skill_config(item_id, check_enabled=False, lang=lang)
            display_title = skill_config.get("title") or display_title
            raw_principles = skill_config.get("scoring_principle", [])
            scoring_principle = [
                ScoringPrincipleItem(
                    range=str(sp.get("range", "")),
                    condition=(sp.get("condition_zh") or sp.get("condition", "")) if lang == "zh" else sp.get("condition", "")
                )
                for sp in raw_principles
            ]
        except (FileNotFoundError, ValueError, KeyError):
            logger.warning("Standard display fields unavailable for %s", item_id, exc_info=True)
        
        item_evaluations.append(ItemEvaluation(
            item_id=item_id,
            domain=eval_data.get("domain", 0),
            title=display_title,
            score=cot.get("score"),
            step1_locate=cot.get("step1_locate"),
            step2_evidence=cot.get("step2_evidence"),
            step3_reasoning=cot.get("step3_reasoning"),  # 兼容新旧格式
            evaluated_at=metadata.get("evaluated_at"),
            scoring_principle=scoring_principle,
            evidence_sources=eval_data.get("evidence_sources"),
            citations=parse_citations(eval_data.get("citations")),
            rule_engine=eval_data.get("rule_engine"),
        ))
    
    partial = data.get("status") == "partial"
    overall_assessment = dict(data.get("overall_assessment", {}))
    if partial:
        overall_assessment["recommendation"] = None

    return ReportDetail(
        task_id=data.get("task_id", task_id),
        guideline_title=data.get("guideline_title", ""),
        evaluation_date=data.get("evaluation_date", ""),
        evaluator=data.get("evaluator", ""),
        domain_scores=domain_scores,
        overall_assessment=overall_assessment,
        item_evaluations=item_evaluations,
        total_raw_score=None if partial else data.get("total_raw_score"),
        overall_quality_score=None if partial else data.get("overall_quality_score"),
        status=data.get("status", "complete"),
        missing_items=data.get("missing_items", []),
        assessment_limitations=data.get("assessment_limitations", []),
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
        raise LocalizedHTTPException(404, "error.evidence_not_found", id=task_id)
    
    try:
        with open(visual_evidence_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise LocalizedHTTPException(500, "error.read_failed", diagnostic=e)


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
        raise LocalizedHTTPException(404, "error.pdf_not_found", path=f"{base_name}.pdf")
    
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=pdf_filename
    )


class EvidenceLocationRequest(BaseModel):
    """证据定位请求"""
    evidence_texts: Optional[List[str]] = None  # 证据文本列表（备选）
    chunk_ids: Optional[List[str]] = None  # 优先使用 chunk_id 匹配


class EvidenceMatch(BaseModel):
    """证据匹配结果"""
    page_idx: int
    bbox: List[float]  # [x1, y1, x2, y2] 归一化坐标
    text_preview: str
    chunk_id: Optional[str] = None


class EvidenceLocationResponse(BaseModel):
    """证据定位响应"""
    matches: List[EvidenceMatch]
    total: int


@router.post("/reports/{task_id}/evidence_location")
async def get_evidence_location(
    task_id: str, 
    request: EvidenceLocationRequest,
    folder: Optional[str] = None
):
    """
    根据 chunk_id 或证据文本查找 PDF 中的位置
    
    优先使用 chunk_ids 进行精确匹配，若未提供则回退到文本匹配
    """
    output_dir = get_output_dir()
    
    # 确定任务目录
    if folder:
        task_dir = output_dir / folder / task_id
    else:
        task_dir = output_dir / task_id
        if not task_dir.exists():
            for subdir in output_dir.iterdir():
                if subdir.is_dir() and subdir.name.startswith("batch_"):
                    candidate = subdir / task_id
                    if candidate.exists():
                        task_dir = candidate
                        break
    
    # 加载 visual_evidence.json
    prep_dir = task_dir / ".ama" / "prep"
    base_name = task_id.rsplit("-", 1)[0]
    visual_evidence_path = prep_dir / f"{base_name}_visual_evidence.json"
    
    if not visual_evidence_path.exists():
        # 返回空结果而不是 404
        return EvidenceLocationResponse(matches=[], total=0)
    
    try:
        with open(visual_evidence_path, "r", encoding="utf-8") as f:
            visual_data = json.load(f)
    except Exception as e:
        raise LocalizedHTTPException(500, "error.read_failed", diagnostic=e)
    
    matches = []
    
    # 优先使用 chunk_ids 匹配
    if request.chunk_ids:
        chunk_set = set(request.chunk_ids)
        for page in visual_data.get("pages", []):
            for region in page.get("regions", []):
                if region.get("chunk_id") in chunk_set:
                    matches.append(EvidenceMatch(
                        page_idx=page.get("page_idx", 0),
                        bbox=region.get("bbox", [0, 0, 1, 1]),
                        text_preview=region.get("text_preview", "")[:100],
                        chunk_id=region.get("chunk_id")
                    ))
    
    # 若 chunk_ids 无匹配，回退到文本匹配
    elif request.evidence_texts:
        for evidence_text in request.evidence_texts:
            if not evidence_text:
                continue
            search_text = evidence_text[:50].lower()
            for page in visual_data.get("pages", []):
                for region in page.get("regions", []):
                    text_preview = region.get("text_preview", "").lower()
                    if search_text in text_preview or text_preview in search_text:
                        matches.append(EvidenceMatch(
                            page_idx=page.get("page_idx", 0),
                            bbox=region.get("bbox", [0, 0, 1, 1]),
                            text_preview=region.get("text_preview", "")[:100],
                            chunk_id=region.get("chunk_id")
                        ))
    
    return EvidenceLocationResponse(
        matches=matches,
        total=len(matches)
    )
