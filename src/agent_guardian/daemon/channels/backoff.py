"""Exponential backoff helper (protocol §9.2)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def with_exponential_backoff(
    operation: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[BaseException], bool],
    initial_delay_ms: float = 500,
    max_delay_ms: float = 8000,
    multiplier: float = 2.0,
    jitter_ratio: float = 0.2,
    max_attempts: int = 5,
) -> T:
    delay = initial_delay_ms / 1000.0
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except BaseException as exc:
            last_exc = exc
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            jitter = delay * jitter_ratio * random.random()
            await asyncio.sleep(delay + jitter)
            delay = min(delay * multiplier, max_delay_ms / 1000.0)
    assert last_exc is not None
    raise last_exc
