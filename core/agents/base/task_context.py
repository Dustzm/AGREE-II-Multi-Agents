"""
任务上下文管理模块

管理每个抽取任务的目录结构和生命周期
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.config import get_settings

logger = logging.getLogger(__name__)


class TaskContext:
    """
    任务上下文管理器
    
    每个抽取任务都有独立的输出目录，格式为:
    {output_root}/{input_filename}-{MMddhhmm}/
    ├── final_kg.jsonl              # 最终知识图谱
    ├── extracted_triples.jsonl     # 抽取的三元组（验证前）
    └── .ama/                    # 中间文件目录
        ├── source.md               # 源文档副本
        ├── guide_map.md            # 全局地图
        ├── plan.md                 # 抽取计划
        └── buffer/                 # 上下文缓冲区
            ├── section_1.md
            └── section_2.md
    """
    
    def __init__(
        self,
        input_filename: str,
        task_id: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        初始化任务上下文
        
        Args:
            input_filename: 输入文件名 (如 "test.pdf")
            task_id: 可选的任务ID，如果不提供则自动生成
            timestamp: 可选的时间戳，如果不提供则使用当前时间
        """
        settings = get_settings()
        self.output_root = Path(settings.app.data_root)
        
        # 解析文件名（去除扩展名）
        self.input_filename = input_filename
        self.base_name = Path(input_filename).stem
        
        # 生成时间戳
        self.timestamp = timestamp or datetime.now()
        self.time_suffix = self.timestamp.strftime("%m%d%H%M")
        
        # 生成任务ID
        self.task_id = task_id or f"{self.base_name}-{self.time_suffix}"
        
        # 创建目录结构
        self.task_dir = self.output_root / self.task_id
        self.ama_dir = self.task_dir / ".ama"
        self.buffer_dir = self.ama_dir / "buffer"
        
        self._ensure_dirs()
        logger.info(f"任务上下文已创建: {self.task_dir}")
    
    def _ensure_dirs(self):
        """确保所有目录存在"""
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.ama_dir.mkdir(parents=True, exist_ok=True)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
    
    # ============ 路径获取方法 ============
    
    @property
    def source_path(self) -> Path:
        """源文档路径"""
        return self.ama_dir / "source.md"
    
    @property
    def map_path(self) -> Path:
        """全局地图路径"""
        return self.ama_dir / "guide_map.md"
    
    @property
    def plan_path(self) -> Path:
        """抽取计划路径"""
        return self.ama_dir / "plan.md"
    
    @property
    def extracted_path(self) -> Path:
        """抽取结果路径（验证前）"""
        return self.task_dir / "extracted_triples.jsonl"
    
    @property
    def final_kg_path(self) -> Path:
        """最终知识图谱路径"""
        return self.task_dir / "final_kg.jsonl"
    
    def buffer_path(self, section_id: str) -> Path:
        """获取缓冲区中某个章节的路径"""
        return self.buffer_dir / f"{section_id}.md"
    
    # ============ 序列化方法 ============
    
    def to_dict(self) -> dict:
        """转换为字典，用于传递给 Agent"""
        return {
            "task_id": self.task_id,
            "input_filename": self.input_filename,
            "base_name": self.base_name,
            "timestamp": self.timestamp.isoformat(),
            "task_dir": str(self.task_dir),
            "weaver_dir": str(self.ama_dir),
            "paths": {
                "source": str(self.source_path),
                "map": str(self.map_path),
                "plan": str(self.plan_path),
                "extracted": str(self.extracted_path),
                "final_kg": str(self.final_kg_path),
                "buffer_dir": str(self.buffer_dir),
            }
        }
    
    @classmethod
    def from_task_id(cls, task_id: str) -> "TaskContext":
        """
        从已有的任务ID恢复上下文
        
        Args:
            task_id: 任务ID (如 "test-12181110")
            
        Returns:
            TaskContext 实例
        """
        settings = get_settings()
        task_dir = Path(settings.app.data_root) / task_id
        
        if not task_dir.exists():
            raise ValueError(f"任务目录不存在: {task_dir}")
        
        # 从 task_id 解析信息
        parts = task_id.rsplit("-", 1)
        if len(parts) == 2:
            base_name, time_suffix = parts
            # 尝试解析时间
            try:
                timestamp = datetime.strptime(time_suffix, "%m%d%H%M")
                # 补充年份
                timestamp = timestamp.replace(year=datetime.now().year)
            except ValueError:
                timestamp = datetime.now()
        else:
            base_name = task_id
            timestamp = datetime.now()
        
        ctx = cls.__new__(cls)
        ctx.output_root = Path(settings.app.data_root)
        ctx.input_filename = base_name
        ctx.base_name = base_name
        ctx.timestamp = timestamp
        ctx.time_suffix = time_suffix if len(parts) == 2 else timestamp.strftime("%m%d%H%M")
        ctx.task_id = task_id
        ctx.task_dir = task_dir
        ctx.ama_dir = task_dir / ".ama"
        ctx.buffer_dir = ctx.ama_dir / "buffer"
        
        return ctx


def create_task_context(input_filename: str) -> TaskContext:
    """
    创建新的任务上下文
    
    Args:
        input_filename: 输入文件名
        
    Returns:
        TaskContext 实例
    """
    return TaskContext(input_filename)


def get_task_context(task_id: str) -> TaskContext:
    """
    获取已有的任务上下文
    
    Args:
        task_id: 任务ID
        
    Returns:
        TaskContext 实例
    """
    return TaskContext.from_task_id(task_id)
