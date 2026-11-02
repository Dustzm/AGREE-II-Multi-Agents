"""Build natural evidence units for rubric-unit appraisal."""
from __future__ import annotations

import re

from core.rule_engine.models import EvidenceChunk, EvidenceUnit, ItemSpec


class EvidenceUnitBuilder:
    """Create high-recall evidence units from routed chunks."""

    _PROFILE_TERMS = {
        "item18_application_factors": (
            "implementation",
            "application",
            "barrier",
            "facilitator",
            "feasibility",
            "uptake",
            "adoption",
            "resource",
            "training",
            "access",
            "capacity",
            "workflow",
            "障碍",
            "促进",
            "实施",
            "应用",
        ),
        "item19_implementation_support": (
            "implementation",
            "tool",
            "resource",
            "appendix",
            "checklist",
            "leaflet",
            "template",
            "questionnaire",
            "manual",
            "audit",
            "flowchart",
            "patient material",
            "put into practice",
            "实施",
            "工具",
            "资源",
            "附录",
        ),
        "item21_monitoring_criteria": (
            "audit",
            "monitoring",
            "indicator",
            "standard",
            "measure",
            "frequency",
            "interval",
            "target",
            "threshold",
            "quality",
            "performance",
            "proportion",
            "HbA1c",
            "blood pressure",
            "审计",
            "监测",
            "指标",
            "标准",
        ),
    }

    def build(self, spec: ItemSpec, chunks: list[EvidenceChunk]) -> list[EvidenceUnit]:
        """Return ranked candidate units for a rubric-unit item."""
        rubric = spec.rubric_unit
        if rubric is None:
            return []

        units: list[EvidenceUnit] = []
        for chunk_order, chunk in enumerate(chunks):
            for local_index, (unit_type, text) in enumerate(self._split_chunk(chunk, rubric.unit_char_limit)):
                quote = text.strip()
                if not quote:
                    continue
                score = self._score_unit(rubric.profile, chunk.title, quote, unit_type, chunk_order, local_index)
                units.append(
                    EvidenceUnit(
                        unit_id=f"{spec.item_id}:{rubric.profile}:{len(units)}",
                        chunk_id=chunk.chunk_id,
                        title=chunk.title,
                        quote=quote[: rubric.unit_char_limit],
                        content=quote[: rubric.unit_char_limit],
                        unit_type=unit_type,
                        chunk_order=chunk_order,
                        metadata=dict(chunk.metadata),
                        score=score,
                    )
                )

        ranked = sorted(
            units,
            key=lambda unit: (-unit.score, unit.chunk_order, unit.unit_id),
        )
        return ranked[: max(1, rubric.max_units)]

    def _split_chunk(self, chunk: EvidenceChunk, limit: int) -> list[tuple[str, str]]:
        content = (chunk.content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not content:
            return []

        blocks = [block.strip() for block in re.split(r"\n\s*\n+", content) if block.strip()]
        if len(blocks) <= 1:
            blocks = self._split_lines(content)

        units: list[tuple[str, str]] = []
        for block in blocks:
            unit_type = self._unit_type(block)
            for part in self._split_long_block(block, limit):
                units.append((unit_type, part))

        if not units:
            units.append(("chunk", content[:limit]))
        return units

    def _split_lines(self, content: str) -> list[str]:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) <= 1:
            return [content]

        units: list[str] = []
        buffer: list[str] = []
        for line in lines:
            if self._is_list_or_table_line(line):
                if buffer:
                    units.append(" ".join(buffer))
                    buffer = []
                units.append(line)
            else:
                buffer.append(line)
        if buffer:
            units.append(" ".join(buffer))
        return units or [content]

    def _split_long_block(self, block: str, limit: int) -> list[str]:
        if len(block) <= limit:
            return [block]

        sentences = re.split(r"(?<=[.!?。！？])\s+", block)
        parts: list[str] = []
        current = ""
        for sentence in sentences:
            if not sentence:
                continue
            if len(current) + len(sentence) + 1 <= limit:
                current = f"{current} {sentence}".strip()
                continue
            if current:
                parts.append(current)
            current = sentence[:limit]
        if current:
            parts.append(current)
        return parts or [block[:limit]]

    def _unit_type(self, text: str) -> str:
        if self._is_list_or_table_line(text):
            if "|" in text or "\t" in text:
                return "table_row"
            return "bullet"
        if len(text.splitlines()) > 1:
            return "local_subsection"
        return "paragraph"

    def _is_list_or_table_line(self, text: str) -> bool:
        stripped = text.strip()
        return bool(
            re.match(r"^([-*•]|\d+[\).]|[A-Za-z][\).])\s+", stripped)
            or "|" in stripped
            or "\t" in stripped
        )

    def _score_unit(
        self,
        profile: str,
        title: str,
        text: str,
        unit_type: str,
        chunk_order: int,
        local_index: int,
    ) -> float:
        searchable = f"{title}\n{text}".casefold()
        terms = self._PROFILE_TERMS.get(profile, ())
        score = 0.0
        for term in terms:
            if term.casefold() in searchable:
                score += 1.0
        if title and any(term.casefold() in title.casefold() for term in terms):
            score += 2.0
        if unit_type in {"table_row", "bullet"}:
            score += 0.4
        score -= chunk_order * 0.01
        score -= local_index * 0.001
        return score
