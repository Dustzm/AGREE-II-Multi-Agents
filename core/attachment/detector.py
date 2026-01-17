"""
附件检测器模块

定义可扩展的附件检测策略。
"""
import re
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class DetectedAttachment:
    """检测到的附件信息"""
    path: Path                    # 附件文件路径
    type: str                     # 附件类型 (pdf, docx, xlsx, ...)
    source: str                   # 检测来源标识
    index: int = 0                # 附件编号（从 1 开始）
    priority: int = 0             # 优先级（数字越小优先级越高）
    output_dir: Path = None       # 附件解析输出目录
    metadata: dict = field(default_factory=dict)  # 额外元数据


class AttachmentDetector(ABC):
    """
    附件检测器抽象基类
    
    子类实现不同的检测策略：
    - SiblingFileDetector: 同目录同名附件
    - MetadataDetector: 从 PDF 元数据提取（未来）
    - ContentLinkDetector: 从正文链接提取（未来）
    """
    
    @abstractmethod
    def detect(self, source_path: Path, **kwargs) -> List[DetectedAttachment]:
        """
        检测附件
        
        Args:
            source_path: 源文件路径（如主指南 PDF）
            **kwargs: 额外参数
            
        Returns:
            检测到的附件列表
        """
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """检测器名称"""
        pass


class SiblingFileDetector(AttachmentDetector):
    """
    同目录附件检测器
    
    检测规则（新版，更宽松的匹配）：
    1. 以源文件 basename 为前缀
    2. 后面可以跟任意描述（附件、执行摘要、PPT摘要、附件-算法 等）
    3. 支持空格、下划线、连字符等分隔符
    
    示例匹配：
    - 1-2023-030E.pdf (源文件)
    - 1-2023-030E附件.pdf → 匹配
    - 1-2023-030E附件 1.doc → 匹配
    - 1-2023-030E执行摘要.pdf → 匹配
    - 1-2023-030E附件-算法.pdf → 匹配
    - 1-2023-030E PPT摘要.pptx → 匹配
    
    支持扩展名：pdf, docx, doc, xlsx, xls, pptx, md, txt, mp4 等
    """
    
    # 支持的扩展名
    EXTENSIONS = [
        "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
        "md", "txt", "mp4", "mov", "avi", "webm", "mkv"
    ]
    
    @property
    def name(self) -> str:
        return "sibling_file"
    
    def detect(self, source_path: Path, **kwargs) -> List[DetectedAttachment]:
        """
        检测同目录下的附件文件
        
        新策略：匹配所有以源文件 basename 为前缀的文件（排除源文件本身）
        
        Args:
            source_path: 源文件路径
            
        Returns:
            检测到的附件列表（按文件名排序）
        """
        source_path = Path(source_path)
        if not source_path.exists():
            logger.warning(f"源文件不存在: {source_path}")
            return []
        
        parent_dir = source_path.parent
        basename = source_path.stem  # 不含扩展名的文件名
        source_name = source_path.name  # 完整文件名（包含扩展名）
        
        detected = []
        
        # 遍历目录查找匹配文件
        for file_path in parent_dir.iterdir():
            if not file_path.is_file():
                continue
            
            file_name = file_path.name
            file_stem = file_path.stem
            file_ext = file_path.suffix.lstrip('.').lower()
            
            # 跳过源文件本身
            if file_name == source_name:
                continue
            
            # 跳过不支持的扩展名
            if file_ext not in self.EXTENSIONS:
                continue
            
            # 核心匹配逻辑：文件名以 basename 为前缀
            # 且后面必须有额外内容（避免匹配同名不同扩展名的文件）
            if file_stem.startswith(basename) and len(file_stem) > len(basename):
                # 检查分隔符：后面紧跟的字符应该是分隔符或中文
                next_char = file_stem[len(basename)]
                # 允许的分隔符：空格、下划线、连字符、中文字符
                if next_char in ' _-' or '\u4e00' <= next_char <= '\u9fff':
                    detected.append(DetectedAttachment(
                        path=file_path,
                        type=file_ext,
                        source=self.name,
                        priority=0,  # 统一优先级
                    ))
                    logger.info(f"检测到附件: {file_path}")
        
        # 按文件名排序
        detected.sort(key=lambda x: x.path.name)
        return detected

