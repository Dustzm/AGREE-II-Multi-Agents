"""为批量评价的每轮结果生成稳定且全局唯一的任务 ID。"""

from pathlib import Path
from hashlib import sha256


def unique_item_id(pdf_path: str, seen: set[str]) -> str:
    """同批次中的同名 PDF 获得不同条目 ID。"""
    base = Path(pdf_path).stem
    candidate = base
    index = 1
    while candidate in seen:
        index += 1
        candidate = f"{base}-{index}"
    seen.add(candidate)
    return candidate


def batch_run_task_id(batch_id: str, round_number: int, item_id: str) -> str:
    """同一批次、轮次和条目重试时返回同一个目录名。"""
    digest = sha256(item_id.encode("utf-8")).hexdigest()[:10]
    return f"{batch_id}-r{round_number}-{item_id[:40]}-{digest}"
