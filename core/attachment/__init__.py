"""
附件处理模块

提供可扩展的附件检测和解析功能。
"""
from .detector import AttachmentDetector, SiblingFileDetector, DetectedAttachment
from .parser import AttachmentParser, PDFAttachmentParser, ParsedContent
from .processor import AttachmentProcessor, AttachmentInfo
from .pptx_parser import PPTXAttachmentParser

__all__ = [
    # 检测器
    "AttachmentDetector",
    "SiblingFileDetector", 
    "DetectedAttachment",
    # 解析器
    "AttachmentParser",
    "PDFAttachmentParser",
    "PPTXAttachmentParser",
    "ParsedContent",
    # 处理器
    "AttachmentProcessor",
    "AttachmentInfo",
]

