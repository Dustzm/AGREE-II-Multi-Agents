"""
Agent 输入输出 Schema 定义

使用 Pydantic 定义各个 Agent 的输入 Schema
AgentTool 会自动使用这些 Schema 生成工具参数定义
"""
from pydantic import BaseModel, Field
from typing import Optional


class InitiatorInput(BaseModel):
    """Initiator Agent 输入 Schema"""
    task_id: str = Field(..., description="任务ID，如 'qlx-12181836'")


class ExplorerInput(BaseModel):
    """Explorer Agent 输入 Schema"""
    task_id: str = Field(..., description="任务ID")
    section_id: str = Field(..., description="章节ID，如 'basic_info', 'problem_1'")


class ExtractorInput(BaseModel):
    """Extractor Agent 输入 Schema"""
    task_id: str = Field(..., description="任务ID")
    buffer_id: str = Field(..., description="Buffer ID，对应 Explorer 生成的 buffer 文件名")


class ValidatorInput(BaseModel):
    """Validator Agent 输入 Schema (保留，暂不使用)"""
    task_id: str = Field(..., description="任务ID")


class AppraiserInput(BaseModel):
    """Appraiser Agent 输入 Schema"""
    task_id: str = Field(..., description="任务ID")
    skill_id: str = Field(..., description="条目 Skill ID，如 'item_1', 'item_2'")


class ReporterInput(BaseModel):
    """Reporter Agent 输入 Schema"""
    task_id: str = Field(..., description="任务ID")


class EvaluationOutput(BaseModel):
    """Appraiser 评价输出 Schema（强制 CoT 四步法）"""
    step1_locate: str = Field(..., description="定位的章节名称和位置")
    step2_evidence: list[str] = Field(..., min_length=1, description="原文证据列表，必须逐字引用")
    step3_reasoning: str = Field(..., min_length=50, description="推理过程，分析满足/缺失的标准")
    score: int = Field(..., ge=1, le=7, description="最终评分 1-7")
    reasoning_summary: str = Field(..., description="评分理由简述")


from typing import Literal


class SemanticLabelOutput(BaseModel):
    """语义标签输出 Schema（用于预处理阶段的内容分类）"""
    reason: str = Field(..., description="A brief, objective classification rationale in 1-2 sentences. Focus on key evidence (title, section path, content type) that supports the tag. Do NOT write in first person or describe your thinking process.")
    tag: Literal["Overview", "Methodology", "Clinical", "Independence", "Irrelevant"] = Field(
        ..., 
        description="Semantic tag: Overview (abstract/introduction), Methodology (process/committee), Clinical (main body recommendations), Independence (conflict of interest), Irrelevant (references/TOC)"
    )
