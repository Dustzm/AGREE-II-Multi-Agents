"""
解析器模块

封装各类文档解析器：
- MinerUParser: 远程 MinerU API 解析器 (高质量，支持复杂排版)
- PyPDFParser: 本地 pypdf 解析器 (简单快速，无需外部服务)
"""
from .mineru_parser import MinerUParser
from .pypdf_parser import PyPDFParser

__all__ = ["MinerUParser", "PyPDFParser"]
