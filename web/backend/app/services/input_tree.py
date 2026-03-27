"""
input 目录树扫描服务。

统一处理：
1. 递归目录树构建
2. 同目录附件识别
3. 批量页主 PDF 列表统计
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ATTACHMENT_PATTERNS = [
    "附件", "附录", "appendix", "supplement",
    "执行摘要", "摘要", "summary", "ppt",
]

SUPPORTED_EXTENSIONS = {
    "document": [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".md", ".rtf"],
    "image": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".tiff", ".tif"],
    "media": [".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".flac", ".aac", ".ogg"],
}

ALL_SUPPORTED_EXTENSIONS = {
    ext
    for exts in SUPPORTED_EXTENSIONS.values()
    for ext in exts
}


@dataclass
class DirectoryEntries:
    """单目录下的文件扫描结果。"""

    files: list[dict]
    main_pdfs: list[dict]


def is_hidden_path(path: Path) -> bool:
    """忽略隐藏文件和隐藏目录。"""
    return any(part.startswith(".") for part in path.parts)


def is_ignored_file(filename: str) -> bool:
    """批量检测时忽略指定的特定指南文件。"""
    return "CDA指南" in filename or "ADA指南" in filename


def is_attachment_keyword(filename: str) -> bool:
    """根据文件名关键词判断是否为附件。"""
    stem_lower = Path(filename).stem.lower()
    return any(pattern.lower() in stem_lower for pattern in ATTACHMENT_PATTERNS)


def is_sibling_attachment(main_stem: str, suspect_stem: str) -> bool:
    """判断同目录文件是否属于主文件附件。"""
    if not suspect_stem.startswith(main_stem):
        return False

    if len(suspect_stem) <= len(main_stem):
        return False

    next_char = suspect_stem[len(main_stem)]
    if next_char in " _-":
        return True

    return "\u4e00" <= next_char <= "\u9fff"


def get_related_attachments(main_file: dict, files: list[dict]) -> list[dict]:
    """获取某个主 PDF 在同目录下的附件列表。"""
    main_stem = Path(main_file["name"]).stem
    return [
        file
        for file in files
        if file["path"] != main_file["path"]
        and file.get("is_attachment") is True
        and is_sibling_attachment(main_stem, Path(file["name"]).stem)
    ]


def _format_modified(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _build_file_payload(file_path: Path, *, is_attachment: bool, attachment_count: int = 0) -> dict:
    stat = file_path.stat()
    return {
        "name": file_path.name,
        "path": str(file_path),
        "size": stat.st_size,
        "modified": _format_modified(stat.st_mtime),
        "is_attachment": is_attachment,
        "attachment_count": attachment_count,
    }


def scan_directory_entries(directory: Path) -> DirectoryEntries:
    """
    扫描单个目录下的支持文件，并识别主 PDF / 附件。

    附件判定仅在当前目录内进行，不跨目录关联。
    """
    all_files = [
        item
        for item in sorted(directory.iterdir(), key=lambda path: path.name.lower())
        if item.is_file()
        and not is_hidden_path(item.relative_to(directory))
        and not is_ignored_file(item.name)
        and item.suffix.lower() in ALL_SUPPORTED_EXTENSIONS
    ]

    potential_mains = [file_path for file_path in all_files if file_path.suffix.lower() == ".pdf"]
    potential_mains.sort(key=lambda path: (len(path.stem), path.name.lower()))

    identified_attachments: set[Path] = set()
    attachments_map: dict[Path, list[Path]] = {}

    for main_pdf in potential_mains:
        if main_pdf in identified_attachments:
            continue

        main_stem = main_pdf.stem
        my_attachments: list[Path] = []

        for file_path in all_files:
            if file_path == main_pdf:
                continue

            if is_sibling_attachment(main_stem, file_path.stem):
                my_attachments.append(file_path)
                identified_attachments.add(file_path)

        attachments_map[main_pdf] = my_attachments

    file_payloads: list[dict] = []
    main_pdf_payloads: list[dict] = []

    for file_path in all_files:
        suffix_lower = file_path.suffix.lower()
        is_attachment = (
            suffix_lower != ".pdf"
            or is_attachment_keyword(file_path.name)
            or file_path in identified_attachments
        )

        attachment_count = len(attachments_map.get(file_path, [])) if not is_attachment else 0
        payload = _build_file_payload(
            file_path,
            is_attachment=is_attachment,
            attachment_count=attachment_count,
        )
        file_payloads.append(payload)

        if suffix_lower == ".pdf" and not is_attachment:
            main_pdf_payloads.append(payload)

    return DirectoryEntries(files=file_payloads, main_pdfs=main_pdf_payloads)


def build_input_tree(directory: Path) -> dict:
    """递归构建 input 目录树。"""
    entries = scan_directory_entries(directory)

    child_directories = [
        build_input_tree(child_dir)
        for child_dir in sorted(directory.iterdir(), key=lambda path: path.name.lower())
        if child_dir.is_dir() and not is_hidden_path(child_dir.relative_to(directory))
    ]

    stat = directory.stat()
    direct_pdf_count = sum(1 for file in entries.files if Path(file["name"]).suffix.lower() == ".pdf")
    direct_selectable_pdf_count = len(entries.main_pdfs)

    return {
        "name": directory.name,
        "path": str(directory),
        "modified": _format_modified(stat.st_mtime),
        "children": child_directories,
        "files": entries.files,
        "pdfs": entries.main_pdfs,
        "file_count": len(entries.files) + sum(child["file_count"] for child in child_directories),
        "pdf_count": direct_pdf_count + sum(child["pdf_count"] for child in child_directories),
        "selectable_pdf_count": direct_selectable_pdf_count + sum(child["selectable_pdf_count"] for child in child_directories),
    }
