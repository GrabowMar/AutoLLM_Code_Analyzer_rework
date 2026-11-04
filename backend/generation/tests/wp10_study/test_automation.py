"""Schedules and post-runs (ported from the v2 scheduling automation; spec sections 3.6 and 9.4)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.generation.models import GenerationRun
from backend.generation.models import Study
from backend.generation.models import StudyCell
from backend.generation.models import StudySchedule
from backend.generation.models.choices import CellState
from backend.generation.models.choices import Outcome
from backend.generation.runs.queries import attrition
from backend.generation.runs.queries import cells_for_stage
from backend.generation.runs.queries import current_runs
from backend.generation.study import automation
from backend.generation.study import launch as svc
from backend.generation.study import plan as planner
from backend.generation.study.analysis_plan import PlanError
from backend.generation.study.analysis_plan import normalize_analysis_plan
from backend.generation.study.analysis_plan import pattern_run_label
from backend.generation.study.results import compute
from backend.generation.study.serialize import cells_payload

from .test_launch import finish

pytestmark = pytest.mark.django_db


def _launched(make_study, **overrides):
    study = make_study(**overrides)
    svc.launch(study, planner.resolve(study).plan_hash, pilot_override=True)
    study.refresh_from_db()
    return study


def _schedule(study, action, **params):
    fields = automation.validate_schedule({"action": action, "cron": "0 3 * * *", "params": params})
    fields["next_run_at"] = timezone.now() - timedelta(minutes=1)
    return StudySchedule.objects.create(study=study, **fields)


def test_top_up_schedule_refills_infra_cells_and_advances_without_burst(make_study, fake_study):
    study = _launched(make_study)
    for index, run in enumerate(GenerationRun.objects.filter(study=study).order_by("sequence")):
        finish(run, Outcome.INFRA_ERROR if index < 2 else Outcome.TESTED, pass_rate=None if index < 2 else 1.0)
    Study.objects.filter(pk=study.pk).update(status="completed")
    schedule = _schedule(study, "top_up")
    schedule.next_run_at = timezone.now() - timedelta(days=3)  # an executor that was down for days
    schedule.save()
    now = timezone.now()
    assert automation.fire_due_schedules(now) == 1
    schedule.refresh_from_db()
    assert schedule.last_result["outcome"] == "fired"
    assert schedule.last_result["runs_created"] == 2
    assert schedule.next_run_at > now  # one fire, no catch-up burst
    study.refresh_from_db()
    assert study.status == "running"  # reopened by the top-up
    assert automation.fire_due_schedules(now) == 0


def test_skip_if_active_and_add_replicates(make_study, fake_study):
    study = _launched(make_study)
    schedule = _schedule(study, "add_replicates", replicates=1)
    automation.fire_due_schedules()
    schedule.refresh_from_db()
    assert schedule.last_result == {"outcome": "skipped", "detail": "study still active"}
    for run in GenerationRun.objects.filter(study=study):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    result = automation.run_schedule_action(schedule.pk)
    assert result["outcome"] == "fired"
    assert result["runs_created"] == 16
    extra = GenerationRun.objects.filter(study=study, repeat_index=2)
    assert extra.count() == 16
    assert all(run.beyond_plan for run in extra)


def test_a_finished_replicate_cell_is_done_and_outside_the_planned_sample(make_study, fake_study):
    """Review fix: replicate cells keep their own current run/state and never count as pending planned cells."""
    study = _launched(make_study)
    for run in GenerationRun.objects.filter(study=study):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    Study.objects.filter(pk=study.pk).update(status="completed")
    planned_main = cells_for_stage(study, "main").count()
    assert svc.add_replicates(study, 1)["created_runs"] == 16
    replicates = StudyCell.objects.filter(study=study, beyond_plan=True)
    assert replicates.count() == 16
    assert cells_for_stage(study, "main").count() == planned_main  # the planned sample is unchanged

    done = finish(GenerationRun.objects.filter(study=study, beyond_plan=True).order_by("sequence")[0],
                  Outcome.TESTED, pass_rate=0.0)  # fmt: skip
    cell = StudyCell.objects.get(pk=done.cell_id)
    assert cell.beyond_plan is True
    assert (cell.state, cell.current_run_id) == (CellState.DONE, done.pk)
    # The remaining replicate runs are still queued: not "planned" (never run).
    assert set(replicates.exclude(pk=cell.pk).values_list("state", flat=True)) == {CellState.QUEUED}

    payload = cells_payload(study, state=None, condition=None, unit=None, stage="main")
    row = next(r for r in payload["cells"] if r["id"] == cell.pk)
    assert row["beyond_plan"] is True
    assert (row["state"], row["current_run"]["id"], row["attempts"]) == ("done", str(done.pk), 1)
    assert sum(1 for r in payload["cells"] if r["beyond_plan"]) == 16

    # Results and attrition analyse the planned sample only: nothing pending, the replicate is not current.
    study.refresh_from_db()
    assert not current_runs(study).filter(pk=done.pk).exists()
    assert current_runs(study).count() == planned_main
    res = compute(study)
    assert all(row["n_missing"]["pending"] == 0 for row in res["leaderboard"])
    assert all(row["n_cells"] * len(res["leaderboard"]) == planned_main for row in res["leaderboard"])
    assert all(row["pass_rate_mean"] == 1.0 for row in res["leaderboard"])  # the 0.0 replicate is not pooled
    rows = attrition(study)
    assert sum(r["cells_without_run"] for r in rows) == 0
    assert sum(r["beyond_plan_runs"] for r in rows) == 16


def test_clone_and_run_is_an_independent_replication(make_study, fake_study):
    study = _launched(make_study)
    for run in GenerationRun.objects.filter(study=study):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    schedule = _schedule(study, "clone_and_run")
    result = automation.run_schedule_action(schedule.pk)
    assert result["outcome"] == "fired", result
    clone = Study.objects.get(pk=result["study_id"])
    assert clone.cloned_from_id == study.pk
    assert clone.base_seed != study.base_seed
    assert clone.status == "running"  # the source reached main, so no second pilot
    assert clone.active_plan.plan_hash != study.active_plan.plan_hash


def test_analysis_post_run_without_tools_runs_the_study_report(make_study, fake_study):
    study = _launched(make_study)
    for run in GenerationRun.objects.filter(study=study):
        finish(run, Outcome.TESTED, pass_rate=1.0)
    Study.objects.filter(pk=study.pk).update(status="completed")
    post_run = automation.create_post_run(study, kind="analysis", trigger="manual", config={"report": True})
    automation.run_post_run(post_run)
    post_run.refresh_from_db()
    assert post_run.status == "completed", post_run.error
    assert post_run.report is not None
    assert post_run.report.config == {"study_id": str(study.pk)}


def test_post_run_events_match_the_study_event_contract(
    make_study,
    fake_study,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    # E2E-22: the event carried ``kind``, which the contract forbids, so every post_run event was refused.
    from backend.generation.contracts.events import parse_study_event
    from backend.realtime import events as realtime_events

    published = []
    monkeypatch.setattr(realtime_events, "publish_study_event", lambda study_id, event: published.append(event))
    study = _launched(make_study)
    with django_capture_on_commit_callbacks(execute=True):
        post_run = automation.create_post_run(study, kind="analysis", trigger="manual", config={"report": True})
        automation._publish(post_run)
    assert published
    for event in published:
        assert parse_study_event(event).type == "post_run"


def test_a_failed_patterns_post_run_is_recorded_not_silent(make_study, fake_study):
    study = _launched(make_study)  # an app study: pattern analysis needs slot variants
    post_run = automation.create_post_run(study, kind="patterns", trigger="manual", config={})
    automation.run_post_run(post_run)
    post_run.refresh_from_db()
    assert post_run.status == "failed"
    assert "slot" in post_run.error


def test_analysis_plan_normalization_and_itt_default():
    plan = normalize_analysis_plan({"primary_metric": "pass_rate"}, kind="confirmatory")
    assert plan["itt"] is True
    assert plan["exclusions"]["truncated"] is False
    assert plan["sensitivity"] == ["exclude_truncated"]
    with pytest.raises(PlanError):
        normalize_analysis_plan({"primary_metric": "pass_rate", "itt": True, "exclusions": {"truncated": True}})
    with pytest.raises(PlanError):
        normalize_analysis_plan(None, kind="confirmatory")
    with pytest.raises(PlanError):
        normalize_analysis_plan({"primary_metric": "pass_rate", "patterns": {"primary_features": ["x"]}})
    sequential = normalize_analysis_plan(
        {"primary_metric": "pass_rate", "sequential": {"looks": [10, 20], "alpha_spending": "pocock"}},
    )
    assert sequential["sequential"]["binding"] is False


def test_pattern_run_is_planned_only_when_it_matches_the_locked_plan(make_study, fake_study):
    from backend.generation.testing.factories import PatternRunFactory

    patterns = {"detector_set_hash": "a" * 64, "primary_features": ["sqli"], "normalizer_version": 1}
    plan = {"primary_metric": "pass_rate", "research_questions": [{"question": "q"}], "patterns": patterns}
    study = _launched(make_study, kind="confirmatory", analysis_plan=plan)
    planned = PatternRunFactory.create(study=study, detector_set_hash="a" * 64, normalizer_version=1)
    other = PatternRunFactory.create(study=study, detector_set_hash="b" * 64, normalizer_version=1)
    assert pattern_run_label(study, planned) == "planned"
    assert pattern_run_label(study, other) == "exploratory"
