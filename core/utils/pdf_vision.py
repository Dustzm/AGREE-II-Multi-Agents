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
        images = []
        with fitz.open(pdf_path) as doc:
            for p_idx in page_indices:
                if p_idx < 0 or p_idx >= len(doc):
                    continue

                page = doc.load_page(p_idx)
                pix = page.get_pixmap(dpi=dpi)

                # Pixmap 返回可能是 RGB 或 RGBA，统一转为 RGB 以备保存为 JPEG
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                pix = None

                if img.height > max_height_per_page:
                    ratio = max_height_per_page / img.height
                    new_size = (int(img.width * ratio), int(img.height * ratio))
                    resized = img.resize(new_size, Image.Resampling.LANCZOS)
                    img.close()
                    img = resized

                images.append(img)

        if not images:
            return ""

        total_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)

        stitched_image = Image.new("RGB", (total_width, total_height), (255, 255, 255))
        try:
            current_y = 0
            for img in images:
                x_offset = (total_width - img.width) // 2
                stitched_image.paste(img, (x_offset, current_y))
                current_y += img.height

            with io.BytesIO() as buffer:
                stitched_image.save(buffer, format="JPEG", quality=quality)
                b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
        finally:
            stitched_image.close()
            for img in images:
                img.close()

        return b64_str
        
    except Exception as e:
        logger.error(f"Failed to stitch PDF pages into base64 image: {e}")
        return ""
