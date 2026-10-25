"""In-process job registry (spec §C.2): which jobs a live worker thread of this executor is running.

A worker adds its job as its first statement and removes it in its
``finally``. The heartbeat renews leases **only** for registered ids, and the
leader's lost-job check requeues jobs it owns (``lease_owner = me``) that are
not registered: a job that was claimed but never reached a worker (submit
raised, pool rejected, thread died before start) therefore stops being renewed
and comes back after the grace period instead of holding a slot forever.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from dataclasses import field


@dataclass(frozen=True)
class Entry:
    job_id: str
    attempt: int
    lane: str  # "sandbox" | "llm" | "eval"
    started: float = field(default_factory=time.monotonic)


class JobRegistry:
    """Thread-safe ``{job_id: Entry}``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, Entry] = {}

    def add(self, job_id, attempt: int, lane: str) -> None:
        with self._lock:
            self._entries[str(job_id)] = Entry(str(job_id), int(attempt), lane)

    def remove(self, job_id) -> None:
        with self._lock:
            self._entries.pop(str(job_id), None)

    def __contains__(self, job_id) -> bool:
        with self._lock:
            return str(job_id) in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._entries)

    def entries(self) -> list[Entry]:
        with self._lock:
            return list(self._entries.values())

    def count(self, lane: str) -> int:
        with self._lock:
            return sum(1 for entry in self._entries.values() if entry.lane == lane)
