"""指南评价部分报告、断点续跑与 Reporter 错误恢复。"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agents.tools import appraiser_batch, appraiser_tools, reporter_tools
from core.agents.tools.task_manifest import ensure_task_manifest
from web.backend.app.api.routes import reports as report_routes
from web.backend.app.services.agent_runner import AppraisalRunner


def _task_paths(root: Path) -> dict[str, str]:
    task_dir = root / "task-recovery"
    prep_dir = task_dir / ".ama" / "prep"
    eval_dir = task_dir / ".ama" / "evaluations"
    prep_dir.mkdir(parents=True, exist_ok=True)
    eval_dir.mkdir(parents=True, exist_ok=True)
    (prep_dir / "guide_docindex.json").write_text(json.dumps({"title_list": [{"cluster_level": 1, "title": "Example Guide"}]}), encoding="utf-8")
    return {"task_dir": str(task_dir), "weaver_dir": str(task_dir / ".ama"), "prep_dir": str(prep_dir), "eval_dir": str(eval_dir), "source": str(task_dir / "source.md")}


def _write_item(paths: dict[str, str], number: int) -> None:
    item_id = f"item_{number}"
    evaluation = {"item_id": item_id, "domain": next(domain for domain, items in reporter_tools.DOMAIN_ITEMS.items() if item_id in items), "title": item_id, "chain_of_thought": {"score": 4, "step1_locate": "section", "step2_evidence": [], "step3_reasoning": "available"}}
    (Path(paths["eval_dir"]) / f"{item_id}.json").write_text(json.dumps(evaluation), encoding="utf-8")


def test_partial_report_becomes_complete_after_three_items(monkeypatch, tmp_path):
    paths = _task_paths(tmp_path)
    monkeypatch.setattr(appraiser_tools, "get_task_paths", lambda task_id: paths)
    monkeypatch.setattr(reporter_tools, "get_task_paths", lambda task_id: paths)
    for number in range(1, 21):
        _write_item(paths, number)

    reporter_tools.generate_appraisal_report(
        "task-recovery",
        ["item_1 (4/7): The saved appraisal supports this observation"],
        ["item_21 (4/7): Missing appraisal is a guideline weakness"],
    )
    report_path = Path(paths["task_dir"]) / "appraisal_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "partial"
    assert report["metadata"]["total_items_evaluated"] == 20
    assert report["missing_items"] == ["item_21", "item_22", "item_23"]
    assert report["overall_quality_score"] is None
    assert report["total_raw_score"] is None
    assert report["overall_assessment"]["recommendation"] is None
    assert report["domain_scores"]["1"]["score"] == 50.0
    assert report["domain_scores"]["5"]["score"] is None
    assert report["domain_scores"]["5"]["evaluated_count"] == 3
    assert report["overall_assessment"]["strengths"] == ["item_1 (4/7): The saved appraisal supports this observation"]
    assert report["overall_assessment"]["weaknesses"] == ["item_21 (4/7): Missing appraisal is a guideline weakness"]
    assert "item_21" in report["assessment_limitations"][0]
    assert "Partial report" in (Path(paths["task_dir"]) / "appraisal_report.md").read_text(encoding="utf-8")
    assert "Pending (3/4)" in (Path(paths["task_dir"]) / "appraisal_report.md").read_text(encoding="utf-8")

    monkeypatch.setattr(report_routes, "get_output_dir", lambda: tmp_path)
    legacy_partial = dict(report)
    legacy_partial["total_raw_score"] = 80
    legacy_partial["overall_quality_score"] = 4
    legacy_partial["overall_assessment"] = {**report["overall_assessment"], "recommendation": "Recommend"}
    report_path.write_text(json.dumps(legacy_partial), encoding="utf-8")
    detail = asyncio.run(report_routes.get_report("task-recovery"))
    assert detail.overall_quality_score is None
    assert detail.total_raw_score is None
    assert detail.overall_assessment["recommendation"] is None
    assert detail.assessment_limitations == report["assessment_limitations"]

    for number in range(21, 24):
        _write_item(paths, number)
    reporter_tools.generate_appraisal_report("task-recovery", ["all items"], [])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "complete"
    assert report["missing_items"] == []
    assert report["total_raw_score"] == 92
    assert report["overall_quality_score"] is not None
    assert report["overall_assessment"]["recommendation"] is not None


def test_free_form_domain_narratives_are_saved_without_filtering(monkeypatch, tmp_path):
    paths = _task_paths(tmp_path)
    monkeypatch.setattr(appraiser_tools, "get_task_paths", lambda task_id: paths)
    monkeypatch.setattr(reporter_tools, "get_task_paths", lambda task_id: paths)
    for number in range(1, 24):
        _write_item(paths, number)

    strength = "领域一 范围和目的（88.9%）：总体目标与适用人群描述清晰；item_2（5/7）在研究设计上仍有缺口。"
    weakness = "领域三 严谨性（35.4%）：item_7（2/7）未给出数据库与检索式，item_8（1/7）缺少选择标准。"
    reporter_tools.generate_appraisal_report("task-recovery", [strength], [weakness])

    task_dir = Path(paths["task_dir"])
    report = json.loads((task_dir / "appraisal_report.json").read_text(encoding="utf-8"))
    assert report["overall_assessment"]["strengths"] == [strength]
    assert report["overall_assessment"]["weaknesses"] == [weakness]

    markdown = (task_dir / "appraisal_report.md").read_text(encoding="utf-8")
    assert strength in markdown
    assert weakness in markdown
    assert f"- {strength}" not in markdown


def test_resume_appraises_only_missing_three(monkeypatch, tmp_path):
    paths = _task_paths(tmp_path)
    monkeypatch.setattr(appraiser_tools, "get_task_paths", lambda task_id: paths)
    for number in range(1, 21):
        _write_item(paths, number)
    requested = []

    async def fake_appraise_all(task_id, *, skill_ids, **kwargs):
        requested.extend(skill_ids)
        for item_id in skill_ids:
            _write_item(paths, int(item_id.split("_")[1]))
        return {"status": "success", "failed": {}, "failed_details": {}, "exceptions": {}}

    monkeypatch.setattr(appraiser_batch, "appraise_all", fake_appraise_all)
    runner = AppraisalRunner(refresh_model_config_on_start=False)
    asyncio.run(runner.pipeline._run_batch_appraisal("task-recovery"))
    assert requested == ["item_21", "item_22", "item_23"]
    assert len(appraiser_tools.list_evaluations("task-recovery")["evaluations"]) == 23


def test_reporter_retries_invalid_unicode_json_and_preserves_error(monkeypatch, tmp_path):
    runner = AppraisalRunner(refresh_model_config_on_start=False).pipeline
    json_path = tmp_path / "appraisal_report.json"
    md_path = tmp_path / "appraisal_report.md"
    monkeypatch.setattr(runner, "_report_paths", lambda task_id: (json_path, md_path))
    monkeypatch.setattr(runner, "_sleep_with_cancel", lambda delay: asyncio.sleep(0, result=True))
    attempts = 0

    async def fake_reporter(task_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise json.JSONDecodeError("Invalid \\uXXXX escape", "\\u", 0)
        json_path.write_text(json.dumps({"task_id": task_id, "status": "complete"}), encoding="utf-8")
        md_path.write_text("report", encoding="utf-8")

    monkeypatch.setattr(runner, "_run_reporter_once", fake_reporter)
    asyncio.run(runner._run_reporter_with_retries("task-recovery"))
    assert attempts == 2

    async def always_invalid(task_id):
        nonlocal attempts
        attempts += 1
        raise json.JSONDecodeError("Invalid \\uXXXX escape", "\\u", 0)

    attempts = 0
    monkeypatch.setattr(runner, "_run_reporter_once", always_invalid)
    with pytest.raises(json.JSONDecodeError, match="Invalid"):
        asyncio.run(runner._run_reporter_with_retries("task-recovery"))
    assert attempts == 3
    assert json_path.exists()  # 旧报告不掩盖本次异常。


def test_reporter_tool_error_keeps_original_message(monkeypatch):
    from core import agents
    from google.adk import runners

    tool_result = {"status": "error", "error": "invalid tool arguments: Invalid \\uXXXX escape"}

    class FakeSessionService:
        async def create_session(self, **kwargs):
            return SimpleNamespace(id="session")

    class FakeRunner:
        def __init__(self, **kwargs):
            self.session_service = FakeSessionService()

        async def run_async(self, **kwargs):
            response = SimpleNamespace(name="generate_appraisal_report", response=tool_result)
            yield SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(function_response=response)]))

    class FakeRuntime:
        @asynccontextmanager
        async def stage_slot(self, stage):
            yield

    monkeypatch.setattr(agents, "create_reporter_agent", lambda lang: object())
    monkeypatch.setattr(runners, "InMemoryRunner", FakeRunner)
    runner = AppraisalRunner(runtime=FakeRuntime(), refresh_model_config_on_start=False).pipeline
    with pytest.raises(RuntimeError, match="Invalid"):
        asyncio.run(runner._run_reporter_once("task-recovery"))


def test_manifest_rejects_changed_pdf_without_touching_items(tmp_path):
    paths = _task_paths(tmp_path)
    pdf = tmp_path / "guide.pdf"
    pdf.write_bytes(b"first")
    ensure_task_manifest(paths["task_dir"], pdf, "llm")
    _write_item(paths, 1)
    saved = (Path(paths["eval_dir"]) / "item_1.json").read_bytes()
    pdf.write_bytes(b"second")
    with pytest.raises(ValueError, match="不一致"):
        ensure_task_manifest(paths["task_dir"], pdf, "llm")
    assert (Path(paths["eval_dir"]) / "item_1.json").read_bytes() == saved


def test_reporter_tool_boundary_returns_error_to_model(monkeypatch, tmp_path):
    from core.agents.tools.boundary import as_tool
    paths = _task_paths(tmp_path)
    monkeypatch.setattr(reporter_tools, "get_task_paths", lambda task_id: paths)
    monkeypatch.setattr(appraiser_tools, "get_task_paths", lambda task_id: paths)
    with pytest.raises(ValueError):
        reporter_tools.generate_appraisal_report("task-recovery", [], [])
    result = as_tool(reporter_tools.generate_appraisal_report)("task-recovery", [], [])
    assert result["status"] == "error" and "ValueError" in result["error"]
    assert as_tool(reporter_tools.generate_appraisal_report).__name__ == "generate_appraisal_report"
