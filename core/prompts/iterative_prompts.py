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

## Scoring Rubric (Use these conditions to identify relevant evidence)
| Score Range | Condition |
|-------------|-----------|
{scoring_table}

## Content to Analyze
```
{chunk_content}
```

## Your Task
Extract **complete and exact text fragments** from "Content to Analyze" that help determine the score according to the Scoring Rubric.

**Extraction Principles:**
1. **COMPLETENESS IS CRITICAL**: Extract ALL relevant information completely. Do NOT summarize, truncate, or abbreviate.
2. **Preserve Full Context**: Each extracted quote must retain its complete meaning. Include surrounding context if necessary for understanding.
3. **Multiple Evidence Items**: If there are multiple pieces of evidence, extract each one separately but in full.
4. **Err on the side of inclusion**: When in doubt, include the content rather than omit it.

**What to Extract:**
- Direct quotes from the document that describe relevant facts, data, or statements related to the scoring conditions
- Content that proves or disproves any of the conditions in the rubric
- Names, affiliations, roles, methods, processes, dates, or any specific details mentioned
- Complete sentences or paragraphs that contain valuable information

**What NOT to Extract:**
- The rubric text itself
- Your own interpretations or summaries
- Irrelevant content that has no connection to the scoring conditions

**Example:**
- ✓ GOOD: "Thomas Skurk, ZIEL Institute for Food & Health, Technical University of Munich, served as the lead methodologist for the systematic review process."
- ✗ BAD: "Thomas Skurk" (too brief, missing context)
- ✗ BAD: "The authors are listed with affiliations" (summary, not a quote)

## Output Format
Return a JSON object with an "evidence" array. Each item must include the chunk_id where the evidence was found:
{{
    "evidence": [
        {{"chunk_id": "chunk_001", "evidence": "complete quote 1"}},
        {{"chunk_id": "chunk_002", "evidence": "complete quote 2"}}
    ]
}}

**IMPORTANT**: The chunk_id must match exactly the "[chunk_XXX]" label shown at the beginning of each content section in "Content to Analyze".

Return {{"evidence": []}} if no relevant content found.
"""


# ============================================================================
# Prompt 2: Dynamic Scoring (called after each iteration)
# ============================================================================

DYNAMIC_SCORING_PROMPT = """# Dynamic Scoring Task

## Current Item
- Item ID: {item_id}
- Title: {title}

## Accumulated Evidence (from {n_sources} sections)
```
{evidence_cache}
```

## Scoring Rubric
| Score Range | Condition |
|-------------|-----------|
{scoring_table}

## Scoring Reference Examples
The following are reference examples showing how similar guidelines were scored. Use these as behavioral guidance for applying the scoring rubric, not as direct templates:
{shots_section}

## Task
Based on the accumulated evidence, determine which scoring condition is best matched.

**Evidence Anchor**:
- {n_sources} evidence sections were provided for analysis
- If evidence sections exist (n > 0), relevant information IS present in the document — a score of 1 ("no information found") is almost certainly incorrect
- A score of 1 is ONLY appropriate when n_sources = 0 or ALL evidence is completely irrelevant to the evaluation criteria

**Scoring Steps**:
1. Review the evidence against the scoring rubric
2. Compare the evidence coverage against each scoring condition
3. Select the score range that best matches the evidence

**CRITICAL RULE**: 
You MUST strictly adhere to the "Score Range" defined in the Scoring Rubric. 
- You may ONLY assign a score if it explicitly falls within one of the provided ranges.
- If a score (e.g., 1) is NOT included in any of the ranges (e.g., if ranges are "2" and "3-7"), you are FORBIDDEN from assigning that score.
- STRICTLY NO hallucinations of scores outside the provided ranges.

## Output Format
Return a JSON object with exactly two keys:
1. 'analysis': brief explanation of which criteria are met and why this score range was selected
2. 'score': integer (must be strictly within one of the valid score ranges from the Scoring Rubric)

Example: {{"analysis": "The evidence covers...", "score": 5}}
"""


# ============================================================================
# Prompt 3: Final Report (called when iteration ends)
# ============================================================================

FINAL_REPORT_PROMPT = """# Final Evaluation Report

## Item Information
- Item ID: {item_id}
- Domain: {domain_name}
- Title: {title}

## Scoring Rubric
| Score Range | Condition |
|-------------|-----------|
{scoring_table}

## Accumulated Evidence (each labeled with its chunk_id)
```
{evidence_cache_indexed}
```

## Final Score
```
{final_score}
```

## Task
Generate a structured evaluation report with clear, itemized reasoning.

## Output Format
Return a JSON object with this EXACT structure:
{{
    "step1_locate": "Description of which sections/chapters the evidence came from",
    "step3_reasoning": {{
        "summary": "One-sentence overall assessment of this item",
        "reasons": [
            {{
                "point": "Short label for this scoring aspect (e.g., 'Health Intent', 'Target Population')",
                "explanation": "Detailed explanation of how the evidence supports or fails this aspect",
                "evidence_chunks": ["chunk_001", "chunk_005"]  // Array of chunk_ids in format "chunk_XXX" from the evidence list above
            }}
        ],
        "score_justification": "Explain why this score was chosen and why adjacent scores were not appropriate"
    }}
}}

## Guidelines
1. Each item in "reasons" should address one specific aspect from the scoring rubric
2. "evidence_chunks" must contain chunk_ids in the format "chunk_XXX" (e.g., "chunk_001", "chunk_015") - use the exact chunk_id labels shown in the evidence list, NOT numeric indices like [0] or [1]
3. Include 2-5 reasons depending on the complexity of the scoring rubric
4. The "score_justification" should explicitly reference the rubric conditions

IMPORTANT: Return ONLY the JSON object, no additional text or markdown formatting.
"""


# ============================================================================
# Rendering Functions
# ============================================================================

def render_extraction_prompt(
    item_id: str,
    title: str,
    scoring_table: str,
    chunk_content: str
) -> str:
    """
    渲染证据提取提示词
    
    Args:
        item_id: 条目 ID
        title: 评价标题
        scoring_table: 评分表格 (用于指导提取)
        chunk_content: 待分析的 chunk 内容
    
    Returns:
        渲染后的提示词
    """
    return EVIDENCE_EXTRACTION_PROMPT.format(
        item_id=item_id,
        title=title,
        scoring_table=scoring_table,
        chunk_content=chunk_content,
    )


def render_scoring_prompt(
    item_id: str,
    title: str,
    shots: list[str],  # 评分示例列表
    evidence_cache: list[str],
    scoring_table: str,
    n_sources: int
) -> str:
    """
    渲染动态评分提示词
    
    Args:
        item_id: 条目 ID
        title: 评价标题
        shots: 评分示例列表（用于 few-shot 指导）
        evidence_cache: 累积的证据列表
        scoring_table: 评分表格 (markdown)
        n_sources: 证据来源数量
    
    Returns:
        渲染后的提示词
    """
    evidence_text = "\n".join(f"- {e}" for e in evidence_cache) if evidence_cache else "No evidence collected yet."
    
    # 构建 shots 示例部分（仅动态内容，固定标题在模板中）
    if shots:
        shots_text = "\n" + "\n".join(f"- {s}" for s in shots) + "\n"
    else:
        shots_text = "\n(No reference examples available)\n"
    
    return DYNAMIC_SCORING_PROMPT.format(
        item_id=item_id,
        title=title,
        evidence_cache=evidence_text,
        scoring_table=scoring_table,
        shots_section=shots_text,
        n_sources=n_sources,
    )


def render_final_report_prompt(
    item_id: str,
    domain_name: str,
    title: str,
    shots: list[str],  # 评分示例（最终报告暂不使用）
    scoring_table: str,
    evidence_cache: list[str],
    evidence_chunk_map: list[str],  # 每条证据对应的 chunk_id（精确映射）
    final_score: int
) -> str:
    """
    渲染最终报告提示词
    
    Args:
        item_id: 条目 ID
        domain_name: 领域名称
        title: 评价标题
        shots: 评分示例列表（最终报告暂不使用）
        scoring_table: 评分表格 (markdown)
        evidence_cache: 累积的证据列表
        evidence_chunk_map: 每条证据对应的 chunk_id 列表（与 evidence_cache 一一对应）
        final_score: 最终评分
    
    Returns:
        渲染后的提示词
    """
    # 带 chunk_id 的证据列表，便于 LLM 引用
    # 使用 evidence_chunk_map 进行精确映射
    if evidence_cache and evidence_chunk_map:
        evidence_indexed_parts = []
        for i, e in enumerate(evidence_cache):
            chunk_id = evidence_chunk_map[i] if i < len(evidence_chunk_map) else f"unknown_{i+1:03d}"
            evidence_indexed_parts.append(f"[{chunk_id}] {e}")
        evidence_indexed = "\n".join(evidence_indexed_parts)
    elif evidence_cache:
        evidence_indexed = "\n".join(f"[chunk_{i+1:03d}] {e}" for i, e in enumerate(evidence_cache))
    else:
        evidence_indexed = "No evidence collected."
    
    return FINAL_REPORT_PROMPT.format(
        item_id=item_id,
        domain_name=domain_name,
        title=title,
        scoring_table=scoring_table,
        evidence_cache_indexed=evidence_indexed,
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
