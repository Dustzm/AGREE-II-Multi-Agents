"""
批量任务 API 路由

提供批量执行 PDF 评价任务的接口
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.config import get_settings
from ...services.batch_runner import (
    BatchRunner, BatchTask, TaskStatus,
    get_batch_runner, create_batch_runner
)

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
    """文件夹列表响应"""
    folders: List[FolderInfo]
    input_dir: str


class PdfInfo(BaseModel):
    """PDF 文件信息"""
    name: str
    path: str
    size: int


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


class BatchStatusResponse(BaseModel):
    """批量任务状态响应"""
    is_running: bool
    total: int
    concurrency: int
    pending: int
    running: int
    done: int
    error: int
    cancelled: int
    tasks: List[dict]


# ===== WebSocket 连接管理 =====

class BatchConnectionManager:
    """批量任务 WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
    
    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[session_id] = websocket
        logger.info(f"批量任务 WebSocket 连接: {session_id}")
    
    def disconnect(self, session_id: str):
        if session_id in self.active_connections:
            del self.active_connections[session_id]
            logger.info(f"批量任务 WebSocket 断开: {session_id}")
    
    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        disconnected = []
        for session_id, ws in self.active_connections.items():
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"发送消息失败 ({session_id}): {e}")
                disconnected.append(session_id)
        
        # 清理断开的连接
        for session_id in disconnected:
            self.disconnect(session_id)


batch_manager = BatchConnectionManager()


# ===== 辅助函数 =====

def get_input_dir() -> Path:
    """获取 input 目录路径"""
    return get_settings().app.path_input


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
    
    folders = []
    for folder_path in input_dir.iterdir():
        if folder_path.is_dir() and not folder_path.name.startswith('.'):
            # 统计 PDF 文件数量
            pdf_count = len(list(folder_path.glob("*.pdf")))
            
            # 获取修改时间
            stat = folder_path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            
            folders.append(FolderInfo(
                name=folder_path.name,
                path=str(folder_path),
                pdf_count=pdf_count,
                modified=modified,
            ))
    
    # 按名称排序
    folders.sort(key=lambda x: x.name)
    
    return FolderListResponse(
        folders=folders,
        input_dir=str(input_dir),
    )


@router.get("/batch/folders/{folder_name}/pdfs", response_model=PdfListResponse)
async def list_folder_pdfs(folder_name: str):
    """
    列出指定文件夹下的所有 PDF 文件
    """
    input_dir = get_input_dir()
    folder_path = input_dir / folder_name
    
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {folder_name}")
    
    if not folder_path.is_dir():
        raise HTTPException(status_code=400, detail=f"不是文件夹: {folder_name}")
    
    pdfs = []
    for pdf_path in folder_path.glob("*.pdf"):
        stat = pdf_path.stat()
        pdfs.append(PdfInfo(
            name=pdf_path.name,
            path=str(pdf_path),
            size=stat.st_size,
        ))
    
    # 按文件名排序
    pdfs.sort(key=lambda x: x.name)
    
    return PdfListResponse(
        folder=folder_name,
        pdfs=pdfs,
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
    folder_path = input_dir / request.folder
    
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder}")
    
    pdf_paths = [str(p) for p in folder_path.glob("*.pdf")]
    
    if not pdf_paths:
        raise HTTPException(status_code=400, detail=f"文件夹中没有 PDF 文件: {request.folder}")
    
    # 创建回调函数
    async def on_update(task: BatchTask):
        await batch_manager.broadcast({
            "type": "task_update",
            "task": task.to_dict(),
        })
    
    async def on_complete(status: dict):
        await batch_manager.broadcast({
            "type": "batch_complete",
            "status": status,
        })
    
    # 包装同步回调为异步
    def sync_on_update(task: BatchTask):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(on_update(task))
            else:
                loop.run_until_complete(on_update(task))
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
    
    # 创建回调函数
    async def on_update(task: BatchTask):
        await batch_manager.broadcast({
            "type": "task_update",
            "task": task.to_dict(),
        })
    
    async def on_complete(status: dict):
        await batch_manager.broadcast({
            "type": "batch_complete",
            "status": status,
        })
    
    # 包装同步回调为异步
    def sync_on_update(task: BatchTask):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(on_update(task))
            else:
                loop.run_until_complete(on_update(task))
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
    
    # 创建并启动批量运行器
    runner = create_batch_runner(
        concurrency=request.concurrency,
        on_update=sync_on_update,
        on_complete=sync_on_complete,
    )
    
    status = await runner.start(pdf_paths)
    
    return BatchStatusResponse(**status)


@router.get("/batch/status", response_model=BatchStatusResponse)
async def get_batch_status():
    """
    获取当前批量任务状态
    """
    runner = get_batch_runner()
    
    if not runner:
        return BatchStatusResponse(
            is_running=False,
            total=0,
            concurrency=2,
            pending=0,
            running=0,
            done=0,
            error=0,
            cancelled=0,
            tasks=[],
        )
    
    return BatchStatusResponse(**runner.get_status())


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
    await batch_manager.connect(session_id, websocket)
    
    try:
        # 发送当前状态
        runner = get_batch_runner()
        if runner:
            await websocket.send_json({
                "type": "status",
                "status": runner.get_status(),
            })
        
        # 保持连接
        while True:
            try:
                data = await websocket.receive_json()
                # 处理客户端消息（如果需要）
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except WebSocketDisconnect:
                break
    finally:
        batch_manager.disconnect(session_id)
