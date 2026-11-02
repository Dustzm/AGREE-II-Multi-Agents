"""
Text normalization helpers for source-span validation.
"""
from __future__ import annotations

import html
import re
import unicodedata


_TRANSLATION_TABLE = str.maketrans(
    {
        "\u00a0": " ",
        "\u2007": " ",
        "\u202f": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
    }
)

_ZERO_WIDTH_TABLE = str.maketrans({"\u200b": "", "\u200c": "", "\u200d": "", "\u200e": "", "\u200f": "", "\ufeff": ""})

# List markers ("- ", "* ", "1. ", "(1) ", "[1] ") at a line start or after whitespace.
# They are stripped from BOTH sides in a dedicated variant only: a quote may keep or
# drop them, and may join list lines with a space instead of a newline.
_LIST_MARKER = re.compile(r"(?:^|(?<=\s))(?:[-*•·]+|[0-9]{1,3}[.)]|\([0-9]{1,3}\)|\[[0-9]{1,3}\])\s+")

# Words hyphenated across a PDF line break are one word in the source.
_HYPHENATION = re.compile(r"(\w)-\s*\n\s*(\w)")

# MinerU emits symbols and sub/superscripts as inline LaTeX ("$\alpha$", "$\geq$",
# "kg/m $^{2}$"); models usually write the Unicode character ("α", "≥", "kg/m2").
_INLINE_MATH = re.compile(r"(\s*)\$([^$\n]{1,120}?)\$")
_LATEX_COMMAND = re.compile(r"\\([A-Za-z]+|.)")
_LATEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ", "tau": "τ", "chi": "χ", "omega": "ω",
    "Delta": "Δ", "geq": "≥", "ge": "≥", "leq": "≤", "le": "≤", "neq": "≠", "ne": "≠",
    "pm": "±", "times": "×", "div": "÷", "approx": "≈", "sim": "~", "cdot": "·",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "uparrow": "↑", "downarrow": "↓",
    "circ": "°", "degree": "°", "lt": "<", "gt": ">",
}
_LATEX_FORMATTING = frozenset({
    "mathrm", "mathbf", "mathit", "mathsf", "text", "textrm", "operatorname", "left", "right", "rm", "bf",
})
# Spacing around symbols differs between the PDF render and model quotes ("≥ 150" vs
# "≥150"); dropping it on both sides keeps the comparison symmetric.
_SYMBOL_SPACING = re.compile(r"\s*([Ͱ-Ͽ←-⇿∀-⋿±×÷°·~<>=%])\s*")

_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；])\s*|(?<=[.!?;])\s+")


def _canonical(text: str) -> str:
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_TRANSLATION_TABLE)
    return text.translate(_ZERO_WIDTH_TABLE)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_text_for_source_match(text: str) -> str:
    """
    Normalize text for conservative source-span matching.

    The helper only removes formatting-level differences that commonly appear
    after PDF/table extraction. It does not perform semantic matching.
    """
    return _collapse(_canonical(text))


def _latex_command(match: re.Match) -> str:
    name = match.group(1)
    if name in _LATEX_SYMBOLS:
        return _LATEX_SYMBOLS[name]
    if name in _LATEX_FORMATTING:
        return ""
    if name.isalpha():
        return name  # e.g. \log
    return " " if name in ",;:! " else name  # spacing commands; \% \_ \# keep the char


def _render_inline_math(match: re.Match) -> str:
    leading, body = match.groups()
    body = body.strip()
    rendered = re.sub(r"[{}^_]", "", _LATEX_COMMAND.sub(_latex_command, body)).strip()
    # A sub/superscript belongs to the preceding token: "m $^{2}$" is "m2".
    return rendered if body.startswith(("^", "_")) else f"{leading}{rendered}"


def _normalize_layout_insensitive(text: str) -> str:
    """Additionally ignore list markers, line-break hyphenation and inline-LaTeX symbols."""
    text = _INLINE_MATH.sub(_render_inline_math, _canonical(text))
    text = _HYPHENATION.sub(r"\1\2", text)
    text = _SYMBOL_SPACING.sub(r"\1", _LIST_MARKER.sub(" ", text))
    return _collapse(text)


def _normalize_text_for_loose_source_match(text: str) -> str:
    """Normalize formatting punctuation for title/table style source matching."""
    text = normalize_text_for_source_match(text)
    text = re.sub(r"[#*_`]+", " ", text)
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Ordered from strictest to loosest; a variant only ever adds matches.
_MATCH_VARIANTS = (
    normalize_text_for_source_match,
    _normalize_layout_insensitive,
    _normalize_text_for_loose_source_match,
)


def count_source_span(container: str, span: str) -> int:
    """Occurrences of span in container under the strictest variant that finds it."""
    for normalize in _MATCH_VARIANTS:
        normalized_span = normalize(span)
        if not normalized_span:
            continue
        count = normalize(container).count(normalized_span)
        if count:
            return count
    return 0


def contains_source_span(container: str, span: str) -> bool:
    """Return whether span appears in container after conservative normalization."""
    return count_source_span(container, span) > 0


def _candidate_units(text: str):
    """Yield sentences, then lines, then paragraphs, then the whole text."""
    seen: set[str] = set()
    groups = (
        _SENTENCE_BOUNDARY.split(text),
        text.splitlines(),
        re.split(r"\n\s*\n", text),
        [text],
    )
    for group in groups:
        for unit in group:
            stripped = unit.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                yield stripped


def find_source_span(container: str, span: str, *, max_chars: int | None = None) -> str | None:
    """Return the smallest source unit containing span, or None when absent.

    Used to re-anchor a paraphrased quote onto real source text: the returned
    value is an original slice of ``container`` (not a normalized copy). Units
    longer than ``max_chars`` are never returned.
    """
    if not container or not span:
        return None
    for unit in _candidate_units(container):
        if max_chars is not None and len(unit) > max_chars:
            continue
        if contains_source_span(unit, span):
            return unit
    return None
