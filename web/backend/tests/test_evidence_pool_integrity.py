"""证据池保存与复用的失败边界。"""

import json

import pytest

from core.preprocessor.evidence import EvidencePool
from core.preprocessor.pojo import SemanticChunk, SemanticLabel
from core.utils import atomic_json


def _pool(content: str) -> EvidencePool:
    return EvidencePool([
        SemanticChunk(
            chunk_id="chunk_001", title="Methods", content=content,
            label=SemanticLabel.METHOD, start_page=0, end_page=0,
        )
    ])


def test_failed_atomic_replacement_preserves_previous_pool(tmp_path, monkeypatch):
    path = tmp_path / "guide_evidence_pool.json"
    _pool("first result").save(str(path))
    original = path.read_bytes()

    def fail_replace(source, target):
        raise OSError("disk error")

    monkeypatch.setattr(atomic_json.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk error"):
        _pool("new result").save(str(path))
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_pool_loader_rejects_corrupt_or_ambiguous_artifacts(tmp_path):
    path = tmp_path / "guide_evidence_pool.json"
    path.write_text('{"chunks":', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        EvidencePool.load_file(path)

    _pool("valid").save(str(path), source_digest="source-a")
    assert EvidencePool.load_file(path, expected_source_digest="source-a").get_evidence_for_domain(3).endswith("valid")
    with pytest.raises(ValueError, match="不一致"):
        EvidencePool.load_file(path, expected_source_digest="source-b")
    _pool("other").save(str(tmp_path / "other_evidence_pool.json"))
    with pytest.raises(ValueError, match="不唯一"):
        EvidencePool.load(str(tmp_path))
