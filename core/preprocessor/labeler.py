"""
语义标签分类器

使用 LLM 为文档内容块分配语义标签，作为预处理流水线的核心组件
"""
import logging
from pathlib import Path
from typing import List, Optional

from .pojo import TitleContentBlock, SemanticChunk, SemanticLabel

logger = logging.getLogger(__name__)

_PROMPT_PREVIEW_LIMIT = 1200

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
        from core.agents.base.model_config import get_provider_kwargs
        
        self.model = model or get_model()
        self.lang = lang
        self.prompt_template = self._load_prompt_template()

    def _preview_text(self, text: str, limit: int = _PROMPT_PREVIEW_LIMIT) -> str:
        """生成简短的日志预览，避免把完整提示词刷进日志。"""
        if not text:
            return ""

        if len(text) <= limit:
            return text

        return f"{text[:limit]}\n... [截断，原始长度 {len(text)} 字符]"
    
    def _load_prompt_template(self) -> str:
        """加载 Prompt 模板"""
        # 文件位置: core/preprocessor/labeler.py
        # 目标路径: core/prompts/zh/semantic_prompt.md
        current_dir = Path(__file__).parent
        # preprocessor -> core -> prompts
        core_dir = current_dir.parent
        
        prompt_dir = core_dir / "prompts" / self.lang
        prompt_file = prompt_dir / "semantic_prompt.md"
        
        # 从文件加载模板
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read()
    
    def _render_prompt(self, title: str, content: str, section: str = "", page: int = 1, total_pages: int = 1) -> str:
        """渲染 Prompt"""
        # 截断过长的内容（避免超过 token 限制）
        max_content_length = 2000
        if len(content) > max_content_length:
            content = content[:max_content_length] + "..."
        
        return self.prompt_template.format(
            title=title, 
            section=section, 
            content=content,
            page=page,
            total_pages=total_pages
        )
    
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
            from core.agents.base.model_config import (
                get_compatible_reasoning_kwargs,
                get_compatible_sampling_kwargs,
                get_provider_kwargs,
                get_provider_config_for,
            )
            
            settings = get_settings()
            provider_cfg = get_provider_config_for(settings.llm.provider)
            prefix = provider_cfg["prefix"]
            model_name = f"{prefix}/{settings.llm.model_name}"
            
            # 构建 kwargs：显式传入 api_key / api_base 以及 provider 专属参数
            kwargs = get_provider_kwargs()
            if settings.llm.api_key:
                kwargs["api_key"] = settings.llm.api_key
            if provider_cfg.get("need_base_url"):
                effective_url = settings.llm.base_url or provider_cfg.get("default_base_url", "")
                if effective_url:
                    kwargs["base_url"] = effective_url
            
            # 使用 litellm 的异步接口，配合 response_format 强制 JSON 输出
            response = await litellm.acompletion(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2500,
                response_format={"type": "json_object"},  # 使用 json_object 避免 SGLang xgrammar 兼容性问题
                timeout=settings.llm.timeout,
                **get_compatible_reasoning_kwargs(model_name),
                **get_compatible_sampling_kwargs(
                    model_name,
                    temperature=0,
                    top_p=0.1,
                    seed=42,
                ),
                **kwargs,
            )
            
            # 提取文本响应
            result = response.choices[0].message.content.strip()
            logger.debug(f"LLM 原始返回: {result}")
            return result
        
        except Exception as e:
            logger.error(f"LLM 调用失败: {str(e)}", exc_info=True)
            return '{"thinking": "LLM调用失败", "tag": "Irrelevant"}'
    
    async def label_chunks(self, chunks: List[TitleContentBlock], max_concurrency: int = 0, total_pages: int = 0) -> List[SemanticChunk]:
        """
        为所有内容块打标签（并发执行）
        
        Args:
            chunks: 标题-内容块列表
            max_concurrency: 最大并发数
            total_pages: 文档总页数（用于计算进度位置）
            
        Returns:
            语义块列表
        """
        import asyncio
        
        # 如果未提供总页数，从 chunks 中推断
        if total_pages <= 0:
            total_pages = max((c.title_page_idx for c in chunks), default=1) + 1
        
        # 从配置读取并发度（若未显式传入）
        from core.config import get_settings
        if max_concurrency <= 0:
            max_concurrency = get_settings().app.labeler_max_concurrency
            
        semaphore = asyncio.Semaphore(max_concurrency)
        tasks = []
        
        logger.info(f"开始标签分类，共 {len(chunks)} 个块，并发数 {max_concurrency}，文档总页数 {total_pages}")
        
        for idx, chunk in enumerate(chunks):
            task = self._process_single_chunk(idx, chunk, len(chunks), semaphore, total_pages)
            tasks.append(task)
        
        # 并发执行并保持顺序
        semantic_chunks = await asyncio.gather(*tasks)
        return [c for c in semantic_chunks if c is not None]

    async def _process_single_chunk(self, idx: int, chunk: TitleContentBlock, total: int, semaphore: any, total_pages: int = 1) -> SemanticChunk:
        """处理单个块的标签分类"""
        async with semaphore:
            # 如果是附件块，直接标记为 APPENDIX，跳过 LLM 分类
            if getattr(chunk, 'is_appendix', False):
                logger.info(f"[{idx+1}/{total}] {chunk.title[:20]}... -> Appendix (附件，跳过分类)")
                return SemanticChunk(
                    chunk_id=f"chunk_{idx+1:03d}",
                    title=chunk.title,
                    content=chunk.raw_text,
                    label=SemanticLabel.APPENDIX,
                    start_page=chunk.title_page_idx,
                    end_page=chunk.title_page_idx,
                    reasoning="附件内容，自动标记为 Appendix",
                    block_ids=chunk.block_ids or [],
                    section=chunk.section
                )
            
            # 获取当前页码（用于位置感知）
            current_page = chunk.title_page_idx + 1  # 转为 1-indexed
            
            # 渲染 Prompt（包含页码信息）
            prompt = self._render_prompt(
                chunk.title, 
                chunk.raw_text, 
                chunk.section,
                page=current_page,
                total_pages=total_pages
            )
            
            # 日志输出：显示开始状态
            # 仅对第一个块显示完整 Prompt，避免刷屏；其他块只显示标题
            if idx == 0:
                logger.info(f"=== 标签分配开始 [{idx+1}/{total}] ===")
                logger.info(f"标题: {chunk.title[:50]}...")
                logger.info(f"Section: {chunk.section if chunk.section else '(无父级)'}")
                logger.info(f"--- 提示词预览 ---\n{self._preview_text(prompt)}\n--- 预览结束 ---")
                logger.debug(f"--- 完整提示词示例 ---\n{prompt}\n--- 示例结束 ---")
            
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
            reasoning = self._extract_reasoning_from_json(data)
            label = self._extract_label_from_json(data)
            
            logger.info(f"LLM 理由: {reasoning}")
            logger.info(f"LLM 标签: {label.value if label else 'N/A'}")

            if label is not None:
                return label, reasoning

            logger.warning(
                f"无法从 JSON 响应中解析标签，keys={list(data.keys())}，默认为 Irrelevant"
            )
            return SemanticLabel.IRRELEVANT, reasoning
            
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}，尝试文本解析")
            # 回退到文本解析
            label = self._parse_label_fallback(response_str)
            return label, ""

    def _extract_reasoning_from_json(self, data: dict) -> str:
        """从 JSON 响应中提取分类理由。"""
        reasoning_keys = [
            "reason",
            "thinking",
            "rationale",
            "explanation",
            "analysis",
            "理由",
            "说明",
        ]

        for key in reasoning_keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        answer = data.get("answer")
        if isinstance(answer, str) and answer.strip():
            answer = answer.strip()
            if self._extract_label_from_text(answer) is None:
                return answer

        return ""

    def _extract_label_from_json(self, data: dict) -> Optional[SemanticLabel]:
        """从 JSON 响应中提取标签，兼容多种字段命名。"""
        label_keys = [
            "tag",
            "label",
            "category",
            "classification",
            "class",
            "type",
            "标签",
            "分类",
            "类别",
            "answer",
            "答案",
        ]

        for key in label_keys:
            value = data.get(key)
            if isinstance(value, str):
                label = self._extract_label_from_text(value)
                if label is not None:
                    return label

        for value in data.values():
            if isinstance(value, str):
                label = self._extract_label_from_text(value)
                if label is not None:
                    return label

        return None

    def _extract_label_from_text(self, text: str) -> Optional[SemanticLabel]:
        """从自由文本中提取标签，不在无法确认时误判为 Irrelevant。"""
        import re

        if not text:
            return None

        normalized = re.sub(r"\s+", " ", text).strip()
        normalized_lower = normalized.lower()

        for label in SemanticLabel:
            value = label.value
            value_lower = value.lower()
            compact = value_lower.replace(" ", "")
            normalized_compact = normalized_lower.replace(" ", "")

            if normalized_lower == value_lower or normalized_compact == compact:
                return label

            if normalized_lower.startswith(value_lower):
                return label

            pattern = r"(?<![a-z])" + re.escape(value_lower) + r"(?![a-z])"
            if re.search(pattern, normalized_lower):
                return label

        return None
    
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
        priority_order = [SemanticLabel.IRRELEVANT, SemanticLabel.OTHERS, 
                         SemanticLabel.METHOD, SemanticLabel.RECOMMENDATION, SemanticLabel.OVERVIEW]
        for label in priority_order:
            if label.value.lower() in label_str.lower():
                return label
        
        return SemanticLabel.IRRELEVANT
