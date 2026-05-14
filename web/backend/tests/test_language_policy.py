"""
评价工作流语言策略测试。
"""
from __future__ import annotations

import os

os.environ.setdefault("AMA_AUTH_ENABLED", "false")

from core.agents.tools.reporter_tools import _generate_markdown_report
from web.backend.app.services.agent_runner import AppraisalRunner


def test_appraisal_runner_uses_english_workflow_prompt():
    """工作流语言应固定为英文，Reporter 用户提示不应混入中文。"""
    runner = AppraisalRunner(lang="zh")

    assert runner.workflow_lang == "en"
    prompt = runner._build_reporter_prompt("task-123")
    assert "Please start the AGREE-II final report generation task." in prompt
    assert "Task information:" in prompt
    assert "请开始处理" not in prompt


def test_markdown_report_template_is_fixed_to_english():
    """Markdown 报告模板应始终使用英文标题和章节头。"""
    report = {
        "guideline_title": "Example Guideline",
        "evaluator": "AMA (AGREE-Multi-Agents)",
        "evaluation_date": "2026-05-08 12:00:00",
        "domain_scores": {
            1: {"name": "Scope and Purpose", "score": 77.8},
        },
        "overall_assessment": {
            "strengths": ["Strong scope definition."],
            "weaknesses": ["Limited stakeholder input."],
            "recommendation": "Recommend with modifications",
        },
    }

    markdown = _generate_markdown_report(report)

    assert "# AGREE-II Guideline Quality Appraisal Report" in markdown
    assert "## Guideline Information" in markdown
    assert "### Strengths" in markdown
    assert "### Weaknesses" in markdown
    assert "### Recommendation" in markdown
    assert "## 指南信息" not in markdown
