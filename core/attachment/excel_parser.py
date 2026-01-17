"""
Excel 附件解析器

使用 openpyxl 提取 Excel 表格内容
"""
import logging
from pathlib import Path
from typing import List

from .parser import AttachmentParser, ParsedContent

logger = logging.getLogger(__name__)


class ExcelAttachmentParser(AttachmentParser):
    """
    Excel 表格解析器
    
    将每个 sheet 转换为 Markdown 表格格式
    """
    
    @property
    def supported_extensions(self) -> List[str]:
        return ["xlsx"]
    
    async def parse(self, file_path: Path, **kwargs) -> List[ParsedContent]:
        """
        解析 Excel 文件
        
        Args:
            file_path: Excel 文件路径
            
        Returns:
            解析后的内容列表（每个 sheet 一个 ParsedContent）
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            logger.error("openpyxl 未安装，请运行: pip install openpyxl")
            return []
        
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"Excel 文件不存在: {file_path}")
            return []
        
        try:
            wb = load_workbook(str(file_path), data_only=True)
            contents = []
            
            for sheet_name in wb.sheetnames:
                sheet = wb[sheet_name]
                md_table = self._sheet_to_markdown(sheet)
                
                if md_table.strip():
                    contents.append(ParsedContent(
                        title=f"[附件] {file_path.stem} - {sheet_name}",
                        content=md_table,
                        content_type="excel_sheet",
                    ))
            
            total_chars = sum(len(c.content) for c in contents)
            logger.info(f"Excel 文件解析完成: {file_path.name}, {len(contents)} 个 sheet, {total_chars} 字符")
            
            return contents
            
        except Exception as e:
            logger.error(f"Excel 文件解析失败: {e}", exc_info=True)
            return []
    
    def _sheet_to_markdown(self, sheet) -> str:
        """
        将 Excel sheet 转换为 Markdown 表格
        
        Args:
            sheet: openpyxl worksheet 对象
            
        Returns:
            Markdown 格式的表格字符串
        """
        rows = []
        col_count = 0
        
        for row in sheet.iter_rows(values_only=True):
            # 过滤完全空的行
            if not any(cell for cell in row):
                continue
            
            # 将 None 转为空字符串，其他值转为字符串
            cells = [str(cell) if cell is not None else "" for cell in row]
            rows.append("| " + " | ".join(cells) + " |")
            
            # 记录列数
            if col_count == 0:
                col_count = len(cells)
        
        if len(rows) > 1 and col_count > 0:
            # 在第一行后插入分隔行
            header_sep = "| " + " | ".join(["---"] * col_count) + " |"
            rows.insert(1, header_sep)
        
        return "\n".join(rows)
