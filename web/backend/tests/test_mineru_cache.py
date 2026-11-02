"""
MinerU 缓存复用测试。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.attachment.parser import PDFAttachmentParser
from core.parsers import mineru_parser
from core.parsers import mineru_cache


def _write_json(path, payload) -> None:
    """写入 JSON 测试文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_restore_document_cache_excludes_evidence_pool(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """恢复主文档缓存时只复制 MinerU 产物，不复制 evidence pool。"""
    guideline_id = "1-2020-001C"
    cache_root = tmp_path / "doc_data"
    cache_dir = cache_root / guideline_id
    cache_prep = cache_dir / "prep"

    monkeypatch.setattr(mineru_cache, "DOC_DATA_ROOT", cache_root)

    (cache_dir / "source.md").parent.mkdir(parents=True, exist_ok=True)
    (cache_dir / "source.md").write_text("# cached source", encoding="utf-8")
    _write_json(cache_prep / f"{guideline_id}_content_list.json", [])
    _write_json(cache_prep / f"{guideline_id}_middle.json", {"pdf_info": [{}, {}]})
    _write_json(cache_prep / f"{guideline_id}_model.json", {"model": "ok"})
    _write_json(cache_prep / f"{guideline_id}_docindex.json", {"title_list": []})
    _write_json(cache_prep / f"{guideline_id}_evidence_pool.json", {"chunks": []})
    _write_json(cache_prep / f"{guideline_id}_visual_evidence.json", {"items": []})
    (cache_prep / "images").mkdir(parents=True)
    (cache_prep / "images" / "page.jpg").write_bytes(b"image")
    _write_json(
        cache_prep / "attachment" / "1" / f"{guideline_id} 附件_content_list.json",
        [{"type": "text", "text": "attachment text"}],
    )

    target_source = tmp_path / "task" / "source.md"
    target_prep = tmp_path / "task" / ".ama" / "prep"

    hit = mineru_cache.restore_document_cache(guideline_id, target_source, target_prep)

    assert hit is not None
    assert hit.pages == 2
    assert target_source.read_text(encoding="utf-8") == "# cached source"
    assert (target_prep / f"{guideline_id}_content_list.json").exists()
    assert (target_prep / f"{guideline_id}_middle.json").exists()
    assert (target_prep / f"{guideline_id}_model.json").exists()
    assert (target_prep / f"{guideline_id}_docindex.json").exists()
    assert (target_prep / "images" / "page.jpg").exists()
    assert not (target_prep / f"{guideline_id}_evidence_pool.json").exists()
    assert not (target_prep / f"{guideline_id}_visual_evidence.json").exists()


def test_pdf_attachment_parser_reuses_cached_mineru_outputs(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """PDF 附件缓存命中时应直接读取 content_list，不再调用 MinerU。"""
    attachment = tmp_path / "1-2020-001C 附件.pdf"
    attachment.write_bytes(b"%PDF-1.4")
    prep_dir = tmp_path / "task" / "attachment" / "1"
    cache_dir = tmp_path / "doc_data" / "1-2020-001C" / "prep" / "attachment" / "1"
    stem = attachment.stem

    monkeypatch.setattr(mineru_cache, "DOC_DATA_ROOT", tmp_path / "doc_data")

    (cache_dir / f"{stem}.md").parent.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{stem}.md").write_text("# attachment", encoding="utf-8")
    _write_json(
        cache_dir / f"{stem}_content_list.json",
        [{"type": "text", "text": "cached attachment text", "page_idx": 0}],
    )

    async def fail_parse_pdf_to_file(*_args, **_kwargs):
        raise AssertionError("MinerU should not be called on cache hit")

    monkeypatch.setattr(
        mineru_parser.MinerUParser,
        "parse_pdf_to_file",
        fail_parse_pdf_to_file,
    )

    parser = PDFAttachmentParser()
    contents = asyncio.run(parser.parse(attachment, prep_dir=prep_dir))

    assert len(contents) == 1
    assert contents[0].title == f"[附件] {stem}"
    assert contents[0].content == "cached attachment text"
