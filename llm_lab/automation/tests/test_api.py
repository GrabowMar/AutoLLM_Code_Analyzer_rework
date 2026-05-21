"""API tests for automation endpoints."""

from __future__ import annotations

import json

import pytest
from django.test import Client

from llm_lab.automation.models import Batch
from llm_lab.automation.models import Pipeline
from llm_lab.automation.models import PipelineRun
from llm_lab.automation.tests.factories import BatchFactory
from llm_lab.automation.tests.factories import PipelineFactory
from llm_lab.automation.tests.factories import PipelineRunFactory
from llm_lab.automation.tests.factories import ScheduleFactory
from llm_lab.users.tests.factories import UserFactory

BASE = "/api/automation"


def _auth_client(user) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _csrfless(
    client: Client,
    method: str,
    path: str,
    data: dict | None = None,
) -> object:
    kwargs: dict = {"content_type": "application/json"}
    if data is not None:
        kwargs["data"] = json.dumps(data)
    return getattr(client, method)(path, **kwargs)


@pytest.mark.django_db
def test_pipelines_list_requires_auth() -> None:
    client = Client()
    res = client.get(f"{BASE}/pipelines/")
    assert res.status_code == 401


@pytest.mark.django_db
def test_list_pipelines_empty() -> None:
    user = UserFactory.create()
    client = _auth_client(user)
    res = client.get(f"{BASE}/pipelines/")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert data["total"] == 0


@pytest.mark.django_db
def test_create_pipeline_valid() -> None:
    user = UserFactory.create()
    client = _auth_client(user)
    payload = {
        "name": "My Pipeline",
        "description": "Test",
        "config": {
            "steps": [
                {
                    "id": "s1",
                    "name": "Gen",
                    "kind": "generate",
                    "config": {"model_id": "gpt-4", "template_slug": "todo"},
                    "depends_on": [],
                },
            ],
        },
        "tags": ["ci"],
    }
    res = _csrfless(client, "post", f"{BASE}/pipelines/", payload)
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "My Pipeline"
    assert Pipeline.objects.filter(name="My Pipeline").exists()


@pytest.mark.django_db
def test_create_pipeline_invalid_dsl() -> None:
    user = UserFactory.create()
    client = _auth_client(user)
    payload = {
        "name": "Bad Pipeline",
        "config": {
            "steps": [
                {
                    "id": "s1",
                    "name": "Gen",
                    "kind": "generate",
                    "config": {},
                    "depends_on": [],
                },
            ],
        },
    }
    res = _csrfless(client, "post", f"{BASE}/pipelines/", payload)
    assert res.status_code == 400
    data = res.json()
    assert "errors" in data


@pytest.mark.django_db
def test_get_pipeline() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = client.get(f"{BASE}/pipelines/{pipeline.id}/")
    assert res.status_code == 200
    assert res.json()["id"] == str(pipeline.id)


@pytest.mark.django_db
def test_update_pipeline() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(
        client,
        "put",
        f"{BASE}/pipelines/{pipeline.id}/",
        {"name": "Updated"},
    )
    assert res.status_code == 200
    pipeline.refresh_from_db()
    assert pipeline.name == "Updated"


@pytest.mark.django_db
def test_delete_pipeline() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(client, "delete", f"{BASE}/pipelines/{pipeline.id}/")
    assert res.status_code == 204
    assert not Pipeline.objects.filter(id=pipeline.id).exists()


@pytest.mark.django_db
def test_clone_pipeline() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(
        client,
        "post",
        f"{BASE}/pipelines/{pipeline.id}/clone/",
        {"new_name": "Clone"},
    )
    assert res.status_code == 201
    assert Pipeline.objects.filter(name="Clone").exists()


@pytest.mark.django_db
def test_trigger_run() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(
        client,
        "post",
        f"{BASE}/pipelines/{pipeline.id}/runs/",
        {"params": {}},
    )
    assert res.status_code == 202
    assert PipelineRun.objects.filter(pipeline=pipeline).exists()


@pytest.mark.django_db
def test_list_pipeline_runs() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    PipelineRunFactory.create(pipeline=pipeline)
    client = _auth_client(user)
    res = client.get(f"{BASE}/pipelines/{pipeline.id}/runs/")
    assert res.status_code == 200
    assert res.json()["total"] == 1


@pytest.mark.django_db
def test_get_run() -> None:
    user = UserFactory.create()
    run = PipelineRunFactory.create(triggered_by=user)
    client = _auth_client(user)
    res = client.get(f"{BASE}/runs/{run.id}/")
    assert res.status_code == 200
    assert res.json()["id"] == str(run.id)


@pytest.mark.django_db
def test_cancel_run() -> None:
    user = UserFactory.create()
    run = PipelineRunFactory.create(triggered_by=user, status="running")
    client = _auth_client(user)
    res = _csrfless(client, "post", f"{BASE}/runs/{run.id}/cancel/")
    assert res.status_code == 200
    run.refresh_from_db()
    assert run.status == "cancelled"


@pytest.mark.django_db
def test_pagination() -> None:
    user = UserFactory.create()
    for i in range(5):
        PipelineFactory.create(owner=user, name=f"P{i}")
    client = _auth_client(user)
    res = client.get(f"{BASE}/pipelines/?per_page=2&page=1")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 2
    assert data["total"] == 5
    assert data["pages"] == 3


@pytest.mark.django_db
def test_create_batch() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(
        client,
        "post",
        f"{BASE}/batches/",
        {
            "pipeline_id": str(pipeline.id),
            "name": "Batch 1",
            "matrix": {"models": ["gpt-4"], "templates": ["todo"]},
        },
    )
    assert res.status_code == 201
    assert Batch.objects.filter(name="Batch 1").exists()


@pytest.mark.django_db
def test_list_batches() -> None:
    user = UserFactory.create()
    BatchFactory.create(owner=user)
    client = _auth_client(user)
    res = client.get(f"{BASE}/batches/")
    assert res.status_code == 200
    assert res.json()["total"] == 1


@pytest.mark.django_db
def test_create_schedule_valid() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(
        client,
        "post",
        f"{BASE}/schedules/",
        {"pipeline_id": str(pipeline.id), "cron_expression": "0 * * * *"},
    )
    assert res.status_code == 201


@pytest.mark.django_db
def test_create_schedule_invalid_cron() -> None:
    user = UserFactory.create()
    pipeline = PipelineFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(
        client,
        "post",
        f"{BASE}/schedules/",
        {"pipeline_id": str(pipeline.id), "cron_expression": "not-a-cron"},
    )
    assert res.status_code == 400


@pytest.mark.django_db
def test_toggle_schedule() -> None:
    user = UserFactory.create()
    sched = ScheduleFactory.create(owner=user, enabled=True)
    client = _auth_client(user)
    res = _csrfless(
        client,
        "patch",
        f"{BASE}/schedules/{sched.id}/enabled/?enabled=false",
    )
    assert res.status_code == 200
    sched.refresh_from_db()
    assert not sched.enabled


@pytest.mark.django_db
def test_delete_schedule() -> None:
    user = UserFactory.create()
    sched = ScheduleFactory.create(owner=user)
    client = _auth_client(user)
    res = _csrfless(client, "delete", f"{BASE}/schedules/{sched.id}/")
    assert res.status_code == 204
