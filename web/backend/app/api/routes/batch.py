"""
批量任务 API 路由

提供批量执行 PDF 评价任务的接口
支持状态持久化和会话恢复
"""
import asyncio
import json
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
    is_attachment: bool = False  # 是否为附件文件
    attachment_count: int = 0    # 附件数量



# 附件文件名模式（与 files.py 保持一致）
ATTACHMENT_PATTERNS = [
    "附件", "附录", "appendix", "supplement",
    "执行摘要", "摘要", "summary", "ppt"
]


def is_attachment_file(filename: str) -> bool:
    """
    判断文件是否为附件
    
    规则：文件名中包含附件相关关键词
    """
    name_lower = filename.lower()
    stem = Path(filename).stem.lower()
    
    for pattern in ATTACHMENT_PATTERNS:
        if pattern.lower() in stem:
            return True
    return False


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
        logger.debug(f"广播消息到 {len(self.active_connections)} 个连接: {message.get('type')}")
        disconnected = []
        for session_id, ws in self.active_connections.items():
            try:
                await ws.send_json(message)
                logger.debug(f"消息已发送到 {session_id}")
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
    列出指定文件夹下的主 PDF 文件（排除附件）
    
    采用前缀匹配策略检测附件：
    1. 获取文件夹下所有文件
    2. 只有 PDF 文件可以作为主文件
    3. 如果文件 A 是文件 B 的前缀（且符合分隔符规则），则 A 是主文件，B 是其附件
    """
    input_dir = get_input_dir()
    folder_path = input_dir / folder_name
    
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {folder_name}")
    
    if not folder_path.is_dir():
        raise HTTPException(status_code=400, detail=f"不是文件夹: {folder_name}")
    
    # 1. 获取所有文件
    all_files = []
    # 记录所有文件名（小写）以便快速查找
    all_filenames_map = {} 
    
    for file_path in folder_path.iterdir():
        if not file_path.is_file():
            continue
        # 忽略隐藏文件
        if file_path.name.startswith('.'):
            continue
            
        all_files.append(file_path)
        all_filenames_map[file_path.name.lower()] = file_path

    # 2. 识别主文件和附件
    # 我们认为任何 PDF 都可能是主文件
    # 但如果一个 PDF 是另一个 PDF 的附件（例如 "A.pdf" 和 "A_appendix.pdf"），
    # 那么 "A.pdf" 是主文件，"A_appendix.pdf" 是附件。
    
    # 候选主文件列表 (只有 PDF 可以是主文件)
    potential_mains = [f for f in all_files if f.suffix.lower() == '.pdf']
    
    # 按文件名长度排序（短的排前面），这样 "A.pdf" 会在 "A_appendix.pdf" 之前被处理
    potential_mains.sort(key=lambda x: len(x.stem))
    
    main_pdfs = []
    # 记录已被归类为附件的文件路径，避免重复处理
    identified_attachments = set()
    
    # 辅助函数：判断是否为附件关系
    def is_sibling_attachment(main_stem: str, suspect_stem: str) -> bool:
        """
        判断 suspect 是否为 main 的附件
        规则：suspect 以 main_stem 开头，且后续接分隔符或中文
        """
        if not suspect_stem.startswith(main_stem):
            return False
            
        if len(suspect_stem) <= len(main_stem):
            return False
            
        # 检查分隔符
        # 取前缀后的第一个字符
        next_char = suspect_stem[len(main_stem)]
        
        # 允许的分隔符：空格、下划线、连字符
        if next_char in ' _-':
            return True
            
        # 或者是中文字符（通常中文不需要空格分隔，如 "Report附件"）
        if '\u4e00' <= next_char <= '\u9fff':
            return True
            
        return False

    # 3. 构建主文件列表并计算附件数
    # 为了正确处理，我们需要先确定哪些文件绝对是附件
    
    # 建立附件映射：main_path -> list of attachment_paths
    attachments_map = {}
    
    # 遍历所有潜在主文件
    for main_pdf in potential_mains:
        # 如果它已经被识别为别的文件的附件，则跳过
        if main_pdf in identified_attachments:
            continue
            
        main_stem = main_pdf.stem
        my_attachments = []
        
        # 在所有文件中查找它的附件
        for file_path in all_files:
            # 跳过自己
            if file_path == main_pdf:
                continue
                
            # 如果该文件已经是其他文件的附件，是否还允许属于当前文件？
            # 这里的逻辑是：最长前缀匹配优先？还是最短？
            # 简单起见，如果 A.pdf 和 A_v1.pdf 都在，
            # A.pdf 是 A_v1.pdf 的主文件。
            # 现有的 potential_mains 是按长度排序的，所以 A.pdf 会先被处理。
            # 如果 A_v1.pdf 也是 PDF，它稍后会被遍历到，但如果我们把它标记为附件，就跳过它。
            
            # 使用现有关键词判断作为补充 (is_attachment_file 是遗留逻辑，仍然有用)
            keyword_match = is_attachment_file(file_path.name)
            
            # 使用前缀匹配
            prefix_match = is_sibling_attachment(main_stem, file_path.stem)
            
            if prefix_match:
                my_attachments.append(file_path)
                identified_attachments.add(file_path)
            elif keyword_match and not prefix_match:
                # 只有当它不匹配任何前缀时，才回退到纯关键词匹配
                # 这种情况下，它属于哪个主文件？很难说。
                # 现有的 is_attachment_file 逻辑是全局的，并不关联到特定主文件。
                # 在以前的逻辑中，它只是被过滤掉，不显示。
                # 在新逻辑中，我们希望显示附件数量。
                # 如果一个文件只是单纯叫 "附件.pdf"，它属于谁？
                # 这种边缘情况暂不处理，只处理明确归属的。
                pass

        # 记录结果
        attachments_map[main_pdf] = my_attachments
        main_pdfs.append(main_pdf)

    # 4. 生成响应
    results = []
    for pdf_path in main_pdfs:
        stat = pdf_path.stat()
        results.append(PdfInfo(
            name=pdf_path.name,
            path=str(pdf_path),
            size=stat.st_size,
            is_attachment=False,
            attachment_count=len(attachments_map.get(pdf_path, [])),
        ))
    
    # 按文件名排序
    results.sort(key=lambda x: x.name)
    
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
    folder_path = input_dir / request.folder
    
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder}")
    
    pdf_paths = [str(p) for p in folder_path.glob("*.pdf")]
    
    if not pdf_paths:
        raise HTTPException(status_code=400, detail=f"文件夹中没有 PDF 文件: {request.folder}")
    
    # 创建回调函数
    # 注意：使用 task_dict 而不是 task 对象，避免异步延迟导致状态被覆盖
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
    
    # 包装同步回调为异步
    def sync_on_update(task: BatchTask):
        try:
            # 关键修复：在创建异步任务前先序列化为字典（创建快照）
            # 避免异步任务执行时 task 对象已被后续更新覆盖
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
    # 注意：使用 task_dict 而不是 task 对象，避免异步延迟导致状态被覆盖
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
    
    # 包装同步回调为异步
    def sync_on_update(task: BatchTask):
        try:
            # 关键修复：在创建异步任务前先序列化为字典（创建快照）
            # 避免异步任务执行时 task 对象已被后续更新覆盖
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

    # 情况2：内存无任务，尝试从文件恢复
    state_dir = Path("output/batch_state")
    latest_file = state_dir / "latest.json"

    if latest_file.exists():
        try:
            with open(latest_file, 'r', encoding='utf-8') as f:
                latest = json.load(f)
                session_id = latest.get("session_id")

            if session_id:
                state_file = state_dir / f"{session_id}.json"
                if state_file.exists():
                    with open(state_file, 'r', encoding='utf-8') as f:
                        state = json.load(f)

                    # 关键修复：检测并处理服务重启导致中断的任务
                    # 如果任务状态为 running 但服务已重启（内存无 runner），说明任务被中断
                    state_modified = False
                    for task in state["tasks"]:
                        if task["status"] == "running":
                            task["status"] = "error"
                            task["error"] = "服务重启，任务中断"
                            task["current_step"] = "服务重启，任务已中断"
                            task["end_time"] = datetime.now().isoformat()
                            state_modified = True
                            logger.info(f"标记中断任务: {task['id']}")

                    # 更新全局状态
                    if state_modified:
                        state["status"]["running"] = False
                        # 持久化变更到文件
                        try:
                            with open(state_file, 'w', encoding='utf-8') as f:
                                json.dump(state, f, indent=2, ensure_ascii=False)
                            logger.info(f"已更新中断任务状态到文件: {state_file}")
                        except Exception as e:
                            logger.warning(f"更新状态文件失败: {e}")

                    # 返回历史状态
                    status_counts = {
                        "pending": 0,
                        "running": 0,
                        "done": 0,
                        "error": 0,
                        "cancelled": 0,
                    }
                    for task in state["tasks"]:
                        status = task["status"]
                        status_counts[status] = status_counts.get(status, 0) + 1

                    response = BatchStatusResponse(
                        is_running=False,  # 历史状态不标记为运行中
                        total=len(state["tasks"]),
                        concurrency=state["config"]["concurrency"],
                        **status_counts,
                        tasks=state["tasks"],
                    )
                    return response
        except Exception as e:
            logger.warning(f"加载历史状态失败: {e}")

    # 情况3：无任何状态
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


# ===== 状态持久化相关端点 =====

@router.get("/batch/sessions")
async def list_batch_sessions():
    """
    列出所有历史会话

    返回每个会话的基本信息和统计
    """
    state_dir = Path("output/batch_state")
    if not state_dir.exists():
        return {"sessions": []}

    sessions = []
    for state_file in state_dir.glob("batch_*.json"):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)

            # 统计各状态数量
            status_counts = {}
            for task in state["tasks"]:
                status = task["status"]
                status_counts[status] = status_counts.get(status, 0) + 1

            sessions.append({
                "session_id": state["metadata"]["session_id"],
                "created_at": state["metadata"]["created_at"],
                "last_updated": state["metadata"]["last_updated"],
                "total_tasks": len(state["tasks"]),
                "done": status_counts.get("done", 0),
                "error": status_counts.get("error", 0),
                "running": status_counts.get("running", 0),
                "pending": status_counts.get("pending", 0),
                "is_completed": status_counts.get("done", 0) == len(state["tasks"]),
            })
        except Exception as e:
            logger.warning(f"读取会话文件失败 {state_file}: {e}")

    # 按创建时间倒序排列
    sessions.sort(key=lambda x: x["created_at"], reverse=True)
    return {"sessions": sessions}


@router.post("/batch/restore/{session_id}")
async def restore_batch_session(session_id: str):
    """
    恢复指定会话

    将历史会话加载到内存的 BatchRunner 中
    """
    runner = get_batch_runner()
    if not runner:
        raise HTTPException(status_code=400, detail="无活跃批量任务实例")

    # 加载状态
    state = runner._load_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"会话 {session_id} 不存在")

    # 恢复状态
    runner.restore_from_state(state)

    # 通知所有WebSocket连接
    try:
        await batch_manager.broadcast({
            "type": "status",
            "status": runner.get_status(),
        })
    except Exception as e:
        logger.warning(f"广播恢复状态失败: {e}")

    return {
        "message": "会话已恢复",
        "session_id": session_id,
        "status": runner.get_status()
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
