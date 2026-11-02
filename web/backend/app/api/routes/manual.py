"""
使用手册路由

提供多语言手册正文与 docs 目录下的静态资源访问能力。
"""
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ...i18n.messages import LocalizedHTTPException
from ...i18n import request_language

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[5]
DOCS_ROOT = PROJECT_ROOT / "docs"
MANUAL_FILES = {
    "zh-CN": "USER_MANUAL.md",
    "en-US": "USER_MANUAL_EN.md",
}


class ManualResponse(BaseModel):
    """手册内容响应"""
    lang: str
    available: bool
    title: str
    content: str


def _resolve_manual_path(lang: str) -> Path:
    filename = MANUAL_FILES.get(lang, MANUAL_FILES["zh-CN"])
    return DOCS_ROOT / filename


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


@router.get("/manual", response_model=ManualResponse)
async def get_manual(
    lang: str | None = Query(None, pattern="^(zh-CN|en-US)$")
):
    """获取指定语言的使用手册正文"""
    lang = lang or ("zh-CN" if request_language.get() == "zh" else "en-US")
    manual_path = _resolve_manual_path(lang)

    if not manual_path.exists():
        return ManualResponse(
            lang=lang,
            available=False,
            title="User Manual" if lang == "en-US" else "用户使用手册",
            content="",
        )

    content = manual_path.read_text(encoding="utf-8")
    return ManualResponse(
        lang=lang,
        available=True,
        title=_extract_title(content, "User Manual" if lang == "en-US" else "用户使用手册"),
        content=content,
    )


@router.get("/manual-assets/{asset_path:path}")
async def get_manual_asset(asset_path: str):
    """读取 docs 目录中的静态资源，禁止目录穿越"""
    if not asset_path:
        raise LocalizedHTTPException(404, "error.not_found")

    target = (DOCS_ROOT / asset_path).resolve()
    docs_root = DOCS_ROOT.resolve()

    if docs_root not in target.parents and target != docs_root:
        raise LocalizedHTTPException(403, "error.forbidden")

    if not target.is_file():
        raise LocalizedHTTPException(404, "error.not_found")

    return FileResponse(target)
