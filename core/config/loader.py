"""
AMA 配置加载器

加载系统配置文件
"""
from pathlib import Path
from typing import Optional

import yaml

# 配置目录路径
CONFIG_DIR = Path(__file__).parent

# 缓存
_config_cache: dict = {}


class AMASettings:
    """AMA 系统配置类"""
    
    def __init__(self, config_dict: dict):
        self._config = config_dict
        
        # 语言设置
        lang = config_dict.get("language", {})
        self.prompt_lang = lang.get("prompt", "en")
        self.skill_lang = lang.get("skill", "en")
        
        # LLM 设置
        llm = config_dict.get("llm", {})
        self.temperature = llm.get("temperature", 0.3)
        max_tokens = llm.get("max_tokens")
        self.max_tokens = None if max_tokens in (None, -1) else max_tokens
        
        # 输出设置
        output = config_dict.get("output", {})
        self.save_cot = output.get("save_cot", True)
        self.save_evidence = output.get("save_evidence", True)
        self.output_formats = output.get("formats", ["json", "md"])
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return self._config


def load_settings(reload: bool = False) -> AMASettings:
    """
    加载系统配置
    
    Args:
        reload: 是否强制重新加载
    
    Returns:
        AMASettings: 配置对象
    """
    global _config_cache
    
    if not reload and "settings" in _config_cache:
        return _config_cache["settings"]
    
    config_path = CONFIG_DIR / "settings.yaml"
    
    if not config_path.exists():
        config_dict = {
            "language": {"prompt": "en", "skill": "en"},
            "llm": {"temperature": 0.3, "max_tokens": None},
            "output": {"save_cot": True, "save_evidence": True, "formats": ["json", "md"]}
        }
    else:
        with open(config_path, "r", encoding="utf-8") as f:
            config_dict = yaml.safe_load(f)
    
    config = AMASettings(config_dict)
    _config_cache["settings"] = config
    
    return config


def get_settings() -> AMASettings:
    """获取系统配置"""
    return load_settings()


__all__ = [
    "AMASettings",
    "load_settings",
    "get_settings",
    "CONFIG_DIR",
]
