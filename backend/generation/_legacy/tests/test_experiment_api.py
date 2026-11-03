"""API tests for /api/generation/experiments/*."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import GenerationJob
from backend.generation.models import GenerationProfile
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _openrouter_key():
    """Launches check the owner's OpenRouter key (inference.access.launch_issues); tests that need none patch it."""
    with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True):
        yield


def _enqueued(enqueue) -> int:
    """Jobs handed to the (patched) executor queue across every ``enqueue_jobs`` call."""
    return sum(len(call.args[0]) for call in enqueue.call_args_list)


def _flask_bundle() -> GenerationProfile:
    return GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_create_experiment(auth_client):
    client, _user = auth_client
    app = AppRequirementTemplateFactory(is_default=True)

    res = client.post(
        "/api/generation/experiments/",
        data=json.dumps(
            {
                "name": "Tone ablation",
                "slug": "tone-ablation",
                "app_requirement_ids": [app.id],
                "repeats": 5,
            },
        ),
        content_type="application/json",
    )

    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Tone ablation"
    assert body["status"] == "draft"
    assert body["repeats"] == 5
    assert body["app_requirement_ids"] == [app.id]


def test_create_experiment_accepts_own_private_and_catalog_specs(auth_client):
    client, user = auth_client
    own = AppRequirementTemplateFactory(is_default=False, created_by=user)
    catalog = AppRequirementTemplateFactory(is_default=True)

    res = client.post(
        "/api/generation/experiments/",
        data=json.dumps({"name": "Mine", "slug": "mine", "app_requirement_ids": [own.id, catalog.id]}),
        content_type="application/json",
    )

    assert res.status_code == 200, res.content
    assert sorted(res.json()["app_requirement_ids"]) == sorted([own.id, catalog.id])


def test_create_experiment_refuses_another_users_private_spec(auth_client):
    client, user = auth_client
    catalog = AppRequirementTemplateFactory(is_default=True)
    hidden = AppRequirementTemplateFactory(is_default=False, created_by=UserFactory(), description="TOP SECRET SPEC")

    res = client.post(
        "/api/generation/experiments/",
        data=json.dumps({"name": "Peek", "slug": "peek", "app_requirement_ids": [catalog.id, hidden.id]}),
        content_type="application/json",
    )

    # 404 like an unknown id (not silently dropped), and no row is left behind.
    assert res.status_code == 404
    assert str(hidden.id) in res.json()["detail"]
    assert not Experiment.objects.filter(created_by=user, slug="peek").exists()


def test_update_experiment_refuses_another_users_private_spec(auth_client):
    client, user = auth_client
    catalog = AppRequirementTemplateFactory(is_default=True)
    exp = ExperimentFactory(created_by=user, repeats=2)
    exp.app_requirements.add(catalog)
    hidden = AppRequirementTemplateFactory(is_default=False, created_by=UserFactory())

    res = client.patch(
        f"/api/generation/experiments/{exp.id}/",
        data=json.dumps({"repeats": 9, "app_requirement_ids": [catalog.id, hidden.id]}),
        content_type="application/json",
    )

    assert res.status_code == 404
    exp.refresh_from_db()
    # Nothing in the PATCH was applied.
    assert exp.repeats == 2
    assert list(exp.app_requirements.values_list("id", flat=True)) == [catalog.id]


def test_create_experiment_rejects_duplicate_slug_for_same_owner(auth_client):
    client, user = auth_client
    ExperimentFactory(created_by=user, slug="dupe")

    res = client.post(
        "/api/generation/experiments/",
        data=json.dumps({"name": "X", "slug": "dupe"}),
        content_type="application/json",
    )

    assert res.status_code == 400


def test_list_experiments_is_scoped_to_owner(auth_client):
    client, user = auth_client
    ExperimentFactory(created_by=user)
    ExperimentFactory(created_by=UserFactory())

    res = client.get("/api/generation/experiments/")

    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert len(res.json()["items"]) == 1


def test_get_experiment_404s_for_non_owner(auth_client):
    client, _user = auth_client
    other = ExperimentFactory(created_by=UserFactory())

    res = client.get(f"/api/generation/experiments/{other.id}/")

    assert res.status_code == 404


def test_update_experiment_in_draft(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)

    res = client.patch(
        f"/api/generation/experiments/{exp.id}/",
        data=json.dumps({"repeats": 10}),
        content_type="application/json",
    )

    assert res.status_code == 200
    assert res.json()["repeats"] == 10


def test_update_experiment_rejects_non_draft(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user, status=Experiment.Status.RUNNING, repeats=3)

    # Design fields are frozen after launch; repeats may only grow.
    res = client.patch(
        f"/api/generation/experiments/{exp.id}/",
        data=json.dumps({"base_seed": 7}),
        content_type="application/json",
    )
    assert res.status_code == 400
    assert res.json()["code"] == "not_draft"

    res = client.patch(
        f"/api/generation/experiments/{exp.id}/",
        data=json.dumps({"repeats": 2}),
        content_type="application/json",
    )
    assert res.status_code == 400
    assert res.json()["code"] == "repeats_decrease"


def test_delete_experiment_in_draft(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)

    res = client.delete(f"/api/generation/experiments/{exp.id}/")

    assert res.status_code == 200
    assert not Experiment.objects.filter(id=exp.id).exists()


def test_archive_experiment(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)

    res = client.post(f"/api/generation/experiments/{exp.id}/archive/")

    assert res.status_code == 200
    assert res.json()["status"] == "archived"


def test_create_condition(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    bundle = _flask_bundle()
    model = LLMModelFactory()

    res = client.post(
        f"/api/generation/experiments/{exp.id}/conditions/",
        data=json.dumps({"profile_id": bundle.id, "model_id": model.id, "label": "gpt-4o / standard"}),
        content_type="application/json",
    )

    assert res.status_code == 200
    assert res.json()["label"] == "gpt-4o / standard"
    assert ExperimentCondition.objects.filter(experiment=exp).count() == 1


def test_create_condition_rejects_duplicate_bundle_model_pair(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    bundle = _flask_bundle()
    model = LLMModelFactory()
    ExperimentConditionFactory(experiment=exp, profile=bundle, model=model)

    res = client.post(
        f"/api/generation/experiments/{exp.id}/conditions/",
        data=json.dumps({"profile_id": bundle.id, "model_id": model.id}),
        content_type="application/json",
    )

    assert res.status_code == 400


def test_delete_condition(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    cond = ExperimentConditionFactory(experiment=exp, profile=_flask_bundle())

    res = client.delete(f"/api/generation/experiments/{exp.id}/conditions/{cond.id}/")

    assert res.status_code == 200
    assert not ExperimentCondition.objects.filter(id=cond.id).exists()


def test_preview_experiment(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user, repeats=2)
    exp.app_requirements.add(AppRequirementTemplateFactory())
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    res = client.post(f"/api/generation/experiments/{exp.id}/preview/")

    assert res.status_code == 200
    body = res.json()
    assert body["total_jobs"] == 2


def test_launch_requires_api_key(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    exp.app_requirements.add(AppRequirementTemplateFactory())
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=False):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 400
    assert res.json()["code"] == "missing_api_key"


def test_launch_requires_conditions(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    exp.app_requirements.add(AppRequirementTemplateFactory())

    with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 400
    assert "condition" in res.json()["detail"].lower()


def test_launch_requires_app_requirements(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    with mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 400
    assert "app spec" in res.json()["detail"].lower()


def test_launch_creates_jobs_and_dispatches(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user, repeats=1)
    exp.app_requirements.add(AppRequirementTemplateFactory())
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    with (
        mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True),
        mock.patch("backend.generation.services.experiments.enqueue_jobs") as dispatch,
    ):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 200
    body = res.json()
    assert body["batch"]["total_jobs"] == 1
    assert body["batch"]["kind"] == "launch"
    assert body["jobs_created"] == 1
    assert body["experiment"]["status"] == "running"
    assert _enqueued(dispatch) == 1
    assert GenerationJob.objects.filter(experiment=exp).count() == 1


def test_status_endpoint(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user, repeats=1)
    exp.app_requirements.add(AppRequirementTemplateFactory())
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    with (
        mock.patch("backend.credentials.services.resolver.has_resolvable_key", return_value=True),
        mock.patch("backend.generation.services.experiments.enqueue_jobs"),
    ):
        client.post(f"/api/generation/experiments/{exp.id}/launch/")

    res = client.get(f"/api/generation/experiments/{exp.id}/status/")

    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["pending"] == 1
    assert body["cells"]["total"] == 1
    assert body["conditions"][0]["counts"]["pending"] == 1


def test_export_endpoint(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)

    res = client.get(f"/api/generation/experiments/{exp.id}/export/")

    assert res.status_code == 200
    assert res.json()["experiment"]["slug"] == exp.slug
