"""item_17（推荐意见的呈现）：在上下文预算内挑选证据，评分时可附带高频页长图。"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core.config import get_settings
from core.llm.context import ContextBudgetExceeded, RequestBudget
from core.preprocessor.pojo import SemanticChunk
from core.utils.pdf_vision import get_stitched_pdf_pages_as_base64
from core.utils.unicode_sanitizer import strip_surrogates

logger = logging.getLogger(__name__)

SCORING_TEXT_BUDGET_TOKENS = 30000
SCORING_MULTIMODAL_BUDGET_TOKENS = 30000
SCORING_IMAGE_TOKEN_OVERHEAD = 14000
REPORT_BUDGET_TOKENS = 30000
SCORING_MAX_EVIDENCE_ITEMS = 16
REPORT_MAX_EVIDENCE_ITEMS = 20
SCORING_MAX_CHARS_PER_EVIDENCE = 1200
REPORT_MAX_CHARS_PER_EVIDENCE = 1800
# 未知视觉 tokenizer 的图片预留，与 RequestBudget.estimate 一致。
_IMAGE_RESERVE_TOKENS = 16384
_SCORING_SYSTEM_PROMPT = "You are a scoring assistant. Output JSON only."

PromptBuilder = Callable[[list[str], list[str]], str]


@dataclass
class Candidate:
    evidence: str
    chunk_id: str
    page: int | None
    is_target_page: bool


@dataclass
class EvidenceSelection:
    """预算内选中的证据子集及其提示词。"""

    prompt: str
    estimated_tokens: int
    evidence: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)


@dataclass
class ScoringRequest:
    messages: list[dict]
    mode: str
    selection: EvidenceSelection
    target_pages: list[int]
    degraded_from: str = "none"


def find_source_pdf(task_id: str) -> str:
    """按 task_id 前缀（去掉末尾时间戳）在 input 目录下查找原始 PDF，找不到时返回空字符串。"""
    input_dir = Path(get_settings().app.dir_input)
    parts = task_id.split("-")
    pdf_stem = "-".join(parts[:-1]) if parts and parts[-1].isdigit() and len(parts[-1]) >= 8 else task_id
    pdf_filename = f"{pdf_stem}.pdf"
    for pdf_file in input_dir.rglob(pdf_filename):
        logger.info("找到源 PDF: %s", pdf_file)
        return str(pdf_file)
    logger.warning("未找到源 PDF: %s (搜索目录: %s)", pdf_filename, input_dir)
    return ""


def _normalize_key(text: str) -> str:
    """对证据做轻量规范化，用于去重近似重复内容。"""
    return " ".join(strip_surrogates(text).lower().split())[:400]


def _trim(text: str, max_chars: int) -> str:
    """裁剪单条证据长度，避免少数超长引文独占预算。"""
    clean_text = strip_surrogates(text)
    if len(clean_text) <= max_chars:
        return clean_text
    truncated = clean_text[:max_chars].rsplit(" ", 1)[0].strip() or clean_text[:max_chars].strip()
    return f"{truncated}\n... [证据已截断]"


class Item17EvidencePacker:
    """把累积证据压缩到预算内；失败时抛出 ContextBudgetExceeded，不静默丢弃全部证据。"""

    def __init__(self, *, budget: RequestBudget, evidence: list[str], chunk_ids: list[str],
                 chunk_lookup: dict[str, SemanticChunk]):
        self.budget = budget
        self.evidence = evidence
        self.chunk_ids = chunk_ids
        self.chunk_lookup = chunk_lookup

    def target_pages(self) -> list[int]:
        """证据最集中的两页，评分阶段优先保留并用于生成长图。"""
        counter: Counter[int] = Counter()
        for chunk_id in self.chunk_ids:
            chunk = self.chunk_lookup.get(chunk_id)
            if chunk and chunk.start_page is not None:
                counter[chunk.start_page] += 1
        return sorted(page for page, _count in counter.most_common(2))

    def candidates(self, max_chars: int, *, prefer_unique_chunks: bool) -> list[Candidate]:
        """去重、裁剪，并按目标页、最近性（以及可选的 chunk 多样性）排序。"""
        target_pages = self.target_pages()
        seen: set[str] = set()
        collected: list[Candidate] = []
        for evidence, chunk_id in reversed(list(zip(self.evidence, self.chunk_ids))):
            key = _normalize_key(evidence)
            if not key or key in seen:
                continue
            seen.add(key)
            chunk = self.chunk_lookup.get(chunk_id)
            page = chunk.start_page if chunk else None
            collected.append(Candidate(_trim(evidence, max_chars), strip_surrogates(chunk_id), page, page in target_pages))

        partitions = [[c for c in collected if c.is_target_page], [c for c in collected if not c.is_target_page]]
        if not prefer_unique_chunks:
            return partitions[0] + partitions[1]
        ordered: list[Candidate] = []
        seen_chunks: set[str] = set()
        for partition in partitions:
            firsts = []
            for candidate in partition:
                if candidate.chunk_id not in seen_chunks:
                    firsts.append(candidate)
                    seen_chunks.add(candidate.chunk_id)
            ordered.extend(firsts + [c for c in partition if c not in firsts])
        return ordered

    def _input_budget(self, token_budget: int, output_tokens: int, image_reserve: int) -> int:
        if not self.budget.context_window:
            return token_budget
        window = self.budget.context_window
        return min(token_budget, window - output_tokens - max(256, math.ceil(window * 0.03)) - image_reserve - 128)

    def _estimate(self, prompt: str) -> int:
        return self.budget.estimate([{"role": "user", "content": strip_surrogates(prompt)}])

    def fit(self, candidates: list[Candidate], build_prompt: PromptBuilder, *, label: str, token_budget: int,
            max_items: int, output_tokens: int, image_reserve: int = 0) -> EvidenceSelection:
        """先每个 chunk 取一条，再补充同 chunk 的其他证据，直到条数或预算用尽。"""
        token_budget = self._input_budget(token_budget, output_tokens, image_reserve)
        selection = EvidenceSelection(prompt="", estimated_tokens=0)
        selected: set[tuple[str, str]] = set()
        for unique_only in (True, False):
            for candidate in candidates:
                signature = (candidate.chunk_id, candidate.evidence)
                if len(selection.evidence) >= max_items:
                    break
                if signature in selected or (unique_only and candidate.chunk_id in selection.chunk_ids):
                    continue
                trial_evidence = selection.evidence + [candidate.evidence]
                trial_ids = selection.chunk_ids + [candidate.chunk_id]
                if self._estimate(build_prompt(trial_evidence, trial_ids)) <= token_budget:
                    selection.evidence, selection.chunk_ids = trial_evidence, trial_ids
                    if candidate.page is not None:
                        selection.pages.append(candidate.page)
                    selected.add(signature)

        selection.prompt = build_prompt(selection.evidence, selection.chunk_ids)
        selection.estimated_tokens = self._estimate(selection.prompt)
        if candidates and not selection.evidence:
            raise ContextBudgetExceeded(
                f"{label}无法容纳完整证据：基础提示约 {self._estimate(build_prompt([], []))} tokens，输入预算 {token_budget}。")
        if selection.estimated_tokens > token_budget:
            raise ContextBudgetExceeded(f"{label}超长：估算输入约 {selection.estimated_tokens} tokens，超过预算 {token_budget}。")
        return selection

    def scoring_request(self, build_prompt: PromptBuilder, *, vision_prompt: str, output_tokens: int,
                        is_vision_model: bool, task_id: str) -> ScoringRequest:
        """优先多模态评分；超预算或无法生成长图时降级为纯文本。"""
        candidates = self.candidates(SCORING_MAX_CHARS_PER_EVIDENCE, prefer_unique_chunks=False)
        target_pages = self.target_pages()
        degraded_from = "none"
        if is_vision_model and target_pages:
            try:
                selection = self.fit(
                    candidates, lambda evidence, ids: strip_surrogates(build_prompt(evidence, ids) + "\n\n" + vision_prompt),
                    label="多模态评分", token_budget=SCORING_MULTIMODAL_BUDGET_TOKENS - SCORING_IMAGE_TOKEN_OVERHEAD,
                    max_items=SCORING_MAX_EVIDENCE_ITEMS, output_tokens=output_tokens, image_reserve=_IMAGE_RESERVE_TOKENS,
                )
            except ContextBudgetExceeded as error:
                degraded_from = "multimodal(over_budget)"
                logger.warning("%s", error)
            else:
                image = get_stitched_pdf_pages_as_base64(find_source_pdf(task_id), target_pages)
                if image:
                    selection.estimated_tokens += SCORING_IMAGE_TOKEN_OVERHEAD
                    request = ScoringRequest(
                        messages=[
                            {"role": "system", "content": _SCORING_SYSTEM_PROMPT},
                            {"role": "user", "content": [
                                {"type": "text", "text": selection.prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
                            ]},
                        ],
                        mode="multimodal", selection=selection, target_pages=target_pages,
                    )
                    self._log("评分", request, len(candidates))
                    return request
                degraded_from = "multimodal(no_image)"
                logger.warning("item_17 多模态评分图片生成失败，降级为纯文本评分。")

        selection = self.fit(candidates, build_prompt, label="文本评分", token_budget=SCORING_TEXT_BUDGET_TOKENS,
                             max_items=SCORING_MAX_EVIDENCE_ITEMS, output_tokens=output_tokens)
        request = ScoringRequest(
            messages=[{"role": "system", "content": _SCORING_SYSTEM_PROMPT}, {"role": "user", "content": selection.prompt}],
            mode="text", selection=selection, target_pages=target_pages, degraded_from=degraded_from,
        )
        self._log("评分", request, len(candidates))
        return request

    def report_selection(self, build_prompt: PromptBuilder, *, output_tokens: int) -> EvidenceSelection:
        """最终报告只用文本证据，并优先覆盖更多 chunk。"""
        candidates = self.candidates(REPORT_MAX_CHARS_PER_EVIDENCE, prefer_unique_chunks=True)
        selection = self.fit(candidates, build_prompt, label="最终报告", token_budget=REPORT_BUDGET_TOKENS,
                             max_items=REPORT_MAX_EVIDENCE_ITEMS, output_tokens=output_tokens)
        self._log("最终报告", ScoringRequest([], "text", selection, self.target_pages()), len(candidates))
        return selection

    def _log(self, stage: str, request: ScoringRequest, deduped_count: int) -> None:
        selection = request.selection
        logger.info(
            "[item_17][%s] 预算详情: 原始证据=%s, 去重后=%s, 发送证据=%s, 估算tokens=%s, mode=%s, 目标页=%s, 已选页=%s, 降级路径=%s",
            stage, len(self.evidence), deduped_count, len(selection.evidence), selection.estimated_tokens,
            request.mode, request.target_pages, selection.pages, request.degraded_from,
        )
