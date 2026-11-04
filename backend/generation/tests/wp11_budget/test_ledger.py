"""The spend ledger (4.5): idempotent writes, settlement sources, caches and the reconciler's repairs."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings

from backend.generation.budget import ledger
from backend.generation.models import GenerationRun
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.testing.fake_gateway import canned_call
from backend.generation.tests.factories import RunAttemptFactory
from backend.generation.tests.wp11_budget.conftest import attach_manifest
from backend.generation.tests.wp11_budget.conftest import make_study

pytestmark = pytest.mark.django_db
D = Decimal


def _claimed_run(user, tick, **kw):
    tree = make_study(user, n_conditions=1, n_units=1, repeats=1, **kw)
    result = tick(4)
    run = GenerationRun.objects.get(pk=result.claimed[0].run_id)
    return tree, run


def _usage(run, amount, *, seq=1, list_cost=None):
    return ledger.record_usage(
        run,
        attempt=1,
        source="gateway_live",
        amount_usd=amount,
        list_cost_usd=amount if list_cost is None else list_cost,
        tokens_in=100,
        tokens_out=10,
        requests=1,
        dedupe_key=f"live:{run.pk}:1:{seq}",
    )


def test_writes_are_idempotent(user, system_ep, tick):
    tree, run = _claimed_run(user, tick)
    first = _usage(run, 0.05)
    again = _usage(run, 0.07)  # same dedupe key: the stored row wins
    assert first.pk == again.pk
    assert again.amount_usd == D("0.05")
    assert SpendLedgerEntry.objects.filter(run=run, kind="usage").count() == 1
    study = Study.objects.get(pk=tree.study.pk)
    assert study.spent_usd == D("0.05")
    assert StudyCell.objects.get(pk=run.cell_id).spent_usd == D("0.05")


def test_settle_releases_and_adjusts_to_the_billed_calls(user, system_ep, tick):
    tree, run = _claimed_run(user, tick, manifests=True)
    _usage(run, 0.04)
    path = Path(settings.GENERATION_RUNS_ROOT) / str(run.pk) / "a1"
    path.mkdir(parents=True)
    calls = [canned_call(1, cost=0.03), canned_call(2, cost=0.02), canned_call(3, status=502)]
    (path / "calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls) + "\n")
    ledger.settle_attempt(run)
    ledger.settle_attempt(run)  # idempotent
    adjust = SpendLedgerEntry.objects.get(dedupe_key=f"adj:{run.pk}:1")
    assert adjust.amount_usd == D("0.01")  # 0.05 billed - 0.04 live
    assert adjust.source == "gateway_final"
    assert ledger.reservation(run.pk, 1) == D(0)
    totals = ledger.study_totals(tree.study.pk)
    assert totals.spent_billed == D("0.05")
    assert totals.held == D(0)  # the block closed too: its only cell finished its generation
    assert totals.open_reserved == D(0)


def test_price_table_when_usage_cost_is_missing(user, system_ep, tick):
    _tree, run = _claimed_run(user, tick, manifests=True, pricing=(1.0, 2.0))
    path = Path(settings.GENERATION_RUNS_ROOT) / str(run.pk) / "a1"
    path.mkdir(parents=True)
    call = canned_call(1, prompt_tokens=10_000, completion_tokens=1_000)
    call["usage"].pop("cost")
    (path / "calls.jsonl").write_text(json.dumps(call) + "\n")
    ledger.settle_attempt(run)
    adjust = SpendLedgerEntry.objects.get(dedupe_key=f"adj:{run.pk}:1")
    assert adjust.amount_usd == D("0.012")  # 10k x 1 + 1k x 2 per Mtok
    assert adjust.source == "price_table"


def test_a_call_settled_at_its_reservation_counts_what_the_gateway_charged(user, system_ep, tick):
    # E2E-29: a usage-less record the gateway charged (``usage_estimated``) was skipped, so the ledger fell
    # short of the calls.jsonl sum (run 1f08e760: 0.0652 booked, 0.0755 charged).
    _tree, run = _claimed_run(user, tick, manifests=True)
    path = Path(settings.GENERATION_RUNS_ROOT) / str(run.pk) / "a1"
    path.mkdir(parents=True)
    ok = canned_call(1, cost=0.03)
    estimated = canned_call(2, status=200)
    estimated.update(
        usage=None,
        usage_estimated=True,
        cost=0.02,
        list_cost=0.025,
        cost_source="price_table",
        reserved_prompt_tokens=1000,
        upstream_max_tokens=500,
    )
    list_priced = dict(estimated, cost=0.01, list_cost=0.01, cost_source="list_price")
    refused = canned_call(3, status=400)
    refused.update(usage=None, usage_estimated=False, cost=0.0, list_cost=0.0)
    calls = [ok, estimated, list_priced, refused]
    (path / "calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls) + "\n")
    final = ledger.final_usage(run, 1)
    listed = D(str(ok["list_cost"])) + D("0.025") + D("0.01")
    assert (final.cost, final.list_cost) == (D("0.03") + D("0.02"), listed)  # a list-price charge bills 0
    assert (final.tokens_in, final.tokens_out, final.requests) == (1200 + 2 * 1000, 300 + 2 * 500, 3)


def test_attempt_usage_then_metrics_fallbacks(user, system_ep, tick):
    _tree, run = _claimed_run(user, tick)
    RunAttemptFactory(run=run, attempt=1, usage={"cost_usd": 0.02, "list_cost_usd": 0.03, "prompt_tokens": 5})
    final = ledger.final_usage(run, 1)
    assert (final.cost, final.list_cost, final.tokens_in) == (D("0.02"), D("0.03"), 5)
    _tree2, other = _claimed_run(user, tick)
    other.metrics = {"cost_usd": 0.011}
    assert ledger.final_usage(other, 1).cost == D("0.011")
    other.metrics = {}
    assert ledger.final_usage(other, 1) is None


def test_settle_without_any_final_keeps_the_live_usage(user, system_ep, tick):
    tree, run = _claimed_run(user, tick)
    _usage(run, 0.03)
    ledger.settle_attempt(run)
    assert not SpendLedgerEntry.objects.filter(dedupe_key=f"adj:{run.pk}:1").exists()
    assert ledger.study_totals(tree.study.pk).spent_billed == D("0.03")
    assert ledger.reservation(run.pk, 1) == D(0)


def test_list_basis_counts_list_cost(user, system_ep, tick):
    tree, run = _claimed_run(user, tick)
    Study.objects.filter(pk=tree.study.pk).update(cap_basis="list")
    _usage(run, 0.0, list_cost=0.08)
    totals = ledger.study_totals(tree.study.pk)
    assert (totals.spent_billed, totals.spent_list, totals.spent) == (D(0), D("0.08"), D("0.08"))


def test_exposure_never_double_counts_live_usage(user, system_ep, tick):
    tree, run = _claimed_run(user, tick, cap_usd=0.15)
    _usage(run, 0.05)
    totals = ledger.study_totals(tree.study.pk)
    # reserved 0.15 for the attempt, 0.05 used: exposure 0.15, not 0.20.
    assert totals.exposure == D("0.15")
    assert totals.open_reserved == D("0.15")


def test_reconciler_settles_ended_attempts_and_closes_their_block(user, system_ep, tick):
    tree = make_study(user, n_conditions=2, n_units=1, repeats=1, cap_usd=0.15)
    claimed = tick(1).claimed  # one of two cells: the block holds the other slice
    run = GenerationRun.objects.get(pk=claimed[0].run_id)
    _usage(run, 0.05)
    # The worker crashed after the run ended: no settlement, the reservation is still open.
    GenerationRun.objects.filter(pk=run.pk).update(status="completed", phase="", outcome="tested", lease_owner="")
    other = next(r for r in tree.runs if r.pk != run.pk)
    GenerationRun.objects.filter(pk=other.pk).update(
        status="cancelled",
        outcome="excluded",
        outcome_reason="cancelled",
    )
    # 0.15 - 0.05 still held by the attempt, plus the other cell's 0.15 slice in the block.
    assert ledger.study_totals(tree.study.pk).held == D("0.25")
    assert ledger.reconcile()["settled"] == 1
    totals = ledger.study_totals(tree.study.pk)
    assert totals.held == D(0)
    assert totals.spent_billed == D("0.05")
    assert ledger.reconcile() == {"settled": 0, "blocks_closed": 0}


def test_reconciler_releases_the_reservation_of_a_finished_block(user, system_ep):
    tree = make_study(user, n_conditions=2, n_units=1, repeats=1, cap_usd=0.15)
    runs = [
        GenerationRun.objects.select_related("cell", "model", "resource_policy", "study").get(pk=r.pk)
        for r in tree.runs
    ]
    ledger.reserve_block(runs)
    GenerationRun.objects.filter(pk__in=[r.pk for r in runs]).update(
        status="cancelled",
        outcome="excluded",
        outcome_reason="cancelled",
    )
    assert ledger.study_totals(tree.study.pk).held == D("0.30")
    assert ledger.reconcile() == {"settled": 0, "blocks_closed": 1}
    assert ledger.study_totals(tree.study.pk).held == D(0)
    assert SpendLedgerEntry.objects.filter(dedupe_key=f"blk:{tree.study.pk}:main:0:close").exists()


def test_reconciler_leaves_running_attempts_alone(user, system_ep, tick):
    _tree, run = _claimed_run(user, tick)
    assert ledger.reconcile()["settled"] == 0
    assert ledger.reservation(run.pk, 1) > 0


def test_run_policy_prefers_the_manifest(user, system_ep, tick):
    _tree, run = _claimed_run(user, tick)
    content = dict(run.resource_policy.content)
    content["run"] = {**content["run"], "max_cost_usd": 0.12}
    attach_manifest(run, effective={"content": content})
    run = GenerationRun.objects.select_related("manifest").get(pk=run.pk)
    assert ledger.run_policy(run).run.max_cost_usd == 0.12


def test_ledger_rows_are_append_only(user, system_ep, tick):
    _tree, run = _claimed_run(user, tick)
    entry = _usage(run, 0.01)
    from backend.generation.models.base import ImmutableVersionError

    with pytest.raises(ImmutableVersionError):
        entry.save()
