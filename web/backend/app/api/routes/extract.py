"""
KG 抽取路由

提供知识图谱抽取的 API 接口
"""
from typing import Optional
from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel

from ...i18n.messages import LocalizedHTTPException

router = APIRouter()


class ExtractRequest(BaseModel):
    """抽取请求模型"""
    # 源文件路径 (已解析的 markdown)
    source_path: Optional[str] = None
    # 或者直接传入文本内容
    content: Optional[str] = None
    # 抽取配置
    config: Optional[dict] = None


class ExtractResponse(BaseModel):
    """抽取响应模型"""
    task_id: str
    status: str
    message: str


class TaskStatus(BaseModel):
    """任务状态模型"""
    task_id: str
    status: str  # pending, running, completed, failed
    progress: float  # 0.0 - 1.0
    result_path: Optional[str] = None
    error: Optional[str] = None


@router.post("/extract", response_model=ExtractResponse)
async def create_extraction_task(request: ExtractRequest):
    """
    创建 KG 抽取任务
    
    接收源文档路径或内容，启动多智能体抽取流程
    """
    # TODO: 实现抽取任务创建逻辑
    # 1. 验证输入
    # 2. 创建任务
    # 3. 启动 Orchestrator
    return ExtractResponse(
        task_id="placeholder-task-id",
        status="pending",
        message="任务已创建，等待处理"
    )


@router.post("/extract/upload", response_model=ExtractResponse)
async def upload_and_extract(file: UploadFile = File(...)):
    """
    上传 PDF 并创建抽取任务
    
    1. 接收 PDF 文件
    2. 调用 MinerU 解析为 Markdown
    3. 启动 KG 抽取流程
    """
    if not file.filename.lower().endswith(".pdf"):
        raise LocalizedHTTPException(400, "error.pdf_only")
    
    # TODO: 实现上传和抽取逻辑
    # 1. 保存文件
    # 2. 调用 MinerU API
    # 3. 创建抽取任务
    return ExtractResponse(
        task_id="placeholder-task-id",
        status="pending",
        message=f"文件 {file.filename} 已上传，等待处理"
    )


@router.get("/extract/{task_id}", response_model=TaskStatus)
async def get_task_status(task_id: str):
    """
    获取抽取任务状态
    """
    # TODO: 实现任务状态查询
    return TaskStatus(
        task_id=task_id,
        status="pending",
        progress=0.0
    )


@router.get("/extract/{task_id}/result")
async def get_task_result(task_id: str):
    """
    获取抽取结果 (knowledge graph)
    """
    # TODO: 实现结果获取
    return {
        "task_id": task_id,
        "entities": [],
        "relations": [],
        "triples": []
    }
