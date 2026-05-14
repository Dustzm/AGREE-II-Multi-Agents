"""
批量任务 API 路由

提供批量执行 PDF 评价任务的接口
支持状态持久化和会话恢复
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
import logging
from pathlib import Path
from typing import DefaultDict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.config import get_settings
from ...auth import AuthenticatedUser, get_authenticated_user_from_websocket, is_auth_enabled
from ...session_registry import register_session_websocket, unregister_session_websocket
from ...services.batch_runner import (
    BatchTask,
    create_batch_runner,
    get_batch_runner,
    get_latest_resumable_state,
    get_or_create_batch_runner,
    list_batch_session_states,
    load_batch_state,
    state_to_status_payload,
)
from ...services.input_tree import build_input_tree

logger = logging.getLogger(__name__)

router = APIRouter()


# ===== 数据模型 =====

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
    is_attachment: bool = False  # 是否为附件文件
    attachment_count: int = 0    # 附件数量


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
    folder: str           # 文件夹名称
    concurrency: int = 2  # 并发数


class StartSelectedRequest(BaseModel):
    """启动勾选 PDF 批量任务请求"""
    pdf_paths: List[str]  # 勾选的 PDF 绝对路径列表
    concurrency: int = 2  # 并发数
    experiment_rounds: int = 1  # 实验轮次，默认 1，最大 4


class BatchStatusResponse(BaseModel):
    """批量任务状态响应"""
    session_id: Optional[str] = None
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

    concurrency: Optional[int] = None


# ===== WebSocket 连接管理 =====

class BatchConnectionManager:
    """批量任务 WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: DefaultDict[str, set[WebSocket]] = defaultdict(set)
    
    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[session_id].add(websocket)
        logger.info(f"批量任务 WebSocket 连接: {session_id}")
    
    def disconnect(self, session_id: str, websocket: WebSocket):
        connections = self.active_connections.get(session_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self.active_connections.pop(session_id, None)
        logger.info(f"批量任务 WebSocket 断开: {session_id}")
    
    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        connection_count = sum(len(connections) for connections in self.active_connections.values())
        logger.debug(f"广播消息到 {connection_count} 个连接: {message.get('type')}")
        disconnected: list[tuple[str, WebSocket]] = []
        for session_id, connections in list(self.active_connections.items()):
            for websocket in list(connections):
                try:
                    await websocket.send_json(message)
                    logger.debug(f"消息已发送到 {session_id}")
                except Exception as e:
                    logger.warning(f"发送消息失败 ({session_id}): {e}")
                    disconnected.append((session_id, websocket))

        # 清理断开的连接
        for session_id, websocket in disconnected:
            self.disconnect(session_id, websocket)


batch_manager = BatchConnectionManager()


# ===== 辅助函数 =====

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
        session_id=None,
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
        tasks=[],
    )


def build_batch_callbacks():
    """构建批量任务广播回调。"""

    async def on_update_dict(task_dict: dict):
        await batch_manager.broadcast({
            "type": "task_update",
            "task": task_dict,
        })

    async def on_complete(status: dict):
        await batch_manager.broadcast({
            "type": "batch_complete",
            "status": status,
        })

    def sync_on_update(task: BatchTask):
        try:
            task_dict = task.to_dict()
            logger.info(f"准备广播任务更新: {task_dict['id']}, step: {task_dict['current_step']}")
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(on_update_dict(task_dict))
            else:
                loop.run_until_complete(on_update_dict(task_dict))
        except Exception as e:
            logger.warning(f"广播任务更新失败: {e}")

    def sync_on_complete(status: dict):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(on_complete(status))
            else:
                loop.run_until_complete(on_complete(status))
        except Exception as e:
            logger.warning(f"广播批量完成失败: {e}")

    return sync_on_update, sync_on_complete


def get_latest_status_from_storage() -> Optional[BatchStatusResponse]:
    """从磁盘读取最新会话状态。"""
    state = load_batch_state()
    if not state:
        return None
    return BatchStatusResponse(**state_to_status_payload(state, is_running=False))


# ===== API 路由 =====

@router.get("/batch/folders", response_model=FolderListResponse)
async def list_folders():
    """
    列出 input 目录下的所有文件夹
    
    返回每个文件夹的名称、路径、PDF 数量和修改时间
    """
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
    
    return FolderListResponse(
        tree=tree,
        folders=folders,
        input_dir=str(input_dir),
    )


@router.get("/batch/folders/{folder_name:path}/pdfs", response_model=PdfListResponse)
async def list_folder_pdfs(folder_name: str):
    """
    列出指定文件夹下的主 PDF 文件（排除附件）
    
    采用前缀匹配策略检测附件：
    1. 获取文件夹下所有文件
    2. 只有 PDF 文件可以作为主文件
    3. 如果文件 A 是文件 B 的前缀（且符合分隔符规则），则 A 是主文件，B 是其附件
    """
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
    
    return PdfListResponse(
        folder=folder_name,
        pdfs=results,
    )




@router.post("/batch/start", response_model=BatchStatusResponse)
async def start_batch(request: StartBatchRequest):
    """
    启动批量任务
    
    选择指定文件夹，执行其中所有 PDF 的评价任务
    """
    # 检查是否有任务正在运行
    runner = get_batch_runner()
    if runner and runner.is_running:
        raise HTTPException(status_code=400, detail="已有批量任务正在运行")
    
    # 获取文件夹下的所有 PDF
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
    
    sync_on_update, sync_on_complete = build_batch_callbacks()

    # 创建并启动批量运行器
    runner = create_batch_runner(
        concurrency=request.concurrency,
        on_update=sync_on_update,
        on_complete=sync_on_complete,
    )
    
    status = await runner.start(pdf_paths)
    
    return BatchStatusResponse(**status)


@router.post("/batch/start-selected", response_model=BatchStatusResponse)
async def start_selected(request: StartSelectedRequest):
    """
    启动勾选 PDF 列表的批量任务
    
    接受勾选的 PDF 绝对路径列表，执行批量评价
    """
    # 检查是否有任务正在运行
    runner = get_batch_runner()
    if runner and runner.is_running:
        raise HTTPException(status_code=400, detail="已有批量任务正在运行")
    
    # 验证 PDF 路径
    pdf_paths = []
    for path_str in request.pdf_paths:
        pdf_path = Path(path_str)
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail=f"PDF 文件不存在: {path_str}")
        if pdf_path.suffix.lower() != '.pdf':
            raise HTTPException(status_code=400, detail=f"不是 PDF 文件: {path_str}")
        pdf_paths.append(str(pdf_path))
    
    if not pdf_paths:
        raise HTTPException(status_code=400, detail="未选择任何 PDF 文件")
    
    sync_on_update, sync_on_complete = build_batch_callbacks()

    # 创建并启动批量运行器
    runner = create_batch_runner(
        concurrency=request.concurrency,
        experiment_rounds=request.experiment_rounds,
        on_update=sync_on_update,
        on_complete=sync_on_complete,
    )
    
    status = await runner.start(pdf_paths)
    
    return BatchStatusResponse(**status)


@router.get("/batch/status", response_model=BatchStatusResponse)
async def get_batch_status():
    """
    获取当前批量任务状态

    优先返回内存中的活跃任务状态
    如果无活跃任务，尝试从 latest.json 加载历史状态
    """
    runner = get_batch_runner()

    # 情况1：内存中有活跃任务
    if runner and (runner.is_running or len(runner._tasks) > 0):
        return BatchStatusResponse(**runner.get_status())

    latest_status = get_latest_status_from_storage()
    if latest_status:
        return latest_status

    return make_empty_batch_status()


BatchDirectoryNode.update_forward_refs()


@router.get("/batch/resume/latest", response_model=ResumeLatestResponse)
async def get_latest_resumable_batch():
    """获取最新一条可继续的批量会话。"""
    state = get_latest_resumable_state()
    if not state:
        return ResumeLatestResponse(available=False)

    status_payload = state_to_status_payload(state, is_running=False)
    resume_round = state.get("runtime", {}).get("resume_round")
    current_round = state.get("runtime", {}).get("current_round", 0)
    remaining_tasks = status_payload["total"] if resume_round and resume_round > current_round else status_payload["total"] - status_payload["done"]

    return ResumeLatestResponse(
        available=True,
        session_id=state["metadata"]["session_id"],
        last_updated=state["metadata"].get("last_updated"),
        resume_round=resume_round,
        remaining_tasks=remaining_tasks,
        status=BatchStatusResponse(**status_payload),
    )


@router.post("/batch/resume/latest", response_model=BatchStatusResponse)
async def resume_latest_batch(request: ResumeLatestRequest | None = None):
    """恢复并继续执行最新一条未完成的批量任务。"""
    existing_runner = get_batch_runner()
    if existing_runner and existing_runner.is_running:
        raise HTTPException(status_code=400, detail="已有批量任务正在运行")

    state = get_latest_resumable_state()
    if not state:
        raise HTTPException(status_code=404, detail="没有可继续的批量任务")

    sync_on_update, sync_on_complete = build_batch_callbacks()
    config = state.get("config", {})
    concurrency_override = request.concurrency if request and request.concurrency is not None else None
    if concurrency_override is not None and concurrency_override <= 0:
        raise HTTPException(status_code=400, detail="并发数必须大于 0")

    runner = get_or_create_batch_runner(
        concurrency=concurrency_override or config.get("concurrency", 2),
        experiment_rounds=config.get("experiment_rounds", 1),
        on_update=sync_on_update,
        on_complete=sync_on_complete,
    )

    status = await runner.resume_from_state(state, concurrency_override=concurrency_override)

    try:
        await batch_manager.broadcast({
            "type": "status",
            "status": status,
        })
    except Exception as e:
        logger.warning(f"广播续跑状态失败: {e}")

    return BatchStatusResponse(**status)


@router.post("/batch/stop")
async def stop_batch():
    """
    停止当前批量任务
    """
    runner = get_batch_runner()
    
    if not runner or not runner.is_running:
        raise HTTPException(status_code=400, detail="没有正在运行的批量任务")
    
    await runner.stop()
    
    return {"message": "批量任务已停止", "status": runner.get_status()}


@router.websocket("/batch/ws/{session_id}")
async def batch_websocket(websocket: WebSocket, session_id: str):
    """
    批量任务 WebSocket 端点
    
    用于接收实时任务状态更新
    """
    auth_user: AuthenticatedUser | None = None
    auth_session_id = ""
    if is_auth_enabled():
        auth_user = get_authenticated_user_from_websocket(websocket)
        if auth_user is None:
            await websocket.close(code=1008, reason="unauthorized")
            return
        auth_session_id = auth_user.session_id or ""

    await batch_manager.connect(session_id, websocket)
    if auth_session_id:
        await register_session_websocket(auth_session_id, websocket)
    
    try:
        # 发送当前状态
        runner = get_batch_runner()
        if runner:
            await websocket.send_json({
                "type": "status",
                "status": runner.get_status(),
            })
        else:
            latest_status = get_latest_status_from_storage()
            if latest_status:
                await websocket.send_json({
                    "type": "status",
                    "status": latest_status.model_dump() if hasattr(latest_status, "model_dump") else latest_status.dict(),
                })
        
        # 保持连接
        while True:
            try:
                data = await websocket.receive_json()
                if is_auth_enabled():
                    current_user = get_authenticated_user_from_websocket(websocket)
                    if current_user is None:
                        await websocket.close(code=1008, reason="unauthorized")
                        break
                # 处理客户端消息（如果需要）
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
    finally:
        if auth_session_id:
            await unregister_session_websocket(auth_session_id, websocket)
        batch_manager.disconnect(session_id, websocket)


# ===== 状态持久化相关端点 =====

@router.get("/batch/sessions")
async def list_batch_sessions():
    """
    列出所有历史会话

    返回每个会话的基本信息和统计
    """
    sessions = []
    for state in list_batch_session_states():
        status = state_to_status_payload(state, is_running=False)
        sessions.append({
            "session_id": state["metadata"]["session_id"],
            "created_at": state["metadata"]["created_at"],
            "last_updated": state["metadata"]["last_updated"],
            "total_tasks": status["total"],
            "done": status["done"],
            "error": status["error"],
            "running": status["running"],
            "pending": status["pending"],
            "is_completed": state.get("runtime", {}).get("is_completed", False),
        })

    # 按创建时间倒序排列
    sessions.sort(key=lambda x: x["created_at"], reverse=True)
    return {"sessions": sessions}


@router.post("/batch/restore/{session_id}")
async def restore_batch_session(session_id: str):
    """
    查看指定会话的归一化状态
    """
    state = load_batch_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    status = BatchStatusResponse(**state_to_status_payload(state, is_running=False))

    return {
        "message": "会话状态已加载",
        "session_id": session_id,
        "status": status,
    }


@router.delete("/batch/sessions/{session_id}")
async def delete_batch_session(session_id: str):
    """
    删除指定会话的状态文件

    用于清理历史会话数据
    """
    state_dir = Path("output/batch_state")
    state_file = state_dir / f"{session_id}.json"

    if not state_file.exists():
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    try:
        state_file.unlink()
        logger.info(f"已删除会话文件: {state_file}")
        return {"message": f"会话 {session_id} 已删除"}
    except Exception as e:
        logger.error(f"删除会话文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除会话失败: {str(e)}")
