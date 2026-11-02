"""
PDF 文件上传 API
"""
import os
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel

from ...i18n.messages import LocalizedHTTPException

router = APIRouter()


class UploadResponse(BaseModel):
    """上传响应"""
    task_id: str
    filename: str
    file_path: str


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """
    上传 PDF 文件并创建评价任务
    
    - 文件保存到 output/{task_id}/source.pdf
    - 返回 task_id 用于后续对话
    """
    # 验证文件类型
    if not file.filename.endswith('.pdf'):
        raise LocalizedHTTPException(400, "error.pdf_only")
    
    # 生成任务 ID
    timestamp = datetime.now().strftime("%m%d%H%M")
    base_name = Path(file.filename).stem[:20]  # 限制文件名长度
    task_id = f"{base_name}-{timestamp}"
    
    # 创建任务目录
    output_root = Path(os.environ.get("DATA_ROOT", "./output"))
    task_dir = output_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    
    # 保存文件
    file_path = task_dir / "source.pdf"
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    return UploadResponse(
        task_id=task_id,
        filename=file.filename,
        file_path=str(file_path)
    )
