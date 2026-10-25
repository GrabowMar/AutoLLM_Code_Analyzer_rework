"""Database connection hygiene for long waits (spec §C.2, §E.1).

Every wait longer than a few seconds (LLM waits, relay long-polls, proxy
handlers) drops its DB connection first, so idle threads and open polls
never pin a Postgres connection. Inside an atomic block (tests wrap each test
in one; a caller may too) the connection is left alone: closing it there
would break the surrounding transaction.
"""

from __future__ import annotations

from django.db import connection


def close_db() -> None:
    """Close this thread's default DB connection unless a transaction is open on it."""
    if connection.in_atomic_block:
        return
    connection.close()
