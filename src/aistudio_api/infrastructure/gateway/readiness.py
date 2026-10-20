"""Cumulative preparation time shared by every attempt of one generation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from aistudio_api.config import settings
from aistudio_api.domain.errors import PreparationTimeout
from aistudio_api.infrastructure.diagnostics.store import get_store


@dataclass
class PreparationBudget:
    remaining: float


_budget: ContextVar[PreparationBudget | None] = ContextVar(
    "preparation_budget", default=None
)
_charging: ContextVar[bool] = ContextVar("charging_preparation", default=False)


@contextmanager
def preparation_budget(seconds: float | None = None) -> Iterator[PreparationBudget]:
    """Allocate once per generation; time spent generating is not charged."""
    budget = PreparationBudget(
        float(seconds if seconds is not None else settings.browser_ready_timeout)
    )
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)


@asynccontextmanager
async def preparing() -> AsyncIterator[None]:
    """Bound and charge an outer preparation operation, including recovery."""
    if _charging.get():
        yield
        return
    budget = _budget.get() or PreparationBudget(float(settings.browser_ready_timeout))
    if budget.remaining <= 0:
        raise PreparationTimeout("Browser preparation budget exhausted")
    token = _charging.set(True)
    started = time.monotonic()
    timeout = asyncio.timeout(budget.remaining)
    try:
        try:
            async with timeout:
                yield
        except TimeoutError as exc:
            if timeout.expired():
                raise PreparationTimeout(
                    "Browser preparation budget exhausted"
                ) from exc
            raise
    finally:
        budget.remaining = max(0.0, budget.remaining - (time.monotonic() - started))
        _charging.reset(token)
        get_store().emit(
            "browser_preparation_wait",
            elapsed_seconds=round(time.monotonic() - started, 3),
            remaining_seconds=round(budget.remaining, 3),
        )
