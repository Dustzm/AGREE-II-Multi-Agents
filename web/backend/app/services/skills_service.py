import logging

import yaml
from typing import List, Dict, Any

from web.backend.app.schemas.skills import SkillUpdateDetails
from core.skills.loader import list_available_skills, _load_skill_config_raw, SKILLS_DIR
from web.backend.app.i18n import get_language

logger = logging.getLogger(__name__)

def _format_skill_response(config: dict, skill_id: str, lang: str = "en") -> dict:
    """
    Format the raw skill config for frontend display.
    
    Args:
        config: 原始配置
        skill_id: 技能 ID
        lang: 语言代码 (zh/en)，默认英语
    """
    lang = get_language(lang)
    
    # 根据语言选择显示字段
    if lang == "zh":
        title = config.get("title_zh", config.get("title", ""))
        description = config.get("description_zh", config.get("description", ""))
        locating_info = config.get("locating_info_zh", config.get("locating_info", ""))
        domain_name = config.get("domain_name_zh", config.get("domain_name", ""))
    else:
        # 英语优先
        title = config.get("title", config.get("title_zh", ""))
        description = config.get("description", config.get("description_zh", ""))
        locating_info = config.get("locating_info", config.get("locating_info_zh", ""))
        domain_name = config.get("domain_name", config.get("domain_name_zh", ""))
    
    return {
        "item_id": skill_id,
        "enabled": config.get("enabled", False),
        "domain": config.get("domain", 0),
        "domain_name": domain_name,
        # 显示字段（根据语言）
        "title": title,
        "description": description,
        "locating_info": locating_info,
        # 中文字段（用于编辑）
        "title_zh": config.get("title_zh", ""),
        "description_zh": config.get("description_zh", ""),
        "locating_info_zh": config.get("locating_info_zh", ""),
        # 英文字段（用于编辑）
        "title_en": config.get("title", ""),
        "description_en": config.get("description", ""),
        "locating_info_en": config.get("locating_info", ""),
        # 原始 criteria/scoring
        "criteria": config.get("criteria", []),
        "scoring_principle": config.get("scoring_principle", [])
    }

def get_all_skills(lang: str = "en") -> List[Dict[str, Any]]:
    """
    Get all available skills with their configuration.
    
    Args:
        lang: 语言代码 (zh/en)，默认英语
    """
    skills_list = []
    skill_ids = list_available_skills(enabled_only=False)
    
    for skill_id in skill_ids:
        try:
            config = _load_skill_config_raw(skill_id)
            skills_list.append(_format_skill_response(config, skill_id, lang))
        except Exception:
            logger.exception("Error loading skill %s", skill_id)
            raise
            
    return skills_list

def update_skill_status(skill_id: str, enabled: bool, lang: str = "en") -> Dict[str, Any]:
    """
    Update the enabled status of a skill in its YAML file.
    """
    config_path = SKILLS_DIR / f"{skill_id}.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Skill configuration not found: {skill_id}")
        
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        
    config["enabled"] = enabled
    
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)
        
    # Reload and format (fixes the language bug)
    updated_config = _load_skill_config_raw(skill_id)
    return _format_skill_response(updated_config, skill_id, lang)

def update_skill_details(skill_id: str, details: SkillUpdateDetails, lang: str = "en") -> Dict[str, Any]:
    """
    Update skill details (title_zh, description_zh, etc.)
    """
    config_path = SKILLS_DIR / f"{skill_id}.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Skill configuration not found: {skill_id}")
        
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # Update localized fields
    config["title_zh"] = details.title_zh
    config["description_zh"] = details.description_zh
    config["locating_info_zh"] = details.locating_info_zh
    
    # Update English fields (mapped from detail.{field} to config[{field}])
    # Note: details.title corresponds to English title in schema
    config["title"] = details.title
    config["description"] = details.description
    config["locating_info"] = details.locating_info
    
    config["criteria"] = details.criteria
    config["scoring_principle"] = details.scoring_principle
    
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)
        
    updated_config = _load_skill_config_raw(skill_id)
    return _format_skill_response(updated_config, skill_id, lang)


def update_all_skills_status(enabled: bool, lang: str = "en") -> List[Dict[str, Any]]:
    """
    批量更新所有 skills 的启用状态
    
    Args:
        enabled: True 全部启用，False 全部禁用
    
    Returns:
        更新后的所有 skills 列表
    """
    skill_ids = list_available_skills(enabled_only=False)
    updated_skills = []
    
    for skill_id in skill_ids:
        try:
            updated = update_skill_status(skill_id, enabled, lang)
            updated_skills.append(updated)
        except Exception:
            logger.exception("Error updating skill %s", skill_id)
            raise
    
    return updated_skills

