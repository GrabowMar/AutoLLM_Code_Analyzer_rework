"""Direct-endpoint pumps (spec §C.2, §D.7, §D.8). Contract stub → Track B.

One thread per in-flight slot of each enabled direct non-OpenRouter endpoint
(Σ ``max_concurrency`` ≤ 32): claims ``LLMRequest`` rows under the
per-endpoint advisory lock (``pump_owner = executor_id``), calls ``base_url``
through ``inference.upstream`` and stores the result exactly like the relay's
result endpoint. Also runs inventory every 120 s (and on
``inventory_resync_requested``).
"""

from __future__ import annotations

import threading


def run_direct_pumps(stop: threading.Event, executor_id: str) -> None:
    """Run the pump threads until ``stop`` is set; picks up endpoint changes (enable/disable/caps) as they happen."""
    msg = "inference.pump.run_direct_pumps: Track B"
    raise NotImplementedError(msg)
