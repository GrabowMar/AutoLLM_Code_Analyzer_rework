"""API tests for /api/analysis/runs/* (submit, ownership, findings, issues, files, artifacts, exports)."""

from __future__ import annotations

import csv
import io
import json
import uuid
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.test import Client

from backend.analysis.engine import artifacts
from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalysisStudyFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ReviewSampleFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.analysis.tests.support.api import APP_PY
from backend.analysis.tests.support.api import STATIC_CONFIG
from backend.analysis.tests.support.api import install_fakes
from backend.analysis.tests.support.api import make_job
from backend.analysis.tests.support.api import make_profile
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
BASE = "/api/analysis"


@pytest.fixture
def engine(monkeypatch, settings):
    return install_fakes(monkeypatch, settings)


@pytest.fixture
def client(user):
    c = Client()
    c.force_login(user)
    return c


def _post(client, path, payload=None):
    return client.post(f"{BASE}{path}", data=json.dumps(payload or {}), content_type="application/json")


def _submit(client, user, **payload):
    profile = make_profile(user)
    job = make_job(user)
    body = {"generation_job_id": str(job.id), "profile_id": str(profile.id), **payload}
    return _post(client, "/runs/", body), job, profile


# --------------------------------------------------------------------------
# Submit / preview
# --------------------------------------------------------------------------


def test_submit_creates_a_queued_run_with_pending_tool_results(engine, client, user):
    res, job, profile = _submit(client, user, name="first look")

    assert res.status_code == 201, res.content
    body = res.json()
    assert body["status"] == "queued"
    assert body["purpose"] == "adhoc"
    assert body["name"] == "first look"
    assert body["generation_job_id"] == str(job.id)
    assert body["profile"]["id"] == str(profile.id)
    assert body["tool_slugs"] == ["bandit", "eslint", "gitleaks"]
    assert [(r["tool_slug"], r["status"]) for r in body["results"]] == [
        ("bandit", "pending"),
        ("eslint", "pending"),
        ("gitleaks", "pending"),
    ]
    assert body["results"][0]["tool_version"] == "1.8.6"
    assert body["tools"]["pending"] == 3
    assert body["manifest"]["totals"]["included"] == 3
    assert body["target"]["model_slug"] == "acme/coder-1"
    assert body["plan"]["analyzer_image"]["tag"] == engine.tag
    assert len(body["plan_hash"]) == 64
    run = AnalysisRun.objects.get(pk=body["id"])
    assert run.created_by == user


def test_submit_inline_source_with_inline_config(engine, client):
    res = _post(client, "/runs/", {"source_code": {"main.py": APP_PY}, "config": STATIC_CONFIG})

    assert res.status_code == 201, res.content
    body = res.json()
    assert body["source"] == "inline"
    assert body["profile"] is None
    assert body["target"]["label"] == "inline source"
    assert body["manifest"]["totals"]["included"] == 1


def test_submit_validation_errors(engine, client, user):
    profile = make_profile(user)
    job = make_job(user)

    both = _post(client, "/runs/", {"generation_job_id": str(job.id), "source_code": {"a.py": "x"}})
    assert both.status_code == 422
    assert both.json()["field_errors"] == {
        "generation_job_id": "provide exactly one of generation_job_id or source_code",
    }

    bad_uuid = _post(client, "/runs/", {"generation_job_id": "not-a-uuid"})
    assert bad_uuid.status_code == 422

    bad_path = _post(client, "/runs/", {"source_code": {"../etc/passwd": "x"}, "config": STATIC_CONFIG})
    assert bad_path.status_code == 422
    assert "source_code" in bad_path.json()["field_errors"]

    bad_config = _post(client, "/runs/", {"source_code": {"a.py": "x"}, "config": {"tools": [{"slug": "nope"}]}})
    assert bad_config.status_code == 422
    detail = bad_config.json()["detail"]
    assert detail[0]["loc"] == ["body", "payload", "config", "tools", "0", "slug"]
    assert bad_config.json()["field_errors"]["config.tools.0.slug"] == "unknown tool 'nope'"

    both_plans = _post(
        client,
        "/runs/",
        {"generation_job_id": str(job.id), "profile_id": str(profile.id), "config": STATIC_CONFIG},
    )
    assert both_plans.status_code == 422

    unknown_profile = _post(client, "/runs/", {"generation_job_id": str(job.id), "profile_id": str(uuid.uuid4())})
    assert unknown_profile.json()["field_errors"] == {"profile_id": "profile not found"}


def test_submit_refuses_foreign_unfinished_and_unknown_jobs(engine, client, user):
    other = UserFactory()
    foreign = make_job(other)
    running = make_job(user, status="running")
    profile = make_profile(user)

    res = _post(client, "/runs/", {"generation_job_id": str(foreign.id), "profile_id": str(profile.id)})
    assert res.status_code == 404
    res = _post(client, "/runs/", {"generation_job_id": str(uuid.uuid4()), "profile_id": str(profile.id)})
    assert res.status_code == 404
    res = _post(client, "/runs/", {"generation_job_id": str(running.id), "profile_id": str(profile.id)})
    assert res.status_code == 400
    assert not AnalysisRun.objects.exists()

    staff = UserFactory(is_staff=True)
    staff_client = Client()
    staff_client.force_login(staff)
    res = _post(staff_client, "/runs/", {"generation_job_id": str(foreign.id), "profile_id": str(profile.id)})
    assert res.status_code == 201


def test_submit_with_reviewer_needs_a_key(engine, client, user):
    from backend.analysis.tests.support.api import review_model

    review_model()
    engine.key = None
    config = {"tools": [{"slug": "bandit"}], "reviewer": {"enabled": True, "reasoning_effort": None}}

    res = _post(client, "/runs/", {"generation_job_id": str(make_job(user).id), "config": config})

    assert res.status_code == 400
    assert "remediation" in res.json()


def test_preview_runs_the_preflight_without_creating_anything(engine, client, user):
    profile = make_profile(user)
    job = make_job(user)

    res = _post(client, "/runs/preview/", {"generation_job_id": str(job.id), "profile_id": str(profile.id)})

    assert res.status_code == 200, res.content
    body = res.json()
    assert body["targets"][0]["job_id"] == str(job.id)
    assert body["targets"][0]["eligible"] is True
    assert body["plan"]["profile"]["slug"] == profile.slug
    assert body["plan_hash"]
    assert not AnalysisRun.objects.exists()


# --------------------------------------------------------------------------
# List / detail / ownership
# --------------------------------------------------------------------------


def test_list_filters_search_pagination_and_ownership(engine, client, user):
    mine = [AnalysisRunFactory(created_by=user, name=f"run {i}") for i in range(3)]
    AnalysisRunFactory(created_by=user, purpose=AnalysisRun.Purpose.CANARY, name="self-test")
    failed = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.FAILED, name="broken thing")
    AnalysisRunFactory(name="someone else's")

    body = client.get(f"{BASE}/runs/").json()
    assert body["total"] == 4  # canary runs are hidden unless asked for
    assert {i["id"] for i in body["items"]} == {str(r.id) for r in [*mine, failed]}

    assert client.get(f"{BASE}/runs/?status=failed").json()["total"] == 1
    assert client.get(f"{BASE}/runs/?purpose=canary").json()["total"] == 1
    assert client.get(f"{BASE}/runs/?q=broken").json()["items"][0]["id"] == str(failed.id)
    assert client.get(f"{BASE}/runs/?status=exploded").status_code == 422
    assert client.get(f"{BASE}/runs/?study_id=nope").status_code == 422
    assert client.get(f"{BASE}/runs/?ordering=sideways").status_code == 422

    page = client.get(f"{BASE}/runs/?per_page=3&page=2&ordering=created_at").json()
    assert (page["total"], page["pages"], len(page["items"])) == (4, 2, 1)
    beyond = client.get(f"{BASE}/runs/?per_page=3&page=9").json()
    assert beyond["items"] == []
    assert beyond["page"] == 9


def test_detail_is_owner_scoped_and_uuid_typed(engine, client, user):
    theirs = AnalysisRunFactory()
    mine = AnalysisRunFactory(created_by=user)

    assert client.get(f"{BASE}/runs/{mine.id}/").status_code == 200
    assert client.get(f"{BASE}/runs/{theirs.id}/").status_code == 404
    assert client.get(f"{BASE}/runs/not-a-uuid/").status_code == 404
    assert client.get(f"{BASE}/runs/{uuid.uuid4()}/").status_code == 404
    assert _post(client, f"/runs/{theirs.id}/cancel/").status_code == 404

    staff = Client()
    staff.force_login(UserFactory(is_staff=True))
    assert staff.get(f"{BASE}/runs/{theirs.id}/").status_code == 200


def test_detail_payload_provenance(engine, client, user):
    run = AnalysisRunFactory(
        created_by=user,
        target_meta={"job_id": None},
        measurement={"schema_version": 1},
        summary={"schema_version": 2},
    )
    result = ToolResultFactory(
        run=run,
        tool_slug="ai_review",
        tool_kind="ai",
        category="ai",
        status=ToolResult.Status.SUCCEEDED,
        argv=["review"],
        exit_code=0,
        duration_seconds=1.5,
        diagnostics={"stderr_tail": ""},
        coverage={"files_in_scope": 2},
        artifacts={"stdout": {"path": "x", "sha256": "a" * 64, "bytes": 3, "compressed": True}},
    )
    ReviewSampleFactory(result=result, index=0, usage={"cost_usd": 0.01})
    FindingFactory(result=result, is_ai=True, counted=False)

    body = client.get(f"{BASE}/runs/{run.id}/").json()

    tool = body["results"][0]
    assert tool["argv"] == ["review"]
    assert tool["exit_code"] == 0
    assert tool["coverage"] == {"files_in_scope": 2}
    assert tool["findings"] == {"total": 1, "counted": 0}
    assert tool["artifacts"][0]["name"] == "stdout"
    assert tool["artifacts"][0]["url"] == f"/api/analysis/runs/{run.id}/artifacts/{result.id}/stdout"
    assert body["review_samples"][0]["usage"] == {"cost_usd": 0.01}
    assert body["measurement"] == {"schema_version": 1}
    assert "outcomes" in body


# --------------------------------------------------------------------------
# Cancel / retry / delete
# --------------------------------------------------------------------------


def test_cancel_retry_delete(engine, client, user, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        res, _job, _profile = _submit(client, user)
    run_id = res.json()["id"]
    assert engine.enqueued == [run_id]  # dispatched after commit, never inside the request

    assert _post(client, f"/runs/{run_id}/retry/").status_code == 409  # still queued
    assert client.delete(f"{BASE}/runs/{run_id}/").status_code == 409

    cancelled = _post(client, f"/runs/{run_id}/cancel/")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert {r["status"] for r in cancelled.json()["results"]} == {"cancelled"}
    assert _post(client, f"/runs/{run_id}/cancel/").status_code == 409

    bad_tools = _post(client, f"/runs/{run_id}/retry/", {"tools": ["semgrep"]})
    assert bad_tools.status_code == 422
    with django_capture_on_commit_callbacks(execute=True):
        retried = _post(client, f"/runs/{run_id}/retry/", {"tools": ["bandit"]})
    assert engine.enqueued == [run_id, run_id]
    assert retried.status_code == 200, retried.content
    body = retried.json()
    assert body["status"] == "queued"
    assert {r["tool_slug"]: r["status"] for r in body["results"]} == {
        "bandit": "pending",
        "eslint": "cancelled",
        "gitleaks": "cancelled",
    }

    _post(client, f"/runs/{run_id}/cancel/")
    deleted = client.delete(f"{BASE}/runs/{run_id}/")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": run_id}
    assert not AnalysisRun.objects.filter(pk=run_id).exists()


def test_study_runs_cannot_be_deleted_alone(engine, client, user):
    study = AnalysisStudyFactory(created_by=user)
    run = AnalysisRunFactory(created_by=user, study=study)
    res = client.delete(f"{BASE}/runs/{run.id}/")
    assert res.status_code == 409
    assert res.json()["study_id"] == str(study.id)


# --------------------------------------------------------------------------
# Findings / issues
# --------------------------------------------------------------------------


@pytest.fixture
def findings_run(user):
    run = AnalysisRunFactory(created_by=user)
    bandit = ToolResultFactory(run=run, tool_slug="bandit", category="security")
    ruff = ToolResultFactory(run=run, tool_slug="ruff", category="lint", position=1)
    rows = {
        "low": FindingFactory(result=ruff, severity="low", category="style", file_path="a.py", line_number=3,
                              rule_id="E501", counted=False),
        "medium": FindingFactory(result=ruff, severity="medium", category="bug", file_path="a.py", line_number=1,
                                 rule_id="B006", issue_key="k-bug"),
        "info": FindingFactory(result=ruff, severity="info", category="maintainability", file_path="b.py",
                               line_number=None, rule_id="C901"),
        "critical": FindingFactory(result=bandit, severity="critical", category="security", file_path="b.py",
                                   line_number=9, rule_id="B602", cwe=["CWE-78"], issue_key="k-sec"),
        "high": FindingFactory(result=bandit, severity="high", category="security", file_path="a.py",
                               line_number=1, rule_id="B608", cwe=["CWE-89"], issue_key="k-bug",
                               file_origin="added"),
    }  # fmt: skip
    return run, rows


def test_findings_are_severity_rank_ordered_with_facets(client, findings_run):
    run, _rows = findings_run

    body = client.get(f"{BASE}/runs/{run.id}/findings/").json()

    assert [i["severity"] for i in body["items"]] == ["critical", "high", "medium", "low", "info"]
    assert body["ordering"] == "severity"
    assert body["facets"]["severity"] == {"critical": 1, "high": 1, "medium": 1, "low": 1, "info": 1}
    assert body["facets"]["tool"] == {"ruff": 3, "bandit": 2}
    item = body["items"][0]
    assert item["cwe"] == ["CWE-78"]
    assert item["rule_id"] == "B602"

    reverse = client.get(f"{BASE}/runs/{run.id}/findings/?ordering=-severity").json()
    assert [i["severity"] for i in reverse["items"]] == ["info", "low", "medium", "high", "critical"]
    by_file = client.get(f"{BASE}/runs/{run.id}/findings/?ordering=file").json()
    assert [(i["file_path"], i["line_number"]) for i in by_file["items"]] == [
        ("a.py", 1),
        ("a.py", 1),
        ("a.py", 3),
        ("b.py", 9),
        ("b.py", None),
    ]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("severity=critical,high", {"critical", "high"}),
        ("tool=bandit", {"critical", "high"}),
        ("category=bug,style", {"medium", "low"}),
        ("origin=added", {"high"}),
        ("file=b.py", {"critical", "info"}),
        ("rule=E501", {"low"}),
        ("counted=false", {"low"}),
        ("is_ai=false&counted=true&severity=info", {"info"}),
        ("issue_key=k-bug", {"medium", "high"}),
        ("q=B60", {"critical", "high"}),
    ],
)
def test_finding_filters(client, findings_run, query, expected):
    run, _rows = findings_run
    body = client.get(f"{BASE}/runs/{run.id}/findings/?{query}").json()
    assert {i["severity"] for i in body["items"]} == expected


def test_finding_query_validation_and_pagination(client, findings_run):
    run, _rows = findings_run
    assert client.get(f"{BASE}/runs/{run.id}/findings/?severity=urgent").status_code == 422
    assert client.get(f"{BASE}/runs/{run.id}/findings/?ordering=random").status_code == 422
    assert client.get(f"{BASE}/runs/{run.id}/findings/?result_id=zzz").status_code == 422
    page = client.get(f"{BASE}/runs/{run.id}/findings/?per_page=2&page=3").json()
    assert (page["total"], page["pages"], [i["severity"] for i in page["items"]]) == (5, 3, ["info"])


def test_issues_group_by_issue_key_with_detecting_tools(client, findings_run):
    run, _rows = findings_run

    body = client.get(f"{BASE}/runs/{run.id}/issues/").json()

    assert body["total"] == 2
    first, second = body["items"]
    assert (first["issue_key"], first["severity"], first["tools"]) == ("k-sec", "critical", ["bandit"])
    assert (second["issue_key"], second["severity"]) == ("k-bug", "high")
    assert second["tools"] == ["bandit", "ruff"]
    assert second["rule_ids"] == ["B006", "B608"]
    assert second["findings"] == 2
    assert second["cwe"] == ["CWE-89"]
    filtered = client.get(f"{BASE}/runs/{run.id}/issues/?tool=ruff").json()
    assert [i["issue_key"] for i in filtered["items"]] == ["k-bug"]
    assert client.get(f"{BASE}/runs/{run.id}/issues/?kind=ai").json()["total"] == 0
    assert client.get(f"{BASE}/runs/{run.id}/issues/?kind=both").status_code == 422


# --------------------------------------------------------------------------
# Files / content / artifacts / exports
# --------------------------------------------------------------------------


def test_files_and_content_are_reresolved_for_owner_and_staff(engine, client, user):
    res, _job, _profile = _submit(client, user)
    run_id = res.json()["id"]
    run = AnalysisRun.objects.get(pk=run_id)
    FindingFactory(result=run.results.get(tool_slug="bandit"), file_path="app.py", counted=True)

    files = client.get(f"{BASE}/runs/{run_id}/files/").json()
    assert files["count"] == 3
    app_py = next(f for f in files["files"] if f["path"] == "app.py")
    assert app_py["findings"]["total"] == 1
    assert app_py["origin"] == "authored"
    assert client.get(f"{BASE}/runs/{run_id}/files/?language=python").json()["count"] == 1

    content = client.get(f"{BASE}/runs/{run_id}/files/content/?path=app.py")
    assert content.status_code == 200
    assert content.json()["content"] == APP_PY
    assert content.json()["sha256"] == app_py["sha256"]
    assert client.get(f"{BASE}/runs/{run_id}/files/content/?path=../../etc/passwd").status_code == 404

    # Staff see code like they see findings and snippets; other users see nothing.
    staff = Client()
    staff.force_login(UserFactory(is_staff=True))
    assert staff.get(f"{BASE}/runs/{run_id}/files/").status_code == 200
    staff_content = staff.get(f"{BASE}/runs/{run_id}/files/content/?path=app.py")
    assert staff_content.status_code == 200
    assert staff_content.json()["content"] == APP_PY
    assert staff.get(f"{BASE}/runs/{run_id}/files/content/?path=secrets.env").status_code == 404
    other = Client()
    other.force_login(UserFactory())
    assert other.get(f"{BASE}/runs/{run_id}/files/content/?path=app.py").status_code == 404

    # The job's code changed after the run: the API refuses to show different code.
    job = run.generation_job
    job.result_data = {"files": {"app.py": "print('rewritten')\n"}}
    job.save()
    assert client.get(f"{BASE}/runs/{run_id}/files/content/?path=app.py").status_code == 409


def test_artifacts_are_sha_verified(client, user):
    run = AnalysisRunFactory(created_by=user)
    entry = artifacts.store(str(run.id), "bandit", "stdout.txt", b"<script>alert(1)</script>")
    result = ToolResultFactory(run=run, artifacts={"stdout": entry, "out/report.json": entry})
    sample = ReviewSampleFactory(result=ToolResultFactory(run=run, tool_slug="ai_review", tool_kind="ai"))
    sample.artifacts = {"response-0.json": artifacts.store(str(run.id), "ai/sample-0", "r.json", b"{}")}
    sample.save()

    ok = client.get(f"{BASE}/runs/{run.id}/artifacts/{result.id}/stdout")
    assert ok.status_code == 200
    assert ok.content == b"<script>alert(1)</script>"
    assert ok["Content-Type"].startswith("text/plain")
    assert ok["X-Content-Type-Options"] == "nosniff"
    assert ok["X-Artifact-SHA256"] == entry["sha256"]
    nested = client.get(f"{BASE}/runs/{run.id}/artifacts/{result.id}/out/report.json?download=1")
    assert nested.status_code == 200
    assert nested["Content-Disposition"].startswith("attachment")
    assert client.get(f"{BASE}/runs/{run.id}/artifacts/{sample.id}/response-0.json").content == b"{}"
    assert client.get(f"{BASE}/runs/{run.id}/artifacts/{result.id}/missing").status_code == 404
    assert client.get(f"{BASE}/runs/{run.id}/artifacts/{uuid.uuid4()}/stdout").status_code == 404

    Path(django_settings.MEDIA_ROOT, entry["path"]).write_bytes(b"tampered")
    assert client.get(f"{BASE}/runs/{run.id}/artifacts/{result.id}/stdout").status_code == 409

    other = Client()
    other.force_login(UserFactory())
    assert other.get(f"{BASE}/runs/{run.id}/artifacts/{result.id}/stdout").status_code == 404


def test_exports_json_csv_sarif(client, findings_run):
    run, _rows = findings_run
    bandit = run.results.get(tool_slug="bandit")
    bandit.tool_version = "1.8.6"
    bandit.argv = ["bandit", "-f", "json", "-r", "/src"]
    bandit.exit_code = 1
    bandit.save()
    ToolResultFactory(
        run=run,
        tool_slug="mypy",
        status=ToolResult.Status.FAILED,
        failure_reason=ToolResult.FailureReason.TOOL_ERROR,
        error_message="exit 2",
        position=2,
    )
    FindingFactory(result=run.results.get(tool_slug="ruff"), title="=HYPERLINK(evil)", severity="low")

    exported = client.get(f"{BASE}/runs/{run.id}/export.json")
    assert exported.status_code == 200
    assert exported["Content-Disposition"].startswith("attachment")
    data = json.loads(exported.content)
    assert data["run"]["id"] == str(run.id)
    assert [f["severity"] for f in data["findings"]][:2] == ["critical", "high"]

    text = client.get(f"{BASE}/runs/{run.id}/export.csv").content.decode()
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["severity"] == "critical"
    assert rows[0]["tool_version"] == "1.8.6"
    assert rows[0]["cwe"] == "CWE-78"
    assert any(r["title"] == "'=HYPERLINK(evil)" for r in rows)

    sarif = json.loads(client.get(f"{BASE}/runs/{run.id}/export.sarif").content)
    assert sarif["version"] == "2.1.0"
    runs = {r["tool"]["driver"]["name"]: r for r in sarif["runs"]}
    assert runs["bandit"]["tool"]["driver"]["version"] == "1.8.6"
    assert runs["bandit"]["invocations"][0]["executionSuccessful"] is True
    assert runs["bandit"]["invocations"][0]["commandLine"] == "bandit -f json -r /src"
    assert runs["mypy"]["invocations"][0]["executionSuccessful"] is False
    assert runs["mypy"]["invocations"][0]["toolExecutionNotifications"][0]["descriptor"]["id"] == "tool_error"
    assert runs["mypy"]["results"] == []
    critical = runs["bandit"]["results"][0]
    assert critical["level"] == "error"
    assert critical["ruleId"] == "B602"
    region = critical["locations"][0]["physicalLocation"]["region"]
    assert region["startLine"] == 9
    assert "external/cwe/cwe-78" in runs["bandit"]["tool"]["driver"]["rules"][0]["properties"]["tags"]


def test_retry_accepts_an_empty_request(engine, client, user):
    res, _job, _profile = _submit(client, user)
    run_id = res.json()["id"]
    _post(client, f"/runs/{run_id}/cancel/")

    retried = client.post(f"{BASE}/runs/{run_id}/retry/")

    assert retried.status_code == 200, retried.content
    assert {r["status"] for r in retried.json()["results"]} == {"pending"}


def test_list_endpoints_nudge_reconcile_at_most_once_a_minute(
    engine,
    client,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from django.core.cache import cache

    from backend.analysis.api import common
    from backend.analysis.engine import executor

    calls = []
    monkeypatch.setattr(executor, "reconcile", lambda now=None: calls.append(now) or {})
    # Run the background work inline (a real thread would use its own connection).
    monkeypatch.setattr(common, "_in_thread", lambda name, target: target())
    cache.delete(common.RECONCILE_CACHE_KEY)

    with django_capture_on_commit_callbacks(execute=True):
        assert client.get(f"{BASE}/runs/").status_code == 200
        assert client.get(f"{BASE}/studies/").status_code == 200

    assert calls == [None]
    cache.delete(common.RECONCILE_CACHE_KEY)


def test_run_list_has_no_n_plus_one(engine, client, user, django_assert_max_num_queries):
    for i in range(12):
        run = AnalysisRunFactory(created_by=user, name=f"r{i}", source_code={"big.py": "x = 1\n" * 1000})
        ToolResultFactory(run=run, tool_slug="bandit")
        ToolResultFactory(run=run, tool_slug="ruff", position=1)

    with django_assert_max_num_queries(8):
        body = client.get(f"{BASE}/runs/?per_page=50").json()

    assert body["total"] == 12
    assert body["items"][0]["tool_slugs"] == ["bandit", "ruff"]
    assert body["items"][0]["source"] == "inline"


# --------------------------------------------------------------------------
# Unstorable text (NUL, unpaired surrogates) is a 422, never a 500
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "where"),
    [
        # A binary file pasted next to the source (Postgres jsonb cannot store NUL).
        (
            {"source_code": {"main.py": "print(1)\n", "assets/logo.bin": "PNG\x00\x01"}},
            "source_code['assets/logo.bin']",
        ),
        ({"source_code": {"main.py": "print(1)\n"}, "name": "x\x00"}, "name"),
        ({"source_code": {"ma\x00in.py": "print(1)\n"}}, "source_code (a key)"),
    ],
)
def test_unstorable_text_in_a_run_is_a_422(engine, client, body, where):
    for path in ("/runs/", "/runs/preview/"):
        response = _post(client, path, {**body, "config": STATIC_CONFIG})

        assert response.status_code == 422, response.content
        detail = response.json()["detail"][0]
        assert detail["loc"][:2] == ["body", "payload"]
        assert f"at {where};" in detail["msg"]
    assert not AnalysisRun.objects.exists()


@pytest.mark.parametrize(
    "path",
    [
        "/runs/?q=%00",
        "/runs/?plan_hash=a%00",
        "/studies/?q=%00",
        "/profiles/?q=%00",
        "/selftest/latest/?image_tag=%00",
        "/targets/options/?x%00=1",
    ],
)
def test_nul_in_a_query_string_is_a_422(engine, client, path):
    response = client.get(f"{BASE}{path}")

    assert response.status_code == 422, response.content
    assert response.json()["detail"][0]["loc"][0] == "query"


def test_an_unpaired_surrogate_in_inline_source_is_a_422(engine, client):
    # Valid JSON ("\ud800" escape) that no UTF-8 encoder can store.
    body = '{"source_code": {"main.py": "x = \\"\\ud800\\"\\n"}, "config": ' + json.dumps(STATIC_CONFIG) + "}"
    for path in ("/runs/", "/runs/preview/"):
        response = client.post(f"{BASE}{path}", data=body, content_type="application/json")

        assert response.status_code == 422, response.content
        assert "unpaired surrogate at source_code['main.py']" in response.json()["detail"][0]["msg"]
    assert not AnalysisRun.objects.exists()


# --------------------------------------------------------------------------
# Single-run retry vs a concurrent study cancel
# --------------------------------------------------------------------------


def _running_study_with_a_failed_run(user):
    study = AnalysisStudyFactory(created_by=user, status="running")
    failed = AnalysisRunFactory(created_by=user, study=study, status=AnalysisRun.Status.FAILED, is_complete=False)
    ToolResultFactory(run=failed, tool_slug="bandit", status=ToolResult.Status.FAILED)
    active = AnalysisRunFactory(created_by=user, study=study, status=AnalysisRun.Status.QUEUED, is_complete=False)
    ToolResultFactory(run=active, tool_slug="bandit", status=ToolResult.Status.PENDING)
    return study, failed


def test_retrying_a_run_of_a_cancelled_or_completed_study(engine, client, user):
    from backend.analysis.models import AnalysisStudy
    from backend.analysis.research import study as study_mod

    study, failed = _running_study_with_a_failed_run(user)
    study_mod.cancel(study)

    refused = _post(client, f"/runs/{failed.id}/retry/")

    assert refused.status_code == 409
    assert refused.json() == {"detail": "The run belongs to a cancelled study.", "code": "invalid_state"}
    failed.refresh_from_db()
    assert failed.status == AnalysisRun.Status.FAILED

    done = AnalysisStudyFactory(created_by=user, status="completed")
    run = AnalysisRunFactory(created_by=user, study=done, status=AnalysisRun.Status.FAILED, is_complete=False)
    ToolResultFactory(run=run, tool_slug="bandit", status=ToolResult.Status.FAILED)
    assert _post(client, f"/runs/{run.id}/retry/").json()["status"] == "queued"
    assert AnalysisStudy.objects.get(pk=done.pk).status == "running"  # reopened


# serialized_rollback: restore the seeded rows after the flush, as later serialized tests expect.
@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_retry_run_and_study_cancel_serialize_on_the_study(engine, user, monkeypatch):
    """A study cancel arriving while a run retry is in flight waits for it, then cancels the requeued run."""
    import threading

    from django.db import connection

    from backend.analysis.engine import executor
    from backend.analysis.models import AnalysisStudy
    from backend.analysis.research import study as study_mod

    study, failed = _running_study_with_a_failed_run(user)
    paused, resume = threading.Event(), threading.Event()
    real_retry = executor.retry

    def retry_then_pause(run, **kwargs):
        result = real_retry(run, **kwargs)  # the run is requeued but not committed yet
        paused.set()
        assert resume.wait(20)
        return result

    monkeypatch.setattr(executor, "retry", retry_then_pause)
    out: dict[str, int] = {}

    def retry_request():
        try:
            c = Client()
            c.force_login(user)
            out["status"] = _post(c, f"/runs/{failed.id}/retry/").status_code
        finally:
            connection.close()

    def cancel_study():
        try:
            study_mod.cancel(AnalysisStudy.objects.get(pk=study.pk))
        finally:
            connection.close()

    retrying = threading.Thread(target=retry_request)
    retrying.start()
    assert paused.wait(20), "the retry request never reached executor.retry"
    cancelling = threading.Thread(target=cancel_study)
    cancelling.start()
    cancelling.join(1.0)
    blocked = cancelling.is_alive()  # waiting for the study row the retry holds
    resume.set()
    retrying.join(20)
    cancelling.join(20)

    assert blocked
    assert out["status"] == 200
    study.refresh_from_db()
    failed.refresh_from_db()
    assert study.status == AnalysisStudy.Status.CANCELLED
    # Cancel saw the requeued run as active and cancelled it: no active run in a cancelled study.
    assert failed.status == AnalysisRun.Status.CANCELLED
    assert not AnalysisRun.objects.filter(study=study, status__in=AnalysisRun.ACTIVE_STATUSES).exists()
