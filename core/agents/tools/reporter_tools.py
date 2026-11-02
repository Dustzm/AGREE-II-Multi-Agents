"""
Reporter 智能体工具函数

提供 AGREE-II 评价报告生成相关的工具函数：
- 加载所有评价结果
- 计算维度分数
- 生成评价报告
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .public_tools import get_task_paths
from .appraiser_tools import read_evaluation
from core.skills import AGREE_DOMAINS

logger = logging.getLogger(__name__)

REPORT_LANGUAGE = "en"


def get_guideline_title(task_id: str) -> dict:
    """
    获取指南标题
    
    从 docindex.json 中读取 cluster_level == 1 的标题作为指南标题
    
    Args:
        task_id: 任务 ID
    
    Returns:
        dict: {"status": "success/error", "title": str, "error": str}
    """
    logger.info(f"[Reporter] get_guideline_title 被调用, task_id={task_id}")
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "title": "",
                "error": f"任务不存在: {task_id}"
            }
        
        prep_dir = Path(paths["prep_dir"])
        
        # 查找 docindex.json 文件
        docindex_files = list(prep_dir.glob("*_docindex.json"))
        if not docindex_files:
            return {
                "status": "error",
                "title": "",
                "error": "未找到 docindex.json 文件"
            }
        
        docindex_path = docindex_files[0]
        with open(docindex_path, "r", encoding="utf-8") as f:
            docindex = json.load(f)
        
        # 查找 cluster_level == 1 的标题（文档最大标题）
        title_list = docindex.get("title_list", [])
        for item in title_list:
            if item.get("cluster_level") == 1:
                title = item.get("title", "")
                logger.info(f"获取指南标题: {title}")
                return {
                    "status": "success",
                    "title": title,
                    "error": ""
                }
        
        # 如果没有 cluster_level == 1，取第一个标题
        if title_list:
            title = title_list[0].get("title", "")
            logger.warning(f"未找到 cluster_level=1 的标题，使用第一个标题: {title}")
            return {
                "status": "success",
                "title": title,
                "error": ""
            }
        
        return {
            "status": "error",
            "title": "",
            "error": "docindex.json 中没有标题"
        }
    except Exception as e:
        logger.exception("指南标题读取异常: task=%s", task_id)
        return {
            "status": "error",
            "title": "",
            "error": str(e)
        }


# 各维度包含的条目
DOMAIN_ITEMS = {
    1: ["item_1", "item_2", "item_3"],
    2: ["item_4", "item_5", "item_6"],
    3: ["item_7", "item_8", "item_9", "item_10", "item_11", "item_12", "item_13", "item_14"],
    4: ["item_15", "item_16", "item_17"],
    5: ["item_18", "item_19", "item_20", "item_21"],
    6: ["item_22", "item_23"],
}


def load_all_evaluations(task_id: str) -> dict:
    """
    加载所有评价结果并计算维度分数
    
    Args:
        task_id: 任务 ID
    
    Returns:
        dict: {
            "status": "success/error",
            "domain_scores": dict,  # 维度分数
            "evaluations": list,  # 所有评价结果
            "error": str
        }
    """
    logger.info(f"[Reporter] load_all_evaluations 被调用, task_id={task_id}")
    try:
        lang = REPORT_LANGUAGE
        
        domain_scores = {}
        all_evaluations = []
        
        for domain_id, item_ids in DOMAIN_ITEMS.items():
            domain_info = AGREE_DOMAINS.get(domain_id, {})
            if lang == "zh":
                domain_name = domain_info.get("name_zh", f"Domain {domain_id}")
            else:
                domain_name = domain_info.get("name", f"Domain {domain_id}")
            
            # 收集该维度的条目评分
            items = []
            total_score = 0
            evaluated_count = 0
            
            for item_id in item_ids:
                result = read_evaluation(task_id, item_id)
                if result["status"] == "success":
                    evaluation = result["evaluation"]
                    score = evaluation.get("chain_of_thought", {}).get("score", 0)
                    items.append({
                        "item_id": item_id,
                        "score": score,
                        "title": evaluation.get("title", item_id)
                    })
                    total_score += score
                    evaluated_count += 1
                    all_evaluations.append(evaluation)
                else:
                    items.append({
                        "item_id": item_id,
                        "score": None,
                        "title": item_id
                    })
            
            # 计算标准化分数
            if evaluated_count == len(item_ids):
                max_possible = 7 * len(item_ids)
                min_possible = len(item_ids)
                standardized_score = ((total_score - min_possible) / (max_possible - min_possible)) * 100
            else:
                standardized_score = None
            
            domain_scores[domain_id] = {
                "name": domain_name,
                "score": round(standardized_score, 1) if standardized_score is not None else None,
                "items": items,
                "item_count": len(item_ids),
                "evaluated_count": evaluated_count,
                "total_raw_score": total_score
            }
        
        logger.info(f"加载评价结果: {len(all_evaluations)} 个条目")
        
        # compact evaluations 数据，只保留 Reporter 需要的字段
        slim_evaluations = []
        for ev in all_evaluations:
            cot = ev.get("chain_of_thought", {})
            reasoning = cot.get("step3_reasoning", "")
            # 如果 reasoning 是 dict，提取 score_justification
            if isinstance(reasoning, dict):
                reasoning_text = reasoning.get("score_justification", str(reasoning))
            else:
                reasoning_text = str(reasoning)
            # 截断过长的 reasoning
            if len(reasoning_text) > 500:
                reasoning_text = reasoning_text[:500] + "..."
            
            slim_evaluations.append({
                "skill_id": ev.get("item_id", ev.get("skill_id", "")),
                "title": ev.get("title", ""),
                "domain": ev.get("domain", ""),
                "score": cot.get("score", 0),
                "reasoning_summary": reasoning_text,
                "degraded": bool((ev.get("iteration_stats") or {}).get("report_degraded")),
            })
        
        return {
            "status": "success",
            "domain_scores": domain_scores,
            "evaluations": slim_evaluations,
            "error": ""
        }
    except Exception as e:
        logger.exception("条目结果汇总异常: task=%s", task_id)
        return {
            "status": "error",
            "domain_scores": {},
            "evaluations": [],
            "error": str(e)
        }


def generate_appraisal_report(
    task_id: str,
    overall_strengths: list[str],
    overall_weaknesses: list[str],
) -> dict:
    """
    生成并保存最终评价报告
    
    Args:
        task_id: 任务 ID
        overall_strengths: 领域级优势分析段落列表
        overall_weaknesses: 领域级不足分析段落列表
    
    Returns:
        dict: {"status": "success/error", "path": str, "markdown_path": str, "error": str}
    """
    logger.info("[Reporter] generate_appraisal_report 被调用, task_id=%s", task_id)
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "path": "",
                "error": f"任务不存在: {task_id}"
            }
        
        # 直接保存在 task 目录下（与 source.md 同级）
        report_dir = Path(paths["task_dir"])
        report_dir.mkdir(parents=True, exist_ok=True)
        
        # 计算已评价条目数
        evaluations_result = load_all_evaluations(task_id)
        if evaluations_result["status"] != "success":
            raise RuntimeError(evaluations_result["error"])
        domain_scores = evaluations_result["domain_scores"]
        total_items_evaluated = len(evaluations_result["evaluations"])
        if total_items_evaluated == 0:
            raise ValueError("没有有效条目结果，无法生成报告")
        completed_items = {evaluation["skill_id"] for evaluation in evaluations_result["evaluations"]}
        missing_items = [item_id for items in DOMAIN_ITEMS.values() for item_id in items if item_id not in completed_items]
        partial = bool(missing_items)
        strengths = _normalize_narratives(overall_strengths)
        weaknesses = _normalize_narratives(overall_weaknesses)
        title_result = get_guideline_title(task_id)
        guideline_title = title_result["title"] if title_result["status"] == "success" else task_id
        if title_result["status"] != "success":
            logger.warning("指南标题读取失败，报告使用任务 ID: task=%s error=%s", task_id, title_result.get("error"))
        
        # 计算总分（所有条目原始分数总和，从各条目评价中直接获取）
        total_raw_score = sum(
            evaluation["score"]
            for evaluation in evaluations_result["evaluations"]
            if isinstance(evaluation.get("score"), (int, float))
        )
        
        # 计算总体质量评分（1-7，基于总分）
        if partial:
            overall_quality_score = None
        elif total_raw_score <= 23:
            overall_quality_score = 1
        elif total_raw_score <= 46:
            overall_quality_score = 2
        elif total_raw_score <= 69:
            overall_quality_score = 3
        elif total_raw_score <= 92:
            overall_quality_score = 4
        elif total_raw_score <= 115:
            overall_quality_score = 5
        elif total_raw_score <= 138:
            overall_quality_score = 6
        else:
            overall_quality_score = 7
        
        # 重新计算推荐意见（基于领域三严谨性和总分）
        domain_3_score = domain_scores.get(3, {}).get("score")
        
        if partial:
            recommendation = None
        elif domain_3_score is not None and domain_3_score < 30:
            # 领域三严谨性 < 30%，直接不推荐
            recommendation = "Do not recommend"
        else:
            # 否则基于总分判断
            if total_raw_score > 96:
                recommendation = "Recommend"
            elif total_raw_score > 88:
                recommendation = "Recommend with modifications"
            else:
                recommendation = "Do not recommend"
        
        # 构建报告
        report = {
            "guideline_title": guideline_title,
            "evaluator": "AMA (AGREE-Multi-Agents)",
            "evaluation_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task_id": task_id,
            "status": "partial" if partial else "complete",
            "missing_items": missing_items,
            
            "domain_scores": domain_scores,
            
            "overall_assessment": {
                "strengths": strengths,
                "weaknesses": weaknesses,
                "recommendation": recommendation
            },
            
            # 添加总分和质量评分
            "total_raw_score": None if partial else total_raw_score,
            "overall_quality_score": overall_quality_score,
            
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_items_evaluated": total_items_evaluated,
                "total_items_expected": 23,
            }
        }
        if partial:
            limitation = (
                f"Only {total_items_evaluated}/23 items were completed; missing: {', '.join(missing_items)}"
                if REPORT_LANGUAGE == "en" else
                f"仅完成 {total_items_evaluated}/23 项；缺失条目: {', '.join(missing_items)}"
            )
            report["assessment_limitations"] = [limitation]
        
        # 保存 JSON 报告
        json_path = report_dir / "appraisal_report.json"
        # 同时生成 Markdown 报告
        md_path = report_dir / "appraisal_report.md"
        md_content = _generate_markdown_report(report)
        md_temp = md_path.with_suffix(".md.tmp")
        json_temp = json_path.with_suffix(".json.tmp")
        try:
            md_temp.write_text(md_content, encoding="utf-8")
            json_temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            md_temp.replace(md_path)
            json_temp.replace(json_path)
        finally:
            md_temp.unlink(missing_ok=True)
            json_temp.unlink(missing_ok=True)
        
        logger.info(f"评价报告已保存: {json_path}")
        return {
            "status": "success",
            "path": str(json_path),
            "markdown_path": str(md_path),
            "error": ""
        }
    except Exception:
        logger.exception("Reporter 报告生成失败: task=%s", task_id)
        raise


def _normalize_narratives(items: object) -> list[str]:
    """保留 Reporter 自由撰写的领域级分析，只去除空白项。"""
    if not isinstance(items, list):
        logger.warning("Reporter 分析内容不是列表，按空处理")
        return []
    return [text.strip() for text in items if isinstance(text, str) and text.strip()]


_MD_BLOCK_PREFIX = re.compile(r"^(#{1,6}\s|[-*+]\s|>\s?|\d+[.)]\s)")


def _escape_markdown_lines(text: str) -> str:
    """Keep free-form narrative as plain paragraphs instead of injected markdown blocks."""
    lines = []
    for line in str(text).splitlines():
        if _MD_BLOCK_PREFIX.match(line):
            lines.append("\\" + line)
        else:
            lines.append(line)
    return "\n".join(lines)


def _generate_markdown_report(report: dict) -> str:
    """生成 Markdown 格式的评价报告（固定英文模板）。"""
    lang = REPORT_LANGUAGE
    is_partial = report.get("status") == "partial"
    
    # 双语模板
    templates = {
        "en": {
            "title": "# AGREE-II Guideline Quality Appraisal Report",
            "info": "## Guideline Information",
            "label_title": "Title",
            "label_tool": "Appraisal Tool",
            "label_date": "Appraisal Date",
            "domain_overview": "## Domain Scores Overview",
            "domain_header": "| Domain | Score |",
            "not_evaluated": "Not evaluated",
            "overall": "## Overall Assessment",
            "strengths": "### Strengths",
            "weaknesses": "### Weaknesses",
            "partial_strengths": "### Observed strengths in completed items",
            "partial_weaknesses": "### Observed weaknesses in completed items",
            "limitations": "### Appraisal limitations",
            "pending": "Pending",
            "partial_notice": "**Partial report — overall score and recommendation pending.**",
            "coverage": "Completed items: {completed}/23; missing: {missing}",
            "recommendation_pending": "Pending remaining items",
            "recommendation": "### Recommendation",
        },
        "zh": {
            "title": "# AGREE-II 指南质量评价报告",
            "info": "## 指南信息",
            "label_title": "标题",
            "label_tool": "评价工具",
            "label_date": "评价日期",
            "domain_overview": "## 维度分数概览",
            "domain_header": "| 维度 | 分数 |",
            "not_evaluated": "未评价",
            "overall": "## 总体评估",
            "strengths": "### 优势",
            "weaknesses": "### 不足",
            "partial_strengths": "### 已完成条目的优势观察",
            "partial_weaknesses": "### 已完成条目的不足观察",
            "limitations": "### 评价局限",
            "pending": "待补齐",
            "partial_notice": "**部分报告：总分与推荐意见待补齐。**",
            "coverage": "已完成条目：{completed}/23；缺失：{missing}",
            "recommendation_pending": "待补齐剩余条目",
            "recommendation": "### 推荐意见",
        }
    }
    
    t = templates.get(lang, templates["en"])
    
    lines = [t["title"]]
    if is_partial:
        lines.extend([
            t["partial_notice"],
            t["coverage"].format(
                completed=report.get("metadata", {}).get("total_items_evaluated", 0),
                missing=", ".join(report.get("missing_items", [])),
            ),
        ])
    lines += [
        "",
        t["info"],
        f"- **{t['label_title']}**: {report['guideline_title']}",
        f"- **{t['label_tool']}**: {report['evaluator']}",
        f"- **{t['label_date']}**: {report['evaluation_date']}",
        "",
        "---",
        "",
        t["domain_overview"],
        "",
        t["domain_header"],
        "|------|------|",
    ]
    
    for domain_id in sorted(report["domain_scores"].keys(), key=lambda x: int(x) if isinstance(x, str) else x):
        domain = report["domain_scores"][domain_id]
        score = domain.get('score')
        if score is not None:
            score_str = f"{score:.1f}%" if isinstance(score, float) else f"{score}%"
        elif domain.get("evaluated_count", 0):
            score_str = f"{t['pending']} ({domain['evaluated_count']}/{domain['item_count']})"
        else:
            score_str = t["not_evaluated"]
        lines.append(f"| {domain.get('name', f'Domain {domain_id}')} | {score_str} |")
    
    lines.extend([
        "",
        "---",
        "",
        t["overall"],
        "",
        t["partial_strengths"] if is_partial else t["strengths"],
    ])
    
    for strength in report["overall_assessment"]["strengths"]:
        lines.extend([_escape_markdown_lines(strength), ""])
    
    lines.extend([
        "",
        t["partial_weaknesses"] if is_partial else t["weaknesses"],
    ])
    
    for weakness in report["overall_assessment"]["weaknesses"]:
        lines.extend([_escape_markdown_lines(weakness), ""])

    if is_partial:
        lines.extend(["", t["limitations"]])
        for limitation in report.get("assessment_limitations", []):
            lines.append(f"- {limitation}")
    
    lines.extend([
        "",
        t["recommendation"],
        f"**{report['overall_assessment']['recommendation'] or t['recommendation_pending']}**",
    ])
    
    return "\n".join(lines)
