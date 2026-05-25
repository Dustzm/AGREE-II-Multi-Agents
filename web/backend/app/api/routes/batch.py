"""
批量任务 API 路由。

运行态统一来自 SQLite，WebSocket 通过轮询事件表推送结构化更新。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from core.config import get_settings
from ...auth import AuthenticatedUser, get_authenticated_user_from_websocket, is_auth_enabled
from ...session_registry import register_session_websocket, unregister_session_websocket
from ...services.batch_jobs import (
    create_batch_job,
    delete_batch_job,
    get_active_job,
    get_batch_job,
    get_batch_job_status_payload,
    get_last_event_id,
    get_latest_job,
    get_latest_resumable_job,
    list_batch_job_events,
    list_batch_sessions,
    recover_stale_batch_jobs,
    request_stop,
    resume_batch_job,
)
from ...services.input_tree import build_input_tree

logger = logging.getLogger(__name__)

router = APIRouter()


class FolderInfo(BaseModel):
    """文件夹信息"""

    name: str
    path: str
    pdf_count: int
    modified: str


class FolderListResponse(BaseModel):
    """文件夹树响应（包含兼容字段）。"""

    tree: "BatchDirectoryNode"
    folders: List[FolderInfo]
    input_dir: str


class PdfInfo(BaseModel):
    """PDF 文件信息"""

    name: str
    path: str
    size: int
    is_attachment: bool = False
    attachment_count: int = 0


class BatchDirectoryNode(BaseModel):
    """批量页目录树节点。"""

    name: str
    path: str
    modified: str
    children: List["BatchDirectoryNode"]
    pdfs: List[PdfInfo]
    pdf_count: int


class PdfListResponse(BaseModel):
    """PDF 列表响应"""

    folder: str
    pdfs: List[PdfInfo]


class StartBatchRequest(BaseModel):
    """启动批量任务请求"""

    folder: str
    concurrency: int = Field(default=2, ge=1, le=10)


class StartSelectedRequest(BaseModel):
    """启动勾选 PDF 批量任务请求"""

    pdf_paths: List[str] = Field(min_length=1)
    concurrency: int = Field(default=2, ge=1, le=10)
    experiment_rounds: int = Field(default=1, ge=1, le=4)


class BatchStatusResponse(BaseModel):
    """批量任务状态响应"""

    job_id: Optional[str] = None
    session_id: Optional[str] = None
    status: str = "completed"
    status_reason: str = "normal"
    last_error: Optional[str] = None
    is_running: bool
    total: int
    concurrency: int
    experiment_rounds: int = 1
    current_round: int = 0
    pending: int
    running: int
    done: int
    error: int
    cancelled: int
    stopped: int = 0
    stop_requested: bool = False
    tasks: List[dict]


class ResumeLatestResponse(BaseModel):
    """最新可继续会话响应。"""

    available: bool
    session_id: Optional[str] = None
    last_updated: Optional[str] = None
    resume_round: Optional[int] = None
    remaining_tasks: int = 0
    status: Optional[BatchStatusResponse] = None


class ResumeLatestRequest(BaseModel):
    """继续执行最新可恢复批量任务的请求。"""

    concurrency: Optional[int] = Field(default=None, ge=1, le=10)


class BatchConnectionManager:
    """批量任务 WebSocket 连接管理器（兼容测试保留）。"""

    def __init__(self):
        self.active_connections: DefaultDict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[session_id].add(websocket)

    def disconnect(self, session_id: str, websocket: WebSocket):
        connections = self.active_connections.get(session_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self.active_connections.pop(session_id, None)

    async def broadcast(self, message: dict):
        disconnected: list[tuple[str, WebSocket]] = []
        for session_id, connections in list(self.active_connections.items()):
            for websocket in list(connections):
                try:
                    await websocket.send_json(message)
                except Exception:
                    disconnected.append((session_id, websocket))
        for session_id, websocket in disconnected:
            self.disconnect(session_id, websocket)


def get_input_dir() -> Path:
    """获取 input 目录路径"""
    return get_settings().app.path_input


def to_batch_directory_node_payload(node: dict) -> dict:
    """将通用 input 树节点转换为批量页目录节点。"""
    return {
        "name": node["name"],
        "path": node["path"],
        "modified": node["modified"],
        "children": [to_batch_directory_node_payload(child) for child in node["children"]],
        "pdfs": node["pdfs"],
        "pdf_count": node["selectable_pdf_count"],
    }


def make_empty_batch_status() -> BatchStatusResponse:
    """返回空批量状态。"""
    return BatchStatusResponse(
        job_id=None,
        session_id=None,
        status="completed",
        status_reason="normal",
        last_error=None,
        is_running=False,
        total=0,
        concurrency=2,
        experiment_rounds=1,
        current_round=0,
        pending=0,
        running=0,
        done=0,
        error=0,
        cancelled=0,
        stopped=0,
        stop_requested=False,
        tasks=[],
    )


def _status_response(payload: dict) -> BatchStatusResponse:
    return BatchStatusResponse(**payload)


def _latest_status() -> BatchStatusResponse:
    recover_stale_batch_jobs()
    active = get_active_job()
    if active is not None:
        return _status_response(get_batch_job_status_payload(active["job_id"]))
    latest = get_latest_job()
    if latest is not None:
        return _status_response(get_batch_job_status_payload(latest["job_id"]))
    return make_empty_batch_status()


@router.get("/batch/folders", response_model=FolderListResponse)
async def list_folders():
    """列出 input 目录下的所有文件夹。"""
    input_dir = get_input_dir()
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)

    tree_payload = build_input_tree(input_dir)
    tree = BatchDirectoryNode.parse_obj(to_batch_directory_node_payload(tree_payload))
    folders = [
        FolderInfo(
            name=child.name,
            path=child.path,
            pdf_count=child.pdf_count,
            modified=child.modified,
        )
        for child in tree.children
    ]
    return FolderListResponse(tree=tree, folders=folders, input_dir=str(input_dir))


@router.get("/batch/folders/{folder_name:path}/pdfs", response_model=PdfListResponse)
async def list_folder_pdfs(folder_name: str):
    """列出指定文件夹下的主 PDF 文件。"""
    input_dir = get_input_dir()
    folder_path = (input_dir / folder_name).resolve()
    input_dir_resolved = input_dir.resolve()

    if input_dir_resolved not in folder_path.parents and folder_path != input_dir_resolved:
        raise HTTPException(status_code=400, detail=f"非法文件夹路径: {folder_name}")
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {folder_name}")
    if not folder_path.is_dir():
        raise HTTPException(status_code=400, detail=f"不是文件夹: {folder_name}")

    tree_payload = build_input_tree(folder_path)
    results = [PdfInfo.parse_obj(pdf) for pdf in tree_payload["pdfs"]]
    return PdfListResponse(folder=folder_name, pdfs=results)


@router.post("/batch/start", response_model=BatchStatusResponse)
async def start_batch(request: StartBatchRequest):
    """启动整个文件夹下的批量任务。"""
    input_dir = get_input_dir()
    folder_path = (input_dir / request.folder).resolve()
    input_dir_resolved = input_dir.resolve()
    if input_dir_resolved not in folder_path.parents and folder_path != input_dir_resolved:
        raise HTTPException(status_code=400, detail=f"非法文件夹路径: {request.folder}")
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder}")

    tree_payload = build_input_tree(folder_path)
    pdf_paths = [pdf["path"] for pdf in tree_payload["pdfs"]]
    if not pdf_paths:
        raise HTTPException(status_code=400, detail=f"文件夹中没有 PDF 文件: {request.folder}")

    try:
        payload = create_batch_job(pdf_paths, request.concurrency, 1)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status_response(payload)


@router.post("/batch/start-selected", response_model=BatchStatusResponse)
async def start_selected(request: StartSelectedRequest):
    """启动勾选 PDF 的批量任务。"""
    pdf_paths = []
    for path_str in request.pdf_paths:
        pdf_path = Path(path_str)
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail=f"PDF 文件不存在: {path_str}")
        if pdf_path.suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail=f"不是 PDF 文件: {path_str}")
        pdf_paths.append(str(pdf_path))

    try:
        payload = create_batch_job(pdf_paths, request.concurrency, request.experiment_rounds)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status_response(payload)


@router.get("/batch/status", response_model=BatchStatusResponse)
async def get_batch_status():
    """兼容接口：返回当前活跃 job，否则返回最近一个 job。"""
    return _latest_status()


@router.get("/batch/jobs/{job_id}", response_model=BatchStatusResponse)
async def get_batch_job_status(job_id: str):
    """返回指定 job 的完整状态。"""
    recover_stale_batch_jobs()
    job = get_batch_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"批量任务不存在: {job_id}")
    return _status_response(get_batch_job_status_payload(job_id))


@router.get("/batch/jobs/latest-resumable", response_model=ResumeLatestResponse)
async def get_latest_resumable_batch():
    """获取最新一条可继续的批量会话。"""
    recover_stale_batch_jobs()
    job = get_latest_resumable_job()
    if job is None:
        return ResumeLatestResponse(available=False)

    payload = get_batch_job_status_payload(job["job_id"])
    remaining_tasks = payload["total"] if payload["done"] == payload["total"] and job["current_round"] < job["experiment_rounds"] else payload["total"] - payload["done"]
    return ResumeLatestResponse(
        available=True,
        session_id=job["job_id"],
        last_updated=job["updated_at"],
        resume_round=job["current_round"] or 1,
        remaining_tasks=max(remaining_tasks, 0),
        status=_status_response(payload),
    )


@router.get("/batch/resume/latest", response_model=ResumeLatestResponse)
async def compat_get_latest_resumable_batch():
    """兼容旧接口。"""
    return await get_latest_resumable_batch()


@router.post("/batch/jobs/{job_id}/resume", response_model=BatchStatusResponse)
async def resume_batch(job_id: str, request: ResumeLatestRequest | None = None):
    """恢复指定 job。"""
    try:
        payload = resume_batch_job(
            job_id,
            concurrency_override=request.concurrency if request and request.concurrency is not None else None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status_response(payload)


@router.post("/batch/resume/latest", response_model=BatchStatusResponse)
async def resume_latest_batch(request: ResumeLatestRequest | None = None):
    """恢复最新一条可恢复的批量任务。"""
    job = get_latest_resumable_job()
    if job is None:
        raise HTTPException(status_code=404, detail="没有可继续的批量任务")
    return await resume_batch(job["job_id"], request)


@router.post("/batch/jobs/{job_id}/stop", response_model=BatchStatusResponse)
async def stop_batch_job(job_id: str):
    """请求停止指定 job。"""
    try:
        payload = request_stop(job_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _status_response(payload)


@router.post("/batch/stop")
async def stop_batch():
    """兼容接口：停止当前活跃 job。"""
    active = get_active_job()
    if active is None:
        raise HTTPException(status_code=400, detail="没有正在运行的批量任务")
    payload = request_stop(active["job_id"])
    return {"message": "批量任务已停止", "status": payload}


@router.websocket("/batch/ws/{job_id}")
async def batch_websocket(websocket: WebSocket, job_id: str):
    """按 job 订阅批量任务事件。"""
    auth_user: AuthenticatedUser | None = None
    auth_session_id = ""
    if is_auth_enabled():
        auth_user = get_authenticated_user_from_websocket(websocket)
        if auth_user is None:
            await websocket.close(code=1008, reason="unauthorized")
            return
        auth_session_id = auth_user.session_id or ""

    await websocket.accept()
    if auth_session_id:
        await register_session_websocket(auth_session_id, websocket)

    try:
        job = get_batch_job(job_id)
        if job is None:
            await websocket.send_json({"type": "error", "content": f"批量任务不存在: {job_id}"})
            return

        await websocket.send_json({
            "type": "status",
            "status": get_batch_job_status_payload(job_id),
        })
        last_event_id = get_last_event_id(job_id)

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=1.0)
                if is_auth_enabled():
                    current_user = get_authenticated_user_from_websocket(websocket)
                    if current_user is None:
                        await websocket.close(code=1008, reason="unauthorized")
                        break
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                events = list_batch_job_events(job_id, after_id=last_event_id)
                for event in events:
                    payload = event["payload"]
                    if event["event_type"] == "status":
                        await websocket.send_json({"type": "status", "status": payload})
                    elif isinstance(payload, dict):
                        await websocket.send_json(payload)
                    last_event_id = event["id"]
            except WebSocketDisconnect:
                break
    finally:
        if auth_session_id:
            await unregister_session_websocket(auth_session_id, websocket)


@router.get("/batch/sessions")
async def list_all_batch_sessions():
    """列出所有历史会话。"""
    return {"sessions": list_batch_sessions()}


@router.post("/batch/restore/{session_id}")
async def restore_batch_session(session_id: str):
    """查看指定会话状态。"""
    job = get_batch_job(session_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")
    status = _status_response(get_batch_job_status_payload(session_id))
    return {
        "message": "会话状态已加载",
        "session_id": session_id,
        "status": status,
    }


@router.delete("/batch/sessions/{session_id}")
async def remove_batch_session(session_id: str):
    """删除指定会话。"""
    try:
        delete_batch_job(session_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"message": f"会话 {session_id} 已删除"}


BatchDirectoryNode.update_forward_refs()
