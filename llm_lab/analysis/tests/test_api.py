from __future__ import annotations

import json
from http import HTTPStatus
from unittest.mock import patch

import pytest

from llm_lab.analysis.models import AnalysisTask
from llm_lab.analysis.models import Finding
from llm_lab.analysis.tests.factories import AnalysisResultFactory
from llm_lab.analysis.tests.factories import AnalysisTaskFactory
from llm_lab.analysis.tests.factories import FindingFactory
from llm_lab.generation.tests.factories import GenerationJobFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client(client, user):
    """Authenticated test client."""
    client.force_login(user)
    return client


@pytest.fixture
def analysis_task(user):
    """Create an analysis task owned by the test user."""
    return AnalysisTaskFactory(created_by=user)


# ── Create Task ───────────────────────────────────────────────────────


class TestCreateTask:
    @patch("llm_lab.analysis.api.views._dispatch_task")
    def test_create_with_source_code(self, mock_dispatch, api_client):
        payload = {
            "name": "Test Analysis",
            "source_code": {"backend": "print('hello')", "frontend": ""},
            "analyzers": ["bandit"],
            "auto_start": False,
        }
        resp = api_client.post(
            "/api/analysis/tasks/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["name"] == "Test Analysis"
        assert data["status"] == "pending"
        mock_dispatch.assert_not_called()

    @patch("llm_lab.analysis.api.views._dispatch_task")
    def test_create_with_generation_job(self, mock_dispatch, api_client, user):
        job = GenerationJobFactory(created_by=user)
        payload = {
            "generation_job_id": str(job.id),
            "analyzers": ["bandit", "eslint"],
            "auto_start": False,
        }
        resp = api_client.post(
            "/api/analysis/tasks/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert str(data["generation_job_id"]) == str(job.id)

    def test_create_requires_auth(self, client):
        payload = {
            "source_code": {"backend": "print('hello')"},
            "analyzers": ["bandit"],
        }
        resp = client.post(
            "/api/analysis/tasks/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == HTTPStatus.UNAUTHORIZED


# ── List Tasks ────────────────────────────────────────────────────────


class TestListTasks:
    def test_list_empty(self, api_client):
        resp = api_client.get("/api/analysis/tasks/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_with_tasks(self, api_client, user):
        batch_size = 3
        AnalysisTaskFactory.create_batch(batch_size, created_by=user)
        resp = api_client.get("/api/analysis/tasks/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["total"] == batch_size
        assert len(data["items"]) == batch_size

    def test_filter_by_status(self, api_client, user):
        AnalysisTaskFactory(created_by=user, status=AnalysisTask.Status.PENDING)
        AnalysisTaskFactory(created_by=user, status=AnalysisTask.Status.COMPLETED)
        AnalysisTaskFactory(created_by=user, status=AnalysisTask.Status.COMPLETED)

        resp = api_client.get("/api/analysis/tasks/?status=completed")
        data = resp.json()
        expected_completed = 2
        assert data["total"] == expected_completed

    def test_pagination(self, api_client, user):
        total_tasks = 5
        page_size = 2
        expected_pages = 3
        AnalysisTaskFactory.create_batch(total_tasks, created_by=user)
        resp = api_client.get("/api/analysis/tasks/?per_page=2&page=1")
        data = resp.json()
        assert data["total"] == total_tasks
        assert len(data["items"]) == page_size
        assert data["pages"] == expected_pages


# ── Get Task ──────────────────────────────────────────────────────────


class TestGetTask:
    def test_get_task(self, api_client, analysis_task):
        resp = api_client.get(f"/api/analysis/tasks/{analysis_task.id}/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["id"] == str(analysis_task.id)
        assert data["status"] == "pending"

    def test_not_found(self, api_client):
        resp = api_client.get(
            "/api/analysis/tasks/00000000-0000-0000-0000-000000000000/",
        )
        assert resp.status_code == HTTPStatus.NOT_FOUND


# ── Cancel Task ───────────────────────────────────────────────────────


class TestCancelTask:
    def test_cancel_pending(self, api_client, analysis_task):
        resp = api_client.post(
            f"/api/analysis/tasks/{analysis_task.id}/cancel/",
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "cancelled"

        analysis_task.refresh_from_db()
        assert analysis_task.status == AnalysisTask.Status.CANCELLED

    def test_cancel_completed(self, api_client, user):
        task = AnalysisTaskFactory(
            created_by=user,
            status=AnalysisTask.Status.COMPLETED,
        )
        resp = api_client.post(f"/api/analysis/tasks/{task.id}/cancel/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["success"] is False
        assert data["message"] == "Task cannot be cancelled"


# ── Delete Task ───────────────────────────────────────────────────────


class TestDeleteTask:
    def test_delete(self, api_client, analysis_task):
        task_id = analysis_task.id
        resp = api_client.delete(f"/api/analysis/tasks/{task_id}/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["success"] is True
        assert not AnalysisTask.objects.filter(id=task_id).exists()


# ── List Results ──────────────────────────────────────────────────────


class TestListResults:
    def test_list_results(self, api_client, analysis_task):
        AnalysisResultFactory(task=analysis_task, analyzer_name="bandit")
        AnalysisResultFactory(task=analysis_task, analyzer_name="eslint")

        resp = api_client.get(
            f"/api/analysis/tasks/{analysis_task.id}/results/",
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        expected_results = 2
        assert len(data) == expected_results


# ── List Findings ─────────────────────────────────────────────────────


class TestListFindings:
    def test_list_findings(self, api_client, analysis_task):
        result = AnalysisResultFactory(
            task=analysis_task,
            analyzer_name="bandit",
        )
        FindingFactory(result=result, severity=Finding.Severity.HIGH)
        FindingFactory(result=result, severity=Finding.Severity.LOW)

        resp = api_client.get(
            f"/api/analysis/tasks/{analysis_task.id}/findings/",
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        expected_findings = 2
        assert data["total"] == expected_findings

    def test_filter_by_severity(self, api_client, analysis_task):
        result = AnalysisResultFactory(
            task=analysis_task,
            analyzer_name="bandit",
        )
        FindingFactory(result=result, severity=Finding.Severity.HIGH)
        FindingFactory(result=result, severity=Finding.Severity.LOW)
        FindingFactory(result=result, severity=Finding.Severity.HIGH)

        resp = api_client.get(
            f"/api/analysis/tasks/{analysis_task.id}/findings/?severity=high",
        )
        data = resp.json()
        expected_high = 2
        assert data["total"] == expected_high


# ── List Analyzers ────────────────────────────────────────────────────


class TestGetSingleResult:
    def test_get_single_result(self, api_client, analysis_task):
        result = AnalysisResultFactory(task=analysis_task, analyzer_name="bandit")
        resp = api_client.get(
            f"/api/analysis/tasks/{analysis_task.id}/results/{result.id}/",
        )
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["analyzer_name"] == "bandit"


class TestCreateTaskUnknownAnalyzer:
    @patch("llm_lab.analysis.api.views._dispatch_task")
    def test_create_task_unknown_analyzer(self, mock_dispatch, api_client):
        payload = {
            "name": "Bad Analysis",
            "source_code": {"backend": "print('hi')"},
            "analyzers": ["totally_fake_analyzer"],
            "auto_start": False,
        }
        resp = api_client.post(
            "/api/analysis/tasks/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == HTTPStatus.BAD_REQUEST
        data = resp.json()
        assert "Unknown analyzers" in data["detail"]
        mock_dispatch.assert_not_called()


class TestListAnalyzers:
    def test_list_analyzers(self, api_client):
        resp = api_client.get("/api/analysis/analyzers/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert isinstance(data, list)
        names = [a["name"] for a in data]
        assert "bandit" in names


# ── Stats ─────────────────────────────────────────────────────────────


class TestGetStats:
    def test_stats_empty(self, api_client):
        resp = api_client.get("/api/analysis/stats/")
        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["total_tasks"] == 0
        assert data["total_findings"] == 0

    def test_stats_with_data(self, api_client, user):
        task1 = AnalysisTaskFactory(
            created_by=user,
            status=AnalysisTask.Status.COMPLETED,
        )
        task2 = AnalysisTaskFactory(
            created_by=user,
            status=AnalysisTask.Status.FAILED,
        )
        result1 = AnalysisResultFactory(task=task1, analyzer_name="bandit")
        FindingFactory(result=result1, severity=Finding.Severity.HIGH)
        FindingFactory(result=result1, severity=Finding.Severity.HIGH)
        FindingFactory(result=result1, severity=Finding.Severity.LOW)

        # task2 has no findings (it just exists as a task)
        _ = task2

        resp = api_client.get("/api/analysis/stats/")
        data = resp.json()
        expected_tasks = 2
        expected_findings = 3
        assert data["total_tasks"] == expected_tasks
        assert data["completed_tasks"] == 1
        assert data["failed_tasks"] == 1
        assert data["total_findings"] == expected_findings
        expected_high = 2
        assert data["findings_by_severity"].get("high") == expected_high
        assert data["findings_by_severity"].get("low") == 1
