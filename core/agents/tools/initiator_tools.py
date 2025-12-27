"""
Initiator 智能体工具函数

提供评价计划生成相关的工具
"""
import logging
from pathlib import Path

from .public_tools import get_task_paths
from core.skills import get_enabled_skills_info, AGREE_DOMAINS

logger = logging.getLogger(__name__)


def save_plan(task_id: str, content: str) -> dict:
    """
    【关键工具】保存评价计划到任务目录
    
    计划文件路径: output/{task_id}/.ama/plan.md
    
    Args:
        task_id: 任务ID（如 "guideline-12231335"）
        content: 计划内容，Markdown 格式，包含 [APPRAISE:item_x] 和 [REPORT] 标记
        
    Returns:
        dict: {
            "status": "success/error",
            "path": str,  # 保存的文件路径
            "error": str
        }
    
    示例:
        save_plan(
            task_id="my-task-123",
            content="# AGREE-II 评价计划\\n\\n- [ ] item_1: 目标描述 [APPRAISE:item_1]\\n..."
        )
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "path": "",
                "error": f"任务不存在: {task_id}"
            }
        
        file_path = Path(paths["plan"])
        file_path.write_text(content, encoding="utf-8")
        
        logger.info(f"评价计划已保存: {file_path}")
        return {
            "status": "success",
            "path": str(file_path),
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "path": "",
            "error": str(e)
        }


def get_enabled_skills_for_plan() -> dict:
    """
    获取所有启用的 Skill 信息，用于 Initiator 生成评价计划
    
    Returns:
        dict: {
            "status": "success/error",
            "skills": list[dict],  # 每个 dict 包含 {item_id, domain, title}
            "domain_groups": dict,  # 按维度分组
            "count": int,
            "error": str
        }
    """
    try:
        # 获取配置的语言
        from core.config import get_ama_settings
        lang = get_ama_settings().skill_lang
        
        skills = get_enabled_skills_info(lang)
        
        # 按维度分组
        domain_groups = {}
        for skill in skills:
            domain = skill["domain"]
            if domain not in domain_groups:
                domain_info = AGREE_DOMAINS.get(domain, {})
                # 根据语言选择维度名称
                if lang == "zh":
                    name = domain_info.get("name_zh", f"维度 {domain}")
                else:
                    name = domain_info.get("name", f"Domain {domain}")
                domain_groups[domain] = {
                    "name": name,
                    "items": []
                }
            domain_groups[domain]["items"].append(skill)
        
        logger.info(f"获取启用的 Skills: {len(skills)} 个")
        return {
            "status": "success",
            "skills": skills,
            "domain_groups": domain_groups,
            "count": len(skills),
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "skills": [],
            "domain_groups": {},
            "count": 0,
            "error": str(e)
        }
