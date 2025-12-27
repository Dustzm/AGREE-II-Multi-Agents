"""
提示词加载器

加载智能体提示词模板
"""
import logging
from pathlib import Path
from typing import Optional

from core.config import get_ama_settings

logger = logging.getLogger(__name__)

# 提示词目录路径
PROMPTS_DIR = Path(__file__).parent


def load_prompt(agent_name: str, lang: Optional[str] = None) -> str:
    """
    加载指定智能体的提示词
    
    Args:
        agent_name: 智能体名称 (initiator/appraiser/reporter/orchestrator)
        lang: 语言 (en/zh)，默认从配置读取
    
    Returns:
        str: 提示词内容
    
    Raises:
        FileNotFoundError: 提示词文件不存在
    """
    if lang is None:
        config = get_ama_settings()
        lang = config.prompt_lang
    
    prompt_path = PROMPTS_DIR / lang / f"{agent_name}.md"
    
    if not prompt_path.exists():
        # 回退到英文
        fallback_path = PROMPTS_DIR / "en" / f"{agent_name}.md"
        if fallback_path.exists():
            logger.warning(f"提示词 {prompt_path} 不存在，使用英文版本")
            prompt_path = fallback_path
        else:
            raise FileNotFoundError(f"提示词文件不存在: {prompt_path}")
    
    content = prompt_path.read_text(encoding="utf-8")
    
    # 移除 Markdown 标题（第一行）
    lines = content.strip().split("\n")
    if lines and lines[0].startswith("# "):
        content = "\n".join(lines[1:]).strip()
    
    logger.info(f"加载提示词: {prompt_path}")
    return content


def get_prompt(agent_name: str) -> str:
    """获取提示词（快捷方法）"""
    return load_prompt(agent_name)


# 导出
__all__ = [
    "load_prompt",
    "get_prompt",
    "PROMPTS_DIR",
]
