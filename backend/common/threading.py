"""Reusable threading helpers for in-process background work."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Callable


def dispatch_in_thread(
    target: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> threading.Thread:
    """Run *target* on a started daemon thread and return the thread.

    The thread is started before being returned. Callers that need to capture
    DB IDs or open new database connections should do so inside *target*.
    """
    thread = threading.Thread(
        target=target,
        args=args,
        kwargs=kwargs,
        daemon=True,
    )
    thread.start()
    return thread
