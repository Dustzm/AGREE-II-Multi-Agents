# 配置模块
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    """DocVLM 配置"""
    
    # VLM API 配置
    vlm_api_base: str = field(default_factory=lambda: os.getenv("VLM_API_BASE", ""))
    vlm_api_key: str = field(default_factory=lambda: os.getenv("VLM_API_KEY", ""))
    vlm_model_name: str = field(default_factory=lambda: os.getenv("VLM_MODEL_NAME", "GLM-4.6V-Flash"))
    vlm_timeout: int = field(default_factory=lambda: int(os.getenv("VLM_TIMEOUT", "40")))
    vlm_max_tokens: int = field(default_factory=lambda: int(os.getenv("VLM_MAX_TOKENS", "5060")))
    vlm_temperature: float = field(default_factory=lambda: float(os.getenv("VLM_TEMPERATURE", "0.2")))
    vlm_top_p: float = field(default_factory=lambda: float(os.getenv("VLM_TOP_P", "0.9")))
    vlm_frequency_penalty: float = field(default_factory=lambda: float(os.getenv("VLM_FREQUENCY_PENALTY", "0.0")))  # 惩罚重复 token
    # vlm_presence_penalty: float = field(default_factory=lambda: float(os.getenv("VLM_PRESENCE_PENALTY", "0.0")))
    # vlm_repetition_penalty: float = field(default_factory=lambda: float(os.getenv("VLM_REPETITION_PENALTY", "1.1")))  # 重复惩罚
    
    # 解析配置
    dpi: int = field(default_factory=lambda: int(os.getenv("DOCVLM_DPI", "200")))
    max_concurrency: int = field(default_factory=lambda: int(os.getenv("DOCVLM_MAX_CONCURRENCY", "4")))
    
    # 图片优化配置
    max_image_edge: int = field(default_factory=lambda: int(os.getenv("DOCVLM_MAX_IMAGE_EDGE", "1500")))  # 长边最大像素
    image_quality: int = field(default_factory=lambda: int(os.getenv("DOCVLM_IMAGE_QUALITY", "85")))  # JPEG 压缩质量
    
    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量加载配置"""
        return cls()
    
    def validate(self) -> None:
        """验证配置"""
        if not self.vlm_api_base:
            raise ValueError("VLM_API_BASE 未配置")
        if not self.vlm_model_name:
            raise ValueError("VLM_MODEL_NAME 未配置")
