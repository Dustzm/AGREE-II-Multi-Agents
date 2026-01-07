"""
Skill 配置加载器

加载 AGREE-II 评价条目的 YAML 配置文件
支持启用/禁用控制
"""
import os
from pathlib import Path
from typing import Optional

import yaml


# Skill 配置目录路径 (当前目录)
SKILLS_DIR = Path(__file__).parent


def _load_skill_config_raw(skill_id: str) -> dict:
    """
    加载指定条目的评价配置（不检查启用状态）
    
    内部函数，用于检查 enabled 状态
    """
    config_path = SKILLS_DIR / f"{skill_id}.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Skill 配置文件不存在: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    return config


def load_skill_config(skill_id: str, check_enabled: bool = True, lang: Optional[str] = None) -> dict:
    """
    加载指定条目的评价配置
    
    Args:
        skill_id: 条目 ID（如 "item_1", "item_2"）
        check_enabled: 是否检查启用状态，默认 True
        lang: 语言 (en/zh)，影响返回的字段名
              - en: 返回 title, description, locating_info
              - zh: 返回 title_zh, description_zh, locating_info_zh
              - None: 返回原始配置（包含所有字段）
    
    Returns:
        dict: 条目配置字典
    
    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: Skill 未启用
    """
    config = _load_skill_config_raw(skill_id)
    
    # 检查是否启用
    if check_enabled and not config.get("enabled", False):
        raise ValueError(f"Skill 未启用: {skill_id}")
    
    # 如果指定语言，转换字段名
    if lang == "zh":
        return {
            "item_id": skill_id,
            "enabled": config.get("enabled", False),
            "domain": config.get("domain", 0),
            "domain_name": config.get("domain_name_zh", config.get("domain_name", "")),
            "title": config.get("title_zh", config.get("title", "")),
            "description": config.get("description_zh", config.get("description", "")),
            "locating_info": config.get("locating_info_zh", config.get("locating_info", "")),
            "criteria": config.get("criteria", []),
            "scoring_principle": config.get("scoring_principle", []),
        }
    elif lang == "en":
        return {
            "item_id": skill_id,
            "enabled": config.get("enabled", False),
            "domain": config.get("domain", 0),
            "domain_name": config.get("domain_name", ""),
            "title": config.get("title", ""),
            "description": config.get("description", ""),
            "locating_info": config.get("locating_info", ""),
            "criteria": config.get("criteria", []),
            "scoring_principle": config.get("scoring_principle", []),
        }
    
    return config


def list_available_skills(enabled_only: bool = True) -> list[str]:
    """
    列出所有可用的 Skill ID
    
    Args:
        enabled_only: 是否只列出已启用的 Skill，默认 True
    
    Returns:
        list[str]: Skill ID 列表（如 ["item_1", "item_2", "item_3"]）
    """
    if not SKILLS_DIR.exists():
        return []
    
    skills = []
    for file in SKILLS_DIR.glob("item_*.yaml"):
        skill_id = file.stem  # 去掉 .yaml 后缀
        
        if enabled_only:
            try:
                config = _load_skill_config_raw(skill_id)
                if not config.get("enabled", False):
                    continue
            except (FileNotFoundError, yaml.YAMLError):
                continue
        
        skills.append(skill_id)
    
    # 按数字排序
    skills.sort(key=lambda x: int(x.split("_")[1]))
    return skills


def get_enabled_skills_info(lang: Optional[str] = None) -> list[dict]:
    """
    获取所有启用的 Skill 信息（用于 Initiator 生成计划）
    
    Args:
        lang: 语言 (en/zh)，默认从配置读取
    
    Returns:
        list[dict]: 每个 dict 包含 {item_id, domain, title}
    """
    # 导入配置
    from core.config import get_ama_settings
    if lang is None:
        lang = get_ama_settings().skill_lang
    
    skills_info = []
    for skill_id in list_available_skills(enabled_only=True):
        try:
            config = _load_skill_config_raw(skill_id)
            if lang == "zh":
                title = config.get("title_zh", config.get("title", skill_id))
            else:
                title = config.get("title", config.get("title_zh", skill_id))
            skills_info.append({
                "item_id": skill_id,
                "domain": config.get("domain", 0),
                "title": title
            })
        except (FileNotFoundError, yaml.YAMLError):
            continue
    
    return skills_info


def get_skill_summary(skill_id: str, lang: Optional[str] = None) -> Optional[str]:
    """
    获取条目的简短摘要（用于显示）
    
    Args:
        skill_id: 条目 ID
        lang: 语言 (en/zh)，默认中文
    
    Returns:
        str: 条目摘要（标题）
    """
    try:
        config = _load_skill_config_raw(skill_id)
        if not config.get("enabled", False):
            return None
        
        if lang == "en":
            return config.get("title", config.get("title_zh", skill_id))
        else:
            return config.get("title_zh", config.get("title", skill_id))
    except FileNotFoundError:
        return None


def get_domain_skills(domain: int, enabled_only: bool = True) -> list[str]:
    """
    获取指定维度的所有条目 ID
    
    Args:
        domain: 维度编号 (1-6)
        enabled_only: 是否只列出已启用的 Skill，默认 True
    
    Returns:
        list[str]: 该维度的条目 ID 列表
    """
    all_skills = list_available_skills(enabled_only=enabled_only)
    domain_skills = []
    
    for skill_id in all_skills:
        try:
            config = _load_skill_config_raw(skill_id)
            if config.get("domain") == domain:
                domain_skills.append(skill_id)
        except (FileNotFoundError, yaml.YAMLError):
            continue
    
    return domain_skills


# AGREE-II 维度信息
AGREE_DOMAINS = {
    1: {"name": "Scope and Purpose", "name_zh": "范围和目的", "items": "1-3"},
    2: {"name": "Stakeholder Involvement", "name_zh": "利益相关者参与", "items": "4-6"},
    3: {"name": "Rigour of Development", "name_zh": "制定的严谨性", "items": "7-14"},
    4: {"name": "Clarity of Presentation", "name_zh": "表达的清晰性", "items": "15-17"},
    5: {"name": "Applicability", "name_zh": "应用性", "items": "18-21"},
    6: {"name": "Editorial Independence", "name_zh": "编辑独立性", "items": "22-23"},
}
