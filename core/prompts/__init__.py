"""
AMA 提示词模块

管理智能体提示词模板
"""
from .loader import (
    load_prompt,
    get_prompt,
    PROMPTS_DIR,
)


__all__ = [
    "load_prompt",
    "get_prompt",
    "PROMPTS_DIR",
]

