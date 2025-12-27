"""
提示词管理服务

读取和更新智能体提示词文件
"""
import re
from pathlib import Path
from typing import List, Optional

# 提示词目录
PROMPTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "core" / "prompts"


def get_agent_list() -> List[str]:
    """获取所有可用的智能体名称列表"""
    # 从 en 目录读取（假设 en 和 zh 目录结构相同）
    en_dir = PROMPTS_DIR / "en"
    if not en_dir.exists():
        return []
    
    agents = []
    for file in en_dir.glob("*.md"):
        agents.append(file.stem)
    
    return sorted(agents)


def extract_variables(content: str) -> List[str]:
    """从提示词内容中提取所有 {variable} 变量"""
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, content)
    # 去重并保持顺序
    seen = set()
    variables = []
    for var in matches:
        if var not in seen:
            seen.add(var)
            variables.append(var)
    return variables


def get_prompt(agent_name: str, lang: str = "en") -> Optional[dict]:
    """
    获取指定智能体的提示词
    
    Args:
        agent_name: 智能体名称 (如 "appraiser")
        lang: 语言 ("en" 或 "zh")
    
    Returns:
        包含 agent_name, lang, content, variables 的字典
    """
    prompt_file = PROMPTS_DIR / lang / f"{agent_name}.md"
    
    if not prompt_file.exists():
        return None
    
    content = prompt_file.read_text(encoding="utf-8")
    variables = extract_variables(content)
    
    return {
        "agent_name": agent_name,
        "lang": lang,
        "content": content,
        "variables": variables
    }


def update_prompt(agent_name: str, lang: str, content: str) -> dict:
    """
    更新指定智能体的提示词
    
    Args:
        agent_name: 智能体名称
        lang: 语言
        content: 新的提示词内容
    
    Returns:
        更新后的提示词数据
    """
    prompt_file = PROMPTS_DIR / lang / f"{agent_name}.md"
    
    # 确保目录存在
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    
    # 写入文件
    prompt_file.write_text(content, encoding="utf-8")
    
    # 返回更新后的数据
    variables = extract_variables(content)
    
    return {
        "agent_name": agent_name,
        "lang": lang,
        "content": content,
        "variables": variables
    }
