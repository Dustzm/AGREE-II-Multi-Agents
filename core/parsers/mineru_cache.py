"""
MinerU 解析产物缓存。

缓存目录固定为项目根目录下的 doc_data，用于复用主文档和 PDF 附件的
MinerU 中间产物，避免重复调用远程 MinerU 服务。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOC_DATA_ROOT = PROJECT_ROOT / "doc_data"

_GUIDELINE_ID_PATTERN = re.compile(r"^(\d+-\d{4}-[A-Za-z0-9]+)")
_MAIN_REQUIRED_SUFFIXES = (
    "_content_list.json",
    "_middle.json",
    "_model.json",
)
_OPTIONAL_SUFFIXES = (
    "_docindex.json",
)
_EXCLUDED_SUFFIXES = (
    "_evidence_pool.json",
    "_visual_evidence.json",
)


@dataclass(frozen=True)
class MinerUCacheHit:
    """MinerU 缓存命中信息。"""

    cache_dir: Path
    source_path: Path
    prep_dir: Path
    pages: int


def extract_guideline_id(name: str) -> str:
    """从主文档或附件文件名中提取指南 id。"""
    stem = Path(name).stem
    match = _GUIDELINE_ID_PATTERN.match(stem)
    if match:
        return match.group(1)
    return stem


def _cache_dir(guideline_id: str) -> Path:
    """返回指南 id 对应的缓存目录。"""
    return DOC_DATA_ROOT / guideline_id


def _has_main_mineru_outputs(prep_dir: Path, guideline_id: str) -> bool:
    """检查主文档 MinerU 核心产物是否完整。"""
    return all((prep_dir / f"{guideline_id}{suffix}").exists() for suffix in _MAIN_REQUIRED_SUFFIXES)


def _is_excluded_cache_file(path: Path) -> bool:
    """判断文件是否不应进入 MinerU 缓存。"""
    return any(path.name.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES)


def _copy_file(src: Path, dst: Path) -> None:
    """复制文件并确保父目录存在。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    """复制目录，排除 evidence pool 等非 MinerU 产物。"""

    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if any(name.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES)
        }

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def _read_pages_count(middle_json_path: Path) -> int:
    """从 middle.json 读取页数，失败时返回 0。"""
    try:
        data = json.loads(middle_json_path.read_text(encoding="utf-8"))
        pdf_info = data.get("pdf_info", [])
        if isinstance(pdf_info, list):
            return len(pdf_info)
    except Exception as exc:
        logger.warning("读取 MinerU 缓存页数失败: path=%s error=%s", middle_json_path, exc)
    return 0


def restore_document_cache(
    guideline_id: str,
    target_source_path: str | Path,
    target_prep_dir: str | Path,
) -> MinerUCacheHit | None:
    """
    将 doc_data 中的主文档 MinerU 产物恢复到当前任务目录。

    仅恢复 source.md、MinerU JSON、docindex、图片和附件 MinerU 产物；
    不恢复 evidence_pool、visual_evidence、评价结果或报告。
    """
    cache_dir = _cache_dir(guideline_id)
    cache_source = cache_dir / "source.md"
    cache_prep = cache_dir / "prep"

    if not cache_source.exists() or not _has_main_mineru_outputs(cache_prep, guideline_id):
        logger.debug("未命中 MinerU 缓存: guideline_id=%s cache_dir=%s", guideline_id, cache_dir)
        return None

    target_source = Path(target_source_path)
    target_prep = Path(target_prep_dir)
    target_prep.mkdir(parents=True, exist_ok=True)

    _copy_file(cache_source, target_source)

    for suffix in (*_MAIN_REQUIRED_SUFFIXES, *_OPTIONAL_SUFFIXES):
        src = cache_prep / f"{guideline_id}{suffix}"
        if src.exists() and not _is_excluded_cache_file(src):
            _copy_file(src, target_prep / src.name)

    for dirname in ("images", "attachment"):
        src_dir = cache_prep / dirname
        if src_dir.exists():
            _copy_tree(src_dir, target_prep / dirname)

    pages = _read_pages_count(cache_prep / f"{guideline_id}_middle.json")
    logger.info(
        "已恢复 MinerU 缓存: guideline_id=%s cache_dir=%s target_prep=%s pages=%s",
        guideline_id,
        cache_dir,
        target_prep,
        pages,
    )
    return MinerUCacheHit(
        cache_dir=cache_dir,
        source_path=target_source,
        prep_dir=target_prep,
        pages=pages,
    )


def _attachment_outputs_complete(attachment_dir: Path, attachment_stem: str) -> bool:
    """检查 PDF 附件解析所需的 MinerU 文本产物是否可用。"""
    required = (
        attachment_dir / f"{attachment_stem}.md",
        attachment_dir / f"{attachment_stem}_content_list.json",
    )
    return all(path.is_file() and path.stat().st_size > 0 for path in required)


def _find_attachment_cache_dir(attachment_path: Path, target_dir: Path) -> Path | None:
    """在当前任务目录或全局 doc_data 中查找附件缓存目录。"""
    attachment_stem = attachment_path.stem

    if _attachment_outputs_complete(target_dir, attachment_stem):
        return target_dir

    guideline_id = extract_guideline_id(attachment_stem)
    attachment_root = _cache_dir(guideline_id) / "prep" / "attachment"
    if not attachment_root.exists():
        return None

    for content_list in attachment_root.glob(f"**/{attachment_stem}_content_list.json"):
        cache_dir = content_list.parent
        if _attachment_outputs_complete(cache_dir, attachment_stem):
            return cache_dir

    return None


def restore_attachment_cache(
    attachment_path: str | Path,
    target_output_dir: str | Path,
) -> Path | None:
    """
    恢复并返回 PDF 附件的 content_list.json 路径。

    如果当前输出目录已包含完整产物，则直接返回；否则按附件文件名 stem
    从 doc_data 中查找并复制对应附件目录。
    """
    attachment_file = Path(attachment_path)
    target_dir = Path(target_output_dir)
    cache_dir = _find_attachment_cache_dir(attachment_file, target_dir)
    if cache_dir is None:
        return None

    if cache_dir != target_dir:
        _copy_tree(cache_dir, target_dir)

    content_list_path = target_dir / f"{attachment_file.stem}_content_list.json"
    if not content_list_path.exists():
        logger.warning(
            "附件 MinerU 缓存缺少 content_list: attachment=%s target=%s",
            attachment_file,
            target_dir,
        )
        return None

    logger.info(
        "已复用附件 MinerU 缓存: attachment=%s cache_dir=%s target=%s",
        attachment_file,
        cache_dir,
        target_dir,
    )
    return content_list_path
