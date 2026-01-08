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
    
    检测规则：
    1. {basename}附件.{ext} — 单个附件
    2. {basename}_附件.{ext} — 单个附件（带下划线）
    3. {basename}附件1.{ext}, {basename}附件2.{ext} — 多个附件编号
    4. {basename}_appendix.{ext}, {basename}_supplement.{ext} — 英文命名
    
    支持扩展名：pdf, docx, xlsx, md, txt
    """
    
    # 支持的后缀模式（正则，会与 basename 拼接）
    SUFFIX_PATTERNS = [
        r"附件\d*",       # 附件、附件1、附件2...
        r"_附件\d*",      # _附件、_附件1...
        r"附录\d*",       # 附录、附录1...
        r"_附录\d*",
        r"_appendix\d*",  # _appendix、_appendix1...
        r"appendix\d*",
        r"_supplement\d*",
        r"supplement\d*",
    ]
    
    EXTENSIONS = ["pdf", "docx", "xlsx", "md", "txt", "mp4", "mov", "avi", "webm", "mkv"]
    
    @property
    def name(self) -> str:
        return "sibling_file"
    
    def detect(self, source_path: Path, **kwargs) -> List[DetectedAttachment]:
        """
        检测同目录下的附件文件
        
        Args:
            source_path: 源文件路径
            
        Returns:
            检测到的附件列表（按优先级排序）
        """
        source_path = Path(source_path)
        if not source_path.exists():
            logger.warning(f"源文件不存在: {source_path}")
            return []
        
        parent_dir = source_path.parent
        basename = source_path.stem  # 不含扩展名的文件名
        
        detected = []
        
        # 构建匹配模式
        for priority, suffix_pattern in enumerate(self.SUFFIX_PATTERNS):
            for ext in self.EXTENSIONS:
                # 构建完整的正则模式
                full_pattern = re.compile(
                    f"^{re.escape(basename)}{suffix_pattern}\\.{ext}$",
                    re.IGNORECASE
                )
                
                # 遍历目录查找匹配文件
                for file_path in parent_dir.iterdir():
                    if file_path.is_file() and full_pattern.match(file_path.name):
                        # 避免重复添加
                        if not any(d.path == file_path for d in detected):
                            detected.append(DetectedAttachment(
                                path=file_path,
                                type=ext.lower(),
                                source=self.name,
                                priority=priority,
                            ))
                            logger.info(f"检测到附件: {file_path}")
        
        # 按优先级排序
        detected.sort(key=lambda x: (x.priority, x.path.name))
        return detected
