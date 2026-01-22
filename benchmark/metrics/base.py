# -*- coding: utf-8 -*-
"""
指标基类

提供指标计算的标准接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict

from ..loader import HumanScore, ModelScore


@dataclass
class MetricResult:
    """指标计算结果"""
    name: str                           # 指标名称
    value: float                        # 指标值
    details: Dict[str, Any] = field(default_factory=dict)  # 详细信息
    
    def __repr__(self) -> str:
        return f"MetricResult(name={self.name}, value={self.value:.4f})"


class BaseMetric(ABC):
    """指标基类"""
    
    name: str = "base_metric"
    description: str = "基础指标"
    
    @abstractmethod
    def compute(
        self,
        human_scores: Dict[str, HumanScore],
        model_scores: Dict[str, ModelScore],
        **kwargs
    ) -> MetricResult:
        """
        计算指标
        
        Args:
            human_scores: 人类评分字典 {item_id: HumanScore}
            model_scores: 模型评分字典 {item_id: ModelScore}
            **kwargs: 额外参数
            
        Returns:
            指标计算结果
        """
        pass
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name})"
