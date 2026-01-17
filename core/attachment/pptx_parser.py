"""
PPTX 附件解析器

使用 python-pptx 提取 PowerPoint 幻灯片内容
"""
import logging
from pathlib import Path
from typing import List

from .parser import AttachmentParser, ParsedContent

logger = logging.getLogger(__name__)


class PPTXAttachmentParser(AttachmentParser):
    """
    PowerPoint 解析器
    
    提取幻灯片标题、正文文本和备注内容
    """
    
    @property
    def supported_extensions(self) -> List[str]:
        return ["pptx", "ppt"]
    
    async def parse(self, file_path: Path, **kwargs) -> List[ParsedContent]:
        """
        解析 PowerPoint 文件
        
        Args:
            file_path: PPTX 文件路径
            
        Returns:
            解析后的内容列表
        """
        try:
            from pptx import Presentation
        except ImportError:
            logger.error("python-pptx 未安装，请运行: pip install python-pptx")
            return []
        
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"PPTX 文件不存在: {file_path}")
            return []
        
        # 检查是否为旧版 .ppt 格式
        if file_path.suffix.lower() == ".ppt":
            logger.warning(f"不支持旧版 .ppt 格式，请转换为 .pptx: {file_path}")
            return [ParsedContent(
                title=f"[附件] {file_path.stem}",
                content=f"[注意] 旧版 .ppt 格式无法解析，请转换为 .pptx 格式",
                content_type="pptx_unsupported",
            )]
        
        try:
            prs = Presentation(str(file_path))
            all_texts = []
            
            for slide_idx, slide in enumerate(prs.slides, start=1):
                slide_content = self._extract_slide_content(slide, slide_idx)
                if slide_content:
                    all_texts.append(slide_content)
            
            # 合并所有幻灯片内容
            content = "\n\n---\n\n".join(all_texts)
            
            logger.info(f"PPTX 文件解析完成: {file_path.name}, {len(prs.slides)} 张幻灯片, {len(content)} 字符")
            
            return [ParsedContent(
                title=f"[附件] {file_path.stem}",
                content=content,
                content_type="pptx_document",
                metadata={
                    "slide_count": len(prs.slides),
                    "source_file": str(file_path),
                }
            )]
            
        except Exception as e:
            logger.error(f"PPTX 文件解析失败: {e}", exc_info=True)
            return []
    
    def _extract_slide_content(self, slide, slide_idx: int) -> str:
        """
        提取单张幻灯片的内容
        
        Args:
            slide: python-pptx slide 对象
            slide_idx: 幻灯片序号 (1-indexed)
            
        Returns:
            格式化的幻灯片内容字符串
        """
        parts = [f"### 幻灯片 {slide_idx}"]
        
        # 提取所有文本框内容
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        texts.append(text)
            
            # 提取表格
            if shape.has_table:
                table_md = self._table_to_markdown(shape.table)
                if table_md:
                    texts.append(table_md)
        
        if texts:
            parts.append("\n".join(texts))
        
        # 提取备注
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes_text = slide.notes_slide.notes_text_frame.text.strip()
            if notes_text:
                parts.append(f"\n**备注:** {notes_text}")
        
        # 如果幻灯片没有任何内容，返回空
        if len(parts) == 1:
            return ""
        
        return "\n\n".join(parts)
    
    def _table_to_markdown(self, table) -> str:
        """
        将 PPTX 表格转换为 Markdown 格式
        
        Args:
            table: python-pptx table 对象
            
        Returns:
            Markdown 格式的表格字符串
        """
        rows = []
        col_count = 0
        
        for row in table.rows:
            cells = []
            for cell in row.cells:
                # 替换换行符为空格
                text = cell.text.replace("\n", " ").strip()
                cells.append(text)
            rows.append("| " + " | ".join(cells) + " |")
            
            if col_count == 0:
                col_count = len(cells)
        
        if len(rows) > 1 and col_count > 0:
            # 在第一行后插入分隔行
            header_sep = "| " + " | ".join(["---"] * col_count) + " |"
            rows.insert(1, header_sep)
        
        return "\n".join(rows)
