"""
批量任务运行服务

支持并发执行多个 PDF 评价任务，自动重试失败任务
支持状态持久化到文件，服务重启后可恢复
"""
import asyncio
import json
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
    first_round_task_id: Optional[str] = None  # 第一轮生成的 task_id（多轮实验复用）
    token_usage: Optional[dict] = None   # Token 使用统计
    
    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "id": self.id,
            "pdf_path": self.pdf_path,
            "status": self.status.value,
            "retry_count": self.retry_count,
            "error": self.error,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "current_step": self.current_step,
            "result_task_id": self.result_task_id,
            "first_round_task_id": self.first_round_task_id,
        }
        if self.token_usage:
            result["token_usage"] = self.token_usage
        return result



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
        experiment_rounds: int = 1,
        on_update: Optional[Callable[[BatchTask], None]] = None,
        on_complete: Optional[Callable[[dict], None]] = None,
    ):
        """
        Args:
            concurrency: 并发数，默认 2
            experiment_rounds: 实验轮次，默认 1，最大 4
            on_update: 任务状态更新回调
            on_complete: 全部任务完成回调
        """
        self.concurrency = concurrency
        self.experiment_rounds = max(1, min(4, experiment_rounds))  # 限制 1-4
        self.on_update = on_update or (lambda _: None)
        self.on_complete = on_complete or (lambda _: None)

        self._semaphore: Optional[asyncio.Semaphore] = None
        self._tasks: dict[str, BatchTask] = {}
        self._running = False
        self._cancelled = False
        self._current_runners: list[AppraisalRunner] = []
        
        # 轮次相关
        self.current_round: int = 0  # 当前轮次 (1-based)
        self._output_subdir: Optional[str] = None  # 当前轮次的输出子目录

        # 状态持久化相关
        self._session_id: Optional[str] = None
        self._state_file_path: Optional[Path] = None
        self._auto_save_enabled: bool = True
    
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
            "experiment_rounds": self.experiment_rounds,
            "current_round": self.current_round,
            **status_counts,
            "tasks": tasks_list,
        }

    # ===== 状态持久化方法 =====

    def _get_state_dir(self) -> Path:
        """获取状态存储目录"""
        state_dir = Path("output/batch_state")
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    def _generate_session_id(self) -> str:
        """生成会话ID: batch_YYYYMMDD_HHMMSS"""
        return f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _save_state(self):
        """保存当前状态到文件（原子写入）"""
        if not self._session_id or not self._auto_save_enabled:
            return

        try:
            # 计算总 token 消耗
            total_prompt_tokens = 0
            total_response_tokens = 0
            total_tokens = 0
            for task in self._tasks.values():
                if task.token_usage:
                    total_prompt_tokens += task.token_usage.get("prompt_token_count", 0)
                    total_response_tokens += task.token_usage.get("response_token_count", 0)
                    total_tokens += task.token_usage.get("total_token_count", 0)
            
            state = {
                "metadata": {
                    "session_id": self._session_id,
                    "created_at": datetime.now().isoformat(),
                    "last_updated": datetime.now().isoformat(),
                    "version": "1.0"
                },
                "config": {
                    "concurrency": self.concurrency,
                    "max_retry": self.MAX_RETRY
                },
                "status": {
                    "running": self._running,
                    "cancelled": self._cancelled
                },
                "token_usage": {
                    "total_prompt_tokens": total_prompt_tokens,
                    "total_response_tokens": total_response_tokens,
                    "total_tokens": total_tokens
                },
                "tasks": [task.to_dict() for task in self._tasks.values()]
            }


            # 原子写入：先写临时文件，再重命名
            if not self._state_file_path:
                return

            temp_file = self._state_file_path.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

            # 原子重命名
            temp_file.replace(self._state_file_path)

            # 更新 latest.json 指针
            latest_file = self._get_state_dir() / "latest.json"
            with open(latest_file, 'w', encoding='utf-8') as f:
                json.dump({"session_id": self._session_id}, f)

            logger.debug(f"状态已保存: {self._state_file_path}, 任务数: {len(self._tasks)}")
        except Exception as e:
            logger.error(f"保存状态失败: {e}")

    def _load_state(self, session_id: Optional[str] = None) -> Optional[dict]:
        """
        从文件加载状态

        Args:
            session_id: 指定会话ID，如果为None则加载latest.json指向的会话

        Returns:
            状态字典，如果不存在则返回None
        """
        try:
            state_dir = self._get_state_dir()

            # 确定要加载的会话
            if session_id:
                state_file = state_dir / f"{session_id}.json"
            else:
                latest_file = state_dir / "latest.json"
                if not latest_file.exists():
                    return None

                with open(latest_file, 'r', encoding='utf-8') as f:
                    latest = json.load(f)
                    session_id = latest.get("session_id")

                if not session_id:
                    return None

                state_file = state_dir / f"{session_id}.json"

            if not state_file.exists():
                return None

            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)

            logger.info(f"已从文件加载批量任务状态: {state_file}")
            return state
        except Exception as e:
            logger.error(f"加载状态失败: {e}")
            return None

    def restore_from_state(self, state: dict):
        """
        从加载的状态数据恢复 BatchRunner

        Args:
            state: _load_state() 返回的状态字典
        """
        try:
            # 恢复元数据
            self._session_id = state["metadata"]["session_id"]
            state_dir = self._get_state_dir()
            self._state_file_path = state_dir / f"{self._session_id}.json"

            # 恢复配置
            self.concurrency = state["config"]["concurrency"]

            # 恢复状态
            self._running = state["status"]["running"]
            self._cancelled = state["status"]["cancelled"]

            # 恢复任务列表
            self._tasks.clear()
            for task_dict in state["tasks"]:
                task = BatchTask(
                    id=task_dict["id"],
                    pdf_path=task_dict["pdf_path"],
                    status=TaskStatus(task_dict["status"]),
                    retry_count=task_dict["retry_count"],
                    error=task_dict["error"],
                    start_time=datetime.fromisoformat(task_dict["start_time"]) if task_dict["start_time"] else None,
                    end_time=datetime.fromisoformat(task_dict["end_time"]) if task_dict["end_time"] else None,
                    current_step=task_dict["current_step"],
                    result_task_id=task_dict["result_task_id"]
                )

                # 恢复逻辑：运行中的任务需要特殊处理
                if task.status == TaskStatus.RUNNING:
                    # 服务重启导致中断，标记为error
                    task.status = TaskStatus.ERROR
                    task.error = "服务重启，任务中断"
                    task.end_time = datetime.now()
                    task.current_step = "服务重启，任务已中断"

                self._tasks[task.id] = task

            # 重置运行状态（服务重启后不应继续运行）
            self._running = False

            logger.info(f"已恢复批量任务会话: {self._session_id}, 任务数: {len(self._tasks)}")
        except Exception as e:
            logger.error(f"恢复状态失败: {e}")

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

        # 生成会话ID和状态文件路径
        self._session_id = self._generate_session_id()
        self._state_file_path = self._get_state_dir() / f"{self._session_id}.json"

        self._running = True
        self._cancelled = False
        self._tasks.clear()
        self._current_runners.clear()
        self._semaphore = asyncio.Semaphore(self.concurrency)
        
        # 保存 PDF 路径列表用于多轮执行
        self._pdf_paths = pdf_paths

        # 初始化任务列表
        for pdf_path in pdf_paths:
            pdf_file = Path(pdf_path)
            task_id = pdf_file.stem  # 使用文件名作为任务 ID
            self._tasks[task_id] = BatchTask(
                id=task_id,
                pdf_path=pdf_path,
            )

        # 保存初始状态
        self._save_state()

        # 异步启动多轮执行
        asyncio.create_task(self._run_all_rounds())

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

        # 保存停止后的状态
        self._save_state()
    
    async def _run_all_rounds(self):
        """运行所有轮次"""
        try:
            for round_num in range(1, self.experiment_rounds + 1):
                if self._cancelled:
                    break
                
                self.current_round = round_num
                self._output_subdir = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                
                logger.info(f"开始第 {round_num}/{self.experiment_rounds} 轮实验")
                
                # 如果不是第一轮，重置所有任务状态
                if round_num > 1:
                    for task in self._tasks.values():
                        task.status = TaskStatus.PENDING
                        task.error = None
                        task.start_time = None
                        task.end_time = None
                        task.current_step = None
                        task.result_task_id = None
                        task.retry_count = 0
                    
                    # 广播整体状态更新（包含 current_round 信息）
                    self.on_complete(self.get_status())
                
                # 运行本轮所有任务
                await self._run_all()
                
                # 保存轮次完成状态
                self._save_state()
                
        finally:
            self._running = False
            self.current_round = self.experiment_rounds  # 确保显示最终轮次
            self.on_complete(self.get_status())
            self._save_state()
    
    async def _run_all(self):
        """运行当前轮次的所有任务"""
        # 并发执行所有任务
        await asyncio.gather(*[
            self._run_with_semaphore(task)
            for task in self._tasks.values()
        ], return_exceptions=True)
    
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
        self._save_state()  # 保存运行状态
        
        error_occurred = False
        error_message = ""
        
        def on_status(msg_type: str, content: str):
            # 批量模式下只允许预处理阶段的消息通过，过滤 Orchestrator 的文本输出
            # 1. 过滤"语义来源池构建完成"（预处理结束标志，不需要显示）
            if "语义来源池构建完成" in content or "Semantic source pool built" in content:
                return
            # 2. 过滤"启动智能体团队"
            if "启动智能体团队" in content or "Starting agent team" in content:
                return
            # 3. 只允许包含预处理关键词的消息通过（白名单）
            preprocess_keywords = [
                "初始化", "Initializing",
                "解析", "Parsing", "parsed",
                "正在构建", "Building",
                "📁", "📄", "✓",
                "失败", "failed", "异常", "error"  # 错误消息也需要显示
            ]
            if not any(kw in content for kw in preprocess_keywords):
                return
            task.current_step = content
            logger.info(f"任务 {task.id} 状态更新: {content}")
            self.on_update(task)
            self._save_state()  # 保存状态更新

        def on_agent(agent: str, content: str, progress: int, total: int, prompt: Any = None):
            # 批量模式下过滤所有 Orchestrator 消息（只显示 Appraiser 和 Reporter）
            if agent == "Orchestrator":
                return
            task.current_step = f"{agent}: {content}"
            logger.info(f"任务 {task.id} 智能体更新: {agent} - {content}")
            self.on_update(task)
            self._save_state()  # 保存状态更新
        
        def on_complete(task_id: str):
            nonlocal error_occurred
            task.result_task_id = task_id
            # 第一轮时记录 task_id 供后续轮次复用
            if self.current_round == 1:
                task.first_round_task_id = task_id
        
        def on_error(err: str):
            nonlocal error_occurred, error_message
            error_occurred = True
            error_message = err
        
        runner = AppraisalRunner(
            on_status=on_status,
            on_agent=on_agent,
            on_complete=on_complete,
            on_error=on_error,
            output_subdir=self._output_subdir,  # 传递当前轮次的输出子目录
        )
        self._current_runners.append(runner)
        
        try:
            # 第二轮及以后复用第一轮的 task_id
            reuse_task_id = task.first_round_task_id if self.current_round > 1 else None
            await runner.run(task.id, task.pdf_path, reuse_task_id=reuse_task_id)
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
                self._save_state()  # 保存重试状态
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
            # 获取并保存 token 使用统计
            token_usage = runner.get_token_usage()
            if token_usage:
                task.token_usage = {
                    "prompt_token_count": token_usage.get("prompt_token_count", 0),
                    "response_token_count": token_usage.get("response_token_count", 0),
                    "total_token_count": token_usage.get("total_token_count", 0),
                }

        self.on_update(task)
        self._save_state()  # 保存最终状态



# 全局单例
_batch_runner: Optional[BatchRunner] = None


def get_batch_runner() -> Optional[BatchRunner]:
    """获取当前批量运行器实例"""
    return _batch_runner


def create_batch_runner(
    concurrency: int = 2,
    experiment_rounds: int = 1,
    on_update: Optional[Callable[[BatchTask], None]] = None,
    on_complete: Optional[Callable[[dict], None]] = None,
) -> BatchRunner:
    """创建新的批量运行器"""
    global _batch_runner
    _batch_runner = BatchRunner(
        concurrency=concurrency,
        experiment_rounds=experiment_rounds,
        on_update=on_update,
        on_complete=on_complete,
    )
    return _batch_runner
