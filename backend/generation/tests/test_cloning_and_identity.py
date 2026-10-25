"""Clones vs. experiment cells, cell seeds, frozen gateways on rerun, numeric identity, spec visibility,
unknown param support, batch request, own default profiles and stack ownership."""

from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.models import RunPreset
from backend.generation.services import presets as preset_service
from backend.generation.services.agent.config import validate_agent_config
from backend.generation.services.agent.snapshot import build_agent_snapshot
from backend.generation.services.agent.snapshot import reseed_agent_snapshot
from backend.generation.services.experiments import launch_experiment
from backend.generation.services.job_cloning import CellTakenError
from backend.generation.services.job_cloning import cell_seed
from backend.generation.services.job_cloning import cell_taken_by
from backend.generation.services.job_cloning import clone_job
from backend.generation.services.profile_resolver import apply_snapshot_to_job
from backend.generation.services.profile_resolver import batch_cell_seed
from backend.generation.services.profile_resolver import build_resolved_snapshot
from backend.generation.services.profile_resolver import derive_experiment_seed
from backend.generation.services.profile_resolver import experiment_cell_seed
from backend.generation.services.profile_resolver import get_default_profile
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.runtime.models import Stack
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _openrouter_key():
    """Launches check the owner's OpenRouter key (inference.access.launch_issues); tests that need none patch it."""
    with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True):
        yield


API = "/api/generation"
SUPPORTED = ["temperature", "top_p", "seed", "tools", "tool_choice", "max_tokens", "reasoning"]
S = GenerationJob.Status
LAUNCH_DISPATCH = "backend.generation.services.experiments.enqueue_jobs"
JOBS_DISPATCH = "backend.generation.api.views.jobs.dispatch_job"


@pytest.fixture(autouse=True)
def _settings(settings, tmp_path):
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
    kw.setdefault("supports_function_calling", True)
    return LLMModelFactory(**kw)


def _app(**kw):
    kw.setdefault("is_default", True)
    return AppRequirementTemplateFactory(
        slug=kw.pop("slug", f"notes-{uuid.uuid4().hex[:6]}"),
        description=kw.pop("description", ""),
        backend_requirements=["Notes have a title"],
        api_endpoints=[
            {"method": "GET", "path": "/api/notes", "description": "List", "request": None, "response": {}},
        ],
        **kw,
    )


def _preset(user, kind, slug, config):
    return preset_service.create_preset(user, kind=kind, slug=slug, name=slug, config=config)


def _post(client, path, payload, *, has_key=True):
    with (
        mock.patch("backend.generation.api.views.custom.has_resolvable_key", return_value=has_key),
        mock.patch("backend.generation.api.views.custom.dispatch_job"),
    ):
        return client.post(f"{API}{path}", data=json.dumps(payload), content_type="application/json")


def _agent_experiment(user, *, base_seed=123, repeats=1):
    experiment = ExperimentFactory(
        created_by=user,
        mode="agent",
        repeats=repeats,
        base_seed=base_seed,
        stack_slug="flask-react",
    )
    app = _app()
    experiment.app_requirements.set([app])
    c1 = ExperimentConditionFactory(experiment=experiment, model=_model(), harness="opencode", profile=None)
    c2 = ExperimentConditionFactory(experiment=experiment, model=_model(), harness="aider", profile=None)
    return experiment, app, c1, c2


def _launch(experiment, user):
    with mock.patch(LAUNCH_DISPATCH):
        return launch_experiment(experiment, user)


def _scaffolding_experiment(user, *, base_seed):
    """Two conditions (models) on the seeded system profile, one app, one repeat."""
    profile = GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()
    assert profile is not None, "system-scaffolding-standard must be auto-seeded"
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=1, base_seed=base_seed)
    app = _app()
    experiment.app_requirements.set([app])
    c1 = ExperimentConditionFactory(experiment=experiment, model=_model(), profile=profile)
    c2 = ExperimentConditionFactory(experiment=experiment, model=_model(), profile=profile)
    return experiment, app, c1, c2


def _batch_job(batch, user, **kw):
    kw.setdefault("status", S.FAILED)
    return GenerationJob.objects.create(created_by=user, batch=batch, **kw)


# ── which clones join the experiment cell ────────────────────────────


class TestCloneCells:
    def test_replay_and_rerun_join_no_experiment_cell(self, user_client):
        client, user = user_client
        experiment, _app_row, c1, _c2 = _agent_experiment(user)
        batch = _launch(experiment, user)
        job = experiment.jobs.get(condition=c1)
        GenerationJob.objects.filter(id=job.id).update(status=S.COMPLETED)

        with mock.patch(JOBS_DISPATCH):
            replay = client.post(f"{API}/jobs/{job.id}/replay/").json()
            rerun = client.post(f"{API}/jobs/{job.id}/rerun/").json()
        replay_job = GenerationJob.objects.get(id=replay["id"])
        rerun_job = GenerationJob.objects.get(id=rerun["id"])
        for clone in (replay_job, rerun_job):
            assert (clone.experiment_id, clone.condition_id, clone.repeat_index) == (None, None, None)
        # Replays stay out of the batch; an extra trial grows it.
        assert replay_job.batch_id is None
        assert rerun_job.batch_id == batch.id
        # The exact replay is still an exact replay.
        assert replay_job.resolved_bundle["run_fingerprint"] == job.resolved_bundle["run_fingerprint"]
        # The design (what reports aggregate and relaunch covers) still has one trial for c1.
        assert GenerationJob.objects.filter(experiment=experiment, condition=c1).count() == 1

    def test_retry_takes_over_the_cell_with_its_paired_seed(self, user_client):
        client, user = user_client
        experiment, app, c1, c2 = _agent_experiment(user, base_seed=123)
        batch = _launch(experiment, user)
        paired = derive_experiment_seed(123, app.id, 0)
        assert set(experiment.jobs.values_list("experiment_seed", flat=True)) == {paired}
        failed = experiment.jobs.get(condition=c2)
        GenerationJob.objects.filter(id=failed.id).update(status=S.FAILED)

        with mock.patch(JOBS_DISPATCH):
            res = client.post(f"{API}/jobs/{failed.id}/retry/")
        assert res.status_code == 200, res.content
        retry = GenerationJob.objects.get(id=res.json()["id"])
        assert (retry.experiment_id, retry.condition_id, retry.repeat_index, retry.batch_id) == (
            experiment.id,
            c2.id,
            0,
            batch.id,
        )
        assert retry.experiment_seed == paired
        assert retry.resolved_bundle["seed"] == paired
        assert retry.resolved_bundle["run_fingerprint"] == failed.resolved_bundle["run_fingerprint"]
        # The c1 vs c2 comparison on this block still differs only in the condition.
        assert experiment.jobs.get(condition=c1).experiment_seed == retry.experiment_seed

    def test_snapshot_failed_cell_retry_rederives_the_seed_and_relaunch_still_repairs(self, user_client):
        client, user = user_client
        experiment, app, _c1, c2 = _agent_experiment(user, base_seed=77)
        # A launch cell whose snapshot failed never stores its seed.
        broken = GenerationJob.objects.create(
            mode=GenerationJob.Mode.AGENT,
            created_by=user,
            experiment=experiment,
            condition=c2,
            repeat_index=0,
            model=c2.model,
            stack_slug="flask-react",
            app_requirement=app,
            agent_config={"harness": "aider"},
            status=S.FAILED,
            error_message="Snapshot build failed: boom",
        )
        assert broken.experiment_seed is None
        expected = experiment_cell_seed(77, agent=True, condition_id=c2.id, app_id=app.id, repeat_index=0)
        assert cell_seed(broken) == expected == derive_experiment_seed(77, app.id, 0)

        with mock.patch(JOBS_DISPATCH):
            res = client.post(f"{API}/jobs/{broken.id}/retry/")
        assert res.status_code == 200, res.content
        retry = GenerationJob.objects.get(id=res.json()["id"])
        assert retry.experiment_seed == expected

        # The retry covers the cell; once it fails too, a relaunch repairs it with the same seed.
        _launch(experiment, user)
        assert experiment.jobs.filter(condition=c2).count() == 2
        GenerationJob.objects.filter(id=retry.id).update(status=S.FAILED)
        _launch(experiment, user)
        repaired = experiment.jobs.filter(condition=c2).order_by("-created_at").first()
        assert repaired.id not in (broken.id, retry.id)
        assert repaired.experiment_seed == expected

    def test_retry_is_refused_once_the_cell_was_repaired(self, user_client):
        client, user = user_client
        experiment, _app_row, _c1, c2 = _agent_experiment(user)
        _launch(experiment, user)
        failed = experiment.jobs.get(condition=c2)
        GenerationJob.objects.filter(id=failed.id).update(status=S.FAILED)
        _launch(experiment, user)  # the relaunch tops the failed cell up
        assert experiment.jobs.filter(condition=c2).count() == 2

        with mock.patch(JOBS_DISPATCH) as dispatch:
            res = client.post(f"{API}/jobs/{failed.id}/retry/")
        assert res.status_code == 400
        assert "extra trial" in res.json()["message"]
        dispatch.assert_not_called()
        assert experiment.jobs.filter(condition=c2).count() == 2

    def test_ad_hoc_batch_retry_keeps_the_cell_seed(self, user_client):
        client, _user = user_client
        app = _app()
        m1, m2 = _model(), _model()
        res = _post(
            client,
            "/jobs/agent/",
            {"app_requirement_ids": [app.id], "model_ids": [m1.id, m2.id], "base_seed": 900, "trials": 1},
        )
        assert res.status_code == 200, res.content
        jobs = list(GenerationJob.objects.filter(batch_id=res.json()["batch_id"]).order_by("model_id"))
        paired = batch_cell_seed(900, app.slug, 0)
        assert {j.experiment_seed for j in jobs} == {paired}
        # Even without a stored seed the retry re-derives it from the batch's base seed.
        GenerationJob.objects.filter(id=jobs[1].id).update(status=S.FAILED, experiment_seed=None)
        with mock.patch(JOBS_DISPATCH):
            retry_res = client.post(f"{API}/jobs/{jobs[1].id}/retry/")
        retry = GenerationJob.objects.get(id=retry_res.json()["id"])
        assert (retry.batch_id, retry.repeat_index, retry.experiment_seed) == (jobs[1].batch_id, 0, paired)

    def test_ad_hoc_batch_cell_takes_one_retry(self, user_client):
        client, _user = user_client
        app, model = _app(), _model()
        res = _post(
            client,
            "/jobs/agent/",
            {"app_requirement_ids": [app.id], "model_ids": [model.id], "base_seed": 5, "trials": 2},
        )
        assert res.status_code == 200, res.content
        batch_id = res.json()["batch_id"]
        failed = GenerationJob.objects.get(batch_id=batch_id, repeat_index=0)
        GenerationJob.objects.filter(id=failed.id).update(status=S.FAILED)

        with mock.patch(JOBS_DISPATCH) as dispatch:
            first = client.post(f"{API}/jobs/{failed.id}/retry/")
            second = client.post(f"{API}/jobs/{failed.id}/retry/")
        assert first.status_code == 200, first.content
        assert second.status_code == 400
        assert "extra trial" in second.json()["message"]
        dispatch.assert_called_once()
        assert GenerationJob.objects.filter(batch_id=batch_id, repeat_index=0).count() == 2
        assert GenerationBatch.objects.get(id=batch_id).total_jobs == 3

        # Once the replacement has failed too, the cell is open again.
        GenerationJob.objects.filter(id=first.json()["id"]).update(status=S.FAILED)
        with mock.patch(JOBS_DISPATCH):
            assert client.post(f"{API}/jobs/{failed.id}/retry/").status_code == 200

    def test_ad_hoc_cells_are_model_task_trial_and_condition(self):
        user = UserFactory()
        batch = GenerationBatch.objects.create(name="b", mode="agent", created_by=user)
        app, other_app = _app(), _app()
        m1, m2 = _model(), _model()
        p1 = preset_service.system_default_preset("harness", "opencode")
        p2 = preset_service.system_default_preset("harness", "aider")
        cell = {"mode": "agent", "model": m1, "app_requirement": app, "repeat_index": 0, "harness_preset": p1}
        failed = _batch_job(batch, user, **cell)
        assert cell_taken_by(failed) is None
        # Another model, task, trial or harness preset is another cell.
        for change in ({"model": m2}, {"app_requirement": other_app}, {"repeat_index": 1}, {"harness_preset": p2}):
            _batch_job(batch, user, **{**cell, **change, "status": S.PENDING})
        # So are reruns (no repeat index), failed / cancelled attempts and other batches.
        _batch_job(batch, user, **{**cell, "repeat_index": None, "status": S.COMPLETED})
        _batch_job(batch, user, **{**cell, "status": S.CANCELLED})
        other_batch = GenerationBatch.objects.create(name="o", mode="agent", created_by=user)
        _batch_job(other_batch, user, **{**cell, "status": S.COMPLETED})
        assert cell_taken_by(failed) is None

        live = _batch_job(batch, user, **{**cell, "status": S.RUNNING})
        assert cell_taken_by(failed) == live

        # A free-form task (no app spec) is its own cell; scaffolding cells are per profile.
        task = _batch_job(batch, user, **{**cell, "app_requirement": None})
        assert cell_taken_by(task) is None
        scaffolding = {"mode": "scaffolding", "model": m1, "app_requirement": app, "repeat_index": 0}
        profile_a, profile_b = GenerationProfileFactory(), GenerationProfileFactory()
        failed_a = _batch_job(batch, user, **scaffolding, profile=profile_a)
        _batch_job(batch, user, **scaffolding, profile=profile_b, status=S.COMPLETED)
        assert cell_taken_by(failed_a) is None
        done_a = _batch_job(batch, user, **scaffolding, profile=profile_a, status=S.COMPLETED)
        assert cell_taken_by(failed_a) == done_a

    def test_the_cell_check_runs_under_a_batch_row_lock(self, user_client):
        client, user = user_client
        app, model = _app(), _model()
        res = _post(client, "/jobs/agent/", {"app_requirement_ids": [app.id], "model_ids": [model.id], "trials": 1})
        failed = GenerationJob.objects.get(batch_id=res.json()["batch_id"])
        GenerationJob.objects.filter(id=failed.id).update(status=S.FAILED)
        failed.refresh_from_db()

        with CaptureQueriesContext(connection) as queries:
            clone_job(failed, user, reuse_snapshot=False, new_seed=True, keep_cell=True)
        sql = [q["sql"] for q in queries.captured_queries]
        lock = next(i for i, q in enumerate(sql) if "FOR UPDATE" in q and GenerationBatch._meta.db_table in q)
        insert = next(i for i, q in enumerate(sql) if q.startswith(f'INSERT INTO "{GenerationJob._meta.db_table}"'))
        assert lock < insert
        # The service refuses the second takeover itself, whatever the caller checked before.
        with pytest.raises(CellTakenError):
            clone_job(failed, user, reuse_snapshot=False, new_seed=True, keep_cell=True)

    def test_launch_reads_covered_cells_under_the_experiment_row_lock(self, user_client):
        # The lock clone_job takes first: a concurrent retry and a top-up can't both fill a cell.
        _client, user = user_client
        experiment, *_ = _agent_experiment(user)
        with CaptureQueriesContext(connection) as queries:
            _launch(experiment, user)
        sql = [q["sql"] for q in queries.captured_queries]
        table = experiment._meta.db_table
        lock = next(i for i, q in enumerate(sql) if "FOR UPDATE" in q and f'FROM "{table}"' in q)
        insert = next(i for i, q in enumerate(sql) if q.startswith(f'INSERT INTO "{GenerationJob._meta.db_table}"'))
        assert lock < insert

    def test_scaffolding_cell_seed_is_shared_by_launch_retry_and_top_up(self, user_client):
        client, user = user_client
        experiment, app, c1, c2 = _scaffolding_experiment(user, base_seed=4321)

        def flaky_snapshot(job, **kw):
            if job.condition_id == c2.id:
                msg = "transient"
                raise RuntimeError(msg)
            return apply_snapshot_to_job(job, **kw)

        with mock.patch("backend.generation.services.experiments.apply_snapshot_to_job", side_effect=flaky_snapshot):
            _launch(experiment, user)
        broken = experiment.jobs.get(condition=c2)
        # Scaffolding keeps its historical per-condition derivation: existing experiments keep their seeds.
        seed_x = derive_experiment_seed(4321, c2.id, app.id, 0)
        # The failed cell keeps the seed launch derived for it (jobs are bulk-created with it).
        assert (broken.status, broken.experiment_seed) == (S.FAILED, seed_x)
        assert cell_seed(broken) == seed_x
        assert experiment.jobs.get(condition=c1).experiment_seed == derive_experiment_seed(4321, c1.id, app.id, 0)

        with mock.patch(JOBS_DISPATCH):
            res = client.post(f"{API}/jobs/{broken.id}/retry/")
        assert res.status_code == 200, res.content
        retry = GenerationJob.objects.get(id=res.json()["id"])
        assert (retry.condition_id, retry.repeat_index) == (c2.id, 0)
        assert retry.experiment_seed == retry.resolved_bundle["seed"] == seed_x

        GenerationJob.objects.filter(id=retry.id).update(status=S.FAILED)
        _launch(experiment, user)  # the relaunch tops the cell up
        top_up = experiment.jobs.filter(condition=c2).exclude(id__in=(broken.id, retry.id)).get()
        assert top_up.experiment_seed == top_up.resolved_bundle["seed"] == seed_x

    def test_custom_retry_keeps_the_explicit_seed(self, user_client):
        client, user = user_client
        job = GenerationJob.objects.create(
            mode=GenerationJob.Mode.CUSTOM,
            created_by=user,
            model=_model(),
            custom_user_prompt="Build it",
            experiment_seed=4242,
            status=S.FAILED,
        )
        with mock.patch(JOBS_DISPATCH):
            res = client.post(f"{API}/jobs/{job.id}/retry/")
        assert GenerationJob.objects.get(id=res.json()["id"]).experiment_seed == 4242


# ── rerun keeps the frozen gateway ───────────────────────────────────


class TestRerunGateway:
    def _snapshot(self, model, **kw):
        return build_agent_snapshot(
            app_requirement=_app(),
            description="",
            profile=None,
            stack_slug="flask-react",
            model=model,
            max_tokens=32000,
            experiment_seed=7,
            llm_overrides=kw.pop("llm_overrides", {"top_p": 0.9}),
            agent_overrides={"harness": "opencode"},
        )

    def test_catalog_change_does_not_reach_the_rerun(self):
        model = _model()
        snap = self._snapshot(model)
        assert snap["gateway"]["forced_params"]["top_p"] == 0.9
        assert snap["gateway"]["forced_params"]["seed"] == 7
        # A catalog sync later drops top_p and seed for this model.
        model.metadata = {"openrouter_supported_parameters": ["temperature", "tools", "max_tokens"]}
        model.save()

        rerun = reseed_agent_snapshot(snap, 99)
        forced = rerun["gateway"]["forced_params"]
        assert forced["top_p"] == 0.9
        assert forced["seed"] == 99
        assert rerun["gateway"]["dropped_params"] == snap["gateway"]["dropped_params"]
        assert rerun["config_fingerprint"] == snap["config_fingerprint"]
        assert rerun["run_fingerprint"] != snap["run_fingerprint"]
        assert snap["gateway"]["forced_params"]["seed"] == 7  # the original is untouched

    def test_a_dropped_seed_stays_dropped(self):
        model = _model(metadata={"openrouter_supported_parameters": ["temperature", "top_p", "tools"]})
        snap = self._snapshot(model)
        assert "seed" in snap["gateway"]["dropped_params"]
        model.metadata = {"openrouter_supported_parameters": SUPPORTED}
        model.save()
        rerun = reseed_agent_snapshot(snap, 99)
        assert "seed" not in rerun["gateway"]["forced_params"]
        assert rerun["gateway"] == snap["gateway"]
        assert rerun["seed"] == 99
        assert rerun["config_fingerprint"] == snap["config_fingerprint"]

    def test_rerun_clone_keeps_the_config_fingerprint(self):
        user = UserFactory()
        model = _model()
        job = GenerationJob.objects.create(
            mode=GenerationJob.Mode.AGENT,
            created_by=user,
            model=model,
            stack_slug="flask-react",
            app_requirement=_app(),
            llm_params={"top_p": 0.9},
            agent_config={"harness": "opencode"},
            experiment_seed=5,
        )
        from backend.generation.services.agent.snapshot import apply_agent_snapshot_to_job

        apply_agent_snapshot_to_job(job)
        model.metadata = {"openrouter_supported_parameters": ["tools"]}
        model.save()
        rerun = clone_job(job, user, reuse_snapshot=True, new_seed=True)
        assert rerun.resolved_bundle["config_fingerprint"] == job.resolved_bundle["config_fingerprint"]
        assert rerun.resolved_bundle["gateway"]["forced_params"]["top_p"] == 0.9


# ── numeric kinds in identity keys ───────────────────────────────────


class TestNumericIdentity:
    def test_validate_agent_config_coerces_declared_types(self):
        config = validate_agent_config({"max_cost_usd": 3, "max_llm_requests": 50.0})
        assert config == {"max_cost_usd": 3.0, "max_llm_requests": 50}
        assert isinstance(config["max_cost_usd"], float)
        assert isinstance(config["max_llm_requests"], int)
        with pytest.raises(ValueError, match="max_llm_requests must be an integer between 1 and 2000"):
            validate_agent_config({"max_llm_requests": 1.5})
        with pytest.raises(ValueError, match="max_cost_usd must be a number between"):
            validate_agent_config({"max_cost_usd": "a lot"})

    def test_preset_ints_and_inline_floats_fingerprint_alike(self, user_client):
        client, user = user_client
        app, model = _app(), _model()
        sampling = _preset(user, "sampling", "t-zero", {"llm_params": {"temperature": 0}})
        budget = _preset(user, "budget", "small", {"max_llm_requests": 50, "max_cost_usd": 3})
        # A budget preset stored before limits were coerced keeps its int.
        RunPreset.objects.filter(id=budget.id).update(config={"max_llm_requests": 50, "max_cost_usd": 3})
        assert RunPreset.objects.get(id=sampling.id).config["llm_params"]["temperature"] == 0

        def create(extra):
            res = _post(
                client,
                "/jobs/agent/",
                {"app_requirement_ids": [app.id], "model_ids": [model.id], "base_seed": 11, **extra},
            )
            assert res.status_code == 200, res.content
            return GenerationJob.objects.get(batch_id=res.json()["batch_id"]).resolved_bundle

        via_presets = create({"sampling_preset_id": sampling.id, "budget_preset_id": budget.id})
        inline = create(
            {"llm_params": {"temperature": 0.0}, "agent_config": {"max_llm_requests": 50, "max_cost_usd": 3.0}},
        )
        assert via_presets["method_key"] == inline["method_key"]
        assert via_presets["config_fingerprint"] == inline["config_fingerprint"]
        assert via_presets["run_fingerprint"] == inline["run_fingerprint"]
        assert isinstance(via_presets["gateway"]["forced_params"]["temperature"], float)
        assert isinstance(via_presets["agent"]["max_cost_usd"], float)

    def test_scaffolding_fingerprint_ignores_int_vs_float(self):
        profile = GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()
        app, model = _app(), _model()

        def build(overrides):
            return build_resolved_snapshot(
                app_requirement=app,
                profile=profile,
                scaffolding_slug="flask-react",
                model=model,
                temperature=0.3,
                max_tokens=32000,
                user=None,
                experiment_seed=3,
                llm_overrides=overrides,
            )

        assert build({"temperature": 1})["run_fingerprint"] == build({"temperature": 1.0})["run_fingerprint"]


# ── app spec visibility ──────────────────────────────────────────────


class TestAppSpecVisibility:
    def test_another_users_private_spec_is_unknown(self, client):
        owner = UserFactory()
        private = _app(is_default=False, created_by=owner, description="TOP SECRET SPEC")
        model = _model()
        body = {"app_requirement_ids": [private.id], "model_ids": [model.id]}

        client.force_login(UserFactory())
        assert _post(client, "/jobs/agent/preview/", body).status_code == 404
        assert _post(client, "/jobs/agent/", body).status_code == 404
        scaffolding = _post(client, "/jobs/scaffolding/", body)
        assert scaffolding.status_code == 404
        assert scaffolding.json()["detail"] == "Unknown app requirement id"
        assert not GenerationJob.objects.filter(app_requirement=private).exists()

        client.force_login(owner)
        assert _post(client, "/jobs/agent/preview/", body).status_code == 200

    def test_catalog_specs_stay_usable(self, user_client):
        client, _user = user_client
        res = _post(client, "/jobs/agent/preview/", {"app_requirement_ids": [_app().id], "model_ids": [_model().id]})
        assert res.status_code == 200, res.content

    def test_a_deleted_owners_private_spec_is_unknown_to_everyone_else(self, client):
        owner = UserFactory()
        private = _app(is_default=False, created_by=owner, description="TOP SECRET SPEC")
        owner.delete()  # created_by is SET_NULL: the spec loses its owner, not its privacy
        private.refresh_from_db()
        assert private.created_by_id is None
        body = {"app_requirement_ids": [private.id], "model_ids": [_model().id]}

        client.force_login(UserFactory())
        assert _post(client, "/jobs/agent/preview/", body).status_code == 404
        assert _post(client, "/jobs/agent/", body).status_code == 404
        assert _post(client, "/jobs/scaffolding/", body).status_code == 404
        experiment = client.post(
            f"{API}/experiments/",
            data=json.dumps({"name": "Leak", "slug": "leak", "app_requirement_ids": [private.id]}),
            content_type="application/json",
        )
        assert experiment.status_code == 404
        assert client.get(f"{API}/app-specs/{private.slug}/").status_code == 404
        assert private.slug not in {spec["slug"] for spec in client.get(f"{API}/app-specs/").json()}
        assert not GenerationJob.objects.filter(app_requirement=private).exists()


# ── unknown parameter support ────────────────────────────────────────


class TestUnknownParamSupport:
    def test_preview_warns_and_snapshot_records_it(self, user_client):
        client, _user = user_client
        app = _app()
        unknown = _model(metadata={})
        known = _model()
        res = _post(
            client,
            "/jobs/agent/preview/",
            {
                "app_requirement_ids": [app.id],
                "model_ids": [unknown.id, known.id],
                "llm_params": {"temperature": 0.2},
                "base_seed": 1,
            },
        )
        assert res.status_code == 200, res.content
        body = res.json()
        rows = {row["model_id"]: row for row in body["models"]}
        assert rows[unknown.id]["params_support"] == "unknown"
        assert rows[known.id]["params_support"] == "catalog"
        warnings = [w for w in body["warnings"] if w["code"] == "support_unknown"]
        assert [(w["severity"], w["model_id"]) for w in warnings] == [("warn", unknown.id)]
        assert "seed, temperature are forwarded unverified" in warnings[0]["detail"]
        # Nothing is dropped: forwarded unverified, enforced by require_parameters routing.
        assert rows[unknown.id]["forced_params"]["temperature"] == 0.2
        assert rows[unknown.id]["dropped_params"] == {}

        snap = build_agent_snapshot(
            app_requirement=app,
            description="",
            profile=None,
            stack_slug="flask-react",
            model=unknown,
            max_tokens=32000,
            experiment_seed=5,
        )
        assert snap["llm"]["supported_params_source"] == "unknown"
        assert snap["gateway"]["forced_params"]["seed"] == 5
        assert snap["llm"]["provider"]["require_parameters"] is True


# ── batch request, default profiles, stack ownership ──────────────────


def test_batch_summary_exposes_the_request(user_client):
    client, _user = user_client
    app, model = _app(), _model()
    res = _post(
        client,
        "/jobs/agent/",
        {"app_requirement_ids": [app.id], "model_ids": [model.id], "base_seed": 31, "trials": 2},
    )
    batch_id = res.json()["batch_id"]
    summary = client.get(f"{API}/batches/{batch_id}/").json()
    request = summary["request"]
    assert (request["base_seed"], request["trials"], request["app_requirement_ids"], request["model_ids"]) == (
        31,
        2,
        [app.id],
        [model.id],
    )
    assert request["harnesses"] == ["opencode"]
    listed = client.get(f"{API}/batches/").json()["items"][0]
    assert listed["request"]["base_seed"] == 31


@pytest.mark.parametrize("agent", [True, False])
def test_experiment_cell_seed_matches_the_historical_launch_formula(agent):
    """Launches derive seeds through experiment_cell_seed; the seeds of existing experiments must not move."""
    for base_seed, condition_id, app_id, repeat in ((0, 1, 1, 0), (123, 7, 42, 2), (2_147_483_646, 999, 3, 19)):
        parts = (app_id, repeat) if agent else (condition_id, app_id, repeat)
        assert experiment_cell_seed(
            base_seed,
            agent=agent,
            condition_id=condition_id,
            app_id=app_id,
            repeat_index=repeat,
        ) == derive_experiment_seed(base_seed, *parts)


class TestOwnDefaultProfile:
    def test_own_default_for_a_stack_wins_over_the_system_default(self):
        user, other = UserFactory(), UserFactory()
        system = get_default_profile(scaffolding_slug="flask-react", user=user)
        assert system is not None
        assert system.is_system
        own = GenerationProfileFactory(
            slug="my-flask",
            scaffolding_slug="flask-react",
            is_system=False,
            is_default=True,
            created_by=user,
        )
        assert get_default_profile(scaffolding_slug="flask-react", user=user) == own
        assert get_default_profile(scaffolding_slug="flask-react", user=other) == system

        # A newer version without the flag withdraws it; the older version does not keep winning.
        GenerationProfileFactory(
            slug="my-flask",
            version=2,
            scaffolding_slug="flask-react",
            is_system=False,
            is_default=False,
            created_by=user,
        )
        assert get_default_profile(scaffolding_slug="flask-react", user=user) == system

    def test_a_default_moved_to_another_stack_stops_winning_for_the_old_one(self):
        user = UserFactory()
        system = get_default_profile(scaffolding_slug="flask-react", user=user)
        fastapi_system = get_default_profile(scaffolding_slug="fastapi-react", user=user)
        own = {"slug": "my-default", "is_system": False, "is_default": True, "created_by": user}
        GenerationProfileFactory(**own, version=1, scaffolding_slug="flask-react")
        moved = GenerationProfileFactory(**own, version=2, scaffolding_slug="fastapi-react")

        # v1 still says "default for flask-react", but it is not the latest version any more.
        assert get_default_profile(scaffolding_slug="flask-react", user=user) == system
        assert get_default_profile(scaffolding_slug="fastapi-react", user=user) == moved
        assert moved != fastapi_system


def test_stacks_say_which_ones_are_the_requesters(user_client):
    client, user = user_client
    other = UserFactory()
    for slug, owner in (("mine-stack", user), ("their-stack", other)):
        Stack.objects.create(
            slug=slug,
            version=1,
            created_by=owner,
            is_approved=True,
            has_frontend=False,
            default_port=8000,
            backend_filename="app.py",
            backend_base_image="python:3.12-slim",
            server_kind="python",
            files={"requirements.txt": "flask\n"},
            content_hash=slug,
            dockerfile_mode=Stack.DockerfileMode.GENERATED,
        )
    stacks = {s["slug"]: s for s in client.get(f"{API}/stacks/").json()}
    assert stacks["mine-stack"]["is_own"] is True
    assert stacks["their-stack"]["is_own"] is False
    assert stacks["flask-react"]["is_own"] is False
