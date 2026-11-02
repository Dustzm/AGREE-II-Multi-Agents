"""MCP PDF 上传凭证与任务提交链路测试。"""
from __future__ import annotations

import io
import asyncio
import inspect
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pypdfium2
import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MCP_ROOT = PROJECT_ROOT / "mcp"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(MCP_ROOT))

from config import MCPSettings  # noqa: E402
from errors import MCPErrorCode, MCPJobError  # noqa: E402
from mcp_upload import create_upload_http_app  # noqa: E402
from repository import MCPJobRepository  # noqa: E402
from service import MCPJobService  # noqa: E402
from server import mcp_server, run_mcp_session, submit_pdf_appraisal  # noqa: E402
from worker import MCPWorker  # noqa: E402


@pytest.mark.parametrize("summary_status,detail,expected_code", [
    ("partial", "invalid score", MCPErrorCode.APPRAISAL_INCOMPLETE),
    ("partial", "model timed out", MCPErrorCode.UPSTREAM_TIMEOUT),
    ("success", "", MCPErrorCode.APPRAISAL_INCOMPLETE),
])
def test_mcp_incomplete_appraisal_preserves_retryable_failure(
    tmp_path, monkeypatch, caplog, summary_status, detail, expected_code,
):
    """真实 Runner 的不完整结果必须进入 MCP 错误路径，并保留条目原因。"""
    from core.config import get_settings
    from core.agents.tools import appraiser_batch, appraiser_tools, public_tools, reporter_tools
    from core.utils.cancellation import CancellationToken
    from core.pipeline import runner as pipeline_runner

    settings = get_settings()
    output_root = tmp_path / "output"
    monkeypatch.setattr(settings.app, "dir_output", str(output_root))
    monkeypatch.setattr(settings.llm, "context_window", 131072)
    monkeypatch.setattr(public_tools, "_task_contexts", {})
    job = {"job_id": "test-job", "run_token": "test-run", "input_path": str(tmp_path / "guide.pdf")}
    Path(job["input_path"]).write_bytes(b"%PDF-1.4\n%test\n")
    task_dir = output_root / "mcp" / job["job_id"] / job["run_token"] / job["job_id"]
    prep_dir = task_dir / ".ama" / "prep"
    prep_dir.mkdir(parents=True)
    (task_dir / "source.md").write_text("Guide methods", encoding="utf-8")
    (prep_dir / "guide_content_list.json").write_text("[]", encoding="utf-8")
    original_error = TimeoutError(detail) if "timed out" in detail else ValueError(detail)

    async def preprocess(**kwargs):
        Path(kwargs["output_path"]).write_text("{}", encoding="utf-8")
        return {"status": "success"}

    async def appraise(task_id, **kwargs):
        for number in range(1, 23):
            saved = appraiser_tools.save_item_evaluation(task_id, f"item_{number}", "Methods", [], "Available", 4)
            assert saved["status"] == "success"
        return {
            "status": summary_status,
            "failed": {"item_23": detail} if detail else {},
            "failed_details": {
                "item_23": {"type": "ModelError", "stage": "dynamic_scoring", "message": detail},
            } if detail else {},
            "exceptions": {"item_23": original_error} if detail else {},
        }

    reporter_calls = []

    async def report(self, task_id):
        reporter_calls.append(task_id)
        result = reporter_tools.generate_appraisal_report(task_id, [], [])
        assert result["status"] == "success"

    failures = []
    repository = SimpleNamespace(finish_unsuccessful=lambda *args, **kwargs: failures.append(kwargs))
    worker = MCPWorker(repository=repository, settings=MCPSettings(enabled=True, api_key="test"))
    monkeypatch.setattr(worker, "_heartbeat", lambda *args: None)
    monkeypatch.setattr(pipeline_runner, "process_content_list", preprocess)
    monkeypatch.setattr(appraiser_batch, "appraise_all", appraise)
    monkeypatch.setattr(pipeline_runner.AppraisalPipeline, "_run_reporter_with_retries", report)

    asyncio.run(worker._run_claimed_job(job, CancellationToken()))

    assert len(failures) == 1
    assert failures[0]["error_code"] == expected_code
    assert MCPJobError(expected_code).retryable is True
    assert "item_23" in failures[0]["error_detail"]
    if detail:
        assert detail in failures[0]["error_detail"]
        assert "dynamic_scoring" in failures[0]["error_detail"]
        assert f"{type(original_error).__name__}: {detail}" in caplog.text
    assert reporter_calls == ([job["job_id"]] if summary_status == "success" else [])


def _pdf_bytes() -> bytes:
    """生成一个可由 PDFium 校验的单页 PDF。"""
    output = io.BytesIO()
    document = pypdfium2.PdfDocument.new()
    document.new_page(595, 842)
    document.save(output)
    document.close()
    return output.getvalue()


def _service(tmp_path: Path, *, max_pdf_bytes: int = 1024 * 1024) -> tuple[MCPJobService, MCPSettings]:
    """创建使用临时目录和数据库的 MCP 服务。"""
    settings = MCPSettings(
        enabled=True,
        api_key="test-api-key",
        max_request_bytes=2 * 1024 * 1024,
        max_pdf_bytes=max_pdf_bytes,
        min_free_disk_bytes=0,
    )
    repository = MCPJobRepository(tmp_path / "ama.db")
    service = MCPJobService(
        repository,
        settings,
        input_root=tmp_path / "input",
        output_root=tmp_path / "output",
    )
    service.initialize()
    return service, settings


def test_upload_then_submit_by_upload_id(tmp_path: Path) -> None:
    """上传接口返回的 ID 可且仅可创建一个新任务。"""
    service, settings = _service(tmp_path)
    client = TestClient(create_upload_http_app(service, settings))

    unauthorized = client.post("/", files={"file": ("guide.pdf", _pdf_bytes(), "application/pdf")})
    assert unauthorized.status_code == 401

    response = client.post(
        "/",
        headers={"Authorization": "Bearer test-api-key"},
        files={"file": ("guide.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["upload_id"].startswith("mcp-upload-")
    assert uploaded["filename"] == "guide.pdf"
    assert uploaded["page_count"] == 1
    assert "stored_path" not in uploaded

    submitted = service.submit(uploaded["upload_id"], "request-001", "hybrid")
    assert submitted["status"] == "queued"
    assert submitted["upload_id"] == uploaded["upload_id"]

    duplicate = service.submit(uploaded["upload_id"], "request-001", "hybrid")
    assert duplicate["job_id"] == submitted["job_id"]
    assert duplicate["deduplicated"] is True

    with pytest.raises(MCPJobError) as reused:
        service.submit(uploaded["upload_id"], "request-002", "hybrid")
    assert reused.value.error_code == MCPErrorCode.UPLOAD_ALREADY_USED


def test_upload_rejects_oversized_pdf_without_leaving_file(tmp_path: Path) -> None:
    """超限文件在流式写入阶段被拒绝且不留下上传目录。"""
    service, _ = _service(tmp_path, max_pdf_bytes=1024)
    oversized = _pdf_bytes() + b"x" * 1024

    with pytest.raises(MCPJobError) as rejected:
        service.upload("guide.pdf", io.BytesIO(oversized))

    assert rejected.value.error_code == MCPErrorCode.PDF_TOO_LARGE
    upload_root = tmp_path / "input" / "mcp" / "uploads"
    assert not upload_root.exists() or not any(upload_root.iterdir())


def test_upload_http_rejects_request_body_before_parsing(tmp_path: Path) -> None:
    """HTTP 请求体超限时在 multipart 解析前返回 413。"""
    service, settings = _service(tmp_path, max_pdf_bytes=1024)
    settings.max_request_bytes = 1024
    client = TestClient(create_upload_http_app(service, settings))

    response = client.post(
        "/",
        headers={"Authorization": "Bearer test-api-key"},
        files={"file": ("guide.pdf", _pdf_bytes() + b"x" * 1024, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_upload_http_rejects_multiple_files(tmp_path: Path) -> None:
    """同一次 multipart 请求携带多个 PDF 时明确拒绝。"""
    service, settings = _service(tmp_path)
    client = TestClient(create_upload_http_app(service, settings))

    response = client.post(
        "/",
        headers={"Authorization": "Bearer test-api-key"},
        files=[
            ("file", ("one.pdf", _pdf_bytes(), "application/pdf")),
            ("file", ("two.pdf", _pdf_bytes(), "application/pdf")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_FILE_COUNT"

    missing = client.post(
        "/",
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "INVALID_FILE_COUNT"


def test_submit_tool_no_longer_accepts_base64() -> None:
    """公开 MCP 工具仅接受上传 ID，不再暴露 Base64 参数。"""
    parameters = inspect.signature(submit_pdf_appraisal).parameters

    assert "upload_id" in parameters
    assert "pdf_base64" not in parameters
    assert "filename" not in parameters


@pytest.mark.asyncio
async def test_mcp_public_descriptions_are_english() -> None:
    """MCP discovery metadata exposed to Agents contains no Chinese copy."""
    tools = await mcp_server.list_tools()
    metadata = [mcp_server.instructions]
    for tool in tools:
        metadata.extend(
            [
                tool.description or "",
                json.dumps(tool.inputSchema, ensure_ascii=False),
            ]
        )

    assert not re.search(r"[\u3400-\u9fff]", "\n".join(metadata))


def test_concurrent_idempotent_submit_creates_one_job(tmp_path: Path) -> None:
    """并发重试同一上传和幂等键时只创建一个任务。"""
    service, _ = _service(tmp_path)
    uploaded = service.upload("guide.pdf", io.BytesIO(_pdf_bytes()))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                service.submit,
                uploaded["upload_id"],
                "concurrent-request",
                "hybrid",
            )
            for _ in range(2)
        ]
    results = [future.result() for future in futures]

    assert results[0]["job_id"] == results[1]["job_id"]
    with service.repository._connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM mcp_jobs").fetchone()[0]
    assert count == 1


def test_idempotent_retry_ignores_new_job_disk_guard(tmp_path: Path, monkeypatch) -> None:
    """已有任务的幂等重试无需再次满足新任务磁盘门槛。"""
    service, _ = _service(tmp_path)
    uploaded = service.upload("guide.pdf", io.BytesIO(_pdf_bytes()))
    submitted = service.submit(uploaded["upload_id"], "disk-retry", "hybrid")
    monkeypatch.setattr(
        service,
        "_capacity_error",
        lambda **_kwargs: MCPErrorCode.LOW_DISK_SPACE,
    )

    duplicate = service.submit(uploaded["upload_id"], "disk-retry", "hybrid")

    assert duplicate["job_id"] == submitted["job_id"]
    assert duplicate["deduplicated"] is True


def test_expired_lease_cannot_publish_result(tmp_path: Path) -> None:
    """租约过期后即使 Worker token 尚未回收也不能发布结果。"""
    service, _ = _service(tmp_path)
    uploaded = service.upload("guide.pdf", io.BytesIO(_pdf_bytes()))
    submitted = service.submit(uploaded["upload_id"], "expired-lease", "hybrid")
    job = service.repository.claim_next("worker-1", lease_seconds=-1)

    assert job is not None
    assert job["job_id"] == submitted["job_id"]
    assert service.repository.complete_job(
        job["job_id"],
        "worker-1",
        job["run_token"],
        result_task_id="result-task",
        result_dir=str(tmp_path / "result"),
        token_usage={},
    ) is False


@pytest.mark.asyncio
async def test_mcp_session_can_restart_in_same_process() -> None:
    """每次应用生命周期都使用新的 FastMCP Session Manager。"""
    async with run_mcp_session() as first_server:
        first_tools = await first_server.list_tools()
    async with run_mcp_session() as second_server:
        second_tools = await second_server.list_tools()

    expected = {
        "submit_pdf_appraisal",
        "get_appraisal_status",
        "cancel_appraisal",
        "get_appraisal_result",
    }
    assert {tool.name for tool in first_tools} == expected
    assert {tool.name for tool in second_tools} == expected
    assert all(tool.description for tool in second_tools)
