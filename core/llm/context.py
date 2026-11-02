"""Task-scoped model settings and conservative whole-request budgeting."""
from contextvars import ContextVar
from dataclasses import dataclass, replace
import json
import logging

from core.config import get_settings
from core.llm.tokenizer import count_messages_tokens, count_text_tokens

logger = logging.getLogger(__name__)
UNKNOWN_IMAGE_TOKENS = 16384
_TASK_LLM = ContextVar("task_llm", default=None)


def has_task_llm_settings() -> bool:
    return _TASK_LLM.get() is not None


def current_llm_settings():
    return _TASK_LLM.get() or get_settings().llm


def bind_llm_settings():
    """Copy settings once; execution runtime propagates this context to workers."""
    return _TASK_LLM.set(get_settings().llm.model_copy(deep=True))


def reset_llm_settings(token):
    _TASK_LLM.reset(token)


class ContextBudgetExceeded(RuntimeError):
    """The complete request cannot fit; callers must split semantic units."""


class ContextWindowNotConfigured(ContextBudgetExceeded):
    """No deployed context window is known, so no request can be budgeted."""


@dataclass(frozen=True)
class RequestBudget:
    context_window: int | None
    model: str
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None

    @classmethod
    def from_settings(cls, llm, model: str):
        from core.config.model_catalog import lookup_model_limits
        from core.agents.base.model_config import get_provider_config_for
        provider = get_provider_config_for(llm.provider)
        official_endpoint = not llm.base_url or llm.base_url.rstrip("/") == provider.get("default_base_url", "").rstrip("/")
        limits = lookup_model_limits(llm.provider, llm.model_name) if official_endpoint else {}
        context_window = llm.context_window
        if not context_window:
            # No explicit window: derive from the catalog's input+output limits when known.
            max_input = limits.get("max_input_tokens")
            if max_input:
                context_window = int(max_input) + int(limits.get("max_output_tokens") or 0)
        return cls(context_window, model, limits.get("max_input_tokens"), limits.get("max_output_tokens"))

    def output_limit(self, requested: int) -> int:
        return min(requested, self.max_output_tokens) if self.max_output_tokens else requested

    def estimate(self, messages, extra=None) -> int:
        """Token estimate for the complete request; unknown VLM images get headroom."""
        cleaned_messages, images = _strip_images(messages)
        tokens = count_messages_tokens(cleaned_messages)
        if extra is not None:
            cleaned_extra, extra_images = _strip_images(extra)
            tokens += count_text_tokens("\n" + json.dumps(cleaned_extra, ensure_ascii=False))
            images += extra_images
        return tokens + images * UNKNOWN_IMAGE_TOKENS + 64

    def require_configured(self):
        if not self.context_window:
            raise ContextWindowNotConfigured("请先配置 LLM_CONTEXT_WINDOW 或在管理台填写上下文窗口")

    def plan_window(self, requested_output: int):
        from core.llm.window import plan_window

        return plan_window(self.context_window, requested_output, max_output_tokens=self.max_output_tokens)

    def check(self, messages, max_tokens: int, *, extra=None, stage="") -> int:
        plan = self.plan_window(max_tokens)
        estimate = self.estimate(messages, extra)
        logger.debug("LLM budget stage=%s model=%s input_estimate=%s output=%s plan=%s",
                     stage, self.model, estimate, plan.output_reserve, plan.describe())
        if not plan.fits(estimate) or (self.max_input_tokens is not None and estimate > self.max_input_tokens):
            raise ContextBudgetExceeded(
                f"{stage}: estimated input={estimate}, output={plan.output_reserve}, reserve={plan.safety_margin}, "
                f"context_window={plan.context_window}; split input or increase deployed context window")
        return estimate


def _strip_images(payload):
    """Replace base64 image payloads with a placeholder and count them separately."""
    images = 0

    def walk(value):
        nonlocal images
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                images += 1
                return {"type": "image"}
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(payload), images


def is_context_error(error: Exception) -> bool:
    return isinstance(error, ContextBudgetExceeded) or type(error).__name__ == "ContextWindowExceededError"


def split_chunk(chunk):
    """Split long source text without dropping characters or changing source IDs."""
    text = chunk.content
    if len(text) < 2:
        raise ContextBudgetExceeded("Fixed prompt or indivisible source exceeds context window")
    midpoint = len(text) // 2
    boundary = text.rfind("\n", len(text) // 4, midpoint + 1)
    midpoint = boundary + 1 if boundary >= 0 else midpoint
    return replace(chunk, content=text[:midpoint]), replace(chunk, content=text[midpoint:])
