# 标题层级分类模块
# 使用聚类算法基于字体属性对标题进行分级

import re
from typing import List, Dict, Any, Tuple
from collections import defaultdict
import numpy as np
from loguru import logger


def classify_title_levels(
    title_blocks: List[Any],
    max_levels: int = 4
) -> Dict[str, Any]:
    """
    基于字体属性对标题进行聚类分级
    
    Args:
        title_blocks: 所有 title 类型的 Block 对象列表
        max_levels: 最大层级数
        
    Returns:
        {
            'clusters': List[{'level': int, 'font_size': float, 'is_bold': bool, 'count': int}],
            'block_levels': Dict[block_id, predicted_level],
            'mismatches': List[{'block_id': str, 'vlm_level': int, 'cluster_level': int}]
        }
    """
    if not title_blocks:
        return {"clusters": [], "block_levels": {}, "mismatches": []}
    
    # 提取特征：font_size 为主要特征
    features = []
    valid_blocks = []
    
    for block in title_blocks:
        if block.font_size is not None:
            features.append({
                "block_id": block.block_id,
                "font_size": block.font_size,
                "is_bold": block.is_bold or False,
                "is_centered": block.is_centered or False,
                "vlm_level": block.level,
                "content": block.content[:50] if block.content else "",
            })
            valid_blocks.append(block)
    
    if not features:
        logger.warning("没有标题包含有效的字体信息，无法进行聚类")
        return {"clusters": [], "block_levels": {}, "mismatches": []}
    
    # 辅助函数：计算标题模式分数
    def get_pattern_score(text: str) -> int:
        text = text.strip()
        # 1. 中文数字 (一、二、 一 标题) -> 30
        if re.match(r'^[一二三四五六七八九十]+([\.、 ]|$)', text):
            return 30
        # 2. 阿拉伯数字 复杂 (1.1, 1.2.3) -> 10 (层级较低)
        if re.match(r'^\d+(\.\d+)+', text):
            return 10
        # 3. 阿拉伯数字 简单 (1., 1 标题, 1标题) -> 20
        if re.match(r'^\d+([^\d\.]|$)', text):
            return 20
        # 4. 其他 -> 0
        return 0

    # 按 (font_size, is_centered, pattern_score) 分组
    # key: (size_key, center_key, score_key)
    # 注：暂时禁用 is_centered 权重
    groups: Dict[Tuple[float, bool, int], List[Dict]] = defaultdict(list)
    
    for f in features:
        # 四舍五入到 0.5 pt 精度
        rounded_size = round(f["font_size"] * 2) / 2
        score = get_pattern_score(f["content"])
        # is_centered = f.get("is_centered", False)  # [暂时禁用]
        is_centered = False  # [暂时禁用] 不使用居中作为分组依据
        groups[(rounded_size, is_centered, score)].append(f)
    
    # 排序键：字号降序，然后分数降序
    # 例如：(10.5, False, 30) > (10.5, False, 20)
    # [暂时禁用] 原逻辑：字号降序，然后居中优先，然后分数降序
    # sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[1], k[2]), reverse=True)
    sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[2]), reverse=True)
    
    # 限制最大层级数
    if len(sorted_keys) > max_levels:
        logger.debug(f"检测到 {len(sorted_keys)} 种层级组合，合并为 {max_levels} 级")
        merged_keys = sorted_keys[:max_levels]
        
        # 将剩余的合并到最接近的层级
        # 距离计算：字号权重大，分数权重小
        # [暂时禁用] 原逻辑包含居中权重
        def calc_dist(k1, k2):
            size_diff = abs(k1[0] - k2[0])
            # center_diff = 1.0 if k1[1] != k2[1] else 0.0  # [暂时禁用]
            score_diff = abs(k1[2] - k2[2])
            
            # 权重：字号(1000) > 分数(1)
            # [暂时禁用] 原权重：字号(1000) > 居中(100) > 分数(1)
            # return size_diff * 1000 + center_diff * 100 + score_diff
            return size_diff * 1000 + score_diff

        for extra_key in sorted_keys[max_levels:]:
            closest = min(merged_keys, key=lambda mk: calc_dist(mk, extra_key))
            groups[closest].extend(groups[extra_key])
            del groups[extra_key]
        
        sorted_keys = merged_keys
    
    # 构建结果
    clusters = []
    block_levels: Dict[str, int] = {}
    
    for level, key in enumerate(sorted_keys, start=1):
        font_size, is_centered, score = key
        group = groups[key]
        bold_count = sum(1 for f in group if f["is_bold"])
        
        clusters.append({
            "level": level,
            "font_size": font_size,
            "is_centered": is_centered,
            "pattern_score": score,
            "is_bold": bold_count > len(group) / 2,
            "count": len(group),
        })
        
        for f in group:
            block_levels[f["block_id"]] = level
    
    # 检测与 VLM 判断的差异
    mismatches = []
    for f in features:
        predicted = block_levels.get(f["block_id"])
        vlm = f["vlm_level"]
        if predicted and vlm and predicted != vlm:
            mismatches.append({
                "block_id": f["block_id"],
                "vlm_level": vlm,
                "cluster_level": predicted,
                "font_size": f["font_size"],
                "is_bold": f["is_bold"],
                "content": f["content"],
            })
    
    return {
        "clusters": clusters,
        "block_levels": block_levels,
        "mismatches": mismatches,
    }


def apply_cluster_levels(
    title_blocks: List[Any],
    block_levels: Dict[str, int]
) -> None:
    """
    将聚类结果应用到 Block 对象（原地修改 level 属性）
    
    Args:
        title_blocks: title 类型的 Block 对象列表
        block_levels: block_id -> predicted_level 映射
    """
    for block in title_blocks:
        if block.block_id in block_levels:
            block.level = block_levels[block.block_id]
