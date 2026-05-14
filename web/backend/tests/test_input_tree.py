"""
input_tree 文件树扫描测试。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "input_tree.py"
)


def load_input_tree_module():
    """按文件路径加载 input_tree 模块，避免触发 services 包副作用。"""
    spec = importlib.util.spec_from_file_location("ama_input_tree", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_scan_directory_entries_keeps_cda_ada_named_attachments(tmp_path):
    """文件树扫描不应再忽略文件名含 CDA/ADA 的附件。"""
    module = load_input_tree_module()
    main_pdf = tmp_path / "1-2018-035E.pdf"
    cda_attachment = tmp_path / "1-2018-035E 附件1 2018年CDA指南introduction.pdf"
    ada_attachment = tmp_path / "1-2018-035E 附件2 2018年ADA指南methods.pdf"

    for path in (main_pdf, cda_attachment, ada_attachment):
        path.write_bytes(b"%PDF-1.4")

    entries = module.scan_directory_entries(tmp_path)

    assert [pdf["name"] for pdf in entries.main_pdfs] == [main_pdf.name]
    assert entries.main_pdfs[0]["attachment_count"] == 2

    files_by_name = {item["name"]: item for item in entries.files}
    assert cda_attachment.name in files_by_name
    assert ada_attachment.name in files_by_name
    assert files_by_name[cda_attachment.name]["is_attachment"] is True
    assert files_by_name[ada_attachment.name]["is_attachment"] is True
