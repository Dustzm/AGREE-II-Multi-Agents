"""引用定位（core/preprocessor/citations.py）与报告接口暴露 citations 的回归测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.preprocessor.citations import (
    citations_for,
    load_document,
    locate_quote,
    locate_quotes,
)
from web.backend.app.api.routes import reports
from web.backend.tests.test_api import client  # noqa: F401 - isolated authenticated app fixture

EXACT = "The guideline development group included clinicians, nurses, pharmacists and patient representatives."
PAGE_END = "All recommendations were reviewed by two external experts"
PAGE_START = "who were not involved in the development of this guideline."
AUTHOR_LINE = "Jane Smith"
CHINESE = "本指南的目标人群为确诊二型糖尿病的成年患者，不包括妊娠期糖尿病患者。"
PARTIAL_SOURCE = (
    "Systematic searches were performed in MEDLINE, Embase and the Cochrane Library from inception "
    "to March 2023 without language restrictions."
)


def _block(block_id: str, content: str, bbox: list[float], kind: str = "text") -> dict:
    return {"block_id": block_id, "type": kind, "content": content, "bbox": bbox}


def _write_prep(prep: Path) -> Path:
    """两页的解析结果：页尾段落跨到下一页，页眉页脚与页码不参与定位。"""
    pages = [
        [
            _block("b0", AUTHOR_LINE, [0.1, 0.05, 0.4, 0.08]),
            _block("b1", EXACT, [0.1, 0.10, 0.9, 0.20]),
            _block("b2", PARTIAL_SOURCE, [0.1, 0.25, 0.9, 0.35]),
            _block("b3", CHINESE, [0.1, 0.40, 0.9, 0.45]),
            _block("b4", PAGE_END, [0.1, 0.85, 0.9, 0.92]),
            _block("f0", "Page 1 of 2", [0.4, 0.95, 0.6, 0.98], kind="footer"),
        ],
        [
            _block("h1", "Example guideline 2024", [0.1, 0.01, 0.9, 0.03], kind="header"),
            _block("b5", PAGE_START, [0.1, 0.05, 0.9, 0.10]),
            _block("b6", f"Correspondence: {AUTHOR_LINE}, Department of Medicine.", [0.1, 0.80, 0.9, 0.85]),
        ],
    ]
    pool = {
        "chunks": [
            {"id": "c-front", "block_ids": ["b0", "b1", "b2", "b3"]},
            {"id": "c-review", "block_ids": ["b4", "b5"]},
            {"id": "c-contact", "block_ids": ["b6"]},
        ]
    }
    prep.mkdir(parents=True, exist_ok=True)
    (prep / "guide_model.json").write_text(json.dumps(pages), encoding="utf-8")
    (prep / "guide_evidence_pool.json").write_text(json.dumps(pool), encoding="utf-8")
    return prep


@pytest.fixture
def document(tmp_path):
    loaded = load_document(_write_prep(tmp_path / "prep"))
    assert loaded is not None
    return loaded


def test_exact_quote_is_located_in_its_block(document):
    citation = locate_quote(document, f"  {EXACT}  ", {"c-front"})
    assert citation == {
        "text": EXACT,
        "match": "exact",
        "regions": [{"page_idx": 0, "bbox": [0.1, 0.10, 0.9, 0.20], "block_id": "b1", "chunk_id": "c-front"}],
    }


def test_quote_across_a_page_break_spans_both_blocks(document):
    citation = locate_quote(document, f"{PAGE_END} {PAGE_START}", {"c-review"})
    assert citation["match"] == "span"
    # 页脚、页眉被跳过，原句落在第 1 页页尾与第 2 页页首两个块
    assert [(r["page_idx"], r["block_id"]) for r in citation["regions"]] == [(0, "b4"), (1, "b5")]


def test_lightly_reworded_quote_is_a_partial_match(document):
    quote = "Systematic searches were performed in MEDLINE, Embase and the Cochrane Library up to 2023."
    citation = locate_quote(document, quote)
    assert citation["match"] == "partial"
    assert [r["block_id"] for r in citation["regions"]] == ["b2"]


def test_short_quote_is_limited_to_retrieved_chunks(document):
    # 作者姓名在全文出现两处：只在模型读过的片段内定位
    inside = locate_quote(document, AUTHOR_LINE, {"c-contact"})
    assert inside["match"] == "exact"
    assert [r["block_id"] for r in inside["regions"]] == ["b6"]
    assert locate_quote(document, AUTHOR_LINE, {"c-front"})["regions"][0]["block_id"] == "b0"
    # 没读过任何一处且全文不唯一时不定位
    assert locate_quote(document, AUTHOR_LINE, {"c-review"}) == {"text": AUTHOR_LINE, "match": "none", "regions": []}
    # 过短的原句不定位
    assert locate_quote(document, "Jane", {"c-front"})["match"] == "none"


def test_chinese_quote_ignores_punctuation_width(document):
    quote = CHINESE.replace("，", ",").rstrip("。")
    citation = locate_quote(document, quote, {"c-front"})
    assert citation["match"] == "exact"
    assert citation["text"] == quote
    assert [(r["page_idx"], r["block_id"]) for r in citation["regions"]] == [(0, "b3")]


def test_unplaced_quotes_keep_text_without_regions(document):
    quote = "Cost-effectiveness analyses were commissioned from an independent health economics unit."
    assert locate_quote(document, quote) == {"text": quote, "match": "none", "regions": []}


def test_citations_for_keeps_quote_order_and_skips_blank_entries(tmp_path):
    prep = _write_prep(tmp_path / "prep")
    quotes = [EXACT, "", None, 42, "Not anywhere in the document at all, nothing to see here.", CHINESE]
    citations = citations_for(prep, quotes, ["c-front"])
    assert citations is not None
    assert [c["match"] for c in citations] == ["exact", "none", "exact"]
    assert [c["text"] for c in citations] == [EXACT, quotes[4], CHINESE]


def test_citations_for_without_parse_result_returns_none(tmp_path):
    assert citations_for(tmp_path / "missing", [EXACT], []) is None
    (tmp_path / "empty").mkdir()
    assert citations_for(tmp_path / "empty", [EXACT], []) is None
    assert locate_quotes(load_document(_write_prep(tmp_path / "prep")), []) == []


def test_report_route_exposes_validated_citations(client, tmp_path, monkeypatch):  # noqa: F811
    task = tmp_path / "report-cite"
    task.mkdir()
    (task / "appraisal_report.json").write_text(json.dumps({
        "task_id": "report-cite",
        "guideline_title": "Guideline",
        "domain_scores": {},
        "overall_assessment": {"strengths": [], "weaknesses": [], "recommendation": "Recommend"},
    }))
    region = {"page_idx": 2, "bbox": [0.1, 0.2, 0.8, 0.3], "block_id": "b1", "chunk_id": "c1"}
    cited = {
        "item_id": "item_1",
        "domain": 1,
        "chain_of_thought": {"score": 6, "step2_evidence": ["quoted"], "step3_reasoning": "reasoning"},
        "evidence_sources": ["c1", "c2", "c3"],
        "metadata": {},
        "citations": [
            {"text": " Located sentence. ", "match": "span", "regions": [region, {"page_idx": -1, "bbox": [0, 0, 1, 1]}]},
            {"text": "Broken regions only.", "match": "exact", "regions": [{"page_idx": "3", "bbox": [0, 0, 1, 1]}, {"page_idx": 1, "bbox": [0, 0, 1]}]},
            {"text": "Unknown match kind.", "match": "fuzzy", "regions": [region]},
            {"text": "Never placed.", "match": "none", "regions": []},
            {"text": "   ", "match": "exact", "regions": [region]},
            {"match": "exact", "regions": [region]},
            "not a citation",
        ],
    }
    legacy = {
        "item_id": "item_2",
        "domain": 1,
        "chain_of_thought": {"score": 5, "step2_evidence": ["old quote"], "step3_reasoning": "old"},
        "evidence_sources": ["c9"],
        "metadata": {},
    }
    monkeypatch.setattr(reports, "get_output_dir", lambda: tmp_path)
    monkeypatch.setattr(reports, "load_evaluations", lambda task_dir: [cited, legacy])
    client.post("/ama/api/auth/login", json={"username": "reviewer", "password": "reviewer123"})

    response = client.get("/ama/api/reports/report-cite")
    assert response.status_code == 200
    items = {item["item_id"]: item for item in response.json()["item_evaluations"]}
    assert items["item_1"]["citations"] == [
        {"text": "Located sentence.", "match": "span", "regions": [region]},
        {"text": "Broken regions only.", "match": "none", "regions": []},
        {"text": "Unknown match kind.", "match": "none", "regions": []},
        {"text": "Never placed.", "match": "none", "regions": []},
    ]
    assert items["item_1"]["step2_evidence"] == ["quoted"]
    assert items["item_1"]["evidence_sources"] == ["c1", "c2", "c3"]
    # 旧结果没有 citations：保持 None，前端沿用按 chunk 定位
    assert items["item_2"]["citations"] is None
    assert items["item_2"]["evidence_sources"] == ["c9"]
