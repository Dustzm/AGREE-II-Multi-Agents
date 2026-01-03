# 图片工具模块
import os
from pathlib import Path
from typing import List, Tuple

from PIL import Image
from loguru import logger


def crop_and_save_image(
    page_image: Image.Image,
    bbox: List[float],
    output_path: str,
    page_size: Tuple[int, int]
) -> str:
    """
    根据归一化 bbox 裁剪图片并保存
    
    Args:
        page_image: 页面图片
        bbox: 归一化坐标 [x1, y1, x2, y2] (0-1)
        output_path: 输出路径
        page_size: 页面尺寸 (width, height)
        
    Returns:
        保存的图片路径
    """
    width, height = page_size
    
    # 归一化坐标转像素坐标
    x1 = int(bbox[0] * width)
    y1 = int(bbox[1] * height)
    x2 = int(bbox[2] * width)
    y2 = int(bbox[3] * height)
    
    # 确保坐标有效
    x1, x2 = max(0, min(x1, x2)), min(width, max(x1, x2))
    y1, y2 = max(0, min(y1, y2)), min(height, max(y1, y2))
    
    if x2 <= x1 or y2 <= y1:
        logger.warning(f"无效的裁剪区域: bbox={bbox}, 转换后=({x1},{y1},{x2},{y2})")
        return ""
    
    # 裁剪并保存
    cropped = page_image.crop((x1, y1, x2, y2))
    
    # 确保目录存在
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    cropped.save(output_path, "JPEG", quality=95)
    logger.debug(f"保存图片: {output_path}, 尺寸: {cropped.size}")
    
    return output_path


def process_images_in_blocks(
    page_image: Image.Image,
    blocks: list,
    page_idx: int,
    images_dir: str,
    page_size: Tuple[int, int]
) -> None:
    """
    处理页面中的所有图片块，裁剪并保存
    
    Args:
        page_image: 页面图片
        blocks: 块列表 (会被原地修改添加 image_path)
        page_idx: 页码
        images_dir: 图片输出目录
        page_size: 页面尺寸
    """
    for block in blocks:
        if block.type == "image" and block.bbox:
            filename = f"{block.block_id}.jpg"
            output_path = os.path.join(images_dir, filename)
            
            saved_path = crop_and_save_image(
                page_image,
                block.bbox,
                output_path,
                page_size
            )
            
            if saved_path:
                # 使用相对路径
                block.image_path = f"images/{filename}"
