"""
预处理相关数据结构定义
"""
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any

@dataclass
class ContentItem:
    """content_list.json 中的单个内容项"""
    type: str                    # text, list, image, table 等
    text: str                    # 文本内容
    page_idx: int               # 页码
    bbox: List[int]             # 边界框坐标 [x1, y1, x2, y2]
    # 其他字段根据 type 而定，使用 **kwargs 接收


@dataclass
class TitleContentBlock:
    """标题-内容块"""
    title: str                    # 标题文本
    title_page_idx: int          # 标题所在页码
    contents: List[Dict[str, Any]]  # 内容项列表（保留原始dict格式）
    raw_text: str                # 拼接后的纯文本内容
    block_ids: List[str] = None  # block_id 列表（用于精确匹配）
    section: str = ""            # 层级路径（如 "预防 > 生活方式干预"）
    is_appendix: bool = False    # 是否为附件内容（跳过 LLM 分类）


class SemanticLabel(str, Enum):
    """语义标签枚举"""
    OVERVIEW = "Overview"        # 摘要、前言、范围、目标人群
    EXECUTIVE_SUMMARY = "Executive Summary"  # 执行摘要
    CONCLUSION = "Conclusion"    # 结论、总结
    METHOD = "Method"            # 指南制定过程、委员会、证据分级、文献检索 (原 METHODOLOGY)
    RECOMMENDATION = "Recommendation"  # 疾病管理、筛查、治疗建议 (原 CLINICAL)
    OTHERS = "Others"            # 利益冲突、资助来源 (原 INDEPENDENCE)
    APPENDIX = "Appendix"        # 附录
    IRRELEVANT = "Irrelevant"    # 目录、参考文献列表、索引


@dataclass
class SemanticChunk:
    """语义块（带标签的内容块）"""
    chunk_id: str                # 块 ID（如 "chunk_001"）
    title: str                   # 标题
    content: str                 # 内容文本
    label: SemanticLabel        # 语义标签
    start_page: int             # 起始页码（标题所在页）
    end_page: int               # 结束页码（最后一个内容项所在页）
    reasoning: str = ""         # 分类理由（LLM thinking）
    block_ids: List[str] = None  # block_id 列表（用于精确匹配）
    section: str = ""            # 层级路径（如 "预防 > 生活方式干预"）
