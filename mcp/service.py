"""MCP 工具使用的提交、查询、取消和结果服务。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

import pypdfium2

from core.config import get_settings

from config import MCPSettings, get_mcp_settings
from errors import MCPErrorCode, MCPJobError, error_payload
from repository import COMPLETED, FAILED, MCPJobRepository, QUEUED

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_UPLOAD_ID_PATTERN = re.compile(r"^mcp-upload-[0-9a-f]{32}$")
_ANALYSIS_ENGINES = {"llm", "rule", "hybrid"}
_COPY_CHUNK_BYTES = 1024 * 1024
logger = logging.getLogger(__name__)


def available_disk_bytes(*paths: Path) -> int:
    """返回相关存储卷中的最小可用空间。"""
    free_bytes: list[int] = []
    for raw_path in paths:
        path = raw_path
        while not path.exists() and path != path.parent:
            path = path.parent
        free_bytes.append(shutil.disk_usage(path).free)
    return min(free_bytes)


class MCPJobService:
    """实现 MCP 单 PDF 作业的业务边界。"""

    def __init__(
        self,
        repository: MCPJobRepository | None = None,
        settings: MCPSettings | None = None,
        *,
        input_root: str | Path | None = None,
        output_root: str | Path | None = None,
    ):
        app_settings = get_settings().app
        self.repository = repository or MCPJobRepository()
        self.settings = settings or get_mcp_settings()
        self.input_root = Path(input_root or app_settings.path_input).resolve()
        self.output_root = Path(output_root or app_settings.path_output).resolve()

    def initialize(self) -> None:
        """校验配置并初始化队列表。"""
        self.settings.validate_runtime()
        self.repository.initialize()

    def upload(self, filename: str, source: BinaryIO) -> dict:
        """流式保存并校验一个短期 PDF 上传。"""
        normalized_name = self._validate_filename(filename)
        capacity_error = self._capacity_error(log_failure=True)
        if capacity_error is not None:
            raise MCPJobError(capacity_error)

        upload_id = f"mcp-upload-{uuid4().hex}"
        upload_dir = self.input_root / "mcp" / "uploads" / upload_id
        stored_path = upload_dir / f"{upload_id}.pdf"
        try:
            input_bytes, digest = self._write_upload_atomically(stored_path, source)
            page_count = self._inspect_pdf_path(stored_path)
            upload = self.repository.create_upload(
                upload_id=upload_id,
                original_filename=normalized_name,
                stored_path=str(stored_path),
                input_sha256=digest,
                input_bytes=input_bytes,
                page_count=page_count,
                retention_hours=self.settings.input_retention_hours,
            )
        except MCPJobError:
            self._remove_unpublished_input(stored_path)
            raise
        except sqlite3.Error as exc:
            self._remove_unpublished_input(stored_path)
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc
        except OSError as exc:
            self._remove_unpublished_input(stored_path)
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc

        return {
            "upload_id": upload_id,
            "filename": normalized_name,
            "input_sha256": digest,
            "input_bytes": input_bytes,
            "page_count": page_count,
            "created_at": upload["created_at"],
            "expires_at": upload["expires_at"],
            "message": "PDF 上传完成，请使用 upload_id 提交 AMA 解析任务",
        }

    def submit(
        self,
        upload_id: str,
        idempotency_key: str,
        analysis_engine: str | None = None,
    ) -> dict:
        """使用已上传的 PDF 创建排队作业。"""
        normalized_upload_id = upload_id.strip()
        self._validate_upload_id(normalized_upload_id)
        normalized_key = idempotency_key.strip()
        self._validate_idempotency_key(normalized_key)
        resolved_engine = self._resolve_analysis_engine(analysis_engine)

        try:
            upload = self.repository.get_upload(normalized_upload_id)
            if upload is None:
                raise MCPJobError(MCPErrorCode.UPLOAD_NOT_FOUND)
            existing = self.repository.get_by_idempotency_key(normalized_key)
        except sqlite3.Error as exc:
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc
        if existing is not None:
            if (
                existing["input_sha256"] != upload["input_sha256"]
                or self._job_analysis_engine(existing) != resolved_engine
            ):
                raise MCPJobError(MCPErrorCode.IDEMPOTENCY_CONFLICT)
            return self._status_payload(existing, deduplicated=True)

        expires_at = datetime.fromisoformat(upload["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise MCPJobError(MCPErrorCode.UPLOAD_EXPIRED)
        if upload.get("consumed_job_id"):
            raise MCPJobError(MCPErrorCode.UPLOAD_ALREADY_USED)
        capacity_error = self._capacity_error(log_failure=True)
        if capacity_error is not None:
            raise MCPJobError(capacity_error)

        job_id = f"mcp-job-{uuid4().hex}"
        input_dir = self.input_root / "mcp" / job_id
        input_path = input_dir / f"{job_id}.pdf"
        try:
            upload_path = self._validated_upload_path(normalized_upload_id, upload["stored_path"])
            self._copy_input_atomically(upload_path, input_path)
        except MCPJobError:
            raise
        except OSError as exc:
            self._remove_unpublished_input(input_path)
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc
        try:
            job, created = self.repository.create_job(
                job_id=job_id,
                idempotency_key=normalized_key,
                original_filename=upload["original_filename"],
                input_path=str(input_path),
                input_sha256=upload["input_sha256"],
                input_bytes=upload["input_bytes"],
                page_count=upload["page_count"],
                upload_id=normalized_upload_id,
                max_queued_jobs=self.settings.max_queued_jobs,
                analysis_engine=resolved_engine,
            )
        except MCPJobError:
            self._remove_unpublished_input(input_path)
            raise
        except sqlite3.Error as exc:
            self._remove_unpublished_input(input_path)
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc
        except Exception:
            self._remove_unpublished_input(input_path)
            raise
        if not created:
            self._remove_unpublished_input(input_path)
        else:
            self._remove_consumed_upload(normalized_upload_id, upload_path)
        return self._status_payload(job, deduplicated=not created)

    def status(self, job_id: str) -> dict:
        """返回不包含服务器路径的任务状态。"""
        job = self._require_job(job_id)
        return self._status_payload(job)

    def cancel(self, job_id: str) -> dict:
        """取消排队作业或请求运行作业停止。"""
        try:
            job = self.repository.request_cancel(job_id)
        except sqlite3.Error as exc:
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc
        return self._status_payload(job)

    def result(self, job_id: str) -> dict:
        """读取并返回已发布的 JSON 报告。"""
        job = self._require_job(job_id)
        if job["status"] != COMPLETED:
            if job["status"] == FAILED:
                raise MCPJobError(self._stored_error_code(job))
            raise MCPJobError(MCPErrorCode.JOB_NOT_READY)
        if job.get("result_expired_at"):
            raise MCPJobError(MCPErrorCode.RESULT_EXPIRED)

        result_dir = self._validated_result_dir(job_id, job.get("result_dir"))
        report_path = result_dir / "appraisal_report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPJobError(MCPErrorCode.RESULT_INVALID, internal_detail=str(exc)) from exc
        if report.get("task_id") != job.get("result_task_id"):
            raise MCPJobError(
                MCPErrorCode.RESULT_INVALID,
                internal_detail="报告 task_id 与任务记录不一致",
            )
        return {
            "job_id": job_id,
            "status": COMPLETED,
            "filename": job["original_filename"],
            "upload_id": job.get("upload_id"),
            "analysis_engine": self._job_analysis_engine(job),
            "input_sha256": job["input_sha256"],
            "page_count": job["page_count"],
            "result_task_id": job["result_task_id"],
            "token_usage": job["token_usage"],
            "report": report,
        }

    @staticmethod
    def _validate_filename(filename: str) -> str:
        """规范并验证展示用 PDF 文件名。"""
        normalized = Path(filename.strip()).name
        if not normalized or normalized in {".", ".."} or len(normalized) > 255:
            raise MCPJobError(MCPErrorCode.INVALID_FILENAME)
        if Path(normalized).suffix.lower() != ".pdf":
            raise MCPJobError(MCPErrorCode.INVALID_FILE_TYPE)
        return normalized

    @staticmethod
    def _validate_idempotency_key(key: str) -> None:
        """限制幂等键字符集和长度。"""
        if not _IDEMPOTENCY_PATTERN.fullmatch(key):
            raise MCPJobError(MCPErrorCode.INVALID_IDEMPOTENCY_KEY)

    @staticmethod
    def _validate_upload_id(upload_id: str) -> None:
        """限制上传 ID 格式，避免路径或查询边界被绕过。"""
        if not _UPLOAD_ID_PATTERN.fullmatch(upload_id):
            raise MCPJobError(MCPErrorCode.INVALID_UPLOAD_ID)

    @staticmethod
    def _resolve_analysis_engine(analysis_engine: str | None) -> str:
        """解析并校验单个任务使用的评价引擎。"""
        resolved = get_settings().app.analysis_engine if analysis_engine is None else analysis_engine
        if resolved not in _ANALYSIS_ENGINES:
            raise MCPJobError(MCPErrorCode.INVALID_ANALYSIS_ENGINE)
        return resolved

    @staticmethod
    def _job_analysis_engine(job: dict) -> str:
        """读取任务引擎；旧任务没有该字段时沿用原有全局配置。"""
        return job.get("analysis_engine") or get_settings().app.analysis_engine

    def _inspect_pdf_path(self, path: Path) -> int:
        """打开 PDF 并检查页数，拒绝损坏或受密码保护的文件。"""
        try:
            document = pypdfium2.PdfDocument(str(path))
            try:
                page_count = len(document)
            finally:
                document.close()
        except Exception as exc:
            raise MCPJobError(MCPErrorCode.INVALID_PDF, internal_detail=str(exc)) from exc
        if page_count < 1:
            raise MCPJobError(MCPErrorCode.INVALID_PDF)
        if page_count > self.settings.max_pdf_pages:
            raise MCPJobError(MCPErrorCode.PDF_TOO_MANY_PAGES)
        return page_count

    def _write_upload_atomically(self, path: Path, source: BinaryIO) -> tuple[int, str]:
        """流式写入上传文件，并返回体积和 SHA-256。"""
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(".pdf.uploading")
        digest = hashlib.sha256()
        total = 0
        header = bytearray()
        try:
            with temporary.open("xb") as output:
                while chunk := source.read(_COPY_CHUNK_BYTES):
                    total += len(chunk)
                    if total > self.settings.max_pdf_bytes:
                        raise MCPJobError(MCPErrorCode.PDF_TOO_LARGE)
                    if len(header) < 5:
                        header.extend(chunk[: 5 - len(header)])
                    digest.update(chunk)
                    output.write(chunk)
                if bytes(header) != b"%PDF-":
                    raise MCPJobError(MCPErrorCode.INVALID_PDF)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return total, digest.hexdigest()

    @staticmethod
    def _copy_input_atomically(source: Path, target: Path) -> None:
        """把不可变上传文件复制到任务目录并原子发布。"""
        target.parent.mkdir(parents=True, exist_ok=False)
        temporary = target.with_suffix(".pdf.uploading")
        try:
            with source.open("rb") as input_file, temporary.open("xb") as output:
                shutil.copyfileobj(input_file, output, length=_COPY_CHUNK_BYTES)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _validated_upload_path(self, upload_id: str, raw_path: str) -> Path:
        """确认上传文件位于对应的短期上传目录且不是符号链接。"""
        upload_root = self.input_root / "mcp" / "uploads" / upload_id
        expected = upload_root / f"{upload_id}.pdf"
        path = Path(raw_path)
        if upload_root.is_symlink() or path.is_symlink() or path.resolve() != expected.resolve():
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail="上传文件路径越界")
        if not path.is_file():
            raise MCPJobError(MCPErrorCode.UPLOAD_NOT_FOUND)
        return path

    def _remove_consumed_upload(self, upload_id: str, path: Path) -> None:
        """任务创建后删除上传暂存副本，保留数据库审计元数据。"""
        try:
            self._remove_unpublished_input(path)
            self.repository.mark_upload_file_deleted(upload_id)
        except (OSError, sqlite3.Error):
            logger.exception("AMA MCP 已消费上传文件清理失败: upload_id=%s", upload_id)

    @staticmethod
    def _remove_unpublished_input(path: Path) -> None:
        """删除未成功入队的本次唯一输入文件。"""
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass

    def _validated_result_dir(self, job_id: str, raw_path: str | None) -> Path:
        """确认结果路径位于当前 MCP 作业的输出边界内。"""
        if not raw_path:
            raise MCPJobError(MCPErrorCode.RESULT_INVALID, internal_detail="任务缺少结果目录")
        path = Path(raw_path).resolve()
        allowed_root = (self.output_root / "mcp" / job_id).resolve()
        if not path.is_relative_to(allowed_root):
            raise MCPJobError(MCPErrorCode.RESULT_INVALID, internal_detail="结果目录越界")
        return path

    def _require_job(self, job_id: str) -> dict:
        """读取作业，不存在时返回稳定错误。"""
        try:
            job = self.repository.get_job(job_id)
        except sqlite3.Error as exc:
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc
        if job is None:
            raise MCPJobError(MCPErrorCode.JOB_NOT_FOUND)
        return job

    def _capacity_error(self, *, log_failure: bool = False) -> MCPErrorCode | None:
        """检查任务存储容量并把探测失败转换为稳定异常。"""
        if self.settings.min_free_disk_bytes == 0:
            return None
        try:
            free = available_disk_bytes(self.input_root, self.output_root, self.repository.db_path)
        except OSError:
            if log_failure:
                logger.exception("AMA MCP 存储容量探测失败")
            return MCPErrorCode.STORAGE_ERROR
        if free < self.settings.min_free_disk_bytes:
            if log_failure:
                logger.warning(
                    "AMA MCP 存储空间不足: free_bytes=%s threshold=%s",
                    free,
                    self.settings.min_free_disk_bytes,
                )
            return MCPErrorCode.LOW_DISK_SPACE
        return None

    @staticmethod
    def _stored_error_code(job: dict) -> MCPErrorCode:
        """读取数据库错误码，兼容升级前的失败记录。"""
        try:
            return MCPErrorCode(job.get("last_error_code") or MCPErrorCode.INTERNAL_ERROR)
        except ValueError:
            return MCPErrorCode.INTERNAL_ERROR

    def _status_payload(self, job: dict, *, deduplicated: bool = False) -> dict:
        """构造 MCP 状态响应。"""
        try:
            queue_position = self.repository.queue_position(job)
        except sqlite3.Error as exc:
            raise MCPJobError(MCPErrorCode.STORAGE_ERROR, internal_detail=str(exc)) from exc
        status_error: MCPErrorCode | None = None
        if job["status"] == QUEUED:
            status_error = self._capacity_error()
            message = (
                error_payload(status_error)["message"]
                if status_error is not None
                else f"任务正在排队，当前位置 {queue_position}"
            )
        elif job["status"] == FAILED:
            status_error = self._stored_error_code(job)
            message = error_payload(status_error)["message"]
        elif job["status"] == COMPLETED and job.get("result_expired_at"):
            status_error = MCPErrorCode.RESULT_EXPIRED
            message = error_payload(status_error)["message"]
        else:
            message = f"任务状态：{job['status']}"
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "phase": job["phase"],
            "queue_position": queue_position,
            "progress": {
                "completed": job["progress_completed"],
                "total": job["progress_total"],
            },
            "filename": job["original_filename"],
            "upload_id": job.get("upload_id"),
            "analysis_engine": self._job_analysis_engine(job),
            "page_count": job["page_count"],
            "message": message,
            "error": error_payload(status_error) if status_error is not None else None,
            "result_available": job["status"] == COMPLETED and not job.get("result_expired_at"),
            "deduplicated": deduplicated,
            "created_at": job["created_at"],
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
        }
