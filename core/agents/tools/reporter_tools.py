"""
Reporter 智能体工具函数

提供 AGREE-II 评价报告生成相关的工具函数：
- 加载所有评价结果
- 计算维度分数
- 生成评价报告
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .public_tools import get_task_paths
from .appraiser_tools import list_evaluations, read_evaluation
from core.skills import AGREE_DOMAINS

logger = logging.getLogger(__name__)


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
    try:
        # 获取配置语言
        from core.config import get_ama_settings
        lang = get_ama_settings().skill_lang
        
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
            if evaluated_count > 0:
                max_possible = 7 * evaluated_count
                min_possible = 1 * evaluated_count
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
        return {
            "status": "success",
            "domain_scores": domain_scores,
            "evaluations": all_evaluations,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "domain_scores": {},
            "evaluations": [],
            "error": str(e)
        }


def generate_appraisal_report(
    task_id: str,
    guideline_title: str,
    domain_scores: dict,
    overall_strengths: list[str],
    overall_weaknesses: list[str],
    recommendation: str
) -> dict:
    """
    生成并保存最终评价报告
    
    Args:
        task_id: 任务 ID
        guideline_title: 指南标题
        domain_scores: 维度分数字典
        overall_strengths: 整体优势列表
        overall_weaknesses: 整体不足列表
        recommendation: 推荐意见 (推荐使用/修改后推荐使用/不推荐使用)
    
    Returns:
        dict: {"status": "success/error", "path": str, "markdown_path": str, "error": str}
    """
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
        evaluations_result = list_evaluations(task_id)
        total_items_evaluated = 0
        if evaluations_result["status"] == "success":
            total_items_evaluated = len(evaluations_result["evaluations"])
        
        # 构建报告
        report = {
            "guideline_title": guideline_title,
            "evaluator": "AMA (AGREE-Multi-Agents)",
            "evaluation_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "task_id": task_id,
            
            "domain_scores": domain_scores,
            
            "overall_assessment": {
                "strengths": overall_strengths,
                "weaknesses": overall_weaknesses,
                "recommendation": recommendation
            },
            
            "metadata": {
                "generated_at": datetime.now().isoformat(),
                "total_items_evaluated": total_items_evaluated
            }
        }
        
        # 保存 JSON 报告
        json_path = report_dir / "appraisal_report.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        # 同时生成 Markdown 报告
        md_path = report_dir / "appraisal_report.md"
        md_content = _generate_markdown_report(report)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        
        logger.info(f"评价报告已保存: {json_path}")
        return {
            "status": "success",
            "path": str(json_path),
            "markdown_path": str(md_path),
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "path": "",
            "error": str(e)
        }


def _generate_markdown_report(report: dict) -> str:
    """生成 Markdown 格式的评价报告（支持双语）"""
    from core.config import get_ama_settings
    lang = get_ama_settings().skill_lang
    
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
            "recommendation": "### 推荐意见",
        }
    }
    
    t = templates.get(lang, templates["en"])
    
    lines = [
        t["title"],
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
        else:
            score_str = t["not_evaluated"]
        lines.append(f"| {domain.get('name', f'Domain {domain_id}')} | {score_str} |")
    
    lines.extend([
        "",
        "---",
        "",
        t["overall"],
        "",
        t["strengths"],
    ])
    
    for strength in report["overall_assessment"]["strengths"]:
        lines.append(f"- {strength}")
    
    lines.extend([
        "",
        t["weaknesses"],
    ])
    
    for weakness in report["overall_assessment"]["weaknesses"]:
        lines.append(f"- {weakness}")
    
    lines.extend([
        "",
        t["recommendation"],
        f"**{report['overall_assessment']['recommendation']}**",
    ])
    
    return "\n".join(lines)

