#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

@dataclass
class MineruBlock:
    """MinerU 提取的内容块"""
    block_id: str
    type: str
    content: str
    bbox: List[float]
    page_idx: int
    text_level: int = None  # MinerU 标注的标题层级
    
    # 字体属性
    font_size: float = None
    font_name: str = None
    is_bold: bool = None
    is_centered: bool = None
    
    # 聚类结果
    level: int = None  # 用于兼容 title_classifier


def load_mineru_content_list(json_path: str) -> List[MineruBlock]:
    """从 MinerU content_list.json 加载内容块"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    blocks = []
    block_idx = 0
    
    for item in data:
        item_type = item.get("type", "text")
        
        # 只处理有 text_level 的内容（视为标题）
        # 以及其他类型用于统计
        text_level = item.get("text_level")
        
        # 获取文本内容
        if item_type == "text":
            content = item.get("text", "")
        elif item_type == "list":
            list_items = item.get("list_items", [])
            content = list_items[0][:50] if list_items else ""
        elif item_type == "table":
            content = item.get("table_caption", [""])[0] if item.get("table_caption") else "[Table]"
        else:
            content = item.get("text", "")
        
        page_idx = item.get("page_idx", 0)
        bbox = item.get("bbox", [])
        
        block = MineruBlock(
            block_id=f"p{page_idx}_b{block_idx}",
            type="title" if text_level else item_type,
            content=content,
            bbox=bbox,
            page_idx=page_idx,
            text_level=text_level,
        )
        blocks.append(block)
        block_idx += 1
    
    return blocks
