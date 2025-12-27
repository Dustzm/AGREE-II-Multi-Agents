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
    if lang is None:
        lang = get_ama_settings().prompt_lang
    
    # 加载配置（lang 参数控制返回字段语言）
    skill_config = load_skill_config(skill_id, check_enabled=True, lang=lang)
    
    # 加载模板
    template = load_prompt("appraiser", lang)
    
    # 格式化 criteria 列表
    criteria_list = "\n".join(f"- {c}" for c in skill_config.get("criteria", []))
    
    # 格式化评分表格
    scoring_rows = []
    for sp in skill_config.get("scoring_principle", []):
        # 根据语言选择对应字段
        if lang == "zh":
            condition = sp.get("condition_zh", sp.get("condition", ""))
        else:
            condition = sp.get("condition", sp.get("condition_zh", ""))
        scoring_rows.append(f"| {sp['range']} | {condition} |")
    scoring_table = "\n".join(scoring_rows)
    
    rendered_prompt = template.format(
        item_id=skill_config.get("item_id", ""),
        domain_name=skill_config.get("domain_name", ""),
        title=skill_config.get("title", ""),
        description=skill_config.get("description", ""),
        locating_info=skill_config.get("locating_info", ""),
        criteria_list=criteria_list,
        scoring_table=scoring_table,
    )
    
    # 输出渲染后的提示词日志
    logger.info(f"=== Appraiser 提示词已渲染 [{skill_id}] ===")
    logger.info(f"提示词长度: {len(rendered_prompt)} 字符")
    # 输出前1000字符预览
    preview = rendered_prompt[:1000] + "..." if len(rendered_prompt) > 1000 else rendered_prompt
    logger.info(f"提示词预览:\n{preview}")
    
    return rendered_prompt


