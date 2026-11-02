"""Tests for the export API."""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta
from http import HTTPStatus

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.export.services.csv_safe import safe_cell
from backend.export.services.findings import FINDING_HEADERS
from backend.export.services.findings import sarif_level
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
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


def _csv_dicts(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


@pytest.fixture
def job_runs(user):
    """One app analyzed twice: bandit + semgrep, then a re-run of bandit only."""
    model = LLMModelFactory(model_id="acme/coder-1")
    condition = ExperimentConditionFactory(model=model, label="baseline")
    job = GenerationJobFactory(
        created_by=user,
        model=model,
        app_requirement=AppRequirementTemplateFactory(slug="todo"),
        experiment=condition.experiment,
        condition=condition,
        repeat_index=1,
    )
    first = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    old_bandit = FindingFactory(result=ToolResultFactory(run=first, tool_slug="bandit"), title="old bandit")
    semgrep = FindingFactory(result=ToolResultFactory(run=first, tool_slug="semgrep"), title="semgrep")
    rerun = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
    new_bandit = FindingFactory(result=ToolResultFactory(run=rerun, tool_slug="bandit"), title="new bandit")
    return job, old_bandit, semgrep, new_bandit


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
            "result_id",
            "result_status",
            "generation_job_id",
            "experiment_id",
            "condition_id",
            "condition_label",
            "model",
            "app",
            "stack",
            "repeat_index",
            "analyzer",
            "tool_version",
            "tool_severity",
            "normalized_severity",
            "category",
            "file_path",
            "line",
            "column",
            "rule_id",
            "message",
            "description",
            "cwe",
            "package",
            "installed_version",
            "fixed_versions",
            "docs_url",
            "is_latest",
            "created_at",
        ]
        assert headers == FINDING_HEADERS
        # The tool's own label and, named as such, the normalized heuristic.
        assert "tool_severity" in headers
        assert "normalized_severity" in headers
        assert "severity" not in headers

    def test_tool_severity_is_exported_and_unrated_is_named(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user), tool_slug="osv-scanner")
        FindingFactory(result=result, severity=Finding.Severity.HIGH, tool_severity="MODERATE", title="rated")
        FindingFactory(result=result, severity=Finding.Severity.MEDIUM, tool_severity="", title="unrated")

        rows = {r["message"]: r for r in _csv_dicts(auth_client.get("/api/export/findings.csv").content.decode())}
        assert rows["rated"]["tool_severity"] == "MODERATE"
        assert rows["rated"]["normalized_severity"] == "high"
        assert rows["unrated"]["tool_severity"] == "unrated"
        assert rows["unrated"]["normalized_severity"] == "medium"

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

    def test_filter_by_normalized_severity(self, auth_client, user):
        task = AnalysisRunFactory(created_by=user)
        result = ToolResultFactory(run=task)
        FindingFactory(result=result, severity=Finding.Severity.HIGH)
        FindingFactory(result=result, severity=Finding.Severity.LOW)

        rows = _csv_dicts(auth_client.get("/api/export/findings.csv?normalized_severity=high").content.decode())
        assert [r["normalized_severity"] for r in rows] == ["high"]
        # ``severity`` stays as a deprecated alias of the normalized filter.
        rows = _csv_dicts(auth_client.get("/api/export/findings.csv?severity=high").content.decode())
        assert [r["normalized_severity"] for r in rows] == ["high"]

    def test_filter_by_tool_severity(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user))
        FindingFactory(result=result, tool_severity="HIGH", title="a")
        FindingFactory(result=result, tool_severity="high", title="b")
        FindingFactory(result=result, tool_severity="", title="c")

        rows = _csv_dicts(auth_client.get("/api/export/findings.csv?tool_severity=High").content.decode())
        assert sorted(r["message"] for r in rows) == ["a", "b"]
        rows = _csv_dicts(auth_client.get("/api/export/findings.csv?tool_severity=unrated").content.decode())
        assert [r["message"] for r in rows] == ["c"]

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

    def test_rows_name_the_app_and_whether_they_are_counted(self, auth_client, job_runs):
        job, old_bandit, semgrep, new_bandit = job_runs
        rows = {r["id"]: r for r in _csv_dicts(auth_client.get("/api/export/findings.csv").content.decode())}
        row = rows[str(semgrep.id)]
        assert row["generation_job_id"] == str(job.id)
        assert row["model"] == "acme/coder-1"
        assert row["app"] == "todo"
        assert row["repeat_index"] == "1"
        assert row["experiment_id"] == str(job.experiment_id)
        assert row["condition_id"] == str(job.condition_id)
        assert row["condition_label"] == "baseline"
        assert row["result_id"] == str(semgrep.result_id)
        assert row["result_status"] == "completed"
        # Per-tool latest: re-running bandit supersedes only the old bandit result.
        assert rows[str(semgrep.id)]["is_latest"] == "true"
        assert rows[str(new_bandit.id)]["is_latest"] == "true"
        assert rows[str(old_bandit.id)]["is_latest"] == "false"

    def test_unlabelled_condition_is_named_as_in_the_study_tables(self, auth_client, user):
        from backend.statistics.services.study import condition_label

        condition = ExperimentConditionFactory(label="", model=LLMModelFactory(model_id="acme/x"))
        job = GenerationJobFactory(
            created_by=user,
            model=condition.model,
            experiment=condition.experiment,
            condition=condition,
        )
        run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
        FindingFactory(result=ToolResultFactory(run=run, tool_slug="bandit"))
        expected = condition_label(condition)
        assert " / " in expected  # "<model> / <method>", never blank

        (row,) = _csv_dicts(auth_client.get("/api/export/findings.csv").content.decode())
        assert row["condition_label"] == expected
        (item,) = json.loads(auth_client.get("/api/export/findings.json").content)
        assert item["condition_label"] == expected
        (sarif_run,) = json.loads(auth_client.get("/api/export/findings.sarif").content)["runs"]
        assert sarif_run["properties"]["condition_label"] == expected
        (job_row,) = json.loads(auth_client.get("/api/export/generation-jobs.json").content)
        assert job_row["condition_label"] == expected
        (job_csv,) = _csv_dicts(auth_client.get("/api/export/generation-jobs.csv").content.decode())
        assert job_csv["condition_label"] == expected

    def test_latest_only(self, auth_client, job_runs):
        _job, old_bandit, semgrep, new_bandit = job_runs
        rows = _csv_dicts(auth_client.get("/api/export/findings.csv?latest_only=true").content.decode())
        assert {r["id"] for r in rows} == {str(semgrep.id), str(new_bandit.id)}
        assert str(old_bandit.id) not in {r["id"] for r in rows}

    def test_filter_by_experiment_and_job(self, auth_client, user, job_runs):
        job, *_ = job_runs
        other = FindingFactory(result=ToolResultFactory(run=AnalysisRunFactory(created_by=user)))
        rows = _csv_dicts(auth_client.get(f"/api/export/findings.csv?generation_job_id={job.id}").content.decode())
        assert len(rows) == 3
        assert str(other.id) not in {r["id"] for r in rows}
        rows = _csv_dicts(
            auth_client.get(f"/api/export/findings.csv?experiment_id={job.experiment_id}").content.decode(),
        )
        assert len(rows) == 3

    def test_ad_hoc_run_has_no_latest_flag(self, auth_client, user):
        FindingFactory(result=ToolResultFactory(run=AnalysisRunFactory(created_by=user)))
        (row,) = _csv_dicts(auth_client.get("/api/export/findings.csv").content.decode())
        assert row["is_latest"] == ""
        assert row["generation_job_id"] == ""

    def test_streaming_path_matches_buffered(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user))
        FindingFactory(result=result, tool_severity="LOW", title="@scoped/pkg advisory")
        buffered = auth_client.get("/api/export/findings.csv").content.decode()
        streamed = b"".join(auth_client.get("/api/export/findings.csv?limit=20000").streaming_content).decode()
        assert streamed == buffered

    def test_formula_cells_are_neutralized(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user), tool_slug="npm-audit")
        FindingFactory(result=result, title="@remix-run/router <=1.23.1: open redirect", file_path="=cmd|x")
        (row,) = _csv_dicts(auth_client.get("/api/export/findings.csv").content.decode())
        assert row["message"] == "'@remix-run/router <=1.23.1: open redirect"
        assert row["file_path"] == "'=cmd|x"
        # Programmatic readers can opt out.
        (row,) = _csv_dicts(auth_client.get("/api/export/findings.csv?spreadsheet_safe=false").content.decode())
        assert row["message"] == "@remix-run/router <=1.23.1: open redirect"


class TestExportCost:
    """Rows read their app from one per-export job map, never from a joined job per finding."""

    def _sql(self, auth_client, url: str) -> tuple[str, int]:
        with CaptureQueriesContext(connection) as ctx:
            resp = auth_client.get(url)
            assert resp.status_code == HTTPStatus.OK
            if resp.streaming:
                b"".join(resp.streaming_content)
        return " ".join(q["sql"] for q in ctx.captured_queries), len(ctx.captured_queries)

    @pytest.mark.parametrize(
        "url",
        [
            "/api/export/findings.json",
            "/api/export/findings.csv",
            "/api/export/findings.csv?limit=20000",  # streaming path
            "/api/export/findings.sarif",
            "/api/export/generation-jobs.json",
        ],
    )
    def test_heavy_columns_are_never_loaded(self, auth_client, job_runs, url):
        job, *_ = job_runs
        job.result_data = {"files": {"app.py": "x = 1\n" * 1000}}
        job.save(update_fields=["result_data"])
        sql, _ = self._sql(auth_client, url)
        # The generated code and bundle (~0.25 MB per job) and the tools' raw output.
        for column in ("result_data", "resolved_bundle", "raw_output", "custom_system_prompt"):
            assert column not in sql, column

    @pytest.mark.parametrize(
        "url",
        [
            "/api/export/findings.json",
            "/api/export/findings.csv",
            "/api/export/findings.csv?limit=20000",
            "/api/export/findings.sarif",
            "/api/export/generation-jobs.csv",
        ],
    )
    def test_query_count_does_not_grow_with_rows(self, auth_client, user, job_runs, url):
        _, before = self._sql(auth_client, url)
        for i in range(3):
            condition = ExperimentConditionFactory(label=f"arm {i}")
            job = GenerationJobFactory(created_by=user, experiment=condition.experiment, condition=condition)
            run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
            FindingFactory.create_batch(4, result=ToolResultFactory(run=run, tool_slug="bandit"))
        _, after = self._sql(auth_client, url)
        assert after == before

    def test_truncation_is_signalled(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user))
        FindingFactory.create_batch(3, result=result)
        resp = auth_client.get("/api/export/findings.json?limit=2")
        assert len(json.loads(resp.content)) == 2
        assert resp["X-Total-Count"] == "3"
        assert resp["X-Truncated"] == "true"
        resp = auth_client.get("/api/export/findings.csv")
        assert resp["X-Total-Count"] == "3"
        assert resp["X-Truncated"] == "false"
        resp = auth_client.get("/api/export/findings.sarif?limit=1")
        assert resp["X-Truncated"] == "true"
        assert json.loads(resp.content)["properties"] == {"total_findings": 3, "truncated": True}

    def test_cut_drops_the_oldest_rows_not_one_severity(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user))
        now = timezone.now()
        for i, sev in enumerate(["medium", "critical", "medium", "info"]):
            f = FindingFactory(result=result, severity=sev, title=f"f{i}")
            Finding.objects.filter(pk=f.pk).update(created_at=now - timedelta(minutes=10 - i))
        data = json.loads(auth_client.get("/api/export/findings.json?limit=2").content)
        # Newest first: f3 (info) and f2 (medium); alphabetical severity order would keep critical + info.
        assert [d["message"] for d in data] == ["f3", "f2"]

    def test_limit_cap_still_clamps(self, auth_client, user):
        FindingFactory(result=ToolResultFactory(run=AnalysisRunFactory(created_by=user)))
        resp = auth_client.get("/api/export/reports.json?limit=100000")
        assert resp.status_code == HTTPStatus.OK


class TestMalformedParams:
    @pytest.mark.parametrize(
        "url",
        [
            "/api/export/findings.json?task_id=notauuid",
            "/api/export/findings.csv?limit=-1",
            "/api/export/findings.json?experiment_id=bad",
            "/api/export/reports.json?limit=-1",
            "/api/export/generation-jobs.csv?limit=-5",
            "/api/export/review-sample.zip?experiment_id=bad",
        ],
    )
    def test_bad_params_are_422_not_500(self, auth_client, url):
        assert auth_client.get(url).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


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
        assert set(item) == set(FINDING_HEADERS)
        assert item["tool_severity"] == "unrated"
        assert item["normalized_severity"] == "high"
        assert "severity" not in item
        # An ad-hoc run (no generation job) has no app and no latest flag.
        assert item["generation_job_id"] is None
        assert item["is_latest"] is None

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
            severity=Finding.Severity.MEDIUM,
            tool_severity="HIGH",
            rule_id="B105",
            file_path="app.py",
            line_number=42,
            column_number=7,
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
        assert result_entry["level"] == "error"  # the tool's own HIGH, not the normalized "medium"
        assert result_entry["properties"]["tool_severity"] == "HIGH"
        assert result_entry["properties"]["normalized_severity"] == "medium"
        assert "severity" not in result_entry["properties"]
        assert "text" in result_entry["message"]
        loc = result_entry["locations"][0]
        assert loc["physicalLocation"]["artifactLocation"]["uri"] == "app.py"
        assert loc["physicalLocation"]["region"] == {"startLine": 42, "startColumn": 7}
        # No fabricated tool metadata.
        assert "version" not in run["tool"]["driver"]
        assert "informationUri" not in run["tool"]["driver"]
        assert run["properties"]["analysis_run_id"] == str(task.id)

    def test_unrated_results_have_level_none(self, auth_client, user):
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user), tool_slug="pip-audit")
        FindingFactory(result=result, severity=Finding.Severity.MEDIUM, tool_severity="", file_path="r.txt")
        (entry,) = json.loads(auth_client.get("/api/export/findings.sarif").content)["runs"][0]["results"]
        assert entry["level"] == "none"
        assert entry["properties"]["tool_severity"] == ""
        assert entry["properties"]["normalized_severity"] == "medium"

    @pytest.mark.parametrize(
        ("label", "level"),
        [
            ("CRITICAL", "error"),
            ("MODERATE", "warning"),
            ("moderate", "warning"),
            ("warning", "warning"),
            ("WARNING", "warning"),
            ("error", "error"),
            ("LOW", "note"),
            ("Informational", "note"),
            ("convention", "note"),
            ("refactor", "note"),
            ("7.0", "error"),
            ("5.3", "warning"),
            ("2.1", "note"),
            ("UNKNOWN", "none"),
            ("", "none"),
        ],
    )
    def test_level_follows_the_tool_label(self, label, level):
        assert sarif_level(label) == level

    def test_one_run_per_tool_result_with_recorded_version(self, auth_client, user):
        job = GenerationJobFactory(created_by=user, model=LLMModelFactory(model_id="acme/m"))
        for version in ("1.7.10", None):
            run = AnalysisRunFactory(created_by=user, generation_job=job, status=AnalysisRun.Status.COMPLETED)
            summary = {"tool_version": version} if version else {}
            FindingFactory(
                result=ToolResultFactory(run=run, tool_slug="bandit", summary=summary),
                rule_id="B104",
                file_path="app.py",
                line_number=3,
            )
        data = json.loads(auth_client.get("/api/export/findings.sarif").content)
        assert len(data["runs"]) == 2
        versions = {r["tool"]["driver"].get("version") for r in data["runs"]}
        assert versions == {"1.7.10", None}
        ids = {r["automationDetails"]["id"] for r in data["runs"]}
        assert len(ids) == 2
        assert all(r["properties"]["generation_job_id"] == str(job.id) for r in data["runs"])
        assert all(r["properties"]["model"] == "acme/m" for r in data["runs"])
        latest = [r["properties"]["is_latest"] for r in data["runs"]]
        assert sorted(latest) == [False, True]

    def test_dast_results_are_web_requests_not_files(self, auth_client, user):
        AnalyzerToolFactory(slug="zap", analysis_mode="dynamic")
        result = ToolResultFactory(run=AnalysisRunFactory(created_by=user), tool_slug="zap")
        FindingFactory(
            result=result,
            file_path="/assets/index.js",
            tool_severity="Medium",
            tool_specific_data={"url": "http://172.17.0.8:8000/assets/index.js", "method": "GET", "param": "q"},
        )
        (entry,) = json.loads(auth_client.get("/api/export/findings.sarif").content)["runs"][0]["results"]
        assert entry["locations"] == []
        assert entry["webRequest"] == {"target": "/assets/index.js", "method": "GET"}
        assert "172.17.0.8" not in json.dumps(entry)
        assert entry["level"] == "warning"

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

    def test_rows_name_model_slug_app_and_cell(self, auth_client, user):
        GenerationJobFactory(
            created_by=user,
            model=LLMModelFactory(model_id="acme/coder-1", model_name="Coder One"),
            app_requirement=AppRequirementTemplateFactory(slug="todo"),
            stack_slug="flask-react",
            repeat_index=2,
        )
        (row,) = _csv_dicts(auth_client.get("/api/export/generation-jobs.csv").content.decode())
        assert row["model"] == "Coder One"
        assert row["model_id"] == "acme/coder-1"
        assert row["app"] == "todo"
        assert row["stack"] == "flask-react"
        assert row["repeat_index"] == "2"

    def test_filter_by_model_slug(self, auth_client, user):
        GenerationJobFactory(created_by=user, model=LLMModelFactory(model_id="deepseek/deepseek-v4.1-flash"))
        GenerationJobFactory(created_by=user, model=LLMModelFactory(model_id="other/model"))
        resp = auth_client.get("/api/export/generation-jobs.json?model_id=deepseek/deepseek-v4.1-flash")
        assert resp.status_code == HTTPStatus.OK
        data = json.loads(resp.content)
        assert [d["model_id"] for d in data] == ["deepseek/deepseek-v4.1-flash"]

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
        assert "normalized_severity_counts" in data[0]
        assert "reported_severity_counts" in data[0]
        assert "created" in data[0]
        # A run without recorded counts says so instead of claiming zero findings.
        assert data[0]["normalized_severity_counts"] is None
        assert data[0]["reported_severity_counts"] is None
        assert data[0]["unrated_findings"] is None

    def test_reported_labels_and_normalized_buckets(self, auth_client, user):
        AnalysisRunFactory(
            created_by=user,
            summary={
                "total_findings": 5,
                "severity_counts": {"high": 3, "medium": 2},
                "reported_severity_counts": {"MODERATE": 2, "HIGH": 1},
                "unrated_findings": 2,
            },
        )
        (row,) = _csv_dicts(auth_client.get("/api/export/analysis-tasks.csv").content.decode())
        assert row["normalized_high"] == "3"
        assert row["normalized_critical"] == "0"
        assert json.loads(row["reported_severity_counts"]) == {"MODERATE": 2, "HIGH": 1}
        assert row["unrated_findings"] == "2"
        assert row["total_findings"] == "5"
        assert "critical" not in row

    def test_counts_blank_before_they_were_recorded(self, auth_client, user):
        AnalysisRunFactory(created_by=user, summary={"severity_counts": {"low": 1}})
        (row,) = _csv_dicts(auth_client.get("/api/export/analysis-tasks.csv").content.decode())
        assert row["normalized_low"] == "1"
        assert row["reported_severity_counts"] == ""
        assert row["unrated_findings"] == ""

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


def test_safe_cell_leaves_numbers_and_plain_text_alone():
    assert safe_cell("-1.5") == "-1.5"
    assert safe_cell("+3") == "+3"
    assert safe_cell(-2) == -2
    assert safe_cell("plain") == "plain"
    assert safe_cell("-1;2") == "'-1;2"
    assert safe_cell("=1+1") == "'=1+1"
    assert safe_cell("\tx") == "'\tx"
    assert safe_cell("-inf") == "'-inf"
