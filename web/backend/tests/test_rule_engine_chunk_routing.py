from __future__ import annotations

import pytest

from core.preprocessor.pojo import SemanticChunk, SemanticLabel
from core.rule_engine.models import ChunkRoutingSpec, ItemSpec, RuleObjectSpec
from core.rule_engine.registry import get_item_spec
from core.rule_engine.routing import WeightedChunkRouter


def _chunk(
    chunk_id: str,
    title: str,
    content: str,
    label: SemanticLabel = SemanticLabel.METHOD,
) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        title=title,
        content=content,
        label=label,
        start_page=1,
        end_page=1,
    )


def test_router_without_config_returns_domain_chunks_only():
    spec = get_item_spec("item_1")
    chunks = [
        _chunk("chunk_001", "Intro", "irrelevant"),
        _chunk("chunk_002", "Scope", "diabetes guideline"),
    ]

    result = WeightedChunkRouter().route(
        spec=spec,
        all_chunks=chunks,
        domain_chunks=[chunks[1]],
    )

    assert [chunk.chunk_id for chunk in result.chunks] == ["chunk_002"]
    assert result.input_chunks == 2
    assert result.domain_chunks == 1
    assert result.selected_chunks == 1
    assert result.routed_chunks[0].reasons == ("domain_match",)


def test_router_scores_heading_phrase_term_and_neighbor_then_truncates():
    spec = ItemSpec(
        item_id="item_route",
        title="Route",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(
            heading_terms=("methods",),
            include_phrases=("study selection",),
            include_terms=("inclusion",),
            neighbor_window=1,
            max_chunks=2,
        ),
    )
    chunks = [
        _chunk("chunk_001", "Intro", "background"),
        _chunk("chunk_002", "Methods", "Study selection used inclusion criteria."),
        _chunk("chunk_003", "Next", "neighbor context"),
        _chunk("chunk_004", "Later", "background"),
    ]

    result = WeightedChunkRouter().route(
        spec=spec,
        all_chunks=chunks,
        domain_chunks=[],
    )

    assert [chunk.chunk_id for chunk in result.chunks] == ["chunk_002", "chunk_001"]
    assert result.routed_chunks[0].score > result.routed_chunks[1].score
    assert any(reason.startswith("heading:") for reason in result.routed_chunks[0].reasons)
    assert any(reason.startswith("phrase:") for reason in result.routed_chunks[0].reasons)
    assert any(reason.startswith("term:") for reason in result.routed_chunks[0].reasons)
    assert result.review_reasons[0].code == "chunk_routing_limit_exceeded"


def test_router_negative_terms_reduce_score_without_hard_delete():
    spec = ItemSpec(
        item_id="item_route_negative",
        title="Route negative",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(
            include_terms=("funding",),
            negative_terms=("trial funded",),
            max_chunks=3,
        ),
    )
    chunks = [
        _chunk("chunk_001", "Funding", "The guideline funding is disclosed."),
        _chunk("chunk_002", "Funding", "A cited trial funded by industry was discussed."),
    ]

    result = WeightedChunkRouter().route(
        spec=spec,
        all_chunks=chunks,
        domain_chunks=[chunks[1]],
    )

    selected_ids = [chunk.chunk_id for chunk in result.chunks]
    assert "chunk_002" in selected_ids
    debug_by_id = {info.chunk_id: info for info in result.routed_chunks}
    assert any(reason.startswith("negative:") for reason in debug_by_id["chunk_002"].reasons)
    assert debug_by_id["chunk_002"].score < debug_by_id["chunk_001"].score


def test_router_max_domain_chunks_limits_domain_noise():
    spec = ItemSpec(
        item_id="item_route_domain_limit",
        title="Route domain limit",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(
            include_terms=("target",),
            max_chunks=3,
            max_domain_chunks=1,
        ),
    )
    chunks = [
        _chunk("chunk_001", "Domain", "domain only"),
        _chunk("chunk_002", "Domain", "domain only"),
        _chunk("chunk_003", "Target", "target evidence"),
    ]

    result = WeightedChunkRouter().route(
        spec=spec,
        all_chunks=chunks,
        domain_chunks=[chunks[0], chunks[1]],
    )

    selected_ids = [chunk.chunk_id for chunk in result.chunks]
    assert "chunk_003" in selected_ids
    assert len({"chunk_001", "chunk_002"} & set(selected_ids)) == 1


def test_router_domain_limit_does_not_prioritize_lower_score_domain_chunks():
    spec = ItemSpec(
        item_id="item_route_domain_rank",
        title="Route domain rank",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(
            heading_terms=("target",),
            include_terms=("target",),
            max_chunks=1,
            max_domain_chunks=1,
        ),
    )
    chunks = [
        _chunk("chunk_001", "Domain", "domain only"),
        _chunk("chunk_002", "Target", "target evidence"),
    ]

    result = WeightedChunkRouter().route(
        spec=spec,
        all_chunks=chunks,
        domain_chunks=[chunks[0]],
    )

    assert [chunk.chunk_id for chunk in result.chunks] == ["chunk_002"]


def test_router_keeps_appendix_but_excludes_references_and_revision_sections():
    spec = ItemSpec(
        item_id="item_route",
        title="Route",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(heading_terms=("methods",), max_chunks=5),
    )
    chunks = [
        _chunk("chunk_001", "Methods", "methods text"),
        _chunk("chunk_002", "附录 A 证据表", "appendix table"),
        _chunk("chunk_003", "修订历史", "revision history"),
        _chunk("chunk_004", "References", "bibliography"),
    ]

    result = WeightedChunkRouter().route(spec=spec, all_chunks=chunks, domain_chunks=chunks)

    # Appendix content is kept (domain label mapping decides domains 2/3/5/6);
    # reference and revision-history titles are still hard-excluded.
    assert [chunk.chunk_id for chunk in result.chunks] == ["chunk_001", "chunk_002"]


def test_router_accepts_item_level_excluded_labels():
    def chunk(chunk_id: str, title: str, content: str, label: SemanticLabel) -> SemanticChunk:
        return SemanticChunk(
            chunk_id=chunk_id, title=title, content=content, label=label, start_page=1, end_page=1,
        )

    spec = ItemSpec(
        item_id="item_route",
        title="Route",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(heading_terms=("methods",), max_chunks=5, excluded_labels=("Others",)),
    )
    chunks = [
        chunk("chunk_001", "Methods", "methods text", SemanticLabel.METHOD),
        chunk("chunk_002", "Funding", "conflict of interest", SemanticLabel.OTHERS),
    ]

    result = WeightedChunkRouter().route(spec=spec, all_chunks=chunks, domain_chunks=chunks)

    assert [chunk.chunk_id for chunk in result.chunks] == ["chunk_001"]


def test_appendix_chunks_rank_behind_body_chunks(monkeypatch):
    monkeypatch.setattr("core.rule_engine.routing.weighted._appendix_score_factor", lambda: 0.25)
    monkeypatch.setattr("core.rule_engine.routing.weighted._appendix_chunk_cap", lambda _n: 3)
    spec = ItemSpec(
        item_id="item_route",
        title="Route",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(heading_terms=("methods",), max_chunks=3),
    )
    chunks = [
        _chunk("chunk_001", "Methods", "methods text"),
        _chunk("chunk_002", "Methods appendix", "methods text duplicate", SemanticLabel.APPENDIX),
    ]

    result = WeightedChunkRouter().route(spec=spec, all_chunks=chunks, domain_chunks=chunks)

    assert [chunk.chunk_id for chunk in result.chunks] == ["chunk_001", "chunk_002"]


def test_appendix_chunks_are_capped_per_item(monkeypatch):
    monkeypatch.setattr("core.rule_engine.routing.weighted._appendix_score_factor", lambda: 0.25)
    monkeypatch.setattr("core.rule_engine.routing.weighted._appendix_chunk_cap", lambda _n: 2)
    spec = ItemSpec(
        item_id="item_route",
        title="Route",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(heading_terms=("methods",), max_chunks=8),
    )
    chunks = [_chunk("chunk_001", "Methods", "methods text")]
    chunks += [
        _chunk(f"chunk_{index:03d}", f"Appendix methods {index}", "methods text", SemanticLabel.APPENDIX)
        for index in range(2, 7)
    ]

    result = WeightedChunkRouter().route(spec=spec, all_chunks=chunks, domain_chunks=chunks)

    appendix_ids = {chunk.chunk_id for chunk in chunks if chunk.label is SemanticLabel.APPENDIX}
    selected_appendix = [chunk.chunk_id for chunk in result.chunks if chunk.chunk_id in appendix_ids]
    assert len(selected_appendix) == 2
    assert "chunk_001" in [chunk.chunk_id for chunk in result.chunks]


@pytest.mark.parametrize(
    "title",
    ["References", "7. 参考文献", "Appendix 2: References", "[附件] Bibliography", "修订历史", "Index"],
)
def test_non_evidence_titles_are_recognised(title):
    from core.preprocessor.section_titles import is_non_evidence_title

    assert is_non_evidence_title(title)


@pytest.mark.parametrize(
    "title",
    [
        "Patient values and preferences",
        "患者偏好与价值观 (Preferences)",
        "Body Mass Index targets",
        "Index test accuracy",
        "Supporting information: search strategy",
        "附录 A 证据表",
    ],
)
def test_content_titles_mentioning_reference_words_are_kept(title):
    spec = ItemSpec(
        item_id="item_route",
        title="Route",
        rule_profile="ordered_milestone",
        object_specs={"A": RuleObjectSpec(type="A", role="component", description="A")},
        chunk_routing=ChunkRoutingSpec(heading_terms=("methods",), max_chunks=5),
    )
    chunks = [_chunk("chunk_001", "Methods", "methods text"), _chunk("chunk_002", title, "methods body")]

    result = WeightedChunkRouter().route(spec=spec, all_chunks=chunks, domain_chunks=chunks)

    assert "chunk_002" in [chunk.chunk_id for chunk in result.chunks]
