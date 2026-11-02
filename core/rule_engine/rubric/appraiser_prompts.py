"""Prompt templates for portfolio-level rubric appraisal."""
from __future__ import annotations

import json

from core.rule_engine.models import EvidenceUnit, ItemSpec


SYSTEM_PROMPT = (
    "You are an AGREE-II item appraiser. Judge the provided evidence portfolio "
    "against the current item skill standard. Return JSON only."
)


def render_rubric_appraisal_prompt(
    spec: ItemSpec,
    units: list[EvidenceUnit],
    *,
    lang: str = "zh",
) -> str:
    """Render the main portfolio-level appraisal prompt."""
    rubric = spec.rubric_appraiser
    if rubric is None:
        raise ValueError(f"{spec.item_id} is missing RubricAppraiserSpec")
    return f"""# AGREE-II Portfolio Rubric Appraisal

## Item
- item_id: {spec.item_id}
- title: {spec.title}

## Skill Standard
{rubric.guidance}

## Appraisal Protocol
Read the evidence portfolio as a whole. Assign one final integer score from 1 to 7 according to the skill standard.
Do not derive the score from keywords, object counts, or isolated evidence-unit labels.
Use only the provided portfolio as citation evidence. Do not invent chunk_id or quote.
For score > 1, include at least one direct quote from the portfolio evidence.
Keep rationale and why_not_higher short.

## Item-Specific Notes
{_profile_rules(rubric.profile)}

## Evidence Portfolio
```json
{json.dumps([_unit_payload(index, unit) for index, unit in enumerate(units)], ensure_ascii=False, indent=2)}
```

## Required JSON
```json
{{
  "score": 1,
  "confidence": 0.8,
  "decision_label": "no_relevant_evidence",
  "rationale": "short reason",
  "why_not_higher": "short reason",
  "evidence": []
}}
```
"""


def render_rubric_appraisal_retry_prompt(
    spec: ItemSpec,
    units: list[EvidenceUnit],
    *,
    error_summary: str,
    lang: str = "zh",
) -> str:
    """Render a minimal retry prompt for malformed or invalid JSON."""
    rubric = spec.rubric_appraiser
    if rubric is None:
        raise ValueError(f"{spec.item_id} is missing RubricAppraiserSpec")
    return f"""# Retry: AGREE-II Portfolio Rubric Appraisal

Previous error: {_truncate(error_summary, 240)}

Item: {spec.item_id} - {spec.title}
Standard: {_truncate(rubric.guidance, 900)}

Return valid JSON only with exactly these fields:
- score: integer 1-7
- decision_label: one of {list(rubric.decision_labels)}
- evidence: array of objects with chunk_id and quote

If you cannot cite direct evidence, return score 1 and evidence [].

Evidence Portfolio:
```json
{json.dumps([_unit_payload(index, unit, compact=True) for index, unit in enumerate(units)], ensure_ascii=False, indent=2)}
```
"""


def _unit_payload(index: int, unit: EvidenceUnit, *, compact: bool = False) -> dict[str, object]:
    limit = 650 if compact else 1200
    return {
        "index": index,
        "unit_id": unit.unit_id,
        "chunk_id": unit.chunk_id,
        "title": unit.title,
        "unit_type": unit.unit_type,
        "quote": _truncate(unit.quote, limit),
        "metadata": unit.metadata,
    }


def _profile_rules(profile: str) -> str:
    rules = {
        "item18_application_factors_appraiser": (
            "Score application facilitators/barriers for the current guideline. Consider whether the guideline "
            "describes factors that help or hinder application, whether the basis for identifying them is described, "
            "and whether they affect recommendations or implementation strategy. Exclude ordinary clinical risks, "
            "disease burden, contraindications, and treatment rationale unless used as implementation/application factors."
        ),
        "item19_implementation_support_appraiser": (
            "Score advice, tools, or resources for putting this guideline's recommendations into practice. "
            "Concrete tools/resources include checklist, template, patient or clinician material, audit form, manual, "
            "resource package, or implementation flowchart. Exclude ordinary clinical workflows, diagnostic/screening/"
            "risk tools, drug/dose tables, and evidence tables unless the guideline explicitly presents them as "
            "implementation support. Scores 6-7 require clear tool/resource use guidance."
        ),
        "item21_monitoring_criteria_appraiser": (
            "Score monitoring or auditing criteria for applying or continuing the guideline recommendations. "
            "Consider indicators, targets, thresholds, frequencies, intervals, audit standards, quality indicators, "
            "and measurement methods across the portfolio. Exclude acute episode rechecks, device operation, pure "
            "diagnostic definitions, cited study outcomes, and guideline update timing."
        ),
    }
    return rules.get(profile, "Apply the item skill standard to the evidence portfolio.")


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."
