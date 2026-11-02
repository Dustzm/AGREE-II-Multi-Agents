"""
评价工作流语言策略测试。
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("AMA_AUTH_ENABLED", "false")

from core.agents.tools.reporter_tools import _generate_markdown_report
from core.pipeline import runner as pipeline_runner
from web.backend.app.services.agent_runner import AppraisalRunner


def test_appraisal_runner_uses_english_workflow_prompt():
    """工作流语言应固定为英文，Reporter 用户提示不应混入中文。"""
    runner = AppraisalRunner(lang="zh")

    assert runner.pipeline.workflow_lang == "en"
    prompt = runner.pipeline._build_reporter_prompt("task-123")
    assert "Please start the AGREE-II final report generation task." in prompt
    assert "Task information:" in prompt
    assert "请开始处理" not in prompt


def test_appraisal_runner_refreshes_model_config_at_task_start(monkeypatch, tmp_path):
    """单篇评价任务开始时应刷新一次 LLM 运行时配置。"""
    refreshed_models: list[str] = []
    monkeypatch.setattr(
        pipeline_runner,
        "refresh_model_config_snapshot",
        lambda: refreshed_models.append("huatuo3-32b") or SimpleNamespace(
            app=SimpleNamespace(
                batch_reporter_timeout=60,
                batch_mineru_health_timeout=5,
                batch_mineru_parse_timeout=10,
            ),
            llm=SimpleNamespace(
                provider="vllm",
                model_name="huatuo3-32b",
                base_url="http://llm.example/v1",
            ),
        ),
    )

    errors: list[str] = []
    runner = AppraisalRunner(lang="zh", on_error=errors.append)

    asyncio.run(runner.run("task-123", str(tmp_path / "missing.pdf")))

    assert refreshed_models == ["huatuo3-32b"]
    assert errors


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


def test_markdown_report_neutralises_injected_headings_and_lists():
    report = {
        "status": "success",
        "guideline_title": "G",
        "evaluator": "E",
        "evaluation_date": "2026-01-01",
        "domain_scores": {"1": {"name": "Domain 1", "score": 100.0, "item_count": 1, "evaluated_count": 1}},
        "overall_assessment": {
            "strengths": ["# Injected heading\n- injected bullet"],
            "weaknesses": ["plain weakness text"],
            "recommendation": "Do not recommend",
        },
    }
    markdown = _generate_markdown_report(report)

    assert "\\# Injected heading" in markdown
    assert "\\- injected bullet" in markdown
    assert "\n- injected bullet" not in markdown
    assert "plain weakness text" in markdown
