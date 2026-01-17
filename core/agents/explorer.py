"""
Explorer (附件探索智能体)

负责检测和解析指南文档的附件内容，包括 PDF、Word、Excel 和视频文件。
"""
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from .base import get_model
from core.prompts import load_prompt
from core.attachment import AttachmentProcessor
from core.agents.tools.public_tools import get_task_paths

logger = logging.getLogger(__name__)


# ============================================================================
# 工具函数
# ============================================================================

def detect_attachments(task_id: str) -> Dict[str, Any]:
    """
    检测任务相关的附件文件
    
    Args:
        task_id: 任务 ID
        
    Returns:
        检测结果，包含附件列表
    """
    paths = get_task_paths(task_id)
    if not paths:
        return {"status": "error", "error": f"任务不存在: {task_id}"}
    
    pdf_path = paths.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        return {"status": "error", "error": "PDF 文件不存在"}
    
    from core.attachment import SiblingFileDetector
    detector = SiblingFileDetector()
    attachments = detector.detect(Path(pdf_path))
    
    return {
        "status": "success",
        "count": len(attachments),
        "attachments": [
            {
                "path": str(a.path),
                "name": a.path.name,
                "type": a.type,
            }
            for a in attachments
        ]
    }


async def parse_attachment(task_id: str, file_path: str) -> Dict[str, Any]:
    """
    解析单个附件文件
    
    Args:
        task_id: 任务 ID
        file_path: 附件文件路径
        
    Returns:
        解析结果
    """
    paths = get_task_paths(task_id)
    if not paths:
        return {"status": "error", "error": f"任务不存在: {task_id}"}
    
    processor = AttachmentProcessor()
    results = await processor.process(
        source_path=Path(file_path),
        prep_dir=Path(paths["prep_dir"])
    )
    
    contents = []
    for result in results:
        for content in result.contents:
            contents.append({
                "title": content.title,
                "content": content.content[:500] + "..." if len(content.content) > 500 else content.content,
                "type": content.content_type,
            })
    
    return {
        "status": "success",
        "contents_count": len(contents),
        "contents": contents,
    }


# ============================================================================
# Agent 创建
# ============================================================================

def create_explorer_agent(lang: str = "zh") -> LlmAgent:
    """
    创建 Explorer（附件探索）智能体
    
    Args:
        lang: 提示词语言（zh/en）
        
    Returns:
        LlmAgent 实例
    """
    instruction = load_prompt("explorer", lang)
    
    return LlmAgent(
        name="Explorer",
        model=get_model(),
        description="附件探索智能体 - 检测并解析指南文档的附件内容",
        instruction=instruction,
        tools=[
            FunctionTool(detect_attachments),
            FunctionTool(parse_attachment),
        ],
    )


# ============================================================================
# 快捷函数（供 process_content_list 直接调用）
# ============================================================================

async def run_explorer(pdf_path: str, prep_dir: str) -> List[Dict[str, Any]]:
    """
    运行附件处理，返回附件内容块
    
    供 process_content_list 在预处理流程中直接调用，
    无需通过 Agent 对话形式。
    
    Args:
        pdf_path: 主 PDF 文件路径
        prep_dir: 预处理输出目录
        
    Returns:
        附件内容块列表，每个元素包含:
        - title: 标题
        - content: 内容文本
        - section: 章节标识（如 [附件: 文件名]）
    """
    processor = AttachmentProcessor()
    
    try:
        results = await processor.process(
            source_path=Path(pdf_path),
            prep_dir=Path(prep_dir)
        )
    except Exception as e:
        logger.error(f"附件处理失败: {e}", exc_info=True)
        return []
    
    blocks = []
    for result in results:
        if result.error:
            logger.warning(f"附件处理错误: {result.source_file}, {result.error}")
            continue
        
        for content in result.contents:
            blocks.append({
                "title": content.title,
                "content": content.content,
                "section": f"[附件: {result.source_file.stem}]",
            })
    
    if blocks:
        logger.info(f"Explorer 处理完成: {len(blocks)} 个附件内容块")
    
    return blocks


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    "create_explorer_agent",
    "run_explorer",
    "detect_attachments",
    "parse_attachment",
]
