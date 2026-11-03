"""Live spend (4.5, 4.7): state.json deltas, proxy usage, SSE spend and budget alerts, the poll interval."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.generation.budget import ledger
from backend.generation.budget import live
from backend.generation.contracts.events import parse_run_event
from backend.generation.contracts.events import parse_study_event
from backend.generation.models import GenerationRun
from backend.generation.models import SpendLedgerEntry
from backend.generation.testing.fake_gateway import gateway_state
from backend.generation.tests.wp11_budget.conftest import attach_manifest
from backend.generation.tests.wp11_budget.conftest import make_study

pytestmark = pytest.mark.django_db
D = Decimal


def _run(user, tick, **kw):
    make_study(user, n_conditions=1, n_units=1, repeats=1, **kw)
    return GenerationRun.objects.get(pk=tick(4).claimed[0].run_id)


def test_poll_writes_deltas_once(user, system_ep, tick, events):
    run = _run(user, tick)
    first = live.poll(
        run,
        state=gateway_state(cost=0.02, list_cost=0.02, prompt_tokens=1000, requests_by_class={"main": 2}),
    )
    assert (first["delta_usd"], first["seq"]) == (0.02, 1)
    same = live.poll(
        run,
        state=gateway_state(cost=0.02, list_cost=0.02, prompt_tokens=1000, requests_by_class={"main": 2}),
    )
    assert same["delta_usd"] == 0.0
    second = live.poll(
        run,
        state=gateway_state(cost=0.05, list_cost=0.05, prompt_tokens=2500, requests_by_class={"main": 5}),
    )
    assert second["delta_usd"] == pytest.approx(0.03)
    assert second["seq"] == 2
    rows = SpendLedgerEntry.objects.filter(run=run, source="gateway_live").order_by("id")
    assert [r.amount_usd for r in rows] == [D("0.02"), D("0.03")]
    assert [r.requests for r in rows] == [2, 3]
    spend = events.of("study:", "spend")
    assert len(spend) == 2
    for payload in spend:
        parse_study_event(payload)
    usage = events.of("run:", "usage")
    assert usage
    assert parse_run_event(usage[-1]).cost_usd == pytest.approx(0.05)


def test_poll_with_a_reader(user, system_ep, tick):
    run = _run(user, tick)
    out = live.poll(run, read=lambda _r: gateway_state(cost=0.01, list_cost=0.01))
    assert out["delta_usd"] == 0.01
    with pytest.raises(ValueError, match="state or a reader"):
        live.poll(run)


def test_subscription_endpoint_bills_nothing(user, system_ep, tick):
    run = _run(user, tick)
    attach_manifest(run, cost_basis="list_price")
    run = GenerationRun.objects.select_related("manifest").get(pk=run.pk)
    live.poll(run, state=gateway_state(cost=0.04, list_cost=0.04))
    row = SpendLedgerEntry.objects.get(run=run, source="gateway_live")
    assert (row.amount_usd, row.list_cost_usd) == (D(0), D("0.04"))


def test_budget_alert_fires_once_per_crossing(user, system_ep, tick, events):
    run = _run(user, tick, cap=0.10, cap_usd=0.10, policy_overrides={"study.alert_at": [0.5, 0.8]})
    live.poll(run, state=gateway_state(cost=0.04, list_cost=0.04))
    assert events.of("study:", "budget_alert") == []
    live.poll(run, state=gateway_state(cost=0.06, list_cost=0.06))
    live.poll(run, state=gateway_state(cost=0.07, list_cost=0.07))
    live.poll(run, state=gateway_state(cost=0.09, list_cost=0.09))
    alerts = [e["fraction"] for e in events.of("study:", "budget_alert")]
    assert alerts == [0.5, 0.8]


def test_proxy_usage_for_local_endpoints(user, system_ep, tick):
    run = _run(user, tick)
    attach_manifest(run, pricing=(1.0, 2.0), cost_basis="free")
    request = SimpleNamespace(pk="req-1", job_id=run.pk, job_attempt=0, prompt_tokens=1000, completion_tokens=500)
    entry = live.record_proxy_usage(request)
    assert entry.dedupe_key == "proxy:req-1"
    assert entry.amount_usd == D(0)  # free endpoint
    assert entry.list_cost_usd == D("0.002")  # 1000 x 1 + 500 x 2 per Mtok
    assert (entry.tokens_in, entry.tokens_out, entry.attempt) == (1000, 500, 1)
    assert live.record_proxy_usage(request).pk == entry.pk  # idempotent
    assert live.record_proxy_usage(SimpleNamespace(pk="x", job_id=None)) is None


def test_poll_interval_scales_with_running(settings):
    settings.BUDGET_LIVE_POLL_S = 10.0
    assert live.poll_interval_s(0) == 10.0
    assert live.poll_interval_s(8) == 10.0
    assert live.poll_interval_s(9) == 20.0
    assert live.poll_interval_s(17) == 30.0


def test_settlement_replaces_live_with_final(user, system_ep, tick):
    run = _run(user, tick)
    live.poll(run, state=gateway_state(cost=0.02, list_cost=0.02))
    run.metrics = {"cost_usd": 0.025}
    ledger.settle_attempt(run)
    assert ledger.study_totals(run.study_id).spent_billed == D("0.025")


def test_record_live_is_the_supervisor_hook(user, system_ep, tick, events):
    run = _run(user, tick)
    out = live.record_live(run, attempt=1, state=gateway_state(cost=0.03, list_cost=0.03), seq=7)
    assert out["seq"] == 7
    assert SpendLedgerEntry.objects.get(run=run, kind="usage").dedupe_key == f"live:{run.pk}:1:7"
    # The same poll again is a no-op; the supervisor publishes run usage itself.
    live.record_live(run, attempt=1, state=gateway_state(cost=0.03, list_cost=0.03), seq=7)
    assert SpendLedgerEntry.objects.filter(run=run, kind="usage").count() == 1
    assert events.of("run:", "usage") == []
    assert len(events.of("study:", "spend")) == 1
