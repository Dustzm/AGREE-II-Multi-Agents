"""使用冻结模型在 LLM 与 Rule 评价路径之间进行选择。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Any

import joblib
import numpy as np
import pandas as pd

from core.preprocessor.evidence import EvidencePool
from core.preprocessor.iterator import EvidenceIterator
from core.rule_engine.registry import get_item_spec
from core.rule_engine.routing import WeightedChunkRouter

MODEL_PATH = Path(__file__).with_name("utility_router.joblib")
MODEL_SHA256 = "8f06a6168b58ed901123c83722f356e5f3c82dda3c2a105eb776011f385ebb5c"
FIXED_LLM_ITEMS = frozenset({"item_17"})
EXPECTED_NUMERIC_FEATURES = (
    "total_chunk_count",
    "total_character_count",
    "domain_chunk_count",
    "domain_character_count",
    "domain_chunk_ratio",
    "selected_chunk_count",
    "selected_character_count",
    "selected_domain_ratio",
    "item_signal_chunk_count",
    "routing_score_mean",
    "routing_score_max",
    "routing_limit_hit",
    "selected_total_ratio",
    "domain_character_ratio",
    "selected_character_ratio",
    "selected_characters_per_chunk",
    "signal_selected_ratio",
    "routing_score_spread",
)
EXPECTED_CATEGORICAL_FEATURES = ("item_id",)


@dataclass(frozen=True)
class HybridRouteDecision:
    """一次 Dynamic-Hybrid 路径选择。"""

    selected_path: str
    executed_path: str
    confidence: float

    def with_executed_path(self, path: str) -> "HybridRouteDecision":
        """返回记录实际执行路径的新决策。"""
        if path not in {"llm", "rule"}:
            raise ValueError(f"Unsupported appraisal path: {path}")
        return replace(self, executed_path=path)

    def as_payload(self) -> dict[str, Any]:
        """返回写入 item JSON 的最小字段。"""
        return {
            "selected_path": self.selected_path,
            "executed_path": self.executed_path,
            "confidence": round(self.confidence, 6),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _load_bundle() -> dict[str, Any]:
    if not MODEL_PATH.is_file():
        raise FileNotFoundError(f"Dynamic-Hybrid Router model is missing: {MODEL_PATH}")
    actual_sha256 = _sha256(MODEL_PATH)
    if actual_sha256 != MODEL_SHA256:
        raise ValueError(
            "Dynamic-Hybrid Router model hash mismatch: "
            f"expected {MODEL_SHA256}, got {actual_sha256}"
        )
    bundle = joblib.load(MODEL_PATH)
    required = {
        "pipeline",
        "confidence_margin",
        "static_route",
        "numeric_features",
        "categorical_features",
    }
    if not isinstance(bundle, dict) or set(bundle) != required:
        raise ValueError("Dynamic-Hybrid Router model bundle schema mismatch")
    if tuple(bundle["numeric_features"]) != EXPECTED_NUMERIC_FEATURES:
        raise ValueError("Dynamic-Hybrid Router numeric feature schema mismatch")
    if tuple(bundle["categorical_features"]) != EXPECTED_CATEGORICAL_FEATURES:
        raise ValueError("Dynamic-Hybrid Router categorical feature schema mismatch")
    if not hasattr(bundle["pipeline"], "predict_proba"):
        raise ValueError("Dynamic-Hybrid Router model cannot predict probabilities")
    return bundle


def _character_count(chunks: list[Any]) -> int:
    return sum(len(chunk.title or "") + len(chunk.content or "") for chunk in chunks)


def _divide(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _feature_row(pool: EvidencePool, item_id: str, domain_id: int) -> pd.DataFrame:
    domain_chunks = list(EvidenceIterator(pool.chunks, domain_id))
    routing = WeightedChunkRouter().route(
        spec=get_item_spec(item_id),
        all_chunks=pool.chunks,
        domain_chunks=domain_chunks,
    )
    scores = [float(info.score) for info in routing.routed_chunks]
    selected_character_count = _character_count(list(routing.chunks))
    row = {
        "item_id": item_id,
        "total_chunk_count": len(pool.chunks),
        "total_character_count": _character_count(pool.chunks),
        "domain_chunk_count": routing.domain_chunks,
        "domain_character_count": _character_count(domain_chunks),
        "domain_chunk_ratio": _divide(routing.domain_chunks, len(pool.chunks)),
        "selected_chunk_count": routing.selected_chunks,
        "selected_character_count": selected_character_count,
        "selected_domain_ratio": _divide(routing.selected_chunks, routing.domain_chunks),
        "item_signal_chunk_count": sum(
            any(reason.startswith(("heading:", "phrase:", "term:")) for reason in info.reasons)
            for info in routing.routed_chunks
        ),
        "routing_score_mean": mean(scores) if scores else 0.0,
        "routing_score_max": max(scores, default=0.0),
        "routing_limit_hit": int(
            any(reason.code == "chunk_routing_limit_exceeded" for reason in routing.review_reasons)
        ),
    }
    row.update(
        {
            "selected_total_ratio": _divide(row["selected_chunk_count"], row["total_chunk_count"]),
            "domain_character_ratio": _divide(row["domain_character_count"], row["total_character_count"]),
            "selected_character_ratio": _divide(row["selected_character_count"], row["total_character_count"]),
            "selected_characters_per_chunk": _divide(row["selected_character_count"], row["selected_chunk_count"]),
            "signal_selected_ratio": _divide(row["item_signal_chunk_count"], row["selected_chunk_count"]),
            "routing_score_spread": row["routing_score_max"] - row["routing_score_mean"],
        }
    )
    frame = pd.DataFrame([row])
    if not np.isfinite(frame[list(EXPECTED_NUMERIC_FEATURES)].to_numpy(dtype=float)).all():
        raise ValueError("Dynamic-Hybrid Router produced non-finite features")
    return frame


def select_appraisal_path(
    pool: EvidencePool,
    item_id: str,
    domain_id: int,
) -> HybridRouteDecision:
    """选择一个条目的评价路径，item 17 固定使用 LLM。"""
    if item_id in FIXED_LLM_ITEMS:
        return HybridRouteDecision("llm", "llm", 1.0)

    bundle = _load_bundle()
    frame = _feature_row(pool, item_id, domain_id)
    columns = [*EXPECTED_NUMERIC_FEATURES, *EXPECTED_CATEGORICAL_FEATURES]
    probability = float(bundle["pipeline"].predict_proba(frame[columns])[0, 1])
    if not np.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError(f"Invalid Dynamic-Hybrid Router probability: {probability}")

    static_path = bundle["static_route"].get(item_id)
    if static_path not in {"llm", "rule"}:
        raise ValueError(f"Dynamic-Hybrid Router has no static path for {item_id}")
    margin = float(bundle["confidence_margin"])
    selected_path = static_path
    if static_path == "llm" and probability >= 0.5 + margin:
        selected_path = "rule"
    elif static_path == "rule" and probability < 0.5 - margin:
        selected_path = "llm"
    confidence = probability if selected_path == "rule" else 1.0 - probability
    return HybridRouteDecision(selected_path, selected_path, confidence)
