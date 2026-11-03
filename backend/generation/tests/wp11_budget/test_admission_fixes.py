"""Review fixes: the launch-resolved study cap, lane-stopped admission, and stale reservations."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.generation.budget import claims
from backend.generation.budget import ledger
from backend.generation.budget import report
from backend.generation.models import GenerationRun
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models.choices import LedgerSource
from backend.generation.tests.wp11_budget.conftest import claimed_ids
from backend.generation.tests.wp11_budget.conftest import make_study

pytestmark = pytest.mark.django_db
D = Decimal


def _run(pk):
    return GenerationRun.objects.select_related("cell", "model", "study", "resource_policy").get(pk=pk)


class TestStudyCapColumns:
    def test_study_cap_usd_overrides_the_policy_number(self, user):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap=10.0)
        Study.objects.filter(pk=tree.study.pk).update(cap_usd=D("0.05"))
        study = Study.objects.select_related("resource_policy", "active_plan").get(pk=tree.study.pk)
        assert claims.study_cap(study) == D("0.05")
        assert report.study_payload(study)["cap"] == pytest.approx(0.05)

    def test_null_cap_usd_falls_back_to_the_policy(self, user):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap=7.0)
        study = Study.objects.select_related("resource_policy", "active_plan").get(pk=tree.study.pk)
        assert study.cap_usd is None
        assert claims.study_cap(study) == D("7.0")

    def test_admission_uses_the_launch_cap_not_the_policy(self, user, system_ep, tick):
        # The policy allows $10 but the cap typed at launch is $0.05: one run (0.15) does not fit.
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap=10.0)
        Study.objects.filter(pk=tree.study.pk).update(cap_usd=D("0.05"))
        assert tick(2).claimed == []
        study = Study.objects.get(pk=tree.study.pk)
        assert study.stop_reason["code"] == "budget"

    def test_raising_the_cap_lets_a_budget_capped_policy_claim(self, user, system_ep, tick):
        # The policy cap ($0.05) is below a run's need; the raised study cap ($1) admits it.
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap=0.05)
        Study.objects.filter(pk=tree.study.pk).update(cap_usd=D("1.00"))
        assert claimed_ids(tick(2)) == {str(tree.runs[0].pk)}

    def test_cap_tokens_is_the_token_cap(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
        Study.objects.filter(pk=tree.study.pk).update(cap_tokens=1)
        study = Study.objects.select_related("resource_policy").get(pk=tree.study.pk)
        assert claims._study_limits(study)["tokens"] == 1
        assert tick(2).claimed == []


class TestLaneStoppedAdmission:
    def test_runs_behind_a_refused_run_are_not_reserved(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=2, cell_cap=0.40, cap_usd=0.15)
        first, second = sorted(tree.runs, key=lambda r: r.sequence)
        # The first cell already spent its cell cap: it is excluded (cell_budget) at admission.
        StudyCell.objects.filter(pk=first.cell_id).update(spent_usd=D("0.40"))
        result = tick(4)
        assert str(second.pk) not in claimed_ids(result)
        assert _run(first.pk).outcome_reason == "cell_budget"
        # Nothing was reserved for the run behind it: no attempt reservation, no block reservation.
        assert not SpendLedgerEntry.objects.filter(run_id=second.pk).exists()
        assert not SpendLedgerEntry.objects.filter(dedupe_key__startswith=f"blk:{tree.study.pk}:main:1:").exists()
        # The reconciler has nothing to release; the next tick claims it with its full reservation.
        assert ledger.reconcile()["settled"] == 0
        assert claimed_ids(tick(4)) == {str(second.pk)}
        assert ledger.reservation(second.pk, 1) == D("0.15")
        assert ledger.study_totals(tree.study.pk).exposure == D("0.15")

    def test_admit_without_lanes_still_decides_every_run(self, user, system_ep):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=2, cell_cap=0.40)
        first = min(tree.runs, key=lambda r: r.sequence)
        StudyCell.objects.filter(pk=first.cell_id).update(spent_usd=D("0.40"))
        ids = [r.pk for r in sorted(tree.runs, key=lambda r: r.sequence)]
        assert len(claims.admit(ids)) == 2
        assert len(claims.admit(ids[1:], lane_of=lambda _id: "lane")) == 1


class TestStaleReservations:
    def test_reconciler_keeps_a_pending_runs_current_reservation(self, user, system_ep):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
        run = _run(tree.runs[0].pk)
        ledger.reserve(run, attempt=1, amount_usd=D("0.15"))
        assert ledger.reconcile()["settled"] == 0
        assert ledger.reservation(run.pk, 1) == D("0.15")

    def test_readmission_reuses_the_open_reservation(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap_usd=0.15)
        run = _run(tree.runs[0].pk)
        ledger.reserve(run, attempt=1, amount_usd=D("0.15"))
        assert claimed_ids(tick(2)) == {str(run.pk)}
        assert ledger.reservation(run.pk, 1) == D("0.15")
        assert ledger.study_totals(tree.study.pk).exposure == D("0.15")  # not 0.30

    def test_settle_after_an_early_release_still_records_the_final_usage(self, user, system_ep):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1)
        run = _run(tree.runs[0].pk)
        ledger.reserve(run, attempt=1, amount_usd=D("0.15"))
        GenerationRun.objects.filter(pk=run.pk).update(status="completed", outcome="tested")
        run = _run(run.pk)
        ledger.settle_attempt(run, attempt=1)  # nothing recorded yet: release only
        assert SpendLedgerEntry.objects.filter(dedupe_key=f"rel:{run.pk}:1").exists()
        final = ledger.FinalUsage(D("0.04"), D("0.04"), 100, 10, 1, LedgerSource.GATEWAY_FINAL)
        ledger.settle_attempt(run, attempt=1, final=final)
        assert SpendLedgerEntry.objects.get(dedupe_key=f"adj:{run.pk}:1").amount_usd == D("0.04")
        totals = ledger.study_totals(tree.study.pk)
        assert totals.spent_billed == D("0.04")
        assert totals.held == D(0)
        ledger.settle_attempt(run, attempt=1, final=final)  # idempotent
        assert ledger.study_totals(tree.study.pk).spent_billed == D("0.04")
