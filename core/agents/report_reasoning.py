"""条目报告 step3 结构化推理的 schema 与容错归一化（纯函数，无 IO）。"""
import json
from typing import Any, Dict, List

from pydantic import BaseModel, Field, ValidationError

from core.utils.llm_errors import InvalidModelJSON


# ============================================================================
# Pydantic Schema 定义 - 强制 LLM 输出结构
# ============================================================================

class ReasonItem(BaseModel):
    """单个评分理由"""
    point: str = Field(description="评分要点名称")
    explanation: str = Field(description="详细解释")
    evidence_chunks: List[str] = Field(default_factory=list, description="相关的 chunk_id 列表")


class StructuredReasoning(BaseModel):
    """结构化推理"""
    summary: str = Field(description="一句话总结")
    reasons: List[ReasonItem] = Field(description="分条理由列表")
    score_justification: str = Field(description="评分判定理由")


_STEP3_SUMMARY_KEYS = ("summary", "assessment", "overall", "review", "总结", "概述", "评价总结")
_REASON_POINT_KEYS = ("point", "label", "title", "aspect", "name", "topic", "评分要点", "评分点", "要点")
_REASON_EXPLANATION_KEYS = (
    "explanation", "detail", "details", "description", "text", "reason", "content",
    "解释", "详细解释", "说明", "理由",
)
_STEP3_JUSTIFICATION_KEYS = (
    "score_justification", "justification", "score_reason", "rationale",
    "评分理由", "评分依据", "判定理由",
)


def _first_text(payload: Dict[str, Any], keys) -> str:
    """Return the first non-empty textual value among the candidate keys."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def _coerce_evidence_chunks(value: Any) -> List[str]:
    if isinstance(value, str):
        entries = [value]
    elif isinstance(value, (list, tuple)):
        entries = list(value)
    else:
        entries = []
    chunks: List[str] = []
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            chunks.append(entry.strip())
        elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
            chunks.append(str(entry))
    return chunks


def _coerce_reason_item(value: Any) -> Dict[str, Any] | None:
    """Coerce one reason entry (string or loosely-typed dict) into a ReasonItem dict."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return {"point": text, "explanation": text, "evidence_chunks": []}
    if not isinstance(value, dict):
        return None
    point = _first_text(value, _REASON_POINT_KEYS)
    explanation = _first_text(value, _REASON_EXPLANATION_KEYS)
    if not explanation:
        explanation = point
    if not point:
        point = explanation
    if not point:
        return None
    evidence = value.get("evidence_chunks")
    if evidence is None:
        evidence = value.get("chunks", value.get("evidence"))
    return {"point": point, "explanation": explanation, "evidence_chunks": _coerce_evidence_chunks(evidence)}


def parse_reasoning_json(value: str) -> Dict[str, Any] | None:
    """Parse a serialized JSON reasoning object that the model emitted as a string."""
    text = value.strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def normalize_step3_reasoning(raw: Any) -> Dict[str, Any]:
    """Repair the report's step3 reasoning payload before strict validation.

    The final report call runs in ``json_object`` mode (no schema enforcement), so
    models drift: ``reasons`` may be a list of bare strings or use alternate field
    names. Coerce everything usable instead of failing the whole item; only a
    payload with no reasoning content at all is invalid.
    """
    if isinstance(raw, str):
        parsed = parse_reasoning_json(raw)
        if parsed is not None:
            raw = parsed
    if isinstance(raw, (list, tuple)):
        raw = {"reasons": list(raw)}
    if not isinstance(raw, dict):
        raise InvalidModelJSON("条目报告缺少有效的评分解释")

    summary = _first_text(raw, _STEP3_SUMMARY_KEYS)
    reasons: List[Dict[str, Any]] = []
    entries = raw.get("reasons")
    if isinstance(entries, (list, tuple)):
        candidates = list(entries)
    elif entries is None:
        candidates = []
    else:
        candidates = [entries]
    for entry in candidates:
        reason = _coerce_reason_item(entry)
        if reason is not None:
            reasons.append(reason)
    if not reasons and summary:
        reasons = [{"point": "综合", "explanation": summary, "evidence_chunks": []}]
    if not summary:
        summary = reasons[0]["point"] if reasons else ""
    if not summary:
        raise InvalidModelJSON("条目报告缺少有效的评分解释")

    justification = _first_text(raw, _STEP3_JUSTIFICATION_KEYS) or summary
    payload = {"summary": summary, "reasons": reasons, "score_justification": justification}
    try:
        return StructuredReasoning.model_validate(payload).model_dump()
    except ValidationError as error:  # pragma: no cover - payload is built from strings
        raise InvalidModelJSON(f"条目报告的结构化评分解释无效: {error}") from error


def coerce_step3_reasoning(value: Any) -> Dict[str, Any] | str:
    """有结构就归一化为 StructuredReasoning，否则接受非空文本；空推理必须重试。"""
    parsed = parse_reasoning_json(value) if isinstance(value, str) else value
    if isinstance(parsed, (dict, list, tuple)):
        return normalize_step3_reasoning(parsed)
    if isinstance(value, str) and value.strip():
        return value
    raise InvalidModelJSON("条目报告缺少有效的评分解释")
