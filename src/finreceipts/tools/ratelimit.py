"""A small thread-safe rate limiter (minimum interval between calls)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Allow at most ``rate`` calls per second by spacing calls evenly.

    SEC's fair-access policy asks automated clients to stay at or below
    10 requests/second; we default to 8 to leave head-room.
    """

    def __init__(
        self,
        rate: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / rate
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a call is allowed; return the seconds waited."""
        with self._lock:
            now = self._clock()
            wait = max(0.0, self._next - now)
            if wait:
                self._sleep(wait)
            self._next = max(now, self._next) + self._interval
            return wait
