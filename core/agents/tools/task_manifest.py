"""评价结果与输入、模型配置的绑定信息。"""

import hashlib
import json
from pathlib import Path

from core.agents.base.model_config import get_provider_config_for
from core.config import get_settings
from core.utils.atomic_json import write_json_atomically


def ensure_task_manifest(task_dir: str | Path, pdf_path: str | Path, analysis_engine: str | None) -> None:
    """新任务记录指纹；续跑时拒绝混用无法证实来源的条目。"""
    task_dir = Path(task_dir)
    digest = hashlib.sha256()
    with Path(pdf_path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    settings = get_settings()
    from core.llm.context import current_llm_settings
    llm = current_llm_settings()
    provider = get_provider_config_for(llm.provider)
    expected = {
        "pdf_sha256": digest.hexdigest(),
        "context_window": llm.context_window,
        "provider": llm.provider,
        "model": llm.model_name,
        "base_url": llm.base_url or provider.get("default_base_url", ""),
        "analysis_engine": analysis_engine or settings.app.analysis_engine,
    }
    manifest = task_dir / ".ama" / "task_manifest.json"
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8")) != expected:
            raise ValueError(f"任务输入或评价配置与已有结果不一致，已保留原文件: {manifest}")
        return
    eval_dir = task_dir / ".ama" / "evaluations"
    if any(eval_dir.glob("item_*.json")):
        raise ValueError(f"已有条目结果缺少来源指纹，无法安全续跑，已保留原文件: {task_dir}")
    write_json_atomically(manifest, expected)
