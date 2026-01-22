#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AGREE II 对比工具命令行入口

用法:
    python -m benchmark.cli --human human.xlsx --output-dir output --report result.xlsx
"""

import argparse
import logging
import sys
from pathlib import Path

from .benchmark import Benchmark
from .metrics.consistency import ConsistencyMetric
from .reporter import ExcelReporter


def setup_logging(verbose: bool = False):
    """配置日志"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main():
    parser = argparse.ArgumentParser(
        description='AGREE II 评估指标对比工具'
    )
    parser.add_argument(
        '--human', '-H',
        required=True,
        help='人类专家评分Excel文件路径'
    )
    parser.add_argument(
        '--output-dir', '-O',
        default='output',
        help='模型评估输出目录 (默认: output)'
    )
    parser.add_argument(
        '--report', '-R',
        default='benchmark_result.xlsx',
        help='输出报告文件路径 (默认: benchmark_result.xlsx)'
    )
    parser.add_argument(
        '--threshold', '-T',
        type=int,
        default=2,
        help='一致性判断阈值 (默认: 2)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='显示详细日志'
    )
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)
    
    # 验证输入文件
    human_file = Path(args.human)
    if not human_file.exists():
        logger.error(f"人类评分文件不存在: {human_file}")
        sys.exit(1)
    
    output_dir = Path(args.output_dir)
    if not output_dir.exists():
        logger.error(f"输出目录不存在: {output_dir}")
        sys.exit(1)
    
    # 创建对比工具
    benchmark = Benchmark(str(human_file), str(output_dir))
    
    # 注册指标
    benchmark.register_metric(ConsistencyMetric(threshold=args.threshold))
    
    # 执行对比
    logger.info("开始执行对比...")
    results = benchmark.run()
    
    # 生成报告
    reporter = ExcelReporter()
    reporter.generate(results, args.report)
    
    # 打印摘要
    success_count = sum(1 for r in results if r.success)
    print(f"\n{'='*50}")
    print(f"对比完成: {success_count}/{len(results)} 个指南")
    print(f"报告已保存: {args.report}")
    
    # 打印一致性比例
    for result in results:
        if result.success:
            consistency = result.metrics.get('consistency_ratio')
            if consistency:
                print(f"  {result.guide_entry.guide_id}: {consistency.value:.2%}")
    
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
