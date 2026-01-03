"""
提示词渲染器

将 Skill 配置动态注入提示词模板
"""
import logging
from typing import Optional
from .loader import load_prompt
from core.skills import load_skill_config

logger = logging.getLogger(__name__)


def render_appraiser_prompt(skill_id: str, lang: Optional[str] = None) -> str:
    """
    渲染 Appraiser 提示词，将 Skill 配置注入模板
    
    Args:
        skill_id: 条目 ID（如 "item_1"）
        lang: 语言 (zh/en)，默认从配置读取
    
    Returns:
        str: 渲染后的完整提示词
    """
    from core.config import get_ama_settings
    from core.prompts.iterative_prompts import (
        EVIDENCE_EXTRACTION_PROMPT,
        DYNAMIC_SCORING_PROMPT,
        FINAL_REPORT_PROMPT
    )
    
    if lang is None:
        lang = get_ama_settings().prompt_lang
    
    # 加载配置
    skill_config = load_skill_config(skill_id, check_enabled=True, lang=lang)
    
    # 格式化 criteria 列表
    criteria_list = "\n".join(f"- {c}" for c in skill_config.get("criteria", []))
    
    # 格式化评分表格
    scoring_rows = []
    for sp in skill_config.get("scoring_principle", []):
        if lang == "zh":
            condition = sp.get("condition_zh", sp.get("condition", ""))
        else:
            condition = sp.get("condition", sp.get("condition_zh", ""))
        scoring_rows.append(f"| {sp['range']} | {condition} |")
    scoring_table = "\n".join(scoring_rows)
    
    # 1. 渲染提取提示词 (带占位符)
    extraction = EVIDENCE_EXTRACTION_PROMPT.format(
        item_id=skill_config.get("item_id", ""),
        title=skill_config.get("title", ""),
        criteria_list=criteria_list,
        chunk_content="[待分析的文档片段 / Document Chunk Content...]"
    )
    
    # 2. 渲染评分提示词 (带占位符)
    scoring = DYNAMIC_SCORING_PROMPT.format(
        item_id=skill_config.get("item_id", ""),
        title=skill_config.get("title", ""),
        evidence_cache="[累积的证据列表 / Accumulated Evidence...]",
        scoring_table=scoring_table,
        n_sources=123
    )
    
    # 3. 渲染报告提示词
    report = FINAL_REPORT_PROMPT.format(
        item_id=skill_config.get("item_id", ""),
        domain_name=skill_config.get("domain_name", ""),
        title=skill_config.get("title", ""),
        criteria_list=criteria_list,
        scoring_table=scoring_table,
        evidence_cache="[最终证据列表 / Final Evidence...]",
        evidence_sources="[Source1, Source2...]",
        final_score=7
    )
    
    # 组合展示
    rendered_prompt = f"""# AGREE-II Iterative Evaluation Logic

## Phase 1: Evidence Extraction (Per Chunk)
{extraction}

---

## Phase 2: Dynamic Scoring (Per Iteration)
{scoring}

---

## Phase 3: Final Report
{report}
"""
    
    return rendered_prompt


