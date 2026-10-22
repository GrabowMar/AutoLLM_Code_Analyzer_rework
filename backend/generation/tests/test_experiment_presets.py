"""Experiments with run presets: conditions, stack rule, layer precedence, comparability, launch, export."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.db import IntegrityError
from django.db import transaction

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ExperimentCondition
from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.models import RunPreset
from backend.generation.services import presets as preset_service
from backend.generation.services.agent.snapshot import build_agent_snapshot
from backend.generation.services.batches import batch_summary
from backend.generation.services.experiments import ExperimentError
from backend.generation.services.experiments import comparability
from backend.generation.services.experiments import experiment_layers
from backend.generation.services.experiments import export_experiment
from backend.generation.services.experiments import launch_experiment
from backend.generation.services.experiments import preview_experiment
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.generation.tests.factories import GenerationProfileFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.services import generators
from backend.runtime.models import Stack
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"
SUPPORTED = ["temperature", "top_p", "top_k", "min_p", "seed", "tools", "tool_choice", "max_tokens", "reasoning"]
DISPATCH = "backend.generation.services.experiments.dispatch_job"


@pytest.fixture(autouse=True)
def _no_auto_evaluation(settings):
    settings.AGENT_AUTO_EVALUATE = False


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def user_client(client, user):
    client.force_login(user)
    return client, user


def _model(**kw):
    kw.setdefault("model_id", "vendor/coder")
    kw.setdefault("metadata", {"openrouter_supported_parameters": SUPPORTED})
    kw.setdefault("max_output_tokens", 64_000)
    return LLMModelFactory(**kw)


def _app(slug="notes"):
    existing = AppRequirementTemplate.objects.filter(slug=slug).first()
    if existing is not None:
        return existing
    return AppRequirementTemplateFactory(
        slug=slug,
        name="Notes",
        is_default=True,  # a catalog spec: visible to every user (the API refuses hidden ones)
        description="",
        backend_requirements=["Notes have a title"],
        api_endpoints=[
            {"method": "GET", "path": "/api/notes", "description": "List", "request": None, "response": {}},
        ],
    )


def _preset(user, kind, slug, config):
    return preset_service.create_preset(user, kind=kind, slug=slug, name=slug.replace("-", " ").title(), config=config)


def _whole(user, slug="aider-whole"):
    return _preset(user, "harness", slug, {"harness": "aider", "options": {"edit_format": "whole"}})


def _default(kind, harness=None):
    return preset_service.system_default_preset(kind, harness)


def _experiment(user, *, apps=None, **kw):
    kw.setdefault("repeats", 1)
    kw.setdefault("base_seed", 5)
    kw.setdefault("stack_slug", "flask-react")
    experiment = ExperimentFactory(created_by=user, **kw)
    experiment.app_requirements.set(apps if apps is not None else [_app()])
    return experiment


def _condition(experiment, model, *, preset=None, **kw):
    kw.setdefault("profile", None)
    if preset is not None:
        kw.setdefault("harness", preset.config["harness"])
    return ExperimentConditionFactory(experiment=experiment, model=model, harness_preset=preset, **kw)


def _send(client, method, path, payload=None):
    return getattr(client, method)(path, data=json.dumps(payload or {}), content_type="application/json")


def _launch_api(client, experiment_id):
    with (
        mock.patch("backend.generation.api.views.experiments.has_resolvable_key", return_value=True),
        mock.patch(DISPATCH),
    ):
        return client.post(f"{API}/experiments/{experiment_id}/launch/")


def _launch(experiment, user):
    with mock.patch(DISPATCH) as dispatch:
        batch = launch_experiment(experiment, user)
    return batch, dispatch


# ── conditions ────────────────────────────────────────────────────────


class TestConditions:
    def test_two_presets_of_one_harness_for_one_model(self, user_client):
        client, user = user_client
        whole = _whole(user)
        no_map = _preset(user, "harness", "aider-no-map", {"harness": "aider", "options": {"map_tokens": 0}})
        model = _model()
        app = _app()
        res = _send(
            client,
            "post",
            f"{API}/experiments/",
            {
                "name": "Aider ablation",
                "slug": "aider-ablation",
                "stack_slug": "flask-react",
                "app_requirement_ids": [app.id],
                "repeats": 2,
                "base_seed": 11,
            },
        )
        assert res.status_code == 200, res.content
        exp_id = res.json()["id"]
        for preset in (whole, no_map):
            res = _send(
                client,
                "post",
                f"{API}/experiments/{exp_id}/conditions/",
                {"model_id": model.id, "harness_preset_id": preset.id},
            )
            assert res.status_code == 200, res.content
            body = res.json()
            assert (body["profile"], body["harness"], body["harness_preset"]) == (None, "aider", preset.id)
            assert body["harness_preset_ref"] == {
                "id": preset.id,
                "slug": preset.slug,
                "version": 1,
                "name": preset.name,
            }
            assert body["bundle_slug"] is None

        comp = _send(client, "post", f"{API}/experiments/{exp_id}/preview/").json()["comparability"]
        assert comp["differs_on"] == ["harness_options"]
        assert comp["has_errors"] is False
        assert [row["harness_options"] for row in comp["conditions"]] == [{"edit_format": "whole"}, {"map_tokens": 0}]

        assert _launch_api(client, exp_id).status_code == 200
        jobs = list(GenerationJob.objects.filter(experiment_id=exp_id))
        assert len(jobs) == 4
        for job in jobs:
            assert job.status != GenerationJob.Status.FAILED, job.error_message
            assert job.profile is None
            assert job.agent_config["harness"] == "aider"
            assert job.harness_options == job.harness_preset.config["options"]
            assert job.harness_preset_id == job.condition.harness_preset_id
            assert job.resolved_bundle["presets"]["harness"]["slug"] == job.harness_preset.slug
        # One task identity, two methods, and paired seeds (common random numbers).
        assert len({j.prompt_hash for j in jobs}) == 1
        assert len({j.bundle_key for j in jobs}) == 2
        assert all("+opts:" in j.bundle_key for j in jobs)
        for repeat in (0, 1):
            assert len({j.experiment_seed for j in jobs if j.repeat_index == repeat}) == 1

    def test_duplicate_conditions_are_refused(self, user_client):
        client, user = user_client
        exp = _experiment(user)
        model = _model()
        whole = _whole(user)
        path = f"{API}/experiments/{exp.id}/conditions/"

        assert _send(client, "post", path, {"model_id": model.id, "harness_preset_id": whole.id}).status_code == 200
        again = _send(client, "post", path, {"model_id": model.id, "harness_preset_id": whole.id})
        assert (again.status_code, again.json()["code"]) == (400, "duplicate_condition")
        # Another preset with the same content is the same arm.
        twin = _whole(user, "aider-whole-copy")
        assert _send(client, "post", path, {"model_id": model.id, "harness_preset_id": twin.id}).status_code == 400
        # A legacy harness name runs that harness's system default preset.
        assert _send(client, "post", path, {"model_id": model.id, "harness": "aider"}).status_code == 200
        res = _send(client, "post", path, {"model_id": model.id, "harness_preset_id": _default("harness", "aider").id})
        assert res.status_code == 400
        # A blank harness runs the experiment default (opencode).
        assert _send(client, "post", path, {"model_id": model.id}).status_code == 200
        res = _send(
            client,
            "post",
            path,
            {"model_id": model.id, "harness_preset_id": _default("harness", "opencode").id},
        )
        assert res.status_code == 400
        # The same model with param overrides is a declared ablation, not a duplicate.
        res = _send(
            client,
            "post",
            path,
            {"model_id": model.id, "harness": "opencode", "param_overrides": {"top_p": 0.9}},
        )
        assert res.status_code == 200
        assert exp.conditions.count() == 4

    def test_constraint_treats_nulls_as_equal(self, user):
        exp = _experiment(user)
        model = _model()
        ExperimentCondition.objects.create(experiment=exp, model=model, harness="aider")
        with pytest.raises(IntegrityError), transaction.atomic():
            ExperimentCondition.objects.create(experiment=exp, model=model, harness="aider")
        whole = _whole(user)
        ExperimentCondition.objects.create(experiment=exp, model=model, harness="aider", harness_preset=whole)
        with pytest.raises(IntegrityError), transaction.atomic():
            ExperimentCondition.objects.create(experiment=exp, model=model, harness="aider", harness_preset=whole)

    def test_validation(self, user_client):
        client, user = user_client
        exp = _experiment(user)
        path = f"{API}/experiments/{exp.id}/conditions/"
        model = _model()
        toolless = _model(model_id="vendor/no-tools", supports_function_calling=False)
        sampling = _preset(user, "sampling", "cold", {"llm_params": {"temperature": 0}})
        foreign = _whole(UserFactory(), "someone-elses")

        def status(payload):
            return _send(client, "post", path, payload).status_code

        assert status({"model_id": model.id, "harness_preset_id": sampling.id}) == 400  # wrong kind
        assert status({"model_id": model.id, "harness_preset_id": 987_654}) == 404
        assert status({"model_id": model.id, "harness_preset_id": foreign.id}) == 404  # not visible
        whole = _whole(user)
        assert status({"model_id": model.id, "harness": "opencode", "harness_preset_id": whole.id}) == 400
        assert status({"model_id": model.id, "harness": "cursor"}) == 400
        # Tool calling is checked for the preset's harness and for the experiment default.
        assert status({"model_id": toolless.id, "harness_preset_id": _default("harness", "opencode").id}) == 400
        assert status({"model_id": toolless.id}) == 400
        assert status({"model_id": toolless.id, "harness_preset_id": whole.id}) == 200
        # Profiles must be visible (system or own).
        private = GenerationProfileFactory(is_system=False, created_by=UserFactory())
        assert status({"model_id": model.id, "profile_id": private.id}) == 404

        scaffolding = _experiment(user, mode="scaffolding")
        spath = f"{API}/experiments/{scaffolding.id}/conditions/"
        res = _send(client, "post", spath, {"model_id": model.id})
        assert res.status_code == 400
        assert "profile" in res.json()["detail"]
        profile = GenerationProfileFactory()
        res = _send(
            client,
            "post",
            spath,
            {"model_id": model.id, "profile_id": profile.id, "harness_preset_id": whole.id},
        )
        assert res.status_code == 400
        assert "agent-mode" in res.json()["detail"]

    def test_profile_less_agent_condition_end_to_end(self, user_client):
        client, user = user_client
        exp = _experiment(user, repeats=1)
        aider = _default("harness", "aider")
        res = _send(
            client,
            "post",
            f"{API}/experiments/{exp.id}/conditions/",
            {"model_id": _model().id, "harness_preset_id": aider.id},
        )
        assert res.status_code == 200
        condition = ExperimentCondition.objects.get(id=res.json()["id"])
        assert str(condition).endswith("/ aider-default v1")

        listed = client.get(f"{API}/experiments/{exp.id}/conditions/").json()
        assert (listed[0]["profile"], listed[0]["bundle_slug"], listed[0]["bundle_version"]) == (None, None, None)
        preview = _send(client, "post", f"{API}/experiments/{exp.id}/preview/").json()
        row = preview["per_condition"][0]
        assert (row["profile"], row["bundle_slug"], row["stack_slug"]) == (None, None, "flask-react")
        assert row["harness_preset"]["slug"] == "aider-default"
        assert preview["comparability"]["conditions"][0]["profile"] is None
        assert preview["launch_errors"] == []

        assert _launch_api(client, exp.id).status_code == 200
        job = GenerationJob.objects.get(experiment=exp)
        assert (job.profile_id, job.stack_slug, job.status) == (None, "flask-react", GenerationJob.Status.PENDING)
        assert client.get(f"{API}/experiments/{exp.id}/status/").json()["jobs_created"] == 1
        exported = client.get(f"{API}/experiments/{exp.id}/export/").json()
        assert exported["conditions"][0]["profile"] is None
        assert exported["conditions"][0]["bundle_slug"] is None

        report = generators.generate_experiment_report({"experiment_id": str(exp.id)})
        assert report["conditions"][0]["bundle_key"] == "aider-default@1"
        assert report["conditions"][0]["label"].endswith("/ aider-default v1")


# ── experiment settings: stack rule, presets ──────────────────────────


class TestExperimentSettings:
    def _create(self, client, **payload):
        payload = {"name": "Exp", "slug": payload.pop("slug", "exp"), **payload}
        return _send(client, "post", f"{API}/experiments/", payload)

    def test_create_with_stack_and_presets(self, user_client):
        client, user = user_client
        sampling = _preset(user, "sampling", "cold", {"llm_params": {"temperature": 0}})
        budget = _preset(user, "budget", "smoke", {"max_llm_requests": 30, "max_cost_usd": 3})
        res = self._create(
            client,
            stack_slug="react-flask",  # an alias: stored canonical
            sampling_preset_id=sampling.id,
            budget_preset_id=budget.id,
        )
        assert res.status_code == 200, res.content
        body = res.json()
        assert body["stack_slug"] == "flask-react"
        assert (body["sampling_preset"], body["budget_preset"]) == (sampling.id, budget.id)
        assert body["sampling_preset_ref"] == {"id": sampling.id, "slug": "cold", "version": 1, "name": sampling.name}
        assert body["budget_preset_ref"]["slug"] == "smoke"

        legacy = self._create(client, slug="legacy").json()
        assert (legacy["stack_slug"], legacy["sampling_preset_ref"], legacy["budget_preset"]) == (None, None, None)

    def test_create_errors(self, user_client):
        client, user = user_client
        sampling = _preset(user, "sampling", "cold", {"llm_params": {"temperature": 0}})
        budget = _preset(user, "budget", "smoke", {"max_llm_requests": 30})
        other_user = UserFactory()
        foreign = _preset(other_user, "budget", "theirs", {"max_llm_requests": 20})
        Stack.objects.create(
            slug="private-stack",
            version=1,
            created_by=other_user,
            is_approved=False,
            has_frontend=False,
            default_port=8000,
            backend_filename="app.py",
            backend_base_image="python:3.12-slim",
            server_kind="python",
            files={"requirements.txt": "flask\n"},
            content_hash="x",
            dockerfile_mode=Stack.DockerfileMode.GENERATED,
        )
        assert self._create(client, sampling_preset_id=budget.id).status_code == 400  # wrong kind
        assert self._create(client, budget_preset_id=987_654).status_code == 404
        assert self._create(client, budget_preset_id=foreign.id).status_code == 404
        assert self._create(client, stack_slug="no-such-stack").status_code == 404
        assert self._create(client, stack_slug="private-stack").status_code == 403
        res = self._create(client, mode="scaffolding", budget_preset_id=budget.id)
        assert res.status_code == 400
        assert "agent-mode" in res.json()["detail"]
        assert self._create(client, base_seed=-1).status_code == 422
        # A scaffolding experiment may take a sampling preset (its llm_params).
        assert self._create(client, mode="scaffolding", sampling_preset_id=sampling.id).status_code == 200

    def test_stack_rule_on_conditions_and_patch(self, user_client):
        client, user = user_client
        exp = _experiment(user, stack_slug="fastapi-react")
        model = _model()
        flask = GenerationProfileFactory(scaffolding_slug="flask-react")
        fastapi = GenerationProfileFactory(scaffolding_slug="fastapi-react")
        path = f"{API}/experiments/{exp.id}/conditions/"

        res = _send(client, "post", path, {"model_id": model.id, "profile_id": flask.id, "harness": "aider"})
        assert res.status_code == 400
        assert "fastapi-react" in res.json()["detail"]
        assert (
            _send(
                client,
                "post",
                path,
                {"model_id": model.id, "profile_id": fastapi.id, "harness": "aider"},
            ).status_code
            == 200
        )

        # Moving the experiment to a stack an existing condition profile does not target is refused.
        res = _send(client, "patch", f"{API}/experiments/{exp.id}/", {"stack_slug": "flask-react"})
        assert res.status_code == 400
        exp.refresh_from_db()
        assert exp.stack_slug == "fastapi-react"
        assert _send(client, "patch", f"{API}/experiments/{exp.id}/", {"stack_slug": "no-such"}).status_code == 404

        # Null = legacy: each condition runs on its own profile's stack.
        res = _send(client, "patch", f"{API}/experiments/{exp.id}/", {"stack_slug": None})
        assert (res.status_code, res.json()["stack_slug"]) == (200, None)
        assert (
            _send(client, "post", path, {"model_id": model.id, "profile_id": flask.id, "harness": "aider"}).status_code
            == 200
        )
        preview = _send(client, "post", f"{API}/experiments/{exp.id}/preview/").json()
        assert preview["stacks"] == ["fastapi-react", "flask-react"]
        assert "stack_slug" in preview["comparability"]["differs_on"]
        assert "profile" in preview["comparability"]["differs_on"]

    def test_patch_presets_and_mode(self, user_client):
        client, user = user_client
        exp = _experiment(user)
        budget = _preset(user, "budget", "smoke", {"max_llm_requests": 30})
        sampling = _preset(user, "sampling", "cold", {"llm_params": {"temperature": 0}})
        url = f"{API}/experiments/{exp.id}/"

        res = _send(
            client,
            "patch",
            url,
            {"budget_preset_id": budget.id, "sampling_preset_id": sampling.id, "name": None},
        )
        assert res.status_code == 200
        assert (res.json()["budget_preset"], res.json()["sampling_preset"], res.json()["name"]) == (
            budget.id,
            sampling.id,
            exp.name,
        )
        assert _send(client, "patch", url, {"budget_preset_id": sampling.id}).status_code == 400

        # Scaffolding has no budget: switching needs the budget preset cleared in the same request.
        assert _send(client, "patch", url, {"mode": "scaffolding"}).status_code == 400
        res = _send(client, "patch", url, {"mode": "scaffolding", "budget_preset_id": None})
        assert (res.status_code, res.json()["mode"], res.json()["budget_preset"]) == (200, "scaffolding", None)
        assert res.json()["sampling_preset"] == sampling.id

        # ... and every condition must have a profile and no harness.
        agent = _experiment(user)
        _condition(agent, _model(), preset=_default("harness", "aider"))
        res = _send(client, "patch", f"{API}/experiments/{agent.id}/", {"mode": "scaffolding"})
        assert res.status_code == 400
        assert "profile" in res.json()["detail"]


# ── layers ────────────────────────────────────────────────────────────


class TestLayers:
    def test_precedence_reaches_job_fields_and_snapshot(self, user):
        profile = GenerationProfileFactory(
            scaffolding_slug="flask-react",
            llm_config={"temperature": 0.1, "top_p": 0.5, "top_k": 10, "max_tokens": 16000},
        )
        sampling = _preset(
            user,
            "sampling",
            "warm",
            {"llm_params": {"temperature": 0.2, "top_k": 40}, "sampling_policy": "harness_native"},
        )
        budget = _preset(
            user,
            "budget",
            "tight",
            {"max_llm_requests": 30, "wall_clock_seconds": 3600, "network_policy": "none"},
        )
        exp = _experiment(
            user,
            sampling_preset=sampling,
            budget_preset=budget,
            llm_defaults={"temperature": 0.3, "min_p": 0.05},
            agent_config={"max_llm_requests": 40},
        )
        condition = _condition(exp, _model(), profile=profile, harness="aider", param_overrides={"temperature": 0.4})

        layers = experiment_layers(exp, condition)
        # job.llm_params: sampling preset -> llm_defaults -> param_overrides (the profile sits below, in the snapshot)
        assert layers.llm_params == {"temperature": 0.4, "top_k": 40, "min_p": 0.05}
        # job.agent_config: budget preset -> sampling policy -> experiment.agent_config, + the harness
        assert layers.agent_config == {
            "max_llm_requests": 40,
            "wall_clock_seconds": 3600,
            "network_policy": "none",
            "sampling_policy": "harness_native",
            "harness": "aider",
        }
        assert layers.harness_preset == _default("harness", "aider")

        batch, _ = _launch(exp, user)
        job = batch.jobs.get()
        assert job.status == GenerationJob.Status.PENDING, job.error_message
        assert (job.llm_params, job.agent_config, job.max_tokens) == (layers.llm_params, layers.agent_config, 32000)
        assert (job.sampling_preset, job.budget_preset, job.harness_preset) == (
            sampling,
            budget,
            layers.harness_preset,
        )
        snapshot = job.resolved_bundle
        llm = snapshot["llm"]
        # max_tokens -> profile -> sampling preset -> llm_defaults -> param_overrides
        assert (llm["temperature"], llm["top_p"], llm["top_k"], llm["min_p"], llm["max_tokens"]) == (
            0.4,
            0.5,
            40,
            0.05,
            16000,
        )
        agent = snapshot["agent"]
        assert (agent["max_llm_requests"], agent["wall_clock_seconds"], agent["network_policy"]) == (40, 3600, "none")
        assert (agent["sampling_policy"], agent["harness"]) == ("harness_native", "aider")
        assert {kind: (snapshot["presets"][kind] or {}).get("slug") for kind in ("harness", "sampling", "budget")} == {
            "harness": "aider-default",
            "sampling": "warm",
            "budget": "tight",
        }

        # Comparability resolves exactly what the snapshot froze.
        row = comparability(exp)["conditions"][0]
        forced = {
            k: v
            for k, v in snapshot["gateway"]["forced_params"].items()
            if k not in ("seed", "provider", "max_tokens")
        }
        assert row["forced_params"] == forced == {"temperature": 0.4, "top_p": 0.5, "top_k": 40, "min_p": 0.05}
        assert row["output_cap"] == snapshot["gateway"]["max_output_tokens"] == 16000
        assert (row["sampling_policy"], row["profile"], row["param_overrides"]) == (
            "harness_native",
            f"{profile.slug}@{profile.version}",
            {"temperature": 0.4},
        )

    def test_inline_agent_config_wins_over_the_sampling_policy(self, user):
        sampling = _preset(user, "sampling", "native", {"llm_params": {}, "sampling_policy": "harness_native"})
        exp = _experiment(user, sampling_preset=sampling)
        condition = _condition(exp, _model(), harness="aider")
        assert experiment_layers(exp, condition).effective_agent_config["sampling_policy"] == "harness_native"
        exp.agent_config = {"sampling_policy": "provider_default"}
        assert experiment_layers(exp, condition).effective_agent_config["sampling_policy"] == "provider_default"

    def test_condition_max_tokens_is_the_job_column(self, user):
        exp = _experiment(user)
        condition = _condition(exp, _model(), harness="aider", param_overrides={"max_tokens": 8000})
        layers = experiment_layers(exp, condition)
        assert layers.max_tokens == 8000
        batch, _ = _launch(exp, user)
        job = batch.jobs.get()
        assert job.max_tokens == 8000
        assert job.resolved_bundle["gateway"]["max_output_tokens"] == 8000

    def test_cost_estimate_uses_the_budget_preset(self, user):
        budget = _preset(user, "budget", "smoke", {"max_llm_requests": 30, "max_cost_usd": 3})
        exp = _experiment(user, repeats=2, budget_preset=budget)
        _condition(exp, _model(), harness="aider")
        preview = preview_experiment(exp)
        assert (preview["estimated_cost"], preview["cost_estimate_kind"]) == (6.0, "budget_upper_bound")
        assert preview["agent_config"]["max_cost_usd"] == 3
        assert preview["presets"]["budget"]["slug"] == "smoke"
        assert preview["presets"]["sampling"]["slug"] == "provider-default"

    def test_scaffolding_takes_only_explicit_sampling_presets(self, user):
        profile = GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()
        cold = _preset(
            user,
            "sampling",
            "cold",
            {"llm_params": {"temperature": 0}, "sampling_policy": "harness_native"},
        )
        exp = _experiment(user, mode="scaffolding", sampling_preset=cold)
        _condition(exp, _model(), profile=profile)
        batch, _ = _launch(exp, user)
        job = batch.jobs.get()
        assert job.status == GenerationJob.Status.PENDING, job.error_message
        assert (job.llm_params, job.temperature, job.agent_config, job.stack_slug) == (
            {"temperature": 0},
            0.0,
            {},
            "flask-react",
        )
        assert (job.sampling_preset, job.harness_preset, job.budget_preset) == (cold, None, None)
        assert job.resolved_bundle["llm"]["temperature"] == 0
        assert job.resolved_bundle["presets"]["sampling"]["slug"] == "cold"

        plain = _experiment(user, mode="scaffolding", slug="plain")
        _condition(plain, _model(model_id="vendor/other"), profile=profile)
        batch, _ = _launch(plain, user)
        job = batch.jobs.get()
        assert (job.sampling_preset, job.temperature, job.llm_params) == (None, 0.3, {})


# ── comparability ─────────────────────────────────────────────────────


class TestComparability:
    def test_differs_on_and_seed_handling(self, user):
        exp = _experiment(user, llm_defaults={"temperature": 0.2})
        full = _model(model_id="a/full")
        small = _model(
            model_id="b/small",
            max_output_tokens=8000,
            metadata={"openrouter_supported_parameters": ["tools", "temperature"]},
        )
        bare = _model(
            model_id="c/bare",
            max_output_tokens=0,
            context_window=0,
            metadata={"openrouter_supported_parameters": ["tools", "seed"]},
        )
        aider = _default("harness", "aider")
        for model in (full, small, bare):
            _condition(exp, model, preset=aider)

        report = comparability(exp)
        rows = {row["model"]: row for row in report["conditions"]}
        assert report["differs_on"] == ["forced_params", "output_cap", "seed_forwarded"]
        assert (report["has_errors"], report["has_problems"]) == (False, True)
        assert rows["a/full"]["forced_params"] == {"temperature": 0.2}
        assert rows["c/bare"]["forced_params"] == {}
        # Output caps come from the snapshot resolver (no 1e9 fallback).
        assert [rows[m]["output_cap"] for m in ("a/full", "b/small", "c/bare")] == [32000, 8000, 32000]
        # The seed is never a "dropped param"; it has its own column.
        assert [rows[m]["seed_forwarded"] for m in ("a/full", "b/small", "c/bare")] == [True, False, True]
        assert rows["b/small"]["dropped_params"] == {}
        assert rows["b/small"]["problems"] == []
        assert rows["c/bare"]["dropped_params"] == {"temperature": "not supported by this model on OpenRouter"}
        assert any("temperature not applied" in p for p in rows["c/bare"]["problems"])
        assert any("context window" in p for p in rows["c/bare"]["problems"])
        assert rows["c/bare"]["context_window_source"] == "fallback"
        assert all(row["provider_routing"] == "default" for row in rows.values())

        batch, _ = _launch(exp, user)
        bare_job = batch.jobs.get(model=bare)
        assert bare_job.resolved_bundle["gateway"]["max_output_tokens"] == rows["c/bare"]["output_cap"]

    def test_declared_ablations_are_surfaced(self, user):
        exp = _experiment(user)
        model = _model()
        profile = GenerationProfileFactory(scaffolding_slug="flask-react")
        _condition(exp, model, preset=_default("harness", "aider"))
        _condition(exp, model, preset=_whole(user), profile=profile, param_overrides={"provider": {"only": ["acme"]}})
        report = comparability(exp)
        assert report["differs_on"] == ["harness_options", "param_overrides", "profile", "provider_routing"]
        assert report["conditions"][1]["provider_routing"] == {"only": ["acme"]}

    def test_legacy_duplicates_are_flagged_not_blocking(self, user):
        exp = _experiment(user)
        model = _model()
        first = _condition(exp, model, preset=_default("harness", "aider"))
        ExperimentCondition.objects.create(experiment=exp, model=model, harness="aider")
        report = comparability(exp)
        assert report["has_errors"] is False
        assert any(f"condition #{first.id}" in p for p in report["conditions"][1]["problems"])


# ── launch ────────────────────────────────────────────────────────────


class TestLaunch:
    def test_blocking_problems_refuse_the_launch_before_any_write(self, user_client):
        client, user = user_client
        exp = _experiment(user)
        toolless = _model(model_id="vendor/no-tools", supports_function_calling=False)
        _condition(exp, toolless, preset=_default("harness", "opencode"))

        with pytest.raises(ExperimentError) as exc:
            _launch(exp, user)
        assert exc.value.code == "models_without_tools"
        res = _launch_api(client, exp.id)
        assert res.status_code == 400
        assert res.json()["errors"][0]["code"] == "models_without_tools"
        assert preview_experiment(exp)["launch_errors"][0]["code"] == "models_without_tools"
        assert not GenerationBatch.objects.filter(created_by=user).exists()
        assert not GenerationJob.objects.filter(experiment=exp).exists()

    def test_invalid_stored_preset_and_stack_mismatch_refuse(self, user):
        exp = _experiment(user)
        whole = _whole(user)
        _condition(exp, _model(), preset=whole)
        RunPreset.objects.filter(id=whole.id).update(config={"harness": "aider", "options": {"edit_format": "bogus"}})
        with pytest.raises(ExperimentError) as exc:
            _launch(exp, user)
        assert exc.value.code == "invalid_preset"

        other = _experiment(user, slug="other")
        _condition(
            other,
            _model(model_id="vendor/x"),
            harness="aider",
            profile=GenerationProfileFactory(scaffolding_slug="flask-react"),
        )
        type(other).objects.filter(id=other.id).update(stack_slug="fastapi-react")  # bypasses the API check
        other.refresh_from_db()
        with pytest.raises(ExperimentError) as exc:
            _launch(other, user)
        assert exc.value.code == "stack_mismatch"
        assert not GenerationJob.objects.filter(created_by=user).exists()

    def test_batches_count_created_jobs_and_record_the_design(self, user):
        exp = _experiment(user, repeats=2, base_seed=5)
        default, whole = _default("harness", "aider"), _whole(user)
        model = _model()
        _condition(exp, model, preset=default)
        _condition(exp, model, preset=whole)

        batch, dispatch = _launch(exp, user)
        assert (batch.total_jobs, batch.status, dispatch.call_count) == (4, "pending", 4)
        request = GenerationBatch.objects.get(id=batch.id).request
        assert request["experiment_id"] == str(exp.id)
        assert (request["base_seed"], request["order_seed"], request["trials"], request["stack_slug"]) == (
            5,
            5,
            2,
            "flask-react",
        )
        assert request["harness_preset_ids"] == [default.id, whole.id]
        assert request["harnesses"] == ["aider"]
        assert request["sampling_preset_id"] == _default("sampling").id
        assert request["budget_preset_id"] == _default("budget").id
        assert (request["cell_count"], request["pending_cell_count"]) == (4, 4)

        # A failed and a cancelled cell are topped up; nothing else is duplicated.
        jobs = list(GenerationJob.objects.filter(experiment=exp).order_by("created_at"))
        GenerationJob.objects.filter(id=jobs[0].id).update(status=GenerationJob.Status.FAILED)
        GenerationJob.objects.filter(id=jobs[1].id).update(status=GenerationJob.Status.CANCELLED)
        top_up, dispatch = _launch(exp, user)
        assert (top_up.total_jobs, dispatch.call_count, top_up.request["pending_cell_count"]) == (2, 2, 2)
        assert GenerationJob.objects.filter(experiment=exp).count() == 6
        # The top-up reproduces the cells' seeds (common random numbers survive).
        new = {(j.condition_id, j.app_requirement_id, j.repeat_index): j.experiment_seed for j in top_up.jobs.all()}
        assert new == {(j.condition_id, j.app_requirement_id, j.repeat_index): j.experiment_seed for j in jobs[:2]}

        empty, dispatch = _launch(exp, user)
        assert (empty.total_jobs, empty.status, dispatch.call_count) == (0, "completed", 0)
        summary = batch_summary(GenerationBatch.objects.get(id=empty.id))
        assert summary["experiment"]["id"] == exp.id
        assert (summary["trials"], summary["base_seed"], summary["stack_slug"]) == (2, 5, "flask-react")

    def test_all_snapshots_failing_marks_the_batch_failed(self, user):
        exp = _experiment(user, repeats=2)
        _condition(exp, _model(), harness="aider")
        with mock.patch(
            "backend.generation.services.experiments.apply_agent_snapshot_to_job",
            side_effect=RuntimeError("boom"),
        ):
            batch, dispatch = _launch(exp, user)
        assert (batch.status, batch.total_jobs, batch.failed_jobs, dispatch.call_count) == ("failed", 2, 2, 0)
        assert GenerationBatch.objects.get(id=batch.id).status == "failed"


# ── identity of default and legacy launches ───────────────────────────


def _identity(snapshot):
    return {
        "prompt_hash": snapshot["prompt_hash"],
        "run_fingerprint": snapshot["run_fingerprint"],
        "config_fingerprint": snapshot["config_fingerprint"],
        "method_key": snapshot["method_key"],
        "brief": snapshot["brief"]["sha256"],
        "agent": snapshot["agent"],
        "gateway": snapshot["gateway"],
        "harness": snapshot["harness"],
    }


class TestIdentity:
    def test_default_profile_less_launch_is_the_default_run(self, user):
        app = _app()
        model = _model()
        exp = _experiment(user, apps=[app], base_seed=9)
        _condition(exp, model, preset=_default("harness", "aider"))
        batch, _ = _launch(exp, user)
        job = batch.jobs.get()
        # The plain default builder call that test_default_run_golden pins to HEAD's values.
        expected = build_agent_snapshot(
            app_requirement=app,
            description="",
            profile=None,
            stack_slug="flask-react",
            model=model,
            max_tokens=32000,
            experiment_seed=job.experiment_seed,
            llm_overrides={},
            agent_overrides={"harness": "aider"},
        )
        assert _identity(job.resolved_bundle) == _identity(expected)
        assert job.bundle_key == f"agent:aider@{expected['harness']['version']}"

    def test_legacy_experiment_keeps_its_identity(self, user):
        app = _app()
        model = _model()
        profile = GenerationProfileFactory(scaffolding_slug="flask-react", llm_config={"top_p": 0.9})
        exp = _experiment(
            user,
            apps=[app],
            stack_slug="",
            agent_config={"max_cost_usd": 0.25},
            llm_defaults={"temperature": 0.2},
        )
        _condition(exp, model, profile=profile, harness="aider", param_overrides={"max_tokens": 8000})
        batch, _ = _launch(exp, user)
        job = batch.jobs.get()
        # The job fields the pre-preset launch wrote, fed to the same builder.
        expected = build_agent_snapshot(
            app_requirement=app,
            description="",
            profile=profile,
            stack_slug="flask-react",
            model=model,
            max_tokens=8000,
            experiment_seed=job.experiment_seed,
            llm_overrides={"temperature": 0.2, "max_tokens": 8000},
            agent_overrides={"max_cost_usd": 0.25, "harness": "aider"},
        )
        assert _identity(job.resolved_bundle) == _identity(expected)
        # The implicit system defaults are recorded as provenance only.
        assert (job.harness_preset.slug, job.sampling_preset.slug, job.budget_preset.slug) == (
            "aider-default",
            "provider-default",
            "platform-default",
        )


# ── export ────────────────────────────────────────────────────────────


def test_export_records_resolved_settings_and_provenance(user):
    budget = _preset(user, "budget", "smoke", {"max_llm_requests": 30, "max_cost_usd": 3})
    sampling = _preset(user, "sampling", "cold", {"llm_params": {"temperature": 0}})
    exp = _experiment(user, budget_preset=budget, sampling_preset=sampling, agent_config={"wall_clock_seconds": 1800})
    whole = _whole(user)
    _condition(exp, _model(), preset=whole)
    _launch(exp, user)

    payload = export_experiment(exp)
    experiment = payload["experiment"]
    assert experiment["stack_slug"] == "flask-react"
    # The budget preset is part of the effective config (not just experiment.agent_config).
    assert (experiment["agent_config"]["max_llm_requests"], experiment["agent_config"]["wall_clock_seconds"]) == (
        30,
        1800,
    )
    assert experiment["agent_config_overrides"] == {"wall_clock_seconds": 1800}
    assert experiment["presets"]["budget"] == preset_service.provenance(budget)
    assert experiment["presets"]["sampling"]["content_hash"] == sampling.content_hash
    condition = payload["conditions"][0]
    assert condition["harness_preset"] == preset_service.provenance(whole)
    assert (condition["harness"], condition["harness_options"], condition["llm_params"]) == (
        "aider",
        {"edit_format": "whole"},
        {"temperature": 0},
    )
    job = payload["jobs"][0]
    assert job["config_fingerprint"]
    assert job["harness"] == "aider"
    assert job["presets"]["harness"]["slug"] == "aider-whole"
    assert job["harness_options"] == {"edit_format": "whole"}
