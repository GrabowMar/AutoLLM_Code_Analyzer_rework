"""Tests for research-experiment support: repeated trials and cost capture."""

from __future__ import annotations

from unittest import mock

import pytest

from backend.generation.models import GenerationBatch
from backend.generation.models import GenerationJob
from backend.generation.services.openrouter_client import OpenRouterClient
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def auth_client(client):
    user = UserFactory()
    client.force_login(user)
    return client, user


def _create_scaffolding_batch(client, *, trials: int | None = None, **overrides):
    app_req = AppRequirementTemplateFactory()
    models = [LLMModelFactory(), LLMModelFactory()]
    payload = {
        "stack_slug": "flask-react",
        "app_requirement_ids": [app_req.id],
        "model_ids": [m.id for m in models],
        **overrides,
    }
    if trials is not None:
        payload["trials"] = trials
    with (
        mock.patch("backend.generation.api.views.custom.dispatch_job"),
        mock.patch(
            "backend.generation.api.views.custom.has_resolvable_key",
            return_value=True,
        ),
    ):
        return client.post(
            "/api/generation/jobs/scaffolding/",
            payload,
            content_type="application/json",
        )


def test_scaffolding_trials_creates_repeated_jobs(auth_client):
    client, _user = auth_client
    res = _create_scaffolding_batch(client, trials=3)
    assert res.status_code == 200
    body = res.json()
    assert body["job_count"] == 6  # 1 template × 2 models × 3 trials

    batch = GenerationBatch.objects.get(id=body["batch_id"])
    assert batch.total_jobs == 6
    # Each model got exactly `trials` independent jobs.
    per_model = GenerationJob.objects.filter(batch=batch).values_list("model_id", flat=True)
    assert len(per_model) == 6
    assert len(set(per_model)) == 2


def test_scaffolding_trials_defaults_to_one(auth_client):
    client, _ = auth_client
    res = _create_scaffolding_batch(client)
    assert res.status_code == 200
    assert res.json()["job_count"] == 2


def test_scaffolding_trials_rejects_out_of_range(auth_client):
    client, _ = auth_client
    assert _create_scaffolding_batch(client, trials=0).status_code == 422
    assert _create_scaffolding_batch(client, trials=11).status_code == 422


def test_extract_usage_includes_cost():
    response = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "cost": 0.01291,
        },
    }
    usage = OpenRouterClient.extract_usage(response)
    assert usage["cost"] == 0.01291
    assert usage["total_tokens"] == 150


def test_extract_usage_cost_defaults_to_zero():
    assert OpenRouterClient.extract_usage({"usage": {}})["cost"] == 0.0
    assert OpenRouterClient.extract_usage({})["cost"] == 0.0
