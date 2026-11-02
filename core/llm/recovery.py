"""One bounded recovery allowance for an entire appraisal item, and the only retry loop."""
import asyncio
import logging
import random
import time
from dataclasses import dataclass

from core.config import app_setting
from core.llm.failures import BudgetSnapshot, FailureKind
from core.utils.cancellation import CancellationRequested
from core.utils.llm_errors import InvalidModelJSON, is_retryable_llm_error

logger = logging.getLogger(__name__)


def _never_cancelled() -> None:
    return None


def backoff_seconds(retry: int) -> float:
    """Exponential backoff with jitter; retry numbers start at 1."""
    return 2 ** retry + random.uniform(0, 0.3)


def cancellable_delay(seconds, check_cancelled):
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


async def cancellable_sleep(seconds, check_cancelled):
    deadline = time.monotonic() + seconds
    while True:
        check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.1, remaining))


@dataclass(frozen=True)
class LedgerQuotas:
    """Per-item recovery quotas keyed by failure kind plus a shared total ceiling."""

    transport: int = 3
    truncation: int = 3
    repair: int = 6
    # Per-stage repair cap: extraction repairs must not starve verification/explanation.
    repair_stage: int = 2
    total: int = 8


def quotas_from_settings() -> LedgerQuotas:
    """Read configured quotas, tolerating settings objects that predate the fields."""
    defaults = LedgerQuotas()
    return LedgerQuotas(
        transport=app_setting("llm_retry_quota_transport", defaults.transport),
        truncation=app_setting("llm_retry_quota_truncation", defaults.truncation),
        repair=app_setting("llm_retry_quota_repair", defaults.repair),
        repair_stage=app_setting("llm_retry_quota_repair_stage", defaults.repair_stage),
        total=app_setting("llm_retry_quota_total", defaults.total),
    )


def _bucket_for(kind: FailureKind) -> str:
    if kind is FailureKind.TRUNCATED:
        return "truncation"
    if kind is FailureKind.TRANSPORT:
        return "transport"
    return "repair"


def _stage_group(stage: str) -> str:
    """Group repair stages, e.g. RuleObjectExtractionRepair:item_2#2 -> extraction repair."""
    return (stage or "repair").split(":", 1)[0].strip() or "repair"


class RecoveryLedger:
    """Single source of truth for one appraisal item's recovery allowances."""

    def __init__(self, quotas: LedgerQuotas | None = None):
        self.quotas = quotas or quotas_from_settings()
        self._used = {"transport": 0, "truncation": 0, "repair": 0}
        self._stage_used: dict[str, int] = {}
        self._total_used = 0

    def reserve(self, kind, stage: str = "") -> int:
        """Spend one allowance of the failure kind's bucket or raise InvalidModelJSON."""
        bucket = _bucket_for(kind)
        quota = getattr(self.quotas, bucket)
        group = _stage_group(stage) if bucket == "repair" else ""
        if bucket == "repair" and group and self._stage_used.get(group, 0) >= self.quotas.repair_stage:
            raise InvalidModelJSON(f"{stage or group}: repair recovery allowance exhausted")
        if self._used[bucket] >= quota or self._total_used >= self.quotas.total:
            raise InvalidModelJSON(f"{stage or 'llm'}: {bucket} recovery allowance exhausted")
        self._used[bucket] += 1
        self._total_used += 1
        if group:
            self._stage_used[group] = self._stage_used.get(group, 0) + 1
        return self._used[bucket]

    def used(self, kind) -> int:
        return self._used[_bucket_for(kind)]

    def retries_left(self, kind) -> int:
        bucket = _bucket_for(kind)
        return max(0, min(getattr(self.quotas, bucket) - self._used[bucket], self.quotas.total - self._total_used))

    @property
    def total_remaining(self) -> int:
        return max(0, self.quotas.total - self._total_used)

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(remaining=self.total_remaining, consumed=self._total_used)


class RecoveryBudget:
    """Legacy facade kept for existing call sites; transport retries plus typed buckets."""

    def __init__(self, remaining: int | None = None, quotas: LedgerQuotas | None = None):
        base = quotas or quotas_from_settings()
        self.ledger = RecoveryLedger(
            LedgerQuotas(
                transport=base.transport if remaining is None else remaining,
                truncation=base.truncation,
                repair=base.repair,
                repair_stage=base.repair_stage,
                total=base.total,
            )
        )
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.ledger.retries_left(FailureKind.TRANSPORT)

    def consume(self, error: Exception) -> int:
        """Spend one transport allowance or re-raise the error; returns the retry number."""
        retry = self.ledger.reserve(FailureKind.TRANSPORT, str(error)[:120])
        self.used = retry
        return retry

    def consume_truncation(self, error: Exception) -> int:
        """Spend one truncation allowance (batch splitting) instead of a transport retry."""
        return self.ledger.reserve(FailureKind.TRUNCATED, str(error)[:120])

    def note_repair(self, stage: str) -> None:
        """A schema repair request spends the repair bucket, not the transport bucket."""
        self.ledger.reserve(FailureKind.SEMANTIC_SPAN, stage)

    def snapshot(self) -> BudgetSnapshot:
        return self.ledger.snapshot()

    def _should_retry(self, error, retryable, before_retry) -> bool:
        if not retryable(error) or self.remaining == 0:
            return False
        if before_retry is not None:
            before_retry(error)
        return True

    def run(self, call, *, check_cancelled=_never_cancelled, retryable=is_retryable_llm_error, before_retry=None):
        """Replay only the failed call while retryable errors and allowance remain."""
        while True:
            check_cancelled()
            try:
                return call()
            except CancellationRequested:
                raise
            except Exception as error:
                if not self._should_retry(error, retryable, before_retry):
                    raise
                retry = self.consume(error)
                logger.warning("模型调用重试: retry=%s error=%s", retry, type(error).__name__)
                cancellable_delay(backoff_seconds(retry), check_cancelled)

    async def arun(self, call, *, check_cancelled=_never_cancelled, retryable=is_retryable_llm_error, before_retry=None):
        """Async counterpart of run(); call returns an awaitable."""
        while True:
            check_cancelled()
            try:
                return await call()
            except CancellationRequested:
                raise
            except Exception as error:
                if not self._should_retry(error, retryable, before_retry):
                    raise
                retry = self.consume(error)
                logger.warning("模型调用重试: retry=%s error=%s", retry, type(error).__name__)
                await cancellable_sleep(backoff_seconds(retry), check_cancelled)


def needs_smaller_batch(client, error: Exception, *, charge: bool = True) -> bool:
    """Local preflight splitting is free; a failed remote request consumes recovery."""
    from core.llm.context import ContextBudgetExceeded, is_context_error
    from core.llm.json_output import TruncatedModelOutput
    if not is_context_error(error) and not isinstance(error, TruncatedModelOutput):
        return False
    budget = getattr(client, "budget", None)
    if budget is not None:
        budget.require_configured()
    if not charge:
        return True
    if not isinstance(error, ContextBudgetExceeded):
        recovery = getattr(client, "recovery", None)
        if recovery is not None:
            consumer = getattr(recovery, "consume_truncation", None)
            if isinstance(error, TruncatedModelOutput) and consumer is not None:
                consumer(error)
            else:
                recovery.consume(error)
    return True


def split_on_overflow(
    items,
    run,
    merge,
    *,
    client,
    split_single=None,
    before_split=None,
    charge_overflow: bool = True,
    max_splits: int | None = None,
    _state: dict | None = None,
):
    """Run one batch; on context overflow or truncated output, halve it and merge the parts in order.

    A single item is divided only when split_single is given. merge receives
    [(offset, result), ...], where offset is the part's first index in this batch.

    ``charge_overflow=False`` turns splitting into a free structural retreat: the
    caller (e.g. rule-engine extraction) then bounds the tree with ``max_splits``
    instead of the truncation allowance.
    """
    try:
        return run(items)
    except Exception as error:
        divisible = len(items) > 1 or (len(items) == 1 and split_single is not None)
        if not divisible or not needs_smaller_batch(client, error, charge=charge_overflow):
            raise
        state = _state if _state is not None else {"splits": 0}
        state["splits"] += 1
        if max_splits is not None and state["splits"] > max_splits:
            from core.llm.json_output import TruncatedModelOutput
            raise TruncatedModelOutput(
                f"Truncation split budget exhausted after {max_splits} splits",
            ) from error
        if before_split is not None:
            before_split()
        if len(items) > 1:
            midpoint = len(items) // 2
            parts = [(0, items[:midpoint]), (midpoint, items[midpoint:])]
        else:
            parts = [(0, [piece]) for piece in split_single(items[0])]
        return merge([
            (offset, split_on_overflow(
                part, run, merge, client=client,
                split_single=split_single, before_split=before_split,
                charge_overflow=charge_overflow, max_splits=max_splits, _state=state,
            ))
            for offset, part in parts
        ])


def is_quota_exhausted(error: BaseException) -> bool:
    """True when a recovery-allowance guard refused to spend a bucket slot."""
    return isinstance(error, InvalidModelJSON) and "allowance exhausted" in str(error)


def try_note_repair(client, stage: str) -> bool:
    """Spend one repair allowance; return False when the quota is already exhausted.

    A repair quota running out is not a failure of the current stage: the caller
    keeps whatever it has already validated and records ``repair_failed``. Only
    non-quota errors are re-raised.
    """
    recovery = getattr(client, "recovery", None)
    if recovery is None:
        return True
    try:
        recovery.note_repair(stage)
    except Exception as error:  # noqa: BLE001 - only quota errors are swallowed
        if not is_quota_exhausted(error):
            raise
        logger.warning("修复配额已耗尽，跳过本轮定向修复: stage=%s error=%s", stage, error)
        return False
    return True
