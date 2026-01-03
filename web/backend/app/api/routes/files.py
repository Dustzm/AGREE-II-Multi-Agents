"""
PDF 文件列表、上传和任务管理 API
"""
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from core.config import get_settings

router = APIRouter()


class PdfFile(BaseModel):
    """PDF 文件信息"""
    name: str
    path: str
    size: int
    modified: str


class PdfListResponse(BaseModel):
    """PDF 文件列表响应（层级结构）"""
    folders: List[dict]  # [{"name": "en", "path": "...", "files": [...]}]
    root_files: List[PdfFile]  # 根目录下的 PDF
    input_dir: str


class UploadToInputResponse(BaseModel):
    """上传到 input 目录响应"""
    filename: str
    path: str
    size: int


class StartTaskRequest(BaseModel):
    """启动任务请求"""
    pdf_path: str


class StartTaskResponse(BaseModel):
    """启动任务响应"""
    task_id: str
    pdf_name: str
    output_dir: str


def get_input_dir() -> Path:
    """获取 input 目录路径"""
    return get_settings().app.path_input


def get_output_dir() -> Path:
    """获取 output 目录路径"""
    return get_settings().app.path_output


def get_unique_filename(directory: Path, filename: str) -> str:
    """
    获取唯一文件名，如果文件已存在则添加数字后缀
    
    例如: test.pdf -> test_1.pdf -> test_2.pdf
    """
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    
    if not (directory / filename).exists():
        return filename
    
    counter = 1
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        if not (directory / new_name).exists():
            return new_name
        counter += 1


@router.get("/files/list", response_model=PdfListResponse)
async def list_pdf_files():
    """
    列出 input 目录下的所有 PDF 文件（层级结构）
    
    返回格式：
    - folders: 子文件夹及其 PDF 文件
    - root_files: 根目录下的 PDF 文件
    """
    input_dir = get_input_dir()
    
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)
    
    folders = []
    root_files = []
    
    # 遍历 input 目录
    for item in sorted(input_dir.iterdir()):
        if item.is_dir() and not item.name.startswith('.'):
            # 子文件夹
            folder_files = []
            for pdf_path in sorted(item.glob("*.pdf")):
                stat = pdf_path.stat()
                folder_files.append(PdfFile(
                    name=pdf_path.name,
                    path=str(pdf_path),
                    size=stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                ))
            
            if folder_files:  # 只添加有 PDF 的文件夹
                folders.append({
                    "name": item.name,
                    "path": str(item),
                    "files": [f.dict() for f in folder_files]
                })
        
        elif item.is_file() and item.suffix.lower() == '.pdf':
            # 根目录 PDF
            stat = item.stat()
            root_files.append(PdfFile(
                name=item.name,
                path=str(item),
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            ))
    
    return PdfListResponse(
        folders=folders,
        root_files=root_files,
        input_dir=str(input_dir)
    )


@router.post("/files/upload", response_model=UploadToInputResponse)
async def upload_pdf_to_input(file: UploadFile = File(...)):
    """
    上传 PDF 文件到 input 目录
    
    - 验证文件类型必须是 PDF
    - 自动处理文件名冲突（添加数字后缀）
    - 返回上传后的文件信息
    """
    # 验证文件类型
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持 PDF 文件")
    
    input_dir = get_input_dir()
    
    # 确保目录存在
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)
    
    # 获取唯一文件名
    final_filename = get_unique_filename(input_dir, file.filename)
    file_path = input_dir / final_filename
    
    # 保存文件
    try:
        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存文件失败: {str(e)}")
    
    # 获取文件大小
    file_size = file_path.stat().st_size
    
    return UploadToInputResponse(
        filename=final_filename,
        path=str(file_path),
        size=file_size
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
