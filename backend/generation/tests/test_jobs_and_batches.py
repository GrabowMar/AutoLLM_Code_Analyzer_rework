"""Job phase, job list/detail rows, batch summaries/cancel, template-mode sampling presets, schema additions."""

from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services import presets as preset_service
from backend.generation.services import result_writer
from backend.generation.services.agent.runner import AgentRunner
from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job
from backend.generation.services.batches import derive_status
from backend.generation.services.batches import empty_counts
from backend.generation.services.job_listing import with_list_fields
from backend.generation.services.orchestrator import GenerationService
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.runtime.models import ContainerInstance
from backend.system.services.maintenance import clear_stuck_generation_jobs
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"
SUPPORTED = ["temperature", "top_p", "seed", "tools", "tool_choice", "max_tokens", "reasoning"]
S = GenerationJob.Status


@pytest.fixture(autouse=True)
def _no_auto_evaluation(settings, tmp_path):
    settings.AGENT_AUTO_EVALUATE = False
    settings.GENERATION_RUNS_ROOT = str(tmp_path / "runs")
    settings.GENERATION_DEFAULT_STACK = "flask-react"


@pytest.fixture
def user_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def _model(**kw):
    kw.setdefault("model_id", f"vendor/coder-{uuid.uuid4().hex[:6]}")
    kw.setdefault("metadata", {"openrouter_supported_parameters": SUPPORTED})
    kw.setdefault("max_output_tokens", 64_000)
    return LLMModelFactory(**kw)


def _app(slug=None, **kw):
    kw.setdefault("is_default", True)  # a catalog spec: usable by every user
    return AppRequirementTemplateFactory(
        slug=slug or f"notes-{uuid.uuid4().hex[:6]}",
        description="",
        backend_requirements=["Notes have a title"],
        api_endpoints=[
            {"method": "GET", "path": "/api/notes", "description": "List", "request": None, "response": {}},
        ],
        **kw,
    )


def _preset(user, kind, slug, config):
    return preset_service.create_preset(user, kind=kind, slug=slug, name=slug.replace("-", " ").title(), config=config)


def _batch(user, mode="agent", **kw):
    return GenerationBatch.objects.create(name=kw.pop("name", "Batch"), mode=mode, created_by=user, **kw)


def _job(user, batch=None, **kw):
    kw.setdefault("mode", GenerationJob.Mode.AGENT)
    return GenerationJob.objects.create(created_by=user, batch=batch, **kw)


def _post(client, path, payload, *, has_key=True):
    with (
        mock.patch("backend.generation.api.views.custom.has_resolvable_key", return_value=has_key),
        mock.patch("backend.generation.api.views.custom.dispatch_job") as dispatch,
    ):
        res = client.post(f"{API}{path}", data=json.dumps(payload), content_type="application/json")
    return res, dispatch


# ── phase ──────────────────────────────────────────────────────────────


class TestPhase:
    def test_phase_is_persisted_while_running_and_cleared_at_the_end(self, monkeypatch):
        user = UserFactory()
        batch = _batch(user)
        job = _job(user, batch, model=_model(), app_requirement=_app())
        seen = []

        def fake_run_stage(runner):
            for phase in ("running", "evaluating"):
                result_writer.publish_phase(runner.job, phase)
                seen.append(GenerationJob.objects.get(id=runner.job.id).phase)
            return "done"

        monkeypatch.setattr(AgentRunner, "run_stage", fake_run_stage)
        GenerationService().execute(job)
        assert seen == ["running", "evaluating"]
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.COMPLETED, "")
        batch.refresh_from_db()
        assert (batch.status, batch.completed_jobs, batch.total_jobs) == ("completed", 1, 1)

    def test_failed_run_clears_the_phase(self, monkeypatch):
        user = UserFactory()
        job = _job(user, model=_model(), app_requirement=_app())

        def fake_run_stage(runner):
            result_writer.publish_phase(runner.job, "running")
            msg = "sandbox exploded"
            raise RuntimeError(msg)

        monkeypatch.setattr(AgentRunner, "run_stage", fake_run_stage)
        GenerationService().execute(job)
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.FAILED, "")

    def test_phase_never_overwrites_a_cancel(self):
        job = _job(UserFactory(), status=S.CANCELLED)
        result_writer.publish_phase(job, "running")
        job.refresh_from_db()
        assert job.phase == ""

    def test_job_cancelled_before_its_thread_started_never_runs(self, monkeypatch):
        user = UserFactory()
        batch = _batch(user)
        job = _job(user, batch, model=_model(), app_requirement=_app())
        GenerationJob.objects.filter(id=job.id).update(status=S.CANCELLED)
        monkeypatch.setattr(AgentRunner, "run_stage", mock.Mock(side_effect=AssertionError("must not run")))
        GenerationService().execute(job)  # the in-memory row still says pending
        job.refresh_from_db()
        assert job.status == S.CANCELLED
        assert job.started_at is None
        assert job.completed_at is not None
        batch.refresh_from_db()
        assert batch.status == "cancelled"

    def test_maintenance_clears_the_phase_of_stuck_legacy_jobs(self):
        job = _job(UserFactory(), status=S.RUNNING, phase="queued")
        GenerationJob.objects.filter(id=job.id).update(updated_at="2000-01-01T00:00:00Z")
        assert clear_stuck_generation_jobs() == 1
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.FAILED, "")

    def test_maintenance_never_fails_a_healthy_executor_job(self):
        from django.utils import timezone

        user = UserFactory()
        old = "2000-01-01T00:00:00Z"
        queued = _job(user, queued_at=timezone.now())
        live = _job(
            user,
            status=S.RUNNING,
            phase="running",
            queued_at=timezone.now(),
            lease_owner="exec:1",
            lease_expires_at=timezone.now() + timezone.timedelta(seconds=60),
        )
        lost = _job(
            user,
            status=S.RUNNING,
            phase="running",
            queued_at=timezone.now(),
            lease_owner="exec:0",
            lease_expires_at=old,
        )
        GenerationJob.objects.filter(id__in=[queued.id, live.id]).update(updated_at=old)
        assert clear_stuck_generation_jobs() == 1
        statuses = dict(GenerationJob.objects.values_list("id", "status"))
        assert (statuses[queued.id], statuses[live.id], statuses[lost.id]) == (S.PENDING, S.RUNNING, S.FAILED)
        assert GenerationJob.objects.get(id=lost.id).lease_owner == ""

    def test_job_cancel_endpoint_clears_phase_and_refreshes_the_batch(self, user_client):
        client, user = user_client
        batch = _batch(user)
        job = _job(user, batch, status=S.RUNNING, phase="queued")
        res = client.post(f"{API}/jobs/{job.id}/cancel/")
        assert res.json()["success"] is True
        job.refresh_from_db()
        assert (job.status, job.phase) == (S.CANCELLED, "")
        batch.refresh_from_db()
        assert batch.status == "cancelled"


# ── job list ───────────────────────────────────────────────────────────


def _validity(*severities, valid=True):
    return {
        "valid": valid,
        "notes": [{"code": f"c{i}", "severity": s, "detail": ""} for i, s in enumerate(severities)],
    }


class TestJobList:
    @pytest.fixture
    def listed(self, user_client):
        client, user = user_client
        batch = _batch(user)
        app = _app(slug="notes-list")
        profile = GenerationProfileFactory(slug="app-notes", version=2)
        preset = preset_service.system_default_preset("harness", "aider")
        experiment = ExperimentFactory(created_by=user)
        condition = ExperimentConditionFactory(experiment=experiment)
        rich = _job(
            user,
            batch,
            status=S.COMPLETED,
            model=_model(model_id="vendor/rich"),
            app_requirement=app,
            profile=profile,
            experiment=experiment,
            condition=condition,
            repeat_index=1,
            experiment_seed=5,
            agent_config={"harness": "aider"},
            harness_preset=preset,
            metrics={"cost": 0.25, "functional": {"pass_rate": 0.75}},
            result_data={
                "files": {"app.py": "x" * 10_000},
                "agent": {"termination": "budget_exhausted", "validity": _validity("info", "warn")},
                "evaluation": {"status": "passed"},
            },
            resolved_bundle={"config_fingerprint": "cfg123", "harness": {"name": "aider"}, "brief": {"text": "..."}},
        )
        # Harness from the snapshot when agent_config carries none (default harness).
        snap_only = _job(user, batch, resolved_bundle={"harness": {"name": "opencode"}})
        excluded = _job(
            user,
            batch,
            agent_config={"harness": "aider"},
            result_data={"agent": {"validity": _validity("exclude", "warn", valid=False)}},
        )
        valid = _job(
            user,
            batch,
            agent_config={"harness": "aider"},
            result_data={"agent": {"validity": _validity("info")}},
        )
        scaffolding = _job(user, mode=GenerationJob.Mode.SCAFFOLDING, status=S.RUNNING, phase="")
        return (
            client,
            user,
            batch,
            {
                "rich": rich,
                "snap_only": snap_only,
                "excluded": excluded,
                "valid": valid,
                "scaffolding": scaffolding,
            },
        )

    def test_list_rows_carry_the_new_fields(self, listed):
        client, _user, batch, jobs = listed
        res = client.get(f"{API}/jobs/", {"per_page": 50})
        assert res.status_code == 200, res.content
        rows = {row["id"]: row for row in res.json()["items"]}
        rich = rows[str(jobs["rich"].id)]
        assert rich["batch_id"] == str(batch.id)
        assert rich["harness"] == "aider"
        preset = jobs["rich"].harness_preset
        assert rich["harness_preset"] == {"id": preset.id, "slug": "aider-default", "version": preset.version}
        assert rich["condition_id"] == jobs["rich"].condition_id
        assert rich["profile_ref"] == "app-notes@2"
        assert rich["repeat_index"] == 1
        assert rich["experiment_seed"] == 5
        assert rich["app_slug"] == "notes-list"
        assert rich["cost_usd"] == 0.25
        assert rich["pass_rate"] == 0.75
        assert rich["eval_status"] == "passed"
        assert rich["termination"] == "budget_exhausted"
        assert rich["validity"] == "warn"
        assert rich["phase"] == ""
        assert rich["config_fingerprint"] == "cfg123"

        assert rows[str(jobs["snap_only"].id)]["harness"] == "opencode"
        assert rows[str(jobs["excluded"].id)]["validity"] == "exclude"
        assert rows[str(jobs["valid"].id)]["validity"] == "valid"
        empty = rows[str(jobs["scaffolding"].id)]
        assert empty["harness"] is None
        assert empty["validity"] is None
        assert empty["cost_usd"] is None
        assert empty["harness_preset"] is None
        assert empty["profile_ref"] is None

    def test_batch_and_harness_filters(self, listed):
        client, _user, batch, jobs = listed
        res = client.get(f"{API}/jobs/", {"batch_id": str(batch.id), "per_page": 50})
        assert res.json()["total"] == 4
        res = client.get(f"{API}/jobs/", {"harness": "opencode"})
        assert [row["id"] for row in res.json()["items"]] == [str(jobs["snap_only"].id)]
        res = client.get(f"{API}/jobs/", {"harness": "aider", "batch_id": str(batch.id)})
        assert res.json()["total"] == 3
        assert client.get(f"{API}/jobs/", {"batch_id": "nope"}).status_code == 400

    def test_list_defers_the_blobs(self, listed):
        _client, _user, _batch, jobs = listed
        row = with_list_fields(GenerationJob.objects.filter(id=jobs["rich"].id)).get()
        assert {"result_data", "resolved_bundle"} <= row.get_deferred_fields()
        assert row.validity == "warn"

    def test_query_count_does_not_grow_with_rows(self, user_client):
        client, user = user_client

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(f"{API}/jobs/").status_code == 200
            return len(ctx.captured_queries)

        preset = preset_service.system_default_preset("harness", "opencode")
        _job(user, model=_model(), app_requirement=_app(), harness_preset=preset, profile=GenerationProfileFactory())
        few = count_queries()
        for _ in range(4):
            _job(
                user,
                model=_model(),
                app_requirement=_app(),
                harness_preset=preset,
                profile=GenerationProfileFactory(),
            )
        assert count_queries() == few

    def test_container_filters_do_not_duplicate_rows(self, user_client):
        client, user = user_client
        job = _job(user)
        bare = _job(user)
        removed_only = _job(user)
        for i in range(2):
            ContainerInstance.objects.create(generation_job=job, name=f"c-{job.id.hex[:8]}-{i}", status="running")
        ContainerInstance.objects.create(
            generation_job=removed_only,
            name=f"c-{removed_only.id.hex[:8]}",
            status="removed",
        )
        res = client.get(f"{API}/jobs/", {"container_status": "running"}).json()
        assert res["total"] == 1
        assert [row["id"] for row in res["items"]] == [str(job.id)]
        res = client.get(f"{API}/jobs/", {"container_status": "none"}).json()
        assert {row["id"] for row in res["items"]} == {str(bare.id), str(removed_only.id)}


# ── job detail ─────────────────────────────────────────────────────────


class TestJobDetail:
    def _agent_job(self, user, **kw):
        job = _job(user, model=_model(), stack_slug="flask-react", app_requirement=_app(), **kw)
        return apply_agent_snapshot_to_job(job)

    def test_agent_temperature_is_null_unless_set(self, user_client):
        client, user = user_client
        default = self._agent_job(user)
        explicit = self._agent_job(user, llm_params={"temperature": 0.2})
        custom = _job(user, mode=GenerationJob.Mode.CUSTOM)
        assert client.get(f"{API}/jobs/{default.id}/").json()["temperature"] is None
        assert client.get(f"{API}/jobs/{explicit.id}/").json()["temperature"] == 0.2
        assert client.get(f"{API}/jobs/{custom.id}/").json()["temperature"] == 0.3

    def test_stats_count_queued_within_running(self, user_client):
        client, user = user_client
        running = GenerationJob.Status.RUNNING
        _job(user, status=running, phase=GenerationJob.Phase.QUEUED)
        _job(user, status=running, phase=GenerationJob.Phase.RUNNING)
        _job(user, status=GenerationJob.Status.COMPLETED)
        stats = client.get(f"{API}/jobs/stats/").json()
        assert stats == {
            "total": 3,
            "completed": 1,
            "running": 2,
            "queued": 1,
            "failed": 0,
            "pending": 0,
            "cancelled": 0,
        }

    def test_malformed_job_id_is_a_json_404(self, user_client):
        client, _user = user_client
        for path in ("/jobs/not-a-uuid/", "/jobs/not-a-uuid/artifacts/", "/jobs/not-a-uuid/export/"):
            res = client.get(f"{API}{path}")
            assert res.status_code == 404, path
            assert res.json() == {"detail": "Not Found"}
        assert client.post(f"{API}/jobs/not-a-uuid/retry/").status_code == 404

    def test_presets_options_phase_and_fingerprint(self, user_client):
        client, user = user_client
        harness = _preset(user, "harness", "aider-whole", {"harness": "aider", "options": {"edit_format": "whole"}})
        sampling = _preset(user, "sampling", "t-zero", {"llm_params": {"temperature": 0}})
        budget = preset_service.system_default_preset("budget")
        job = self._agent_job(
            user,
            agent_config={"harness": "aider"},
            harness_options={"edit_format": "whole"},
            llm_params={"temperature": 0},
            harness_preset=harness,
            sampling_preset=sampling,
            budget_preset=budget,
        )
        GenerationJob.objects.filter(id=job.id).update(status=S.RUNNING, phase="queued")
        data = client.get(f"{API}/jobs/{job.id}/").json()
        assert data["harness_preset"] == {"id": harness.id, "slug": "aider-whole", "version": 1, "name": "Aider Whole"}
        assert data["sampling_preset"]["slug"] == "t-zero"
        assert data["budget_preset"] == {
            "id": budget.id,
            "slug": "platform-default",
            "version": budget.version,
            "name": budget.name,
        }
        assert data["harness_options"] == {"edit_format": "whole"}
        assert data["phase"] == "queued"
        assert data["config_fingerprint"] == job.resolved_bundle["config_fingerprint"]
        assert data["temperature"] == 0
        assert data["app_requirement_id"] == job.app_requirement_id
        assert data["model_pk"] == job.model_id

    def test_detail_without_presets(self, user_client):
        client, user = user_client
        job = _job(user, mode=GenerationJob.Mode.CUSTOM)
        data = client.get(f"{API}/jobs/{job.id}/").json()
        assert data["harness_preset"] is None
        assert data["sampling_preset"] is None
        assert data["config_fingerprint"] is None
        assert data["phase"] == ""


# ── batches ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("counts", "status"),
    [
        ({}, "completed"),
        ({"pending": 3}, "pending"),
        ({"pending": 2, "queued": 1}, "running"),
        ({"pending": 2, "failed": 1}, "running"),
        ({"completed": 3}, "completed"),
        ({"cancelled": 2}, "cancelled"),
        ({"failed": 2, "cancelled": 1}, "failed"),
        ({"completed": 1, "failed": 1}, "partial"),
        ({"completed": 1, "cancelled": 1}, "partial"),
    ],
)
def test_derived_batch_status(counts, status):
    assert derive_status({**empty_counts(), **counts}) == status


class TestBatches:
    @pytest.fixture
    def summary_batch(self, user_client):
        client, user = user_client
        p1 = preset_service.system_default_preset("harness", "aider")
        p2 = preset_service.system_default_preset("harness", "opencode")
        batch = _batch(
            user,
            request={
                "stack_slug": "flask-react",
                "trials": 2,
                "base_seed": 9,
                "harnesses": ["opencode", "aider"],
                "harness_preset_ids": [p2.id, p1.id],
            },
        )
        app_a, app_b = _app(), _app()
        m1, m2 = _model(), _model()
        rows = [
            (S.PENDING, "", app_a, m1, p1, None, None),
            (S.RUNNING, "queued", app_a, m2, p1, None, None),
            (S.RUNNING, "running", app_b, m1, p2, None, None),
            (S.RUNNING, "evaluating", app_b, m2, p2, None, None),
            (S.COMPLETED, "", app_a, m1, p2, 0.5, 1.0),
            (S.COMPLETED, "", app_b, m1, p1, 0.25, 0.5),
            (S.FAILED, "", app_a, m2, p1, None, None),
            (S.CANCELLED, "", app_b, m2, p2, None, None),
        ]
        for i, (status, phase, app, model, preset, cost, pass_rate) in enumerate(rows):
            metrics = {}
            if cost is not None:
                metrics = {"cost": cost, "functional": {"pass_rate": pass_rate}}
            _job(
                user,
                batch,
                status=status,
                phase=phase,
                app_requirement=app,
                model=model,
                harness_preset=preset,
                agent_config={"harness": preset.config["harness"]},
                stack_slug="flask-react",
                repeat_index=i % 2,
                metrics=metrics,
            )
        return client, user, batch, (p1, p2)

    def test_summary(self, summary_batch):
        client, _user, batch, (p1, p2) = summary_batch
        data = client.get(f"{API}/batches/{batch.id}/").json()
        assert data["counts"] == {"pending": 1, "queued": 1, "running": 2, "completed": 2, "failed": 1, "cancelled": 1}
        assert data["status"] == "running"
        assert data["is_terminal"] is False
        assert data["total_jobs"] == 8
        assert data["stack_slug"] == "flask-react"
        assert data["harnesses"] == ["opencode", "aider"]
        assert data["harness_presets"] == [
            {"id": p2.id, "slug": p2.slug, "version": p2.version},
            {"id": p1.id, "slug": p1.slug, "version": p1.version},
        ]
        assert (data["model_count"], data["app_count"], data["trials"], data["base_seed"]) == (2, 2, 2, 9)
        assert data["cost_usd"] == 0.75
        assert data["graded"] is True
        assert data["mean_pass_rate"] == 0.75
        assert data["experiment"] is None

    def test_summary_falls_back_to_jobs_and_experiment(self, user_client):
        client, user = user_client
        experiment = ExperimentFactory(created_by=user, base_seed=77, name="Exp A")
        batch = _batch(user, mode="scaffolding", name="Experiment: Exp A")
        for repeat in range(3):
            _job(
                user,
                batch,
                mode=GenerationJob.Mode.SCAFFOLDING,
                status=S.COMPLETED,
                experiment=experiment,
                stack_slug="fastapi-vue",
                repeat_index=repeat,
            )
        data = client.get(f"{API}/batches/{batch.id}/").json()
        assert data["experiment"] == {"id": str(experiment.id), "name": "Exp A"}
        assert (data["trials"], data["base_seed"], data["stack_slug"]) == (3, 77, "fastapi-vue")
        assert (data["status"], data["is_terminal"]) == ("completed", True)
        assert data["graded"] is False
        assert data["mean_pass_rate"] is None
        assert data["harnesses"] == []

    def test_list_pagination_filters_and_ownership(self, summary_batch):
        client, user, batch, _presets = summary_batch
        experiment = ExperimentFactory(created_by=user)
        exp_batch = _batch(user, mode="agent", name="exp")
        _job(user, exp_batch, experiment=experiment)
        empty_launch = _batch(user, mode="agent", request={"experiment_id": str(experiment.id)})
        scaffolding = _batch(user, mode="scaffolding")
        _batch(UserFactory())  # someone else's

        body = client.get(f"{API}/batches/").json()
        assert body["total"] == 4
        assert [item["id"] for item in body["items"]] == [
            str(scaffolding.id),
            str(empty_launch.id),
            str(exp_batch.id),
            str(batch.id),
        ]
        body = client.get(f"{API}/batches/", {"per_page": 1, "page": 2}).json()
        assert (body["pages"], body["page"], [i["id"] for i in body["items"]]) == (4, 2, [str(empty_launch.id)])
        body = client.get(f"{API}/batches/", {"mode": "scaffolding"}).json()
        assert [i["id"] for i in body["items"]] == [str(scaffolding.id)]
        ids = {i["id"] for i in client.get(f"{API}/batches/", {"experiment": "true"}).json()["items"]}
        assert ids == {str(exp_batch.id), str(empty_launch.id)}
        ids = {i["id"] for i in client.get(f"{API}/batches/", {"experiment": "false"}).json()["items"]}
        assert ids == {str(batch.id), str(scaffolding.id)}
        launch = next(i for i in client.get(f"{API}/batches/").json()["items"] if i["id"] == str(empty_launch.id))
        assert launch["experiment"]["id"] == str(experiment.id)
        assert (launch["status"], launch["is_terminal"], launch["total_jobs"]) == ("completed", True, 0)

        other = _batch(UserFactory())
        assert client.get(f"{API}/batches/{other.id}/").status_code == 404
        assert client.get(f"{API}/batches/{other.id}/jobs/").status_code == 404
        assert client.post(f"{API}/batches/{other.id}/cancel/").status_code == 404
        assert client.get(f"{API}/batches/not-a-uuid/").status_code == 404

    def test_list_query_count_does_not_grow_with_batches(self, user_client):
        client, user = user_client
        preset = preset_service.system_default_preset("harness", "aider")

        def add_batch():
            batch = _batch(user)
            _job(user, batch, harness_preset=preset, experiment=ExperimentFactory(created_by=user))

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(f"{API}/batches/").status_code == 200
            return len(ctx.captured_queries)

        add_batch()
        few = count_queries()
        for _ in range(3):
            add_batch()
        assert count_queries() == few

    def test_batch_jobs_in_creation_order(self, summary_batch):
        client, _user, batch, _presets = summary_batch
        rows = client.get(f"{API}/batches/{batch.id}/jobs/").json()
        expected = list(batch.jobs.order_by("created_at", "id").values_list("id", flat=True))
        assert [row["id"] for row in rows] == [str(i) for i in expected]
        assert {row["phase"] for row in rows} == {"", "queued", "running", "evaluating"}

    def test_cancel(self, summary_batch, django_capture_on_commit_callbacks):
        client, _user, batch, _presets = summary_batch
        with (
            mock.patch("backend.generation.services.result_writer.publish_status") as published,
            django_capture_on_commit_callbacks(execute=True),
        ):
            res = client.post(f"{API}/batches/{batch.id}/cancel/")
        assert res.json() == {"cancelled": 4}
        assert published.call_count == 4
        statuses = list(batch.jobs.values_list("status", "phase"))
        assert sorted(statuses) == sorted(
            [("cancelled", "")] * 5 + [("completed", "")] * 2 + [("failed", "")],
        )
        data = client.get(f"{API}/batches/{batch.id}/").json()
        assert (data["status"], data["is_terminal"]) == ("partial", True)
        batch.refresh_from_db()
        assert (batch.status, batch.total_jobs, batch.completed_jobs, batch.failed_jobs) == ("partial", 8, 2, 1)
        assert client.post(f"{API}/batches/{batch.id}/cancel/").json() == {"cancelled": 0}

    def test_cancelling_a_pending_batch_makes_it_cancelled(self, user_client):
        client, user = user_client
        batch = _batch(user)
        for _ in range(2):
            _job(user, batch)
        assert client.post(f"{API}/batches/{batch.id}/cancel/").json() == {"cancelled": 2}
        assert client.get(f"{API}/batches/{batch.id}/").json()["status"] == "cancelled"

    def test_retry_and_rerun_grow_the_batch(self, user_client):
        client, user = user_client
        batch = _batch(user, mode="custom", total_jobs=1)
        failed = _job(
            user,
            batch,
            mode=GenerationJob.Mode.CUSTOM,
            status=S.FAILED,
            model=_model(),
            llm_params={"temperature": 0.9},
        )
        batch.status = "failed"
        batch.save()
        with mock.patch("backend.generation.api.views.jobs.dispatch_job") as dispatch:
            res = client.post(f"{API}/jobs/{failed.id}/retry/")
        assert res.status_code == 200, res.content
        dispatch.assert_called_once()
        retry = GenerationJob.objects.get(id=res.json()["id"])
        # A custom retry freezes the llm section again (llm_params included).
        assert retry.resolved_bundle["llm"]["temperature"] == 0.9
        batch.refresh_from_db()
        assert (batch.total_jobs, batch.status) == (2, "running")
        data = client.get(f"{API}/batches/{batch.id}/").json()
        assert (data["total_jobs"], data["is_terminal"]) == (2, False)

        GenerationJob.objects.filter(id=retry.id).update(status=S.COMPLETED)
        with mock.patch("backend.generation.api.views.jobs.dispatch_job"):
            assert client.post(f"{API}/jobs/{retry.id}/rerun/").status_code == 200
        batch.refresh_from_db()
        assert batch.total_jobs == 3

    def test_deleting_a_job_refreshes_the_batch(self, user_client):
        client, user = user_client
        batch = _batch(user, total_jobs=2)
        done = _job(user, batch, status=S.COMPLETED)
        pending = _job(user, batch)
        assert client.delete(f"{API}/jobs/{pending.id}/").json() == {"success": True}
        batch.refresh_from_db()
        assert (batch.status, batch.total_jobs) == ("completed", 1)
        assert done.id in set(batch.jobs.values_list("id", flat=True))

    def test_agent_create_then_summary(self, user_client):
        client, user = user_client
        app = _app()
        model = _model()
        whole = _preset(user, "harness", "aider-whole", {"harness": "aider", "options": {"edit_format": "whole"}})
        default = preset_service.system_default_preset("harness", "opencode")
        res, _ = _post(
            client,
            "/jobs/agent/",
            {
                "app_requirement_ids": [app.id],
                "model_ids": [model.id],
                "harness_preset_ids": [whole.id, default.id],
                "trials": 2,
                "base_seed": 11,
            },
        )
        assert res.status_code == 200, res.content
        data = client.get(f"{API}/batches/{res.json()['batch_id']}/").json()
        assert (data["status"], data["total_jobs"], data["counts"]["pending"]) == ("pending", 4, 4)
        assert [p["id"] for p in data["harness_presets"]] == [whole.id, default.id]
        assert data["harnesses"] == ["aider", "opencode"]
        assert (data["trials"], data["base_seed"], data["stack_slug"]) == (2, 11, "flask-react")
        assert (data["model_count"], data["app_count"], data["graded"]) == (1, 1, True)

    def test_agent_create_all_snapshots_failed(self, user_client):
        client, _user = user_client
        with mock.patch(
            "backend.generation.api.views.custom.apply_agent_snapshot_to_job",
            side_effect=ValueError("boom"),
        ):
            res, dispatch = _post(client, "/jobs/agent/", {"description": "x", "model_ids": [_model().id]})
        assert res.status_code == 200, res.content
        assert res.json()["status"] == "failed"
        batch = GenerationBatch.objects.get(id=res.json()["batch_id"])
        assert (batch.status, batch.failed_jobs) == ("failed", 1)
        dispatch.assert_not_called()


# ── scaffolding / custom with sampling presets ─────────────────────────


@pytest.fixture
def flask_default_profile():
    return GenerationProfileFactory(
        slug="system-test-default",
        scaffolding_slug="flask-react",
        is_default=True,
        is_system=True,
    )


class TestScaffolding:
    def test_sampling_preset_is_applied_into_job_fields(self, user_client, flask_default_profile):
        client, user = user_client
        app = _app()
        models = [_model(), _model()]
        sampling = _preset(user, "sampling", "t-zero", {"llm_params": {"temperature": 0, "top_p": 0.5}})
        res, dispatch = _post(
            client,
            "/jobs/scaffolding/",
            {
                "stack_slug": "flask-react",
                "app_requirement_ids": [app.id],
                "model_ids": [m.id for m in models],
                "sampling_preset_id": sampling.id,
                "llm_params": {"top_p": 0.9},
                "trials": 2,
            },
        )
        assert res.status_code == 200, res.content
        body = res.json()
        assert (body["job_count"], body["status"]) == (4, "pending")
        assert dispatch.call_count == 4
        batch = GenerationBatch.objects.get(id=body["batch_id"])
        assert batch.request["sampling_preset_id"] == sampling.id
        assert batch.request["stack_slug"] == "flask-react"
        assert batch.request["llm_params"] == {"top_p": 0.9}
        jobs = list(batch.jobs.order_by("created_at"))
        assert sorted(j.repeat_index for j in jobs) == [0, 0, 1, 1]
        for job in jobs:
            assert job.sampling_preset_id == sampling.id
            assert job.llm_params == {"temperature": 0, "top_p": 0.9}
            # Chosen per app for the stack (a seeded system profile may win over the fixture's).
            assert job.profile.scaffolding_slug == "flask-react"
            llm = job.resolved_bundle["llm"]
            assert (llm["temperature"], llm["top_p"], llm["max_tokens"]) == (0, 0.9, 32000)
            assert job.resolved_bundle["presets"]["sampling"]["slug"] == "t-zero"

    def test_without_a_preset_the_snapshot_shape_is_unchanged(self, user_client, flask_default_profile):
        client, _user = user_client
        res, _ = _post(
            client,
            "/jobs/scaffolding/",
            {"app_requirement_ids": [_app().id], "model_ids": [_model().id]},
        )
        assert res.status_code == 200, res.content
        job = GenerationJob.objects.get(batch_id=res.json()["batch_id"])
        assert job.stack_slug == "flask-react"  # settings default
        assert job.sampling_preset is None
        assert "presets" not in job.resolved_bundle
        assert job.resolved_bundle["llm"]["temperature"] == 0.3

    def test_stack_follows_the_profile_and_must_match_it(self, user_client):
        client, _user = user_client
        profile = GenerationProfileFactory(scaffolding_slug="fastapi-vue")
        app, model = _app(), _model()
        before = GenerationBatch.objects.count()
        res, _ = _post(
            client,
            "/jobs/scaffolding/",
            {
                "stack_slug": "flask-react",
                "profile_id": profile.id,
                "app_requirement_ids": [app.id],
                "model_ids": [model.id],
            },
        )
        assert res.status_code == 400
        assert "does not match" in res.json()["detail"]
        assert GenerationBatch.objects.count() == before
        res, _ = _post(
            client,
            "/jobs/scaffolding/",
            {"profile_id": profile.id, "app_requirement_ids": [app.id], "model_ids": [model.id]},
        )
        assert res.status_code == 200, res.content
        job = GenerationJob.objects.get(batch_id=res.json()["batch_id"])
        assert (job.stack_slug, job.profile_id) == ("fastapi-vue", profile.id)
        assert job.resolved_bundle["scaffolding_slug"] == "fastapi-vue"

    def test_automatic_profile_of_another_stack_is_refused(self, user_client, flask_default_profile):
        client, _user = user_client
        before = GenerationJob.objects.count()
        res, _ = _post(
            client,
            "/jobs/scaffolding/",
            {"stack_slug": "generic-python", "app_requirement_ids": [_app().id], "model_ids": [_model().id]},
        )
        assert res.status_code == 400
        assert "No generation profile targets stack 'generic-python'" in res.json()["detail"]
        assert GenerationJob.objects.count() == before

    def test_bad_requests(self, user_client, flask_default_profile):
        client, _user = user_client
        app, model = _app(), _model()
        budget = preset_service.system_default_preset("budget")
        theirs = _preset(UserFactory(), "sampling", "theirs", {"llm_params": {"temperature": 1}})
        base = {"app_requirement_ids": [app.id], "model_ids": [model.id]}
        cases = [
            ({"app_requirement_ids": []}, 400, "app spec"),
            ({"model_ids": []}, 400, "model"),
            ({"app_requirement_ids": [app.id, 999_999]}, 404, "Unknown app requirement"),
            ({"model_ids": [999_999]}, 404, "Unknown model"),
            ({"stack_slug": "no-such-stack"}, 404, "Unknown stack"),
            ({"sampling_preset_id": budget.id}, 400, "not a sampling preset"),
            ({"sampling_preset_id": theirs.id}, 404, "Unknown preset"),
            ({"profile_id": 999_999}, 404, ""),
            ({"llm_params": {"temperature": 7}}, 400, "temperature"),
        ]
        before = GenerationJob.objects.count()
        for extra, status, needle in cases:
            res, _ = _post(client, "/jobs/scaffolding/", {**base, **extra})
            assert res.status_code == status, (extra, res.content)
            assert needle in res.json().get("detail", ""), (extra, res.json())
        assert GenerationJob.objects.count() == before

    def test_all_snapshots_failed_marks_the_batch_failed_at_creation(self, user_client, flask_default_profile):
        client, _user = user_client
        with mock.patch(
            "backend.generation.api.views.custom.apply_snapshot_to_job",
            side_effect=ValueError("missing block"),
        ):
            res, dispatch = _post(
                client,
                "/jobs/scaffolding/",
                {"app_requirement_ids": [_app().id], "model_ids": [_model().id, _model().id]},
            )
        assert res.status_code == 200, res.content
        assert res.json()["status"] == "failed"
        batch = GenerationBatch.objects.get(id=res.json()["batch_id"])
        assert (batch.status, batch.failed_jobs, batch.total_jobs) == ("failed", 2, 2)
        dispatch.assert_not_called()


class TestCustom:
    def test_sampling_preset_layers_under_inline_params(self, user_client):
        client, user = user_client
        sampling = _preset(
            user,
            "sampling",
            "focused",
            {"llm_params": {"temperature": 0.1, "reasoning": {"effort": "high"}}},
        )
        res, dispatch = _post(
            client,
            "/jobs/custom/",
            {
                "model_id": _model().id,
                "system_prompt": "s",
                "user_prompt": "u",
                "sampling_preset_id": sampling.id,
                "llm_params": {"temperature": 0.7},
            },
        )
        assert res.status_code == 200, res.content
        dispatch.assert_called_once()
        data = res.json()
        assert data["sampling_preset"]["slug"] == "focused"
        assert data["temperature"] == 0.7
        job = GenerationJob.objects.get(id=data["id"])
        assert job.llm_params == {"temperature": 0.7, "reasoning": {"effort": "high"}}
        llm = job.resolved_bundle["llm"]
        assert (llm["temperature"], llm["reasoning"], llm["max_tokens"]) == (0.7, {"effort": "high"}, 32000)
        assert job.resolved_bundle["presets"]["sampling"]["id"] == sampling.id

    def test_negative_seed_is_rejected(self, user_client):
        client, _user = user_client
        res, _ = _post(
            client,
            "/jobs/custom/",
            {"model_id": _model().id, "system_prompt": "s", "user_prompt": "u", "seed": -1},
        )
        assert res.status_code == 422


class TestLLMPreview:
    def test_sources_per_layer(self, user_client):
        client, user = user_client
        profile = GenerationProfileFactory(llm_config={"temperature": 0.5, "top_k": 40})
        sampling = _preset(user, "sampling", "s-preset", {"llm_params": {"top_k": 20, "top_p": 0.8}})
        res = client.post(
            f"{API}/jobs/llm-preview/",
            data=json.dumps(
                {
                    "mode": "scaffolding",
                    "profile_id": profile.id,
                    "sampling_preset_id": sampling.id,
                    "llm_params": {"top_p": 0.95},
                },
            ),
            content_type="application/json",
        )
        assert res.status_code == 200, res.content
        assert res.json() == {
            "effective": {"temperature": 0.5, "max_tokens": 32000, "top_k": 20, "top_p": 0.95},
            "sources": {"temperature": "profile", "max_tokens": "mode_default", "top_k": "preset", "top_p": "inline"},
        }

    def test_custom_defaults_and_errors(self, user_client):
        client, _user = user_client

        def preview(body):
            return client.post(f"{API}/jobs/llm-preview/", data=json.dumps(body), content_type="application/json")

        res = preview({"mode": "custom"})
        assert res.json() == {
            "effective": {"temperature": 0.3, "max_tokens": 32000},
            "sources": {"temperature": "mode_default", "max_tokens": "mode_default"},
        }
        profile = GenerationProfileFactory()
        assert preview({"mode": "custom", "profile_id": profile.id}).status_code == 400
        assert preview({"mode": "custom", "llm_params": {"top_p": 3}}).status_code == 400
        assert preview({"mode": "custom", "sampling_preset_id": 999_999}).status_code == 404
        assert preview({"mode": "agent"}).status_code == 422


# ── schema additions ───────────────────────────────────────────────────


def test_model_list_reports_reasoning_support(user_client):
    client, _user = user_client
    LLMModelFactory(model_id="a/reasoner", metadata={"openrouter_supported_parameters": ["tools", "reasoning"]})
    LLMModelFactory(model_id="b/legacy", metadata={"openrouter_supported_parameters": ["include_reasoning"]})
    LLMModelFactory(model_id="c/plain", metadata={"openrouter_supported_parameters": ["tools"]})
    LLMModelFactory(model_id="d/unsynced", metadata={})
    items = client.get("/api/models/", {"per_page": 50}).json()["items"]
    flags = {item["model_id"]: item["supports_reasoning"] for item in items}
    assert flags == {"a/reasoner": True, "b/legacy": True, "c/plain": False, "d/unsynced": False}


def test_app_specs_expose_difficulty(user_client):
    client, user = user_client
    spec = _app(difficulty="advanced", created_by=user)
    items = client.get(f"{API}/app-specs/").json()
    assert next(item for item in items if item["id"] == spec.id)["difficulty"] == "advanced"


def test_stacks_expose_approval_and_server_kind(user_client):
    client, _user = user_client
    items = client.get(f"{API}/stacks/").json()
    assert items
    for item in items:
        assert isinstance(item["is_approved"], bool)
        assert item["server_kind"] in ("python", "uvicorn")


# ── jobs list: experiment filters and execution fields (spec §F.2) ─────


class TestJobListExecutionFilters:
    def test_filters_current_attempts_and_execution_fields(self, user_client):
        from django.utils import timezone

        client, user = user_client
        experiment = ExperimentFactory(created_by=user)
        condition = ExperimentConditionFactory(experiment=experiment)
        app = _app()
        common = {"experiment": experiment, "condition": condition, "app_requirement": app, "repeat_index": 0}
        old = _job(user, status=S.FAILED, sequence=3, **common)
        retry = _job(user, status=S.RUNNING, phase="eval_queued", sequence=3, attempt=1, **common)
        GenerationJob.objects.filter(id=retry.id).update(
            queued_at=timezone.now(),
            execution={"queue_seconds": 12.5},
        )
        other = _job(user, experiment=experiment, condition=condition, app_requirement=app, repeat_index=1, sequence=1)
        _job(user)  # not in the experiment

        def ids(**params):
            res = client.get(f"{API}/jobs/", params)
            assert res.status_code == 200, res.content
            return [row["id"] for row in res.json()["items"]]

        assert set(ids(experiment_id=experiment.id)) == {str(old.id), str(retry.id), str(other.id)}
        assert set(ids(experiment_id=experiment.id, current="true")) == {str(retry.id), str(other.id)}
        assert ids(experiment_id=experiment.id, phase="eval_queued,evaluating") == [str(retry.id)]
        assert ids(experiment_id=experiment.id, attempt_gt=0) == [str(retry.id)]
        assert ids(experiment_id=experiment.id, repeat_index=1) == [str(other.id)]
        assert ids(experiment_id=experiment.id, sort_by="sequence")[0] == str(other.id)
        row = client.get(f"{API}/jobs/", {"experiment_id": experiment.id, "attempt_gt": 0}).json()["items"][0]
        assert (row["attempt"], row["sequence"], row["queue_seconds"], row["endpoint_slug"]) == (1, 3, 12.5, None)
        assert client.get(f"{API}/jobs/", {"experiment_id": experiment.id, "per_page": 500}).status_code == 200
        assert client.get(f"{API}/jobs/", {"per_page": 500}).status_code == 400
        assert client.get(f"{API}/jobs/", {"experiment_id": "nope"}).status_code == 400

    def test_cancel_endpoint_leaves_finished_generations_to_their_evaluation(self, user_client):
        client, user = user_client
        job = _job(user, status=S.RUNNING, phase="eval_queued")
        GenerationJob.objects.filter(id=job.id).update(queued_at="2026-01-01T00:00:00Z")
        res = client.post(f"{API}/jobs/{job.id}/cancel/").json()
        assert res["success"] is False
        assert GenerationJob.objects.get(id=job.id).status == S.RUNNING

    def test_scaffolding_batch_is_enqueued_in_creation_order(self, user_client, flask_default_profile):
        client, _ = user_client
        res, dispatch = _post(
            client,
            "/jobs/scaffolding/",
            {"stack_slug": "flask-react", "app_requirement_ids": [_app().id], "model_ids": [_model().id], "trials": 3},
        )
        assert res.status_code == 200, res.content
        jobs = [c.args[0] for c in dispatch.call_args_list]
        assert [j.sequence for j in jobs] == [0, 1, 2]
        assert len({j.queued_at for j in jobs}) == 1

    def test_invisible_models_are_unknown(self, user_client):
        from backend.inference.models import LLMEndpoint

        client, _ = user_client
        foreign = LLMEndpoint.objects.create(slug="someone-else", name="x", kind="ollama", owner=UserFactory())
        model = _model(endpoint=foreign, served_model_name="qwen")
        res, dispatch = _post(client, "/jobs/custom/", {"model_id": model.id, "system_prompt": "", "user_prompt": "x"})
        assert res.status_code == 404
        dispatch.assert_not_called()

    @pytest.mark.parametrize("finished", ["completed", "cancelled"])
    def test_retrying_a_cell_reopens_its_finished_experiment(self, user_client, finished):
        """The executor only claims jobs of running experiments: a retried cell must not wait forever."""
        client, user = user_client
        model = _model()
        experiment = ExperimentFactory(created_by=user, mode="custom", status=finished)
        condition = ExperimentConditionFactory(experiment=experiment, model=model)
        failed = _job(
            user,
            mode=GenerationJob.Mode.CUSTOM,
            status=S.FAILED,
            model=model,
            experiment=experiment,
            condition=condition,
            app_requirement=_app(),
            repeat_index=0,
        )
        with mock.patch("backend.generation.api.views.jobs.dispatch_job") as dispatch:
            res = client.post(f"{API}/jobs/{failed.id}/retry/")
        assert res.status_code == 200, res.content
        dispatch.assert_called_once()
        experiment.refresh_from_db()
        assert (experiment.status, experiment.completed_at, experiment.cancelled_at) == ("running", None, None)

    def test_retrying_a_cell_leaves_a_paused_experiment_paused(self, user_client):
        client, user = user_client
        model = _model()
        experiment = ExperimentFactory(created_by=user, mode="custom", status="paused")
        condition = ExperimentConditionFactory(experiment=experiment, model=model)
        failed = _job(
            user,
            mode=GenerationJob.Mode.CUSTOM,
            status=S.FAILED,
            model=model,
            experiment=experiment,
            condition=condition,
            app_requirement=_app(),
            repeat_index=0,
        )
        with mock.patch("backend.generation.api.views.jobs.dispatch_job"):
            assert client.post(f"{API}/jobs/{failed.id}/retry/").status_code == 200
        experiment.refresh_from_db()
        assert experiment.status == "paused"
