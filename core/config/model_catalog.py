"""Offline model limits; provider identity is part of a model's catalog key."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def load_catalog() -> dict:
    return json.loads(Path(__file__).with_name("model_catalog.json").read_text(encoding="utf-8"))


def lookup_model_limits(provider: str, model_id: str) -> dict:
    """Suggest only exact matches with unambiguous limits; never alter saved settings."""
    catalog = load_catalog()
    providers = {
        "gemini": {"google"}, "dashscope": {"alibaba", "alibaba-cn"},
        "zhipu": {"zhipuai", "zhipuai-cn"},
    }.get(provider, {provider})
    custom = provider in {"vllm", "openai_compatible"}
    matches = [row for row in catalog["models"] if row["id"] == model_id
               and (custom or row["provider"] in providers)]
    limits = {row["context"] for row in matches}
    optional_limits = {}
    if not custom and len(limits) == 1:
        for key in ("input", "output"):
            values = {row.get(key) for row in matches}
            if len(values) == 1 and None not in values:
                optional_limits[f"max_{key}_tokens"] = next(iter(values))
    return {
        **optional_limits,
        "status": "matched" if len(limits) == 1 else "ambiguous" if matches else "unknown",
        "context_window": next(iter(limits)) if len(limits) == 1 else None,
        "source": catalog["source"], "snapshot_at": catalog["snapshot_at"],
    }
