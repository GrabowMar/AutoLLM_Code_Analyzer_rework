"""The timeout chain from an endpoint's per-call timeout to the harness client (spec §D.3). Pure.

Invariants (unit-tested by Track B):
- ``proxy_wait_s < gateway_upstream_s``
- ``gateway_upstream_s > request_timeout_s + RELAY_ACK_LEASE_S``
- ``harness_client_s >= gateway_upstream_s * (GATEWAY_UPSTREAM_RETRIES + 1)``
  (the harness never cuts the gateway's 504 re-attach chain)
- ``deadline_s <= proxy_wait_s * (GATEWAY_UPSTREAM_RETRIES + 1)``
  (the re-attach chain outlives the request deadline)
"""

from __future__ import annotations

from dataclasses import dataclass

# images/llm-gateway/gateway.py: UPSTREAM_RETRIES = CONFIG.get("upstream_retries", 4).
# Snapshots never set upstream_retries, so the gateway default applies.
GATEWAY_UPSTREAM_RETRIES = 4
# LLMRequest.max_attempts default.
REQUEST_MAX_ATTEMPTS = 3
DEFAULT_REQUEST_TIMEOUT_S = 900


@dataclass(frozen=True)
class Chain:
    request_timeout_s: int  # endpoint, per upstream call (default 900)
    deadline_s: int  # request deadline = request_timeout_s * max_attempts + 600 (3300 by default)
    gateway_upstream_s: int  # gateway per-recv socket timeout = request_timeout_s + 300 (1200)
    proxy_wait_s: int  # non-stream proxy wait cap = gateway_upstream_s - 60 (1140)
    harness_client_s: int  # harness HTTP client timeout = gateway_upstream_s * (UPSTREAM_RETRIES + 1) + 60


def chain(endpoint) -> Chain:
    """The chain for ``endpoint`` (any object with ``request_timeout_s``; None -> defaults)."""
    request_timeout_s = int(getattr(endpoint, "request_timeout_s", None) or DEFAULT_REQUEST_TIMEOUT_S)
    gateway_upstream_s = request_timeout_s + 300
    return Chain(
        request_timeout_s=request_timeout_s,
        deadline_s=request_timeout_s * REQUEST_MAX_ATTEMPTS + 600,
        gateway_upstream_s=gateway_upstream_s,
        proxy_wait_s=gateway_upstream_s - 60,
        harness_client_s=gateway_upstream_s * (GATEWAY_UPSTREAM_RETRIES + 1) + 60,
    )
