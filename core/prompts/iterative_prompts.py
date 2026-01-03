"""
Iterative Evaluation Prompts

English prompts for the three-stage iterative evaluation process.
"""
from typing import List, Dict, Any


# ============================================================================
# Prompt 1: Evidence Extraction (called per chunk)
# ============================================================================

EVIDENCE_EXTRACTION_PROMPT = """# Evidence Extraction Task

## Current Item
- Item ID: {item_id}
- Title: {title}
- Evaluation Criteria:
{criteria_list}

## Content to Analyze
{chunk_content}

## Task
Extract verbatim text fragments from the content above that are relevant to the evaluation criteria listed.

## Output Format
Return a JSON array of exact quotes from the source text:
["quote 1", "quote 2", ...]

If no relevant content is found, return an empty array: []
"""


# ============================================================================
# Prompt 2: Dynamic Scoring (called after each iteration)
# ============================================================================

DYNAMIC_SCORING_PROMPT = """# Dynamic Scoring Task

## Current Item
- Item ID: {item_id}
- Title: {title}

## Accumulated Evidence (from {n_sources} sections)
{evidence_cache}

## Scoring Rubric
| Score Range | Condition |
|-------------|-----------|
{scoring_table}

## Task
Based on the accumulated evidence and scoring rubric above, provide the current score (integer 1-7).

## Output Format
Return only a single integer, e.g.: 5
"""


# ============================================================================
# Prompt 3: Final Report (called when iteration ends)
# ============================================================================

FINAL_REPORT_PROMPT = """# Final Evaluation Report

## Item Information
- Item ID: {item_id}
- Domain: {domain_name}
- Title: {title}
- Evaluation Criteria:
{criteria_list}

## Scoring Rubric
| Score Range | Condition |
|-------------|-----------|
{scoring_table}

## Accumulated Evidence
{evidence_cache}

## Evidence Sources
{evidence_sources}

## Final Score
{final_score}

## Task
Generate a complete Chain-of-Thought (CoT) evaluation report with the following structure:

1. **Location Information** (step1_locate): Describe which sections/chapters the evidence came from
2. **Evidence Summary** (step2_evidence): Compile all extracted verbatim quotes
3. **Reasoning Process** (step3_reasoning): Explain why this score was assigned based on the evidence and rubric

## Output Format
Return a JSON object:
{{
    "step1_locate": "...",
    "step2_evidence": "...",
    "step3_reasoning": "..."
}}
"""


# ============================================================================
# Rendering Functions
# ============================================================================

def render_extraction_prompt(
    item_id: str,
    title: str,
    criteria: List[str],
    chunk_content: str
) -> str:
    """
    渲染证据提取提示词
    
    Args:
        item_id: 条目 ID
        title: 评价标题
        criteria: 评价要素列表
        chunk_content: 待分析的 chunk 内容
    
    Returns:
        渲染后的提示词
    """
    criteria_list = "\n".join(f"  - {c}" for c in criteria)
    return EVIDENCE_EXTRACTION_PROMPT.format(
        item_id=item_id,
        title=title,
        criteria_list=criteria_list,
        chunk_content=chunk_content,
    )


def render_scoring_prompt(
    item_id: str,
    title: str,
    evidence_cache: List[str],
    scoring_table: str,
    n_sources: int
) -> str:
    """
    渲染动态评分提示词
    
    Args:
        item_id: 条目 ID
        title: 评价标题
        evidence_cache: 累积的证据列表
        scoring_table: 评分表格 (markdown)
        n_sources: 证据来源数量
    
    Returns:
        渲染后的提示词
    """
    evidence_text = "\n".join(f"- {e}" for e in evidence_cache) if evidence_cache else "No evidence collected yet."
    return DYNAMIC_SCORING_PROMPT.format(
        item_id=item_id,
        title=title,
        evidence_cache=evidence_text,
        scoring_table=scoring_table,
        n_sources=n_sources,
    )


def render_final_report_prompt(
    item_id: str,
    domain_name: str,
    title: str,
    criteria: List[str],
    scoring_table: str,
    evidence_cache: List[str],
    evidence_sources: List[str],
    final_score: int
) -> str:
    """
    渲染最终报告提示词
    
    Args:
        item_id: 条目 ID
        domain_name: 领域名称
        title: 评价标题
        criteria: 评价要素列表
        scoring_table: 评分表格 (markdown)
        evidence_cache: 累积的证据列表
        evidence_sources: 证据来源 chunk_id 列表
        final_score: 最终评分
    
    Returns:
        渲染后的提示词
    """
    criteria_list = "\n".join(f"  - {c}" for c in criteria)
    evidence_text = "\n".join(f"- {e}" for e in evidence_cache) if evidence_cache else "No evidence collected."
    sources_text = ", ".join(evidence_sources) if evidence_sources else "None"
    
    return FINAL_REPORT_PROMPT.format(
        item_id=item_id,
        domain_name=domain_name,
        title=title,
        criteria_list=criteria_list,
        scoring_table=scoring_table,
        evidence_cache=evidence_text,
        evidence_sources=sources_text,
        final_score=final_score,
    )


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "EVIDENCE_EXTRACTION_PROMPT",
    "DYNAMIC_SCORING_PROMPT",
    "FINAL_REPORT_PROMPT",
    "render_extraction_prompt",
    "render_scoring_prompt",
    "render_final_report_prompt",
]
