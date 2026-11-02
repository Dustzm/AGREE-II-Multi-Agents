"""
公共工具函数

多个智能体共用的工具：
- 任务目录管理
- 源文档读取
"""
import logging
from pathlib import Path
from typing import Optional

from core.config import get_settings

logger = logging.getLogger(__name__)


# 全局任务上下文缓存 (task_id -> paths dict)
_task_contexts: dict[str, dict] = {}


def init_task_directory(
    input_filename: str, 
    task_id: Optional[str] = None,
    output_subdir: Optional[str] = None
) -> dict:
    """
    初始化任务目录结构
    
    为每个任务创建独立的输出目录:
    {output_root}/[output_subdir/]{filename}-{MMddhhmm}/
    ├── source.md
    ├── appraisal_report.json
    ├── appraisal_report.md
    └── .ama/
        ├── plan.md
        ├── prep/
        └── evaluations/
    
    Args:
        input_filename: 输入文件名 (如 "test.pdf")
        task_id: 可选的任务ID，不提供则自动生成
        output_subdir: 可选的输出子目录 (如 "batch_1", "batch_2")
        
    Returns:
        dict: {"status": "success/error", "task_id": str, "paths": dict, "error": str}
    """
    from datetime import datetime
    
    try:
        settings = get_settings()
        output_root = Path(settings.app.data_root)
        
        # 如果指定了子目录，则使用 output/batch_N/ 作为根目录
        if output_subdir:
            output_root = output_root / output_subdir
            output_root.mkdir(parents=True, exist_ok=True)
        
        # 解析文件名
        base_name = Path(input_filename).stem
        
        # 生成任务ID
        time_suffix = datetime.now().strftime("%m%d%H%M")
        generated_task_id = task_id or f"{base_name}-{time_suffix}"
        if Path(generated_task_id).name != generated_task_id or generated_task_id in {".", ".."}:
            raise ValueError("无效任务 ID")
        
        # 创建目录结构
        task_dir = output_root / generated_task_id
        weaver_dir = task_dir / ".ama"
        prep_dir = weaver_dir / "prep"  # MinerU 预处理中间文件目录
        eval_dir = weaver_dir / "evaluations"  # 评价结果目录
        
        task_dir.mkdir(parents=True, exist_ok=True)
        weaver_dir.mkdir(parents=True, exist_ok=True)
        prep_dir.mkdir(parents=True, exist_ok=True)
        eval_dir.mkdir(parents=True, exist_ok=True)
        
        # 缓存路径信息
        paths = {
            "task_dir": str(task_dir),
            "weaver_dir": str(weaver_dir),
            "prep_dir": str(prep_dir),
            "eval_dir": str(eval_dir),
            "source": str(task_dir / "source.md"),
            "plan": str(weaver_dir / "plan.md"),
        }
        
        _task_contexts[generated_task_id] = paths
        
        logger.info(f"任务目录已创建: {task_dir}")
        return {
            "status": "success",
            "task_id": generated_task_id,
            "paths": paths,
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "task_id": "",
            "paths": {},
            "error": str(e)
        }


def get_task_paths(task_id: str) -> dict:
    """
    获取任务的路径信息
    
    Args:
        task_id: 任务ID
        
    Returns:
        dict: 路径字典，失败时返回空字典
    """
    if Path(task_id).name != task_id or task_id in {".", ".."}:
        return {}
    if task_id in _task_contexts:
        return _task_contexts[task_id]
    
    # 尝试从文件系统恢复
    settings = get_settings()
    data_root = Path(settings.app.data_root)
    
    # 首先在根目录查找
    task_dir = data_root / task_id
    
    # 如果根目录不存在，搜索 batch_N 子目录
    if not task_dir.exists():
        for subdir in data_root.iterdir():
            if subdir.is_dir() and subdir.name.startswith("batch_"):
                candidate = subdir / task_id
                if candidate.exists():
                    task_dir = candidate
                    break
    
    if task_dir.exists():
        weaver_dir = task_dir / ".ama"
        prep_dir = weaver_dir / "prep"
        eval_dir = weaver_dir / "evaluations"
        
        paths = {
            "task_dir": str(task_dir),
            "weaver_dir": str(weaver_dir),
            "prep_dir": str(prep_dir),
            "eval_dir": str(eval_dir),
            "source": str(task_dir / "source.md"),
            "plan": str(weaver_dir / "plan.md"),
        }
        _task_contexts[task_id] = paths
        return paths
    
    return {}



def read_source_document(task_id: str) -> dict:
    """
    读取任务目录下已解析的源文档内容
    
    源文档路径固定为: output/[task_id]/source.md
    
    Args:
        task_id: 任务ID (如 "qlx-12181836")
        
    Returns:
        dict: {"status": "success/error", "content": str, "path": str, "error": str}
    """
    try:
        paths = get_task_paths(task_id)
        if not paths:
            return {
                "status": "error",
                "content": "",
                "path": "",
                "error": f"任务不存在: {task_id}"
            }
        
        source_path = Path(paths["source"])
        if not source_path.exists():
            return {
                "status": "error",
                "content": "",
                "path": str(source_path),
                "error": f"源文档尚未解析，请先使用 PDF 解析器处理文档"
            }
        
        # 读取内容
        content = source_path.read_text(encoding="utf-8")
        
        # 截断过长的内容（防止 Initiator 爆显存/上下文）
        # 尤其是当 source.md 包含大量表格或参考文献时
        MAX_CHARS = 50000 
        if len(content) > MAX_CHARS:
            content = content[:MAX_CHARS] + "\n\n... (Content truncated due to length limit) ..."
            logger.warning(f"源文档过长 ({len(content)} 字符)，已截断为 {MAX_CHARS} 字符")
        
        logger.info(f"读取源文档: {source_path} ({len(content)} 字符)")
        return {
            "status": "success",
            "content": content,
            "path": str(source_path),
            "error": ""
        }
    except Exception as e:
        return {
            "status": "error",
            "content": "",
            "path": "",
            "error": str(e)
        }
