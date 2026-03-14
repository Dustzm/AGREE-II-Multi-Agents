"""
URL 规则过滤器

基于规则快速排除非附件 URL 或保留明确的附件 URL
"""

import re
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class URLFilter:
    """
    URL 规则过滤器
    
    基于常见模式排除明显非附件的 URL 或保留明确附件
    """
    
    EXCLUDE_PATTERNS = [
        (r'\[?\d+\]?\s*\(?doi:', 'reference'),
        (r'\breference\b|\bcitation\b|\bbibliography\b', 'reference', re.IGNORECASE),
        (r'google\.(com|cn)', 'search_engine'),
        (r'bing\.com', 'search_engine'),
        (r'wikipedia\.org', 'encyclopedia'),
        (r'doi\.org/10\.', 'reference'),
    ]
    
    INCLUDE_PATTERNS = [
        (r'\.(pdf|doc|docx|xlsx|xls|ppt|pptx)$', 'document'),
        (r'(who\.int|cdc\.gov|nice\.org\.uk|nhmrc\.gov\.au)', 'guideline_site'),
        (r'\battachment\b|\bappendix\b|\bsupplementary\b|\bsupplement\b', 'attachment_keyword', re.IGNORECASE),
    ]
    
    @classmethod
    def should_include(cls, url: str, context: str = '') -> Tuple[Optional[bool], str]:
        """
        判断 URL 是否应该被包含
        
        Args:
            url: URL 地址
            context: URL 周围的上下文文本
            
        Returns:
            (should_include, reason)
            - should_include: True（包含）、False（丢弃）、None（需要 LLM 分类）
            - reason: 判断理由
        """
        url_lower = url.lower()
        context_lower = context.lower() if context else ''
        
        for pattern_item in cls.INCLUDE_PATTERNS:
            pattern = pattern_item[0]
            reason = pattern_item[1]
            flags = pattern_item[2] if len(pattern_item) > 2 else 0
            
            if re.search(pattern, url_lower, flags) or re.search(pattern, context_lower, flags):
                logger.debug(f"包含规则匹配: {url} -> {reason}")
                return (True, f'include_{reason}')
        
        for pattern_item in cls.EXCLUDE_PATTERNS:
            pattern = pattern_item[0]
            reason = pattern_item[1]
            flags = pattern_item[2] if len(pattern_item) > 2 else 0
            
            if re.search(pattern, url_lower, flags) or re.search(pattern, context_lower, flags):
                logger.debug(f"排除规则匹配: {url} -> {reason}")
                return (False, f'exclude_{reason}')
        
        return (None, 'needs_llm_classification')
