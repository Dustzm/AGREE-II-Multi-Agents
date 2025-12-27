"""
Orchestrator 智能体工具函数

提供计划管理相关的工具
"""
import logging
import re
from pathlib import Path

from .public_tools import get_task_paths

logger = logging.getLogger(__name__)


def read_plan(task_id: str) -> dict:
    """
    读取评价计划
    
    Args:
        task_id: 任务ID
        
    Returns:
        dict: {"status": "success/error", "content": str, "error": str}
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "content": "",
                "error": f"任务不存在: {task_id}"
            }
        
        file_path = Path(paths["plan"])
        if not file_path.exists():
            return {
                "status": "error",
                "content": "",
                "error": f"计划文件不存在: {file_path}"
            }
        
        content = file_path.read_text(encoding="utf-8")
        return {
            "status": "success",
            "content": content,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "content": "",
            "error": str(e)
        }


def update_plan_item(task_id: str, item_id: str, status: str, notes: str = "") -> dict:
    """
    更新计划中某个任务项的状态
    
    Args:
        task_id: 任务ID
        item_id: 任务项ID (如 "item_1", "item_2", "report")
        status: 新状态 ("pending" | "in_progress" | "completed" | "failed")
        notes: 可选的备注信息
        
    Returns:
        dict: {"status": "success/error", "updated_plan": str, "error": str}
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "updated_plan": "",
                "error": f"任务不存在: {task_id}"
            }
        
        file_path = Path(paths["plan"])
        if not file_path.exists():
            return {
                "status": "error",
                "updated_plan": "",
                "error": f"计划文件不存在: {file_path}"
            }
        
        content = file_path.read_text(encoding="utf-8")
        
        # 状态标记映射
        status_markers = {
            "pending": "[ ]",
            "in_progress": "[/]",
            "completed": "[x]",
            "failed": "[!]"
        }
        
        new_marker = status_markers.get(status, "[ ]")
        
        # 查找并替换对应的任务项
        pattern = rf"(- \[[^\]]*\] {re.escape(item_id)}:)"
        replacement = f"- {new_marker} {item_id}:"
        
        new_content, count = re.subn(pattern, replacement, content)
        
        if count == 0:
            return {
                "status": "error",
                "updated_plan": content,
                "error": f"未找到任务项: {item_id}"
            }
        
        # 如果有备注，添加到该行末尾
        if notes:
            pattern = rf"(- {re.escape(new_marker)} {re.escape(item_id)}:[^\n]*)"
            new_content = re.sub(pattern, rf"\1 ({notes})", new_content)
        
        # 保存更新后的计划
        file_path.write_text(new_content, encoding="utf-8")
        
        logger.info(f"计划已更新: {item_id} -> {status}")
        return {
            "status": "success",
            "updated_plan": new_content,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "updated_plan": "",
            "error": str(e)
        }
