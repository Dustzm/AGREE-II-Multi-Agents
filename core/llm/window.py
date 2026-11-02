"""Turn a deployed context window into an explicit input/output plan."""
from __future__ import annotations

import math
from dataclasses import dataclass


DEFAULT_MARGIN_RATIO = 0.03
MIN_MARGIN = 256


@dataclass(frozen=True)
class WindowPlan:
    """Budget split for one request: output reserve, safety margin, input ceiling."""

    context_window: int
    requested_output: int
    output_reserve: int
    safety_margin: int
    input_budget: int

    def fits(self, input_tokens: int) -> bool:
        return input_tokens <= self.input_budget

    def describe(self) -> str:
        return (
            f"context_window={self.context_window} input_budget={self.input_budget} "
            f"output={self.output_reserve} margin={self.safety_margin}"
        )


def plan_window(
    context_window: int | None,
    requested_output: int,
    *,
    max_output_tokens: int | None = None,
    margin_ratio: float = DEFAULT_MARGIN_RATIO,
) -> WindowPlan:
    """Compute the largest safe input budget; fail loudly when the window is unusable."""
    from core.llm.context import ContextBudgetExceeded, ContextWindowNotConfigured

    if not context_window:
        raise ContextWindowNotConfigured("请先配置 LLM_CONTEXT_WINDOW 或在管理台填写上下文窗口")
    output = min(requested_output, max_output_tokens) if max_output_tokens else requested_output
    margin = max(MIN_MARGIN, math.ceil(context_window * margin_ratio))
    input_budget = context_window - output - margin
    if input_budget <= 0:
        raise ContextBudgetExceeded(
            f"context_window={context_window} cannot host output={output} margin={margin}; "
            "lower max output tokens or raise the deployed context window"
        )
    return WindowPlan(
        context_window=context_window,
        requested_output=requested_output,
        output_reserve=output,
        safety_margin=margin,
        input_budget=input_budget,
    )
