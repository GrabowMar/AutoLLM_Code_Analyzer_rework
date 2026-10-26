"""Cross-process locks for the analysis engine (owner: executor stage).

Postgres session-level advisory locks held on the calling thread's DB
connection: they are visible to every process, and the database releases
them by itself when the holder's connection dies, so a crashed worker can
never leak a run slot or an image-build lock. On other databases (never in
production) an in-process registry stands in.
"""

from __future__ import annotations

import threading

from django.db import DEFAULT_DB_ALIAS
from django.db import connections

# First key of every lock ("ANLY"); the second key names the lock.
NAMESPACE = 0x414E4C59
# Run slots use keys SLOT_BASE .. SLOT_BASE + ANALYSIS_MAX_CONCURRENT_RUNS - 1.
SLOT_BASE = 0
IMAGE_BUILD_KEY = 1_000_000

_local_lock = threading.Lock()
_local_held: dict[int, int] = {}


def _is_postgres(using: str) -> bool:
    return connections[using].vendor == "postgresql"


def try_lock(key: int, *, using: str = DEFAULT_DB_ALIAS) -> bool:
    """Take lock *key* without waiting; True when it is now held by this connection.

    Postgres advisory locks are re-entrant per session: a second
    ``try_lock`` of a key this connection already holds succeeds and needs a
    second :func:`unlock`.
    """
    if not _is_postgres(using):
        with _local_lock:
            if _local_held.get(key):
                return False
            _local_held[key] = 1
            return True
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s, %s)", [NAMESPACE, int(key)])
        return bool(cursor.fetchone()[0])


def unlock(key: int, *, using: str = DEFAULT_DB_ALIAS) -> bool:
    if not _is_postgres(using):
        with _local_lock:
            return _local_held.pop(key, None) is not None
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s, %s)", [NAMESPACE, int(key)])
        return bool(cursor.fetchone()[0])


def held_keys(first: int, last: int, *, using: str = DEFAULT_DB_ALIAS) -> set[int]:
    """Keys in [first, last] currently held by any session (read-only; takes nothing)."""
    if not _is_postgres(using):
        with _local_lock:
            return {k for k in _local_held if first <= k <= last}
    with connections[using].cursor() as cursor:
        # Two-key advisory locks appear with classid = key1, objid = key2, objsubid = 2.
        # pg_locks spans the whole server; advisory locks are per database.
        cursor.execute(
            "SELECT DISTINCT objid FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
            "AND objsubid = 2 AND classid = %s AND objid BETWEEN %s AND %s",
            [NAMESPACE, int(first), int(last)],
        )
        return {int(row[0]) for row in cursor.fetchall()}


def is_held(key: int, *, using: str = DEFAULT_DB_ALIAS) -> bool:
    return key in held_keys(key, key, using=using)
