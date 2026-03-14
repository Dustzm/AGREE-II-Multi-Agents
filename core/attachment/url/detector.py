"""
内容链接检测器

从指南正文提取 URL 并判断是否为附件链接
"""

import json
import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from ..detector import DetectedAttachment, AttachmentDetector
from .classifier import URLClassifier
from .filter import URLFilter

logger = logging.getLogger(__name__)


class ContentLinkDetector(AttachmentDetector):
    """
    内容链接检测器
    
    从 content_list.json 的 text 字段提取 URL
    """
    
    @property
    def name(self) -> str:
        return "content_link"
    
    def __init__(self, enable_llm: bool = True):
        self.enable_llm = enable_llm
        self.url_filter = URLFilter()
        self.url_classifier = URLClassifier() if enable_llm else None
        logger.info(f"ContentLinkDetector 初始化: enable_llm={enable_llm}")
    
    async def detect(
        self,
        source_path: Path,
        content_list_path: Optional[Path] = None,
        classifier: Optional[URLClassifier] = None,
        **kwargs
    ) -> List[DetectedAttachment]:
        """
        检测内容链接
        
        Args:
            source_path: 源文件路径
            content_list_path: content_list.json 路径（可选，默认在 prep_dir 下查找）
            classifier: URL 分类器（可选，不提供则跳过 LLM 分类）
            **kwargs: 额外参数
            
        Returns:
            检测到的附件列表
        """
        logger.info(f"ContentLinkDetector 开始检测: source_path={source_path}")
        
        if not content_list_path:
            prep_dir = source_path.parent / '.ama'
            content_list_path = prep_dir / f'{source_path.stem}_content_list.json'
        
        if not content_list_path.exists():
            logger.warning(f"content_list 不存在: {content_list_path}")
            return []
        
        with open(content_list_path, 'r', encoding='utf-8') as f:
            items = json.load(f)
        
        logger.info(f"加载 content_list: {len(items)} 个条目")
        
        all_detections = []
        url_filter = URLFilter()
        total_urls_extracted = 0
        total_urls_excluded = 0
        total_urls_classified = 0
        
        for item in items:
            item_type = item.get('type', '')
            if item_type in {'header', 'footer', 'page_number'}:
                continue
            
            text = item.get('text', '')
            page_idx = item.get('page_idx', 0)
            source = item.get('text_level', 'unknown')
            
            urls = self._extract_urls(text)
            total_urls_extracted += len(urls)
            
            if urls:
                logger.debug(f"条目 page_idx={page_idx}, type={item_type}: 提取到 {len(urls)} 个 URL")
            
            for url in urls:
                should_include, reason = url_filter.should_include(url, text)
                
                if should_include is False:
                    logger.debug(f"排除 URL: {url}, 原因: {reason}")
                    total_urls_excluded += 1
                    continue
                
                if should_include is None and classifier:
                    try:
                        logger.debug(f"LLM 分类 URL: {url}")
                        total_urls_classified += 1
                        result = await classifier.classify(
                            url=url,
                            context=text[:200],
                            title=source
                        )
                        is_attachment = result['is_attachment']
                        confidence = result['confidence']
                        reasoning = result['reasoning']
                        attachment_type = result['attachment_type']
                        logger.debug(f"LLM 分类结果: is_attachment={is_attachment}, confidence={confidence}, type={attachment_type}")
                    except Exception as e:
                        logger.warning(f"LLM 分类失败: {url}, {e}")
                        is_attachment = False
                        confidence = 0.0
                        reasoning = "LLM classification failed"
                        attachment_type = 'other'
                else:
                    is_attachment = True
                    confidence = 0.9
                    reasoning = f"Matched include pattern: {reason}"
                    attachment_type = self._guess_type(url)
                
                if is_attachment:
                    all_detections.append(DetectedAttachment(
                        path=Path(url),
                        type=attachment_type,
                        source=self.name,
                        priority=1,
                        metadata={
                            'url': url,
                            'page_idx': page_idx,
                            'source_section': source,
                            'confidence': confidence,
                            'reasoning': reasoning,
                            'detection_method': 'content_link'
                        }
                    ))
                    logger.info(f"检测到附件链接: {url}, 置信度: {confidence}")
        
        logger.info(
            f"ContentLinkDetector 完成: 提取={total_urls_extracted}, "
            f"排除={total_urls_excluded}, "
            f"LLM分类={total_urls_classified}, "
            f"最终检测={len(all_detections)} 个附件链接"
        )
        return all_detections
    
    def _extract_urls(self, text: str) -> List[str]:
        """从文本提取 URL"""
        URL_PATTERNS = [
            r'https?://[^\s<>"\'\)\]\\]+',
            r'www\.[^\s<>"\'\)\]\\]+\.[a-zA-Z]{2,}',
            r'doi\.org/10\.[^\s<>"\'\)\]\\]+',
        ]
        
        urls = set()
        for pattern in URL_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            urls.update(matches)
        
        normalized = []
        for url in urls:
            url = url.strip()
            if url.startswith('www.'):
                url = f'https://{url}'
            normalized.append(url)
        
        return normalized
    
    def _guess_type(self, url: str) -> str:
        """根据 URL 猜测附件类型"""
        url_lower = url.lower()
        
        if '.pdf' in url_lower:
            return 'pdf'
        elif any(ext in url_lower for ext in ['.doc', '.docx']):
            return 'word'
        elif any(ext in url_lower for ext in ['.xls', '.xlsx']):
            return 'excel'
        else:
            return 'html'
