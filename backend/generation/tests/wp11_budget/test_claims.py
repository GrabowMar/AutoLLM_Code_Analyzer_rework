"""Budget admission in the claim (spec 4.5): blocks, reservations, cell caps, spend caps, on_budget_exhausted."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from backend.generation.budget import ledger
from backend.generation.models import GenerationRun
from backend.generation.models import SpendLedgerEntry
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.tests.factories import SpendCapFactory
from backend.generation.tests.factories import SpendLedgerEntryFactory
from backend.generation.tests.wp11_budget.conftest import backdate
from backend.generation.tests.wp11_budget.conftest import claimed_ids
from backend.generation.tests.wp11_budget.conftest import finish
from backend.generation.tests.wp11_budget.conftest import make_study

pytestmark = pytest.mark.django_db
D = Decimal


class TestBlocks:
    def test_first_claim_reserves_the_whole_block_and_moves_its_slice(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=2, n_units=1, repeats=2, cap_usd=0.15, run_cap=0.30)
        result = tick(1)
        assert len(result.claimed) == 1
        study_id = tree.study.pk
        block = SpendLedgerEntry.objects.get(dedupe_key=f"blk:{study_id}:main:0:r")
        assert block.amount_usd == D("0.30")  # 2 cells x min(run cap 0.30, cap_usd 0.15)
        run_id = result.claimed[0].run_id
        assert SpendLedgerEntry.objects.get(dedupe_key=f"res:{run_id}:1").amount_usd == D("0.15")
        remainder, _tokens, started = ledger.block_remainder(study_id, "main", 0)
        assert started
        assert remainder == D("0.15")
        # The second cell of the block moves the rest; no second block reservation.
        second = tick(2)
        assert len(second.claimed) >= 1
        remainder, _tokens, _started = ledger.block_remainder(study_id, "main", 0)
        assert remainder == D("0")

    def test_reservation_is_min_of_run_cap_and_cell_cap_usd(self, user, system_ep, tick):
        make_study(user, n_conditions=1, n_units=1, repeats=1, cap_usd=0.50, run_cap=0.30)
        result = tick(1)
        run_id = result.claimed[0].run_id
        assert SpendLedgerEntry.objects.get(dedupe_key=f"res:{run_id}:1").amount_usd == D("0.30")

    def test_claim_order_is_repeat_major(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=2, n_units=2, repeats=2)
        result = tick(4)
        claimed = {str(c.run_id) for c in result.claimed}
        first_two_blocks = {str(r.pk) for r in tree.runs if r.cell.block_index in (0, 1)}
        assert claimed == first_two_blocks


class TestOnBudgetExhausted:
    def test_stop_balanced_claims_no_new_block_and_stops_when_idle(self, user, system_ep, tick, events):
        # cap 0.45: block 0 (0.30) fits, block 1 (0.30 more) does not.
        tree = make_study(
            user,
            n_conditions=2,
            n_units=1,
            repeats=3,
            cap=0.45,
            cap_usd=0.15,
            on_exhausted="stop_balanced",
        )
        result = tick(8)
        assert claimed_ids(result) == {str(r.pk) for r in tree.runs if r.cell.block_index == 0}
        study = Study.objects.get(pk=tree.study.pk)
        assert study.stop_reason["code"] == "budget"
        assert study.status == "running"  # started blocks finish first
        excluded = GenerationRun.objects.filter(study=study, outcome="excluded", outcome_reason="budget_stop")
        assert excluded.count() == 4
        assert StudyCell.objects.filter(study=study, state="excluded", exclusion_reason="budget_stop").count() == 4
        assert result.lanes
        assert "budget" in set(result.blocked().values())
        for claimed in result.claimed:
            finish(GenerationRun.objects.get(pk=claimed.run_id), cost=0.1)
        study.refresh_from_db()
        assert study.status == "stopped"
        assert {"type": "stopped", "reason": "budget"} in events.of("study:", "stopped")

    def test_pause_pauses_and_claims_nothing_until_raised(self, user, system_ep, tick, events):
        tree = make_study(user, n_conditions=2, n_units=1, repeats=2, cap=0.20, cap_usd=0.15, on_exhausted="pause")
        result = tick(8)
        assert result.claimed == []
        study = Study.objects.get(pk=tree.study.pk)
        assert (study.status, study.pause_reason) == ("paused", "budget")
        assert GenerationRun.objects.filter(study=study, status="pending").count() == 4
        assert any(e["reason"] == "budget" for e in events.of("study:", "paused"))
        assert not SpendLedgerEntry.objects.filter(study=study, kind="reserve").exists()

    def test_stop_excludes_every_pending_run(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=2, n_units=1, repeats=2, cap=0.20, cap_usd=0.15, on_exhausted="stop")
        tick(8)
        study = Study.objects.get(pk=tree.study.pk)
        assert study.status == "stopped"
        assert GenerationRun.objects.filter(study=study, outcome_reason="budget_stop").count() == 4

    def test_token_cap_binds_like_the_usd_cap(self, user, system_ep, tick):
        tree = make_study(
            user,
            n_conditions=2,
            n_units=1,
            repeats=2,
            policy_overrides={"study.max_total_tokens": 7_000_000, "run.max_total_tokens": 3_000_000},
        )
        result = tick(8)
        assert claimed_ids(result) == {str(r.pk) for r in tree.runs if r.cell.block_index == 0}
        assert "tokens" in Study.objects.get(pk=tree.study.pk).stop_reason["detail"]

    def test_max_runs_cap(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=2, n_units=1, repeats=3, policy_overrides={"study.max_runs": 4})
        result = tick(8)
        assert len(result.claimed) == 4
        assert "runs" in Study.objects.get(pk=tree.study.pk).stop_reason["detail"]

    def test_wall_hours_cap(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=2, policy_overrides={"study.max_wall_hours": 1.0})
        Study.objects.filter(pk=tree.study.pk).update(launched_at=timezone.now() - timedelta(hours=2))
        assert tick(8).claimed == []
        assert "wall hours" in Study.objects.get(pk=tree.study.pk).stop_reason["detail"]


class TestCellCap:
    def test_retry_beyond_the_cell_cap_is_excluded(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cell_cap=0.40, cap_usd=0.30, run_cap=0.30)
        run = tree.runs[0]
        claimed = tick(1).claimed
        assert [c.run_id for c in claimed] == [run.pk]
        # Attempt 1 spends 0.25 and is requeued as infra; attempt 2 would need 0.30: 0.25 + 0.30 > 0.40.
        run.refresh_from_db()
        ledger.record_usage(
            run,
            attempt=1,
            source="gateway_live",
            amount_usd=0.25,
            list_cost_usd=0.25,
            tokens_in=10,
            tokens_out=10,
            requests=1,
            dedupe_key=f"live:{run.pk}:1:1",
        )
        from backend.execution import transitions

        assert transitions.infra_retry(run, transitions.REASON_INFRA, "boom")
        GenerationRun.objects.filter(pk=run.pk).update(run_after=None)
        assert tick(1).claimed == []
        run.refresh_from_db()
        assert (run.outcome, run.outcome_reason, run.status) == ("excluded", "cell_budget", "cancelled")
        cell = StudyCell.objects.get(pk=run.cell_id)
        assert (cell.state, cell.exclusion_reason) == ("excluded", "cell_budget")
        assert cell.spent_usd == D("0.25")

    def test_retry_reserves_fresh_budget_not_from_the_block(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap_usd=0.10, run_cap=0.30)
        run = tree.runs[0]
        tick(1)
        run.refresh_from_db()
        from backend.execution import transitions

        transitions.infra_retry(run, transitions.REASON_INFRA, "boom")
        GenerationRun.objects.filter(pk=run.pk).update(run_after=None)
        assert [c.run_id for c in tick(1).claimed] == [run.pk]
        assert SpendLedgerEntry.objects.get(dedupe_key=f"res:{run.pk}:2").amount_usd == D("0.10")
        assert not SpendLedgerEntry.objects.filter(dedupe_key__contains=f"mv:{run.pk}:2").exists()
        # Attempt 1 was settled (released) by infra_retry.
        assert ledger.reservation(run.pk, 1) == D("0")


class TestSpendCaps:
    def test_block_cap_pauses_with_spend_cap_and_keeps_runs_queued(self, user, system_ep, tick):
        SpendCapFactory(user=user, period="day", unit="usd", limit=D("0.10"), action="block")
        tree = make_study(user, n_conditions=1, n_units=1, repeats=2, cap_usd=0.15)
        result = tick(4)
        assert result.claimed == []
        study = Study.objects.get(pk=tree.study.pk)
        assert (study.status, study.pause_reason) == ("paused", "spend_cap")
        assert GenerationRun.objects.filter(study=study, status="pending").count() == 2

    def test_warn_cap_only_warns(self, user, system_ep, tick):
        SpendCapFactory(user=user, period="day", unit="usd", limit=D("0.01"), action="warn")
        make_study(user, n_conditions=1, n_units=1, repeats=1)
        assert len(tick(1).claimed) == 1

    def test_model_quota_5h_pauses_with_quota_and_resumes_when_the_window_frees(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=2, cap_usd=0.15)
        slug = tree.conditions[0].model.model_id
        SpendCapFactory(scope="model", user=None, model_slug=slug, period="5h", unit="list_usd", limit=D("1.00"))
        entry = SpendLedgerEntryFactory(user=user, model_slug=slug, amount_usd=D(0), list_cost_usd=D("0.95"))
        assert tick(2).claimed == []
        study = Study.objects.get(pk=tree.study.pk)
        assert (study.status, study.pause_reason) == ("paused", "quota")
        # Six hours later the usage is outside the 5 h window: the reconciler resumes the study.
        backdate(entry, timedelta(hours=6))
        from backend.generation.budget import claims

        assert claims.resume_paused_for_caps() == 1
        study.refresh_from_db()
        assert (study.status, study.pause_reason) == ("running", "")
        assert len(tick(2).claimed) == 2

    def test_week_window_counts_older_usage(self, user, system_ep, tick):
        tree = make_study(user, n_conditions=1, n_units=1, repeats=1, cap_usd=0.15)
        slug = tree.conditions[0].model.model_id
        SpendCapFactory(scope="model", user=None, model_slug=slug, period="week", unit="list_usd", limit=D("1.00"))
        entry = SpendLedgerEntryFactory(user=user, model_slug=slug, list_cost_usd=D("0.95"))
        backdate(entry, timedelta(hours=6))
        assert tick(1).claimed == []
        assert Study.objects.get(pk=tree.study.pk).pause_reason == "quota"

    def test_tokens_unit(self, user, system_ep, tick):
        SpendCapFactory(user=user, period="day", unit="tokens", limit=D(4_000_000))
        make_study(user, n_conditions=1, n_units=1, repeats=2)
        # run.max_total_tokens (frugal) is 3M: one attempt fits, two do not.
        result = tick(4)
        assert len(result.claimed) == 1

    def test_global_cap_applies_to_everyone(self, user, system_ep, tick):
        SpendCapFactory(scope="global", user=None, period="month", unit="usd", limit=D("0.05"))
        make_study(user, n_conditions=1, n_units=1, repeats=1)
        assert tick(1).claimed == []


class TestAdhoc:
    def test_adhoc_run_reserves_without_a_study(self, user, system_ep, tick):
        from backend.generation.tests.factories import GenerationRunFactory
        from backend.generation.tests.wp11_budget.conftest import model

        run = GenerationRunFactory(created_by=user, model=model(), queued_at=timezone.now())
        result = tick(1)
        assert [c.run_id for c in result.claimed] == [run.pk]
        entry = SpendLedgerEntry.objects.get(dedupe_key=f"res:{run.pk}:1")
        assert entry.study_id is None
        assert entry.amount_usd == D("0.30")  # frugal fallback run cap
