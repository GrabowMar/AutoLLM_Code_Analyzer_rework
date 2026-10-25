"""The durable LLM request queue (spec §D.2 request flow, §E.2, §C.7 step 3). Contract stub → Track B.

Redis only rings doorbells: ``inference:wake:<endpoint_id>`` (LPUSH + LTRIM 0 0
+ EXPIRE 60) and ``inference:done:<rid>`` (``SET … EX 600`` + ``PUBLISH
inference:done <rid>``). All state lives in ``LLMRequest`` rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.inference.access import Access
    from backend.inference.models import LLMRequest
    from backend.inference.models import RelaySession


class WaitTimeout(Exception):  # noqa: N818 — contract name (§D.2)
    """``wait_result`` hit ``max_wait`` while the request is still queued or leased (the request is untouched)."""


def enqueue(
    access: Access,
    path: str,
    body: dict[str, Any] | None,
    job=None,
    source: str = "scaffolding",
    priority: int = 0,
    digest: str = "",
    dedupe_key: str = "",
    *,
    method: str = "POST",
    model_name: str | None = None,
) -> uuid.UUID:
    """INSERT an ``LLMRequest(status=queued)`` for ``access.endpoint`` and ring its doorbell; returns the id.

    ``deadline_at = now + request_timeout_s * max_attempts + 600``.
    ``job_attempt`` is copied from ``job.attempt``. ``model_name`` defaults
    to ``access.served_model_name``.
    """
    msg = "inference.requests.enqueue: Track B"
    raise NotImplementedError(msg)


def wait_result(
    rid: uuid.UUID | str,
    deadline: datetime | None = None,
    cancel_check: Callable[[], bool] | None = None,
    max_wait: float | None = None,
) -> LLMRequest:
    """Block until request ``rid`` is ``done`` / ``failed`` / ``cancelled`` / ``expired`` and return the row.

    Closes the DB connection first, then loops on the ``inference:done:<rid>``
    key (5 s slices on the pub/sub channel), re-reading the row each time.
    ``cancel_check()`` True cancels the request; ``max_wait`` raises
    :class:`WaitTimeout` without touching it; passing ``deadline`` marks it
    ``expired``. Sets ``delivered_at`` on first delivery (``response`` is kept).
    """
    msg = "inference.requests.wait_result: Track B"
    raise NotImplementedError(msg)


def cancel_for_jobs(job_ids: Iterable[Any], *, attempt: int | None = None) -> int:
    """Cancel queued/leased requests of these jobs (optionally one attempt).

    Signals their waiters and rings the endpoint doorbells; returns the count.
    """
    msg = "inference.requests.cancel_for_jobs: Track B"
    raise NotImplementedError(msg)


def requeue_expired(now: datetime | None = None) -> int:
    """Reconciler step 3: expired ``leased`` rows → ``queued`` (``attempt+1``) or ``failed``/``lease_expired``.

    Returns the count.
    """
    msg = "inference.requests.requeue_expired: Track B"
    raise NotImplementedError(msg)


def requeue_session(session: RelaySession, *, bump_attempt: bool = False, reason: str = "") -> int:
    """Requeue every request leased to ``session`` (session ended, token revoked, ``bye``).

    Un-acked leases never bump ``attempt``.
    """
    msg = "inference.requests.requeue_session: Track B"
    raise NotImplementedError(msg)


def purge_old(now: datetime | None = None) -> int:
    """Daily: clear ``body``/``response`` of rows older than ``LLM_REQUEST_RETENTION_DAYS``.

    Metrics columns are kept. Returns the count.
    """
    msg = "inference.requests.purge_old: Track B"
    raise NotImplementedError(msg)
