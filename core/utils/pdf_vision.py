import base64
import logging
import io

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from PIL import Image
except ImportError:
    Image = None

logger = logging.getLogger(__name__)

def get_stitched_pdf_pages_as_base64(
    pdf_path: str, 
    page_indices: list[int], 
    dpi: int = 180, 
    quality: int = 90,
    max_height_per_page: int = 1500
) -> str:
    """
    提取选定的 PDF 页面，使用 Pillow 垂直拼接为一张单图，
    并进行 Base64 编码 (JPEG 格式) 返回，以突破单图请求限制并控制显存。
    
    Args:
        pdf_path: PDF 文件的路径
        page_indices: 需要提取的页码列表 (0-indexed)
        dpi: 渲染分辨率 (不宜过高，避免打爆显存，100-150 足够看清通用排版)
        quality: JPEG 压缩质量 (0-100)
        max_height_per_page: 每页最大高度限制，超长进行缩放
        
    Returns:
        拼接后的 base64 jpeg 字符串。若失败返回空字符串。
    """
    if not fitz or not Image:
        logger.error("PyMuPDF (fitz) or Pillow (PIL) is not installed. Both are required for vision extraction.")
        return ""
        
    if not page_indices:
        return ""
    
    if not pdf_path:
        logger.error("pdf_path is empty, cannot extract pages.")
        return ""
        
    try:
        doc = fitz.open(pdf_path)
        images = []
        
        for p_idx in page_indices:
            if p_idx < 0 or p_idx >= len(doc):
                continue
                
            page = doc.load_page(p_idx)
            # 采用恰当的 DPI 进行栅格化
            pix = page.get_pixmap(dpi=dpi)
            
            # 转换为 PIL Image 以便拼接
            # Pixmap 返回可能是 RGB 或 RGBA，统一转为 RGB 以备保存为 JPEG
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # 如果单张图片高度失控过大，等比缩放
            if img.height > max_height_per_page:
                ratio = max_height_per_page / img.height
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                
            images.append(img)
            
        doc.close()
        
        if not images:
            return ""
            
        # 计算拼接后的总宽高
        total_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)
        
        # 创建空白长图底板
        stitched_image = Image.new("RGB", (total_width, total_height), (255, 255, 255))
        
        # 逐个贴入底板
        current_y = 0
        for img in images:
            # 居中贴或者靠左贴皆可，这里采用居中
            x_offset = (total_width - img.width) // 2
            stitched_image.paste(img, (x_offset, current_y))
            current_y += img.height
            
        # 存入 BytesIO 并转 Base64，大幅降低 Quality 有助于减少 Payload 开销
        buffer = io.BytesIO()
        stitched_image.save(buffer, format="JPEG", quality=quality)
        b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
        
        return b64_str
        
    except Exception as e:
        logger.error(f"Failed to stitch PDF pages into base64 image: {e}")
        return ""
