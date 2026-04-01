"""
Skill 扩展 prompt 注入测试
"""
import importlib.util
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_DIR = REPO_ROOT / "core"
SKILLS_DIR = CORE_DIR / "skills"
PROMPTS_DIR = CORE_DIR / "prompts"
PKG_ROOT = "test_core_skill_injection"


def _ensure_package(package_name: str, package_path: Path) -> None:
    if package_name in sys.modules:
        return

    module = types.ModuleType(package_name)
    module.__path__ = [str(package_path)]
    sys.modules[package_name] = module


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_loader_module():
    _ensure_package(PKG_ROOT, CORE_DIR)
    _ensure_package(f"{PKG_ROOT}.skills", SKILLS_DIR)
    return _load_module(f"{PKG_ROOT}.skills.loader", SKILLS_DIR / "loader.py")


def _load_iterative_prompts_module():
    _ensure_package(PKG_ROOT, CORE_DIR)
    _ensure_package(f"{PKG_ROOT}.prompts", PROMPTS_DIR)
    return _load_module(f"{PKG_ROOT}.prompts.iterative_prompts", PROMPTS_DIR / "iterative_prompts.py")


def test_loader_exposes_item17_vision_prompt_for_zh():
    loader = _load_loader_module()

    config = loader.load_skill_config("item_17", lang="zh")

    assert "vision_prompt" in config
    assert "截图" in config["vision_prompt"]


def test_loader_falls_back_to_english_for_missing_zh_special_prompt():
    loader = _load_loader_module()

    config = {
        "enabled": True,
        "domain": 5,
        "domain_name": "Applicability",
        "domain_name_zh": "适用性",
        "title": "Title",
        "title_zh": "标题",
        "description": "Description",
        "description_zh": "描述",
        "locating_info": "Locate",
        "locating_info_zh": "定位",
        "scoring_principle": [],
        "special_rules_en": "English special rule only",
    }

    resolved = loader._build_lang_config(config, "item_x", "zh")

    assert resolved["special_rules"] == "English special rule only"


def test_loader_exposes_item19_special_prompts_and_no_criteria_in_shots():
    loader = _load_loader_module()

    config = loader.load_skill_config("item_19", lang="zh")

    assert config["shots"] == []
    assert "special_rules" in config
    assert "工具/资源" in config["special_rules"]


def test_render_prompts_inject_item_specific_rules_section():
    prompts = _load_iterative_prompts_module()
    special_instruction = "Rule A\nRule B"

    extraction_prompt = prompts.render_extraction_prompt(
        item_id="item_19",
        title="Title",
        scoring_table="| 1 | A |",
        chunk_content="Chunk text",
        special_instruction=special_instruction,
    )
    scoring_prompt = prompts.render_scoring_prompt(
        item_id="item_19",
        title="Title",
        shots=["Example 1"],
        evidence_cache=["Evidence 1"],
        scoring_table="| 1 | A |",
        n_sources=1,
        special_instruction=special_instruction,
    )

    assert "## Item-Specific Rules" in extraction_prompt
    assert "## Item-Specific Rules" in scoring_prompt
    assert extraction_prompt.index("## Item-Specific Rules") < extraction_prompt.index("## Content to Analyze")
    assert scoring_prompt.index("## Item-Specific Rules") < scoring_prompt.index("## Scoring Reference Examples")


def test_render_prompts_remain_compatible_without_special_rules():
    prompts = _load_iterative_prompts_module()

    extraction_prompt = prompts.render_extraction_prompt(
        item_id="item_1",
        title="Title",
        scoring_table="| 1 | A |",
        chunk_content="Chunk text",
    )
    scoring_prompt = prompts.render_scoring_prompt(
        item_id="item_1",
        title="Title",
        shots=[],
        evidence_cache=["Evidence 1"],
        scoring_table="| 1 | A |",
        n_sources=1,
    )

    assert "## Item-Specific Rules" not in extraction_prompt
    assert "## Item-Specific Rules" not in scoring_prompt
