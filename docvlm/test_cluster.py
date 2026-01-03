#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
标题层级聚类测试脚本

功能：
1. 读取现有的 docvlm_output.json（VLM 解析结果）
2. 读取原始 PDF 文件（提取字体信息）
3. 执行坐标匹配和聚类逻辑
4. 输出对比报告

使用方法：
    python -m docvlm.test_cluster <json_path> <pdf_path>

示例：
    python -m docvlm.test_cluster input/1-2017-014E_docvlm/docvlm_output.json input/1-2017-014E.pdf
"""

import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any

from .schemas import Block, Page
from .font_extractor import extract_font_info, match_block_font
from .title_classifier import classify_title_levels


def load_blocks_from_json(json_path: str) -> tuple:
    """
    从 docvlm_output.json 加载 blocks
    
    Returns:
        (pages: List[Page], page_sizes: List[tuple])
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    pages = []
    for page_data in data.get("pages", []):
        blocks = []
        for block_data in page_data.get("blocks", []):
            block = Block(
                block_id=block_data.get("block_id", ""),
                type=block_data.get("type", "text"),
                content=block_data.get("content", ""),
                bbox=block_data.get("bbox", []),
                level=block_data.get("level"),
            )
            blocks.append(block)
        
        page = Page(
            page_idx=page_data.get("page_idx", 0),
            page_size=page_data.get("page_size", [0, 0]),
            blocks=blocks,
        )
        pages.append(page)
    
    return pages


def print_report(
    pages: List[Page],
    classify_result: Dict[str, Any],
    json_path: str,
    pdf_path: str
) -> None:
    """打印聚类报告"""
    clusters = classify_result["clusters"]
    block_levels = classify_result["block_levels"]
    mismatches = classify_result["mismatches"]
    
    # 收集所有标题
    all_titles = []
    for page in pages:
        for block in page.blocks:
            if block.type == "title":
                all_titles.append({
                    "page_idx": page.page_idx,
                    "block": block,
                    "cluster_level": block_levels.get(block.block_id),
                })
    
    print("=" * 60)
    print("=== Title Clustering Report ===")
    print("=" * 60)
    print(f"PDF: {pdf_path}")
    print(f"JSON: {json_path}")
    print(f"Total Titles: {len(all_titles)}")
    print(f"Detected Clusters: {len(clusters)}")
    print()
    
    # Cluster Summary
    print("--- Cluster Summary ---")
    for c in clusters:
        bold_str = "Bold" if c["is_bold"] else "Regular"
        print(f"Level {c['level']}: font_size={c['font_size']}pt, {bold_str}, count={c['count']}")
    print()
    
    # Title List
    print("--- Title List (Document Order) ---")
    header = f"{'Page':>4} | {'Block ID':<10} | {'Cluster':<7} | {'VLM Lvl':<7} | {'Font':<14} | Title Content"
    print(header)
    print("-" * len(header))
    
    for item in all_titles:
        page_idx = item["page_idx"] + 1
        block = item["block"]
        cluster_lvl = item["cluster_level"]
        vlm_lvl = block.level
        
        # 格式化字体信息
        if block.font_size:
            bold_str = "Bold" if block.is_bold else ""
            font_str = f"{block.font_size}pt {bold_str}".strip()
        else:
            font_str = "N/A"
        
        # 检查是否匹配
        mismatch = ""
        if cluster_lvl and vlm_lvl and cluster_lvl != vlm_lvl:
            mismatch = " ❌"
        
        cluster_str = f"Lvl {cluster_lvl}" if cluster_lvl else "N/A"
        vlm_str = f"{vlm_lvl}{mismatch}" if vlm_lvl else "N/A"
        content = block.content[:40] + "..." if len(block.content) > 40 else block.content
        
        print(f"{page_idx:>4} | {block.block_id:<10} | {cluster_str:<7} | {vlm_str:<7} | {font_str:<14} | {content}")
    
    print()
    
    # Mismatch Summary
    print("--- Mismatch Summary ---")
    print(f"Total Mismatches: {len(mismatches)} / {len(all_titles)} ({len(mismatches)/len(all_titles)*100:.1f}%)" if all_titles else "No titles found")
    print()
    
    for m in mismatches:
        print(f"[Mismatch] {m['block_id']} \"{m['content']}\"")
        print(f"  VLM Level: {m['vlm_level']} → Cluster Level: {m['cluster_level']}")
        print()
    
    print("=" * 60)


def save_debug_json(
    pages: List[Page],
    classify_result: Dict[str, Any],
    output_path: str
) -> None:
    """保存调试 JSON 文件 - 更直观的格式"""
    block_levels = classify_result["block_levels"]
    
    # 构建标题列表 - 按文档顺序，直观展示
    title_list = []
    for page in pages:
        for block in page.blocks:
            if block.type == "title":
                cluster_level = block_levels.get(block.block_id)
                vlm_level = block.level
                
                # 判断是否匹配
                is_match = (cluster_level == vlm_level) if cluster_level and vlm_level else None
                
                title_list.append({
                    "page": page.page_idx + 1,
                    "title": block.content,
                    "cluster_level": cluster_level,
                    "vlm_level": vlm_level,
                    "match": "✓" if is_match else ("✗" if is_match is False else "?"),
                    "font": f"{block.font_size}pt {'Bold' if block.is_bold else ''}" if block.font_size else "未匹配",
                })
    
    # 构建输出数据
    debug_data = {
        "summary": {
            "total_titles": len(title_list),
            "clusters": [
                f"Level {c['level']}: {c['font_size']}pt {'Bold' if c['is_bold'] else 'Regular'} ({c['count']}个)"
                for c in classify_result["clusters"]
            ],
            "mismatch_count": len(classify_result["mismatches"]),
        },
        "title_list": title_list,
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(debug_data, f, ensure_ascii=False, indent=2)
    
    print(f"--- Debug JSON saved to: {output_path} ---")


def main():
    parser = argparse.ArgumentParser(description="标题层级聚类测试脚本")
    parser.add_argument("json_path", help="docvlm_output.json 文件路径")
    parser.add_argument("pdf_path", help="原始 PDF 文件路径")
    parser.add_argument("--output", "-o", default="cluster_debug.json", help="输出调试 JSON 路径")
    
    args = parser.parse_args()
    
    # 检查文件存在
    if not Path(args.json_path).exists():
        print(f"错误: JSON 文件不存在: {args.json_path}")
        sys.exit(1)
    if not Path(args.pdf_path).exists():
        print(f"错误: PDF 文件不存在: {args.pdf_path}")
        sys.exit(1)
    
    # Step 1: 加载 JSON
    print(f"加载 JSON: {args.json_path}")
    pages = load_blocks_from_json(args.json_path)
    print(f"  共 {len(pages)} 页")
    
    # Step 2: 提取 PDF 字体信息
    print(f"提取字体信息: {args.pdf_path}")
    font_info_list = extract_font_info(args.pdf_path)
    print(f"  共 {len(font_info_list)} 页字体信息")
    
    # Step 3: 坐标匹配 - 为每个 block 填充字体信息
    print("执行坐标匹配...")
    for page in pages:
        if page.page_idx < len(font_info_list):
            page_font_info = font_info_list[page.page_idx]
            # page.page_size 是渲染图片尺寸 [width, height]
            image_size = tuple(page.page_size) if page.page_size else None
            
            for block in page.blocks:
                bbox = block.bbox
                # 如果是归一化坐标（0-1范围），反算为像素坐标
                if bbox and len(bbox) == 4 and image_size:
                    if max(bbox) <= 1.0:  # 归一化坐标
                        img_w, img_h = image_size
                        pixel_bbox = [
                            bbox[0] * img_w,
                            bbox[1] * img_h,
                            bbox[2] * img_w,
                            bbox[3] * img_h
                        ]
                    else:
                        pixel_bbox = bbox  # 已经是像素坐标
                else:
                    pixel_bbox = bbox
                
                font_match = match_block_font(pixel_bbox, page_font_info, image_size=image_size)
                block.font_size = font_match["font_size"]
                block.font_name = font_match["font_name"]
                block.is_bold = font_match["is_bold"]
    
    # Step 4: 收集所有标题并聚类
    all_title_blocks = []
    for page in pages:
        for block in page.blocks:
            if block.type == "title":
                all_title_blocks.append(block)
    
    print(f"发现 {len(all_title_blocks)} 个标题")
    
    # Step 5: 执行聚类
    print("执行聚类分析...")
    classify_result = classify_title_levels(all_title_blocks)
    
    # Step 6: 打印报告
    print()
    print_report(pages, classify_result, args.json_path, args.pdf_path)
    
    # Step 7: 保存调试 JSON
    save_debug_json(pages, classify_result, args.output)


if __name__ == "__main__":
    main()
