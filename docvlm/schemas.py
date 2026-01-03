# 数据结构定义
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


@dataclass
class Block:
    """内容块"""
    block_id: str               # 唯一标识 p{页码}_b{序号}
    type: str                   # 块类型: title/text/list/table/image/equation
    content: str = ""           # 文本内容
    bbox: List[float] = field(default_factory=list)  # 归一化坐标 [x1, y1, x2, y2]
    level: Optional[int] = None # 标题层级 (仅 title 类型)
    image_path: Optional[str] = None  # 图片路径 (仅 image 类型)
    
    # 字体属性（用于标题层级聚类）
    font_size: Optional[float] = None     # 字体大小 (pt)
    font_name: Optional[str] = None       # 字体名称
    is_bold: Optional[bool] = None        # 是否粗体
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        d = {
            "block_id": self.block_id,
            "type": self.type,
            "bbox": self.bbox,
        }
        if self.content:
            d["content"] = self.content
        if self.level is not None:
            d["level"] = self.level
        if self.image_path:
            d["image_path"] = self.image_path
        if self.font_size is not None:
            d["font_size"] = self.font_size
        if self.font_name:
            d["font_name"] = self.font_name
        if self.is_bold is not None:
            d["is_bold"] = self.is_bold
        return d


@dataclass
class Page:
    """页面"""
    page_idx: int
    page_size: List[int]        # [width, height] 像素
    blocks: List[Block] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "page_idx": self.page_idx,
            "page_size": self.page_size,
            "blocks": [b.to_dict() for b in self.blocks],
        }


@dataclass
class Document:
    """文档"""
    title: str = ""
    total_pages: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    pages: List[Page] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "metadata": {
                "title": self.title,
                "total_pages": self.total_pages,
                "created_at": self.created_at,
            },
            "pages": [p.to_dict() for p in self.pages],
        }


@dataclass
class ParseResult:
    """解析结果"""
    json_path: str      # docvlm_output.json 路径
    md_path: str        # output.md 路径
    images_dir: str     # images/ 目录路径
    document: Document  # 解析后的文档对象
