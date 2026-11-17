"""Deployment filters and feature scans use current evidence without changing samples."""

from types import SimpleNamespace

import pytest
from django.test import Client

from backend.generation.runs.application_checks import feature_check_summary
from backend.generation.testing.factories import EvalSuiteFactory
from backend.generation.testing.factories import GenerationRunFactory
from backend.generation.testing.factories import RunEvaluationFactory
from backend.generation.testing.factories import RunManifestFactory
from backend.runtime.models import ContainerInstance
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
BASE = "/api/generation/runs"


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def client(user):
    client = Client()
    client.force_login(user)
    return client


def ids(client, **params):
    response = client.get(f"{BASE}/", params)
    assert response.status_code == 200, response.content
    return {row["id"] for row in response.json()["items"]}


def test_deployment_and_generation_status_are_distinct(client, user):
    started = GenerationRunFactory(tested=True, created_by=user)
    failed = GenerationRunFactory(created_by=user, status="completed", outcome="build_fail")
    unknown = GenerationRunFactory(created_by=user, status="running", outcome="")
    GenerationRunFactory(tested=True)  # invisible to caller
    assert ids(client, deployment="started") == {str(started.pk)}
    assert ids(client, deployment="failed") == {str(failed.pk)}
    assert ids(client, deployment="unknown") == {str(unknown.pk)}
    assert ids(client, deployment="started", status="running") == set()
    assert client.get(f"{BASE}/", {"deployment": "bogus"}).status_code == 422


def test_untested_outcome_requires_recorded_successful_start(client, user):
    run = GenerationRunFactory(tested=True, created_by=user, outcome_reason="not_tested:timeout")
    assert ids(client, deployment="started") == set()
    RunEvaluationFactory(run=run, status="not_measured", stages={"start": {"status": "ok"}})
    assert ids(client, deployment="started") == {str(run.pk)}


def test_current_final_suite_upgrade_drives_filter_scan_and_detail(client, user):
    run = GenerationRunFactory(tested=True, created_by=user)
    RunManifestFactory(run=run)
    old = RunEvaluationFactory(run=run, measured=True, stages={"start": {"status": "fail"}})
    current = RunEvaluationFactory(run=run, measured=True, eval_suite=EvalSuiteFactory())
    RunEvaluationFactory(run=run, status="pending")
    RunEvaluationFactory(run=run, measured=True, retest_of=current, stages={"start": {"status": "fail"}})
    assert current.pk > old.pk
    assert ids(client, deployment="started") == {str(run.pk)}
    scan = client.get(f"{BASE}/feature-scan/").json()["items"][0]
    assert scan["evaluation_id"] == current.pk
    assert scan["eval_suite"] == current.eval_suite.ref
    detail = client.get(f"{BASE}/{run.pk}/").json()
    assert detail["latest_evaluation"]["id"] == current.pk
    assert detail["evaluation_in_progress"] is True


def test_current_known_start_failure_overrides_historical_tested_outcome(client, user):
    run = GenerationRunFactory(tested=True, created_by=user)
    RunEvaluationFactory(run=run, measured=True, stages={"build": {"status": "ok"}, "start": {"status": "fail"}})
    assert ids(client, deployment="started") == set()
    assert ids(client, deployment="failed") == {str(run.pk)}


def test_preview_running_is_not_generation_running_and_does_not_duplicate_rows(client, user):
    started = GenerationRunFactory(tested=True, created_by=user)
    generating = GenerationRunFactory(created_by=user, status="running", outcome="")
    for index in range(2):
        ContainerInstance.objects.create(generation_job=started, name=f"preview-{index}", status="running")
    ContainerInstance.objects.create(generation_job=generating, name="stopped", status="stopped")
    assert ids(client, preview_running=True) == {str(started.pk)}
    assert ids(client, preview_running=False) == {str(generating.pk)}
    listing = client.get(f"{BASE}/", {"preview_running": True}).json()
    assert listing["total"] == 1


def test_deployment_facet_ignores_only_its_own_filter(client, user):
    GenerationRunFactory(tested=True, created_by=user)
    GenerationRunFactory(created_by=user, status="completed", outcome="start_fail")
    data = client.get(f"{BASE}/facets/", {"deployment": "started"}).json()
    assert data["summary"]["total"] == 1
    assert {row["value"]: row["count"] for row in data["deployments"]} == {"started": 1, "failed": 1, "unknown": 0}


def test_scan_is_visible_paginated_and_read_only(client, user):
    runs = [GenerationRunFactory(tested=True, created_by=user) for _ in range(3)]
    GenerationRunFactory(tested=True)
    data = client.get(f"{BASE}/feature-scan/", {"page": 2, "page_size": 2}).json()
    assert (data["total"], data["page"], len(data["items"])) == (3, 2, 1)
    assert data["items"][0]["run_id"] in {str(run.pk) for run in runs}
    assert data["items"][0]["measured"] is False
    assert all(run.evaluations.count() == 0 for run in runs)
    assert client.get(f"{BASE}/feature-scan/", {"sort": "bogus"}).status_code == 400


def test_failed_skipped_flaky_and_uncovered_requirements_remain_distinct():
    task = {
        "endpoints": [{"id": "items.list", "method": "GET", "path": "/items"}, {"id": "items.create"}],
        "ui": [{"id": "ui.list", "text": "An interactive list", "endpoints": ["items.list"]}],
        "acceptance": [{"id": "accept.items"}],
    }
    evaluation = SimpleNamespace(
        pk=10,
        status="measured",
        eval_suite=SimpleNamespace(ref="suite@2", content={"flaky_checks": ["flaky"]}),
        result={
            "measured": True,
            "checks": [
                {"id": "list.schema", "category": "functional", "endpoint": "items.list", "status": "fail"},
                {"id": "create", "category": "functional", "endpoint": "items.create", "status": "skipped"},
                {"id": "accept.items", "category": "functional", "endpoint": "items.list", "status": "pass"},
                {"id": "flaky", "category": "functional", "endpoint": "items.create", "status": "fail"},
                {"id": "index", "category": "infra", "status": "pass"},
            ],
        },
    )
    summary = feature_check_summary(task, evaluation)
    assert (summary["checks"], summary["passed"], summary["failed"], summary["skipped"]) == (3, 1, 1, 1)
    assert [row["id"] for row in summary["failed_checks"]] == ["list.schema"]
    assert {row["id"] for row in summary["uncovered_requirements"]} == {"items.create", "ui.list"}
    assert summary["excluded_flaky_checks"] == ["flaky"]
    evaluation.status = "not_measured"
    summary = feature_check_summary(task, evaluation)
    assert summary["checks"] == 0
    assert not summary["measured"]
    assert len(summary["uncovered_requirements"]) == 4

    evaluation.status = "measured"
    evaluation.result = None
    assert feature_check_summary(task, evaluation)["measured"] is False
