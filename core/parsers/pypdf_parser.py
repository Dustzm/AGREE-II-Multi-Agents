"""
PyPDF 简单解析器

使用 pypdf 库直接从 PDF 提取文本
适用于文本结构简单的 PDF 文档
"""
import logging
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PyPDFParser:
    """
    PyPDF 简单 PDF 解析器
    
    使用 pypdf 库直接提取 PDF 文本内容
    优点：简单快速，无需外部服务
    缺点：不支持复杂排版、表格识别较弱
    """
    
    def __init__(self):
        """初始化解析器"""
        pass
    
    def parse_pdf(self, pdf_path: str, output_dir: Optional[str] = None) -> dict:
        """
        解析 PDF 文件
        
        Args:
            pdf_path: PDF 文件路径
            output_dir: 输出目录 (可选，如果提供则保存 md 文件)
            
        Returns:
            dict: {
                "status": "success/error",
                "content": str,  # Markdown 格式的文本内容
                "pages": int,    # 页数
                "output_path": str,  # 输出文件路径 (如果指定了 output_dir)
                "error": str
            }
        """
        try:
            path = Path(pdf_path)
            if not path.exists():
                return {
                    "status": "error",
                    "content": "",
                    "pages": 0,
                    "output_path": "",
                    "error": f"文件不存在: {pdf_path}"
                }
            
            if not path.suffix.lower() == ".pdf":
                return {
                    "status": "error",
                    "content": "",
                    "pages": 0,
                    "output_path": "",
                    "error": f"不是 PDF 文件: {pdf_path}"
                }
            
            # 读取 PDF
            reader = PdfReader(path)
            pages_count = len(reader.pages)
            
            # 提取文本
            text_parts = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"<!-- Page {i + 1} -->\n\n{page_text}")
            
            content = "\n\n---\n\n".join(text_parts)
            
            # 添加文档信息头
            markdown_content = f"""# {path.stem}

**来源**: {path.name}  
**页数**: {pages_count}  
**解析器**: PyPDF

---

{content}
"""
            
            output_path = ""
            
            # 如果指定了输出目录，保存 md 文件
            if output_dir:
                output_dir_path = Path(output_dir)
                output_dir_path.mkdir(parents=True, exist_ok=True)
                output_file = output_dir_path / f"{path.stem}.md"
                output_file.write_text(markdown_content, encoding="utf-8")
                output_path = str(output_file)
                logger.info(f"PDF 已解析并保存: {output_path}")
            
            logger.info(f"PDF 解析成功: {pdf_path} ({pages_count} 页)")
            
            return {
                "status": "success",
                "content": markdown_content,
                "pages": pages_count,
                "output_path": output_path,
                "error": ""
            }
            
        except Exception as e:
            logger.error(f"PDF 解析失败: {e}")
            return {
                "status": "error",
                "content": "",
                "pages": 0,
                "output_path": "",
                "error": str(e)
            }
    
    def parse_pdf_to_file(self, pdf_path: str, output_path: str) -> dict:
        """
        解析 PDF 并保存到指定文件
        
        Args:
            pdf_path: PDF 文件路径
            output_path: 输出 Markdown 文件路径
            
        Returns:
            dict: {"status": "success/error", "pages": int, "error": str}
        """
        result = self.parse_pdf(pdf_path)
        
        if result["status"] == "error":
            return {
                "status": "error",
                "pages": 0,
                "error": result["error"]
            }
        
        try:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(result["content"], encoding="utf-8")
            
            logger.info(f"已保存到: {output_path}")
            return {
                "status": "success",
                "pages": result["pages"],
                "error": ""
            }
        except Exception as e:
            return {
                "status": "error",
                "pages": 0,
                "error": str(e)
            }
