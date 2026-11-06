"""引用定位：把条目理由里逐字摘录的原句（`step2_evidence`）定位到原文中的具体块。

背景：`evidence_sources` 是喂给模型阅读的全部片段（可达数百个），不是引用；原文定位索引
（`*_visual_evidence.json`）的区域只保留前 100 字，无法按原句匹配。这里改用解析结果
（`*_model.json`）中每个块的完整文本与归一化坐标，逐句定位：

1. 完全包含：规范化后（小写、去标签与标点、合并空白）原句是某个块文本的子串；
2. 跨块：原句跨越阅读顺序上相邻的 2–3 个块（段落、分页、列表项）；
3. 部分重合：原句与某块的最长公共片段 ≥ 原句的 60%（至少 24 字，至多要求 60 字），容许轻微改写；
4. 候选排序：落在模型读过的片段内优先，其次匹配方式与重合度，再其次块越短越贴合；只取最优一处。

短原句（规范化后 8–23 字，如作者姓名、小标题）只在模型读过的片段内找完全包含的块，否则在全文只出现一处时才定位；
更短的不定位，只展示文字。
定位失败不影响条目保存：调用方按“无定位”处理。
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypedDict

logger = logging.getLogger(__name__)

MatchKind = Literal["exact", "span", "partial", "none"]

# 页眉页脚与页码不作为引用位置
_SKIP_TYPES = frozenset({"header", "footer", "page_number"})
_MIN_QUOTE_CHARS = 24
_SHORT_QUOTE_CHARS = 8
_PARTIAL_MIN_CHARS = 60
_PARTIAL_RATIO = 0.6
# 字符 n-gram 倒排索引：块按步长取样，原句逐位取，任何 ≥ GRAM + STRIDE - 1 字的公共片段都能命中
_GRAM = 12
_STRIDE = 4
_SPAN_BLOCKS = 3
_PARTIAL_CANDIDATES = 24

_TAG = re.compile(r"<[^>]+>")
_NON_WORD = re.compile(r"[^\w]+")


class CitationRegion(TypedDict):
    page_idx: int
    bbox: list[float]
    block_id: str | None
    chunk_id: str | None


class Citation(TypedDict):
    text: str
    match: MatchKind
    regions: list[CitationRegion]


def normalize(text: str) -> str:
    """比较用文本：NFKC、去 HTML 标签与标点、小写、合并空白（适用于中英文）。"""
    text = unicodedata.normalize("NFKC", _TAG.sub(" ", text))
    return " ".join(_NON_WORD.sub(" ", text.lower()).split())


@dataclass(frozen=True)
class Block:
    order: int
    page_idx: int
    bbox: tuple[float, float, float, float]
    block_id: str | None
    chunk_id: str | None
    text: str

    def region(self) -> CitationRegion:
        return {
            "page_idx": self.page_idx,
            "bbox": list(self.bbox),
            "block_id": self.block_id,
            "chunk_id": self.chunk_id,
        }


@dataclass(frozen=True)
class Document:
    """一篇文献的可定位块（阅读顺序）与 n-gram 索引。"""

    blocks: tuple[Block, ...]
    index: dict[str, tuple[int, ...]]

    @classmethod
    def build(cls, blocks: Sequence[Block]) -> Document:
        grams: defaultdict[str, list[int]] = defaultdict(list)
        for block in blocks:
            text = block.text
            for start in range(0, max(1, len(text) - _GRAM + 1), _STRIDE):
                gram = text[start : start + _GRAM]
                if len(gram) == _GRAM and (not grams[gram] or grams[gram][-1] != block.order):
                    grams[gram].append(block.order)
        return cls(tuple(blocks), {gram: tuple(orders) for gram, orders in grams.items()})

    def candidates(self, quote: str) -> list[int]:
        """与原句共享 n-gram 的块，按命中数从多到少。"""
        hits: Counter[int] = Counter()
        for start in range(max(1, len(quote) - _GRAM + 1)):
            for order in self.index.get(quote[start : start + _GRAM], ()):
                hits[order] += 1
        return [order for order, _ in hits.most_common()]


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, list | tuple) and len(value) == 4 and all(isinstance(v, int | float) for v in value):
        x1, y1, x2, y2 = (float(v) for v in value)
        return (x1, y1, x2, y2)
    return None


def blocks_from(model: Any, chunks: Iterable[Any]) -> list[Block]:
    """`*_model.json`（按页的块列表）与证据池片段（含 block_ids）→ 可定位块。"""
    chunk_of: dict[str, str] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict) or not isinstance(chunk.get("id"), str):
            continue
        for block_id in chunk.get("block_ids") or []:
            if isinstance(block_id, str):
                chunk_of.setdefault(block_id, chunk["id"])
    blocks: list[Block] = []
    for page_idx, page in enumerate(model if isinstance(model, list) else []):
        for raw in page if isinstance(page, list) else []:
            if not isinstance(raw, dict) or raw.get("type") in _SKIP_TYPES:
                continue
            content, bbox = raw.get("content"), _bbox(raw.get("bbox"))
            text = normalize(content) if isinstance(content, str) else ""
            if not text or bbox is None:
                continue
            block_id = raw.get("block_id") if isinstance(raw.get("block_id"), str) else None
            chunk_id = chunk_of.get(block_id) if block_id else None
            blocks.append(Block(len(blocks), page_idx, bbox, block_id, chunk_id, text))
    return blocks


@lru_cache(maxsize=4)
def _load(model_path: str, pool_path: str, _stamp: tuple[float, float]) -> Document:
    model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    pool = json.loads(Path(pool_path).read_text(encoding="utf-8")) if Path(pool_path).is_file() else {}
    chunks = pool.get("chunks", []) if isinstance(pool, dict) else []
    return Document.build(blocks_from(model, chunks))


def load_document(prep_dir: str | os.PathLike[str]) -> Document | None:
    """任务预处理目录中的主文献（`*_model.json` + `*_evidence_pool.json`）；没有解析结果时为 None。"""
    prep = Path(prep_dir)
    models = sorted(prep.glob("*_model.json"))
    if not models:
        return None
    model = models[0]
    pool = model.with_name(model.name.removesuffix("_model.json") + "_evidence_pool.json")
    stamp = (model.stat().st_mtime, pool.stat().st_mtime if pool.is_file() else 0.0)
    return _load(str(model), str(pool), stamp)


@dataclass(frozen=True)
class _Match:
    orders: tuple[int, ...]
    kind: MatchKind
    coverage: float


def _exact(document: Document, quote: str, candidates: Sequence[int]) -> list[_Match]:
    return [_Match((order,), "exact", 1.0) for order in candidates if quote in document.blocks[order].text]


def _span(document: Document, quote: str, candidates: Sequence[int]) -> list[_Match]:
    """原句跨越相邻块：以候选块为起点或终点，拼接阅读顺序上相邻的块后包含原句。"""
    blocks = document.blocks
    found: dict[tuple[int, ...], _Match] = {}
    for order in candidates[:_PARTIAL_CANDIDATES]:
        for first in range(max(0, order - _SPAN_BLOCKS + 1), order + 1):
            for size in range(2, _SPAN_BLOCKS + 1):
                orders = tuple(range(first, min(len(blocks), first + size)))
                if len(orders) < 2 or order not in orders:
                    continue
                if quote in " ".join(blocks[o].text for o in orders):
                    # 更短的窗口同样会被找到，排序时按总长度取最贴合的一个
                    found.setdefault(orders, _Match(orders, "span", 1.0))
    return list(found.values())


def _partial(document: Document, quote: str, candidates: Sequence[int]) -> list[_Match]:
    need = max(_MIN_QUOTE_CHARS, min(_PARTIAL_MIN_CHARS, int(len(quote) * _PARTIAL_RATIO)))
    matches = []
    for order in candidates[:_PARTIAL_CANDIDATES]:
        text = document.blocks[order].text
        size = SequenceMatcher(None, quote, text, autojunk=False).find_longest_match(0, len(quote), 0, len(text)).size
        if size >= need:
            matches.append(_Match((order,), "partial", size / len(quote)))
    return matches


def _best(document: Document, matches: Sequence[_Match], retrieved: frozenset[str]) -> _Match:
    def rank(match: _Match) -> tuple[int, int, float, int, int]:
        blocks = [document.blocks[o] for o in match.orders]
        inside = all(block.chunk_id in retrieved for block in blocks) if retrieved else False
        quality = 1 if match.kind in ("exact", "span") else 0
        length = sum(len(block.text) for block in blocks)
        return (-int(inside), -quality, -match.coverage, length, match.orders[0])

    return min(matches, key=rank)


def _short(document: Document, quote: str, retrieved: frozenset[str]) -> list[_Match]:
    """短原句容易在全文多处出现：先在读过的片段内找，否则只接受全文唯一的一处。"""
    padded = f" {quote} "
    found = [block.order for block in document.blocks if padded in f" {block.text} "]
    inside = [order for order in found if document.blocks[order].chunk_id in retrieved]
    if inside:
        return [_Match((order,), "exact", 1.0) for order in inside]
    return [_Match((found[0],), "exact", 1.0)] if len(found) == 1 else []


def locate_quote(document: Document, quote: str, retrieved: Iterable[str] = ()) -> Citation:
    text = quote.strip()
    normalized = normalize(text)
    none: Citation = {"text": text, "match": "none", "regions": []}
    sources = frozenset(retrieved)
    if len(normalized) < _MIN_QUOTE_CHARS:
        short = _short(document, normalized, sources) if len(normalized) >= _SHORT_QUOTE_CHARS else []
        if not short:
            return none
        best = _best(document, short, sources)
        return {"text": text, "match": best.kind, "regions": [document.blocks[o].region() for o in best.orders]}
    candidates = document.candidates(normalized)
    if not candidates:
        return none
    matches = _exact(document, normalized, candidates) or _span(document, normalized, candidates)
    if not matches:
        matches = _partial(document, normalized, candidates)
    if not matches:
        return none
    best = _best(document, matches, sources)
    return {"text": text, "match": best.kind, "regions": [document.blocks[o].region() for o in best.orders]}


def locate_quotes(document: Document, quotes: Iterable[Any], retrieved: Iterable[str] = ()) -> list[Citation]:
    """逐句定位；非字符串与空白原句跳过，顺序与原句一致。"""
    sources = frozenset(source for source in retrieved if isinstance(source, str))
    return [locate_quote(document, quote, sources) for quote in quotes if isinstance(quote, str) and quote.strip()]


def citations_for(
    prep_dir: str | os.PathLike[str], quotes: Iterable[Any], retrieved: Iterable[str]
) -> list[Citation] | None:
    """保存条目时调用：没有解析结果或定位出错时返回 None（不阻断保存）。"""
    try:
        document = load_document(prep_dir)
        if document is None:
            return None
        return locate_quotes(document, quotes, retrieved)
    except Exception:
        logger.warning("locating citations failed in %s", prep_dir, exc_info=True)
        return None
