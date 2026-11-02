"""
PDF 文件列表、上传和任务管理 API
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel

from core.config import get_settings
from ...services.input_tree import build_input_tree

from ...i18n import t
from ...i18n.messages import descriptor, sanitize_diagnostic, LocalizedHTTPException

router = APIRouter()


class PdfFile(BaseModel):
    """PDF 文件信息"""
    name: str
    path: str
    size: int
    modified: str
    is_attachment: bool = False  # 是否为附件文件
    attachment_count: int = 0


class InputDirectoryNode(BaseModel):
    """递归目录节点。"""

    name: str
    path: str
    modified: str
    children: List["InputDirectoryNode"]
    files: List[PdfFile]
    pdfs: List[PdfFile]
    file_count: int
    pdf_count: int
    selectable_pdf_count: int


class PdfListResponse(BaseModel):
    """PDF 文件列表响应（递归树 + 兼容字段）。"""

    tree: InputDirectoryNode
    folders: List[InputDirectoryNode]
    root_files: List[PdfFile]
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


class UploadedFile(BaseModel):
    """单个上传文件信息"""
    filename: str
    relative_path: str
    absolute_path: str
    size: int


class UploadFolderResponse(BaseModel):
    """文件夹上传响应"""
    total_count: int
    success_count: int
    failed_count: int
    uploaded_files: List[UploadedFile]
    failed_files: List[dict]


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
    
    使用前缀匹配策略检测附件：
    1. 在每个目录（根目录或子文件夹）内独立检测
    2. 如果文件 A 是文件 B 的前缀（且符合分隔符规则），则 B 是 A 的附件
    """
    input_dir = get_input_dir()
    
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)

    tree_payload = build_input_tree(input_dir)
    tree = InputDirectoryNode.parse_obj(tree_payload)

    return PdfListResponse(
        tree=tree,
        folders=tree.children,
        root_files=tree.files,
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
        raise LocalizedHTTPException(400, "error.filename_required")
    
    if not file.filename.lower().endswith('.pdf'):
        raise LocalizedHTTPException(400, "error.pdf_only")
    
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
        raise LocalizedHTTPException(500, "error.save_failed", diagnostic=e)
    
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
        raise LocalizedHTTPException(404, "error.file_not_found", path=pdf_path)
    
    if not pdf_path.suffix.lower() == '.pdf':
        raise LocalizedHTTPException(400, "error.pdf_only")
    
    # 生成任务 ID
    timestamp = datetime.now().strftime("%m%d%H%M%S%f")
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


@router.post("/files/upload-folder", response_model=UploadFolderResponse)
async def upload_folder(files: List[UploadFile] = File(...)):
    """
    批量上传文件夹中的 PDF 文件

    - 保持原有文件夹结构
    - 递归处理子文件夹
    - 自动处理文件名冲突
    - 返回每个文件的上传结果
    """
    input_dir = get_input_dir()

    # 确保目录存在
    if not input_dir.exists():
        input_dir.mkdir(parents=True, exist_ok=True)

    uploaded_files = []
    failed_files = []

    for file in files:
        try:
            # 获取相对路径（从 filename 字段，前端会设置 webkitRelativePath）
            relative_path = file.filename if file.filename else ""

            # 统一路径分隔符为 /
            relative_path = relative_path.replace("\\", "/")

            # 验证文件类型
            if not relative_path.lower().endswith('.pdf'):
                failed_files.append({"filename": relative_path, "error": t("error.pdf_only"), "i18n": descriptor("error.pdf_only")})
                continue

            # 安全检查：防止路径遍历
            if ".." in relative_path or relative_path.startswith("/"):
                failed_files.append({"filename": relative_path, "error": t("error.invalid_path", path=relative_path), "i18n": descriptor("error.invalid_path", path=relative_path)})
                continue

            # 构建目标路径
            file_path = input_dir / relative_path

            # 确保父目录存在
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # 处理文件名冲突（在目标目录中检查）
            final_filename = get_unique_filename(file_path.parent, file_path.name)
            final_path = file_path.parent / final_filename

            # 保存文件
            with open(final_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            # 获取文件大小
            file_size = final_path.stat().st_size

            uploaded_files.append(UploadedFile(
                filename=final_filename,
                relative_path=relative_path,
                absolute_path=str(final_path),
                size=file_size
            ))

        except Exception as e:
            failed_files.append({
                "filename": getattr(file, 'filename', 'unknown'),
                "error": t("error.upload_failed"), "i18n": descriptor("error.upload_failed"), "diagnostic": sanitize_diagnostic(e)
            })

    return UploadFolderResponse(
        total_count=len(files),
        success_count=len(uploaded_files),
        failed_count=len(failed_files),
        uploaded_files=uploaded_files,
        failed_files=failed_files
    )


InputDirectoryNode.update_forward_refs()
