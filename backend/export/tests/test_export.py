"""Tests for the export API."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
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
        # Legacy columns keep names and order (researchers' scripts); new ones are appended.
        assert headers[:10] == [
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
        for column in (
            "job_id",
            "model_id",
            "experiment_id",
            "condition_id",
            "run_status",
            "tool_version",
            "file_origin",
            "is_ai",
            "counted",
            "fingerprint",
            "issue_key",
            "plan_hash",
        ):
            assert column in headers

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


# ── Findings: research context, order, truncation ────────────────────────────


class TestFindingsResearchColumns:
    def test_row_carries_job_run_and_tool_context(self, auth_client, user):
        from backend.llm_models.tests.factories import LLMModelFactory
        from backend.rankings.tests.measured import make_run

        job = GenerationJobFactory(created_by=user, status="completed", model=LLMModelFactory(model_id="m/x"))
        run = make_run(job)
        FindingFactory(
            result=run.results.get(tool_slug="bandit"),
            cwe=["CWE-78", "CWE-89"],
            file_origin="added",
            fingerprint="f" * 64,
            issue_key="i" * 64,
        )

        resp = auth_client.get("/api/export/findings.csv")
        headers, rows = _parse_csv(resp.content.decode())
        row = dict(zip(headers, rows[0], strict=True))
        assert row["task_id"] == str(run.id)
        assert row["job_id"] == str(job.id)
        assert row["model_id"] == "m/x"
        assert row["cwe"] == "CWE-78;CWE-89"
        assert row["tool_version"] == "1.2.3"
        assert row["run_status"] == "completed"
        assert row["run_complete"] == "true"
        assert row["file_origin"] == "added"
        assert row["counted"] == "true"
        assert row["is_ai"] == "false"
        assert row["fingerprint"] == "f" * 64
        assert row["issue_key"] == "i" * 64
        assert row["plan_hash"] == run.plan_hash

    def test_ordered_by_severity_rank_not_alphabetically(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user))
        for severity in ("medium", "info", "critical", "low", "high"):
            FindingFactory(result=result, severity=severity)

        resp = auth_client.get("/api/export/findings.json")
        assert [f["severity"] for f in json.loads(resp.content)] == ["critical", "high", "medium", "low", "info"]

    def test_truncation_is_announced(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user))
        FindingFactory.create_batch(3, result=result, severity="low")
        FindingFactory(result=result, severity="critical")

        resp = auth_client.get("/api/export/findings.csv?limit=2")
        _, rows = _parse_csv(resp.content.decode())
        assert len(rows) == 2
        assert rows[0][3] == "critical"
        assert resp["X-Total-Count"] == "4"
        assert resp["X-Export-Truncated"] == "true"

    def test_canonical_counted_and_ai_filters(self, auth_client, user):
        from backend.rankings.tests.measured import make_run

        job = GenerationJobFactory(created_by=user, status="completed")
        stale = make_run(job)
        AnalysisRun.objects.filter(id=stale.id).update(created_at=timezone.now() - timedelta(hours=1))
        fresh = make_run(job, tools={"bandit": "succeeded", "ai_review": "succeeded"})
        FindingFactory.create_batch(2, result=stale.results.get(tool_slug="bandit"))
        FindingFactory(result=fresh.results.get(tool_slug="bandit"))
        FindingFactory(result=fresh.results.get(tool_slug="bandit"), counted=False)
        FindingFactory(result=fresh.results.get(tool_slug="ai_review"), is_ai=True, counted=False)

        def count(query):
            return len(json.loads(auth_client.get(f"/api/export/findings.json?{query}").content))

        assert count("") == 5
        assert count("canonical=true") == 3
        assert count("canonical=true&counted=true") == 1
        assert count("canonical=true&is_ai=true") == 1
        assert count(f"job_id={job.id}&counted=false") == 2

    def test_invalid_uuid_filter_is_422(self, auth_client):
        assert auth_client.get("/api/export/findings.csv?task_id=nope").status_code == 422

    def test_bearer_token_can_export(self, client, user):
        from backend.tokens.services import generate_token

        raw, _ = generate_token(user, "scripts")
        FindingFactory(result=ToolResultFactory(run=AnalysisRunFactory(created_by=user)))

        resp = client.get("/api/export/findings.json", HTTP_AUTHORIZATION=f"Bearer {raw}")
        assert resp.status_code == HTTPStatus.OK
        assert len(json.loads(resp.content)) == 1


# ── Findings SARIF ────────────────────────────────────────────────────────────


class TestFindingsSarif:
    def test_schema_structure(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(
            run=task,
            tool_slug="bandit",
            tool_version="1.9.2",
            exit_code=1,
            argv=["bandit", "-f", "json", "-r", "/src"],
        )
        FindingFactory(
            result=result,
            severity=Finding.Severity.HIGH,
            rule_id="B105",
            file_path="app.py",
            line_number=42,
            cwe=["CWE-259"],
            fingerprint="a" * 64,
        )

        resp = auth_client.get("/api/export/findings.sarif")
        assert resp.status_code == HTTPStatus.OK
        data = json.loads(resp.content)

        assert data["version"] == "2.1.0"
        assert len(data["runs"]) == 1
        run = data["runs"][0]
        # the real version the image manifest recorded, never a placeholder
        driver = run["tool"]["driver"]
        assert {k: driver.get(k) for k in ("name", "version")} == {"name": "bandit", "version": "1.9.2"}
        # informationUri is the seeded catalog's docs URL (migrate seeds the tool rows), when it has one.
        docs_url = AnalyzerTool.objects.filter(slug="bandit").values_list("docs_url", flat=True).first()
        assert driver.get("informationUri") == (docs_url or None)
        invocation = run["invocations"][0]
        assert invocation["executionSuccessful"] is True
        assert invocation["exitCode"] == 1
        assert invocation["commandLine"] == "bandit -f json -r /src"
        assert len(run["results"]) == 1
        result_entry = run["results"][0]
        assert result_entry["ruleId"] == "B105"
        assert result_entry["level"] == "error"  # HIGH -> error
        # Original 5-bucket severity survives the level collapse.
        assert result_entry["properties"]["severity"] == "high"
        assert result_entry["properties"]["cwe"] == ["CWE-259"]
        assert result_entry["partialFingerprints"] == {"llmlab/v1": "a" * 64}
        loc = result_entry["locations"][0]
        assert loc["physicalLocation"]["artifactLocation"]["uri"] == "app.py"
        assert loc["physicalLocation"]["region"]["startLine"] == 42

    def test_failed_tools_appear_as_unsuccessful_invocations(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        ToolResultFactory(run=task, tool_slug="bandit", tool_version="1.9.2")  # clean, succeeded
        ToolResultFactory(
            run=task,
            tool_slug="semgrep",
            tool_version="1.140.0",
            status=ToolResult.Status.FAILED,
            failure_reason=ToolResult.FailureReason.PARSE_ERROR,
            error_message="invalid JSON",
        )

        data = json.loads(auth_client.get(f"/api/export/findings.sarif?task_id={task.id}").content)

        runs = {r["tool"]["driver"]["name"]: r for r in data["runs"]}
        assert runs["bandit"]["invocations"][0]["executionSuccessful"] is True
        assert runs["bandit"]["results"] == []
        semgrep = runs["semgrep"]["invocations"][0]
        assert semgrep["executionSuccessful"] is False
        assert semgrep["properties"]["failure_reason"] == "parse_error"
        assert semgrep["toolExecutionNotifications"][0]["message"]["text"] == "invalid JSON"

    def test_sarif_groups_by_analyzer_and_version(self, auth_client, user):
        result_bandit = ToolResultFactory(
            run=AnalysisRunFactory(created_by=user),
            tool_slug="bandit",
            tool_version="1",
        )
        result_bandit_2 = ToolResultFactory(
            run=AnalysisRunFactory(created_by=user),
            tool_slug="bandit",
            tool_version="2",
        )
        result_eslint = ToolResultFactory(
            run=AnalysisRunFactory(created_by=user),
            tool_slug="eslint",
            tool_version="9",
        )
        FindingFactory.create_batch(2, result=result_bandit)
        FindingFactory(result=result_bandit_2)
        FindingFactory.create_batch(3, result=result_eslint)

        data = json.loads(auth_client.get("/api/export/findings.sarif").content)

        keys = {(r["tool"]["driver"]["name"], r["tool"]["driver"]["version"]) for r in data["runs"]}
        assert keys == {("bandit", "1"), ("bandit", "2"), ("eslint", "9")}


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

    def test_json_includes_tool_metrics_of_succeeded_results(self, auth_client, user):
        run = AnalysisRunFactory(created_by=user)
        ToolResultFactory(run=run, tool_slug="radon", metrics={"average_complexity": 4.2})
        ToolResultFactory(run=run, tool_slug="jscpd", status=ToolResult.Status.FAILED, metrics={"x": 1})
        resp = auth_client.get("/api/export/analysis-tasks.json")
        data = json.loads(resp.content)
        assert data[0]["metrics_by_tool"] == {"radon": {"average_complexity": 4.2}}
        assert data[0]["tool_statuses"] == {"radon": "succeeded", "jscpd": "failed"}

    def test_json_legacy_summary_metrics_fallback(self, auth_client, user):
        AnalysisRunFactory(
            created_by=user,
            summary={"metrics_by_tool": {"radon": {"average_complexity": 4.2}}},
        )
        data = json.loads(auth_client.get("/api/export/analysis-tasks.json").content)
        assert data[0]["metrics_by_tool"] == {"radon": {"average_complexity": 4.2}}

    def test_csv_measured_run_columns(self, auth_client, user):
        from backend.rankings.tests.measured import make_run

        job = GenerationJobFactory(created_by=user, status="completed")
        run = make_run(job, findings={"high": 2}, measured_sloc=500)
        AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.QUEUED, summary={})

        headers, rows = _parse_csv(auth_client.get("/api/export/analysis-tasks.csv").content.decode())
        assert headers[:11] == [
            "id",
            "name",
            "status",
            "target",
            "critical",
            "high",
            "medium",
            "low",
            "info",
            "duration",
            "created",
        ]
        by_id = {r[0]: dict(zip(headers, r, strict=True)) for r in rows}
        measured = by_id[str(run.id)]
        assert measured["high"] == "2"
        assert measured["total_findings"] == "2"
        assert measured["measured_sloc"] == "500"
        assert measured["canonical_for_plan"] == "true"
        assert measured["job_id"] == str(job.id)
        queued = next(r for r in by_id.values() if r["status"] == "queued")
        # no summary yet: missing, not zero findings
        assert queued["high"] == ""
        assert queued["total_findings"] == ""


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


# ── Regressions: canonical selection under a plan, missing severities ──────────


def _two_plan_jobs(user):
    """job1: plan-A run (3 findings) + a NEWER plan-B run (1 finding); job2: plan-A run (2 findings)."""
    from backend.rankings.tests.measured import PLAN_A
    from backend.rankings.tests.measured import PLAN_B
    from backend.rankings.tests.measured import make_run

    job1 = GenerationJobFactory(created_by=user, status="completed")
    job2 = GenerationJobFactory(created_by=user, status="completed")
    old = make_run(job1, plan_hash=PLAN_A)
    AnalysisRun.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(hours=2))
    FindingFactory.create_batch(3, result=old.results.get(tool_slug="bandit"))
    newer = make_run(job1, plan_hash=PLAN_B)
    FindingFactory(result=newer.results.get(tool_slug="bandit"))
    other = make_run(job2, plan_hash=PLAN_A)
    FindingFactory.create_batch(2, result=other.results.get(tool_slug="bandit"))
    return PLAN_A, PLAN_B


def test_canonical_export_under_a_plan_keeps_jobs_rerun_under_another_plan(auth_client, user):
    plan_a, plan_b = _two_plan_jobs(user)

    def get(query):
        return auth_client.get(f"/api/export/findings.json?{query}")

    by_plan = get(f"plan_hash={plan_a}")
    canonical = get(f"canonical=true&plan_hash={plan_a}")

    assert len(json.loads(by_plan.content)) == 5
    # job1's plan-A run is its canonical run *under plan A* (as rankings/reports pick it)
    assert len(json.loads(canonical.content)) == 5
    assert canonical["X-Total-Count"] == "5"
    assert canonical["X-Canonical-Runs"] == "2"
    assert canonical["X-Canonical-Plans"] == "1"
    assert len(json.loads(get(f"canonical=true&plan_hash={plan_b}").content)) == 1
    # Without a plan: each job's latest complete run of any plan, flagged as mixed.
    unpinned = get("canonical=true")
    assert len(json.loads(unpinned.content)) == 3
    assert unpinned["X-Canonical-Plans"] == "2"


def test_canonical_sarif_under_a_plan_keeps_every_invocation(auth_client, user):
    plan_a, _ = _two_plan_jobs(user)

    sarif = json.loads(auth_client.get(f"/api/export/findings.sarif?canonical=true&plan_hash={plan_a}").content)

    invocations = [inv for run in sarif["runs"] for inv in run.get("invocations", [])]
    assert len(invocations) == 2
    assert sum(len(run.get("results", [])) for run in sarif["runs"]) == 5


def test_analysis_tasks_severities_are_empty_when_static_is_unavailable(auth_client, user):
    """A failed/partial run's zero-filled summary severities are missing data, not a clean scan."""
    from backend.rankings.tests.measured import make_run

    job = GenerationJobFactory(created_by=user, status="completed")
    partial = make_run(
        job,
        status=AnalysisRun.Status.PARTIAL,
        is_complete=False,
        static_available=False,
        findings={"high": 2},
        tools={"bandit": "succeeded", "semgrep": "timed_out"},
    )
    assert partial.summary["severity_counts"]["high"] == 2  # what the engine stores
    # A v2 run whose measure stage failed: a summary but no measurement.
    unmeasured = AnalysisRunFactory(
        created_by=user,
        status=AnalysisRun.Status.FAILED,
        summary={"schema_version": 2, "severity_counts": dict.fromkeys(("critical", "high", "medium", "low"), 0)},
        measurement={},
    )
    # A legacy (v1) flat summary keeps its counts.
    legacy = AnalysisRunFactory(created_by=user, summary={"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0})

    headers, rows = _parse_csv(auth_client.get("/api/export/analysis-tasks.csv").content.decode())
    by_id = {r[0]: dict(zip(headers, r, strict=True)) for r in rows}
    data = {d["id"]: d for d in json.loads(auth_client.get("/api/export/analysis-tasks.json").content)}

    for run in (partial, unmeasured):
        row = by_id[str(run.id)]
        assert [row[s] for s in ("critical", "high", "medium", "low", "info")] == [""] * 5
        assert row["total_findings"] == ""
        assert data[str(run.id)]["severity_counts"] is None
        assert data[str(run.id)]["high"] is None
    assert by_id[str(partial.id)]["static_available"] == "false"
    assert by_id[str(legacy.id)]["critical"] == "1"
