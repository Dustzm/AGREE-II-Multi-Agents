"""
URL 分类器

使用 LLM 判断 URL 是否为附件链接
"""

import json
import re
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

import litellm

from core.config.settings import get_settings

logger = logging.getLogger(__name__)


class URLClassificationOutput(BaseModel):
    """URL 分类输出 Schema"""
    is_attachment: bool = Field(
        ...,
        description="URL 是否为附件链接（true/false）"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="置信度（0.0-1.0），越高表示越确定"
    )
    reasoning: str = Field(
        ...,
        description="判断理由（1-2 句话），如'正文明确说明详见该文档'"
    )
    attachment_type: str = Field(
        ...,
        description="附件类型：pdf/word/excel/html/other"
    )


class URLClassifier:
    """
    URL 智能分类器
    
    使用 LLM 判断 URL 是否为附件链接
    """
    
    SYSTEM_PROMPT_ZH = """你是一个 URL 分类器，负责判断临床指南中的 URL 是否为附件链接。

## 判断标准

请根据以下三个标准判断 URL 是否为附件链接（满足任一标准即可）：

1. **明确的引用指示**
   - 正文中包含"请参考"、"详见"、"更多信息请见"、"访问"、"查看"等指示性词汇
   - 语法结构为：指示词 + URL（例如："更多信息请访问 https://example.com/guide"）
   - URL 与当前章节内容直接相关

2. **文档类型判断**
   - URL 本身指向文档资源（PDF、Word、Excel、PPT 等文件）
   - URL 路径包含 "document"、"file"、"download"、"attachment"、"appendix" 等关键词
   - 文件扩展名为 .pdf、.docx、.xlsx、.pptx 等

3. **章节关联性**
   - 附件内容与当前章节主题高度相关
   - 例如：在"方法学"章节链接到"详细的检索策略文档"
   - 在"推荐"章节链接到"完整推荐表"

## 排除标准

如果符合以下任一情况，则**不是**附件链接：

- 简单的外部网站首页链接（如医院官网首页）
- 参考文献、文献库链接（如 PubMed、Elsevier、SpringerLink）
- 一般性资源链接（如"了解更多关于疾病 X 的信息"）
- 新闻媒体链接
- 搜索引擎链接

## 输出要求

请以 JSON 格式返回，严格遵循以下 Schema：

{
  "is_attachment": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "判断理由（1-2 句话）",
  "attachment_type": "pdf/word/excel/html/other"
}

## 示例

### 示例 1：附件链接
章节标题：方法学 - 文献检索
上下文：详细的检索策略请访问 https://guidelines.org/methods/search_strategy.pdf
URL：https://guidelines.org/methods/search_strategy.pdf

输出：
{
  "is_attachment": true,
  "confidence": 0.95,
  "reasoning": "正文中明确说明'详细的检索策略请访问'，URL 指向 PDF 文档，与当前章节'方法学-文献检索'高度相关",
  "attachment_type": "pdf"
}

### 示例 2：非附件链接（参考文献）
章节标题：方法学 - 文献检索
上下文：参见文献 [1] Smith et al. (2023). https://doi.org/10.1000/example
URL：https://doi.org/10.1000/example

输出：
{
  "is_attachment": false,
  "confidence": 0.9,
  "reasoning": "URL 为 DOI 格式的文献引用，带有参考文献编号 [1]，属于标准参考文献而非附件",
  "attachment_type": "other"
}

### 示例 3：非附件链接（外部网站）
章节标题：概述
上下文：如需了解更多信息，请访问 WHO 官网 https://www.who.int
URL：https://www.who.int

输出：
{
  "is_attachment": false,
  "confidence": 0.85,
  "reasoning": "URL 为外部网站首页链接，虽然提示'了解更多信息'，但链接指向首页而非具体文档，属于一般性资源引用",
  "attachment_type": "html"
}
"""
    
    SYSTEM_PROMPT_EN = """You are a URL classifier responsible for determining whether a URL in clinical guidelines is an attachment link.

## Evaluation Criteria

Determine if a URL is an attachment link based on following three criteria (any one is sufficient):

1. **Explicit Reference Indicators**
   - The text contains phrases like "please refer to", "see also", "for more information", "visit", "view", etc.
   - Syntax structure: indicator phrase + URL (e.g., "For more information, visit https://example.com/guide")
   - The URL is directly relevant to current section content

2. **Document Type Indicators**
   - The URL itself points to document resources (PDF, Word, Excel, PPT files)
   - The URL path contains keywords like "document", "file", "download", "attachment", "appendix", etc.
   - File extension is .pdf, .docx, .xlsx, .pptx, etc.

3. **Section Relevance**
   - The attachment content is highly relevant to current section topic
   - Example: In "Methodology" section, link to "detailed search strategy document"
   - Example: In "Recommendations" section, link to "complete recommendation table"

## Exclusion Criteria

If any of following conditions apply,  URL is **NOT** an attachment link:

- Simple external website homepage links (e.g., hospital homepage)
- Reference or literature database links (e.g., PubMed, Elsevier, SpringerLink)
- General resource links (e.g., "Learn more about disease X")
- News media links
- Search engine links

## Output Requirements

Please return in JSON format, strictly following this schema:

{
  "is_attachment": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "Reasoning for judgment (1-2 sentences)",
  "attachment_type": "pdf/word/excel/html/other"
}

## Examples

### Example 1: Attachment Link
Section Title: Methodology - Literature Search
Context: For detailed search strategy, visit https://guidelines.org/methods/search_strategy.pdf
URL: https://guidelines.org/methods/search_strategy.pdf

Output:
{
  "is_attachment": true,
  "confidence": 0.95,
  "reasoning": "Text explicitly states 'For detailed search strategy, visit', URL points to PDF document, highly relevant to current section 'Methodology - Literature Search'",
  "attachment_type": "pdf"
}

### Example 2: Non-Attachment Link (Reference)
Section Title: Methodology - Literature Search
Context: See reference [1] Smith et al. (2023). https://doi.org/10.1000/example
URL: https://doi.org/10.1000/example

Output:
{
  "is_attachment": false,
  "confidence": 0.9,
  "reasoning": "URL is a DOI-formatted literature citation with reference number [1], belongs to standard reference rather than attachment",
  "attachment_type": "other"
}

### Example 3: Non-Attachment Link (External Website)
Section Title: Overview
Context: For more information, visit WHO website at https://www.who.int
URL: https://www.who.int

Output:
{
  "is_attachment": false,
  "confidence": 0.85,
  "reasoning": "URL is external website homepage link. Although 'for more information' is mentioned, link points to homepage rather than specific document, belongs to general resource reference",
  "attachment_type": "html"
}
"""
    
    def __init__(self, lang: str = 'zh', model: Optional[str] = None):
        """
        初始化 URL 分类器
        
        Args:
            lang: 语言（zh/en）
            model: LLM 模型（None 则使用默认配置）
        """
        self.lang = lang
        settings = get_settings()
        
        self.model = model or settings.model_config.model
        self.api_base = settings.model_config.base_url
        self.api_key = settings.model_config.api_key
        
        self.system_prompt = self.SYSTEM_PROMPT_ZH if lang == 'zh' else self.SYSTEM_PROMPT_EN
        
        logger.info(f"URLClassifier 初始化: lang={lang}, model={self.model}")
    
    def _build_prompt(self, url: str, context: str, title: str) -> str:
        """
        构建分类 Prompt
        
        Args:
            url: URL 地址
            context: URL 周围的上下文文本（100-200 字）
            title: 所在章节标题（可选）
            
        Returns:
            Prompt 文本
        """
        if self.lang == 'zh':
            return f"""请判断以下 URL 是否为附件链接。

章节标题：{title}

上下文：{context}

URL：{url}

请以 JSON 格式返回，严格遵循 Schema 要求。"""
        else:
            return f"""Please determine if the following URL is an attachment link.

Section Title: {title}

Context: {context}

URL: {url}

Please return in JSON format, strictly following the schema requirements."""
    
    async def classify(
        self,
        url: str,
        context: str,
        title: str = ''
    ) -> Dict[str, Any]:
        """
        分类 URL
        
        Args:
            url: URL 地址
            context: URL 周围的上下文文本（100-200 字）
            title: 所在章节标题（可选）
            
        Returns:
            {
                "is_attachment": bool,
                "confidence": float,
                "reasoning": str,
                "attachment_type": str
            }
        """
        prompt = self._build_prompt(url, context, title)
        
        logger.info(f"LLM 分类 URL: {url}")
        
        try:
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt}
                ],
                response_format=URLClassificationOutput,
                temperature=0.1,
                api_base=self.api_base,
                api_key=self.api_key,
                timeout=get_settings().llm.timeout,
            )
            
            result = response.choices[0].message.parsed
            
            logger.info(f"LLM 分类结果: is_attachment={result.is_attachment}, confidence={result.confidence}, type={result.attachment_type}")
            
            return {
                "is_attachment": result.is_attachment,
                "confidence": result.confidence,
                "reasoning": result.reasoning,
                "attachment_type": result.attachment_type
            }
        except Exception as e:
            logger.error(f"LLM 分类失败: {url}, {e}")
            return {
                "is_attachment": False,
                "confidence": 0.0,
                "reasoning": f"LLM classification failed: {str(e)}",
                "attachment_type": "other"
            }
