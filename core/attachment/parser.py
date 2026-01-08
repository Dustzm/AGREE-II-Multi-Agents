"""
附件解析器模块

将附件文件内容解析为结构化格式。
"""
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ParsedContent:
    """解析后的内容块"""
    title: str                       # 标题（如 "[附件] 证据分级表"）
    content: str                     # 文本内容
    page_idx: int = 0               # 页码（相对于附件）
    content_type: str = "text"      # 内容类型
    metadata: dict = field(default_factory=dict)  # 额外元数据


class AttachmentParser(ABC):
    """
    附件解析器抽象基类
    
    子类实现不同文件格式的解析：
    - PDFAttachmentParser: PDF 文件
    - WordAttachmentParser: Word 文档（未来）
    - ExcelAttachmentParser: Excel 表格（未来）
    """
    
    @abstractmethod
    async def parse(self, file_path: Path, **kwargs) -> List[ParsedContent]:
        """
        解析附件文件
        
        Args:
            file_path: 附件文件路径
            **kwargs: 额外参数
            
        Returns:
            解析后的内容列表
        """
        pass
    
    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        """支持的文件扩展名"""
        pass
    
    def can_parse(self, file_path: Path) -> bool:
        """检查是否支持解析该文件"""
        return file_path.suffix.lstrip('.').lower() in self.supported_extensions


class PDFAttachmentParser(AttachmentParser):
    """
    PDF 附件解析器
    
    使用 MinerU 解析 PDF 附件，复用现有的解析流程。
    """
    
    @property
    def supported_extensions(self) -> List[str]:
        return ["pdf"]
    
    async def parse(self, file_path: Path, prep_dir: Path = None, **kwargs) -> List[ParsedContent]:
        """
        解析 PDF 附件
        
        Args:
            file_path: PDF 文件路径
            prep_dir: 预处理输出目录（用于存放中间文件）
            **kwargs: 额外参数
            
        Returns:
            解析后的内容列表
        """
        from core.parsers.mineru_parser import MinerUParser
        
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"附件文件不存在: {file_path}")
            return []
        
        # 确定输出目录：直接使用 prep_dir（即 attachment/{id}/）
        if prep_dir is None:
            prep_dir = file_path.parent / f"{file_path.stem}_prep"
        
        # 直接使用 prep_dir 作为输出目录，不创建附件名子目录
        attachment_output_dir = Path(prep_dir)
        attachment_output_dir.mkdir(parents=True, exist_ok=True)
        
        # source.md 路径
        source_path = attachment_output_dir / f"{file_path.stem}.md"
        
        try:
            # 调用 MinerU 解析（使用 parse_pdf_to_file 避免 vlm 子目录）
            logger.info(f"开始解析附件 PDF: {file_path} -> {attachment_output_dir}")
            parser = MinerUParser()
            result = await parser.parse_pdf_to_file(
                pdf_path=str(file_path),
                source_path=str(source_path),
                prep_dir=str(attachment_output_dir)
            )
            
            if result.get("status") != "success":
                logger.error(f"PDF 解析失败: {result.get('error', 'unknown')}")
                return []
            
            # 读取 content_list.json
            content_list_path = Path(result.get("content_list", ""))
            if not content_list_path.exists():
                logger.error(f"content_list 不存在: {content_list_path}")
                return []
            
            with open(content_list_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            
            # 转换为 ParsedContent
            contents = self._items_to_contents(items, file_path.stem)
            
            logger.info(f"附件解析完成: {file_path}, 生成 {len(contents)} 个内容块")
            return contents
            
        except Exception as e:
            logger.error(f"附件解析异常: {e}", exc_info=True)
            return []
    
    def _items_to_contents(self, items: List[Dict[str, Any]], attachment_name: str) -> List[ParsedContent]:
        """
        将 content_list items 转换为 ParsedContent 列表
        
        Args:
            items: content_list.json 中的项目列表
            attachment_name: 附件文件名（不含扩展名）
            
        Returns:
            ParsedContent 列表
        """
        contents = []
        current_title = f"[附件] {attachment_name}"
        current_texts = []
        current_page = 0
        
        for item in items:
            # 跳过页眉页脚
            item_type = item.get("type", "text")
            if item_type in {"header", "footer", "page_number"}:
                continue
            
            if "text_level" in item:
                # 遇到新标题，保存之前的内容
                if current_texts:
                    contents.append(ParsedContent(
                        title=current_title,
                        content="\n".join(current_texts),
                        page_idx=current_page,
                    ))
                    current_texts = []
                current_title = f"[附件] {item.get('text', '')}"
                current_page = item.get("page_idx", 0)
            else:
                # 累积内容
                text = self._extract_text(item)
                if text:
                    current_texts.append(text)
                    if not current_page:
                        current_page = item.get("page_idx", 0)
        
        # 保存最后一块
        if current_texts:
            contents.append(ParsedContent(
                title=current_title,
                content="\n".join(current_texts),
                page_idx=current_page,
            ))
        
        return contents
    
    def _extract_text(self, item: Dict[str, Any]) -> str:
        """从 content_list item 提取文本"""
        item_type = item.get("type", "")
        
        if item_type == "text":
            return item.get("text", "")
        elif item_type == "list":
            list_items = item.get("list_items", [])
            lines = []
            for li in list_items:
                if isinstance(li, str):
                    lines.append(f"- {li}")
                elif isinstance(li, dict):
                    lines.append(f"- {li.get('text', '')}")
            return "\n".join(lines)
        elif item_type == "table":
            parts = []
            caption = item.get("table_caption", [])
            if caption:
                parts.append(f"[表格] {' '.join(str(c) for c in caption)}")
            body = item.get("table_body", "")
            if body:
                # 截断过长的表格
                if len(body) > 3000:
                    body = body[:3000] + "..."
                parts.append(body)
            return "\n".join(parts)
        elif item_type == "image":
            caption = item.get("image_caption", [])
            if caption:
                return f"[图片] {' '.join(str(c) for c in caption)}"
            return "[图片]"
        
        return ""
