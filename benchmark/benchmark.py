# -*- coding: utf-8 -*-
"""
主对比引擎

协调数据加载和指标计算，支持2×2匹配组合。
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .loader import (
    GuideEntry,
    HumanScore,
    HumanScoreLoader,
    ModelScore,
    BatchModelScoreLoader,
)
from .metrics.base import BaseMetric, MetricResult

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """单个指南的对比结果"""
    guide_entry: GuideEntry
    task_dirs: Dict[str, Optional[Path]]  # {batch_id: task_dir}
    human_scores: Dict[str, HumanScore]
    model_scores: Dict[str, Dict[str, ModelScore]]  # {batch_id: {item_id: ModelScore}}
    metrics: Dict[str, MetricResult] = field(default_factory=dict)
    error: Optional[str] = None
    
    @property
    def success(self) -> bool:
        """检查是否有至少一个批次的模型评分"""
        return self.error is None and any(len(scores) > 0 for scores in self.model_scores.values())
    
    @property
    def has_all_batches(self) -> bool:
        """检查是否所有批次都有评分"""
        return all(len(scores) > 0 for scores in self.model_scores.values())


class Benchmark:
    """主对比引擎（2×2匹配组合版本）"""

    def __init__(self, human_file: str, output_dir: str):
        """
        初始化

        Args:
            human_file: 人类评分Excel文件路径
            output_dir: 模型评估输出目录（包含batch_1, batch_2子目录）
        """
        self.human_loader = HumanScoreLoader(human_file)
        self.model_loader = BatchModelScoreLoader(output_dir)
        self.metrics: List[BaseMetric] = []
        self.rater_names: List[str] = []  # 评分者姓名

        # 记录可用批次
        available = self.model_loader.available_batches
        logger.info(f"可用批次: {available}")

        if len(available) < 2:
            logger.warning(f"期望2个批次，但只找到 {len(available)} 个: {available}")
    
    def register_metric(self, metric: BaseMetric):
        """
        注册指标
        
        Args:
            metric: 指标实例
        """
        self.metrics.append(metric)
        logger.info(f"注册指标: {metric.name}")
    
    def run(self) -> List[BenchmarkResult]:
        """
        执行对比

        Returns:
            所有指南的对比结果列表
        """
        results = []

        # 加载目录
        entries = self.human_loader.load_index()
        logger.info(f"开始对比 {len(entries)} 个指南")

        # 获取评分者姓名（从第一个指南 sheet）
        if entries:
            self.rater_names = self.human_loader.get_rater_names(entries[0].seq_no)
            logger.info(f"评分者姓名: {self.rater_names}")

        for entry in entries:
            result = self._process_guide(entry)
            results.append(result)
        
        # 统计结果
        success_count = sum(1 for r in results if r.success)
        full_success_count = sum(1 for r in results if r.has_all_batches)
        logger.info(f"对比完成: {success_count}/{len(results)} 个指南成功 (完整批次: {full_success_count})")
        
        return results
    
    def _process_guide(self, entry: GuideEntry) -> BenchmarkResult:
        """
        处理单个指南
        
        Args:
            entry: 指南条目
            
        Returns:
            对比结果
        """
        logger.info(f"处理指南: {entry.guide_id} (序号 {entry.seq_no})")
        
        # 加载人类评分
        try:
            human_scores = self.human_loader.load_scores(entry.seq_no)
        except Exception as e:
            logger.error(f"加载人类评分失败: {e}")
            return BenchmarkResult(
                guide_entry=entry,
                task_dirs={},
                human_scores={},
                model_scores={},
                error=f"加载人类评分失败: {e}"
            )
        
        # 加载所有批次的模型评分
        try:
            model_scores = self.model_loader.load_all_scores(entry.guide_id)
            task_dirs = self.model_loader.find_latest_tasks(entry.guide_id)
        except Exception as e:
            logger.error(f"加载模型评分失败: {e}")
            return BenchmarkResult(
                guide_entry=entry,
                task_dirs={},
                human_scores=human_scores,
                model_scores={},
                error=f"加载模型评分失败: {e}"
            )
        
        # 检查是否有模型评分
        has_scores = any(len(scores) > 0 for scores in model_scores.values())
        if not has_scores:
            return BenchmarkResult(
                guide_entry=entry,
                task_dirs=task_dirs,
                human_scores=human_scores,
                model_scores=model_scores,
                error=f"未找到任何批次的模型评估结果"
            )
        
        # 计算指标
        result = BenchmarkResult(
            guide_entry=entry,
            task_dirs=task_dirs,
            human_scores=human_scores,
            model_scores=model_scores
        )
        
        for metric in self.metrics:
            try:
                metric_result = metric.compute(human_scores, model_scores, rater_names=self.rater_names)
                result.metrics[metric.name] = metric_result
                logger.debug(f"  {metric.name}: {metric_result.value:.4f}")
            except Exception as e:
                logger.error(f"计算指标 {metric.name} 失败: {e}")
        
        return result
