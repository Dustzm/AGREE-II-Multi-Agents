# -*- coding: utf-8 -*-
"""
结果报告生成器

生成Excel格式的对比报告，包含指南一致性和条目一致性汇总。
"""

import logging
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd

from .benchmark import BenchmarkResult
from .metrics.consistency import MATCH_COMBINATIONS

logger = logging.getLogger(__name__)


class ExcelReporter:
    """Excel报告生成器（2×2匹配组合版本）"""
    
    def generate(self, results: List[BenchmarkResult], output_path: str):
        """
        生成Excel报告
        
        Args:
            results: 对比结果列表
            output_path: 输出文件路径
        """
        output_path = Path(output_path)
        
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # Sheet1: 指南一致性汇总
            self._write_guide_consistency_sheet(writer, results)
            
            # Sheet2: 条目一致性汇总
            self._write_item_consistency_sheet(writer, results)
            
            # 每个成功的指南一个详情Sheet
            for result in results:
                if result.success:
                    self._write_detail_sheet(writer, result)
        
        logger.info(f"报告已生成: {output_path}")
    
    def _write_guide_consistency_sheet(
        self, 
        writer: pd.ExcelWriter, 
        results: List[BenchmarkResult]
    ):
        """
        写入Sheet1: 指南一致性汇总
        
        Consistency_n = Σδij,kj / (23 × 4)
        """
        rows = []
        
        for result in results:
            consistency = result.metrics.get('consistency_ratio')
            
            row = {
                '序号': result.guide_entry.seq_no,
                '指南编号': result.guide_entry.guide_id,
                '指南名称': result.guide_entry.guide_name,
                '状态': '成功' if result.success else '失败',
            }
            
            if consistency:
                details = consistency.details
                row['一致匹配数'] = details.get('consistent_count', 0)
                row['总匹配数'] = details.get('total_matches', 92)  # 23 × 4
                row['一致性比例'] = f"{consistency.value:.2%}"
            else:
                row['一致匹配数'] = '-'
                row['总匹配数'] = '-'
                row['一致性比例'] = '-'
            
            # 添加错误信息
            if result.error:
                row['错误'] = result.error
            
            rows.append(row)
        
        # 添加汇总行
        success_results = [r for r in results if r.success]
        if success_results:
            total_consistent = sum(
                r.metrics.get('consistency_ratio').details.get('consistent_count', 0)
                for r in success_results
                if r.metrics.get('consistency_ratio')
            )
            total_matches = len(success_results) * 23 * 4
            overall_ratio = total_consistent / total_matches if total_matches > 0 else 0
            
            rows.append({
                '序号': '【总计】',
                '指南编号': '-',
                '指南名称': f'{len(success_results)}部指南',
                '状态': '-',
                '一致匹配数': total_consistent,
                '总匹配数': total_matches,
                '一致性比例': f"{overall_ratio:.2%}",
            })
        
        df = pd.DataFrame(rows)
        df.to_excel(writer, sheet_name='指南一致性', index=False)
    
    def _write_item_consistency_sheet(
        self, 
        writer: pd.ExcelWriter, 
        results: List[BenchmarkResult]
    ):
        """
        写入Sheet2: 条目一致性汇总
        
        Item Consistency_j = Σδij,kj / (4 × N)
        其中N是评价的指南数量
        """
        # 统计每个条目在所有指南中的一致性
        item_stats: Dict[str, Dict[str, int]] = {}
        for i in range(1, 24):
            item_id = f"item_{i}"
            item_stats[item_id] = {
                'consistent_count': 0,
                'total_matches': 0,
                'guides_count': 0
            }
        
        # 遍历所有成功的结果
        for result in results:
            if not result.success:
                continue
            
            consistency = result.metrics.get('consistency_ratio')
            if not consistency:
                continue
            
            items = consistency.details.get('items', {})
            for item_id, item_detail in items.items():
                if item_detail.get('status') == 'ok':
                    item_stats[item_id]['consistent_count'] += item_detail.get('consistent_count', 0)
                    item_stats[item_id]['total_matches'] += 4  # 每个指南4次匹配
                    item_stats[item_id]['guides_count'] += 1
        
        # 生成行数据
        rows = []
        for i in range(1, 24):
            item_id = f"item_{i}"
            stats = item_stats[item_id]
            total = stats['total_matches']
            ratio = stats['consistent_count'] / total if total > 0 else 0
            
            rows.append({
                '条目': f'条目{i}',
                'item_id': item_id,
                '参与指南数': stats['guides_count'],
                '一致匹配数': stats['consistent_count'],
                '总匹配数': total,
                '一致性比例': f"{ratio:.2%}"
            })
        
        # 添加汇总行
        total_consistent = sum(s['consistent_count'] for s in item_stats.values())
        total_matches = sum(s['total_matches'] for s in item_stats.values())
        overall_ratio = total_consistent / total_matches if total_matches > 0 else 0
        
        rows.append({
            '条目': '【总计】',
            'item_id': '-',
            '参与指南数': '-',
            '一致匹配数': total_consistent,
            '总匹配数': total_matches,
            '一致性比例': f"{overall_ratio:.2%}"
        })
        
        df = pd.DataFrame(rows)
        df.to_excel(writer, sheet_name='条目一致性', index=False)
    
    def _write_detail_sheet(self, writer: pd.ExcelWriter, result: BenchmarkResult):
        """写入详情Sheet（每个指南一个）"""
        rows = []
        
        # 获取一致性指标详情
        consistency = result.metrics.get('consistency_ratio')
        item_details = consistency.details.get('items', {}) if consistency else {}
        
        for i in range(1, 24):
            item_id = f"item_{i}"
            human = result.human_scores.get(item_id)
            detail = item_details.get(item_id, {})
            
            row = {
                '条目': f"条目{i}",
                'item_id': item_id,
                '人类评分者1': human.score1 if human else '-',
                '人类评分者2': human.score2 if human else '-',
            }
            
            # 添加4次匹配的详细信息
            matches = detail.get('matches', [])
            for match in matches:
                human_key = match.get('human_key', '')
                batch_id = match.get('batch_id', '')
                col_prefix = f"{human_key}_{batch_id}"
                
                model_score = match.get('model_score')
                row[f'{col_prefix}_模型分'] = model_score if model_score is not None else '-'
                row[f'{col_prefix}_差值'] = match.get('diff', '-')
                row[f'{col_prefix}_一致'] = '✓' if match.get('consistent') == 1 else '✗'
            
            # 汇总
            row['一致匹配数'] = detail.get('consistent_count', 0) if detail.get('status') == 'ok' else '-'
            
            rows.append(row)
        
        # 添加汇总行
        if consistency:
            rows.append({
                '条目': '【汇总】',
                'item_id': '-',
                '人类评分者1': '-',
                '人类评分者2': '-',
                '一致匹配数': f"{consistency.details.get('consistent_count', 0)}/{consistency.details.get('total_matches', 92)}",
            })
            rows.append({
                '条目': '【一致性比例】',
                'item_id': '-',
                '人类评分者1': '-',
                '人类评分者2': '-',
                '一致匹配数': f"{consistency.value:.2%}",
            })
        
        df = pd.DataFrame(rows)
        
        # Sheet名使用序号，最多31字符
        sheet_name = str(result.guide_entry.seq_no)[:31]
        df.to_excel(writer, sheet_name=sheet_name, index=False)
