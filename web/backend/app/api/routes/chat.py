"""
WebSocket 对话 API

实现智能体评价任务的实时通信和普通对话
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

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
    type: str  # start, cancel, status, agent, progress, complete, error
    content: Optional[str] = None
    agent: Optional[str] = None
    progress: Optional[int] = None
    total: Optional[int] = None
    task_id: Optional[str] = None
    prompt: Optional[str] = None  # Appraiser 的提示词内容


class ConnectionManager:
    """WebSocket 连接管理器"""
    
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
    
    async def connect(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[session_id] = websocket
    
    def disconnect(self, session_id: str):
        if session_id in self.active_connections:
            del self.active_connections[session_id]
    
    async def send_message(self, session_id: str, message: dict):
        if session_id in self.active_connections:
            await self.active_connections[session_id].send_json(message)


manager = ConnectionManager()


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
                parts=[types.Part(text=request.message)],
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
            response=response_text or "我是 AMA 评价助手，有什么可以帮你的吗？",
            agent="Portal"
        )
    
    except Exception as e:
        logger.warning(f"Portal 调用失败: {e}")
        # 回退到简单响应
        return ChatMessageResponse(
            response="我是 AMA，一个 AGREE-II 指南质量评价系统。你可以上传 PDF 文件开始评价，或者继续和我聊天。",
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
    await manager.connect(session_id, websocket)
    logger.info(f"WebSocket 连接: {session_id}")
    
    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "start":
                task_id = data.get("task_id")
                pdf_path = data.get("pdf_path")
                
                if not task_id:
                    await manager.send_message(session_id, {
                        "type": "error",
                        "content": "缺少 task_id"
                    })
                    continue
                
                # 启动评价任务
                await run_appraisal(session_id, task_id, pdf_path)
            
            elif msg_type == "cancel":
                await manager.send_message(session_id, {
                    "type": "status",
                    "content": "任务已取消"
                })
                break
    
    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: {session_id}")
    finally:
        manager.disconnect(session_id)


async def run_appraisal(session_id: str, task_id: str, pdf_path: str = None):
    """
    运行评价任务
    
    集成真实 Orchestrator
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
                "content": f"PDF 文件不存在: {pdf_file}"
            })
            return
        
        # 发送开始状态
        await manager.send_message(session_id, {
            "type": "status",
            "content": f"开始评价: {pdf_file.name}",
            "task_id": task_id
        })
        
        # 导入并运行真实评价器
        from web.backend.app.services.agent_runner import AppraisalRunner
        
        async def send_msg(msg: dict):
            await manager.send_message(session_id, msg)
        
        runner = AppraisalRunner(
            on_status=lambda t, c: asyncio.get_event_loop().create_task(
                send_msg({"type": t, "content": c})
            ),
            on_agent=lambda a, c, p, t, prompt=None: asyncio.get_event_loop().create_task(
                send_msg({"type": "agent", "agent": a, "content": c, "progress": p, "total": t, "prompt": prompt})
            ),
            on_complete=lambda tid: asyncio.get_event_loop().create_task(
                send_msg({"type": "complete", "content": "评价完成", "task_id": tid})
            ),
            on_error=lambda e: asyncio.get_event_loop().create_task(
                send_msg({"type": "error", "content": e})
            ),
        )
        
        await runner.run(task_id, str(pdf_file))
    
    except Exception as e:
        logger.error(f"评价任务失败: {e}", exc_info=True)
        await manager.send_message(session_id, {
            "type": "error",
            "content": str(e)
        })

