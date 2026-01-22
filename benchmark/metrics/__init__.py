# -*- coding: utf-8 -*-
"""
指标计算模块

提供可扩展的指标计算框架。
"""

from .base import BaseMetric, MetricResult
from .consistency import ConsistencyMetric

__all__ = [
    "BaseMetric",
    "MetricResult",
    "ConsistencyMetric",
]
