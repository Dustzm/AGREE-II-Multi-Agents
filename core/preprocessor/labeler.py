"""
语义标签分类器

使用 LLM 为文档内容块分配语义标签，作为预处理流水线的核心组件
"""
import logging
from pathlib import Path
from typing import List, Optional

from .pojo import TitleContentBlock, SemanticChunk, SemanticLabel

logger = logging.getLogger(__name__)

class SemanticLabeler:
    """
    语义标签分类器
    
    使用 LLM 为每个标题-内容块打语义标签
    """
    
    def __init__(self, model=None, lang: str = "zh"):
        """
        初始化分类器
        
        Args:
            model: LLM 模型实例，默认从配置获取
            lang: Prompt 语言（zh/en）
        """
        from core.agents.base import get_model
        
        self.model = model or get_model()
        self.lang = lang
        self.prompt_template = self._load_prompt_template()
    
    def _load_prompt_template(self) -> str:
        """加载 Prompt 模板"""
        # 文件位置: core/agents/core/preprocessor/labeler.py
        # 目标路径: core/prompts/zh/semantic_prompt.md
        
        current_dir = Path(__file__).parent
        # preprocessor -> core -> agents -> core -> prompts
        core_dir = current_dir.parent.parent.parent
        
        prompt_dir = core_dir / "prompts" / self.lang
        prompt_file = prompt_dir / "semantic_prompt.md"

        
        if not prompt_file.exists():
            logger.warning(f"Prompt 文件不存在: {prompt_file}，使用默认模板")
            # 回退到默认模板
            if self.lang == "zh":
                return """你是一位临床实践指南分析专家。请为以下指南内容片段分配一个语义标签。

## 标签定义
- **Overview**: 摘要、前言、范围定义、目标人群描述
- **Methodology**: 指南制定过程、委员会组成、证据分级方法、文献检索策略
- **Clinical**: 具体的疾病管理建议、筛查建议、治疗方案、临床推荐
- **Independence**: 利益冲突声明、资助来源、编辑独立性声明
- **Irrelevant**: 目录、索引、纯参考文献列表、版权信息

## 输入内容
标题: {title}
内容: {content}

## 输出格式
仅输出一个标签名称: Overview / Methodology / Clinical / Independence / Irrelevant"""
            else:
                return """You are a clinical practice guideline analysis expert. Assign a semantic label to the following guideline content fragment.

## Label Definitions
- **Overview**: Abstract, preface, scope definition, target population description
- **Methodology**: Guideline development process, committee composition, evidence grading methods, literature search strategies
- **Clinical**: Specific disease management recommendations, screening recommendations, treatment plans, clinical recommendations
- **Independence**: Conflict of interest statements, funding sources, editorial independence statements
- **Irrelevant**: Table of contents, index, reference lists only, copyright information

## Input Content
Title: {title}
Content: {content}

## Output Format
Output only one label name: Overview / Methodology / Clinical / Independence / Irrelevant"""
        
        # 从文件加载模板
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read()
    
    def _render_prompt(self, title: str, content: str, section: str = "") -> str:
        """渲染 Prompt"""
        # 截断过长的内容（避免超过 token 限制）
        max_content_length = 2000
        if len(content) > max_content_length:
            content = content[:max_content_length] + "..."
        
        return self.prompt_template.format(title=title, section=section, content=content)
    
    async def _call_llm(self, prompt: str) -> str:
        """
        调用 LLM 获取标签
        
        Args:
            prompt: 提示词
            
        Returns:
            JSON 格式的响应字符串
        """
        try:
            import litellm
            from core.config import get_settings
            from core.agents.base.schemas import SemanticLabelOutput
            
            settings = get_settings()
            model_name = f"openai/{settings.llm.model_name}"
            
            # 使用 litellm 的异步接口，配合 response_format 强制 JSON 输出
            response = await litellm.acompletion(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                top_p=0.9,
                max_tokens=2500,  # 限制输出长度，防止模型退化导致无止境输出
                response_format=SemanticLabelOutput  # 使用 Pydantic schema 强制 JSON 输出
            )
            
            # 提取文本响应
            result = response.choices[0].message.content.strip()
            logger.debug(f"LLM 原始返回: {result}")
            return result
        
        except Exception as e:
            logger.error(f"LLM 调用失败: {str(e)}", exc_info=True)
            return '{"thinking": "LLM调用失败", "tag": "Irrelevant"}'
    
    async def label_chunks(self, chunks: List[TitleContentBlock], max_concurrency: int = 70) -> List[SemanticChunk]:
        """
        为所有内容块打标签（并发执行）
        
        Args:
            chunks: 标题-内容块列表
            max_concurrency: 最大并发数
            
        Returns:
            语义块列表
        """
        import asyncio
        
        semaphore = asyncio.Semaphore(max_concurrency)
        tasks = []
        
        logger.info(f"开始标签分类，共 {len(chunks)} 个块，并发数 {max_concurrency}")
        
        for idx, chunk in enumerate(chunks):
            task = self._process_single_chunk(idx, chunk, len(chunks), semaphore)
            tasks.append(task)
        
        # 并发执行并保持顺序
        semantic_chunks = await asyncio.gather(*tasks)
        return [c for c in semantic_chunks if c is not None]

    async def _process_single_chunk(self, idx: int, chunk: TitleContentBlock, total: int, semaphore: any) -> SemanticChunk:
        """处理单个块的标签分类"""
        async with semaphore:
            # 渲染 Prompt
            prompt = self._render_prompt(chunk.title, chunk.raw_text, chunk.section)
            
            # 日志输出：显示开始状态
            # 仅对第一个块显示完整 Prompt，避免刷屏；其他块只显示标题
            if idx == 0:
                logger.info(f"=== 标签分配开始 [{idx+1}/{total}] ===")
                logger.info(f"标题: {chunk.title[:50]}...")
                logger.info(f"Section: {chunk.section if chunk.section else '(无父级)'}")
                logger.info(f"--- 完整提示词示例 ---\n{prompt}\n--- 示例结束 ---")
            
            # 调用 LLM
            response_str = await self._call_llm(prompt)
            
            # 解析 JSON 响应（包含 tag 和 thinking）
            label, reasoning = self._parse_json_response(response_str)
            
            # 计算页码范围
            start_page = chunk.title_page_idx
            end_page = start_page
            if chunk.contents:
                # 找出内容块中最大的页码
                for content in chunk.contents:
                    page = content.get("page_idx", start_page)
                    if page > end_page:
                        end_page = page
            
            # 创建语义块
            semantic_chunk = SemanticChunk(
                chunk_id=f"chunk_{idx+1:03d}",
                title=chunk.title,
                content=chunk.raw_text,
                label=label,
                start_page=start_page,
                end_page=end_page,
                reasoning=reasoning,
                block_ids=chunk.block_ids or [],
                section=chunk.section  # 传递 section
            )
            
            # 完成日志
            logger.info(f"[{idx+1}/{total}] {chunk.title[:20]}... -> {label.value}")
            return semantic_chunk
    
    def _parse_json_response(self, response_str: str) -> tuple:
        """
        解析 LLM 返回的 JSON 响应
        
        Args:
            response_str: LLM 返回的字符串（应为 JSON 格式）
            
        Returns:
            (SemanticLabel 枚举值, reasoning 字符串)
        """
        import re
        import json
        
        # 过滤 Qwen3 thinking 模型的 <think>...</think> 标签
        response_str = re.sub(r'<think>.*?</think>', '', response_str, flags=re.DOTALL)
        response_str = response_str.strip()
        
        logger.debug(f"解析 JSON 响应，过滤后内容: {response_str[:200]}...")
        
        # 尝试提取 JSON 块
        json_match = re.search(r'```json\s*(.*?)\s*```', response_str, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析为 JSON
            json_str = response_str
        
        # 解析 JSON
        try:
            data = json.loads(json_str)
            # 优先使用 reason，兼容旧的 thinking 字段
            reasoning = data.get("reason", data.get("thinking", ""))
            tag_str = data.get("tag", "Irrelevant")
            
            logger.info(f"LLM 理由: {reasoning}")
            logger.info(f"LLM 标签: {tag_str}")
            
            # 解析标签
            tag_upper = tag_str.upper().strip()
            for label in SemanticLabel:
                if label.value.upper() == tag_upper:
                    return label, reasoning
            
            # 模糊匹配
            for label in SemanticLabel:
                if label.value.lower() in tag_str.lower():
                    return label, reasoning
            
            logger.warning(f"无法解析标签: {tag_str}，默认为 Irrelevant")
            return SemanticLabel.IRRELEVANT, reasoning
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}，尝试文本解析")
            # 回退到文本解析
            label = self._parse_label_fallback(response_str)
            return label, ""
    
    def _parse_label_fallback(self, label_str: str) -> SemanticLabel:
        """回退的标签解析方法（文本匹配）"""
        import re
        label_str = re.sub(r'<think>.*?</think>', '', label_str, flags=re.DOTALL)
        label_str = label_str.strip()
        
        # 尝试精确匹配
        for label in SemanticLabel:
            if label.value.upper() == label_str.upper():
                return label
        
        # 模糊匹配
        for label in SemanticLabel:
            if label_str.lower().startswith(label.value.lower()):
                return label
        
        # 回退
        priority_order = [SemanticLabel.IRRELEVANT, SemanticLabel.INDEPENDENCE, 
                         SemanticLabel.METHODOLOGY, SemanticLabel.CLINICAL, SemanticLabel.OVERVIEW]
        for label in priority_order:
            if label.value.lower() in label_str.lower():
                return label
        
        return SemanticLabel.IRRELEVANT

