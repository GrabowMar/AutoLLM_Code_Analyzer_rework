"""Experiments API v2 (spec §F.1): one-call design, preview, launch and lifecycle, read models, results."""

from __future__ import annotations

import itertools
import json
import math
import time
import uuid
from unittest import mock

import pytest
from django.utils import timezone

from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.services import experiment_results as results_svc
from backend.generation.services import experiments as svc
from backend.generation.services.experiment_automation import pause_for_digest_drift
from backend.generation.services.experiment_results import ResultFilters
from backend.generation.services.experiment_results import compute
from backend.generation.services.presets import system_default_preset
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.inference.models import LLMEndpoint
from backend.inference.models import LLMRequest
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"
ENQUEUE = "backend.generation.services.experiments.enqueue_jobs"
S = GenerationJob.Status


@pytest.fixture(autouse=True)
def _env(settings):
    settings.AGENT_AUTO_EVALUATE = False
    with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True):
        yield


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def api(client, user):
    client.force_login(user)
    return client


def _send(client, method, path, payload=None):
    if method in ("get", "delete"):
        return getattr(client, method)(path)
    return getattr(client, method)(path, data=json.dumps(payload or {}), content_type="application/json")


def _apps(n=1):
    return [AppRequirementTemplateFactory(is_default=True) for _ in range(n)]


def _models(n, **kw):
    return [LLMModelFactory(model_id=f"vendor/m{uuid.uuid4().hex[:8]}", **kw) for _ in range(n)]


def _profile() -> GenerationProfile:
    return GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()


def _presets():
    return system_default_preset("harness", "aider"), system_default_preset("harness", "mini-swe-agent")


def _create_body(models, apps, *, presets=(), **kw):
    body = {
        "name": kw.pop("name", "Sweep"),
        "slug": kw.pop("slug", f"sweep-{uuid.uuid4().hex[:6]}"),
        "repeats": kw.pop("repeats", 1),
        "base_seed": kw.pop("base_seed", 11),
        "stack_slug": "flask-react",
        "app_requirement_ids": [a.id for a in apps],
        "conditions": {"cross": {"model_ids": [m.id for m in models], "harness_preset_ids": [p.id for p in presets]}},
    }
    body.update(kw)
    return body


def _scaffolding_experiment(user, *, n_conditions=2, n_apps=1, repeats=1, **kw):
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=repeats, base_seed=3, **kw)
    experiment.app_requirements.set(_apps(n_apps))
    conditions = [
        ExperimentConditionFactory(experiment=experiment, profile=_profile(), model=model)
        for model in _models(n_conditions)
    ]
    experiment.baseline_condition = conditions[0]
    experiment.save(update_fields=["baseline_condition"])
    return experiment, conditions


def _launch(experiment, user, **kw):
    with mock.patch(ENQUEUE) as enqueue:
        batch = svc.launch_experiment(experiment, user, **kw)
    return batch, enqueue


def _local_endpoint(owner, **kw):
    kw.setdefault("slug", f"gpu-{uuid.uuid4().hex[:6]}")
    kw.setdefault("name", "GPU box")
    kw.setdefault("kind", LLMEndpoint.Kind.OLLAMA)
    return LLMEndpoint.objects.create(owner=owner, **kw)


def _local_model(endpoint, name="qwen3:8b", digest="sha256:aaa"):
    return LLMModelFactory(
        model_id=f"local-{endpoint.slug}/{name.replace(':', '-')}",
        endpoint=endpoint,
        served_model_name=name,
        digest=digest,
        is_free=True,
        input_price_per_token=0,
        output_price_per_token=0,
    )


# ── create: one call with a cross-product ─────────────────────────────


class TestCreate:
    def test_cross_product_creates_every_condition_in_one_call(self, api, user, django_assert_max_num_queries):
        models, apps = _models(30), _apps(2)
        body = _create_body(models, apps, presets=_presets())
        # O(N): a handful of queries per condition (insert + preset/profile lookups are cached).
        with django_assert_max_num_queries(60 * 3 + 40):
            res = _send(api, "post", f"{API}/experiments/", body)
        assert res.status_code == 200, res.content
        detail = res.json()
        assert detail["status"] == "draft"
        assert detail["condition_count"] == 60
        assert detail["model_count"] == 30
        assert len(detail["conditions"]) == 60
        # The pre-registered baseline defaults to the first cross condition.
        first = detail["conditions"][0]
        assert detail["baseline_condition_id"] == first["id"]
        assert first["label"].startswith(models[0].model_name)
        assert " · aider@aider-default" in first["label"]

    def test_duplicates_are_skipped_and_reported_by_bulk(self, api, user):
        models, apps = _models(2), _apps()
        aider, _mini = _presets()
        res = _send(api, "post", f"{API}/experiments/", _create_body(models, apps, presets=[aider]))
        experiment_id = res.json()["id"]
        bulk = _send(
            api,
            "post",
            f"{API}/experiments/{experiment_id}/conditions/bulk/",
            # Harness "aider" with no preset runs the aider default preset: the same arm.
            {"cross": {"model_ids": [models[0].id], "harnesses": ["aider", "opencode"]}},
        )
        assert bulk.status_code == 200, bulk.content
        body = bulk.json()
        assert [c["harness"] for c in body["created"]] == ["opencode"]
        assert [s["reason"] for s in body["skipped"]] == ["duplicate"]
        assert body["skipped"][0]["key"] == svc.choice_key(models[0].id, None, "aider", None)

    def test_scaffolding_cross_uses_profiles_and_ineligible_profiles_are_skipped(self, api, user):
        from backend.generation.tests.factories import GenerationProfileFactory

        models, apps = _models(2), _apps()
        fastapi = GenerationProfileFactory(scaffolding_slug="fastapi-react")
        body = _create_body(models, apps, mode="scaffolding")
        body["conditions"] = {"cross": {"model_ids": [m.id for m in models], "profile_ids": [_profile().id]}}
        res = _send(api, "post", f"{API}/experiments/", body)
        assert res.status_code == 200, res.content
        experiment_id = res.json()["id"]
        bulk = _send(
            api,
            "post",
            f"{API}/experiments/{experiment_id}/conditions/bulk/",
            {"cross": {"model_ids": [models[0].id], "profile_ids": [fastapi.id]}},
        )
        assert bulk.json()["skipped"][0]["reason"] == "ineligible"
        missing = _send(
            api,
            "post",
            f"{API}/experiments/",
            {**body, "slug": "no-profile", "conditions": {"cross": {"model_ids": [models[0].id]}}},
        )
        assert missing.status_code == 400
        assert missing.json()["code"] == "profile_required"

    def test_invisible_model_is_404_and_writes_nothing(self, api, user):
        private = _local_model(_local_endpoint(UserFactory()))
        res = _send(api, "post", f"{API}/experiments/", _create_body([private], _apps()))
        assert res.status_code == 404
        assert not Experiment.objects.filter(created_by=user).exists()

    def test_execution_post_run_and_schedules_in_the_create_body(self, api, user):
        body = _create_body(
            _models(1),
            _apps(),
            execution={"scheduling_policy": "blocked", "priority": 7, "max_concurrency": 4, "infra_retries": 1},
            post_run={"evaluate": True, "report": {"enabled": True, "exclude_truncated": False}},
            schedules=[{"action": "top_up", "cron": "0 3 * * *", "timezone": "Europe/Warsaw"}],
        )
        res = _send(api, "post", f"{API}/experiments/", body)
        assert res.status_code == 200, res.content
        detail = res.json()
        assert (detail["scheduling_policy"], detail["max_concurrency"], detail["infra_retries"]) == ("blocked", 4, 1)
        assert detail["priority"] == 0  # non-staff are clamped to -5..0
        assert detail["post_run"]["report"] == {"enabled": True, "exclude_truncated": False}
        assert detail["has_schedule"] is True
        assert detail["schedules"][0]["next_run_at"] is not None
        assert detail["next_run_at"] == detail["schedules"][0]["next_run_at"]

    def test_create_and_launch_keeps_the_draft_when_the_launch_fails(self, api, user):
        body = _create_body(_models(1), [])  # no apps: the launch refuses
        res = api.post(f"{API}/experiments/?launch=true", data=json.dumps(body), content_type="application/json")
        assert res.status_code == 400
        assert res.json()["code"] == "no_apps"
        draft = Experiment.objects.get(id=res.json()["experiment_id"])
        assert draft.status == Experiment.Status.DRAFT
        assert draft.conditions.count() == 1

    def test_create_and_launch(self, api, user):
        body = _create_body(_models(2), _apps())
        with mock.patch(ENQUEUE):
            res = api.post(f"{API}/experiments/?launch=true", data=json.dumps(body), content_type="application/json")
        assert res.status_code == 201, res.content
        payload = res.json()
        assert payload["jobs_created"] == 2
        assert payload["experiment"]["status"] == "running"
        assert payload["policy_effective_by_endpoint"] == {"openrouter": "interleaved"}

    def test_1500_cells_launch_within_budget(self, user, django_assert_max_num_queries):
        experiment, _conditions = _scaffolding_experiment(user, n_conditions=15, n_apps=20, repeats=5)
        started = time.monotonic()
        # O(cells): the per-job snapshot build dominates (about 6 queries per scaffolding job).
        with django_assert_max_num_queries(1500 * 7 + 200):
            batch, enqueue = _launch(experiment, user)
        elapsed = time.monotonic() - started
        assert batch.total_jobs == 1500
        assert sum(len(c.args[0]) for c in enqueue.call_args_list) == 1500
        assert elapsed < 120, elapsed


# ── preview ───────────────────────────────────────────────────────────


class TestPreview:
    def test_stateless_preview_writes_nothing(self, api, user):
        before = (Experiment.objects.count(), ExperimentCondition.objects.count())
        res = _send(api, "post", f"{API}/experiments/preview/", _create_body(_models(3), _apps(2), repeats=2))
        assert res.status_code == 200, res.content
        preview = res.json()
        assert (Experiment.objects.count(), ExperimentCondition.objects.count()) == before
        assert preview["total_jobs"] == 3 * 2 * 2
        assert preview["schedule"]["policy_effective_by_endpoint"] == {"openrouter": "interleaved"}
        assert preview["endpoints"][0]["slug"] == "openrouter"
        assert preview["endpoints"][0]["cells"] == 12
        assert preview["cost"]["kind"] == "budget_upper_bound"
        assert preview["capacity"]["sandbox_slots"] >= 1

    def test_local_endpoint_plan_and_issues(self, api, user):
        endpoint = _local_endpoint(user, max_resident_models=1)
        models = [_local_model(endpoint, "a:1", "sha256:a"), _local_model(endpoint, "b:1", "")]
        models[0].supports_function_calling = False
        models[0].save()
        body = _create_body(models, _apps(2), repeats=2, execution={"scheduling_policy": "interleaved"})
        preview = _send(api, "post", f"{API}/experiments/preview/", body).json()
        row = preview["endpoints"][0]
        assert (row["slug"], row["resident_limit"], row["online"]) == (endpoint.slug, 1, False)
        assert preview["cost"] == {"openrouter_upper_bound_usd": 0.0, "local_cells": 8, "kind": "local_zero"}
        codes = {i["code"]: i["blocking"] for i in preview["issues"]}
        assert codes["models_without_tools"] is True
        assert codes["digest_unknown"] is False
        assert codes["endpoint_offline"] is False
        assert codes["model_thrash_risk"] is False
        assert preview["schedule"]["estimated_swaps"][endpoint.slug] >= 4

    def test_auto_policy_blocks_on_finite_k(self, api, user):
        endpoint = _local_endpoint(user, max_resident_models=1)
        body = _create_body([_local_model(endpoint)], _apps())
        preview = _send(api, "post", f"{API}/experiments/preview/", body).json()
        assert preview["schedule"]["policy_effective_by_endpoint"] == {endpoint.slug: "blocked"}


# ── launch and top-up ─────────────────────────────────────────────────


class TestLaunch:
    def test_launch_freezes_queue_fields(self, user):
        experiment, _conditions = _scaffolding_experiment(user, n_conditions=2, n_apps=2, repeats=2)
        experiment.priority = -3
        experiment.save(update_fields=["priority"])
        batch, _enqueue = _launch(experiment, user)
        experiment.refresh_from_db()
        assert (experiment.status, batch.kind, batch.experiment_id) == ("running", "launch", experiment.id)
        assert experiment.launched_at is not None
        jobs = list(experiment.jobs.all())
        assert len(jobs) == 8
        assert sorted(j.sequence for j in jobs) == list(range(8))
        assert {(j.lane, j.endpoint_id, j.priority) for j in jobs} == {("llm", None, -3)}
        assert {j.execution["policy"] for j in jobs} == {"interleaved"}
        # Randomised complete blocks: repeat 0 runs before repeat 1.
        by_sequence = sorted(jobs, key=lambda j: j.sequence)
        assert [j.repeat_index for j in by_sequence] == [0] * 4 + [1] * 4
        schedule = batch.request["schedule"]
        assert schedule["policy_effective_by_endpoint"] == {"openrouter": "interleaved"}
        assert schedule["order_seed"] == 3

    def test_real_enqueue_marks_jobs_for_the_executor(self, user):
        experiment, _ = _scaffolding_experiment(user)
        svc.launch_experiment(experiment, user)
        assert all(j.queued_at is not None for j in experiment.jobs.all())

    def test_top_up_limited_to_condition_ids_reopens_a_finished_experiment(self, user):
        experiment, (c1, _c2) = _scaffolding_experiment(user, n_apps=2)
        _launch(experiment, user)
        experiment.jobs.update(status=S.FAILED)
        Experiment.objects.filter(id=experiment.id).update(
            status=Experiment.Status.COMPLETED,
            completed_at=timezone.now(),
        )
        batch, _enqueue = _launch(experiment, user, mode=svc.TOP_UP, condition_ids=[c1.id])
        experiment.refresh_from_db()
        assert (experiment.status, experiment.completed_at) == ("running", None)
        assert batch.kind == "retry"
        new = experiment.jobs.filter(batch=batch)
        assert set(new.values_list("condition_id", flat=True)) == {c1.id}
        assert new.count() == 2
        original = {(j.condition_id, j.app_requirement_id): j.sequence for j in experiment.jobs.exclude(batch=batch)}
        # A top-up keeps each cell's frozen position.
        assert all(original[(j.condition_id, j.app_requirement_id)] == j.sequence for j in new)

    def test_raising_repeats_tops_up_new_cells_after_existing_ones(self, api, user):
        experiment, _ = _scaffolding_experiment(user, n_apps=2)
        _launch(experiment, user)
        res = _send(api, "patch", f"{API}/experiments/{experiment.id}/", {"repeats": 2})
        assert res.status_code == 200, res.content
        batch, _ = _launch(experiment, user, mode=svc.TOP_UP)
        assert batch.kind == "top_up"
        new = list(experiment.jobs.filter(batch=batch))
        old_max = max(experiment.jobs.exclude(batch=batch).values_list("sequence", flat=True))
        assert {j.repeat_index for j in new} == {1}
        assert min(j.sequence for j in new) > old_max

    def test_paused_experiment_stays_paused_on_top_up(self, user):
        experiment, _ = _scaffolding_experiment(user)
        _launch(experiment, user)
        svc.pause_experiment(experiment)
        experiment.jobs.update(status=S.FAILED)
        _launch(experiment, user, mode=svc.TOP_UP)
        experiment.refresh_from_db()
        assert experiment.status == Experiment.Status.PAUSED

    def test_launch_endpoint_returns_launch_result(self, api, user):
        experiment, (c1, _c2) = _scaffolding_experiment(user)
        with mock.patch(ENQUEUE):
            res = _send(api, "post", f"{API}/experiments/{experiment.id}/launch/", {})
            assert res.status_code == 200, res.content
            assert res.json()["jobs_created"] == 2
            experiment.jobs.filter(condition=c1).update(status=S.FAILED)
            res = _send(api, "post", f"{API}/experiments/{experiment.id}/launch/", {"mode": "top_up"})
        assert (res.json()["batch"]["kind"], res.json()["jobs_created"]) == ("retry", 1)

    def test_blocking_endpoint_issue_refuses_the_launch(self, user):
        endpoint = _local_endpoint(UserFactory(), visibility=LLMEndpoint.Visibility.SHARED)
        experiment, _ = _scaffolding_experiment(user, n_conditions=1)
        model = _local_model(endpoint)
        experiment.conditions.update(model=model)
        LLMEndpoint.objects.filter(id=endpoint.id).update(enabled=False)
        with pytest.raises(svc.ExperimentError) as exc:
            _launch(experiment, user)
        assert exc.value.code == "endpoint_disabled"
        assert not experiment.jobs.exists()


# ── lifecycle ─────────────────────────────────────────────────────────


class TestLifecycle:
    def test_pause_resume(self, api, user):
        experiment, _ = _scaffolding_experiment(user)
        _launch(experiment, user)
        res = _send(api, "post", f"{API}/experiments/{experiment.id}/pause/")
        assert res.json() | {"paused_at": None} == {
            "status": "paused",
            "pause_reason": "user",
            "paused_at": None,
            "running": 0,
            "pending": 2,
        }
        assert _send(api, "post", f"{API}/experiments/{experiment.id}/pause/").status_code == 400
        with mock.patch("backend.generation.services.experiments.wake"):
            res = _send(api, "post", f"{API}/experiments/{experiment.id}/resume/")
        assert (res.json()["status"], res.json()["pause_reason"]) == ("running", "")

    def test_cancel_delegates_to_the_executor_and_ends_the_experiment(self, api, user):
        experiment, _ = _scaffolding_experiment(user)
        _launch(experiment, user)
        counts = {"cancelled_pending": 2, "signalled_running": 0, "finishing_evaluations": 0}
        with mock.patch("backend.generation.services.experiments.cancel_jobs", return_value=counts) as cancel:
            res = _send(api, "post", f"{API}/experiments/{experiment.id}/cancel/", {"scope": "all"})
        assert res.status_code == 200, res.content
        assert cancel.call_args.kwargs == {"scope": "all"}
        assert set(cancel.call_args.args[0].values_list("id", flat=True)) == set(
            experiment.jobs.values_list("id", flat=True),
        )
        assert (res.json()["status"], res.json()["cancelled_pending"]) == ("cancelled", 2)

    def test_cancel_pending_scope_keeps_the_experiment_running(self, user):
        experiment, _ = _scaffolding_experiment(user)
        _launch(experiment, user)
        with mock.patch("backend.generation.services.experiments.cancel_jobs", return_value={}):
            state = svc.cancel_experiment(experiment, "pending")
        assert state["status"] == "running"

    def test_archive_refused_while_jobs_are_active(self, api, user):
        experiment, _ = _scaffolding_experiment(user)
        _launch(experiment, user)
        res = _send(api, "post", f"{API}/experiments/{experiment.id}/archive/")
        assert (res.status_code, res.json()["code"]) == (400, "active_jobs")
        experiment.jobs.update(status=S.COMPLETED)
        assert _send(api, "post", f"{API}/experiments/{experiment.id}/archive/").json()["status"] == "archived"

    def test_digest_drift_pauses_and_resume_requires_acceptance(self, api, user):
        endpoint = _local_endpoint(user)
        model = _local_model(endpoint, digest="sha256:old")
        experiment, (condition,) = _scaffolding_experiment(user, n_conditions=1, n_apps=2)
        experiment.conditions.update(model=model)
        _launch(experiment, user)
        condition.refresh_from_db()
        assert condition.pinned_digest == "sha256:old"

        model.digest = "sha256:new"
        model.save(update_fields=["digest"])
        assert pause_for_digest_drift(model) == [experiment.id]
        experiment.refresh_from_db()
        assert (experiment.status, experiment.pause_reason) == ("paused", "digest_changed")

        res = _send(api, "post", f"{API}/experiments/{experiment.id}/resume/")
        assert (res.status_code, res.json()["code"]) == (400, "digest_drift")
        with mock.patch("backend.generation.services.experiments.wake"):
            res = _send(api, "post", f"{API}/experiments/{experiment.id}/resume/", {"accept_digest_drift": True})
        assert res.json()["status"] == "running"
        condition.refresh_from_db()
        assert condition.pinned_digest == "sha256:new"
        history = experiment.batches.get().request["schedule"]["history"]
        assert history[0] | {"at": None} == {
            "event": "digest_accepted",
            "condition_id": condition.id,
            "from": "sha256:old",
            "to": "sha256:new",
            "at": None,
            "user": user.username,
        }

    def test_top_up_with_drift_is_blocked(self, user):
        endpoint = _local_endpoint(user)
        model = _local_model(endpoint, digest="sha256:old")
        experiment, _ = _scaffolding_experiment(user, n_conditions=1)
        experiment.conditions.update(model=model)
        _launch(experiment, user)
        experiment.jobs.update(status=S.FAILED)
        model.digest = "sha256:new"
        model.save(update_fields=["digest"])
        with pytest.raises(svc.ExperimentError) as exc:
            _launch(experiment, user, mode=svc.TOP_UP)
        assert exc.value.code == "digest_drift"

    def test_priority_patch_updates_pending_jobs_and_queued_requests(self, api, user, settings):
        experiment, _ = _scaffolding_experiment(user)
        _launch(experiment, user)
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        job = experiment.jobs.first()
        endpoint = _local_endpoint(user)
        request = LLMRequest.objects.create(
            endpoint=endpoint,
            job=job,
            source="scaffolding",
            path="/api/chat",
            deadline_at=timezone.now(),
        )
        with mock.patch("backend.generation.services.experiments.wake"):
            res = _send(api, "patch", f"{API}/experiments/{experiment.id}/", {"priority": 5})
        assert res.status_code == 200, res.content
        assert set(experiment.jobs.values_list("priority", flat=True)) == {5}
        request.refresh_from_db()
        assert request.priority == 5

    def test_policy_change_only_while_paused_and_it_resequences(self, api, user):
        experiment, _ = _scaffolding_experiment(user, n_apps=2, repeats=2)
        _launch(experiment, user)
        url = f"{API}/experiments/{experiment.id}/"
        res = _send(api, "patch", url, {"scheduling_policy": "grouped"})
        assert (res.status_code, res.json()["code"]) == (400, "not_paused")
        svc.pause_experiment(experiment)
        res = _send(api, "patch", url, {"scheduling_policy": "grouped"})
        assert res.status_code == 200, res.content
        assert {j.execution["policy"] for j in experiment.jobs.all()} == {"grouped"}
        history = experiment.batches.get().request["schedule"]["history"]
        assert (history[-1]["from"], history[-1]["to"]) == ("auto", "grouped")

    def test_baseline_frozen_after_launch(self, api, user):
        experiment, (_c1, c2) = _scaffolding_experiment(user)
        _launch(experiment, user)
        res = _send(api, "patch", f"{API}/experiments/{experiment.id}/", {"baseline_condition_id": c2.id})
        assert res.status_code == 400
        assert "frozen" in res.json()["detail"]

    def test_requeue_legacy_jobs(self, api, user):
        experiment, (c1, c2) = _scaffolding_experiment(user)
        _launch(experiment, user)
        experiment.jobs.filter(condition=c1).update(queued_at=None, status=S.RUNNING, phase="queued")
        experiment.jobs.filter(condition=c2).update(queued_at=None, status=S.COMPLETED)
        status = _send(api, "get", f"{API}/experiments/{experiment.id}/status/").json()
        assert status["legacy_pending"] == 1  # drives the "Requeue legacy runs" banner
        res = _send(api, "post", f"{API}/experiments/{experiment.id}/jobs/requeue-legacy/")
        assert res.json() == {"requeued": 1, "batches_refreshed": 1}
        assert _send(api, "get", f"{API}/experiments/{experiment.id}/status/").json()["legacy_pending"] == 0
        job = experiment.jobs.get(condition=c1)
        assert (job.status, job.phase, job.queued_at is not None) == (S.PENDING, "", True)
        assert experiment.jobs.get(condition=c2).queued_at is None

    def test_clone(self, api, user):
        experiment, (c1, c2) = _scaffolding_experiment(user, n_apps=2)
        experiment.baseline_condition = c2
        experiment.save(update_fields=["baseline_condition"])
        _launch(experiment, user)
        res = _send(api, "post", f"{API}/experiments/{experiment.id}/clone/", {"name": "Again"})
        assert res.status_code == 200, res.content
        clone = res.json()
        assert clone["status"] == "draft"
        assert clone["cloned_from"] == {"id": str(experiment.id), "name": experiment.name}
        assert clone["slug"].startswith("again-")
        assert [(c["model"], c["profile"]) for c in clone["conditions"]] == [
            (c1.model_id, c1.profile_id),
            (c2.model_id, c2.profile_id),
        ]
        assert clone["baseline_condition_id"] == clone["conditions"][1]["id"]
        assert clone["app_requirement_ids"] == sorted(experiment.app_requirements.values_list("id", flat=True))
        # The slug is unique per owner: cloning twice in one minute gets a suffix.
        again = _send(api, "post", f"{API}/experiments/{experiment.id}/clone/", {"name": "Again"}).json()
        assert again["slug"] == f"{clone['slug']}-2"


# ── read models ───────────────────────────────────────────────────────


class TestReadModels:
    def test_list_pagination_filters_and_progress(self, api, user):
        for i in range(3):
            ExperimentFactory(created_by=user, name=f"Draft {i}")
        experiment, _ = _scaffolding_experiment(user, n_apps=2)
        _launch(experiment, user)
        job = experiment.jobs.order_by("sequence").first()
        GenerationJob.objects.filter(id=job.id).update(status=S.RUNNING, phase=GenerationJob.Phase.EVAL_QUEUED)
        ExperimentFactory(created_by=UserFactory())

        page = api.get(f"{API}/experiments/?per_page=2&page=2&ordering=name").json()
        assert (page["total"], page["pages"], page["page"], len(page["items"])) == (4, 2, 2, 2)
        active = api.get(f"{API}/experiments/?status=active").json()
        assert [i["id"] for i in active["items"]] == [str(experiment.id)]
        row = active["items"][0]
        assert row["progress"] | {} == {
            "pending": 3,
            "running": 0,
            "eval_queued": 1,
            "evaluating": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        assert (row["condition_count"], row["app_count"], row["total_cells"]) == (2, 2, 4)
        assert row["endpoints"][0]["slug"] == "openrouter"
        assert api.get(f"{API}/experiments/?search=Draft 1").json()["total"] == 1
        assert api.get(f"{API}/experiments/?endpoint=local").json()["total"] == 0
        assert api.get(f"{API}/experiments/?status=bogus").status_code == 400

    def test_status_rollups_and_cells(self, api, user):
        experiment, (c1, c2) = _scaffolding_experiment(user, n_apps=2)
        _launch(experiment, user)
        now = timezone.now()
        jobs = {(j.condition_id, j.app_requirement_id): j for j in experiment.jobs.all()}
        apps = sorted({a for _c, a in jobs})
        done = {"functional": {"pass_rate": 1.0, "passed": True}, "cost": 0.5, "total_tokens": 100}
        GenerationJob.objects.filter(id=jobs[(c1.id, apps[0])].id).update(
            status=S.COMPLETED,
            metrics=done,
            completed_at=now,
            duration_seconds=60,
        )
        GenerationJob.objects.filter(id=jobs[(c1.id, apps[1])].id).update(
            status=S.COMPLETED,
            metrics={"functional": {"pass_rate": None}},
            completed_at=now,
        )
        GenerationJob.objects.filter(id=jobs[(c2.id, apps[0])].id).update(status=S.FAILED, attempt=1)
        GenerationJob.objects.filter(id=jobs[(c2.id, apps[1])].id).update(
            run_after=now + timezone.timedelta(minutes=5),
        )

        status = api.get(f"{API}/experiments/{experiment.id}/status/").json()
        assert status["counts"]["completed"] == 2
        assert status["cells"] == {"total": 4, "done": 2, "failed_final": 1, "in_flight": 0, "retried": 1}
        assert status["throughput"]["completed_last_15m"] == 2
        cond = {c["condition_id"]: c for c in status["conditions"]}
        assert (cond[c1.id]["k_passed"], cond[c1.id]["n_graded"], cond[c1.id]["pass_rate"]) == (1, 1, 1.0)
        assert cond[c1.id]["cost_usd"] == 0.5
        assert status["endpoints"][0]["slug"] == "openrouter"
        assert set(status["lanes"]) == {"sandbox", "eval", "llm", "post"}

        cells = api.get(f"{API}/experiments/{experiment.id}/cells/").json()
        codes = {(cells["conditions"][ci]["id"], cells["apps"][ai]["id"]): s for ci, ai, _r, s, *_ in cells["cells"]}
        assert codes == {
            (c1.id, apps[0]): "c",
            (c1.id, apps[1]): "u",
            (c2.id, apps[0]): "x",
            (c2.id, apps[1]): "b",
        }

    def test_export_json_and_csv(self, api, user):
        experiment, _ = _scaffolding_experiment(user)
        _launch(experiment, user)
        payload = api.get(f"{API}/experiments/{experiment.id}/export/").json()
        job = payload["jobs"][0]
        assert (job["endpoint"], job["digest"], job["num_ctx"], job["attempt"]) == ("openrouter", "", None, 0)
        assert job["execution"]["policy"] == "interleaved"
        res = api.get(f"{API}/experiments/{experiment.id}/export/?format=csv")
        assert res["Content-Type"].startswith("text/csv")
        lines = res.content.decode().strip().splitlines()
        assert lines[0].split(",")[:3] == ["job_id", "condition_id", "condition_label"]
        assert len(lines) == 3

    def test_batches_route_uses_the_experiment_fk(self, api, user):
        experiment, _ = _scaffolding_experiment(user)
        batch, _ = _launch(experiment, user)
        listed = api.get(f"{API}/experiments/{experiment.id}/batches/").json()
        assert [b["id"] for b in listed] == [str(batch.id)]

    def test_cron_preview_is_not_shadowed(self, api):
        res = api.get(f"{API}/cron-preview/?cron=0 3 * * *&tz=UTC").json()
        assert res["valid"] is True
        assert len(res["next_runs"]) == 5
        bad = api.get(f"{API}/cron-preview/?cron=* * * * *").json()
        assert (bad["valid"], "15 minutes" in bad["detail"]) == (False, True)


# ── ownership ─────────────────────────────────────────────────────────


def test_every_experiment_route_404s_for_non_owners(api, user):
    owner = UserFactory()
    experiment, (condition, _c2) = _scaffolding_experiment(owner)
    schedule = experiment.schedules.create(action="top_up", cron="0 3 * * *", created_by=owner)
    base = f"{API}/experiments/{experiment.id}"
    routes = [
        ("get", f"{base}/"),
        ("patch", f"{base}/"),
        ("delete", f"{base}/"),
        ("get", f"{base}/conditions/"),
        ("put", f"{base}/conditions/"),
        ("post", f"{base}/conditions/"),
        ("post", f"{base}/conditions/bulk/"),
        ("delete", f"{base}/conditions/{condition.id}/"),
        ("get", f"{base}/preview/"),
        ("post", f"{base}/preview/"),
        ("post", f"{base}/launch/"),
        ("post", f"{base}/pause/"),
        ("post", f"{base}/resume/"),
        ("post", f"{base}/cancel/"),
        ("post", f"{base}/archive/"),
        ("post", f"{base}/clone/"),
        ("get", f"{base}/status/"),
        ("get", f"{base}/cells/"),
        ("get", f"{base}/results/"),
        ("get", f"{base}/batches/"),
        ("post", f"{base}/jobs/requeue-legacy/"),
        ("get", f"{base}/export/"),
        ("get", f"{base}/schedules/"),
        ("post", f"{base}/schedules/"),
        ("patch", f"{base}/schedules/{schedule.id}/"),
        ("delete", f"{base}/schedules/{schedule.id}/"),
        ("post", f"{base}/schedules/{schedule.id}/run-now/"),
        ("get", f"{base}/post-run/"),
        ("put", f"{base}/post-run/"),
        ("post", f"{base}/post-run/run/"),
    ]
    bodies = {
        f"{base}/conditions/": {"cross": {"model_ids": []}},
        f"{base}/conditions/bulk/": {"cross": {"model_ids": []}},
        f"{base}/schedules/": {"action": "top_up", "cron": "0 3 * * *"},
        f"{base}/post-run/": {"evaluate": True},
    }
    for method, path in routes:
        payload = bodies.get(path) if method in ("put", "post") else None
        if method == "post" and path.endswith("/conditions/"):
            payload = {"model_id": condition.model_id, "profile_id": condition.profile_id}
        res = _send(api, method, path, payload)
        assert res.status_code == 404, (method, path, res.status_code)
    experiment.refresh_from_db()
    assert experiment.status == Experiment.Status.DRAFT
    assert ExperimentCondition.objects.filter(id=condition.id).exists()


# ── results (app-blocked statistics) ──────────────────────────────────


def _graded(experiment, condition, app, repeat, pass_rate, **extra):
    status = extra.pop("status", S.COMPLETED)
    metrics = {"functional": {"pass_rate": pass_rate, "passed": pass_rate == 1.0}} if pass_rate is not None else {}
    if pass_rate is None and status == S.COMPLETED:
        metrics = {"functional": {"pass_rate": None, "passed": False}}
    return GenerationJob.objects.create(
        mode="scaffolding",
        created_by=experiment.created_by,
        experiment=experiment,
        condition=condition,
        app_requirement=app,
        repeat_index=repeat,
        status=status,
        metrics=metrics,
        **extra,
    )


def _design(user, *, n_apps=6, repeats=2, status=Experiment.Status.COMPLETED, n_conditions=2):
    experiment, conditions = _scaffolding_experiment(user, n_conditions=n_conditions, n_apps=n_apps, repeats=repeats)
    Experiment.objects.filter(id=experiment.id).update(status=status)
    experiment.refresh_from_db()
    return experiment, conditions, list(experiment.app_requirements.order_by("id"))


class TestResults:
    def test_known_answer_app_blocked_comparison(self, user):
        experiment, (a, b), apps = _design(user)
        for app in apps:
            for r in range(2):
                _graded(experiment, a, app, r, 0.5)
            _graded(experiment, b, app, 0, 1.0)
            _graded(experiment, b, app, 1, 0.8)
        res = compute(experiment, None, ResultFilters(), user)
        assert (res["baseline_condition_id"], res["baseline_source"], res["interim"]) == (a.id, "preregistered", False)
        assert res["balanced"] is True
        board = {row["condition_id"]: row for row in res["leaderboard"]}
        assert board[b.id]["rank"] == 1
        assert board[b.id]["pass_rate_mean"] == pytest.approx(0.9)
        assert (board[b.id]["n_apps"], board[b.id]["n_graded"], board[b.id]["k_passed"]) == (6, 12, 6)
        assert board[a.id]["pass_rate_ci95"] == [0.5, 0.5]
        lo, hi = board[b.id]["pass_rate_ci95"]
        assert 0.8 <= lo <= 0.9 <= hi <= 1.0
        (row,) = res["paired"]["rows"]
        assert (row["condition_id"], row["n_pairs"], row["mean_delta"]) == (b.id, 6, pytest.approx(0.4))
        assert (row["wins"], row["losses"], row["ties"]) == (6, 0, 0)
        assert row["sign_test_p"] == pytest.approx(2 / 64)
        assert row["wilcoxon_p"] == pytest.approx(2 / 64)
        assert row["p_holm"] == pytest.approx(2 / 64)
        assert 0.3 <= row["ci95"][0] <= 0.4 <= row["ci95"][1] <= 0.5
        assert res["paired"]["n_apps"] == 6
        # Deterministic per experiment.
        assert compute(experiment, None, ResultFilters(), user)["paired"]["rows"][0]["ci95"] == row["ci95"]

    def test_interim_hides_p_values_and_few_apps_are_flagged(self, user):
        experiment, (a, b), apps = _design(user, n_apps=3, repeats=1, status=Experiment.Status.RUNNING)
        for app in apps:
            _graded(experiment, a, app, 0, 0.0)
            _graded(experiment, b, app, 0, 1.0)
        res = compute(experiment, None, ResultFilters(), user)
        row = res["paired"]["rows"][0]
        assert res["interim"] is True
        assert (row["sign_test_p"], row["wilcoxon_p"], row["p_holm"]) == (None, None, None)
        assert row["mean_delta"] == 1.0
        assert results_svc.NOTE_INTERIM in res["notes"]
        Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.COMPLETED)
        experiment.refresh_from_db()
        assert results_svc.NOTE_FEW_APPS in compute(experiment, None, ResultFilters(), user)["notes"]

    def test_common_cells_and_missing_reasons(self, user):
        experiment, (a, b), apps = _design(user, n_apps=2, repeats=2)
        for app in apps:
            for r in range(2):
                _graded(experiment, a, app, r, 1.0)
        _graded(experiment, b, apps[0], 0, 0.0)
        _graded(experiment, b, apps[0], 1, None, status=S.FAILED)
        _graded(experiment, b, apps[1], 0, None)  # completed, ungraded
        _graded(experiment, b, apps[1], 1, 0.0, result_data={"backend_truncated": True})
        res = compute(experiment, None, ResultFilters(), user)
        board = {row["condition_id"]: row for row in res["leaderboard"]}
        assert res["balanced"] is False
        assert board[b.id]["n_missing"] == {
            "failed": 1,
            "ungraded": 1,
            "excluded_invalid": 0,
            "excluded_truncated": 1,
            "not_common": 0,
        }
        # A keeps only the cell B also has, symmetric with B's exclusions.
        assert board[a.id]["n_graded"] == 1
        assert board[a.id]["n_missing"]["not_common"] == 3
        loose = compute(experiment, None, ResultFilters(common_cells=False, exclude_truncated=False), user)
        assert {r["condition_id"]: r["n_graded"] for r in loose["leaderboard"]} == {a.id: 4, b.id: 2}
        assert results_svc.NOTE_ONE_SIDED in loose["notes"]

    def test_exclude_invalid_is_symmetric(self, user):
        experiment, (a, b), apps = _design(user, n_apps=1, repeats=2)
        _graded(experiment, a, apps[0], 0, 1.0)
        _graded(experiment, a, apps[0], 1, 1.0, result_data={"agent": {"validity": {"valid": False, "notes": []}}})
        _graded(experiment, b, apps[0], 0, 0.0)
        _graded(experiment, b, apps[0], 1, 0.0)
        res = compute(experiment, None, ResultFilters(exclude_invalid=True), user)
        board = {row["condition_id"]: row for row in res["leaderboard"]}
        assert board[a.id]["n_missing"]["excluded_invalid"] == 1
        assert board[b.id]["n_missing"]["not_common"] == 1
        assert board[a.id]["validity"] == {"ok": 1, "warn": 0, "invalid": 1}

    def test_current_attempt_wins(self, user):
        experiment, (a, b), apps = _design(user, n_apps=1, repeats=1)
        _graded(experiment, a, apps[0], 0, 1.0)
        _graded(experiment, a, apps[0], 0, None, status=S.FAILED)  # a newer attempt of the same cell
        _graded(experiment, b, apps[0], 0, 1.0)
        res = compute(experiment, None, ResultFilters(), user)
        board = {row["condition_id"]: row for row in res["leaderboard"]}
        assert (board[a.id]["n_graded"], board[a.id]["n_missing"]["failed"]) == (0, 1)
        cell = next(c for c in res["matrix"]["cells"] if c["condition_id"] == a.id)
        assert [r["code"] for r in cell["runs"]] == ["x"]

    def test_post_hoc_baseline_and_mixed_fingerprints(self, user):
        experiment, (a, b, c), apps = _design(user, n_apps=2, repeats=2, n_conditions=3)
        for app in apps:
            for r in range(2):
                _graded(experiment, a, app, r, 0.5, resolved_bundle={"config_fingerprint": "fa"})
                _graded(experiment, b, app, r, 1.0, resolved_bundle={"config_fingerprint": "fb" if r else "fb2"})
                _graded(experiment, c, app, r, 0.0, resolved_bundle={"config_fingerprint": "fc"})
        res = compute(experiment, b.id, ResultFilters(), user)
        assert (res["baseline_condition_id"], res["baseline_source"]) == (b.id, "post_hoc")
        assert results_svc.NOTE_POST_HOC in res["notes"]
        assert len(res["paired"]["rows"]) == 2  # N-1 rows, never all pairs
        board = {row["condition_id"]: row for row in res["leaderboard"]}
        assert board[b.id]["mixed_fingerprints"] == {"count": 2, "majority_share": 0.5}
        assert board[a.id]["mixed_fingerprints"] is None
        majority = compute(experiment, None, ResultFilters(fingerprint="majority"), user)
        mb = {row["condition_id"]: row for row in majority["leaderboard"]}[b.id]
        assert mb["n_missing"]["excluded_invalid"] == 2
        with pytest.raises(ValueError, match="not part"):
            compute(experiment, 999_999, ResultFilters(), user)

    def test_results_endpoint(self, api, user):
        experiment, (a, b), apps = _design(user, n_apps=1, repeats=1)
        _graded(experiment, a, apps[0], 0, 1.0)
        _graded(experiment, b, apps[0], 0, 0.0)
        res = api.get(f"{API}/experiments/{experiment.id}/results/?common_cells=false&baseline={b.id}")
        assert res.status_code == 200, res.content
        body = res.json()
        assert (body["common_cells"], body["baseline_condition_id"]) == (False, b.id)
        assert body["matrix"]["apps"][0]["id"] == apps[0].id
        assert api.get(f"{API}/experiments/{experiment.id}/results/?metric=loc").status_code == 400


class TestStatistics:
    @staticmethod
    def _brute_wilcoxon(deltas):
        nonzero = [d for d in deltas if d != 0]
        ranks = results_svc._ranks([abs(d) for d in nonzero])
        observed = sum(r for r, d in zip(ranks, nonzero, strict=True) if d > 0)
        total, mean = sum(ranks), sum(ranks) / 2
        extreme = 0
        for signs in itertools.product((0, 1), repeat=len(ranks)):
            w = sum(r for r, s in zip(ranks, signs, strict=True) if s)
            if abs(w - mean) >= abs(observed - mean) - 1e-9:
                extreme += 1
        assert total
        return extreme / 2 ** len(ranks)

    @pytest.mark.parametrize(
        "deltas",
        [
            [0.1, 0.2, 0.3, 0.4, 0.5, -0.6],
            [0.5, 0.5, -0.5, 0.25, 0.75, 1.0, -0.1],
            [1.0, 1.0, 1.0, -1.0],
            [0.0, 0.3, -0.2, 0.3],
        ],
    )
    def test_exact_wilcoxon_matches_enumeration(self, deltas):
        assert results_svc.wilcoxon_p(deltas) == pytest.approx(self._brute_wilcoxon(deltas))

    def test_normal_approximation_for_large_n(self):
        deltas = [float(i) for i in range(1, 31)]
        p = results_svc.wilcoxon_p(deltas)
        assert 0 < p < 1e-5

    def test_sign_test_and_holm(self):
        assert results_svc.sign_test_p(5, 0) == pytest.approx(2 / 32)
        assert results_svc.sign_test_p(3, 3) == 1.0
        assert results_svc.sign_test_p(0, 0) is None
        assert results_svc.holm([0.01, None, 0.04, 0.03]) == [0.03, None, 0.06, 0.06]
        assert math.isclose(results_svc.holm([0.5])[0], 0.5)


def test_comparability_shows_where_each_condition_runs(user):
    endpoint = _local_endpoint(user, num_ctx_cap=16384)
    local = _local_model(endpoint, "qwen3:8b", "sha256:abc")
    local.context_window, local.quantization = 40960, "Q4_K_M"
    local.save()
    remote = _models(1, metadata={"openrouter_supported_parameters": ["temperature", "seed", "tools", "max_tokens"]})[
        0
    ]
    experiment = ExperimentFactory(created_by=user, stack_slug="flask-react", repeats=1)
    experiment.app_requirements.set(_apps())
    aider, _ = _presets()
    for model in (remote, local):
        ExperimentConditionFactory(
            experiment=experiment,
            model=model,
            profile=None,
            harness_preset=aider,
            harness="aider",
        )
    payload = svc.comparability(experiment)
    rows = {row["model"]: row for row in payload["conditions"]}
    assert {k: rows[remote.model_id][k] for k in ("endpoint", "digest", "num_ctx", "api_flavor")} == {
        "endpoint": "openrouter",
        "digest": "",
        "num_ctx": None,
        "api_flavor": "openai",
    }
    assert {k: rows[local.model_id][k] for k in ("endpoint", "digest", "num_ctx", "quantization")} == {
        "endpoint": endpoint.slug,
        "digest": "sha256:abc",
        "num_ctx": 16384,
        "quantization": "Q4_K_M",
    }
    assert rows[local.model_id]["provider_routing"] == "n/a"
    assert {"endpoint", "digest", "num_ctx", "api_flavor", "quantization"} <= set(payload["differs_on"])


# ── review fixes ──────────────────────────────────────────────────────


def _relay_session(endpoint, *, role="active", age_s=0):
    from datetime import timedelta

    from backend.inference.models import RelaySession
    from backend.inference.models import RelayToken

    token = RelayToken.objects.create(
        endpoint=endpoint,
        prefix=f"llr_{uuid.uuid4().hex[:8]}",
        secret_hash="unused",  # noqa: S106 - never verified here
    )
    return RelaySession.objects.create(
        endpoint=endpoint,
        token=token,
        relay_version="1.0.0",
        upstream_kind="ollama",
        role=role,
        last_seen_at=timezone.now() - timedelta(seconds=age_s),
    )


class TestReviewFixes:
    def test_cancelling_a_completed_experiment_keeps_its_status(self, api, user):
        experiment, _ = _scaffolding_experiment(user)
        Experiment.objects.filter(id=experiment.id).update(status=Experiment.Status.COMPLETED)
        res = _send(api, "post", f"{API}/experiments/{experiment.id}/cancel/", {"scope": "all"})
        assert (res.status_code, res.json()["code"]) == (400, "not_active")
        experiment.refresh_from_db()
        assert experiment.status == Experiment.Status.COMPLETED

    def test_malformed_experiment_id_is_404(self, api):
        for path in ("not-a-uuid/", "not-a-uuid/status/", "123/results/"):
            assert api.get(f"{API}/experiments/{path}").status_code == 404, path

    def test_liveness_matches_the_access_layer(self, user):
        from backend.inference.access import launch_issues

        endpoint = _local_endpoint(user)
        model = _local_model(endpoint)
        _relay_session(endpoint, role="standby")
        live = svc.endpoint_liveness([endpoint])[endpoint.pk]
        # A standby alone does not serve claims: offline, as launch_issues says, but counted.
        assert (live.online, live.sessions) == (False, 1)
        assert "endpoint_offline" in {i["code"] for i in launch_issues(user, [model])}
        _relay_session(endpoint)
        assert svc.endpoint_liveness([endpoint])[endpoint.pk] == svc.EndpointLive(online=True, sessions=2)
        endpoint.enabled = False
        endpoint.save(update_fields=["enabled"])
        assert svc.endpoint_liveness([endpoint])[endpoint.pk].online is False

    def test_preview_reports_an_offline_endpoint_once_per_model(self, api, user):
        endpoint = _local_endpoint(user)
        models = [_local_model(endpoint, "a:1", "sha256:a"), _local_model(endpoint, "b:1", "sha256:b")]
        preview = _send(api, "post", f"{API}/experiments/preview/", _create_body(models, _apps())).json()
        offline = [i for i in preview["issues"] if i["code"] == "endpoint_offline"]
        assert sorted(i["model_id"] for i in offline) == sorted(m.id for m in models)
        (row,) = preview["endpoints"]
        assert len([i for i in row["issues"] if i["code"] == "endpoint_offline"]) == 2

    def test_bootstrap_ci_is_memoised_and_reproducible(self):
        from django.core.cache import cache

        data = {1: [0.2, 0.4, 1.0], 2: [0.5], 3: [0.0, 1.0]}
        first = results_svc._bootstrap(results_svc.cluster_bootstrap_ci, data, 42)
        assert first == results_svc.cluster_bootstrap_ci(data, __import__("random").Random(42))

        def cluster_bootstrap_ci(*_args):  # same name, so the same cache key
            msg = "recomputed"
            raise AssertionError(msg)

        assert results_svc._bootstrap(cluster_bootstrap_ci, data, 42) == first
        cache.clear()
        with pytest.raises(AssertionError, match="recomputed"):
            results_svc._bootstrap(cluster_bootstrap_ci, data, 42)
        assert results_svc._bootstrap(results_svc.cluster_bootstrap_ci, {}, 42) is None
