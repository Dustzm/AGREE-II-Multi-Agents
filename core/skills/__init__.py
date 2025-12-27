"""
AGREE-II Skill 配置模块

该模块包含 AGREE-II 评价工具的 23 个条目配置文件
每个条目配置包含：评价标准、评分原则、定位信息等
"""
from .loader import (
    load_skill_config,
    list_available_skills,
    get_enabled_skills_info,
    get_skill_summary,
    get_domain_skills,
    AGREE_DOMAINS,
    SKILLS_DIR,
)


__all__ = [
    "load_skill_config",
    "list_available_skills",
    "get_enabled_skills_info",
    "get_skill_summary",
    "get_domain_skills",
    "AGREE_DOMAINS",
    "SKILLS_DIR",
]
