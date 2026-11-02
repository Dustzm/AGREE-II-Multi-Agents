"""Greedy batch planning that never sends a request larger than the window plan."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypeVar

from core.llm.context import ContextBudgetExceeded
from core.llm.window import WindowPlan

Unit = TypeVar("Unit")


@dataclass(frozen=True)
class BatchPlan:
    """Batches of semantic units that each fit the planned input budget."""

    batches: tuple[tuple[Unit, ...], ...]

    @property
    def batch_count(self) -> int:
        return len(self.batches)


_MAX_SPLIT_DEPTH = 24


def _split_recursive(
    unit: Unit,
    measure: Callable[[Sequence[Unit]], int],
    plan: WindowPlan,
    split_unit: Callable[[Unit], Sequence[Unit] | None],
    depth: int = 0,
) -> list[Unit]:
    if depth > _MAX_SPLIT_DEPTH:
        raise ContextBudgetExceeded(
            f"split recursion exceeded depth {_MAX_SPLIT_DEPTH}; "
            f"input_budget={plan.input_budget} ({plan.describe()})"
        )
    if measure((unit,)) <= plan.input_budget:
        return [unit]
    parts = split_unit(unit)
    if not parts or len(parts) < 2:
        raise ContextBudgetExceeded(
            f"single unit exceeds input_budget={plan.input_budget} ({plan.describe()}); "
            "split the unit further or raise the deployed context window"
        )
    result: list[Unit] = []
    for part in parts:
        result.extend(_split_recursive(part, measure, plan, split_unit, depth + 1))
    return result


def plan_batches(
    units: Iterable[Unit],
    measure: Callable[[Sequence[Unit]], int],
    plan: WindowPlan,
    *,
    split_unit: Callable[[Unit], Sequence[Unit] | None] | None = None,
    label: str = "batch",
) -> BatchPlan:
    """Group units greedily; oversized units are split so no request overflows."""
    prepared: list[Unit] = []
    for unit in units:
        if measure((unit,)) <= plan.input_budget:
            prepared.append(unit)
        elif split_unit is not None:
            prepared.extend(_split_recursive(unit, measure, plan, split_unit))
        else:
            raise ContextBudgetExceeded(
                f"{label}: one unit exceeds input_budget={plan.input_budget} ({plan.describe()})"
            )

    batches: list[tuple[Unit, ...]] = []
    current: list[Unit] = []
    for unit in prepared:
        candidate = current + [unit]
        if current and measure(candidate) > plan.input_budget:
            batches.append(tuple(current))
            current = [unit]
        else:
            current = candidate
    if current:
        batches.append(tuple(current))
    return BatchPlan(tuple(batches))
