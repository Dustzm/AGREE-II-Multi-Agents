# -*- coding: utf-8 -*-
"""
一致性比例指标

计算模型评分与人类评分的一致性比例，支持2×2匹配组合。
"""

from typing import Dict, List, Tuple, Any

from .base import BaseMetric, MetricResult
from ..loader import HumanScore, ModelScore


# 4次匹配组合定义
MATCH_COMBINATIONS = [
    ('human1', 'batch_1'),  # 人类评价员1 vs 模型批次1
    ('human1', 'batch_2'),  # 人类评价员1 vs 模型批次2
    ('human2', 'batch_1'),  # 人类评价员2 vs 模型批次1
    ('human2', 'batch_2'),  # 人类评价员2 vs 模型批次2
]


class ConsistencyMetric(BaseMetric):
    """
    一致性比例指标（2×2匹配组合版本）
    
    对于每个条目j，计算4次匹配组合的一致性：
    - Δij,kj = |Score_ij - Score_kj|
    - 如果 Δij,kj ≤ threshold，则 δij,kj = 1，否则为0
    
    指南一致性: Consistency_n = Σδij,kj / (23 × 4)
    """
    
    name = "consistency_ratio"
    description = "一致性比例（2×2匹配组合，误差≤阈值的匹配占比）"
    
    def __init__(self, threshold: int = 2):
        """
        初始化
        
        Args:
            threshold: 误差阈值，默认2分
        """
        self.threshold = threshold
    
    def compute(
        self,
        human_scores: Dict[str, HumanScore],
        model_scores: Dict[str, Dict[str, ModelScore]],
        rater_names: List[str] = None,
        **kwargs
    ) -> MetricResult:
        """
        计算一致性比例（4次匹配组合）

        Args:
            human_scores: 人类评分字典 {item_id: HumanScore}
            model_scores: 模型评分字典 {batch_id: {item_id: ModelScore}}
            rater_names: 评分者姓名列表，如 ['吴之仪', '曾传飞']

        Returns:
            MetricResult 包含一致性比例和条目级详情
        """
        # 默认评分者姓名
        if rater_names is None:
            rater_names = []
        total_items = 23  # AGREE II共23个条目
        
        # 动态获取 batch_ids（从 model_scores 字典的键中获取）
        batch_ids = list(model_scores.keys())
        if len(batch_ids) < 2:
            # 如果不足2个 batch，补充空的
            batch_ids.extend(['batch_1', 'batch_2'][:2-len(batch_ids)])
        batch_1, batch_2 = batch_ids[0], batch_ids[1]
        
        # 动态生成匹配组合
        match_combinations = [
            ('human1', batch_1),  # 人类评价员1 vs 模型批次1
            ('human1', batch_2),  # 人类评价员1 vs 模型批次2
            ('human2', batch_1),  # 人类评价员2 vs 模型批次1
            ('human2', batch_2),  # 人类评价员2 vs 模型批次2
        ]
        
        total_matches = total_items * 4  # 每个条目4次匹配
        consistent_count = 0
        item_details = {}
        
        for item_id in [f"item_{i}" for i in range(1, 24)]:
            human = human_scores.get(item_id)
            
            if human is None:
                item_details[item_id] = {
                    'status': 'missing_human',
                    'matches': [],
                    'consistent_count': 0
                }
                continue
            
            # 获取人类评分
            human_scores_dict = {
                'human1': human.score1,
                'human2': human.score2
            }
            
            # 计算4次匹配
            matches = []
            item_consistent_count = 0
            
            for human_key, batch_id in match_combinations:
                human_value = human_scores_dict[human_key]
                
                # 获取模型评分
                batch_scores = model_scores.get(batch_id, {})
                model = batch_scores.get(item_id)
                
                if model is None:
                    match_detail = {
                        'human_key': human_key,
                        'batch_id': batch_id,
                        'human_score': human_value,
                        'model_score': None,
                        'diff': None,
                        'consistent': 0,
                        'status': 'missing_model'
                    }
                else:
                    model_value = model.score
                    diff = abs(model_value - human_value)
                    is_consistent = diff <= self.threshold
                    
                    if is_consistent:
                        item_consistent_count += 1
                        consistent_count += 1
                    
                    match_detail = {
                        'human_key': human_key,
                        'batch_id': batch_id,
                        'human_score': human_value,
                        'model_score': model_value,
                        'diff': diff,
                        'consistent': 1 if is_consistent else 0,
                        'status': 'ok'
                    }
                
                matches.append(match_detail)
            
            item_details[item_id] = {
                'status': 'ok',
                'human_score1': human.score1,
                'human_score2': human.score2,
                'matches': matches,
                'consistent_count': item_consistent_count
            }
        
        # 计算指南一致性比例
        consistency_ratio = consistent_count / total_matches if total_matches > 0 else 0

        return MetricResult(
            name=self.name,
            value=consistency_ratio,
            details={
                'threshold': self.threshold,
                'consistent_count': consistent_count,
                'total_matches': total_matches,
                'total_items': total_items,
                'batch_ids': batch_ids[:2],  # 记录使用的 batch_ids
                'rater_names': rater_names,  # 记录评分者姓名
                'items': item_details
            }
        )


def compute_item_consistency_across_guides(
    all_results: List[Dict[str, Any]],
    threshold: int = 2
) -> Dict[str, Dict[str, Any]]:
    """
    跨指南计算单个条目的一致性
    
    Item Consistency_j = Σδij,kj / (4 × N)
    其中N是评价的指南数量
    
    Args:
        all_results: 所有指南的一致性计算结果列表
        threshold: 误差阈值
        
    Returns:
        {item_id: {consistent_count, total_matches, consistency_ratio}}
    """
    item_stats = {}
    
    for i in range(1, 24):
        item_id = f"item_{i}"
        item_stats[item_id] = {
            'consistent_count': 0,
            'total_matches': 0,
            'guides_count': 0
        }
    
    # 统计各指南中每个条目的一致性
    for result in all_results:
        items = result.get('items', {})
        
        for item_id, item_detail in items.items():
            if item_detail.get('status') == 'ok':
                item_stats[item_id]['consistent_count'] += item_detail.get('consistent_count', 0)
                item_stats[item_id]['total_matches'] += 4  # 每个指南4次匹配
                item_stats[item_id]['guides_count'] += 1
    
    # 计算一致性比例
    for item_id, stats in item_stats.items():
        total = stats['total_matches']
        stats['consistency_ratio'] = stats['consistent_count'] / total if total > 0 else 0
    
    return item_stats
