"""WP-11 "done when" and the 13.4 invariants it owns.

* A FakeGateway-driven study of 20 runs with a cap at 60% of the expected total stops cleanly and
  balanced, with no overspend, and the stop reason, ``budget_stop`` exclusions and ``stopped_early``
  deviations are visible in the results payload.
* Reservation invariant (4.5): ``spent + held <= study cap`` at every instant, including under concurrent
  claims (threads + ``select_for_update`` on the Study row).
* Balanced budget stop: after a budget stop, cell counts per (condition, unit) differ by at most 1, and
  complete blocks only feed paired results.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from django.db import transaction

from backend.execution import transitions
from backend.generation.budget import claims
from backend.generation.budget import gateway_config
from backend.generation.budget import ledger
from backend.generation.budget import live
from backend.generation.budget import report
from backend.generation.models import GenerationRun
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.testing.fake_gateway import FakeGateway
from backend.generation.testing.fake_gateway import ScriptedReply
from backend.generation.tests.wp11_budget.conftest import make_study

D = Decimal
CALLS_PER_RUN = 4
# 4 calls of 20k prompt + 2.5k completion tokens at 1 / 2 USD per Mtok = 0.10 USD per run.
REPLY = ScriptedReply(prompt_tokens=20_000, completion_tokens=2_500)


def _chat(gw: FakeGateway) -> int:
    body = json.dumps({"model": "any", "messages": [{"role": "user", "content": "Build the app."}]}).encode()
    request = urllib.request.Request(  # noqa: S310 — a local fake on 127.0.0.1
        f"{gw.base_url}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _run_attempt(run: GenerationRun) -> None:
    """One generation stage through the fake gateway: calls, a live poll mid-way, then the terminal finish."""
    run.refresh_from_db()
    attempt = ledger.attempt_no(run)
    config = gateway_config.build(run)
    logs = Path(settings.GENERATION_RUNS_ROOT) / str(run.pk) / f"a{attempt}"
    with FakeGateway(config, default_reply=REPLY, logs_dir=logs) as gw:
        for index in range(CALLS_PER_RUN):
            assert _chat(gw) == 200
            if index == 1:
                live.poll(run, state=gw.state())
        live.poll(run, state=gw.state())
    run.outcome = "tested"
    run.metrics = {"cost_usd": 0.10}
    assert transitions.finish_stage(run, transitions.OUTCOME_DONE)


def _exposure(study_id) -> Decimal:
    return ledger.study_totals(study_id).exposure


@pytest.mark.django_db
def test_fakegateway_study_stops_balanced_at_60_percent_without_overspend(user, system_ep, tick, on_commit):
    tree = make_study(
        user,
        n_conditions=2,
        n_units=2,
        repeats=5,
        cap_usd=0.15,
        run_cap=0.30,
        estimate_usd=0.10,
        cap=1.20,  # 60% of the expected 20 x 0.10
        on_exhausted="stop_balanced",
        manifests=True,
        pricing=(1.0, 2.0),
    )
    study_id = tree.study.pk
    cap = D("1.20")
    max_exposure = D(0)
    for _ in range(40):
        result = tick(4)
        max_exposure = max(max_exposure, _exposure(study_id))
        assert _exposure(study_id) <= cap
        if not result.claimed:
            break
        for claimed in result.claimed:
            with on_commit():
                _run_attempt(GenerationRun.objects.get(pk=claimed.run_id))
            max_exposure = max(max_exposure, _exposure(study_id))
            assert _exposure(study_id) <= cap
    study = Study.objects.get(pk=study_id)
    assert study.status == "stopped"
    assert study.stop_reason["code"] == "budget"

    # No overspend: billed spend equals the gateways' calls and stays under the cap.
    totals = ledger.study_totals(study)
    tested = GenerationRun.objects.filter(study=study, outcome="tested")
    assert totals.spent_billed == D("0.10") * tested.count()
    assert totals.spent_billed <= cap
    assert max_exposure <= cap
    assert totals.held == 0  # every reservation released, every block closed

    # Balanced: per (condition, unit) counts differ by at most one, and only whole blocks ran.
    counts = Counter((r.condition_id, r.cell.unit_id) for r in tested.select_related("cell"))
    per_pair = [counts.get((c.pk, u.pk), 0) for c in tree.conditions for u in tree.units]
    assert max(per_pair) - min(per_pair) <= 1
    complete = report.complete_blocks(study)
    assert len(complete) * 2 == tested.count()
    for _stage, block in complete:
        assert {r.condition_id for r in tested if r.cell.block_index == block} == {c.pk for c in tree.conditions}

    # Visible in results: the stop reason, budget_stop exclusions per condition and stopped_early cells.
    payload = report.study_payload(study)
    deviations = payload["deviations"]
    assert deviations["stop_reason"]["code"] == "budget"
    excluded = {row["condition_id"]: row["budget_stop"] for row in deviations["exclusions"]}
    assert set(excluded) == {c.pk for c in tree.conditions}
    assert sum(excluded.values()) == 20 - tested.count()
    assert deviations["stopped_early"]
    assert payload["blocks"]["complete"] == len(complete)
    assert payload["spent"] <= 1.20
    assert all(row["excluded_budget_stop"] > 0 for row in payload["by_condition"])


@pytest.mark.django_db
def test_balanced_stop_counts_differ_by_at_most_one(user, system_ep, tick, on_commit):
    tree = make_study(user, n_conditions=3, n_units=3, repeats=4, cap_usd=0.10, cap=1.75, on_exhausted="stop_balanced")
    for _ in range(50):
        result = tick(5)
        if not result.claimed:
            break
        for claimed in result.claimed:
            from backend.generation.tests.wp11_budget.conftest import finish

            with on_commit():
                finish(GenerationRun.objects.get(pk=claimed.run_id), cost=0.07)
    study = Study.objects.get(pk=tree.study.pk)
    assert study.status == "stopped"
    done = GenerationRun.objects.filter(study=study, outcome="tested").select_related("cell")
    counts = Counter((r.condition_id, r.cell.unit_id) for r in done)
    per_pair = [counts.get((c.pk, u.pk), 0) for c in tree.conditions for u in tree.units]
    assert max(per_pair) - min(per_pair) <= 1
    assert sum(per_pair) % 3 == 0  # whole blocks only


@pytest.mark.django_db(transaction=True)
def test_reservation_invariant_under_concurrent_claims(user, system_ep):
    """Eight threads claim eight single-cell blocks at once; the Study row lock lets exactly three fit."""
    tree = make_study(user, n_conditions=1, n_units=1, repeats=8, cap=0.45, cap_usd=0.15, on_exhausted="pause")
    barrier = threading.Barrier(len(tree.runs))
    decisions: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(run_id) -> None:
        try:
            barrier.wait(timeout=10)
            with transaction.atomic():
                decision = claims.admit([run_id])[str(run_id)]
            with lock:
                decisions.append(decision.ok)
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(run.pk,)) for run in tree.runs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    assert decisions.count(True) == 3
    totals = ledger.study_totals(tree.study.pk)
    assert totals.exposure == D("0.45")
    assert SpendLedgerEntry.objects.filter(study=tree.study, kind="reserve", run__isnull=True).count() == 3
    assert Study.objects.get(pk=tree.study.pk).pause_reason == "budget"
