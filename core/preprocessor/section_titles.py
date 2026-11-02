"""Recognise section titles that index sources rather than hold guideline content.

Reference lists, revision histories and indexes repeat item vocabulary without
carrying evidence. Matching is on the whole title (after stripping numbering and
appendix/attachment prefixes), never on substrings: "Patient values and
preferences" or "Body Mass Index targets" are guideline content.
"""
from __future__ import annotations

import re
import unicodedata

_NON_EVIDENCE_TITLES = frozenset({
    "references",
    "reference",
    "reference list",
    "list of references",
    "bibliography",
    "参考文献",
    "主要参考文献",
    "参考书目",
    "引用文献",
    "文献列表",
    "revision history",
    "version history",
    "修订历史",
    "修订记录",
    "版本历史",
    "index",
    "subject index",
    "索引",
})

_NUMERAL = r"(?:\d+|[ivx]+\b|[a-z]\b|[一二三四五六七八九十]+)"
_PREFIXES = (
    re.compile(r"^\[[^\]]*\]\s*"),
    re.compile(rf"^(?:appendix|annex|supplement(?:ary|al)?|附录|附件)\s*{_NUMERAL}?\s*[:：.、\-–—]?\s*"),
    re.compile(r"^(?:\d+(?:\.\d+)*\.?|[一二三四五六七八九十]+[、.])\s*"),
)
_DECORATION = re.compile(r"[\s#*_:：.。()（）\[\]【】]+")


def _core_title(title: object) -> str:
    text = unicodedata.normalize("NFKC", str(title or "")).casefold().strip()
    for prefix in _PREFIXES:
        text = prefix.sub("", text, count=1)
    return _DECORATION.sub(" ", text).strip()


def is_non_evidence_title(title: object) -> bool:
    """True for reference/revision-history/index sections; content titles stay False."""
    return _core_title(title) in _NON_EVIDENCE_TITLES
