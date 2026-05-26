"""
迭代评价器

使用 LiteLLM 逐个遍历 chunks，累积证据并动态评分
"""
import math
import json
import logging
from collections import Counter
from pathlib import Path
from typing import List, Optional, Dict, Any

import litellm
from pydantic import BaseModel, Field

from core.utils.token_tracker import add_litellm_usage
from core.utils.unicode_sanitizer import sanitize_unicode, strip_surrogates
from core.agents.base.model_config import (
    get_compatible_reasoning_kwargs,
    get_compatible_sampling_kwargs,
    get_provider_kwargs,
)

from core.config import get_settings
from core.skills import load_skill_config
from core.prompts.iterative_prompts import (
    render_extraction_prompt,
    render_scoring_prompt,
    render_final_report_prompt,
)
from core.preprocessor import EvidencePool
from core.preprocessor.iterator import EvidenceIterator
from core.preprocessor.pojo import SemanticChunk
from .tools.public_tools import get_task_paths
from core.utils.pdf_vision import get_stitched_pdf_pages_as_base64
from core.utils.cancellation import CancellationRequested, CancellationToken

logger = logging.getLogger(__name__)

_INFO_LOG_PREVIEW_LIMIT = 1200
_DEBUG_LOG_PREVIEW_LIMIT = 4000
_ITEM17_ESTIMATED_CHARS_PER_TOKEN = 3.5
_ITEM17_SCORING_TEXT_BUDGET_TOKENS = 30000
_ITEM17_SCORING_MULTIMODAL_BUDGET_TOKENS = 30000
_ITEM17_SCORING_IMAGE_TOKEN_OVERHEAD = 14000
_ITEM17_REPORT_BUDGET_TOKENS = 30000
_ITEM17_SCORING_MAX_EVIDENCE_ITEMS = 16
_ITEM17_REPORT_MAX_EVIDENCE_ITEMS = 20
_ITEM17_SCORING_MAX_CHARS_PER_EVIDENCE = 1200
_ITEM17_REPORT_MAX_CHARS_PER_EVIDENCE = 1800


# ============================================================================
# Pydantic Schema 定义 - 强制 LLM 输出结构
# ============================================================================

class ReasonItem(BaseModel):
    """单个评分理由"""
    point: str = Field(description="评分要点名称")
    explanation: str = Field(description="详细解释")
    evidence_chunks: List[str] = Field(default_factory=list, description="相关的 chunk_id 列表")


class StructuredReasoning(BaseModel):
    """结构化推理"""
    summary: str = Field(description="一句话总结")
    reasons: List[ReasonItem] = Field(description="分条理由列表")
    score_justification: str = Field(description="评分判定理由")


class FinalReportOutput(BaseModel):
    """最终报告输出 Schema"""
    step1_locate: str = Field(description="证据来源位置描述")
    step3_reasoning: StructuredReasoning = Field(description="结构化推理")




class IterativeEvaluator:
    """
    迭代评价器 - 遍历 chunks，累积证据，动态评分
    
    流程：
    1. 按文章原始顺序遍历 chunks
    2. 对每个 chunk 调用 LLM 提取证据
    3. 累积证据后调用 LLM 动态评分
    4. 满分 (7分) 时早停
    5. 最终生成完整 CoT 报告
    """
    
    def __init__(
        self,
        task_id: str,
        skill_id: str,
        lang: str = "en",
        cancellation_token: CancellationToken | None = None,
    ):
        """
        初始化评价器
        
        Args:
            task_id: 任务 ID
            skill_id: 条目 ID (如 "item_7")
            lang: 提示词语言 (默认 en)
        """
        self.task_id = task_id
        self.skill_id = skill_id
        self.lang = lang
        self.cancellation_token = cancellation_token
        
        # 累积状态
        self.evidence_cache: List[str] = []      # 累积的证据文本
        self.evidence_chunk_map: List[str] = []  # 每条证据对应的 chunk_id（与 evidence_cache 一一对应）
        self.evidence_sources: List[str] = []    # 所有处理过的 chunk_id 列表
        self.current_score: int = 1              # 当前评分
        self._chunk_lookup: Dict[str, SemanticChunk] = {}
        
        # 加载配置
        self.skill_config = load_skill_config(skill_id, check_enabled=True, lang=lang)
        self.domain_id = self.skill_config.get("domain", 1)
        
        # LLM 配置
        from core.agents.base.model_config import get_provider_config_for
        settings = get_settings()
        provider_cfg = get_provider_config_for(settings.llm.provider)
        prefix = provider_cfg["prefix"]
        self.model = f"{prefix}/{settings.llm.model_name}"
        # 优先使用用户配置的 base_url，其次使用 providers.json 中的 default_base_url
        self.llm_base_url = settings.llm.base_url or provider_cfg.get("default_base_url", "")
        self.llm_api_key = settings.llm.api_key or "dummy-key"
        self.llm_timeout = settings.llm.timeout
        
        logger.info(f"IterativeEvaluator 初始化: task={task_id}, skill={skill_id}, domain={self.domain_id}")

    def _raise_if_cancelled(self) -> None:
        if self.cancellation_token is not None:
            self.cancellation_token.raise_if_cancelled()

    @property
    def _is_item17(self) -> bool:
        return self.skill_id == "item_17"

    def _preview_text(self, text: str, limit: int = _INFO_LOG_PREVIEW_LIMIT) -> str:
        """生成适合日志的文本预览，避免超长内容压垮日志与 I/O。"""
        text = strip_surrogates(text)
        if not text:
            return ""

        if len(text) <= limit:
            return text

        return f"{text[:limit]}\n... [截断，原始长度 {len(text)} 字符]"
    
    def _find_source_pdf(self) -> str:
        """
        根据 task_id 在 input 目录下递归查找原始 PDF 文件
        
        task_id 格式形如 '1-2023-015E-03021630'，其中前缀 '1-2023-015E' 即为 PDF 文件名。
        
        Returns:
            PDF 文件的绝对路径，找不到时返回空字符串
        """
        settings = get_settings()
        input_dir = Path(settings.app.dir_input)
        
        # 从 task_id 提取 PDF 文件名前缀：去掉最后的时间戳后缀
        # 例如 '1-2023-015E-03021630' -> '1-2023-015E'
        parts = self.task_id.split('-')
        # task_id 末尾是一个纯数字时间戳(如03021630)，去掉它
        if parts and parts[-1].isdigit() and len(parts[-1]) >= 8:
            pdf_stem = '-'.join(parts[:-1])
        else:
            pdf_stem = self.task_id
        
        pdf_filename = f"{pdf_stem}.pdf"
        
        # 递归搜索 input 目录
        for pdf_file in input_dir.rglob(pdf_filename):
            logger.info(f"找到源 PDF: {pdf_file}")
            return str(pdf_file)
        
        logger.warning(f"未找到源 PDF: {pdf_filename} (搜索目录: {input_dir})")
        return ""

    def _estimate_token_count(self, text: str) -> int:
        """基于字符数的轻量级 token 估算，用于 item_17 预算控制。"""
        clean_text = strip_surrogates(text)
        if not clean_text:
            return 0
        return max(1, math.ceil(len(clean_text) / _ITEM17_ESTIMATED_CHARS_PER_TOKEN))

    def _normalize_evidence_key(self, text: str) -> str:
        """对证据做轻量规范化，用于去重近似重复内容。"""
        normalized = " ".join(strip_surrogates(text).lower().split())
        return normalized[:400]

    def _trim_evidence_text(self, text: str, max_chars: int) -> str:
        """裁剪单条证据长度，避免少数超长引文独占预算。"""
        clean_text = strip_surrogates(text)
        if len(clean_text) <= max_chars:
            return clean_text

        truncated = clean_text[:max_chars].rsplit(" ", 1)[0].strip()
        if not truncated:
            truncated = clean_text[:max_chars].strip()
        return f"{truncated}\n... [证据已截断]"

    def _get_chunk_by_id(self, chunk_id: str) -> Optional[SemanticChunk]:
        return self._chunk_lookup.get(chunk_id)

    def _get_item17_target_pages(self) -> list[int]:
        """根据当前证据分布选出高频页码，供 item_17 评分阶段优先保留。"""
        page_counter: Counter[int] = Counter()
        for chunk_id in self.evidence_chunk_map:
            chunk = self._get_chunk_by_id(chunk_id)
            if chunk and chunk.start_page is not None:
                page_counter[chunk.start_page] += 1
        return sorted(page for page, _count in page_counter.most_common(2))

    def _collect_item17_candidates(self, max_chars_per_evidence: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """收集 item_17 的证据候选，并做去重/裁剪。"""
        target_pages = self._get_item17_target_pages()
        seen_keys: set[str] = set()
        candidates: list[dict[str, Any]] = []

        raw_pairs = list(zip(self.evidence_cache, self.evidence_chunk_map))
        for recency_rank, (evidence, chunk_id) in enumerate(reversed(raw_pairs)):
            normalized_key = self._normalize_evidence_key(evidence)
            if not normalized_key or normalized_key in seen_keys:
                continue
            seen_keys.add(normalized_key)

            chunk = self._get_chunk_by_id(chunk_id)
            page = chunk.start_page if chunk else None
            candidates.append({
                "evidence": self._trim_evidence_text(evidence, max_chars_per_evidence),
                "chunk_id": strip_surrogates(chunk_id),
                "page": page,
                "is_target_page": page in target_pages,
                "recency_rank": recency_rank,
            })

        meta = {
            "original_count": len(self.evidence_cache),
            "deduped_count": len(candidates),
            "target_pages": target_pages,
        }
        return candidates, meta

    def _prioritize_item17_candidates(
        self,
        candidates: list[dict[str, Any]],
        *,
        prefer_unique_chunks: bool,
    ) -> list[dict[str, Any]]:
        """按目标页、最近性和 chunk 多样性排序 item_17 证据候选。"""
        ordered: list[dict[str, Any]] = []
        seen_chunks: set[str] = set()

        partitions = [
            [candidate for candidate in candidates if candidate["is_target_page"]],
            [candidate for candidate in candidates if not candidate["is_target_page"]],
        ]

        if prefer_unique_chunks:
            for partition in partitions:
                for candidate in partition:
                    chunk_id = candidate["chunk_id"]
                    if chunk_id not in seen_chunks:
                        ordered.append(candidate)
                        seen_chunks.add(chunk_id)
                for candidate in partition:
                    if candidate["chunk_id"] in seen_chunks and candidate not in ordered:
                        ordered.append(candidate)
        else:
            for partition in partitions:
                ordered.extend(partition)

        return ordered

    def _fit_item17_candidates_to_budget(
        self,
        *,
        stage: str,
        candidates: list[dict[str, Any]],
        build_prompt,
        token_budget: int,
        max_items: int,
        original_count: int,
        deduped_count: int,
        target_pages: list[int],
    ) -> dict[str, Any]:
        """在预算内为 item_17 选择证据子集。"""
        selected_evidence: list[str] = []
        selected_chunk_ids: list[str] = []
        selected_pages: list[int] = []
        selected_chunks: set[str] = set()
        selected_signatures: set[tuple[str, str]] = set()

        for unique_only in (True, False):
            for candidate in candidates:
                if len(selected_evidence) >= max_items:
                    break
                signature = (candidate["chunk_id"], candidate["evidence"])
                if signature in selected_signatures:
                    continue
                if unique_only and candidate["chunk_id"] in selected_chunks:
                    continue

                trial_evidence = selected_evidence + [candidate["evidence"]]
                trial_chunk_ids = selected_chunk_ids + [candidate["chunk_id"]]
                prompt_text = build_prompt(trial_evidence, trial_chunk_ids)
                estimated_tokens = self._estimate_token_count(prompt_text)

                if estimated_tokens <= token_budget:
                    selected_evidence = trial_evidence
                    selected_chunk_ids = trial_chunk_ids
                    if candidate["page"] is not None:
                        selected_pages.append(candidate["page"])
                    selected_chunks.add(candidate["chunk_id"])
                    selected_signatures.add(signature)

        if not selected_evidence:
            base_prompt = build_prompt([], [])
            estimated_tokens = self._estimate_token_count(base_prompt)
            return {
                "status": "error",
                "error": f"{stage}超长：估算输入约 {estimated_tokens} tokens，超过预算 {token_budget}。",
                "estimated_tokens": estimated_tokens,
                "selected_count": 0,
                "selected_evidence": [],
                "selected_chunk_ids": [],
                "selected_pages": [],
                "original_count": original_count,
                "deduped_count": deduped_count,
                "target_pages": target_pages,
            }

        prompt_text = build_prompt(selected_evidence, selected_chunk_ids)
        estimated_tokens = self._estimate_token_count(prompt_text)
        if estimated_tokens > token_budget:
            return {
                "status": "error",
                "error": f"{stage}超长：估算输入约 {estimated_tokens} tokens，超过预算 {token_budget}。",
                "estimated_tokens": estimated_tokens,
                "selected_count": len(selected_evidence),
                "selected_evidence": selected_evidence,
                "selected_chunk_ids": selected_chunk_ids,
                "selected_pages": selected_pages,
                "original_count": original_count,
                "deduped_count": deduped_count,
                "target_pages": target_pages,
            }

        return {
            "status": "success",
            "prompt": prompt_text,
            "estimated_tokens": estimated_tokens,
            "selected_count": len(selected_evidence),
            "selected_evidence": selected_evidence,
            "selected_chunk_ids": selected_chunk_ids,
            "selected_pages": selected_pages,
            "original_count": original_count,
            "deduped_count": deduped_count,
            "target_pages": target_pages,
        }

    def _log_item17_budget(self, stage: str, payload: dict[str, Any]) -> None:
        logger.info(
            f"[item_17][{stage}] 预算详情: 原始证据={payload.get('original_count', 0)}, "
            f"去重后={payload.get('deduped_count', 0)}, 发送证据={payload.get('selected_count', 0)}, "
            f"估算tokens={payload.get('estimated_tokens', 0)}, mode={payload.get('mode', 'text')}, "
            f"目标页={payload.get('target_pages', [])}, 已选页={payload.get('selected_pages', [])}, "
            f"降级路径={payload.get('degraded_from', 'none')}"
        )

    def _build_item17_scoring_payload(self, scoring_table: str) -> dict[str, Any]:
        """为 item_17 评分阶段构建预算内请求，优先多模态，必要时降级到纯文本。"""
        candidates, meta = self._collect_item17_candidates(_ITEM17_SCORING_MAX_CHARS_PER_EVIDENCE)
        ordered_candidates = self._prioritize_item17_candidates(candidates, prefer_unique_chunks=False)
        title = self.skill_config.get("title", "")
        shots = self.skill_config.get("shots", [])
        target_pages = meta["target_pages"]
        vision_prompt = self.skill_config.get("vision_prompt", "Please judge based on the provided images.")

        def build_text_prompt(evidence_subset: list[str], chunk_ids: list[str]) -> str:
            return strip_surrogates(render_scoring_prompt(
                item_id=self.skill_id,
                title=title,
                shots=shots,
                evidence_cache=evidence_subset,
                scoring_table=scoring_table,
                n_sources=len(set(chunk_ids)),
                special_instruction=self.skill_config.get("special_rules", ""),
            ))

        def build_multimodal_prompt(evidence_subset: list[str], chunk_ids: list[str]) -> str:
            base_prompt = build_text_prompt(evidence_subset, chunk_ids)
            return strip_surrogates(base_prompt + "\n\n" + vision_prompt)

        degraded_from = "none"
        if get_settings().llm.is_vision_model and target_pages:
            multimodal_payload = self._fit_item17_candidates_to_budget(
                stage="多模态评分",
                candidates=ordered_candidates,
                build_prompt=build_multimodal_prompt,
                token_budget=_ITEM17_SCORING_MULTIMODAL_BUDGET_TOKENS - _ITEM17_SCORING_IMAGE_TOKEN_OVERHEAD,
                max_items=_ITEM17_SCORING_MAX_EVIDENCE_ITEMS,
                original_count=meta["original_count"],
                deduped_count=meta["deduped_count"],
                target_pages=target_pages,
            )
            if multimodal_payload["status"] == "success":
                pdf_path = self._find_source_pdf()
                b64_img = get_stitched_pdf_pages_as_base64(pdf_path, target_pages)
                if b64_img:
                    multimodal_payload.update({
                        "mode": "multimodal",
                        "messages": [
                            {"role": "system", "content": "You are a scoring assistant. Output JSON only."},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": multimodal_payload["prompt"]},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}},
                                ],
                            },
                        ],
                        "estimated_tokens": multimodal_payload["estimated_tokens"] + _ITEM17_SCORING_IMAGE_TOKEN_OVERHEAD,
                        "degraded_from": "none",
                    })
                    self._log_item17_budget("评分", multimodal_payload)
                    return multimodal_payload

                degraded_from = "multimodal(no_image)"
                logger.warning("item_17 多模态评分图片生成失败，降级为纯文本评分。")
            else:
                degraded_from = "multimodal(over_budget)"
                logger.warning(multimodal_payload["error"])

        text_payload = self._fit_item17_candidates_to_budget(
            stage="文本评分",
            candidates=ordered_candidates,
            build_prompt=build_text_prompt,
            token_budget=_ITEM17_SCORING_TEXT_BUDGET_TOKENS,
            max_items=_ITEM17_SCORING_MAX_EVIDENCE_ITEMS,
            original_count=meta["original_count"],
            deduped_count=meta["deduped_count"],
            target_pages=target_pages,
        )
        if text_payload["status"] == "error":
            return text_payload

        text_payload.update({
            "mode": "text",
            "messages": [
                {"role": "system", "content": "You are a scoring assistant. Output JSON only."},
                {"role": "user", "content": text_payload["prompt"]},
            ],
            "degraded_from": degraded_from,
        })
        self._log_item17_budget("评分", text_payload)
        return text_payload

    def _build_item17_report_payload(self, scoring_table: str) -> dict[str, Any]:
        """为 item_17 最终报告构建纯文本压缩证据子集。"""
        candidates, meta = self._collect_item17_candidates(_ITEM17_REPORT_MAX_CHARS_PER_EVIDENCE)
        ordered_candidates = self._prioritize_item17_candidates(candidates, prefer_unique_chunks=True)
        title = self.skill_config.get("title", "")
        domain_name = self.skill_config.get("domain_name", "")
        shots = self.skill_config.get("shots", [])

        def build_report_prompt(evidence_subset: list[str], chunk_ids: list[str]) -> str:
            return strip_surrogates(render_final_report_prompt(
                item_id=self.skill_id,
                domain_name=domain_name,
                title=title,
                shots=shots,
                scoring_table=scoring_table,
                evidence_cache=evidence_subset,
                evidence_chunk_map=chunk_ids,
                final_score=self.current_score,
            ))

        payload = self._fit_item17_candidates_to_budget(
            stage="最终报告",
            candidates=ordered_candidates,
            build_prompt=build_report_prompt,
            token_budget=_ITEM17_REPORT_BUDGET_TOKENS,
            max_items=_ITEM17_REPORT_MAX_EVIDENCE_ITEMS,
            original_count=meta["original_count"],
            deduped_count=meta["deduped_count"],
            target_pages=meta["target_pages"],
        )
        if payload["status"] == "error":
            return payload

        payload.update({
            "mode": "text",
            "degraded_from": "none",
        })
        self._log_item17_budget("最终报告", payload)
        return payload
    
    def _log_llm_call(
        self,
        stage: str,
        response,
        content: str,
        reasoning_content: str | None,
    ) -> None:
        """
        统一记录 LLM 调用的完整日志
        
        Args:
            stage: 调用阶段名称 (如 "Evidence Extraction", "Dynamic Scoring")
            response: LiteLLM 响应对象
            content: 提取的最终输出内容
            reasoning_content: 思考内容 (可能为 None)
        """
        # 调用元信息
        usage = getattr(response, 'usage', None)
        model_used = getattr(response, 'model', self.model)
        
        logger.info(f"=== [{stage}] LLM Call Meta ===")
        logger.info(f"  Model: {model_used}")
        logger.info(f"  Skill: {self.skill_id}")
        if usage:
            prompt_tokens = getattr(usage, 'prompt_tokens', 'N/A')
            completion_tokens = getattr(usage, 'completion_tokens', 'N/A')
            total_tokens = getattr(usage, 'total_tokens', 'N/A')
            logger.info(f"  Tokens: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")
        logger.info(f"  Output Length: {len(content)}")
        if reasoning_content:
            logger.info(f"  Reasoning Length: {len(reasoning_content)}")
        
        # INFO 级别只保留摘要，完整内容仅在 DEBUG 时输出
        if reasoning_content:
            logger.info(f"=== [{stage}] Reasoning Preview ===")
            logger.info(self._preview_text(reasoning_content))
            logger.info(f"=== [{stage}] End Reasoning Preview ===")
            logger.debug(f"=== [{stage}] Reasoning Content (full) ===\n{reasoning_content}\n=== [{stage}] End Reasoning Content ===")
        
        logger.info(f"=== [{stage}] Output Preview ===")
        logger.info(self._preview_text(content))
        logger.info(f"=== [{stage}] End Output Preview ===")
        logger.debug(f"=== [{stage}] Output Content (full) ===\n{content}\n=== [{stage}] End Output Content ===")
    
    def _clean_thinking_content(self, content: str) -> str:
        """
        移除内容中的 <think>...</think> 标签及其内容
        
        Args:
            content: 原始内容
            
        Returns:
            清理后的内容
        """
        import re
        # 使用 DOTALL 模式匹配跨行的 think 标签
        return strip_surrogates(re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip())
    
    def _extract_content_from_response(self, response) -> tuple[str, str | None]:
        """
        从 LLM 响应中提取内容和思考内容
        
        Args:
            response: LiteLLM 响应对象
            
        Returns:
            (content, reasoning_content) 元组
        """
        message = response.choices[0].message
        content = strip_surrogates(message.content.strip()) if message.content else ""
        
        # vLLM + reasoning-parser 模式下，reasoning 内容会在单独字段
        # 兼容最新 vLLM 的 'reasoning' 字段和旧版 'reasoning_content'
        reasoning_content = getattr(message, 'reasoning', None) or getattr(message, 'reasoning_content', None)
        if isinstance(reasoning_content, str):
            reasoning_content = strip_surrogates(reasoning_content)
        
        # 清理可能残留的 think 标签
        content = self._clean_thinking_content(content)
        
        return content, reasoning_content

    def _extract_json_object_text(self, content: str) -> str:
        """
        从模型输出中提取 JSON 对象文本。

        Args:
            content: 模型原始输出

        Returns:
            可传入 json.loads 的 JSON 对象字符串
        """
        import re

        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()

        json_match = re.search(r"\{[\s\S]*\}", text)
        if json_match:
            return json_match.group(0)
        return text
    
    def evaluate(self) -> Dict[str, Any]:
        """
        执行迭代评价
        
        Returns:
            评价结果字典
        """
        # 1. 加载证据池
        self._raise_if_cancelled()
        paths = get_task_paths(self.task_id)
        if not paths:
            return self._error_result(f"任务不存在: {self.task_id}")
        
        prep_dir = paths["prep_dir"]
        
        try:
            pool = EvidencePool.load(prep_dir)
        except FileNotFoundError as e:
            return self._error_result(f"证据池加载失败: {e}")
        self._chunk_lookup = {chunk.chunk_id: chunk for chunk in pool.chunks}
        
        # 2. 创建迭代器
        iterator = EvidenceIterator(pool.chunks, self.domain_id)
        
        if len(iterator) == 0:
            logger.warning(f"领域 {self.domain_id} 无相关 chunks，自动评分为 1")
            return self._no_evidence_result()
        
        logger.info(f"开始迭代评价: {len(iterator)} 个 chunks")
        
        # 3. 迭代遍历
        # 3. 迭代遍历 (使用 batch 加速)
        batch_size = 5
        iterator_list = list(iterator)
        
        for i in range(0, len(iterator_list), batch_size):
            self._raise_if_cancelled()
            batch_chunks = iterator_list[i:i+batch_size]
            logger.info(f"处理 batch {i//batch_size + 1}/{(len(iterator_list)+batch_size-1)//batch_size}: 包含 {len(batch_chunks)} 个 chunks")
            
            # 3.1 提取证据 (Batch) - 返回 (evidence_texts, chunk_ids)
            new_evidence, new_chunk_ids = self._extract_evidence_batch(batch_chunks)
            
            if new_evidence:
                self.evidence_cache.extend(sanitize_unicode(new_evidence))
                self.evidence_chunk_map.extend(sanitize_unicode(new_chunk_ids))
                for chunk in batch_chunks:
                    self.evidence_sources.append(strip_surrogates(chunk.chunk_id))
                logger.info(f"Batch 提取到 {len(new_evidence)} 条证据")
            
            # 3.2 动态评分 (仅在有新证据时)
            if self.evidence_cache:
                self.current_score = self._score_current()
                logger.info(f"当前评分: {self.current_score}")
            
            # 3.3 早停检查
            if self.current_score == 7:
                logger.info(f"达到满分 7 分，提前结束")
                break
        
        # 4. 生成最终报告
        return self._synthesize_report()
    
    def _extract_evidence_batch(self, chunks: List[SemanticChunk]) -> tuple[List[str], List[str]]:
        """
        从 batch chunks 提取与评价标准相关的证据
        
        Args:
            chunks: 语义块列表
        
        Returns:
            (evidence_texts, chunk_ids) 元组：证据文本列表和对应的 chunk_id 列表
        """
        # 记录处理的 chunk 内容
        self._raise_if_cancelled()
        for c in chunks:
            logger.info(f"Evaluating Chunk: {c.title}")
            logger.debug(f"Chunk Content: {self._preview_text(c.content, limit=_DEBUG_LOG_PREVIEW_LIMIT)}")

        # 拼接 chunk 内容（带 chunk_id 标记，便于 LLM 识别来源）
        combined_content = []
        for c in chunks:
            chunk_text = f"[{strip_surrogates(c.chunk_id)}] ## {strip_surrogates(c.title)}\n{strip_surrogates(c.content)}"
            
            # 条目17：注入格式元数据供 LLM 评价推荐意见呈现方式
            if self.skill_id == "item_17" and getattr(c, "format_hints", None):
                format_desc = c.format_hints.get("format_description", "")
                if format_desc:
                    chunk_text += f"\n\n{strip_surrogates(format_desc)}"
            
            combined_content.append(chunk_text)
        
        full_text = "\n\n".join(combined_content)

        # 格式化评分表格 (作为提取依据)
        scoring_rows = []
        for sp in self.skill_config.get("scoring_principle", []):
            condition = sp.get("condition", sp.get("condition_zh", ""))
            scoring_rows.append(f"| {sp['range']} | {condition} |")
        scoring_table = "\n".join(scoring_rows)

        prompt = render_extraction_prompt(
            item_id=self.skill_id,
            title=self.skill_config.get("title", ""),
            scoring_table=scoring_table,
            chunk_content=full_text,
            special_instruction=self.skill_config.get("special_rules", ""),
        )
        prompt = strip_surrogates(prompt)
        
        # 定义 Pydantic Schema 用于约束输出（新格式：包含 chunk_id）
        from pydantic import BaseModel, Field
        
        class EvidenceItem(BaseModel):
            chunk_id: str = Field(description="The chunk_id where the evidence was found")
            evidence: str = Field(description="The extracted evidence text")
        
        class EvidenceOutput(BaseModel):
            evidence: List[EvidenceItem] = Field(description="List of evidence items with chunk_id")

        try:
            self._raise_if_cancelled()
            # 使用 response_format={ "type": "json_object" } 配合 Pydantic Schema 约束
            system_instruction = "You are a strict extraction system. Output JSON only."
            # 提示词已在 iterative_prompts.py 中修改为新格式

            logger.info("=== [Evidence Extraction] Input Prompt Preview ===")
            logger.info(self._preview_text(prompt))
            logger.info("==========================================")
            logger.debug(f"=== [Evidence Extraction] Input Prompt (full) ===\n{prompt}\n==========================================")

            response = litellm.completion(
                model=self.model,
                messages=sanitize_unicode([
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt}
                ]),
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                max_tokens=8192,
                response_format={"type": "json_object"},
                timeout=self.llm_timeout,
                **get_compatible_reasoning_kwargs(self.model),
                **get_compatible_sampling_kwargs(
                    self.model,
                    temperature=0,
                    top_p=0.1,
                    seed=42,
                ),
                **get_provider_kwargs(),
            )
            
            # 统计 Token 使用
            add_litellm_usage(response)
            
            # 提取内容和思考内容
            content, reasoning_content = self._extract_content_from_response(response)
            
            # 记录完整 LLM 调用日志
            self._log_llm_call(
                stage="Evidence Extraction",
                response=response,
                content=content,
                reasoning_content=reasoning_content,
            )
            
            # 解析 JSON（新格式：包含 chunk_id）
            evidence_texts: List[str] = []
            chunk_ids: List[str] = []
            
            try:
                data = json.loads(content)
                if isinstance(data, dict):
                    evidence_list = data.get("evidence", [])
                elif isinstance(data, list):
                    evidence_list = data
                else:
                    evidence_list = []
                
                # 解析新格式 [{chunk_id, evidence}]
                for item in evidence_list:
                    if isinstance(item, dict):
                        # 新格式：{"chunk_id": "xxx", "evidence": "text"}
                        chunk_id = item.get("chunk_id", "unknown")
                        evidence_text = item.get("evidence", "")
                        if evidence_text:
                            evidence_texts.append(strip_surrogates(str(evidence_text)))
                            chunk_ids.append(strip_surrogates(str(chunk_id)))
                    elif isinstance(item, str) and item:
                        # 兼容旧格式：纯字符串数组（回退到第一个 chunk）
                        evidence_texts.append(strip_surrogates(str(item)))
                        chunk_ids.append(strip_surrogates(chunks[0].chunk_id if chunks else "unknown"))
                        
            except json.JSONDecodeError:
                import re
                # 尝试匹配 JSON 数组
                match = re.search(r'\[.*\]', content, re.DOTALL)
                if match:
                    try:
                        evidence_list = json.loads(match.group(0))
                        for item in evidence_list:
                            if isinstance(item, dict):
                                chunk_id = item.get("chunk_id", "unknown")
                                evidence_text = item.get("evidence", "")
                                if evidence_text:
                                    evidence_texts.append(strip_surrogates(str(evidence_text)))
                                    chunk_ids.append(strip_surrogates(str(chunk_id)))
                            elif isinstance(item, str) and item:
                                evidence_texts.append(strip_surrogates(str(item)))
                                chunk_ids.append(strip_surrogates(chunks[0].chunk_id if chunks else "unknown"))
                    except json.JSONDecodeError:
                        pass
            
            return evidence_texts, chunk_ids
            
        except CancellationRequested:
            raise
        except Exception as e:
            logger.exception("Batch 证据提取失败: %s", e)
            raise

    
    def _score_current(self) -> int:
        """
        根据当前累积证据评分
        
        Returns:
            当前评分 (1-7)
        """
        # 格式化评分表格
        scoring_rows = []
        for sp in self.skill_config.get("scoring_principle", []):
            condition = sp.get("condition", sp.get("condition_zh", ""))
            scoring_rows.append(f"| {sp['range']} | {condition} |")
        scoring_table = "\n".join(scoring_rows)
        
        content = ""
        try:
            self._raise_if_cancelled()
            if self._is_item17:
                payload = self._build_item17_scoring_payload(scoring_table)
                if payload["status"] == "error":
                    logger.warning(payload["error"])
                    return self.current_score
                messages = payload["messages"]
                api_prompt = payload["prompt"]
                if payload["mode"] == "multimodal":
                    logger.info(f"多模态评分(item_17)：按密度选取最高频的 {len(payload['target_pages'])} 页生成长图 {payload['target_pages']}")
                    logger.info(">> 已成功挂载 VLM 长拼接单图数据")
            else:
                prompt = render_scoring_prompt(
                    item_id=self.skill_id,
                    title=self.skill_config.get("title", ""),
                    shots=self.skill_config.get("shots", []),
                    evidence_cache=self.evidence_cache,
                    scoring_table=scoring_table,
                    n_sources=len(self.evidence_sources),
                    special_instruction=self.skill_config.get("special_rules", ""),
                )
                api_prompt = strip_surrogates(prompt)
                messages = [
                    {"role": "system", "content": "You are a scoring assistant. Output JSON only."},
                    {"role": "user", "content": api_prompt},
                ]

            logger.info("=== [Dynamic Scoring] Input Prompt Preview ===")
            logger.info(self._preview_text(api_prompt))
            logger.info("======================================")
            logger.debug(f"=== [Dynamic Scoring] Input Prompt (full) ===\n{api_prompt}\n======================================")

            response = litellm.completion(
                model=self.model,
                messages=sanitize_unicode(messages),
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                max_tokens=8096,
                response_format={"type": "json_object"},
                timeout=self.llm_timeout,
                **get_compatible_reasoning_kwargs(self.model),
                **get_compatible_sampling_kwargs(
                    self.model,
                    temperature=0,
                    top_p=0.1,
                    seed=42,
                ),
                **get_provider_kwargs(),
            )
            
            # 统计 Token 使用
            add_litellm_usage(response)
            
            # 提取内容和思考内容
            content, reasoning_content = self._extract_content_from_response(response)

            # 记录完整 LLM 调用日志
            self._log_llm_call(
                stage="Dynamic Scoring",
                response=response,
                content=content,
                reasoning_content=reasoning_content,
            )
            
            # 解析 JSON。部分兼容 OpenAI 的服务会在 json_object 模式下仍返回 markdown fence。
            data = json.loads(self._extract_json_object_text(content))
            score = data.get("score", self.current_score)
            
            # 处理可能的范围格式如 "3-4"
            if isinstance(score, str):
                if "-" in score:
                    # 取范围的较高值
                    parts = score.split("-")
                    score = int(parts[-1].strip())
                else:
                    score = int(score)
            
            if 1 <= score <= 7:
                logger.info(f"从 JSON 提取到评分: {score}")
                return score
            else:
                logger.warning(f"评分 {score} 超出范围，使用当前值: {self.current_score}")
                return self.current_score
            
        except CancellationRequested:
            raise
        except json.JSONDecodeError as e:
            logger.exception("评分 JSON 解析失败: %s", e)
            raise
            
        except Exception as e:
            logger.exception("评分失败: %s", e)
            raise
    
    def _synthesize_report(self) -> Dict[str, Any]:
        """
        综合所有证据生成最终报告
        
        Returns:
            完整评价结果
        """
        # 格式化评分表格
        scoring_rows = []
        for sp in self.skill_config.get("scoring_principle", []):
            condition = sp.get("condition", sp.get("condition_zh", ""))
            scoring_rows.append(f"| {sp['range']} | {condition} |")
        scoring_table = "\n".join(scoring_rows)
        
        report_evidence = self.evidence_cache
        report_chunk_ids = self.evidence_chunk_map
        if self._is_item17:
            payload = self._build_item17_report_payload(scoring_table)
            if payload["status"] == "error":
                logger.error(payload["error"])
                return self._error_result(payload["error"])
            prompt = payload["prompt"]
            report_evidence = payload["selected_evidence"]
            report_chunk_ids = payload["selected_chunk_ids"]
        else:
            prompt = render_final_report_prompt(
                item_id=self.skill_id,
                domain_name=self.skill_config.get("domain_name", ""),
                title=self.skill_config.get("title", ""),
                shots=self.skill_config.get("shots", []),
                scoring_table=scoring_table,
                evidence_cache=self.evidence_cache,
                evidence_chunk_map=self.evidence_chunk_map,  # 使用精确映射
                final_score=self.current_score,
            )
            prompt = strip_surrogates(prompt)
        
        logger.info("=== [Final Report] Input Prompt Preview ===")
        logger.info(self._preview_text(prompt))
        logger.info("===================================")
        logger.debug(f"=== [Final Report] Input Prompt (full) ===\n{prompt}\n===================================")
        
        max_retries = 3
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                self._raise_if_cancelled()
                # 使用 Pydantic Schema 强制输出结构
                system_instruction = "You are an evaluation report generator. Output valid JSON matching the required schema."
                
                # 最终报告只基于已抽取证据生成，避免重复注入图片导致上下文膨胀。
                messages = [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": prompt},
                ]
                
                response = litellm.completion(
                    model=self.model,
                    messages=sanitize_unicode(messages),
                    base_url=self.llm_base_url,
                    api_key=self.llm_api_key,
                    max_tokens=16384,
                    response_format={"type": "json_object"},  # 使用简单 json_object 避免 SGLang xgrammar 兼容性问题
                    timeout=self.llm_timeout,
                    **get_compatible_reasoning_kwargs(self.model),
                    **get_compatible_sampling_kwargs(
                        self.model,
                        temperature=0,
                        top_p=0.1,
                        seed=42,
                    ),
                    **get_provider_kwargs(),
                )
                
                # 统计 Token 使用
                add_litellm_usage(response)
                
                # 提取内容和思考内容
                content, reasoning_content = self._extract_content_from_response(response)

                # 记录完整 LLM 调用日志
                self._log_llm_call(
                    stage=f"Final Report (attempt {attempt}/{max_retries})",
                    response=response,
                    content=content,
                    reasoning_content=reasoning_content,
                )
                
                # 解析 JSON 并验证结构
                cot = sanitize_unicode(json.loads(self._extract_json_object_text(content)))
                
                # 使用 Pydantic 验证结构，确保字段完整
                try:
                    validated = FinalReportOutput.model_validate(cot)
                    step3_reasoning = validated.step3_reasoning.model_dump()
                    step1_locate = validated.step1_locate
                except Exception as validation_error:
                    logger.warning(f"Schema 验证失败，使用原始数据: {validation_error}")
                    step3_reasoning = cot.get("step3_reasoning", "")
                    step1_locate = cot.get("step1_locate", f"证据来自以下章节: {', '.join(self.evidence_sources)}")
                
                return sanitize_unicode({
                    "status": "success",
                    "skill_id": self.skill_id,
                    "domain": self.domain_id,
                    "title": self.skill_config.get("title", ""),
                    "chain_of_thought": {
                        "step1_locate": step1_locate,
                        "step2_evidence": report_evidence,
                        "step3_reasoning": step3_reasoning,
                        "score": self.current_score,
                    },
                    "evidence_sources": self.evidence_sources,
                    "iteration_stats": {
                        "total_chunks": len(self.evidence_sources) if self.evidence_sources else 0,
                        "evidence_count": len(self.evidence_cache),
                        "report_evidence_count": len(report_evidence),
                        "early_stopped": self.current_score == 7,
                    },
                })
                
            except CancellationRequested:
                raise
            except json.JSONDecodeError as e:
                last_error = e
                logger.exception("最终报告 JSON 解析失败 (第 %s/%s 次): %s", attempt, max_retries, e)
                
            except Exception as e:
                last_error = e
                logger.exception("最终报告生成失败 (第 %s/%s 次): %s", attempt, max_retries, e)
        
        # 所有重试均失败，返回错误结果，避免用简化报告掩盖真实异常。
        logger.error("最终报告生成在 %s 次尝试后均失败。最后错误: %s", max_retries, last_error)
        if isinstance(last_error, json.JSONDecodeError):
            return self._error_result(f"最终报告 JSON 解析失败: {last_error}")
        return self._error_result(f"最终报告生成失败: {last_error}")
    
    def _simple_report(self) -> Dict[str, Any]:
        """生成简化报告（LLM 调用失败时的回退）"""
        # 安全检查：如果有证据但评分为最低分，说明评分可能不准确
        if self.evidence_cache and self.current_score == 1:
            logger.warning(
                f"⚠️ 简化报告异常检测: 已提取 {len(self.evidence_cache)} 条证据，"
                f"但当前评分为 1（最低分），评分可能因 LLM 调用失败而不准确。"
                f"证据摘要: {[e[:80] + '...' for e in self.evidence_cache[:3]]}"
            )
        
        # 尝试从评分标准中找到对应的条件描述
        scoring_principles = self.skill_config.get("scoring_principle", [])
        matched_condition = ""
        for sp in scoring_principles:
            score_range = sp.get("range", "")
            # 处理范围格式如 "5-6" 或单个值 "7"
            if "-" in str(score_range):
                low, high = map(int, str(score_range).split("-"))
                if low <= self.current_score <= high:
                    matched_condition = sp.get("condition", sp.get("condition_zh", ""))
                    break
            elif str(score_range) == str(self.current_score):
                matched_condition = sp.get("condition", sp.get("condition_zh", ""))
                break
        
        reasoning = f"基于 {len(self.evidence_cache)} 条证据进行评价（注意：最终报告 LLM 调用失败，此为简化回退报告）。"
        if matched_condition:
            reasoning += f" 根据评分标准，符合【{matched_condition}】条件，评分为 {self.current_score}。"
        else:
            reasoning += f" 评分为 {self.current_score}。"
        
        # 如果有证据但评分异常低，在报告中明确标注
        if self.evidence_cache and self.current_score == 1:
            reasoning += f" ⚠️ 警告：已提取到 {len(self.evidence_cache)} 条证据但评分为最低分，建议人工复核。"
        
        return sanitize_unicode({
            "status": "fallback",  # 标记为回退报告，区别于正常 success
            "skill_id": self.skill_id,
            "domain": self.domain_id,
            "title": self.skill_config.get("title", ""),
            "chain_of_thought": {
                "step1_locate": f"证据来自以下章节: {', '.join(self.evidence_sources)}",
                "step2_evidence": self.evidence_cache,
                "step3_reasoning": reasoning,
                "score": self.current_score,
            },
            "evidence_sources": self.evidence_sources,
            "iteration_stats": {
                "total_chunks": len(self.evidence_sources),
                "evidence_count": len(self.evidence_cache),
                "early_stopped": self.current_score == 7,
            },
        })
    
    def _no_evidence_result(self) -> Dict[str, Any]:
        """无证据时的结果"""
        return sanitize_unicode({
            "status": "no_evidence",
            "skill_id": self.skill_id,
            "domain": self.domain_id,
            "title": self.skill_config.get("title", ""),
            "chain_of_thought": {
                "step1_locate": "无法定位：证据池中无该领域相关内容",
                "step2_evidence": [],
                "step3_reasoning": "由于证据池中不存在与该评价条目领域相关的语义块，无法提取有效证据进行评价。根据 AGREE-II 评分原则，信息完全缺失时应评为最低分。",
                "score": 1,
            },
            "evidence_sources": [],
            "iteration_stats": {
                "total_chunks": 0,
                "evidence_count": 0,
                "early_stopped": False,
            },
        })
    
    def _error_result(self, error: str) -> Dict[str, Any]:
        """错误结果"""
        return sanitize_unicode({
            "status": "error",
            "skill_id": self.skill_id,
            "error": error,
        })


def run_iterative_evaluation(
    task_id: str,
    skill_id: str,
    lang: str = "en",
    cancellation_token: CancellationToken | None = None,
) -> Dict[str, Any]:
    """
    执行迭代评价的快捷函数
    
    Args:
        task_id: 任务 ID
        skill_id: 条目 ID
        lang: 提示词语言
    
    Returns:
        评价结果
    """
    evaluator = IterativeEvaluator(task_id, skill_id, lang, cancellation_token=cancellation_token)
    try:
        return evaluator.evaluate()
    except CancellationRequested as exc:
        return sanitize_unicode({
            "status": "cancelled",
            "skill_id": skill_id,
            "error": str(exc),
        })


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "IterativeEvaluator",
    "run_iterative_evaluation",
]
