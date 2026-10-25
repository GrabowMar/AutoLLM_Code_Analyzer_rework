"""Experiment schedules (spec §F.5): cron validation, the due tick, skip rules and the three actions."""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from unittest import mock

import pytest
from django.db import connection
from django.db import transaction
from django.utils import timezone

from backend.execution.models import ExecutorSettings
from backend.generation.models import Experiment
from backend.generation.models import ExperimentSchedule
from backend.generation.models import GenerationJob
from backend.generation.services import experiment_automation as automation
from backend.generation.services.experiments import ExperimentError
from backend.generation.services.experiments import launch_experiment
from backend.generation.tests.test_experiments_v2_api import _local_endpoint
from backend.generation.tests.test_experiments_v2_api import _local_model
from backend.generation.tests.test_experiments_v2_api import _scaffolding_experiment
from backend.users.tests.factories import UserFactory

API = "/api/generation"
ENQUEUE = "backend.generation.services.experiments.enqueue_jobs"
S = GenerationJob.Status


@pytest.fixture(autouse=True)
def _env():
    with (
        mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True),
        mock.patch(ENQUEUE),
    ):
        yield


@pytest.fixture
def user():
    return UserFactory()


def _schedule(experiment, action="top_up", *, due=True, **kw):
    fields = automation.validate_schedule({"action": action, "cron": kw.pop("cron", "0 3 * * *"), **kw})
    if due:
        fields["next_run_at"] = timezone.now() - timedelta(minutes=1)
    return ExperimentSchedule.objects.create(experiment=experiment, created_by=experiment.created_by, **fields)


def _finished(user, **kw):
    experiment, conditions = _scaffolding_experiment(user, **kw)
    launch_experiment(experiment, user)
    experiment.jobs.update(status=S.COMPLETED)
    Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.COMPLETED)
    experiment.refresh_from_db()
    return experiment, conditions


# ── cron ──────────────────────────────────────────────────────────────


class TestCron:
    def test_minimum_interval_is_fifteen_minutes(self):
        assert automation.cron_preview("*/15 * * * *")["valid"] is True
        bad = automation.cron_preview("*/5 * * * *")
        assert (bad["valid"], bad["next_runs"]) == (False, [])
        assert "15 minutes" in bad["detail"]

    def test_invalid_cron_timezone_and_params(self):
        with pytest.raises(ExperimentError, match="cron"):
            automation.validate_schedule({"action": "top_up", "cron": "nope"})
        with pytest.raises(ExperimentError, match="timezone"):
            automation.validate_schedule({"action": "top_up", "cron": "0 3 * * *", "timezone": "Mars/Base"})
        with pytest.raises(ExperimentError, match="replicates"):
            automation.validate_schedule(
                {"action": "add_replicates", "cron": "0 3 * * *", "params": {"replicates": 9}},
            )
        with pytest.raises(ExperimentError, match="name_template"):
            automation.validate_schedule(
                {"action": "clone_and_run", "cron": "0 3 * * *", "params": {"name_template": "{owner}"}},
            )

    def test_next_run_honours_the_schedule_timezone(self):
        fields = automation.validate_schedule({"action": "top_up", "cron": "0 3 * * *", "timezone": "Asia/Tokyo"})
        assert fields["next_run_at"].astimezone(timezone.get_fixed_timezone(9 * 60)).hour == 3
        disabled = automation.validate_schedule({"action": "top_up", "cron": "0 3 * * *", "enabled": False})
        assert disabled["next_run_at"] is None


# ── the due tick ──────────────────────────────────────────────────────


@pytest.mark.django_db
class TestDueTick:
    def test_due_schedules_are_claimed_and_advanced_without_burst_catch_up(self, user):
        experiment, _ = _finished(user)
        schedule = _schedule(experiment, cron="0 * * * *")
        ExperimentSchedule.objects.filter(id=schedule.id).update(next_run_at=timezone.now() - timedelta(days=3))
        submitted = []
        now = timezone.now()
        ids = automation.run_due_schedules(now, submit=lambda fn, sid: submitted.append((fn, sid)))
        assert ids == [schedule.id]
        assert submitted == [(automation.run_schedule_action, schedule.id)]
        schedule.refresh_from_db()
        assert now < schedule.next_run_at <= now + timedelta(hours=1)
        # Not due again in this tick or the next one.
        assert automation.run_due_schedules(now) == []

    def test_skip_if_active(self, user):
        experiment, _ = _scaffolding_experiment(user)
        launch_experiment(experiment, user)  # pending jobs
        schedule = _schedule(experiment)
        assert automation.run_due_schedules(timezone.now()) == []
        schedule.refresh_from_db()
        assert schedule.last_result["outcome"] == "skipped"
        assert schedule.last_run_at is not None
        loose = _schedule(experiment, skip_if_active=False)
        assert automation.run_due_schedules(timezone.now()) == [loose.id]

    def test_paused_experiments_count_as_active(self, user):
        experiment, _ = _finished(user)
        Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.PAUSED)
        _schedule(experiment)
        assert automation.run_due_schedules(timezone.now()) == []

    def test_draining_stops_schedule_firing(self, user):
        experiment, _ = _finished(user)
        schedule = _schedule(experiment)
        ExecutorSettings.objects.filter(pk=ExecutorSettings.get().pk).update(draining=True)
        assert automation.run_due_schedules(timezone.now()) == []
        schedule.refresh_from_db()
        assert schedule.next_run_at < timezone.now()  # untouched: it fires after the drain

    def test_disabled_and_archived(self, user):
        experiment, _ = _finished(user)
        _schedule(experiment, enabled=False)
        archived = _schedule(experiment)
        Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.ARCHIVED)
        assert automation.run_due_schedules(timezone.now()) == []
        archived.refresh_from_db()
        assert archived.enabled is False


@pytest.mark.django_db(transaction=True)
def test_concurrent_ticks_fire_once(user):
    experiment, _ = _finished(user)
    schedule = _schedule(experiment)
    locked, release = threading.Event(), threading.Event()

    def hold_the_row():
        try:
            with transaction.atomic():
                ExperimentSchedule.objects.select_for_update().get(id=schedule.id)
                locked.set()
                release.wait(10)
        finally:
            connection.close()

    holder = threading.Thread(target=hold_the_row)
    holder.start()
    assert locked.wait(10)
    try:
        # The row is locked by the other tick: SKIP LOCKED passes over it.
        assert automation.run_due_schedules(timezone.now()) == []
    finally:
        release.set()
        holder.join(10)
    assert automation.run_due_schedules(timezone.now()) == [schedule.id]
    assert automation.run_due_schedules(timezone.now()) == []


# ── actions ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestActions:
    def test_top_up_reopens_and_refills_failed_cells(self, user):
        experiment, (c1, _c2) = _finished(user)
        experiment.jobs.filter(condition=c1).update(status=S.FAILED)
        schedule = _schedule(experiment)
        result = automation.run_schedule_action(schedule.id)
        assert (result["outcome"], result["jobs_created"]) == ("fired", 1)
        experiment.refresh_from_db()
        assert experiment.status == Experiment.Status.RUNNING
        assert experiment.batches.get(id=result["batch_id"]).kind == "schedule"
        schedule.refresh_from_db()
        assert schedule.last_result == result

    def test_add_replicates_adds_new_repeat_blocks(self, user):
        experiment, _ = _finished(user)
        schedule = _schedule(experiment, "add_replicates", params={"replicates": 2})
        result = automation.run_schedule_action(schedule.id)
        experiment.refresh_from_db()
        assert experiment.repeats == 3
        assert result["jobs_created"] == 4
        assert set(experiment.jobs.filter(status=S.PENDING).values_list("repeat_index", flat=True)) == {1, 2}

    def test_clone_and_run(self, user):
        experiment, _ = _finished(user)
        schedule = _schedule(experiment, "clone_and_run", params={"name_template": "{name} nightly {date}"})
        result = automation.run_schedule_action(schedule.id)
        assert result["outcome"] == "fired", result
        clone = Experiment.objects.get(id=result["experiment_id"])
        assert (clone.cloned_from_id, clone.created_by_schedule_id) == (experiment.id, schedule.id)
        assert clone.name.startswith(f"{experiment.name} nightly ")
        assert clone.status == Experiment.Status.RUNNING
        assert clone.schedules.count() == 0
        assert clone.jobs.count() == 2

    def test_digest_drift_is_recorded_not_raised(self, user):
        endpoint = _local_endpoint(user)
        model = _local_model(endpoint, digest="sha256:old")
        experiment, _ = _scaffolding_experiment(user, n_conditions=1)
        experiment.conditions.update(model=model)
        launch_experiment(experiment, user)
        experiment.jobs.update(status=S.FAILED)
        model.digest = "sha256:new"
        model.save(update_fields=["digest"])
        for action in ("top_up", "add_replicates"):
            schedule = _schedule(experiment, action)
            result = automation.run_schedule_action(schedule.id)
            assert (result["outcome"], result["detail"]) == ("error", "digest_drift")
        experiment.refresh_from_db()
        assert experiment.repeats == 1

    def test_unexpected_errors_are_recorded(self, user):
        experiment, _ = _finished(user)
        experiment.jobs.update(status=S.FAILED)
        schedule = _schedule(experiment)
        with mock.patch(
            "backend.generation.services.experiments.launch_experiment",
            side_effect=RuntimeError("daemon down"),
        ):
            result = automation.run_schedule_action(schedule.id)
        assert result["outcome"] == "error"
        assert "daemon down" in result["detail"]

    def test_top_up_and_add_replicates_never_launch_a_draft(self, user):
        experiment, _ = _scaffolding_experiment(user)
        for action in ("top_up", "add_replicates"):
            result = automation.run_schedule_action(_schedule(experiment, action).id)
            assert (result["outcome"], result["detail"]) == ("skipped", "experiment not launched yet")
        experiment.refresh_from_db()
        assert (experiment.status, experiment.repeats, experiment.jobs.count()) == (Experiment.Status.DRAFT, 1, 0)

    def test_a_refused_add_replicates_launch_keeps_repeats(self, user):
        experiment, _ = _finished(user)
        schedule = _schedule(experiment, "add_replicates", params={"replicates": 2})
        refused = ExperimentError(400, "No OpenRouter API key is configured.", "missing_api_key")
        with mock.patch("backend.generation.services.experiments.launch_experiment", side_effect=refused):
            for _ in range(3):
                result = automation.run_schedule_action(schedule.id)
                assert (result["outcome"], result["code"]) == ("error", "missing_api_key")
        experiment.refresh_from_db()
        assert experiment.repeats == 1

    def test_a_clone_whose_launch_fails_is_reported(self, user):
        experiment, _ = _finished(user)
        schedule = _schedule(experiment, "clone_and_run")
        refused = ExperimentError(400, "refused", "missing_api_key")
        with mock.patch("backend.generation.services.experiments.launch_experiment", side_effect=refused):
            result = automation.run_schedule_action(schedule.id)
        assert result["outcome"] == "error"
        clone = Experiment.objects.get(id=result["experiment_id"])
        assert (clone.cloned_from_id, clone.status) == (experiment.id, Experiment.Status.DRAFT)


# ── API ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_schedule_crud_and_run_now(client, user):
    client.force_login(user)
    experiment, _ = _finished(user)
    base = f"{API}/experiments/{experiment.id}/schedules/"
    send = lambda m, p, b=None: getattr(client, m)(p, data=json.dumps(b or {}), content_type="application/json")  # noqa: E731

    res = send("post", base, {"action": "top_up", "cron": "*/5 * * * *", "timezone": "UTC", "skip_if_active": True})
    assert (res.status_code, res.json()["code"]) == (400, "invalid_schedule")
    res = send("post", base, {"action": "top_up", "cron": "30 2 * * 1", "timezone": "UTC", "skip_if_active": True})
    assert res.status_code == 200, res.content
    schedule = res.json()
    assert schedule["next_run_at"] is not None

    res = send("patch", f"{base}{schedule['id']}/", {"enabled": False})
    assert (res.json()["enabled"], res.json()["next_run_at"]) == (False, None)
    res = send("patch", f"{base}{schedule['id']}/", {"cron": "* * * * *"})
    assert res.status_code == 400

    # Every cell is completed: nothing to top up, and no empty batch is created.
    batches = experiment.batches.count()
    res = send("post", f"{base}{schedule['id']}/run-now/")
    assert res.json() == {"outcome": "skipped", "batch_id": None, "experiment_id": None, "jobs_created": 0}
    assert experiment.batches.count() == batches
    experiment.jobs.update(status=S.FAILED)
    res = send("post", f"{base}{schedule['id']}/run-now/")
    assert (res.json()["outcome"], res.json()["experiment_id"], res.json()["jobs_created"]) == (
        "fired",
        str(experiment.id),
        2,
    )
    assert client.get(base).json()[0]["last_result"]["outcome"] == "fired"
    assert client.delete(f"{base}{schedule['id']}/").json() == {"success": True}
    other = ExperimentSchedule.objects.create(
        experiment=_finished(UserFactory())[0],
        action="top_up",
        cron="0 3 * * *",
        created_by=user,
    )
    assert client.delete(f"{base}{other.id}/").status_code == 404
