"""AMA MCP 的原始 PDF 上传入口。"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.types import ASGIApp

from access import MCPAccessMiddleware
from config import MCPSettings, get_mcp_settings
from errors import MCPErrorCode, MCPJobError, error_payload
from service import MCPJobService

logger = logging.getLogger(__name__)


class UploadResponse(BaseModel):
    """一个已校验 PDF 的短期上传凭证。"""

    upload_id: str
    filename: str
    input_sha256: str
    input_bytes: int
    page_count: int
    created_at: str
    expires_at: str
    message: str


_ERROR_STATUS = {
    MCPErrorCode.PDF_TOO_LARGE: 413,
    MCPErrorCode.LOW_DISK_SPACE: 507,
    MCPErrorCode.STORAGE_ERROR: 503,
}


def create_upload_http_app(
    service: MCPJobService | None = None,
    settings: MCPSettings | None = None,
) -> ASGIApp:
    """创建带统一鉴权和请求体限制的 PDF 上传应用。"""
    upload_service = service or MCPJobService()
    upload_settings = settings or get_mcp_settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/", response_model=UploadResponse, status_code=201)
    async def upload_pdf(
        file: list[UploadFile] | None = File(default=None),
    ) -> dict | JSONResponse:
        """接收单个 multipart PDF，并返回供 MCP 工具使用的 upload_id。"""
        if not file or len(file) != 1:
            for item in file or []:
                await item.close()
            return JSONResponse(
                status_code=400,
                content={"error": error_payload(MCPErrorCode.INVALID_FILE_COUNT)},
            )
        uploaded_file = file[0]
        try:
            return await asyncio.to_thread(
                upload_service.upload,
                uploaded_file.filename or "",
                uploaded_file.file,
            )
        except MCPJobError as exc:
            if exc.internal_detail:
                logger.warning(
                    "AMA MCP PDF 上传失败: code=%s detail=%s",
                    exc.code,
                    exc.internal_detail,
                )
            status = _ERROR_STATUS.get(exc.error_code, 400)
            return JSONResponse(
                status_code=status,
                content={"error": exc.to_payload()},
            )
        except Exception:
            logger.exception("AMA MCP PDF 上传发生未预期异常")
            return JSONResponse(
                status_code=500,
                content={"error": error_payload(MCPErrorCode.INTERNAL_ERROR)},
            )
        finally:
            await uploaded_file.close()

    return MCPAccessMiddleware(app, upload_settings)


upload_http_app = create_upload_http_app()
