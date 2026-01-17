"""
Word 附件解析器

使用 python-docx 提取 Word 文档内容
"""
import logging
from pathlib import Path
from typing import List

from .parser import AttachmentParser, ParsedContent

logger = logging.getLogger(__name__)


class WordAttachmentParser(AttachmentParser):
    """
    Word 文档解析器
    
    提取段落文本和表格（转为 Markdown 格式）
    """
    
    @property
    def supported_extensions(self) -> List[str]:
        return ["docx"]
    
    async def parse(self, file_path: Path, **kwargs) -> List[ParsedContent]:
        """
        解析 Word 文档
        
        Args:
            file_path: Word 文件路径
            
        Returns:
            解析后的内容列表
        """
        try:
            from docx import Document
        except ImportError:
            logger.error("python-docx 未安装，请运行: pip install python-docx")
            return []
        
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"Word 文件不存在: {file_path}")
            return []
        
        try:
            doc = Document(str(file_path))
            texts = []
            
            # 提取段落
            for para in doc.paragraphs:
                text = para.text.strip()
                if text:
                    texts.append(text)
            
            # 提取表格（转为 Markdown）
            for table in doc.tables:
                md_table = self._table_to_markdown(table)
                if md_table:
                    texts.append(md_table)
            
            content = "\n\n".join(texts)
            
            logger.info(f"Word 文档解析完成: {file_path.name}, {len(content)} 字符")
            
            return [ParsedContent(
                title=f"[附件] {file_path.stem}",
                content=content,
                content_type="word_document",
            )]
            
        except Exception as e:
            logger.error(f"Word 文档解析失败: {e}", exc_info=True)
            return []
    
    def _table_to_markdown(self, table) -> str:
        """
        将 Word 表格转换为 Markdown 格式
        
        Args:
            table: docx 表格对象
            
        Returns:
            Markdown 格式的表格字符串
        """
        rows = []
        
        for row in table.rows:
            cells = []
            for cell in row.cells:
                # 替换换行符为空格
                text = cell.text.replace("\n", " ").strip()
                cells.append(text)
            rows.append("| " + " | ".join(cells) + " |")
        
        if len(rows) > 1:
            # 在第一行后插入分隔行
            col_count = len(table.rows[0].cells)
            header_sep = "| " + " | ".join(["---"] * col_count) + " |"
            rows.insert(1, header_sep)
        
        return "\n".join(rows)
