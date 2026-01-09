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
{chunk_content}

## Your Task
Extract **exact text fragments** from "Content to Analyze" that help determine the score according to the Scoring Rubric.

**What to Extract:**
- Direct quotes from the document that describe relevant facts, data, or statements related to the scoring conditions
- Content that proves or disproves any of the conditions in the rubric

**What NOT to Extract:**
- The rubric text itself
- Your own interpretations or summaries

**Example:**
- ✓ GOOD: "The target patient population comprises all adults with type 2 diabetes"
- ✗ BAD: "Target population"

## Output Format
Return a JSON array of exact quotes:
["quote 1", "quote 2", ...]

Return empty array [] if no relevant content found.
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

## Scoring Reference Examples
The following are reference examples showing how similar guidelines were scored. Use these as behavioral guidance for applying the scoring rubric, not as direct templates:
{shots_section}
## Task
Based on the accumulated evidence, determine which scoring condition is best matched.

**Scoring Steps**:
1. Review the evidence against the scoring rubric
2. Compare the evidence coverage against each scoring condition
3. Select the score range that best matches the evidence

## Output Format
Provide your analysis and score in the following format:

Analysis: [Brief explanation of which scoring condition matches and why]
Score: [integer 1-7]
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

## Accumulated Evidence
{evidence_cache}

## Evidence Sources
{evidence_sources}

## Final Score
{final_score}

## Task
Generate a complete Chain-of-Thought (CoT) evaluation report with the following structure:

1. **Location Information** (step1_locate): Describe which sections/chapters the evidence came from
2. **Evidence Summary** (step2_evidence): List the key quotes extracted as evidence
3. **Reasoning Process** (step3_reasoning): Provide a detailed explanation that includes:
   - How the evidence matches (or fails to match) each scoring condition
   - Why the final score was chosen over other possible scores

## Output Format
Return a JSON object:
{{
    "step1_locate": "Description of evidence locations...",
    "step2_evidence": ["quote1", "quote2", ...],
    "step3_reasoning": "Detailed explanation: The evidence addresses the scoring rubric by showing [specific content]. This matches the condition for score [N] because [reason]. Score [N-1] was not chosen because [missing element]."
}}

IMPORTANT: The step3_reasoning must explicitly reference the scoring rubric conditions and explain which condition best matches the evidence.
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
    evidence_sources: list[str],
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
        evidence_sources: 证据来源 chunk_id 列表
        final_score: 最终评分
    
    Returns:
        渲染后的提示词
    """
    evidence_text = "\n".join(f"- {e}" for e in evidence_cache) if evidence_cache else "No evidence collected."
    sources_text = ", ".join(evidence_sources) if evidence_sources else "None"
    
    return FINAL_REPORT_PROMPT.format(
        item_id=item_id,
        domain_name=domain_name,
        title=title,
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
