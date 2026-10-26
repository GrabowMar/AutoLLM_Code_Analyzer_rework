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

    def add(self, job_id, attempt: int, lane: str) -> Entry:
        """Register a stage of ``job_id``; returns the entry, the worker's token for :meth:`remove`."""
        entry = Entry(str(job_id), int(attempt), lane)
        with self._lock:
            self._entries[str(job_id)] = entry
        return entry

    def remove(self, job_id, entry: Entry | None = None) -> None:
        """Drop ``job_id``; with ``entry``, only if it is still that worker's own registration.

        The generation stage of a job may still be unwinding (batch refresh,
        events) after it committed ``eval_queued``, while the evaluation stage
        of the same job was already claimed and registered: the generation
        worker's ``finally`` must not drop the live evaluation, or its lease
        stops being renewed and the lost-job check requeues it.
        """
        with self._lock:
            key = str(job_id)
            if entry is not None and self._entries.get(key) is not entry:
                return
            self._entries.pop(key, None)

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
