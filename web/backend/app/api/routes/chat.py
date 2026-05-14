"""
WebSocket 对话 API

实现智能体评价任务的实时通信和普通对话
"""
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, DefaultDict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.utils.unicode_sanitizer import strip_surrogates
from web.backend.app.auth import AuthenticatedUser, get_authenticated_user_from_websocket, is_auth_enabled
from web.backend.app.i18n import t, get_language
from ...session_registry import register_session_websocket, unregister_session_websocket

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatMessageRequest(BaseModel):
    """普通对话请求"""
    message: str
    session_id: str


class ChatMessageResponse(BaseModel):
    """普通对话响应"""
    response: str
    agent: str = "Portal"


class ChatMessage(BaseModel):
    """WebSocket 消息格式"""
    type: str  # start, cancel, status, agent, progress, complete, cancelled, error
    content: Optional[str] = None
    agent: Optional[str] = None
    progress: Optional[int] = None
    total: Optional[int] = None
    task_id: Optional[str] = None
    prompt: Optional[str] = None  # Appraiser 的提示词内容


@dataclass
class SessionTaskHandle:
    """当前会话中单任务运行句柄。"""

    task_id: str
    runner: Any
    asyncio_task: asyncio.Task[Any]
    state: str = "starting"


class ConnectionManager:
    """WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: DefaultDict[str, set[WebSocket]] = defaultdict(set)
        # 离线消息队列: session_id -> list[message_dict]
        self.message_queues: dict[str, list[dict]] = {}
    
    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[session_id].add(websocket)
        
        # 检查并发送离线消息
        if session_id in self.message_queues:
            queue = self.message_queues[session_id]
            if queue:
                logger.info(f"发送积压消息给 {session_id}: {len(queue)} 条")
                for msg in queue:
                    try:
                        await websocket.send_json(msg)
                    except Exception as e:
                        logger.error(f"发送积压消息失败: {e}")
                        break
                # 清空队列
                self.message_queues[session_id] = []
    
    def disconnect(self, session_id: str, websocket: WebSocket):
        connections = self.active_connections.get(session_id)
        if not connections:
            return
        connections.discard(websocket)
        if not connections:
            self.active_connections.pop(session_id, None)
    
    async def send_message(self, session_id: str, message: dict):
        connections = list(self.active_connections.get(session_id, set()))
        if connections:
            sent = False
            disconnected: list[WebSocket] = []
            for websocket in connections:
                try:
                    await websocket.send_json(message)
                    sent = True
                except Exception as e:
                    logger.warning(f"发送消息失败，准备移除断开的连接: {e}")
                    disconnected.append(websocket)

            for websocket in disconnected:
                self.disconnect(session_id, websocket)

            if not sent:
                self._enqueue_message(session_id, message)
        else:
            # 用户离线，加入队列
            self._enqueue_message(session_id, message)
            
    def _enqueue_message(self, session_id: str, message: dict):
        """将消息加入离线队列"""
        if session_id not in self.message_queues:
            self.message_queues[session_id] = []
        
        # 限制队列大小，防止无限增长
        if len(self.message_queues[session_id]) > 1000:
            self.message_queues[session_id].pop(0)  # 移除最旧的消息
            
        self.message_queues[session_id].append(message)


manager = ConnectionManager()
active_task_handles: dict[str, SessionTaskHandle] = {}


def get_active_task_handle(session_id: str) -> Optional[SessionTaskHandle]:
    handle = active_task_handles.get(session_id)
    if not handle:
        return None

    if handle.asyncio_task.done():
        active_task_handles.pop(session_id, None)
        return None

    return handle


def clear_active_task_handle(session_id: str, asyncio_task: asyncio.Task[Any]):
    handle = active_task_handles.get(session_id)
    if handle and handle.asyncio_task is asyncio_task:
        active_task_handles.pop(session_id, None)


@router.post("/chat/message", response_model=ChatMessageResponse)
async def chat_message(request: ChatMessageRequest):
    """
    普通对话 API - 由 Portal 智能体处理
    
    用于非评价任务的普通聊天
    """
    try:
        # 尝试调用真实的 Portal 智能体
        import sys
        from pathlib import Path
        project_root = Path(__file__).parent.parent.parent.parent.parent.parent
        sys.path.insert(0, str(project_root))
        
        from google.adk import runners
        from google.genai import types
        from core.agents import create_portal_agent
        
        portal = create_portal_agent()
        runner = runners.InMemoryRunner(
            agent=portal,
            app_name='ama_web_portal',
        )
        session = await runner.session_service.create_session(
            app_name='ama_web_portal',
            user_id=request.session_id
        )
        
        response_text = ""
        async for event in runner.run_async(
            user_id=request.session_id,
            session_id=session.id,
            new_message=types.Content(
                parts=[types.Part(text=strip_surrogates(request.message))],
                role="user"
            )
        ):
            if hasattr(event, 'content') and event.content:
                for part in event.content.parts:
                    if hasattr(part, 'text') and part.text:
                        # 过滤思考标签
                        text = part.text
                        if '<think>' not in text and '💭' not in text:
                            response_text = text
        
        return ChatMessageResponse(
            response=response_text or t("chat.default_response", "en"),
            agent="Portal"
        )
    
    except Exception as e:
        logger.exception(
            "Portal 调用失败: session_id=%s message=%s",
            request.session_id,
            strip_surrogates(request.message)[:300],
        )
        # 回退到简单响应
        return ChatMessageResponse(
            response=t("chat.fallback_response", "en"),
            agent="Portal"
        )


@router.websocket("/chat/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    """
    WebSocket 对话端点
    
    消息协议:
    - 客户端发送: {"type": "start", "task_id": "xxx", "pdf_path": "/path/to/file.pdf"}
    - 服务端发送: {"type": "status", "agent": "Initiator", "content": "..."}
    """
    auth_user: AuthenticatedUser | None = None
    auth_session_id = ""
    if is_auth_enabled():
        auth_user = get_authenticated_user_from_websocket(websocket)
        if auth_user is None:
            await websocket.close(code=1008, reason="unauthorized")
            return
        auth_session_id = auth_user.session_id or ""

    await manager.connect(session_id, websocket)
    if auth_session_id:
        await register_session_websocket(auth_session_id, websocket)
    logger.info(f"WebSocket 连接: {session_id}")
    
    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_json()
            if is_auth_enabled():
                current_user = get_authenticated_user_from_websocket(websocket)
                if current_user is None:
                    await websocket.close(code=1008, reason="unauthorized")
                    break
            msg_type = data.get("type")
            
            if msg_type == "ping":
                await manager.send_message(session_id, {
                    "type": "pong",
                })

            elif msg_type == "start":
                task_id = data.get("task_id")
                pdf_path = data.get("pdf_path")
                lang = data.get("lang", "en")  # 默认英语
                
                if not task_id:
                    await manager.send_message(session_id, {
                        "type": "error",
                        "content": t("error.missing_task_id", lang)
                    })
                    continue

                if get_active_task_handle(session_id):
                    await manager.send_message(session_id, {
                        "type": "error",
                        "content": t("error.task_already_running", lang),
                        "task_id": task_id,
                    })
                    continue

                async def send_msg(msg: dict):
                    await manager.send_message(session_id, msg)

                from web.backend.app.services.agent_runner import AppraisalRunner

                runner = AppraisalRunner(
                    on_status=lambda type_, content: asyncio.get_event_loop().create_task(
                        send_msg({"type": type_, "content": content, "task_id": task_id})
                    ),
                    on_agent=lambda a, c, p, total, prompt=None: asyncio.get_event_loop().create_task(
                        send_msg({"type": "agent", "agent": a, "content": c, "progress": p, "total": total, "prompt": prompt, "task_id": task_id})
                    ),
                    on_complete=lambda tid: asyncio.get_event_loop().create_task(
                        send_msg({"type": "complete", "content": t("status.appraisal_complete", lang), "task_id": tid})
                    ),
                    on_cancelled=lambda tid: asyncio.get_event_loop().create_task(
                        send_msg({"type": "cancelled", "content": t("status.task_cancelled", lang), "task_id": tid})
                    ),
                    on_error=lambda e: asyncio.get_event_loop().create_task(
                        send_msg({"type": "error", "content": e, "task_id": task_id})
                    ),
                    lang=lang,
                )
                
                # 启动评价任务 - 使用后台任务而不是阻塞等待
                # 这样即使 WebSocket 断开，任务也会继续运行，产生的消息会进入 Buffer
                loop = asyncio.get_event_loop()
                task = loop.create_task(run_appraisal(session_id, task_id, pdf_path, runner, lang))
                active_task_handles[session_id] = SessionTaskHandle(
                    task_id=task_id,
                    runner=runner,
                    asyncio_task=task,
                    state="starting",
                )
                
                # 添加回调以处理任务自身的异常（避免静默失败）
                def handle_task_result(t):
                    try:
                        t.result()
                    except asyncio.CancelledError:
                        logger.info(f"任务 {task_id} 已取消")
                    except Exception as e:
                        logger.error(f"任务 {task_id} 异常: {e}", exc_info=True)
                        
                task.add_done_callback(handle_task_result)
            
            elif msg_type == "cancel":
                lang = data.get("lang", "en")
                handle = get_active_task_handle(session_id)
                if not handle:
                    await manager.send_message(session_id, {
                        "type": "error",
                        "content": t("error.no_active_task", lang),
                    })
                    continue

                handle.state = "stopping"
                handle.runner.cancel()
                await manager.send_message(session_id, {
                    "type": "status",
                    "content": t("status.task_cancelling", lang),
                    "task_id": handle.task_id,
                })
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: {session_id}")
    finally:
        if auth_session_id:
            await unregister_session_websocket(auth_session_id, websocket)
        manager.disconnect(session_id, websocket)


async def run_appraisal(
    session_id: str,
    task_id: str,
    pdf_path: str | None = None,
    runner: Any | None = None,
    lang: str = "en",
):
    """
    运行评价任务
    
    集成真实 Orchestrator
    
    Args:
        session_id: WebSocket 会话 ID
        task_id: 任务 ID
        pdf_path: PDF 文件路径
        lang: 语言代码 (zh/en)，默认英语
    """
    from pathlib import Path
    
    try:
        # 验证 PDF 路径
        if pdf_path:
            pdf_file = Path(pdf_path)
        else:
            # 回退：从 output 目录查找
            project_root = Path(__file__).parent.parent.parent.parent.parent.parent
            pdf_file = project_root / "output" / task_id / "source.pdf"
        
        if not pdf_file.exists():
            await manager.send_message(session_id, {
                "type": "error",
                "content": t("error.pdf_not_found", lang, path=str(pdf_file))
            })
            return
        
        # 发送开始状态
        await manager.send_message(session_id, {
            "type": "status",
            "content": t("status.starting_appraisal", lang, filename=pdf_file.name),
            "task_id": task_id
        })
        
        if runner is None:
            raise RuntimeError("Appraisal runner is not initialized")

        # 设置并发评价进度回调
        async def send_item_progress(skill_id: str, title: str, status: str, duration: float = None):
            await manager.send_message(session_id, {
                "type": "item_progress",
                "skill_id": skill_id,
                "title": title,
                "item_status": status,
                "duration": duration
            })
        
        async def send_batch_summary(total: int, completed: int, elapsed: float):
            await manager.send_message(session_id, {
                "type": "batch_summary",
                "total": total,
                "completed_count": completed,
                "elapsed": elapsed
            })
        
        runner._send_item_progress = send_item_progress
        runner._send_batch_summary = send_batch_summary

        handle = get_active_task_handle(session_id)
        if handle:
            handle.state = "running"
        
        await runner.run(task_id, str(pdf_file))
    
    except Exception as e:
        logger.error(f"评价任务失败: {e}", exc_info=True)
        await manager.send_message(session_id, {
            "type": "error",
            "content": str(e),
            "task_id": task_id,
        })
    finally:
        current_task = asyncio.current_task()
        if current_task is not None:
            clear_active_task_handle(session_id, current_task)
