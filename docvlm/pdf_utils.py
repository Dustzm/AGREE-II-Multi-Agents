# PDF 工具模块
import io
from pathlib import Path
from typing import List, Tuple

from PIL import Image
import pypdfium2 as pdfium
from loguru import logger


def pdf_to_images(pdf_path: str, dpi: int = 200) -> Tuple[List[Image.Image], List[Tuple[int, int]]]:
    """
    将 PDF 转换为图片列表
    
    Args:
        pdf_path: PDF 文件路径
        dpi: 图片 DPI
        
    Returns:
        (images, page_sizes): 图片列表和对应的页面尺寸列表
    """
    logger.info(f"开始转换 PDF: {pdf_path}, DPI={dpi}")
    
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
    
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    
    return pdf_bytes_to_images(pdf_bytes, dpi)


def pdf_bytes_to_images(pdf_bytes: bytes, dpi: int = 200) -> Tuple[List[Image.Image], List[Tuple[int, int]]]:
    """
    将 PDF bytes 转换为图片列表
    
    Args:
        pdf_bytes: PDF 二进制数据
        dpi: 图片 DPI
        
    Returns:
        (images, page_sizes): 图片列表和对应的页面尺寸列表
    """
    pdf_doc = pdfium.PdfDocument(pdf_bytes)
    images = []
    page_sizes = []
    
    for page_idx in range(len(pdf_doc)):
        page = pdf_doc[page_idx]
        
        # 计算缩放比例
        # pdfium 默认 72 DPI，需要按比例缩放
        scale = dpi / 72.0
        
        # 渲染页面为位图
        bitmap = page.render(scale=scale)
        pil_image = bitmap.to_pil()
        
        images.append(pil_image)
        page_sizes.append((pil_image.width, pil_image.height))
        
        logger.debug(f"转换第 {page_idx + 1}/{len(pdf_doc)} 页, 尺寸: {pil_image.size}")
    
    pdf_doc.close()
    logger.info(f"PDF 转换完成, 共 {len(images)} 页")
    
    return images, page_sizes


def image_to_base64(image: Image.Image, format: str = "PNG") -> str:
    """
    将 PIL Image 转换为 base64 字符串
    
    Args:
        image: PIL Image 对象
        format: 图片格式
        
    Returns:
        base64 编码的图片字符串
    """
    import base64
    
    buffered = io.BytesIO()
    image.save(buffered, format=format)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def preprocess_image_for_vlm(
    image: Image.Image,
    max_edge: int = 1500,
    quality: int = 85
) -> Image.Image:
    """
    预处理图片用于 VLM 推理：缩放 + 转 RGB
    
    Args:
        image: 原始图片
        max_edge: 长边最大像素
        quality: JPEG 压缩质量（用于日志）
        
    Returns:
        预处理后的图片
    """
    original_size = image.size
    
    # 等比例缩放
    width, height = image.size
    if max(width, height) > max_edge:
        scale = max_edge / max(width, height)
        new_size = (int(width * scale), int(height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
        logger.debug(f"图片缩放: {original_size} → {image.size}")
    
    # 转 RGB（JPEG 不支持透明通道）
    if image.mode != "RGB":
        image = image.convert("RGB")
    
    return image

