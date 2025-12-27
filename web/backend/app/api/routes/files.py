"""
PDF 文件列表和任务管理 API
"""
import os
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class PdfFile(BaseModel):
    """PDF 文件信息"""
    name: str
    path: str
    size: int
    modified: str


class PdfListResponse(BaseModel):
    """PDF 文件列表响应"""
    files: List[PdfFile]
    input_dir: str


class StartTaskRequest(BaseModel):
    """启动任务请求"""
    pdf_path: str


class StartTaskResponse(BaseModel):
    """启动任务响应"""
    task_id: str
    pdf_name: str
    output_dir: str


from core.config import get_settings


def get_input_dir() -> Path:
    """获取 input 目录路径"""
    return get_settings().app.path_input


def get_output_dir() -> Path:
    """获取 output 目录路径"""
    return get_settings().app.path_output


@router.get("/files/list", response_model=PdfListResponse)
async def list_pdf_files():
    """
    列出 input 目录下的所有 PDF 文件
    """
    input_dir = get_input_dir()
    
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)
    
    files = []
    for pdf_path in input_dir.glob("*.pdf"):
        stat = pdf_path.stat()
        files.append(PdfFile(
            name=pdf_path.name,
            path=str(pdf_path),
            size=stat.st_size,
            modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        ))
    
    # 按修改时间排序
    files.sort(key=lambda x: x.modified, reverse=True)
    
    return PdfListResponse(
        files=files,
        input_dir=str(input_dir)
    )


@router.post("/files/start", response_model=StartTaskResponse)
async def start_task(request: StartTaskRequest):
    """
    启动评价任务
    
    根据选择的 PDF 文件创建任务目录
    """
    pdf_path = Path(request.pdf_path)
    
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {pdf_path}")
    
    if not pdf_path.suffix.lower() == '.pdf':
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")
    
    # 生成任务 ID
    timestamp = datetime.now().strftime("%m%d%H%M")
    base_name = pdf_path.stem[:20]
    task_id = f"{base_name}-{timestamp}"
    
    # 创建输出目录
    output_dir = get_output_dir()
    task_dir = output_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    
    # 创建 .ama 目录
    ama_dir = task_dir / ".ama"
    ama_dir.mkdir(exist_ok=True)
    (ama_dir / "evaluations").mkdir(exist_ok=True)
    
    return StartTaskResponse(
        task_id=task_id,
        pdf_name=pdf_path.name,
        output_dir=str(task_dir)
    )
