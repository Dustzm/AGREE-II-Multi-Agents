"""
HTML 附件解析器

将 HTML 内容解析为 Markdown
"""

import logging
from pathlib import Path
from typing import List

import trafilatura
from markdownify import markdownify as md

from ..parser import ParsedContent, AttachmentParser

logger = logging.getLogger(__name__)


class HTMLAttachmentParser(AttachmentParser):
    """
    HTML 附件解析器
    
    使用 trafilatura 提取正文，markdownify 转换为 Markdown
    """
    
    def __init__(self, max_content_length: int = 50000):
        """
        初始化解析器
        
        Args:
            max_content_length: 最大内容长度（字符）
        """
        self.max_content_length = max_content_length
        logger.info(f"HTMLAttachmentParser 初始化: max_content_length={max_content_length}")
    
    @property
    def supported_extensions(self) -> List[str]:
        return ["html"]
    
    async def parse(
        self,
        file_path: Path,
        prep_dir: Path = None,
        **kwargs
    ) -> List[ParsedContent]:
        """
        解析 HTML 文件
        
        Args:
            file_path: HTML 文件路径
            prep_dir: 预处理输出目录
            **kwargs: 额外参数
            
        Returns:
            ParsedContent 列表
        """
        url = kwargs.get('url', None)
        logger.info(f"HTMLAttachmentParser 开始解析: file_path={file_path}, url={url}")
        
        html_content = file_path.read_text(encoding='utf-8', errors='ignore')
        logger.debug(f"读取 HTML 文件: {len(html_content)} 字符")
        
        logger.debug(f"使用 trafilatura 提取正文")
        downloaded = trafilatura.extract(
            html_content,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            url=url
        )
        
        if not downloaded:
            logger.warning(f"trafilatura 提取失败: {url}, 使用原始 HTML")
            downloaded = html_content
        else:
            logger.debug(f"trafilatura 提取成功: {len(downloaded)} 字符")
        
        logger.debug(f"转换为 Markdown")
        markdown_content = md(downloaded, heading_style="ATX")
        
        if len(markdown_content) > self.max_content_length:
            logger.warning(f"内容超过限制: {len(markdown_content)} 字符，截断到 {self.max_content_length} 字符")
            markdown_content = markdown_content[:self.max_content_length]
            markdown_content += f"\n\n[内容已截断，原始共 {len(downloaded)} 字符]"
        
        metadata = {
            'source_url': url,
            'content_length': len(downloaded),
            'truncated': len(downloaded) > self.max_content_length,
        }
        
        logger.info(f"HTML 解析完成: HTML {len(html_content)} -> 正文 {len(downloaded)} -> Markdown {len(markdown_content)} 字符")
        
        return [ParsedContent(
            title=f"[附件] {file_path.name}",
            content=markdown_content,
            page_idx=0,
            content_type="text",
            metadata=metadata
        )]
