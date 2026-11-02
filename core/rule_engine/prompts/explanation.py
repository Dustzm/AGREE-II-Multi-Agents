"""
Prompt template for rule-engine explanation.
"""
from __future__ import annotations

import json
import re

from core.rule_engine.explainers.models import RuleExplanationInput


SYSTEM_PROMPT = """You are an AGREE-II rule-engine explanation assistant.
Output JSON only.
Explain only from the provided rule result and evidence.
Do not expose internal fact names, object type names, rule profile names, snake_case terms, or code-like identifiers.
Do not invent evidence or chunk IDs.
The final score must be one of the allowed scores."""


def render_explanation_prompt(payload: RuleExplanationInput, lang: str = "zh") -> str:
    """Render the rule-engine explanation prompt."""
    language_instruction = _language_instruction(lang)
    return f"""# Rule Engine Explanation Task

{language_instruction}

## Input
```json
{json.dumps(_payload_to_prompt_data(payload), ensure_ascii=False, indent=2)}
```

## Requirements
1. Only explain the provided rule result and evidence.
2. Choose "score" only from "allowed_scores".
3. For score 1 with no mentioned evidence, reasons may use an empty evidence_chunks array.
4. For scores above 1, every reason must include evidence_chunks from the provided evidence.
5. Do not mention internal object names, fact keys, rule profiles, snake_case identifiers, or code-like variants.
6. If needs_review is true, mention the review need naturally without changing the allowed score range.
7. Use natural AGREE-II appraisal language; do not copy technical labels from the input.

## Output JSON
Return exactly this shape:
```json
{json.dumps(_output_example(payload), ensure_ascii=False, indent=2)}
```
"""


def render_explanation_retry_prompt(
    payload: RuleExplanationInput,
    *,
    previous_error: str,
    lang: str = "zh",
) -> str:
    """Render a compact retry prompt after an invalid explanation response."""
    retry_data = {
        "item_id": payload.item_id,
        "title": payload.title,
        "score_range": payload.score_range,
        "allowed_scores": payload.allowed_scores,
        "needs_review": payload.needs_review,
        "available_citations": sorted(_available_citations(payload)),
        "matched_criteria": [
            {
                "description": criterion.description,
                "evidence": [
                    {
                        "chunk_id": evidence.chunk_id,
                        "quote": evidence.quote,
                    }
                    for evidence in criterion.evidence
                ],
            }
            for criterion in payload.matched_criteria
        ],
        "missing_criteria": [
            {"description": criterion.description}
            for criterion in payload.missing_criteria
        ],
        "previous_error_summary": sanitize_retry_error(previous_error),
    }
    return f"""# Retry Rule Engine Explanation

{_language_instruction(lang)}

Return JSON only. Do not include markdown fences, analysis, comments, or extra keys.

## Input
{json.dumps(retry_data, ensure_ascii=False, indent=2)}

## Output Shape
{json.dumps(_output_example(payload), ensure_ascii=False, indent=2)}

## Requirements
1. The score must be one of allowed_scores.
2. Use only available_citations in evidence_chunks.
3. If allowed_scores is [1] and available_citations is empty, evidence_chunks may be [].
4. For scores above 1, each reason must cite at least one available citation.
5. Use natural AGREE-II appraisal language and never output snake_case or internal field names.
"""


def sanitize_retry_error(error: str) -> str:
    """Return a retry-safe error summary without internal identifiers."""
    text = str(error)
    text = re.sub(r"\[[^\]]*[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+[^\]]*\]", "[internal identifiers omitted]", text)
    text = re.sub(r"'[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+'", "'internal identifier omitted'", text)
    text = re.sub(r'"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+"', '"internal identifier omitted"', text)
    text = re.sub(
        r"(?<![A-Za-z0-9_])(?!(?:chunk)_\d+\b)[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+(?![A-Za-z0-9_])",
        "internal_identifier",
        text,
    )
    return text[:500]


def _payload_to_prompt_data(payload: RuleExplanationInput) -> dict:
    return {
        "item_id": payload.item_id,
        "title": payload.title,
        "scoring_principle": payload.scoring_principle,
        "score_range": payload.score_range,
        "allowed_scores": payload.allowed_scores,
        "needs_review": payload.needs_review,
        "matched_criteria": [
            {
                "description": criterion.description,
                "evidence": [
                    {
                        "chunk_id": evidence.chunk_id,
                        "title": evidence.title,
                        "quote": evidence.quote,
                    }
                    for evidence in criterion.evidence
                ],
            }
            for criterion in payload.matched_criteria
        ],
        "missing_criteria": [
            {
                "description": criterion.description,
            }
            for criterion in payload.missing_criteria
        ],
    }


def _output_example(payload: RuleExplanationInput) -> dict:
    evidence_chunks = ["chunk_001"]
    if payload.allowed_scores == [1] and not _available_citations(payload):
        evidence_chunks = []
    return {
        "step1_locate": "Description of evidence source locations",
        "step2_evidence": ["Direct evidence quote"],
        "step3_reasoning": {
            "summary": "One-sentence assessment",
            "reasons": [
                {
                    "point": "Human-readable aspect label",
                    "explanation": "How the cited evidence supports this aspect",
                    "evidence_chunks": evidence_chunks,
                }
            ],
            "score_justification": "Why the final score was selected from the allowed scores",
        },
        "score": payload.allowed_scores[-1] if payload.allowed_scores else 1,
    }


def _available_citations(payload: RuleExplanationInput) -> set[str]:
    return {
        evidence.chunk_id
        for criterion in payload.matched_criteria
        for evidence in criterion.evidence
    }


def _language_instruction(lang: str) -> str:
    return "请使用简体中文输出。" if lang.startswith("zh") else "Write the explanation in English."
