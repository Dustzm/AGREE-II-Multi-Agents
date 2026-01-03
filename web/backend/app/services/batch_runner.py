"""
批量任务运行服务

支持并发执行多个 PDF 评价任务，自动重试失败任务
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Any

from .agent_runner import AppraisalRunner

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class BatchTask:
    """单个批量任务状态"""
    id: str                              # 任务 ID（PDF 文件名不含扩展名）
    pdf_path: str                        # PDF 完整路径
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0                 # 重试次数
    error: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    current_step: Optional[str] = None   # 当前执行步骤
    result_task_id: Optional[str] = None # 执行后生成的 task_id
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "pdf_path": self.pdf_path,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "error": self.error,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "current_step": self.current_step,
            "result_task_id": self.result_task_id,
        }


class BatchRunner:
    """
    批量任务运行器
    
    支持：
    - 并发数控制
    - 失败自动重试（最多一次）
    - 实时状态回调
    """
    
    # 最大重试次数
    MAX_RETRY = 1
    
    def __init__(
        self,
        concurrency: int = 2,
        on_update: Optional[Callable[[BatchTask], None]] = None,
        on_complete: Optional[Callable[[dict], None]] = None,
    ):
        """
        Args:
            concurrency: 并发数，默认 2
            on_update: 任务状态更新回调
            on_complete: 全部任务完成回调
        """
        self.concurrency = concurrency
        self.on_update = on_update or (lambda _: None)
        self.on_complete = on_complete or (lambda _: None)
        
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._tasks: dict[str, BatchTask] = {}
        self._running = False
        self._cancelled = False
        self._current_runners: list[AppraisalRunner] = []
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    def get_status(self) -> dict:
        """获取当前批量任务状态"""
        tasks_list = [t.to_dict() for t in self._tasks.values()]
        
        # 统计各状态数量
        status_counts = {
            "pending": 0,
            "running": 0,
            "done": 0,
            "error": 0,
            "cancelled": 0,
        }
        for task in self._tasks.values():
            status_counts[task.status.value] += 1
        
        return {
            "is_running": self._running,
            "total": len(self._tasks),
            "concurrency": self.concurrency,
            **status_counts,
            "tasks": tasks_list,
        }
    
    async def start(self, pdf_paths: list[str]) -> dict:
        """
        启动批量任务
        
        Args:
            pdf_paths: PDF 文件路径列表
            
        Returns:
            初始状态字典
        """
        if self._running:
            raise RuntimeError("批量任务正在运行中")
        
        self._running = True
        self._cancelled = False
        self._tasks.clear()
        self._current_runners.clear()
        self._semaphore = asyncio.Semaphore(self.concurrency)
        
        # 初始化任务列表
        for pdf_path in pdf_paths:
            pdf_file = Path(pdf_path)
            task_id = pdf_file.stem  # 使用文件名作为任务 ID
            self._tasks[task_id] = BatchTask(
                id=task_id,
                pdf_path=pdf_path,
            )
        
        # 异步启动所有任务
        asyncio.create_task(self._run_all())
        
        return self.get_status()
    
    async def stop(self):
        """停止批量任务"""
        self._cancelled = True
        
        # 取消所有运行中的 runner
        for runner in self._current_runners:
            runner.cancel()
        
        # 将所有 pending 任务标记为 cancelled
        for task in self._tasks.values():
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                self.on_update(task)
        
        self._running = False
        self.on_complete(self.get_status())
    
    async def _run_all(self):
        """运行所有任务"""
        try:
            # 并发执行所有任务
            await asyncio.gather(*[
                self._run_with_semaphore(task)
                for task in self._tasks.values()
            ], return_exceptions=True)
        finally:
            self._running = False
            self.on_complete(self.get_status())
    
    async def _run_with_semaphore(self, task: BatchTask):
        """使用信号量控制并发执行单个任务"""
        if self._cancelled:
            return
        
        async with self._semaphore:
            if self._cancelled:
                return
            await self._run_single_task(task)
    
    async def _run_single_task(self, task: BatchTask, is_retry: bool = False):
        """
        执行单个任务
        
        Args:
            task: 任务对象
            is_retry: 是否为重试
        """
        if self._cancelled:
            return
        
        task.status = TaskStatus.RUNNING
        task.start_time = datetime.now()
        task.current_step = "正在初始化..."
        self.on_update(task)
        
        error_occurred = False
        error_message = ""
        
        def on_status(msg_type: str, content: str):
            task.current_step = content
            self.on_update(task)
        
        def on_agent(agent: str, content: str, progress: int, total: int, prompt: Any = None):
            task.current_step = f"{agent}: {content}"
            self.on_update(task)
        
        def on_complete(task_id: str):
            nonlocal error_occurred
            task.result_task_id = task_id
        
        def on_error(err: str):
            nonlocal error_occurred, error_message
            error_occurred = True
            error_message = err
        
        runner = AppraisalRunner(
            on_status=on_status,
            on_agent=on_agent,
            on_complete=on_complete,
            on_error=on_error,
        )
        self._current_runners.append(runner)
        
        try:
            await runner.run(task.id, task.pdf_path)
        except Exception as e:
            error_occurred = True
            error_message = str(e)
            logger.exception(f"任务 {task.id} 执行异常")
        finally:
            if runner in self._current_runners:
                self._current_runners.remove(runner)
        
        task.end_time = datetime.now()
        
        if self._cancelled:
            task.status = TaskStatus.CANCELLED
            task.current_step = "已取消"
        elif error_occurred:
            # 检查是否需要重试
            if task.retry_count < self.MAX_RETRY:
                task.retry_count += 1
                task.current_step = f"正在重试 ({task.retry_count}/{self.MAX_RETRY})..."
                self.on_update(task)
                logger.info(f"任务 {task.id} 失败，正在重试 ({task.retry_count}/{self.MAX_RETRY})")
                await self._run_single_task(task, is_retry=True)
                return
            else:
                task.status = TaskStatus.ERROR
                task.error = error_message
                task.current_step = f"失败: {error_message[:50]}..."
        else:
            task.status = TaskStatus.DONE
            task.current_step = "已完成"
        
        self.on_update(task)


# 全局单例
_batch_runner: Optional[BatchRunner] = None


def get_batch_runner() -> Optional[BatchRunner]:
    """获取当前批量运行器实例"""
    return _batch_runner


def create_batch_runner(
    concurrency: int = 2,
    on_update: Optional[Callable[[BatchTask], None]] = None,
    on_complete: Optional[Callable[[dict], None]] = None,
) -> BatchRunner:
    """创建新的批量运行器"""
    global _batch_runner
    _batch_runner = BatchRunner(
        concurrency=concurrency,
        on_update=on_update,
        on_complete=on_complete,
    )
    return _batch_runner
