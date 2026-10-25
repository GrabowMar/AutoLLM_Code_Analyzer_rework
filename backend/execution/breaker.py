"""Per-endpoint circuit breaker (spec §C.7). In-memory, on the leader only.

Three infra failures on one endpoint within five minutes open its circuit for
60 s. While open the scheduler gives the endpoint a job cap of 0 and reports
``circuit_open``. Executor-side failures (``executor_lost``,
``submit_failed``) are not the endpoint's fault and are never recorded.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

FAILURE_THRESHOLD = 3
WINDOW_S = 300.0
OPEN_S = 60.0


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int = FAILURE_THRESHOLD,
        window_s: float = WINDOW_S,
        open_s: float = OPEN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.window_s = window_s
        self.open_s = open_s
        self.clock = clock
        self._lock = threading.Lock()
        self._failures: dict[int, deque[float]] = {}
        self._open_until: dict[int, float] = {}

    def record_failure(self, endpoint_id: int | None) -> bool:
        """Record one infra failure; returns True when this failure opened the circuit."""
        if endpoint_id is None:
            return False
        now = self.clock()
        with self._lock:
            failures = self._failures.setdefault(endpoint_id, deque())
            failures.append(now)
            while failures and failures[0] < now - self.window_s:
                failures.popleft()
            if len(failures) >= self.threshold and not self._is_open(endpoint_id, now):
                self._open_until[endpoint_id] = now + self.open_s
                failures.clear()
                return True
        return False

    def record_success(self, endpoint_id: int | None) -> None:
        if endpoint_id is None:
            return
        with self._lock:
            self._failures.pop(endpoint_id, None)

    def is_open(self, endpoint_id: int | None) -> bool:
        if endpoint_id is None:
            return False
        with self._lock:
            return self._is_open(endpoint_id, self.clock())

    def open_endpoints(self) -> list[int]:
        now = self.clock()
        with self._lock:
            return sorted(ep for ep in self._open_until if self._is_open(ep, now))

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._open_until.clear()

    def _is_open(self, endpoint_id: int, now: float) -> bool:
        until = self._open_until.get(endpoint_id)
        if until is None:
            return False
        if now >= until:
            del self._open_until[endpoint_id]
            return False
        return True


# The executor process's breaker (workers record into it, the scheduler reads it).
breaker = CircuitBreaker()
