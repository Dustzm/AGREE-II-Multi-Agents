#!/usr/bin/env python
# -*- coding: utf-8 -*-

from typing import List, Dict, Any, Tuple
import pdfplumber
from loguru import logger

from .loader import MineruBlock, load_mineru_content_list
from .font_extractor import extract_words_with_font, match_title_font
from .classifier import classify_title_levels

def build_doc_index(
    json_path: str,
    pdf_path: str,
    margin: float = 40.0,
    line_tolerance: float = None
) -> Tuple[List[MineruBlock], Dict[str, Any]]:
    """
    构建文档索引：提取字体、匹配标题、执行聚类
    
    Args:
        json_path: MinerU content_list.json 路径
        pdf_path: 原始 PDF 路径
        margin: bbox 扩充边距 (默认 40.0)
        line_tolerance: 行高匹配容差 (默认 None=自适应)
        
    Returns:
        (blocks, classify_result)
        blocks: 包含字体信息的 MineruBlock 列表
        classify_result: 聚类结果字典
    """
    # 1. 加载 MinerU JSON
    logger.info(f"Loading MinerU JSON: {json_path}")
    blocks = load_mineru_content_list(json_path)
    title_blocks = [b for b in blocks if b.text_level is not None]
    logger.info(f"Loaded {len(blocks)} blocks, {len(title_blocks)} titles")
    
    # 2. 提取 PDF 字符信息
    logger.info(f"Extracting font info from PDF: {pdf_path}")
    all_chars = extract_words_with_font(pdf_path)
    logger.info(f"Extracted {len(all_chars)} chars")
    
    # 3. 计算缩放比例
    all_bboxes = [b.bbox for b in blocks if b.bbox and len(b.bbox) == 4]
    if all_bboxes:
        max_x = max(bb[2] for bb in all_bboxes)
        max_y = max(bb[3] for bb in all_bboxes)
    else:
        max_x, max_y = 1000, 1000
        
    with pdfplumber.open(pdf_path) as pdf:
        if pdf.pages:
            pdf_width = pdf.pages[0].width
            pdf_height = pdf.pages[0].height
        else:
            pdf_width, pdf_height = 595, 842 # A4 fallback
            
    scale_x = pdf_width / max_x if max_x > pdf_width else 1.0
    scale_y = pdf_height / max_y if max_y > pdf_height else 1.0
    bbox_scale = (scale_x, scale_y)
    logger.info(f"Scale: x={scale_x:.3f}, y={scale_y:.3f}")
    
    # 4. 执行字体匹配
    logger.info("Matching title fonts...")
    matched_count = 0
    for block in title_blocks:
        font_match = match_title_font(
            block.content,
            all_chars,
            page_idx=block.page_idx,
            bbox=block.bbox,
            bbox_scale=bbox_scale,
            margin=margin,
            line_tolerance=line_tolerance
        )
        
        if font_match["font_size"] is not None:
            matched_count += 1
            
        block.font_size = font_match["font_size"]
        block.font_name = font_match["font_name"]
        block.is_bold = font_match["is_bold"]
        block.is_centered = font_match.get("is_centered", False)
        block.level = block.text_level
        
    logger.info(f"Matched {matched_count} / {len(title_blocks)} titles")
    
    # 5. 执行聚类
    logger.info("Classifying title levels...")
    classify_result = classify_title_levels(title_blocks, max_levels=6)
    
    return blocks, classify_result


def save_docindex_json(
    blocks: List[MineruBlock],
    classify_result: Dict[str, Any],
    output_path: str
) -> None:
    """
    保存 docindex 结果为 JSON 文件
    
    Args:
        blocks: MineruBlock 列表（已含 cluster_level）
        classify_result: 聚类结果
        output_path: 输出 JSON 路径
    """
    import json
    from pathlib import Path
    
    title_blocks = [b for b in blocks if b.text_level is not None]
    # 从聚类结果获取 block_id -> cluster_level 映射
    block_levels = classify_result.get("block_levels", {})
    
    # 构建输出数据
    title_list = []
    for block in title_blocks:
        # 使用聚类结果的 cluster_level，而非 MinerU 原始的 text_level
        if not block.font_size:
            cluster_level = -1
            font_str = "unknown"
        else:
            cluster_level = block_levels.get(block.block_id, block.text_level)
            font_str = f"{block.font_size:.1f}pt"

        title_list.append({
            "page": block.page_idx + 1,  # 转为 1-indexed
            "title": block.content,
            "cluster_level": cluster_level,
            "font": font_str,
            "is_centered": block.is_centered,
        })
    
    data = {
        "summary": {
            "total_titles": len(title_blocks),
            "clusters": classify_result.get("clusters", []),
        },
        "title_list": title_list,
    }
    
    # 保存
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Saved docindex JSON: {output_path}")


def build_and_save_docindex(
    json_path: str,
    pdf_path: str,
    output_path: str = None
) -> str:
    """
    构建并保存 docindex，供 AMA 预处理流程调用
    
    Args:
        json_path: MinerU content_list.json 路径
        pdf_path: 原始 PDF 路径
        output_path: 输出路径（默认与 json_path 同目录，后缀改为 _docindex.json）
        
    Returns:
        输出文件路径
    """
    from pathlib import Path
    
    # 确定输出路径
    if output_path is None:
        json_path_obj = Path(json_path)
        base_name = json_path_obj.stem.replace("_content_list", "")
        output_path = str(json_path_obj.parent / f"{base_name}_docindex.json")
    
    # 构建索引
    blocks, classify_result = build_doc_index(json_path, pdf_path)
    
    # 保存
    save_docindex_json(blocks, classify_result, output_path)
    
    return output_path
