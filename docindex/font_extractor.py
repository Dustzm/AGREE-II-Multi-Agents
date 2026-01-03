# 字体信息提取模块 (简化版)
# 使用 pdfplumber 从 PDF 源文件提取字体信息

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import re
import pdfplumber
from loguru import logger


@dataclass
class CharInfo:
    """字符信息"""
    text: str           # 字符文本
    size: float         # 字体大小 (pt) - PDF 元数据
    fontname: str       # 字体名称
    top: float          # 上边界
    x0: float           # 左边界
    width: float        # 字符宽度
    page_width: float   # 页面宽度
    page_idx: int       # 页码索引


def clean_latex_math(text: str) -> str:
    """
    清理文本中的 LaTeX 数学公式标记
    只处理 $ 或 $$ 包裹的内容
    """
    # 检测是否包含 $ 包裹的数学公式
    if '$' not in text:
        return text
    
    # 移除 LaTeX 命令如 \mathrm{}, \text{}, _{}, ^{}
    cleaned = text
    # 移除 $...$ 中的 LaTeX 命令
    cleaned = re.sub(r'\$+', '', cleaned)           # 移除 $ 符号
    cleaned = re.sub(r'\\mathrm\{([^}]*)\}', r'\1', cleaned)  # \mathrm{X} -> X
    cleaned = re.sub(r'\\text\{([^}]*)\}', r'\1', cleaned)    # \text{X} -> X
    cleaned = re.sub(r'_\{([^}]*)\}', r'\1', cleaned)         # _{X} -> X
    cleaned = re.sub(r'\^\{([^}]*)\}', r'\1', cleaned)        # ^{X} -> X
    cleaned = re.sub(r'[_^]', '', cleaned)          # 移除单独的 _ 和 ^
    cleaned = re.sub(r'\\[a-zA-Z]+', '', cleaned)   # 移除其他 \命令
    
    return cleaned


def normalize_text(text: str) -> str:
    """标准化文本：清理 LaTeX，去除空格、标点，转小写"""
    # 先清理 LaTeX 数学公式
    text = clean_latex_math(text)
    return re.sub(r'[^\w\u4e00-\u9fff]', '', text).lower()


def extract_chars_with_font(pdf_path: str) -> List[CharInfo]:
    """
    从 PDF 提取所有字符及其字体信息（使用元数据）
    
    Args:
        pdf_path: PDF 文件路径
        
    Returns:
        所有字符的信息列表
    """
    all_chars = []
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                for char in page.chars:
                    char_info = CharInfo(
                        text=char.get('text', ''),
                        size=char.get('size', 0),  # 直接使用 PDF 元数据
                        fontname=char.get('fontname', ''),
                        top=char.get('top', 0),
                        x0=char.get('x0', 0),
                        width=char.get('width', 0),
                        page_width=page.width,
                        page_idx=page_idx,
                    )
                    all_chars.append(char_info)
            
            logger.debug(f"从 PDF 提取字符: {len(all_chars)} 个")
    
    except Exception as e:
        logger.warning(f"无法从 PDF 提取字符: {e}")
    
    return all_chars


def match_title_font(
    title: str,
    chars: List[CharInfo],
    page_idx: Optional[int] = None,
    bbox: Optional[List[float]] = None,
    bbox_scale: Optional[tuple] = None,
    margin: float = 20.0,
    line_tolerance: Optional[float] = None
) -> Dict[str, Any]:
    """
    通过 bbox 定位 + 文本匹配获取标题的字体信息
    
    Args:
        title: 标题文本
        chars: PDF 中的所有字符
        page_idx: 限定在哪一页搜索
        bbox: MinerU 的 bbox [x0, y0, x1, y1]
        bbox_scale: (scale_x, scale_y) 坐标缩放比例
        margin: 区域扩大的边距 (pt)
        
    Returns:
        {'font_size': float, 'font_name': str, 'is_bold': bool, 'is_centered': bool}
    """
    if not title or not chars:
        return {"font_size": None, "font_name": None, "is_bold": None, "is_centered": False}
    
    search = normalize_text(title)
    
    # 尝试去除列表序号（如 "1.", "一、", "(1)" 等）
    # 这样可以锚定到标题文字本身，避免因序号位置偏移导致的问题
    # 常见序号格式：数字+点/顿号，中文数字+顿号，括号+数字
    # 先定义一个简单的移除逻辑，如果移除后剩余长度足够，就用移除后的
    clean_search = re.sub(r'^(\d+|[一二三四五六七八九十]+|[a-zA-Z])(\.|、)\s*', '', search)
    # 处理 (1) 这种括号形式因为 normalize 后括号没了变成了 1，比较难搞，暂时忽略
    # normalize_text 已经去除了标点，所以 "1. " 变成了 "1 " (空格也被去除了) -> "1"
    # 所以正则应该匹配 normalize 后的文本开头的数字/汉字
    
    # 更稳健的方式：分别尝试原始 title 去除序号后的 normalize 结果
    title_stripped = re.sub(r'^(\d+(\.\d+)*|[一二三四五六七八九十]+|[a-zA-Z])(\.|、|\s)\s*', '', title)
    search_stripped = normalize_text(title_stripped)
    
    # 如果去除序号后剩余文本足够长，优先使用去除后的
    target_search = search_stripped if len(search_stripped) >= 3 else search
    
    # 最小长度检查：
    # 纯英文/数字至少 3 个字符
    # 包含中文至少 2 个字符 (如 "推荐", "小结")
    min_len = 2 if re.search(r'[\u4e00-\u9fff]', target_search) else 3
    
    if len(target_search) < min_len:
        return {"font_size": None, "font_name": None, "is_bold": None, "is_centered": False}
    
    # 取前 10 个字符作为搜索前缀
    search_prefix = target_search[:min(10, len(target_search))]
    first_char = search_prefix[0]
    
    # 筛选指定页的字符
    page_chars = [c for c in chars if page_idx is None or c.page_idx == page_idx]
    
    # 如果提供了 bbox，进一步筛选区域内的字符
    if bbox and len(bbox) == 4 and bbox_scale:
        scale_x, scale_y = bbox_scale
        # 转换 MinerU bbox 到 PDF 坐标并扩大区域
        pdf_x0 = bbox[0] * scale_x - margin
        pdf_y0 = bbox[1] * scale_y - margin
        pdf_x1 = bbox[2] * scale_x + margin
        pdf_y1 = bbox[3] * scale_y + margin
        
        # 筛选在扩大区域内的字符
        page_chars = [
            c for c in page_chars
            if pdf_x0 <= c.x0 <= pdf_x1 and pdf_y0 <= c.top <= pdf_y1
        ]
    
    # 搜索首字符匹配
    for char in page_chars:
        if char.text.lower() != first_char:
            continue
        
        # 找同行字符
        # 自适应容差：如果是 None，则使用 0.8 * 字体大小
        # 这样小字号（如 7pt 表格）容差小（~5.6pt），避免合并行
        # 大字号（如 10.3pt 标题）容差大（~8.2pt），允许垂直偏差
        tolerance = line_tolerance if line_tolerance is not None else (char.size * 0.8)
        
        line_chars = [c for c in page_chars if abs(c.top - char.top) < tolerance]
        sorted_line = sorted(line_chars, key=lambda c: c.x0)
        
        # 拼接行文本
        line_text = ''.join([c.text for c in sorted_line])
        normalized_line = normalize_text(line_text)
        
        # 检查是否匹配
        matched = False
        
        # 1. 简单包含匹配
        if normalized_line.startswith(search_prefix) or search_prefix in normalized_line:
            matched = True
        
        # 2. CJK 容错匹配（处理 PDF 隐形水印/乱码插入字符）
        # 如果搜索词包含中文，且未匹配，尝试只比对中文字符
        if not matched and re.search(r'[\u4e00-\u9fff]', search_prefix):
            cjk_line = re.sub(r'[^\u4e00-\u9fff]', '', normalized_line)
            cjk_search = re.sub(r'[^\u4e00-\u9fff]', '', search_prefix)
            
            # 方法 2a: 包含匹配
            if len(cjk_search) >= 2 and cjk_search in cjk_line:
                matched = True
            # 方法 2b: 子序列匹配 (处理由 CJK 字符组成的干扰码，如 "参摇考摇文摇献")
            elif len(cjk_search) >= 2:
                it = iter(cjk_line)
                if all(char in it for char in cjk_search):
                    matched = True
        
        if matched:
            # 找到匹配，返回首字符的字体信息
            is_bold = False
            if char.fontname:
                bold_indicators = ["Bold", "bold", "BOLD", "Heavy", "Black", "Semibold", "Demi"]
                is_bold = any(ind in char.fontname for ind in bold_indicators)
            
            # 计算是否居中
            # 计算这一行的左右边界
            min_x0 = min(c.x0 for c in sorted_line)
            max_x1 = max(c.x0 + c.width for c in sorted_line)
            # 严格居中判定逻辑 (Strict Centering)
            # 1. 对称检测: 左边距 ≈ 右边距
            # 2. 满行检测: 如果占宽 > 90% 栏宽，则认为是正文，不视为居中标题
            
            page_width = char.page_width
            threshold = 60.0 # 放宽阈值适配实际文档 (Diff ~51.05)
            col_width_half = page_width * 0.5
            
            # --- 辅助函数：检查是否在指定区域内居中 ---
            def check_centered(region_start, region_end):
                region_width = region_end - region_start
                line_width = max_x1 - min_x0
                
                # 满行检测：如果文字占据了区域宽度的 80% 以上，认为是正文满行，不是标题居中
                if line_width > region_width * 0.8:
                    return False
                
                left_margin = min_x0 - region_start
                right_margin = region_end - max_x1
                
                # 对称性检测：左右边距差值小于阈值
                return abs(left_margin - right_margin) < threshold
            
            # 1. 页面居中 (跨栏标题)
            is_page_centered = check_centered(0, page_width)
            
            # 2. 左栏居中
            is_left_col_centered = check_centered(0, col_width_half)
            
            # 3. 右栏居中
            is_right_col_centered = check_centered(col_width_half, page_width)
            
            is_centered = is_page_centered or is_left_col_centered or is_right_col_centered
            
            # Pattern Override: 编号子标题不应被视为居中大标题
            # 常见模式: (一), （一）, 一、, 1., 1.1 等 (re 已在文件顶部导入)
            if is_centered:
                numbered_pattern = r'^[（\(]?[一二三四五六七八九十\d]+[）\)、\.]'
                if re.match(numbered_pattern, title.strip()):
                    is_centered = False
            
            return {
                "font_size": round(char.size, 1),
                "font_name": char.fontname,
                "is_bold": is_bold,
                "is_centered": is_centered,
            }
    
    return {"font_size": None, "font_name": None, "is_bold": None, "is_centered": False}


# 兼容旧接口
def extract_words_with_font(pdf_path: str) -> List[CharInfo]:
    """兼容旧接口，实际调用 extract_chars_with_font"""
    return extract_chars_with_font(pdf_path)
