"""流水线对外只发布事件；界面文案、批量状态和 MCP 协议由各入口的 sink 负责。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .outcome import FailureCode, Phase


@dataclass(frozen=True)
class PipelineEvent:
    """阶段进度事件；key 与 params 是语言无关的消息标识。"""

    kind: str  # "status" 或 "agent"
    phase: Phase
    key: str
    state: str = "running"
    params: dict[str, Any] = field(default_factory=dict)
    agent: str | None = None
    progress: int | None = None
    total: int | None = None


@dataclass(frozen=True)
class PipelineFailureReport:
    """一次失败的完整上下文：代码、阶段、原始异常和条目级失败摘要。"""

    code: FailureCode
    phase: Phase
    error: BaseException
    details: dict[str, Any] = field(default_factory=dict)
    item_failures: dict[str, dict[str, str]] = field(default_factory=dict)

    def item_summary(self) -> str:
        return "; ".join(
            f"{item}: {info['type']} ({info['stage']}): {info['message']}" for item, info in self.item_failures.items()
        )


class PipelineSink:
    """事件接收方；默认忽略全部事件，入口只覆盖自己关心的部分。"""

    def emit(self, event: PipelineEvent) -> None:
        pass

    def task_ready(self, task_id: str) -> None:
        pass

    async def item_progress(self, skill_id: str, title: str, status: str, duration: float | None) -> None:
        pass

    async def summary(self, total: int, completed: int, elapsed: float) -> None:
        pass

    def completed(self, task_id: str) -> None:
        pass

    def partial(self, task_id: str, missing: list[str], failures: dict) -> None:
        pass

    def cancelled(self, task_id: str) -> None:
        pass

    def failed(self, report: PipelineFailureReport) -> None:
        pass
