"""Every inequality of the timeout chain (spec §D.3), for the whole configurable range."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.conf import settings

from backend.inference import timeouts
from backend.inference.timeouts import GATEWAY_UPSTREAM_RETRIES
from backend.inference.timeouts import chain


@pytest.mark.parametrize("request_timeout_s", [30, 60, 300, 900, 1800, 3600, 7200])
@pytest.mark.parametrize("max_attempts", [1, 2, 3, 4, 5])
def test_chain_invariants(monkeypatch, request_timeout_s, max_attempts):
    monkeypatch.setattr(timeouts, "REQUEST_MAX_ATTEMPTS", max_attempts)
    c = chain(SimpleNamespace(request_timeout_s=request_timeout_s))
    links = GATEWAY_UPSTREAM_RETRIES + 1
    assert c.proxy_wait_s < c.gateway_upstream_s
    assert c.gateway_upstream_s > c.request_timeout_s + settings.RELAY_ACK_LEASE_S
    # The harness never cuts the gateway's 504 re-attach chain ...
    assert c.harness_client_s >= c.gateway_upstream_s * links
    # ... and that chain outlives the request deadline (default attempts).
    if max_attempts <= 3:
        assert c.deadline_s <= c.proxy_wait_s * links


def test_defaults_match_the_spec():
    c = chain(SimpleNamespace(request_timeout_s=900))
    assert (c.deadline_s, c.gateway_upstream_s, c.proxy_wait_s, c.harness_client_s) == (3300, 1200, 1140, 6060)


def test_missing_endpoint_uses_defaults():
    assert chain(None).request_timeout_s == timeouts.DEFAULT_REQUEST_TIMEOUT_S
