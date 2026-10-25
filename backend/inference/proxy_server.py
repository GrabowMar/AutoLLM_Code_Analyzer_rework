"""Internal LLM proxy for local agent runs (spec §D.3). Contract stub → Track B.

A stdlib ``ThreadingHTTPServer`` thread inside the executor, bound only to
``settings.LLM_PROXY_BIND`` after leadership is won. Routes:
``POST /v1/chat/completions``, ``GET /v1/models``, ``GET /healthz``.
Auth: ``Bearer`` = ``gateway_wiring.proxy_token(job)`` re-checked against the
job's live lease.
"""

from __future__ import annotations

import threading


def serve(stop: threading.Event) -> None:
    """Serve until ``stop`` is set; then stop accepting and return (every handler closes its DB connection)."""
    msg = "inference.proxy_server.serve: Track B"
    raise NotImplementedError(msg)
