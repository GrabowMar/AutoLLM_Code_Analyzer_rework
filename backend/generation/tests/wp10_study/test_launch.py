"""Launch, lock, pilot expansion, top-up and clone (spec sections 4.6, 6.2, 6.14, 9.4; Appendix A #11-13)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.generation.models import GenerationRun
from backend.generation.models import ImmutableVersionError
from backend.generation.models import RunManifest
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudyPlan
from backend.generation.models.choices import Outcome
from backend.generation.models.choices import RunStatus
from backend.generation.runs.queries import current_runs
from backend.generation.runs.queries import refresh_current
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.study.errors import StudyError

from .conftest import app_design

pytestmark = pytest.mark.django_db

PLAN = {"primary_metric": "pass_rate", "research_questions": [{"question": "Does the model matter?"}]}


def finish(run: GenerationRun, outcome: str, *, pass_rate: float | None = None, **extra) -> GenerationRun:
    """What the executor and evaluator leave behind (status, outcome, metrics), then the cell refresh."""
    run.status = RunStatus.FAILED if outcome == Outcome.INFRA_ERROR else RunStatus.COMPLETED
    run.outcome = outcome
    if outcome == Outcome.EXCLUDED:
        run.outcome_reason = extra.pop("reason", "cancelled")
    metrics = {"cost_usd": 0.01}
    if pass_rate is not None:
        metrics["eval"] = {
            "eval_suite": "eval-v2@1",
            "eval_level": "security",
            "status": "measured",
            "measured": True,
            "agent_pass_rate": pass_rate,
        }
    run.metrics = metrics
    for key, value in extra.items():
        setattr(run, key, value)
    run.save()
    refresh_current(run.cell)
    return run


def test_launch_creates_pilot_runs_with_manifests_after_commit(
    make_study,
    fake_study,
    django_capture_on_commit_callbacks,
):
    study = make_study()
    plan_hash = planner.resolve(study).plan_hash
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        result = svc.launch(study, plan_hash)
        assert fake_study.enqueued == []  # nothing dispatched before commit
    for callback in callbacks:
        callback()
    study.refresh_from_db()
    assert result["stage"] == "pilot"
    assert study.status == "piloting"
    runs = GenerationRun.objects.filter(study=study)
    assert runs.count() == result["created_runs"] == 16
    assert fake_study.enqueued
    assert set(fake_study.enqueued[0]) == {str(r.pk) for r in runs}
    run = runs.select_related("manifest", "cell").first()
    assert run.manifest.manifest["identity"]["run_fingerprint"] == run.run_fingerprint
    assert run.manifest.manifest["sampling"]["seed"] == run.seed == run.cell.seed
    assert run.stage == "pilot"
    assert run.cell_attempt == 1
    assert not run.beyond_plan
    assert study.active_plan.status == "launched"
    assert study.max_parallel is not None
    assert study.execution_options
    assert study.locked_at is None  # exploratory


def test_launch_rejects_a_stale_plan_and_plan_errors(make_study, fake_study):
    study = make_study()
    plan_hash = planner.resolve(study).plan_hash
    Study.objects.filter(pk=study.pk).update(design=app_design(repeats=3), design_hash="0" * 64)
    study.refresh_from_db()
    with pytest.raises(StudyError) as stale:
        svc.launch(study, plan_hash)
    assert (stale.value.status, stale.value.code) == (409, "plan_stale")
    broken = make_study(name="broken", units={"tasks": ["crud_todo_list@1:minimal"]})
    with pytest.raises(StudyError) as errors:
        svc.launch(broken, planner.resolve(broken).plan_hash)
    assert errors.value.code == "plan_has_errors"
    assert GenerationRun.objects.count() == 0


def test_confirmation_is_required_above_the_threshold(make_study, fake_study):
    fake_study.cost_per_cell = 1.0  # p90 = 2.2 x cells, far above frugal's confirmation threshold
    study = make_study()
    plan = planner.resolve(study)
    with pytest.raises(StudyError) as refused:
        svc.launch(study, plan.plan_hash)
    assert refused.value.code == "confirmation_required"
    svc.launch(study, plan.plan_hash, confirm_usd=plan.estimate.p90_total)


def test_a_list_basis_launch_confirms_and_stores_list_estimates(make_study, fake_study):
    """E2E-13: on a subscription endpoint the billed p90 is 0; the confirmation and the cells' estimates use
    list price, so the gates and stop rules have a list estimate to compare list cost with."""
    fake_study.list_price = True
    fake_study.cost_per_cell = 1.0
    study = make_study()
    plan = planner.resolve(study)
    assert plan.estimate.p90_total == 0.0
    assert plan.estimate.p90_list_total > 0
    with pytest.raises(StudyError) as refused:
        svc.launch(study, plan.plan_hash, confirm_usd=0.0)
    assert refused.value.code == "confirmation_required"
    assert refused.value.data["p90_total"] == pytest.approx(plan.estimate.p90_list_total)
    assert refused.value.data["cap_basis"] == "list"
    svc.launch(study, plan.plan_hash, confirm_usd=plan.estimate.p90_list_total)
    cells = StudyCell.objects.filter(study=study)
    assert cells.exists()
    assert {(c.estimate_usd, c.estimate_list_usd) for c in cells} == {(Decimal("0"), Decimal("1"))}


def test_confirmatory_lock_is_immutable_and_prereg_covers_patterns(make_study, fake_study):
    study = make_study(kind="confirmatory", analysis_plan=PLAN)
    svc.launch(study, planner.resolve(study).plan_hash)
    study.refresh_from_db()
    assert study.locked_at is not None
    assert study.prereg_hash
    assert study.analysis_plan_hash
    assert study.analysis_plan["itt"] is True
    assert study.analysis_plan["exclusions"]["truncated"] is False
    with pytest.raises(StudyError) as locked:
        svc.update_study(study, design=app_design(kind="confirmatory", analysis_plan=PLAN, repeats=3))
    assert locked.value.code == "study_locked"
    study.locked_at = None
    with pytest.raises(ImmutableVersionError):
        study.save()
    with pytest.raises(ImmutableVersionError):
        Study.objects.filter(pk=study.pk).update(analysis_plan={})
    other = make_study(
        name="patterns",
        kind="confirmatory",
        analysis_plan={**PLAN, "patterns": {"detectors": ["py-sql-fstring@1"], "primary_features": ["sqli"]}},
    )
    svc.launch(other, planner.resolve(other).plan_hash)
    other.refresh_from_db()
    assert other.prereg_hash != study.prereg_hash
    assert other.analysis_plan_hash != study.analysis_plan_hash


def test_pilot_expand_reuses_pilot_cells_in_exploratory_studies(make_study, fake_study):
    study = make_study()
    svc.launch(study, planner.resolve(study).plan_hash)
    for run in GenerationRun.objects.filter(study=study):
        finish(run, Outcome.TESTED, pass_rate=0.5)
    result = svc.expand_pilot(study)
    study.refresh_from_db()
    assert study.status == "running"
    assert result["reused_pilot_cells"] == 16
    main = StudyCell.objects.filter(study=study, stage="main")
    assert main.count() == 32  # 8 conditions x 2 units x 2 repeats; repeat 0 came from the pilot
    assert not StudyCell.objects.filter(study=study, stage="pilot").exists()
    assert result["created_runs"] == 16
    assert study.active_plan.plan_hash == planner.resolve(study).plan_hash  # same plan


def test_confirmatory_pilot_is_never_reused_and_gates_block(make_study, fake_study):
    study = make_study(kind="confirmatory", analysis_plan=PLAN)
    svc.launch(study, planner.resolve(study).plan_hash)
    for run in GenerationRun.objects.filter(study=study):
        finish(run, Outcome.TESTED, pass_rate=0.5)
    fake_study.gates = [{"code": "min_build_rate", "ok": False, "value": 0.2, "threshold": 0.5}]
    with pytest.raises(StudyError) as failed:
        svc.expand_pilot(study)
    assert failed.value.code == "pilot_gates_failed"
    with pytest.raises(StudyError):
        svc.expand_pilot(study, override_gates=True)
    result = svc.expand_pilot(study, override_gates=True, reason="checked by hand")
    assert result["reused_pilot_cells"] == 0
    assert result["created_runs"] == 32
    assert StudyCell.objects.filter(study=study, stage="pilot").count() == 16
    assert current_runs(study, "main").count() == 32


def _running(make_study, **overrides):
    study = make_study(**overrides)
    svc.launch(study, planner.resolve(study).plan_hash, pilot_override=True)
    study.refresh_from_db()
    return study


def test_top_up_only_refills_infra_and_never_supersedes_a_usable_run(make_study, fake_study):
    study = _running(make_study)
    runs = list(GenerationRun.objects.filter(study=study).order_by("sequence"))
    finish(runs[0], Outcome.TESTED, pass_rate=1.0)
    finish(runs[1], Outcome.BUILD_FAIL)
    finish(runs[2], Outcome.INFRA_ERROR)
    finish(runs[3], Outcome.EXCLUDED, reason="budget_stop")
    finish(runs[4], Outcome.EXCLUDED, reason="resource_limit")
    explicit = svc.top_up(study, [r.cell_id for r in runs[:5]], reason="test")
    refused = {row["cell_id"]: row["code"] for row in explicit["refused"]}
    assert refused == {
        runs[0].cell_id: "cell_has_usable_run",
        runs[1].cell_id: "cell_has_usable_run",
        runs[4].cell_id: "not_eligible",
    }
    assert explicit["created_runs"] == 2
    again = svc.top_up(study, "infra", reason="test")
    assert again["created_runs"] == 0  # the new attempts are pending: nothing to refill yet
    new = GenerationRun.objects.get(cell=runs[2].cell, cell_attempt=2)
    assert new.seed == runs[2].seed
    assert new.run_fingerprint == runs[2].run_fingerprint
    assert not new.beyond_plan
    finish(new, Outcome.TESTED, pass_rate=0.0)
    cell = StudyCell.objects.get(pk=runs[2].cell_id)
    assert cell.current_run_id == new.pk
    assert cell.state == "done"


def test_a_usable_current_run_never_changes(make_study, fake_study):
    """Invariant (6.14): for any sequence of retries, top-ups and re-evaluations, a usable current run stays."""
    study = _running(make_study)
    run = GenerationRun.objects.filter(study=study).order_by("sequence").first()
    finish(run, Outcome.INFRA_ERROR)
    svc.top_up(study, "infra", reason="first")
    second = GenerationRun.objects.get(cell=run.cell, cell_attempt=2)
    finish(second, Outcome.OUTPUT_INVALID)
    cell = StudyCell.objects.get(pk=run.cell_id)
    assert cell.current_run_id == second.pk
    # A later run on the cell (forced, e.g. an out-of-band re-run) is beyond the plan and never current.
    third = GenerationRun.objects.get(pk=second.pk)
    third.pk = None
    third.id = None
    third.cell_attempt = 3
    third.beyond_plan = True
    third.save()
    finish(GenerationRun.objects.get(pk=third.pk), Outcome.TESTED, pass_rate=1.0)
    # The first (infra) run is re-evaluated and becomes usable: the current run still does not move.
    finish(GenerationRun.objects.get(pk=run.pk), Outcome.TESTED, pass_rate=1.0)
    cell.refresh_from_db()
    assert cell.current_run_id == second.pk
    assert second.pk in set(current_runs(study).values_list("pk", flat=True))
    refused = svc.top_up(study, [cell.pk], reason="again")["refused"]
    assert refused == [{"cell_id": cell.pk, "code": "cell_has_usable_run"}]


def test_clone_exact_and_latest_versions(make_study, fake_study, user, golden):
    from backend.generation.models import StyleDirective

    study = make_study()
    exact, diffs = svc.clone_with_diffs(study, mode="exact", name="copy", user=user)
    assert exact.design == study.design
    assert exact.status == "draft"
    assert diffs == []
    tailwind = StyleDirective.objects.get(slug="tailwind", version=1)
    StyleDirective.objects.create(
        slug="tailwind",
        version=2,
        name="v2",
        content=dict(tailwind.content, tone_text="x"),
        created_by=user,
    )
    latest, diffs = svc.clone_with_diffs(study, mode="latest_versions", name="latest", user=user)
    assert {d["factor"] for d in diffs} == {"style"}
    assert any(level.startswith("tailwind@2#") for level in latest.design["factors"][2]["levels"])
    # An exact clone freezes its own plan row with the same plan_hash (unique per (study, plan_hash)).
    source_row = planner.freeze(study)
    clone_row = planner.freeze(exact)
    assert clone_row.pk != source_row.pk
    assert clone_row.plan_hash == source_row.plan_hash
    assert clone_row.study_id == exact.pk
    assert planner.freeze(exact).pk == clone_row.pk  # idempotent per study


def test_launch_adhoc_returns_the_automation_keys(golden, user, fake_study):
    result = svc.launch_adhoc(app_design(), user=user)
    assert set(result) >= {"study_id", "plan_hash", "run_ids"}
    assert len(result["run_ids"]) == 16
    assert StudyPlan.objects.get(plan_hash=result["plan_hash"]).status == "launched"
    assert RunManifest.objects.filter(run_id__in=result["run_ids"]).count() == 16


def test_cancel_pending_runs(make_study, fake_study, django_capture_on_commit_callbacks):
    study = _running(make_study)
    with django_capture_on_commit_callbacks(execute=True):
        svc.cancel(study)
    study.refresh_from_db()
    assert study.status == "cancelled"
    assert set(GenerationRun.objects.filter(study=study).values_list("outcome_reason", flat=True)) == {"cancelled"}
    assert set(StudyCell.objects.filter(study=study).values_list("state", flat=True)) == {"excluded"}


def test_top_up_dispatches_only_after_commit(make_study, fake_study, django_capture_on_commit_callbacks):
    study = _running(make_study)
    run = GenerationRun.objects.filter(study=study).order_by("sequence").first()
    finish(run, Outcome.INFRA_ERROR)
    fake_study.enqueued.clear()
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        result = svc.top_up(study, "infra", reason="on commit")
        assert fake_study.enqueued == []
    assert result["created_runs"] == 1
    for callback in callbacks:
        callback()
    assert fake_study.enqueued == [result["run_ids"]]


@pytest.mark.parametrize(
    ("study_cap", "estimate_cap", "expected"),
    [
        (3.5, None, Decimal("3.5")),  # the LaunchDialog's number
        ("auto", 1.23, Decimal("1.23")),  # auto -> the frozen estimate's cap
        (None, 0.75, Decimal("0.75")),  # the frugal preset's study.max_cost_usd is auto
        ("auto", None, None),  # no resolved auto cap: no USD cap, never a made-up number
    ],
)
def test_launch_cap_is_the_cap_claims_enforce(make_study, fake_study, study_cap, estimate_cap, expected):
    """Cross-WP (WP-10 -> WP-11): ``resolved_cap`` == ``Study.cap_usd`` == ``budget.claims.study_cap``."""
    from backend.generation.budget import claims
    from backend.generation.study.deps import get_deps
    from backend.generation.study.deps import override_deps

    base_estimate = get_deps().estimate_plan

    def estimate_plan(plan, **kwargs):
        return base_estimate(plan, **kwargs).model_copy(update={"cap_usd": estimate_cap})

    with override_deps(estimate_plan=estimate_plan):
        study = make_study()
        out = svc.launch(study, planner.resolve(study).plan_hash, study_cap=study_cap, pilot_override=True)
    study.refresh_from_db()
    assert study.cap_usd == expected
    assert claims.study_cap(study) == expected
    assert out["resolved_cap"] == (float(expected) if expected is not None else None)
