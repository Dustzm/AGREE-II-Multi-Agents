"""任务目录的三种来源：新建、续跑同一目录、复用上一轮的解析与预处理结果。"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from core.config import get_settings
from core.runtime import ExecutionStage

from .outcome import FailureCode, PipelineFailure

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Workspace:
    task_id: str
    task_dir: Path
    source_path: Path
    prep_dir: Path
    # 续跑或复用上一轮时，已有证据池在校验通过后可以直接使用。
    may_reuse_evidence: bool = False
    copied_from_round: bool = False

    @property
    def weaver_dir(self) -> Path:
        return self.prep_dir.parent


def _init_directory(pdf_file: Path, task_id: str, output_subdir: str | None) -> dict:
    from core.agents import init_task_directory

    result = init_task_directory(pdf_file.name, task_id=task_id, output_subdir=output_subdir)
    if result.get("status") != "success":
        raise PipelineFailure(FailureCode.TASK_INIT_FAILED, result.get("error", "任务目录初始化失败"))
    return result


def _workspace(result: dict, **flags) -> Workspace:
    paths = result["paths"]
    return Workspace(
        task_id=result.get("task_id"),
        task_dir=Path(paths["task_dir"]),
        source_path=Path(paths["source"]),
        prep_dir=Path(paths["prep_dir"]),
        **flags,
    )


def _first_round_paths(reuse_task_id: str, reuse_output_subdir: str | None) -> dict | None:
    from core.agents.tools.public_tools import get_task_paths

    if not reuse_output_subdir:
        return get_task_paths(reuse_task_id) or None
    first_dir = Path(get_settings().app.data_root) / reuse_output_subdir / reuse_task_id
    if not first_dir.is_dir():
        raise PipelineFailure(FailureCode.TASK_INIT_FAILED, f"第一轮任务目录不存在: {first_dir}")
    return {"task_dir": str(first_dir), "source": str(first_dir / "source.md"), "prep_dir": str(first_dir / ".ama" / "prep")}


def _copy_round_files(first_round: dict, workspace: Workspace) -> None:
    first_source = Path(first_round["source"])
    if first_source.exists():
        shutil.copy2(first_source, workspace.source_path)
    for item in Path(first_round["prep_dir"]).iterdir():
        dest = workspace.prep_dir / item.name
        if dest.exists():
            continue
        if item.is_file():
            shutil.copy2(item, dest)
        elif item.is_dir():
            shutil.copytree(item, dest)
        logger.info("复制预处理文件: %s", item.name)


async def prepare_workspace(
    runtime,
    pdf_file: Path,
    task_id: str,
    *,
    output_subdir: str | None,
    analysis_engine: str | None,
    resume: bool = False,
    reuse_task_id: str | None = None,
    reuse_output_subdir: str | None = None,
) -> Workspace:
    """创建或定位任务目录；复制失败时退回为新任务，由后续阶段重新生成。"""
    from core.agents.tools.task_manifest import ensure_task_manifest

    if resume:
        task_root = Path(get_settings().app.data_root) / (output_subdir or "")
        if not (task_root / task_id).is_dir():
            raise PipelineFailure(FailureCode.TASK_INIT_FAILED, f"无法续跑，任务目录不存在: {task_id}")
        return _workspace(_init_directory(pdf_file, task_id, output_subdir), may_reuse_evidence=True)

    if reuse_task_id:
        first_round = _first_round_paths(reuse_task_id, reuse_output_subdir)
        if first_round:
            ensure_task_manifest(first_round["task_dir"], pdf_file, analysis_engine)
            workspace = _workspace(_init_directory(pdf_file, task_id, output_subdir), may_reuse_evidence=True)
            try:
                await runtime.run_blocking(
                    ExecutionStage.BLOCKING, _copy_round_files, first_round, workspace,
                    serialization_key=("reuse-prep", workspace.task_id),
                )
            except Exception as error:
                logger.warning("复制预处理文件失败: %s，将重新生成", error)
                return workspace
            logger.info("多轮实验: 已复制预处理文件到 %s", workspace.prep_dir)
            return replace(workspace, copied_from_round=True)
        logger.warning("找不到第一轮目录: %s，将创建新任务", reuse_task_id)

    return _workspace(_init_directory(pdf_file, task_id, output_subdir))
