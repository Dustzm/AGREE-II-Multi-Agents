# 字体信息提取模块
# 使用 pdfplumber 从 PDF 源文件提取字体信息

from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter
import pdfplumber
from loguru import logger


@dataclass
class CharInfo:
    """字符信息"""
    text: str
    x0: float           # 左边界 (PDF 点坐标)
    top: float          # 上边界 (PDF 点坐标)
    x1: float           # 右边界
    bottom: float       # 下边界
    size: float         # 字体大小 (pt)
    fontname: str       # 字体名称


@dataclass
class PageFontInfo:
    """页面字体信息"""
    page_idx: int
    page_width: float   # PDF 页面宽度 (点)
    page_height: float  # PDF 页面高度 (点)
    chars: List[CharInfo]


def extract_font_info(pdf_path: str) -> List[PageFontInfo]:
    """
    提取 PDF 每页的字符和字体信息
    
    Args:
        pdf_path: PDF 文件路径
        
    Returns:
        每页的字体信息列表
    """
    result = []
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                chars = []
                page_width = page.width
                page_height = page.height
                
                for char in page.chars:
                    chars.append(CharInfo(
                        text=char.get("text", ""),
                        x0=char.get("x0", 0),
                        top=char.get("top", 0),
                        x1=char.get("x1", 0),
                        bottom=char.get("bottom", 0),
                        size=char.get("size", 0),
                        fontname=char.get("fontname", ""),
                    ))
                
                result.append(PageFontInfo(
                    page_idx=page_idx,
                    page_width=page_width,
                    page_height=page_height,
                    chars=chars,
                ))
                
        logger.debug(f"从 PDF 提取字体信息: {len(result)} 页")
        
    except Exception as e:
        logger.warning(f"无法从 PDF 提取字体信息: {e}")
        # 返回空列表，调用方需要处理 fallback
    
    return result


def match_block_font(
    block_bbox: List[float],
    page_font_info: PageFontInfo,
    image_size: Optional[Tuple[int, int]] = None,  # 新增：渲染图片尺寸 (width, height)
    margin: float = 0.02  # 容差（PDF 点单位）
) -> Dict[str, Any]:
    """
    通过坐标匹配获取 block 对应的字体信息
    
    Args:
        block_bbox: Block 的 bbox [x1, y1, x2, y2]，可以是归一化坐标(0-1)或像素坐标
        page_font_info: 页面字体信息
        image_size: 渲染图片尺寸 (width, height)，用于像素坐标转 PDF 坐标
        margin: 匹配容差（PDF 点单位）
        
    Returns:
        {'font_size': float, 'font_name': str, 'is_bold': bool}
    """
    if not block_bbox or len(block_bbox) != 4:
        return {"font_size": None, "font_name": None, "is_bold": None}
    
    bx1, by1, bx2, by2 = block_bbox
    pdf_width = page_font_info.page_width
    pdf_height = page_font_info.page_height
    
    # 自动检测是否为像素坐标（如果任意值 > 1.5，认为是像素坐标）
    is_pixel_coords = max(bx1, by1, bx2, by2) > 1.5
    
    if is_pixel_coords:
        if image_size:
            # 像素坐标转 PDF 坐标（DPI 转换）
            img_width, img_height = image_size
            scale_x = pdf_width / img_width
            scale_y = pdf_height / img_height
            pdf_x1 = bx1 * scale_x - margin
            pdf_y1 = by1 * scale_y - margin
            pdf_x2 = bx2 * scale_x + margin
            pdf_y2 = by2 * scale_y + margin
        else:
            # 无法转换，直接使用（可能不准确）
            pdf_x1 = bx1 - margin
            pdf_y1 = by1 - margin
            pdf_x2 = bx2 + margin
            pdf_y2 = by2 + margin
    else:
        # 归一化坐标转换为 PDF 点坐标
        pdf_x1 = bx1 * pdf_width - margin
        pdf_y1 = by1 * pdf_height - margin
        pdf_x2 = bx2 * pdf_width + margin
        pdf_y2 = by2 * pdf_height + margin
    
    # 找到落在该区域内的字符
    matched_chars: List[CharInfo] = []
    for char in page_font_info.chars:
        # 字符中心点在 bbox 内
        cx = (char.x0 + char.x1) / 2
        cy = (char.top + char.bottom) / 2
        if pdf_x1 <= cx <= pdf_x2 and pdf_y1 <= cy <= pdf_y2:
            matched_chars.append(char)
    
    if not matched_chars:
        return {"font_size": None, "font_name": None, "is_bold": None}
    
    # 取首行字符的字体信息（按 top 排序，取最小 top 值的字符）
    # 这样可以避免 bbox 包含多行时取到正文而非标题
    min_top = min(c.top for c in matched_chars)
    first_line_chars = [c for c in matched_chars if abs(c.top - min_top) < 3]  # 同一行容差 3pt
    
    if first_line_chars:
        # 取首行的众数
        sizes = [round(c.size, 1) for c in first_line_chars]
        fontnames = [c.fontname for c in first_line_chars]
    else:
        sizes = [round(c.size, 1) for c in matched_chars]
        fontnames = [c.fontname for c in matched_chars]
    
    font_size = Counter(sizes).most_common(1)[0][0] if sizes else None
    font_name = Counter(fontnames).most_common(1)[0][0] if fontnames else None
    
    # 判断是否粗体
    is_bold = False
    if font_name:
        # 常见的粗体标识
        bold_indicators = ["Bold", "bold", "BOLD", "Heavy", "Black", "Semibold", "Demi"]
        is_bold = any(ind in font_name for ind in bold_indicators)
    
    return {
        "font_size": font_size,
        "font_name": font_name,
        "is_bold": is_bold,
    }


def enrich_blocks_with_font(
    blocks: List[Any],
    page_font_info: Optional[PageFontInfo]
) -> None:
    """
    为 blocks 填充字体信息（原地修改）
    
    Args:
        blocks: Block 对象列表
        page_font_info: 页面字体信息，如果为 None 则跳过
    """
    if page_font_info is None:
        return
    
    for block in blocks:
        font_info = match_block_font(block.bbox, page_font_info)
        block.font_size = font_info["font_size"]
        block.font_name = font_info["font_name"]
        block.is_bold = font_info["is_bold"]


def normalize_text(text: str) -> str:
    """
    标准化文本用于匹配：去除空格、标点、转小写
    """
    import re
    # 只保留字母、数字和中文
    text = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
    return text.lower()


def match_font_by_text(
    text: str,
    page_font_info: PageFontInfo,
    min_match_ratio: float = 0.3
) -> Dict[str, Any]:
    """
    通过文本内容匹配获取字体信息
    
    策略：搜索文本首字符，然后用容差找同行字符进行匹配
    
    Args:
        text: 要匹配的文本内容
        page_font_info: 页面字体信息
        min_match_ratio: 最小匹配比例
        
    Returns:
        {'font_size': float, 'font_name': str, 'is_bold': bool}
    """
    if not text or not page_font_info.chars:
        return {"font_size": None, "font_name": None, "is_bold": None}
    
    # 标准化搜索文本
    search_text = normalize_text(text)
    if len(search_text) < 3:
        return {"font_size": None, "font_name": None, "is_bold": None}
    
    # 搜索前缀（取前 10 个字符）
    search_prefix = search_text[:min(10, len(search_text))]
    first_char = search_text[0]
    
    best_match = None
    best_ratio = 0
    
    # 遍历所有字符，找到可能的起始位置
    for char in page_font_info.chars:
        if char.text.lower() != first_char:
            continue
        
        # 找到首字母匹配，获取同行的所有字符（用 5pt 容差）
        line_chars = [c for c in page_font_info.chars if abs(c.top - char.top) < 5]
        sorted_chars = sorted(line_chars, key=lambda c: c.x0)
        
        # 找到首字符在行中的位置
        start_idx = next((i for i, c in enumerate(sorted_chars) if c.text.lower() == first_char), 0)
        
        # 从首字符位置开始拼接文本
        text_from_start = ''.join([c.text for c in sorted_chars[start_idx:]])
        normalized_from_start = normalize_text(text_from_start)
        
        # 检查匹配：要求从首字符开始的文本以搜索前缀开头
        if normalized_from_start.startswith(search_prefix[:8]):
            # 计算匹配分数：字体大小 * 匹配长度
            match_score = char.size * len(search_prefix)
            
            if match_score > best_ratio:  # 这里 best_ratio 用作 best_score
                best_ratio = match_score
                best_match = sorted_chars[start_idx:start_idx+15]
    
    if best_match is None or best_ratio < min_match_ratio:
        return {"font_size": None, "font_name": None, "is_bold": None}
    
    # 取首字符的字体信息（而非众数，以处理 drop cap 样式）
    font_size = round(best_match[0].size, 1) if best_match else None
    font_name = best_match[0].fontname if best_match else None
    
    # 判断是否粗体
    is_bold = False
    if font_name:
        bold_indicators = ["Bold", "bold", "BOLD", "Heavy", "Black", "Semibold", "Demi"]
        is_bold = any(ind in font_name for ind in bold_indicators)
    
    return {
        "font_size": font_size,
        "font_name": font_name,
        "is_bold": is_bold,
    }
