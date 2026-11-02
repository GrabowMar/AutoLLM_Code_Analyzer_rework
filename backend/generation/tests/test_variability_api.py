"""Variability endpoints: experiment repeatability, same-setup replicates (contract C3) and model variability."""

from __future__ import annotations

import pytest
from django.core.cache import cache

from backend.generation.models import Experiment
from backend.generation.models import GenerationArtifact
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.inference.models import LLMEndpoint
from backend.llm_models.tests.factories import LLMModelFactory
from backend.statistics.services import variability as svc
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"
C3_TOP = {"job_id", "grouping", "n", "members", "summary", "notes"}
C3_MEMBER = {
    "job_id",
    "is_self",
    "relation",
    "seed",
    "repeat_index",
    "experiment_id",
    "experiment_name",
    "condition_label",
    "batch_id",
    "created_at",
    "status",
    "pass_rate",
    "source_loc",
    "source_files",
    "cost",
    "total_tokens",
    "llm_calls",
    "wall_seconds",
    "termination",
    "code_issues",
    "issues_per_kloc",
    "findings_measured",
    "similarity_to_self",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def api(client, user):
    client.force_login(user)
    return client


SMOKE = {"passed": True, "health_ok": True, "pass_rate": 1.0, "endpoints_ok": 2, "endpoints_checked": 2}


def _conformance(pass_rate):
    passed = pass_rate == 1.0
    return {
        "source": "conformance",
        "status": "passed" if passed else "failed",
        "pass_rate": pass_rate,
        "passed": passed,
    }


def _job(
    user,
    *,
    fingerprint=None,
    run_fingerprint=None,
    seed=None,
    status="completed",
    pass_rate=1.0,
    functional=None,
    **kw,
):
    bundle = {}
    if fingerprint:
        bundle["config_fingerprint"] = fingerprint
    if run_fingerprint:
        bundle["run_fingerprint"] = run_fingerprint
    if seed is not None:
        bundle["seed"] = seed
    kw.setdefault("mode", "agent")
    kw.setdefault(
        "result_data",
        {
            "files": {"app.py": f"value = {seed}\nshared = 1\n"},
            "agent": {"wall_seconds": 100.0, "termination": "completed"},
        },
    )
    return GenerationJob.objects.create(
        created_by=user,
        status=status,
        resolved_bundle=bundle,
        metrics={
            "functional": functional or _conformance(pass_rate),
            "cost": 0.1,
            "total_tokens": 1000,
            "llm_calls": 5,
        }
        if status == "completed"
        else {},
        **kw,
    )


def _experiment(user, status=Experiment.Status.COMPLETED):
    experiment = ExperimentFactory(created_by=user, mode="scaffolding", repeats=2, status=status)
    apps = [AppRequirementTemplateFactory() for _ in range(3)]
    experiment.app_requirements.set(apps)
    conditions = [ExperimentConditionFactory(experiment=experiment, model=LLMModelFactory()) for _ in range(2)]
    for ci, condition in enumerate(conditions):
        for app in apps:
            for r in range(2):
                GenerationJob.objects.create(
                    mode="scaffolding",
                    created_by=user,
                    experiment=experiment,
                    condition=condition,
                    app_requirement=app,
                    repeat_index=r,
                    status=GenerationJob.Status.COMPLETED,
                    metrics={"functional": _conformance(1.0), "cost": 0.1 * (ci + 1) + 0.01 * r},
                    result_data={"files": {"app.py": f"c{ci} = {r}\n"}},
                )
    return experiment


class TestExperimentVariability:
    def test_owner_gets_the_payload(self, api, user):
        experiment = _experiment(user)
        res = api.get(f"{API}/experiments/{experiment.id}/variability/")
        assert res.status_code == 200, res.content
        body = res.json()
        assert body["experiment_id"] == str(experiment.id)
        assert body["design"]["n_runs"] == 12
        for key in (
            "conditions",
            "cells",
            "components",
            "icc",
            "pass_at_k",
            "checks",
            "similarity",
            "finding_overlap",
        ):
            assert key in body
        assert body["rank_stability"]["pass_rate"]["n_apps"] == 3
        assert body["planner"] is not None

    def test_query_options_mirror_results(self, api, user):
        experiment = _experiment(user)
        res = api.get(f"{API}/experiments/{experiment.id}/variability/?common_cells=false&fingerprint=majority")
        assert res.status_code == 200
        assert res.json()["filters"]["common_cells"] is False
        assert api.get(f"{API}/experiments/{experiment.id}/variability/?fingerprint=nope").status_code == 400
        assert api.get(f"{API}/experiments/{experiment.id}/variability/?format=xml").status_code == 400

    def test_csv(self, api, user):
        experiment = _experiment(user)
        res = api.get(f"{API}/experiments/{experiment.id}/variability/?format=csv")
        assert res.status_code == 200
        assert res["Content-Type"].startswith("text/csv")
        assert "variability.csv" in res["Content-Disposition"]
        assert len(res.content.decode().strip().splitlines()) == 1 + 6

    def test_access_control(self, api, user):
        other = _experiment(UserFactory())
        assert api.get(f"{API}/experiments/{other.id}/variability/").status_code == 404
        assert api.get(f"{API}/experiments/not-a-uuid/variability/").status_code == 404

    def test_anonymous_is_rejected(self, client, user):
        experiment = _experiment(user)
        assert client.get(f"{API}/experiments/{experiment.id}/variability/").status_code in (401, 403)


class TestReplicates:
    def test_contract_shape_and_relations(self, api, user):
        experiment = ExperimentFactory(created_by=user, repeats=2)
        condition = ExperimentConditionFactory(experiment=experiment)
        app = AppRequirementTemplateFactory()
        cell = {"experiment": experiment, "condition": condition, "app_requirement": app}
        retried = _job(user, fingerprint="fp1", run_fingerprint="r0", seed=5, repeat_index=1, **cell)
        me = _job(user, fingerprint="fp1", run_fingerprint="r1", seed=1, repeat_index=0, **cell)
        sibling = _job(user, fingerprint="fp1", run_fingerprint="r2", seed=2, repeat_index=1, **cell)
        replay = _job(user, fingerprint="fp1", run_fingerprint="r1", seed=1)
        rerun = _job(user, fingerprint="fp1", run_fingerprint="r3", seed=3)
        failed = _job(user, fingerprint="fp1", run_fingerprint="r4", seed=4, status="failed")
        foreign = _job(UserFactory(), fingerprint="fp1", run_fingerprint="r1", seed=1)
        other_setup = _job(user, fingerprint="fp2", run_fingerprint="r9", seed=9)

        res = api.get(f"{API}/jobs/{me.id}/replicates/")
        assert res.status_code == 200, res.content
        body = res.json()
        assert set(body) >= C3_TOP
        assert body["job_id"] == str(me.id)
        assert body["grouping"] == {"key": "config_fingerprint", "value": "fp1", "approximate": False}
        assert body["n"] == 6
        relation = {m["job_id"]: m["relation"] for m in body["members"]}
        assert relation == {
            str(retried.id): "rerun",  # a superseded attempt of the same cell
            str(me.id): "self",
            str(sibling.id): "replicate",
            str(replay.id): "replay",
            str(rerun.id): "rerun",
            str(failed.id): "rerun",
        }
        assert str(foreign.id) not in relation
        assert str(other_setup.id) not in relation
        for member in body["members"]:
            assert set(member) >= C3_MEMBER
        by_id = {m["job_id"]: m for m in body["members"]}
        assert by_id[str(me.id)]["is_self"] is True
        assert by_id[str(me.id)]["similarity_to_self"] is None
        assert by_id[str(me.id)]["condition_label"]
        assert by_id[str(me.id)]["experiment_name"] == experiment.name
        assert by_id[str(sibling.id)]["seed"] == 2
        assert by_id[str(sibling.id)]["source_loc"] == 2
        assert by_id[str(sibling.id)]["wall_seconds"] == 100.0
        assert by_id[str(sibling.id)]["termination"] == "completed"
        # value = 1 / shared = 1 vs value = 2 / shared = 1: one of three normalized lines in common.
        assert by_id[str(sibling.id)]["similarity_to_self"]["path_jaccard"] == 1.0
        assert by_id[str(sibling.id)]["similarity_to_self"]["line_jaccard"] == pytest.approx(1 / 3, abs=1e-3)
        # Unanalyzed: unmeasured, never 0; a failed run has no metrics and no similarity.
        assert by_id[str(sibling.id)]["code_issues"] is None
        assert by_id[str(sibling.id)]["findings_measured"] is False
        assert by_id[str(failed.id)]["pass_rate"] is None
        assert by_id[str(failed.id)]["similarity_to_self"] is None
        # Summary: completed members only (5), pass rate has no CV.
        assert set(body["summary"]) == set(svc.REPLICATE_METRICS)
        assert body["summary"]["pass_rate"] == {"n": 5, "mean": 1.0, "sd": 0.0, "min": 1.0, "max": 1.0, "cv": None}
        assert body["summary"]["cost"]["n"] == 5
        assert body["summary"]["code_issues"]["n"] == 0
        assert any("pools every completed member" in n for n in body["notes"])

    def test_pass_rate_is_the_conformance_suite_only(self, api, user):
        """A smoke check's 1.0 (no source) and an ungraded run are not pass rates: — and left out of the summary."""
        me = _job(user, fingerprint="fp", run_fingerprint="r1", seed=1, pass_rate=0.4375)
        smoke = _job(user, fingerprint="fp", run_fingerprint="r2", seed=2, functional=SMOKE)
        infra = _job(
            user,
            fingerprint="fp",
            run_fingerprint="r3",
            seed=3,
            functional={"source": "conformance", "status": "infra_error", "pass_rate": 0.0, "passed": False},
        )
        body = api.get(f"{API}/jobs/{me.id}/replicates/").json()
        rate = {m["job_id"]: m["pass_rate"] for m in body["members"]}
        assert rate == {str(me.id): 0.4375, str(smoke.id): None, str(infra.id): None}
        assert body["summary"]["pass_rate"] == {
            "n": 1,
            "mean": 0.4375,
            "sd": None,
            "min": 0.4375,
            "max": 0.4375,
            "cv": None,
        }
        assert any(n.startswith(svc.NOTE_CONFORMANCE_ONLY) and "2 of 3 completed" in n for n in body["notes"])
        alone = api.get(f"{API}/jobs/{smoke.id}/replicates/").json()
        assert next(m for m in alone["members"] if m["is_self"])["pass_rate"] is None

    def test_replay_determinism_probe(self, api, user):
        me = _job(user, fingerprint="fp", run_fingerprint="r1", seed=1)
        replay = _job(user, fingerprint="fp", run_fingerprint="r1", seed=1)
        for job, contents in ((me, ["a", "b"]), (replay, ["a", "c"])):
            for i, content in enumerate(contents, start=1):
                GenerationArtifact.objects.create(
                    job=job,
                    stage=f"agent_call_{i:04d}",
                    response_payload={"status": 200, "provider": "P", "content": content},
                )
        body = api.get(f"{API}/jobs/{me.id}/replicates/").json()
        (probe,) = body["determinism"]
        assert probe["job_id"] == str(replay.id)
        assert probe["first_divergent_call"] == 2
        assert probe["identical_first_response"] is True
        assert probe["reproduced"] is False

    def test_legacy_jobs_group_approximately(self, api, user):
        model = LLMModelFactory()
        app = AppRequirementTemplateFactory()
        key = {
            "model": model,
            "app_requirement": app,
            "prompt_hash": "ph",
            "bundle_key": "agent:aider@1",
            "stack_slug": "flask-react",
        }
        me = _job(user, seed=1, **key)
        twin = _job(user, seed=2, **key)
        _job(user, seed=3, **{**key, "prompt_hash": "other"})
        _job(user, fingerprint="fp", seed=4, **key)  # has a fingerprint: never in an approximate group
        body = api.get(f"{API}/jobs/{me.id}/replicates/").json()
        assert body["grouping"]["key"] == "approximate"
        assert body["grouping"]["approximate"] is True
        assert body["grouping"]["value"] == f"agent|{model.pk}|{app.pk}|ph|agent:aider@1|flask-react"
        assert {m["job_id"] for m in body["members"]} == {str(me.id), str(twin.id)}
        assert any("Approximate" in n for n in body["notes"])

    def test_no_group_without_fingerprint_model_or_app(self, api, user):
        me = _job(user, seed=1, mode="custom")
        body = api.get(f"{API}/jobs/{me.id}/replicates/").json()
        assert body["grouping"] == {"key": None, "value": None, "approximate": False}
        assert body["n"] == 1
        assert [m["relation"] for m in body["members"]] == ["self"]

    def test_group_is_capped(self, user):
        jobs = [_job(user, fingerprint="fp", run_fingerprint=f"r{i}", seed=i) for i in range(4)]
        body = svc.job_replicates(jobs[0], user, cap=3)
        assert body["n"] == 4
        assert len(body["members"]) == 3
        assert any(m["is_self"] for m in body["members"])
        assert any("most recent" in n for n in body["notes"])

    def test_access_control(self, api, user):
        foreign = _job(UserFactory(), fingerprint="fp", seed=1)
        assert api.get(f"{API}/jobs/{foreign.id}/replicates/").status_code == 404
        assert api.get(f"{API}/jobs/not-a-uuid/replicates/").status_code == 404


class TestModelVariability:
    def test_groups_and_overall(self, api, user):
        model = LLMModelFactory()
        app = AppRequirementTemplateFactory()
        a = _job(user, fingerprint="g1", run_fingerprint="r1", seed=1, model=model, app_requirement=app)
        b = _job(user, fingerprint="g1", run_fingerprint="r1", seed=1, model=model, app_requirement=app, pass_rate=0.5)
        _job(user, fingerprint="g2", run_fingerprint="r5", seed=5, model=model, app_requirement=app)  # a group of one
        _job(UserFactory(), fingerprint="g1", run_fingerprint="r7", seed=7, model=model, app_requirement=app)
        for job in (a, b):
            GenerationArtifact.objects.create(
                job=job,
                stage="agent_call_0001",
                response_payload={"status": 200, "provider": "P", "content": "same"},
            )
        res = api.get(f"{API}/models/{model.pk}/variability/")
        assert res.status_code == 200, res.content
        body = res.json()
        assert (body["n_groups"], body["n_runs"], body["n_completed_jobs"]) == (1, 2, 3)
        (group,) = body["groups"]
        assert group["value"] == "g1"
        assert group["app_slug"] == app.slug
        assert group["pass"] == {"n": 2, "passed": 1, "agree": False}
        assert group["stats"]["pass_rate"]["cv"] is None
        assert group["similarity"]["n_pairs"] == 1
        assert group["replays"] == [{"job_a": str(a.id), "job_b": str(b.id), "first_divergent_call": None}]
        assert body["overall"]["pass_agreement"] == {"groups": 1, "agree": 0, "share": 0.0}
        assert body["overall"]["replays"] == {"pairs": 1, "reproduced": 1}
        assert body["overall"]["metrics"]["pass_rate"]["df"] == 1

    def test_smoke_checks_do_not_count_as_passes(self, api, user):
        model = LLMModelFactory()
        app = AppRequirementTemplateFactory()
        cell = {"model": model, "app_requirement": app}
        _job(user, fingerprint="g1", run_fingerprint="r1", seed=1, pass_rate=0.5, **cell)
        _job(user, fingerprint="g1", run_fingerprint="r2", seed=2, functional=SMOKE, **cell)
        _job(user, fingerprint="g1", run_fingerprint="r3", seed=3, pass_rate=0.25, **cell)
        body = api.get(f"{API}/models/{model.pk}/variability/").json()
        (group,) = body["groups"]
        assert group["n"] == 3
        # The smoke check's passed=True / 1.0 is neither a pass nor a pass rate.
        assert group["pass"] == {"n": 2, "passed": 0, "agree": True}
        assert (group["stats"]["pass_rate"]["n"], group["stats"]["pass_rate"]["mean"]) == (2, 0.375)
        assert svc.NOTE_CONFORMANCE_ONLY in body["notes"]

    def test_invisible_model_is_404(self, api, user):
        owner = UserFactory()
        endpoint = LLMEndpoint.objects.create(
            owner=owner,
            slug="gpu-private",
            name="GPU",
            kind=LLMEndpoint.Kind.OLLAMA,
        )
        hidden = LLMModelFactory(model_id="local-gpu-private/x", endpoint=endpoint)
        assert api.get(f"{API}/models/{hidden.pk}/variability/").status_code == 404
        assert api.get(f"{API}/models/999999999/variability/").status_code == 404

    def test_model_without_runs(self, api, user):
        model = LLMModelFactory()
        body = api.get(f"{API}/models/{model.pk}/variability/").json()
        assert (body["n_groups"], body["n_runs"]) == (0, 0)
        assert body["overall"]["median_shingle_jaccard"] is None
