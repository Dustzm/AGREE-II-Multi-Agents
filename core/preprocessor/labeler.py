"""
语义标签分类器

使用 LLM 为文档内容块分配语义标签，作为预处理流水线的核心组件
"""
import logging
import re
from pathlib import Path
from typing import List, Optional

from core.llm.gateway import LLMGateway
from core.llm.json_output import parse_json_object
from core.llm.recovery import RecoveryBudget
from core.utils.llm_errors import InvalidModelJSON
from .label_checkpoint import LabelCheckpoint
from .pojo import TitleContentBlock, SemanticChunk, SemanticLabel
from .section_titles import is_non_evidence_title

logger = logging.getLogger(__name__)


class SemanticLabeler:
    """
    语义标签分类器
    
    使用 LLM 为每个标题-内容块打语义标签
    """
    
    def __init__(self, lang: str = "zh"):
        """
        初始化分类器；模型配置取自当前任务快照，分类失败始终抛出异常。
        
        Args:
            lang: Prompt 语言（zh/en）
        """
        self.lang = lang
        self.prompt_template = self._load_prompt_template()
    
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
        """调用模型获取标签文本；截断或空响应不会被记录为成功断点。"""
        from core.runtime import ExecutionStage, execution_stage

        gateway = LLMGateway.from_settings()
        async with execution_stage(ExecutionStage.LABELER):
            reply = await gateway.acomplete(
                "labeler:semantic_classification",
                [{"role": "user", "content": prompt}],
                max_tokens=gateway.output_limit(2500),
            )
        text = reply.checked_text()
        if not text:
            raise InvalidModelJSON("语义分类模型返回空响应")
        return text

    async def _classify(self, prompt: str) -> tuple:
        return self._parse_json_response(await self._call_llm(prompt))

    async def label_chunks(
        self, chunks: List[TitleContentBlock], max_concurrency: int = 0,
        total_pages: int = 0, checkpoint: LabelCheckpoint | None = None,
    ) -> List[SemanticChunk]:
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
            
        logger.info(f"开始标签分类，共 {len(chunks)} 个块，并发数 {max_concurrency}，文档总页数 {total_pages}")

        queue: asyncio.Queue[tuple[int, TitleContentBlock]] = asyncio.Queue()
        for indexed_chunk in enumerate(chunks):
            queue.put_nowait(indexed_chunk)
        semantic_chunks: list[SemanticChunk | None] = [None] * len(chunks)

        async def worker() -> None:
            while True:
                try:
                    idx, chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                cached = checkpoint.get(idx, chunk) if checkpoint is not None else None
                result = await self._process_single_chunk(
                    idx,
                    chunk,
                    len(chunks),
                    total_pages,
                    cached=cached,
                )
                semantic_chunks[idx] = result
                if checkpoint is not None and cached is None and not chunk.is_appendix:
                    checkpoint.record(idx, chunk, result)

        workers = [
            asyncio.create_task(worker())
            for _ in range(min(max_concurrency, len(chunks)))
        ]
        if workers:
            try:
                await asyncio.gather(*workers)
            except Exception:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                raise
        if any(chunk is None for chunk in semantic_chunks):
            raise RuntimeError("语义分类未覆盖全部内容块")
        return [chunk for chunk in semantic_chunks if chunk is not None]

    async def _process_single_chunk(
        self,
        idx: int,
        chunk: TitleContentBlock,
        total: int,
        total_pages: int = 1,
        cached: tuple[SemanticLabel, str] | None = None,
    ) -> SemanticChunk:
        """处理单个块的标签分类"""
        # 附件块默认标记为 APPENDIX 并跳过 LLM 分类；标题整体为参考文献/修订历史/索引的
        # 附件标为 IRRELEVANT，避免进入证据池（按整题匹配，避免误伤 preferences 等正文）。
        if getattr(chunk, 'is_appendix', False):
            if is_non_evidence_title(chunk.title):
                label = SemanticLabel.IRRELEVANT
                reasoning = "附件中的参考文献/修订历史/索引，标记为 Irrelevant"
            else:
                label = SemanticLabel.APPENDIX
                reasoning = "附件内容，自动标记为 Appendix"
            logger.info(f"[{idx+1}/{total}] {chunk.title[:20]}... -> {label.value} (附件，跳过分类)")
            return SemanticChunk(
                chunk_id=f"chunk_{idx+1:03d}",
                title=chunk.title,
                content=chunk.raw_text,
                label=label,
                start_page=chunk.title_page_idx,
                end_page=chunk.title_page_idx,
                reasoning=reasoning,
                block_ids=chunk.block_ids or [],
                section=chunk.section
            )

        # 获取当前页码（用于位置感知）
        current_page = chunk.title_page_idx + 1  # 转为 1-indexed

        if cached is not None:
            label, reasoning = cached
            logger.info("复用语义分类断点: chunk=%s", idx + 1)
        else:
            prompt = self._render_prompt(
                chunk.title, chunk.raw_text, chunk.section,
                page=current_page, total_pages=total_pages,
            )
            logger.info("语义标签分类: chunk=%s/%s title=%s", idx + 1, total, chunk.title[:50])
            logger.debug("语义标签提示词长度: %s", len(prompt))

            # 每个内容块允许一次重试，传输故障和无效输出共用额度。
            label, reasoning = await RecoveryBudget(1).arun(lambda: self._classify(prompt))

        start_page = chunk.title_page_idx
        end_page = start_page
        if chunk.contents:
            for content in chunk.contents:
                page = content.get("page_idx", start_page)
                if page > end_page:
                    end_page = page

        semantic_chunk = SemanticChunk(
            chunk_id=f"chunk_{idx+1:03d}",
            title=chunk.title,
            content=chunk.raw_text,
            label=label,
            start_page=start_page,
            end_page=end_page,
            reasoning=reasoning,
            block_ids=chunk.block_ids or [],
            section=chunk.section
        )

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
        # 过滤 Qwen3 thinking 模型的 <think>...</think> 标签
        response_str = re.sub(r'<think>.*?</think>', '', response_str, flags=re.DOTALL)
        response_str = response_str.strip()
        
        logger.debug("解析语义标签 JSON，响应长度=%s", len(response_str))
        
        # 尝试提取 JSON 块
        json_match = re.search(r'```json\s*(.*?)\s*```', response_str, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接解析为 JSON
            json_str = response_str
        
        data = parse_json_object(json_str)
        reasoning = self._extract_reasoning_from_json(data)
        label = self._extract_label_from_json(data)
        if label is None:
            raise InvalidModelJSON("语义标签结果不包含有效标签")
        logger.info("LLM 标签: %s", label.value)
        return label, reasoning

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

        return None
