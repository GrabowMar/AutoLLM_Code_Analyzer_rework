"""Tests for the export API."""

from __future__ import annotations

import csv
import io
import json
from http import HTTPStatus

import pytest

from backend.analysis.models import Finding
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.reports.models import Report
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def other_user():
    return UserFactory()


# ── Helper ────────────────────────────────────────────────────────────────────


def _parse_csv(content: str) -> tuple[list[str], list[list[str]]]:
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    return rows[0], rows[1:]


# ── Auth required ─────────────────────────────────────────────────────────────


class TestAuthRequired:
    def test_findings_csv_requires_auth(self, client):
        resp = client.get("/api/export/findings.csv")
        assert resp.status_code == HTTPStatus.UNAUTHORIZED

    def test_findings_json_requires_auth(self, client):
        resp = client.get("/api/export/findings.json")
        assert resp.status_code == HTTPStatus.UNAUTHORIZED

    def test_jobs_csv_requires_auth(self, client):
        resp = client.get("/api/export/generation-jobs.csv")
        assert resp.status_code == HTTPStatus.UNAUTHORIZED

    def test_analysis_tasks_csv_requires_auth(self, client):
        resp = client.get("/api/export/analysis-tasks.csv")
        assert resp.status_code == HTTPStatus.UNAUTHORIZED

    def test_reports_csv_requires_auth(self, client):
        resp = client.get("/api/export/reports.csv")
        assert resp.status_code == HTTPStatus.UNAUTHORIZED


# ── Findings CSV ──────────────────────────────────────────────────────────────


class TestFindingsCsv:
    def test_headers(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(run=task)
        FindingFactory(result=result)

        resp = auth_client.get("/api/export/findings.csv")
        assert resp.status_code == HTTPStatus.OK
        assert resp["Content-Type"] == "text/csv"
        headers, _ = _parse_csv(resp.content.decode())
        assert headers == [
            "id",
            "task_id",
            "analyzer",
            "severity",
            "file_path",
            "line",
            "rule_id",
            "message",
            "cwe",
            "created_at",
        ]

    def test_row_count(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(run=task)
        FindingFactory.create_batch(3, result=result)

        resp = auth_client.get("/api/export/findings.csv")
        _, rows = _parse_csv(resp.content.decode())
        assert len(rows) == 3

    def test_user_scoping(self, auth_client, user, other_user):
        """User should only see their own findings."""
        my_task = AnalysisRunFactory(created_by=user)
        my_result = ToolResultFactory(run=my_task)
        FindingFactory.create_batch(2, result=my_result)

        other_task = AnalysisRunFactory(created_by=other_user)
        other_result = ToolResultFactory(run=other_task)
        FindingFactory.create_batch(5, result=other_result)

        resp = auth_client.get("/api/export/findings.csv")
        _, rows = _parse_csv(resp.content.decode())
        assert len(rows) == 2

    def test_filter_by_severity(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(run=task)
        FindingFactory(result=result, severity=Finding.Severity.HIGH)
        FindingFactory(result=result, severity=Finding.Severity.LOW)

        resp = auth_client.get("/api/export/findings.csv?severity=high")
        _, rows = _parse_csv(resp.content.decode())
        assert len(rows) == 1
        assert rows[0][3] == "high"

    def test_filter_by_task_id(self, auth_client, user):
        task1 = AnalysisRunFactory(created_by=user)
        result1 = ToolResultFactory(run=task1)
        FindingFactory.create_batch(2, result=result1)

        task2 = AnalysisRunFactory(created_by=user)
        result2 = ToolResultFactory(run=task2)
        FindingFactory.create_batch(3, result=result2)

        resp = auth_client.get(f"/api/export/findings.csv?task_id={task1.id}")
        _, rows = _parse_csv(resp.content.decode())
        assert len(rows) == 2


# ── Findings JSON ─────────────────────────────────────────────────────────────


class TestFindingsJson:
    def test_structure(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(run=task)
        FindingFactory(
            result=result,
            rule_id="B101",
            file_path="app.py",
            line_number=10,
        )

        resp = auth_client.get("/api/export/findings.json")
        assert resp.status_code == HTTPStatus.OK
        data = json.loads(resp.content)
        assert isinstance(data, list)
        assert len(data) == 1
        item = data[0]
        assert "id" in item
        assert "task_id" in item
        assert "analyzer" in item
        assert item["rule_id"] == "B101"
        assert item["file_path"] == "app.py"
        assert item["line"] == 10

    def test_limit_cap(self, auth_client, user):
        """limit param cannot exceed 50000 hard cap."""
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(run=task)
        FindingFactory.create_batch(5, result=result)

        resp = auth_client.get("/api/export/findings.json?limit=100000")
        assert resp.status_code == HTTPStatus.OK
        data = json.loads(resp.content)
        assert len(data) == 5


# ── Findings SARIF ────────────────────────────────────────────────────────────


class TestFindingsSarif:
    def test_schema_structure(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(run=task, tool_slug="bandit")
        FindingFactory(
            result=result,
            severity=Finding.Severity.HIGH,
            rule_id="B105",
            file_path="app.py",
            line_number=42,
        )

        resp = auth_client.get("/api/export/findings.sarif")
        assert resp.status_code == HTTPStatus.OK
        data = json.loads(resp.content)

        assert data["version"] == "2.1.0"
        assert "runs" in data
        assert len(data["runs"]) == 1
        run = data["runs"][0]
        assert run["tool"]["driver"]["name"] == "bandit"
        assert len(run["results"]) == 1
        result_entry = run["results"][0]
        assert result_entry["ruleId"] == "B105"
        assert result_entry["level"] == "error"  # HIGH → error
        # Original 5-bucket severity survives the level collapse.
        assert result_entry["properties"]["severity"] == "high"
        assert "text" in result_entry["message"]
        loc = result_entry["locations"][0]
        assert loc["physicalLocation"]["artifactLocation"]["uri"] == "app.py"
        assert loc["physicalLocation"]["region"]["startLine"] == 42

    def test_sarif_groups_by_analyzer(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result_bandit = ToolResultFactory(run=task, tool_slug="bandit")
        result_eslint = ToolResultFactory(run=task, tool_slug="eslint")
        FindingFactory.create_batch(2, result=result_bandit)
        FindingFactory.create_batch(3, result=result_eslint)

        resp = auth_client.get("/api/export/findings.sarif")
        data = json.loads(resp.content)
        assert len(data["runs"]) == 2
        names = {run["tool"]["driver"]["name"] for run in data["runs"]}
        assert names == {"bandit", "eslint"}


# ── Generation jobs ───────────────────────────────────────────────────────────


class TestGenerationJobsExport:
    def test_csv_headers(self, auth_client, user):
        GenerationJobFactory(created_by=user)
        resp = auth_client.get("/api/export/generation-jobs.csv")
        assert resp.status_code == HTTPStatus.OK
        headers, rows = _parse_csv(resp.content.decode())
        assert "id" in headers
        assert "status" in headers
        assert len(rows) == 1

    def test_json_structure(self, auth_client, user):
        GenerationJobFactory(created_by=user, status="completed")
        resp = auth_client.get("/api/export/generation-jobs.json")
        data = json.loads(resp.content)
        assert isinstance(data, list)
        assert data[0]["status"] == "completed"

    def test_filter_by_status(self, auth_client, user):
        GenerationJobFactory(created_by=user, status="completed")
        GenerationJobFactory(created_by=user, status="pending")
        resp = auth_client.get("/api/export/generation-jobs.csv?status=completed")
        _, rows = _parse_csv(resp.content.decode())
        assert len(rows) == 1

    def test_user_scoping(self, auth_client, user, other_user):
        GenerationJobFactory(created_by=user)
        GenerationJobFactory(created_by=other_user)
        resp = auth_client.get("/api/export/generation-jobs.json")
        data = json.loads(resp.content)
        assert len(data) == 1


# ── Analysis tasks ────────────────────────────────────────────────────────────


class TestAnalysisTasksExport:
    def test_csv_headers(self, auth_client, user):
        AnalysisRunFactory(created_by=user)
        resp = auth_client.get("/api/export/analysis-tasks.csv")
        assert resp.status_code == HTTPStatus.OK
        headers, rows = _parse_csv(resp.content.decode())
        assert "id" in headers
        assert "name" in headers
        assert "status" in headers
        assert len(rows) == 1

    def test_json_structure(self, auth_client, user):
        AnalysisRunFactory(created_by=user)
        resp = auth_client.get("/api/export/analysis-tasks.json")
        data = json.loads(resp.content)
        assert isinstance(data, list)
        assert "severity_counts" in data[0]
        assert "created" in data[0]

    def test_json_includes_tool_metrics(self, auth_client, user):
        AnalysisRunFactory(
            created_by=user,
            summary={"metrics_by_tool": {"radon": {"average_complexity": 4.2}}},
        )
        resp = auth_client.get("/api/export/analysis-tasks.json")
        data = json.loads(resp.content)
        assert data[0]["metrics_by_tool"] == {"radon": {"average_complexity": 4.2}}


# ── Reports ───────────────────────────────────────────────────────────────────


class TestReportsExport:
    def _make_report(self, user):
        return Report.objects.create(
            title="Test Report",
            report_type=Report.Type.MODEL_ANALYSIS,
            status=Report.Status.COMPLETED,
            created_by=user,
        )

    def test_csv_headers(self, auth_client, user):
        self._make_report(user)
        resp = auth_client.get("/api/export/reports.csv")
        assert resp.status_code == HTTPStatus.OK
        headers, rows = _parse_csv(resp.content.decode())
        assert headers == ["id", "title", "type", "status", "created"]
        assert len(rows) == 1

    def test_json_structure(self, auth_client, user):
        self._make_report(user)
        resp = auth_client.get("/api/export/reports.json")
        data = json.loads(resp.content)
        assert isinstance(data, list)
        assert data[0]["title"] == "Test Report"
        assert data[0]["type"] == "model_analysis"

    def test_user_scoping(self, auth_client, user, other_user):
        self._make_report(user)
        self._make_report(other_user)
        resp = auth_client.get("/api/export/reports.json")
        data = json.loads(resp.content)
        assert len(data) == 1
