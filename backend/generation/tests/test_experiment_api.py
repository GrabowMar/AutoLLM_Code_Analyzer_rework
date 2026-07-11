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


def _flask_bundle() -> GenerationProfile:
    return GenerationProfile.objects.filter(slug="system-scaffolding-standard").order_by("-version").first()


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def test_create_experiment(auth_client):
    client, _user = auth_client
    app = AppRequirementTemplateFactory()

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
    assert len(res.json()) == 1


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
    exp = ExperimentFactory(created_by=user, status=Experiment.Status.RUNNING)

    res = client.patch(
        f"/api/generation/experiments/{exp.id}/",
        data=json.dumps({"repeats": 10}),
        content_type="application/json",
    )

    assert res.status_code == 400


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

    with mock.patch("backend.generation.api.views.experiments.has_resolvable_key", return_value=False):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 400
    assert res.json()["code"] == "missing_api_key"


def test_launch_requires_conditions(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    exp.app_requirements.add(AppRequirementTemplateFactory())

    with mock.patch("backend.generation.api.views.experiments.has_resolvable_key", return_value=True):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 400
    assert "condition" in res.json()["detail"].lower()


def test_launch_requires_app_requirements(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    with mock.patch("backend.generation.api.views.experiments.has_resolvable_key", return_value=True):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 400
    assert "app requirement" in res.json()["detail"].lower()


def test_launch_creates_jobs_and_dispatches(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user, repeats=1)
    exp.app_requirements.add(AppRequirementTemplateFactory())
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    with (
        mock.patch("backend.generation.api.views.experiments.has_resolvable_key", return_value=True),
        mock.patch("backend.generation.services.experiments.dispatch_job") as dispatch,
    ):
        res = client.post(f"/api/generation/experiments/{exp.id}/launch/")

    assert res.status_code == 200
    body = res.json()
    assert body["total_jobs"] == 1
    assert dispatch.call_count == 1
    assert GenerationJob.objects.filter(experiment=exp).count() == 1


def test_status_endpoint(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user, repeats=1)
    exp.app_requirements.add(AppRequirementTemplateFactory())
    ExperimentConditionFactory(experiment=exp, profile=_flask_bundle(), model=LLMModelFactory())

    with (
        mock.patch("backend.generation.api.views.experiments.has_resolvable_key", return_value=True),
        mock.patch("backend.generation.services.experiments.dispatch_job"),
    ):
        client.post(f"/api/generation/experiments/{exp.id}/launch/")

    res = client.get(f"/api/generation/experiments/{exp.id}/status/")

    assert res.status_code == 200
    assert res.json()["jobs_created"] == 1


def test_export_endpoint(auth_client):
    client, user = auth_client
    exp = ExperimentFactory(created_by=user)

    res = client.get(f"/api/generation/experiments/{exp.id}/export/")

    assert res.status_code == 200
    assert res.json()["experiment"]["slug"] == exp.slug
