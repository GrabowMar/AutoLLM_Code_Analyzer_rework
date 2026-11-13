"""Overlay for the executor's local LLM proxy upstream (``GatewayConfig.upstream.kind = "local_proxy"``).

The base ``gateway.py`` owns every policy, budget, retry and log rule; this overlay only registers the
``local_proxy`` upstream adapter, so local endpoints take the same canonical path as OpenRouter runs:

* transport: plain HTTP or HTTPS by the upstream URL's scheme, the per-run proxy token
  (``LLMLAB_UPSTREAM_TOKEN``) as bearer, none of the OpenRouter attribution headers;
* body: the OpenRouter-only fields are dropped (``usage``, ``provider``, ``session_id``, the ``:floor``
  suffix, ``cache_control`` markers), since the proxy builds the endpoint's own body; assistant
  ``reasoning_details`` (replayed by the translators) become plain ``reasoning`` text, the only
  reasoning a local endpoint reads back (signatures mean nothing to it);
* headers for the proxy's budget backstop (``budget_rules``, local proxy parity):
  ``X-LLMLab-Request-Class`` and ``X-LLMLab-Call-Index`` (a gateway retry of the same call keeps its
  index, so the proxy never reserves twice for one call);
* retries: no 500 (an Ollama 500 is usually an out-of-memory model load, not transient) and no 408 (the
  proxy owns timeouts); 504 is always retried, because a proxy 504 means "still waiting, re-attach".

Claude Code, Codex and Gemini CLI reach local endpoints through the ``translate`` mode of their fronts
(canonical chat upstream); passthrough is OpenRouter-only.
"""

# Standalone script baked into its own image (no package).
# ruff: noqa: INP001

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gateway as g
from protocols.common import strip_cache_control
from protocols.translation import details_text

if TYPE_CHECKING:
    from protocols import budget_rules

OPENROUTER_ONLY = ("usage", "provider", "session_id", "models", "route", "transforms")
NO_RETRY = frozenset({500, 408})


class LocalProxyUpstream(g.OpenRouterUpstream):
    kind = "local_proxy"

    def retry_statuses(self) -> set[int]:
        return (self.policy.retry_statuses() - NO_RETRY) | {504}

    def prepare(self, body: dict[str, Any]) -> dict[str, Any]:
        for key in OPENROUTER_ONLY:
            body.pop(key, None)
        model = body.get("model")
        if isinstance(model, str) and model.endswith(":floor"):
            body["model"] = model.removesuffix(":floor")
        strip_cache_control(body)
        for message in body.get("messages") or []:
            if isinstance(message, dict) and "reasoning_details" in message:
                details = message.pop("reasoning_details") or []
                text = details_text(d for d in details if isinstance(d, dict))
                if text and not message.get("reasoning"):
                    message["reasoning"] = text
        return body

    def headers(self, *, stream: bool, native: Mapping[str, str], reservation: budget_rules.Reservation) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "X-LLMLab-Request-Class": reservation.request_class,
            "X-LLMLab-Call-Index": str(reservation.index),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


g.UPSTREAMS["local_proxy"] = LocalProxyUpstream

if __name__ == "__main__":
    sys.exit(g.main())
