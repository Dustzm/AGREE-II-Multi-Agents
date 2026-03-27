"""
批量任务运行服务

支持并发执行多个 PDF 评价任务、状态持久化，以及服务中断后的续跑。
"""
import asyncio
import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from .agent_runner import AppraisalRunner

logger = logging.getLogger(__name__)

STATE_VERSION = "2.0"
INTERRUPTED_ERROR = "服务重启或中断，等待继续"
INTERRUPTED_STEP = "服务中断，等待继续"


class TaskStatus(str, Enum):
    """任务状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class BatchTask:
    """单个批量任务状态。"""

    id: str
    pdf_path: str
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    error: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    current_step: Optional[str] = None
    result_task_id: Optional[str] = None
    first_round_task_id: Optional[str] = None
    token_usage: Optional[dict] = None

    def to_dict(self) -> dict:
        """转换为字典。"""
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


def get_batch_state_dir() -> Path:
    """获取状态存储目录。"""
    state_dir = Path("output/batch_state")
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _default_status_counts() -> dict:
    return {
        "pending": 0,
        "running": 0,
        "done": 0,
        "error": 0,
        "cancelled": 0,
    }


def load_batch_state(session_id: Optional[str] = None) -> Optional[dict]:
    """
    从磁盘加载批量任务状态。

    Args:
        session_id: 指定会话 ID；为空时读取 latest.json 指向的会话。
    """
    try:
        state_dir = get_batch_state_dir()
        if session_id:
            state_file = state_dir / f"{session_id}.json"
        else:
            latest_file = state_dir / "latest.json"
            if not latest_file.exists():
                return None

            with open(latest_file, "r", encoding="utf-8") as f:
                latest = json.load(f)
            session_id = latest.get("session_id")
            if not session_id:
                return None

            state_file = state_dir / f"{session_id}.json"

        if not state_file.exists():
            return None

        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error(f"加载状态失败: {exc}")
        return None


def determine_resume_round(state: dict) -> Optional[int]:
    """根据当前状态判断续跑应从哪一轮开始。"""
    config = state.get("config", {})
    runtime = state.get("runtime", {})
    tasks = state.get("tasks", [])

    experiment_rounds = max(1, int(config.get("experiment_rounds", 1)))
    current_round = max(0, int(runtime.get("current_round", 0) or 0))
    current_round = min(current_round, experiment_rounds)

    if not tasks:
        return None

    all_done = all(task.get("status") == TaskStatus.DONE.value for task in tasks)
    if current_round == 0:
        return 1

    if all_done:
        if current_round >= experiment_rounds:
            return None
        return current_round + 1

    return current_round


def normalize_batch_state(state: dict, mark_interrupted: bool = True) -> dict:
    """
    归一化历史状态，兼容旧版本状态文件，并把中断任务调整为可继续状态。
    """
    normalized = copy.deepcopy(state)

    metadata = normalized.setdefault("metadata", {})
    metadata.setdefault("session_id", None)
    metadata.setdefault("created_at", datetime.now().isoformat())
    metadata.setdefault("last_updated", metadata["created_at"])
    metadata["version"] = metadata.get("version", STATE_VERSION)

    config = normalized.setdefault("config", {})
    config["concurrency"] = config.get("concurrency", 2)
    config["max_retry"] = config.get("max_retry", BatchRunner.MAX_RETRY)
    config["experiment_rounds"] = max(1, min(4, config.get("experiment_rounds", 1)))

    runtime = normalized.setdefault("runtime", {})
    tasks = normalized.setdefault("tasks", [])
    runtime["pdf_paths"] = runtime.get("pdf_paths") or [
        task.get("pdf_path")
        for task in tasks
        if task.get("pdf_path")
    ]

    current_round = runtime.get("current_round", normalized.get("current_round", 0)) or 0
    current_round = max(0, min(config["experiment_rounds"], current_round))
    runtime["current_round"] = current_round

    round_output_dirs = runtime.get("round_output_dirs") or {}
    runtime["round_output_dirs"] = {
        str(round_no): path
        for round_no, path in round_output_dirs.items()
        if path
    }

    output_subdir = runtime.get("output_subdir")
    if not output_subdir and current_round > 0:
        output_subdir = runtime["round_output_dirs"].get(str(current_round))
    runtime["output_subdir"] = output_subdir

    status = normalized.setdefault("status", {})
    status["running"] = bool(status.get("running", False))
    status["cancelled"] = bool(status.get("cancelled", False))

    has_interrupted_task = False
    for task in tasks:
        task_status = task.get("status", TaskStatus.PENDING.value)
        if task_status == TaskStatus.RUNNING.value and mark_interrupted:
            task["status"] = TaskStatus.PENDING.value
            task["error"] = task.get("error") or INTERRUPTED_ERROR
            task["current_step"] = INTERRUPTED_STEP
            task["end_time"] = task.get("end_time") or datetime.now().isoformat()
            has_interrupted_task = True

    is_current_round_complete = bool(tasks) and all(
        task.get("status") == TaskStatus.DONE.value
        for task in tasks
    )
    is_completed = (
        bool(tasks)
        and current_round >= config["experiment_rounds"]
        and is_current_round_complete
    )

    if is_completed or has_interrupted_task:
        status["running"] = False

    runtime["is_completed"] = is_completed
    runtime["is_current_round_complete"] = is_current_round_complete
    runtime["resume_round"] = determine_resume_round(normalized)
    runtime["is_resumable"] = runtime["resume_round"] is not None

    return normalized


def state_to_status_payload(state: dict, is_running: Optional[bool] = None) -> dict:
    """把状态文件转换为接口返回结构。"""
    normalized = normalize_batch_state(state)
    tasks = normalized.get("tasks", [])
    status_counts = _default_status_counts()
    for task in tasks:
        task_status = task.get("status", TaskStatus.PENDING.value)
        if task_status in status_counts:
            status_counts[task_status] += 1

    return {
        "session_id": normalized.get("metadata", {}).get("session_id"),
        "is_running": normalized.get("status", {}).get("running", False) if is_running is None else is_running,
        "total": len(tasks),
        "concurrency": normalized.get("config", {}).get("concurrency", 2),
        "experiment_rounds": normalized.get("config", {}).get("experiment_rounds", 1),
        "current_round": normalized.get("runtime", {}).get("current_round", 0),
        **status_counts,
        "tasks": tasks,
    }


def list_batch_session_states() -> list[dict]:
    """按时间倒序列出所有会话状态。"""
    states: list[dict] = []
    state_dir = get_batch_state_dir()
    for state_file in sorted(state_dir.glob("batch_*.json"), reverse=True):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                states.append(normalize_batch_state(json.load(f)))
        except Exception as exc:
            logger.warning(f"读取会话文件失败 {state_file}: {exc}")
    return states


def get_latest_resumable_state() -> Optional[dict]:
    """获取最新一条可续跑的会话。"""
    for state in list_batch_session_states():
        if state.get("runtime", {}).get("is_resumable"):
            return state
    return None


class BatchRunner:
    """
    批量任务运行器。

    支持：
    - 并发数控制
    - 失败自动重试（最多一次）
    - 状态持久化
    - 服务中断后继续执行
    """

    MAX_RETRY = 1

    def __init__(
        self,
        concurrency: int = 2,
        experiment_rounds: int = 1,
        on_update: Optional[Callable[[BatchTask], None]] = None,
        on_complete: Optional[Callable[[dict], None]] = None,
    ):
        self.concurrency = concurrency
        self.experiment_rounds = max(1, min(4, experiment_rounds))
        self.on_update = on_update or (lambda _: None)
        self.on_complete = on_complete or (lambda _: None)

        self._semaphore: Optional[asyncio.Semaphore] = None
        self._tasks: dict[str, BatchTask] = {}
        self._running = False
        self._cancelled = False
        self._current_runners: list[AppraisalRunner] = []
        self._background_task: Optional[asyncio.Task] = None

        self.current_round: int = 0
        self._output_subdir: Optional[str] = None
        self._round_output_dirs: dict[str, str] = {}
        self._pdf_paths: list[str] = []
        self._created_at: Optional[str] = None

        self._session_id: Optional[str] = None
        self._state_file_path: Optional[Path] = None
        self._auto_save_enabled: bool = True

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        """获取当前批量任务状态。"""
        tasks_list = [t.to_dict() for t in self._tasks.values()]
        status_counts = _default_status_counts()
        for task in self._tasks.values():
            status_counts[task.status.value] += 1

        return {
            "session_id": self._session_id,
            "is_running": self._running,
            "total": len(self._tasks),
            "concurrency": self.concurrency,
            "experiment_rounds": self.experiment_rounds,
            "current_round": self.current_round,
            **status_counts,
            "tasks": tasks_list,
        }

    def _get_state_dir(self) -> Path:
        return get_batch_state_dir()

    def _generate_session_id(self) -> str:
        return f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def _save_state(self):
        """保存当前状态到文件（原子写入）。"""
        if not self._session_id or not self._auto_save_enabled:
            return

        try:
            total_prompt_tokens = 0
            total_response_tokens = 0
            total_tokens = 0
            for task in self._tasks.values():
                if task.token_usage:
                    total_prompt_tokens += task.token_usage.get("prompt_token_count", 0)
                    total_response_tokens += task.token_usage.get("response_token_count", 0)
                    total_tokens += task.token_usage.get("total_token_count", 0)

            now = datetime.now().isoformat()
            state = {
                "metadata": {
                    "session_id": self._session_id,
                    "created_at": self._created_at or now,
                    "last_updated": now,
                    "version": STATE_VERSION,
                },
                "config": {
                    "concurrency": self.concurrency,
                    "max_retry": self.MAX_RETRY,
                    "experiment_rounds": self.experiment_rounds,
                },
                "runtime": {
                    "current_round": self.current_round,
                    "output_subdir": self._output_subdir,
                    "round_output_dirs": self._round_output_dirs,
                    "pdf_paths": self._pdf_paths,
                },
                "status": {
                    "running": self._running,
                    "cancelled": self._cancelled,
                },
                "token_usage": {
                    "total_prompt_tokens": total_prompt_tokens,
                    "total_response_tokens": total_response_tokens,
                    "total_tokens": total_tokens,
                },
                "tasks": [task.to_dict() for task in self._tasks.values()],
            }

            if not self._state_file_path:
                return

            temp_file = self._state_file_path.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

            temp_file.replace(self._state_file_path)

            latest_file = self._get_state_dir() / "latest.json"
            with open(latest_file, "w", encoding="utf-8") as f:
                json.dump({"session_id": self._session_id}, f)

            logger.debug(f"状态已保存: {self._state_file_path}, 任务数: {len(self._tasks)}")
        except Exception as exc:
            logger.error(f"保存状态失败: {exc}")

    def _load_state(self, session_id: Optional[str] = None) -> Optional[dict]:
        state = load_batch_state(session_id)
        if state:
            logger.info(f"已从文件加载批量任务状态: {state.get('metadata', {}).get('session_id')}")
        return state

    def restore_from_state(self, state: dict):
        """把历史状态恢复到内存中的 BatchRunner。"""
        try:
            normalized = normalize_batch_state(state)

            self._session_id = normalized["metadata"]["session_id"]
            self._created_at = normalized["metadata"].get("created_at")
            self._state_file_path = self._get_state_dir() / f"{self._session_id}.json"

            self.concurrency = normalized["config"]["concurrency"]
            self.experiment_rounds = normalized["config"]["experiment_rounds"]

            runtime = normalized.get("runtime", {})
            self.current_round = runtime.get("current_round", 0)
            self._output_subdir = runtime.get("output_subdir")
            self._round_output_dirs = dict(runtime.get("round_output_dirs", {}))
            self._pdf_paths = list(runtime.get("pdf_paths", []))

            self._running = False
            self._cancelled = normalized["status"]["cancelled"]
            self._current_runners.clear()
            self._background_task = None
            self._semaphore = asyncio.Semaphore(self.concurrency)

            self._tasks.clear()
            for task_dict in normalized["tasks"]:
                task = BatchTask(
                    id=task_dict["id"],
                    pdf_path=task_dict["pdf_path"],
                    status=TaskStatus(task_dict["status"]),
                    retry_count=task_dict["retry_count"],
                    error=task_dict["error"],
                    start_time=datetime.fromisoformat(task_dict["start_time"]) if task_dict["start_time"] else None,
                    end_time=datetime.fromisoformat(task_dict["end_time"]) if task_dict["end_time"] else None,
                    current_step=task_dict["current_step"],
                    result_task_id=task_dict["result_task_id"],
                    first_round_task_id=task_dict.get("first_round_task_id"),
                    token_usage=task_dict.get("token_usage"),
                )
                self._tasks[task.id] = task

            logger.info(f"已恢复批量任务会话: {self._session_id}, 任务数: {len(self._tasks)}")
        except Exception as exc:
            logger.error(f"恢复状态失败: {exc}")

    async def start(self, pdf_paths: list[str]) -> dict:
        """启动批量任务。"""
        if self._running:
            raise RuntimeError("批量任务正在运行中")

        self._session_id = self._generate_session_id()
        self._state_file_path = self._get_state_dir() / f"{self._session_id}.json"
        self._created_at = datetime.now().isoformat()

        self._running = True
        self._cancelled = False
        self._tasks.clear()
        self._current_runners.clear()
        self._background_task = None
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self.current_round = 0
        self._output_subdir = None
        self._round_output_dirs = {}
        self._pdf_paths = list(pdf_paths)

        for pdf_path in pdf_paths:
            pdf_file = Path(pdf_path)
            task_id = pdf_file.stem
            self._tasks[task_id] = BatchTask(id=task_id, pdf_path=pdf_path)

        self._save_state()

        self._background_task = asyncio.create_task(self._run_all_rounds())
        self._background_task.add_done_callback(self._handle_background_task_done)

        return self.get_status()

    async def resume_from_state(self, state: dict, concurrency_override: Optional[int] = None) -> dict:
        """从历史状态恢复并继续执行。"""
        if self._running:
            raise RuntimeError("批量任务正在运行中")

        normalized = normalize_batch_state(state)
        resume_round = normalized.get("runtime", {}).get("resume_round")
        if resume_round is None:
            raise RuntimeError("没有可继续的批量任务")

        self.restore_from_state(normalized)
        if concurrency_override is not None:
            self.concurrency = concurrency_override
        self._running = True
        self._cancelled = False
        self._semaphore = asyncio.Semaphore(self.concurrency)

        reuse_current_round = resume_round == self.current_round and self.current_round > 0
        if reuse_current_round:
            self._prepare_tasks_for_resume()
        else:
            self.current_round = resume_round
            self._output_subdir = None

        self._save_state()
        self._background_task = asyncio.create_task(
            self._run_all_rounds(
                start_round=resume_round,
                reuse_current_round_progress=reuse_current_round,
            )
        )
        self._background_task.add_done_callback(self._handle_background_task_done)

        return self.get_status()

    def _handle_background_task_done(self, task: asyncio.Task):
        """记录后台批量任务的结束状态，避免静默停工。"""
        try:
            task.result()
        except asyncio.CancelledError:
            logger.info("批量后台任务已取消")
        except Exception:
            logger.exception("批量后台任务异常结束")

    async def stop(self):
        """停止批量任务。"""
        self._cancelled = True

        for runner in self._current_runners:
            runner.cancel()

        if self._background_task and not self._background_task.done():
            self._background_task.cancel()

        for task in self._tasks.values():
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                self.on_update(task)

        self._running = False
        self.on_complete(self.get_status())
        self._save_state()

    def _prepare_tasks_for_resume(self):
        """当前轮续跑前，把未完成任务重新置为 pending。"""
        for task in self._tasks.values():
            if task.status == TaskStatus.DONE:
                continue
            task.status = TaskStatus.PENDING
            task.error = None
            task.start_time = None
            task.end_time = None
            task.current_step = INTERRUPTED_STEP
            task.result_task_id = None
            task.retry_count = 0

    def _reset_tasks_for_new_round(self):
        """进入新一轮前重置所有任务。"""
        for task in self._tasks.values():
            task.status = TaskStatus.PENDING
            task.error = None
            task.start_time = None
            task.end_time = None
            task.current_step = None
            task.result_task_id = None
            task.retry_count = 0

    def _generate_output_subdir(self, round_num: int) -> str:
        """为某一轮生成唯一的输出目录名。"""
        base = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        candidate = base
        index = 1
        existing = set(self._round_output_dirs.values())
        while candidate in existing:
            index += 1
            candidate = f"{base}_{round_num}_{index}"
        return candidate

    def _ensure_round_output_subdir(self, round_num: int, reuse_existing: bool = False):
        """确保当前轮的输出子目录已准备好。"""
        round_key = str(round_num)
        output_subdir = self._round_output_dirs.get(round_key) if reuse_existing else None
        if not output_subdir:
            output_subdir = self._round_output_dirs.get(round_key)
        if not output_subdir:
            output_subdir = self._generate_output_subdir(round_num)
            self._round_output_dirs[round_key] = output_subdir

        self._output_subdir = output_subdir

    async def _run_all_rounds(self, start_round: int = 1, reuse_current_round_progress: bool = False):
        """运行所有轮次。"""
        try:
            for round_num in range(start_round, self.experiment_rounds + 1):
                if self._cancelled:
                    break

                self.current_round = round_num
                is_resumed_round = reuse_current_round_progress and round_num == start_round
                self._ensure_round_output_subdir(round_num, reuse_existing=is_resumed_round)

                logger.info(f"开始第 {round_num}/{self.experiment_rounds} 轮实验")

                if round_num == start_round:
                    if is_resumed_round:
                        self.on_complete(self.get_status())
                    elif round_num > 1:
                        self._reset_tasks_for_new_round()
                        self.on_complete(self.get_status())
                else:
                    self._reset_tasks_for_new_round()
                    self.on_complete(self.get_status())

                self._save_state()
                await self._run_all(skip_completed=is_resumed_round)
                self._save_state()
        except Exception:
            logger.exception("批量任务执行过程中发生未捕获异常")
            raise
        finally:
            self._running = False
            if self._tasks:
                self.current_round = max(self.current_round, start_round)
            self.on_complete(self.get_status())
            self._save_state()

    async def _run_all(self, skip_completed: bool = False):
        """运行当前轮次的所有任务。"""
        await asyncio.gather(*[
            self._run_with_semaphore(task)
            for task in self._tasks.values()
            if not (skip_completed and task.status == TaskStatus.DONE)
        ], return_exceptions=True)

    async def _run_with_semaphore(self, task: BatchTask):
        """使用信号量控制并发执行单个任务。"""
        if self._cancelled:
            return

        async with self._semaphore:
            if self._cancelled:
                return
            await self._run_single_task(task)

    async def _run_single_task(self, task: BatchTask, is_retry: bool = False):
        """
        执行单个任务。

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
        self._save_state()

        error_occurred = False
        error_message = ""

        def on_status(msg_type: str, content: str):
            if "语义来源池构建完成" in content or "Semantic source pool built" in content:
                return
            if "启动智能体团队" in content or "Starting agent team" in content:
                return
            preprocess_keywords = [
                "初始化", "Initializing",
                "解析", "Parsing", "parsed",
                "正在构建", "Building",
                "附件", "attachment",
                "语义", "semantic",
                "证据池", "evidence",
                "文档索引", "docindex",
                "📁", "📄", "✓",
                "失败", "failed", "异常", "error",
            ]
            if not any(keyword in content for keyword in preprocess_keywords):
                return
            task.current_step = content
            logger.info(f"任务 {task.id} 状态更新: {content}")
            self.on_update(task)
            self._save_state()

        def on_agent(agent: str, content: str, progress: int, total: int, prompt: Any = None):
            if agent == "Orchestrator":
                return
            task.current_step = f"{agent}: {content}"
            logger.info(f"任务 {task.id} 智能体更新: {agent} - {content}")
            self.on_update(task)
            self._save_state()

        def on_complete(task_id: str):
            nonlocal error_occurred
            task.result_task_id = task_id
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
            output_subdir=self._output_subdir,
        )
        self._current_runners.append(runner)

        try:
            reuse_task_id = task.first_round_task_id if self.current_round > 1 else None
            await runner.run(task.id, task.pdf_path, reuse_task_id=reuse_task_id)
        except Exception as exc:
            error_occurred = True
            error_message = str(exc)
            logger.exception(f"任务 {task.id} 执行异常")
        finally:
            if runner in self._current_runners:
                self._current_runners.remove(runner)

        task.end_time = datetime.now()

        if self._cancelled:
            task.status = TaskStatus.CANCELLED
            task.current_step = "已取消"
        elif error_occurred:
            if task.retry_count < self.MAX_RETRY:
                task.retry_count += 1
                task.current_step = f"正在重试 ({task.retry_count}/{self.MAX_RETRY})..."
                self.on_update(task)
                self._save_state()
                logger.info(f"任务 {task.id} 失败，正在重试 ({task.retry_count}/{self.MAX_RETRY})")
                await self._run_single_task(task, is_retry=True)
                return
            task.status = TaskStatus.ERROR
            task.error = error_message
            task.current_step = f"失败: {error_message[:50]}..."
        else:
            task.status = TaskStatus.DONE
            task.current_step = "已完成"
            token_usage = runner.get_token_usage()
            if token_usage:
                task.token_usage = {
                    "prompt_token_count": token_usage.get("prompt_token_count", 0),
                    "response_token_count": token_usage.get("response_token_count", 0),
                    "total_token_count": token_usage.get("total_token_count", 0),
                }

        self.on_update(task)
        self._save_state()


_batch_runner: Optional[BatchRunner] = None


def get_batch_runner() -> Optional[BatchRunner]:
    """获取当前批量运行器实例。"""
    return _batch_runner


def create_batch_runner(
    concurrency: int = 2,
    experiment_rounds: int = 1,
    on_update: Optional[Callable[[BatchTask], None]] = None,
    on_complete: Optional[Callable[[dict], None]] = None,
) -> BatchRunner:
    """创建新的批量运行器。"""
    global _batch_runner
    _batch_runner = BatchRunner(
        concurrency=concurrency,
        experiment_rounds=experiment_rounds,
        on_update=on_update,
        on_complete=on_complete,
    )
    return _batch_runner


def get_or_create_batch_runner(
    concurrency: int = 2,
    experiment_rounds: int = 1,
    on_update: Optional[Callable[[BatchTask], None]] = None,
    on_complete: Optional[Callable[[dict], None]] = None,
) -> BatchRunner:
    """获取当前运行器；若不存在则创建一个新的。"""
    runner = get_batch_runner()
    if runner is None:
        runner = create_batch_runner(
            concurrency=concurrency,
            experiment_rounds=experiment_rounds,
            on_update=on_update,
            on_complete=on_complete,
        )
    else:
        if on_update is not None:
            runner.on_update = on_update
        if on_complete is not None:
            runner.on_complete = on_complete
    return runner
