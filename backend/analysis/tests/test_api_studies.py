"""API tests for /api/analysis/studies/* (full lifecycle) and /api/analysis/targets/*."""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
import zipfile

import pytest
from django.test import Client

from backend.analysis.engine import measurement
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.models import ToolResult
from backend.analysis.research import report as report_mod
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalysisStudyFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ReviewSampleFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.analysis.tests.support.api import install_fakes
from backend.analysis.tests.support.api import llm_model
from backend.analysis.tests.support.api import make_job
from backend.analysis.tests.support.api import make_profile
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import ExperimentFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
BASE = "/api/analysis"
# A question added to a frozen protocol after the fact (an edit in the database).
POST_HOC = [{"id": "RQX", "text": "Post hoc?", "kind": "confirmatory", "outcomes": ["measured_sloc"]}]


@pytest.fixture
def engine(monkeypatch, settings):
    return install_fakes(monkeypatch, settings)


@pytest.fixture
def client(user):
    c = Client()
    c.force_login(user)
    return c


def _send(client, method, path, payload=None):
    return getattr(client, method)(f"{BASE}{path}", data=json.dumps(payload or {}), content_type="application/json")


def _finish(run: AnalysisRun, *, status=AnalysisRun.Status.COMPLETED, tool_status=ToolResult.Status.SUCCEEDED):
    """Simulate the executor finishing a run (tool statuses, findings, measurement, summary)."""
    ToolResult.objects.filter(run=run).update(status=tool_status, tool_version="1.8.6", duration_seconds=2.0)
    bandit = run.results.get(tool_slug="bandit")
    FindingFactory(result=bandit, severity="high", category="security", file_path="app.py", line_number=7,
                   rule_id="B602", issue_key=f"k-{run.pk}", file_origin="authored")  # fmt: skip
    run.refresh_from_db()
    run.status = status
    run.is_complete = status == AnalysisRun.Status.COMPLETED
    run.measurement = measurement.compute(run)
    run.summary = measurement.summarize(run)
    run.save()


@pytest.fixture
def design_jobs(user):
    m1, m2 = llm_model("acme/a"), llm_model("acme/b")
    return [make_job(user, model=m, app_slug=app) for m in (m1, m2) for app in ("todo", "blog")]


def test_study_full_lifecycle(engine, client, user, design_jobs, django_capture_on_commit_callbacks):
    profile = make_profile(user, slug="study-profile")
    payload = {
        "name": "Model effect on security density",
        "description": "Two models x two apps",
        "research_questions": [{"text": "Do models differ in issue density?", "kind": "confirmatory"}],
        "target_spec": {"kind": "filter", "filters": {"app_slugs": ["todo", "blog"]}},
        "profile_id": str(profile.id),
        "analysis_design": {"group_by": "model", "bootstrap_resamples": 200},
    }

    # ---- draft ---------------------------------------------------------------
    created = _send(client, "post", "/studies/", payload)
    assert created.status_code == 201, created.content
    study = created.json()
    sid = study["id"]
    assert study["status"] == "draft"
    assert study["profile"]["slug"] == "study-profile"
    assert study["research_questions"][0]["id"] == "RQ1"
    assert study["analysis_design"]["group_by"] == ["model"]
    assert study["exclusion_rules"]["exclude_invalid"] is True
    assert study["actions"]["lock"] is True
    assert study["actions"]["launch"] is False

    bad = _send(client, "patch", f"/studies/{sid}/", {"analysis_design": {"correction": "bonferroni"}})
    assert bad.status_code == 422
    assert list(bad.json()["field_errors"]) == ["analysis_design.correction"]
    assert _send(client, "patch", f"/studies/{sid}/", {"name": "Renamed"}).json()["name"] == "Renamed"

    preflight = _send(client, "post", f"/studies/{sid}/preflight/")
    assert preflight.status_code == 200, preflight.content
    assert preflight.json()["estimates"]["eligible"] == 4
    assert preflight.json()["design"]["complete_blocks"] is True
    assert client.get(f"{BASE}/studies/{sid}/").json()["preflight_summary"]["eligible"] == 4

    # ---- lock (refused, then accepted) ----------------------------------------
    engine.built = False
    refused = _send(client, "post", f"/studies/{sid}/lock/")
    assert refused.status_code == 409
    assert refused.json()["code"] == "blocking_issues"
    assert "image_missing" in {b["code"] for b in refused.json()["blocking"]}
    engine.built = True
    assert client.get(f"{BASE}/studies/{sid}/protocol/").status_code == 404

    locked = _send(client, "post", f"/studies/{sid}/lock/")
    assert locked.status_code == 200, locked.content
    assert locked.json()["status"] == "locked"
    assert locked.json()["protocol_verified"] is True
    protocol = client.get(f"{BASE}/studies/{sid}/protocol/").json()
    assert protocol["verified"] is True
    assert protocol["protocol_hash"] == report_mod.protocol_hash(protocol["protocol"])
    assert protocol["protocol"]["targets"]["counts"]["eligible"] == 4
    protocol_md = client.get(f"{BASE}/studies/{sid}/protocol.md")
    assert protocol_md["Content-Type"].startswith("text/markdown")
    assert b"Renamed" in protocol_md.content

    assert _send(client, "patch", f"/studies/{sid}/", {"name": "too late"}).status_code == 409
    assert _send(client, "post", f"/studies/{sid}/preflight/").status_code == 409
    assert _send(client, "post", f"/studies/{sid}/unlock/").json()["status"] == "draft"
    assert _send(client, "post", f"/studies/{sid}/lock/").json()["status"] == "locked"

    # ---- launch (checks in the request, submission in the background) ---------------
    with django_capture_on_commit_callbacks(execute=True):
        launched = _send(client, "post", f"/studies/{sid}/launch/")
    assert launched.status_code == 202, launched.content
    assert launched.json()["status"] == "running"
    assert launched.json()["launch"]["status"] == "submitting"
    assert launched.json()["launch"]["planned"] == 4
    assert engine.dispatched == [("launch", sid)]
    detail = client.get(f"{BASE}/studies/{sid}/").json()
    assert detail["run_counts"] == {"queued": 4}
    assert detail["preflight"]["launch"]["submitted"] == 4
    assert detail["launch"] == {**detail["launch"], "status": "submitted", "pending": False, "submitted": 4}
    assert detail["actions"]["launch"] is False
    assert _send(client, "post", f"/studies/{sid}/launch/").json()["code"] == "invalid_state"
    assert _send(client, "post", f"/studies/{sid}/unlock/").status_code == 409
    assert client.delete(f"{BASE}/studies/{sid}/").status_code == 409

    progress = client.get(f"{BASE}/studies/{sid}/progress/").json()
    assert progress["counts"]["runs"]["queued"] == 4
    assert progress["counts"]["active"] == 4
    assert (progress["counts"]["planned"], progress["counts"]["submitted"]) == (4, 4)
    assert progress["launch"]["status"] == "submitted"
    assert progress["tools"] == ["bandit", "eslint", "gitleaks"]
    assert {r["tools"]["bandit"]["status"] for r in progress["rows"]} == {"pending"}
    assert {r["group"] for r in progress["rows"]} == {"acme/a", "acme/b"}
    assert progress["progress"] == 0.0
    assert progress["capacity"] is not None

    # ---- runs finish -> completed -------------------------------------------------
    runs = list(AnalysisRun.objects.filter(study_id=sid).order_by("created_at"))
    for run in runs[:3]:
        _finish(run)
    _finish(runs[3], status=AnalysisRun.Status.PARTIAL, tool_status=ToolResult.Status.FAILED)
    progress = client.get(f"{BASE}/studies/{sid}/progress/").json()
    assert progress["status"] == "completed"
    assert progress["counts"]["terminal"] == 4
    assert progress["counts"]["complete"] == 3
    assert progress["tool_counts"]["bandit"] == {"succeeded": 3, "failed": 1}

    # ---- report (background) + exports ------------------------------------------------
    assert client.get(f"{BASE}/studies/{sid}/report/").status_code == 404
    for path in ("report.md", "export/replication.zip"):
        missing = client.get(f"{BASE}/studies/{sid}/{path}")
        assert missing.status_code == 409, path
        assert missing.json()["report_status"] == "none"
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        requested = _send(client, "post", f"/studies/{sid}/report/")
    assert requested.status_code == 202, requested.content
    assert requested.json()["status"] == "generating"
    assert requested.json()["report"] is None
    polled = client.get(f"{BASE}/studies/{sid}/report/").json()
    assert (polled["status"], polled["report"]) == ("generating", None)
    assert client.get(f"{BASE}/studies/{sid}/report.md").json()["report_status"] == "generating"
    assert client.get(f"{BASE}/studies/{sid}/").json()["report_state"]["status"] == "generating"
    for callback in callbacks:
        callback()

    stored = client.get(f"{BASE}/studies/{sid}/report/").json()
    assert stored["status"] == "ready"
    assert stored["verified"] is True
    assert stored["stale"] is False
    # The protocol was re-frozen by the unlock/lock round trip above.
    assert stored["protocol_hash"] == AnalysisStudy.objects.get(pk=sid).protocol_hash != protocol["protocol_hash"]
    assert stored["report_hash"] == report_mod.report_hash(stored["report"]) == stored["report"]["report_hash"]
    assert client.get(f"{BASE}/studies/{sid}/report.md").content.startswith(b"# Analysis report")

    runs_csv = client.get(f"{BASE}/studies/{sid}/export/runs.csv")
    assert runs_csv.status_code == 200
    assert len(list(csv.DictReader(io.StringIO(runs_csv.content.decode())))) == 4
    tables = zipfile.ZipFile(io.BytesIO(client.get(f"{BASE}/studies/{sid}/export/tables.zip").content))
    assert {"runs.csv", "findings.csv"} <= set(tables.namelist())
    replication = zipfile.ZipFile(io.BytesIO(client.get(f"{BASE}/studies/{sid}/export/replication.zip").content))
    assert "SHA256SUMS" in {name.rsplit("/", 1)[-1] for name in replication.namelist()}
    # The package carries the stored report byte for byte in content (no rebuild).
    assert json.loads(replication.read("report.json")) == stored["report"]
    assert json.loads(replication.read("manifest.json"))["report_verified"] is True
    assert client.get(f"{BASE}/studies/{sid}/export/secrets.txt").status_code == 404

    # ---- retry, cancel, delete -------------------------------------------------------------
    retried = _send(client, "post", f"/studies/{sid}/retry-failed/").json()
    assert retried["retried"] == [str(runs[3].pk)]
    assert retried["study"]["status"] == "running"
    cancelled = _send(client, "post", f"/studies/{sid}/cancel/")
    assert cancelled.json()["status"] == "cancelled"
    runs[3].refresh_from_db()
    assert runs[3].status == AnalysisRun.Status.CANCELLED
    deleted = client.delete(f"{BASE}/studies/{sid}/")
    assert deleted.json() == {"deleted": True, "id": sid, "runs": 4}
    assert not AnalysisRun.objects.filter(study_id=sid).exists()


def test_study_validation_and_ownership(engine, client, user):
    invalid = _send(
        client,
        "post",
        "/studies/",
        {
            "name": "x",
            "target_spec": {"kind": "teleport"},
            "exclusion_rules": {"vibes": True},
            "profile_id": "0c49cd0d-0000-4000-8000-000000000001",
        },
    )
    assert invalid.status_code == 422
    assert set(invalid.json()["field_errors"]) == {"target_spec.kind", "exclusion_rules.vibes", "profile_id"}
    assert _send(client, "post", "/studies/", {"name": ""}).status_code == 422

    theirs = AnalysisStudyFactory()
    assert client.get(f"{BASE}/studies/{theirs.id}/").status_code == 404
    assert _send(client, "post", f"/studies/{theirs.id}/lock/").status_code == 404
    assert client.get(f"{BASE}/studies/not-a-uuid/").status_code == 404
    mine = AnalysisStudyFactory(created_by=user)
    listed = client.get(f"{BASE}/studies/").json()
    assert [s["id"] for s in listed["items"]] == [str(mine.id)]
    assert client.get(f"{BASE}/studies/?status=bogus").status_code == 422

    staff = Client()
    staff.force_login(UserFactory(is_staff=True))
    assert staff.get(f"{BASE}/studies/{theirs.id}/").status_code == 200


def test_lock_without_targets_is_refused(engine, client, user):
    study = AnalysisStudyFactory(
        created_by=user,
        profile=make_profile(user),
        target_spec={"kind": "filter", "filters": {"app_slugs": ["nothing-here"]}},
    )
    res = _send(client, "post", f"/studies/{study.id}/lock/")
    assert res.status_code == 409
    assert "no_targets" in {b["code"] for b in res.json()["blocking"]}
    study.refresh_from_db()
    assert study.status == AnalysisStudy.Status.DRAFT


# --------------------------------------------------------------------------
# Targets
# --------------------------------------------------------------------------


def test_resolve_targets_paginates_with_eligibility(engine, client, user, design_jobs):
    empty = make_job(user, files={"README.md": "# docs only\n"}, app_slug="todo")

    res = _send(
        client,
        "post",
        "/targets/resolve/",
        {"target_spec": {"kind": "filter"}, "per_page": 3, "page": 2},
    )

    assert res.status_code == 200, res.content
    body = res.json()
    assert (body["total"], body["pages"], len(body["items"])) == (5, 2, 2)
    assert body["summary"]["models"] == {"acme/a": 2, "acme/b": 2, "acme/coder-1": 1}
    by_id = {i["job_id"]: i for i in body["items"]}
    assert by_id[str(empty.id)]["eligible"] is False
    assert body["eligible_on_page"] == 1

    bad = _send(client, "post", "/targets/resolve/", {"target_spec": {"kind": "experiment"}})
    assert bad.status_code == 422
    assert bad.json()["field_errors"] == {"target_spec.experiment_id": "required for kind=experiment"}
    missing = _send(
        client,
        "post",
        "/targets/resolve/",
        {"target_spec": {"kind": "experiment", "experiment_id": "0c49cd0d-0000-4000-8000-000000000001"}},
    )
    assert missing.json()["field_errors"] == {"target_spec.experiment_id": "experiment not found"}


def test_target_options(engine, client, user):
    experiment = ExperimentFactory(created_by=user)
    model = llm_model("acme/a")
    condition = ExperimentConditionFactory(experiment=experiment, model=model, harness="aider")
    make_job(user, model=model, experiment=experiment, condition=condition, app_slug="todo")
    make_job(user, model=model, experiment=experiment, condition=condition, status="failed")
    make_job(UserFactory())

    body = client.get(f"{BASE}/targets/options/").json()

    assert body["completed_jobs"] == 1
    exp = body["experiments"][0]
    assert (exp["id"], exp["total_jobs"], exp["completed_jobs"]) == (str(experiment.id), 2, 1)
    assert exp["conditions"][0]["model_id"] == "acme/a"
    assert exp["app_slugs"] == ["todo"]
    assert body["models"] == [
        {"id": model.id, "model_id": "acme/a", "name": "acme/a", "provider": model.provider, "jobs": 1},
    ]
    assert body["stacks"] == [{"slug": "flask-react", "jobs": 1}]
    assert body["apps"][0]["slug"] == "todo"
    assert body["modes"] == [{"mode": "scaffolding", "jobs": 1}]


# --------------------------------------------------------------------------
# List endpoints stay flat (no N+1) and every id is typed
# --------------------------------------------------------------------------


def _populate(study, user, n: int) -> None:
    for i in range(n):
        job = make_job(user, app_slug=f"app{i % 4}")
        run = AnalysisRunFactory(
            created_by=user,
            study=study,
            generation_job=job,
            purpose=AnalysisRun.Purpose.RESEARCH,
            status=AnalysisRun.Status.RUNNING if i % 3 else AnalysisRun.Status.QUEUED,
            target_meta={"job_id": str(job.id), "model_slug": f"acme/m{i % 2}", "app_slug": f"app{i % 4}"},
            source_code=None,
        )
        ToolResultFactory(run=run, tool_slug="bandit", position=0)
        ai = ToolResultFactory(run=run, tool_slug="ai_review", tool_kind="ai", category="ai", position=1)
        ReviewSampleFactory(result=ai, index=0, usage={"cost_usd": 0.01})


@pytest.mark.parametrize("path", ["/runs/?per_page=100", "/studies/{sid}/progress/", "/studies/"])
def test_list_endpoints_do_not_grow_queries_with_runs(engine, client, user, path):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def queries(n: int) -> int:
        study = AnalysisStudyFactory(
            created_by=user,
            status=AnalysisStudy.Status.RUNNING,
            plan={"tools": [{"slug": "bandit"}], "reviewer": {"enabled": True, "tool_slug": "ai_review"}},
        )
        _populate(study, user, n)
        client.get(f"{BASE}{path.format(sid=study.id)}")  # warm caches (image status, capacity, reconcile)
        with CaptureQueriesContext(connection) as ctx:
            res = client.get(f"{BASE}{path.format(sid=study.id)}")
        assert res.status_code == 200, res.content
        return len(ctx.captured_queries)

    small, large = queries(5), queries(20)

    # Constant in the number of runs and studies (no N+1); today 7 for /runs/, 12 for progress, auth included.
    assert large == small
    assert large <= 15


UUID_PARAM = re.compile(r"\{uuid(?:str)?:[^}]+\}")  # Ninja registers ``uuid:`` as ``uuidstr:``


def _routes() -> dict[str, list[str]]:
    from backend.analysis.api.views import router

    return {
        path: sorted({method.lower() for operation in view.operations for method in operation.methods})
        for path, view in router.path_operations.items()
    }


def test_every_id_path_parameter_is_typed():
    params = {p for path in _routes() for p in re.findall(r"\{([^}]+)\}", path)}
    ids = {p for p in params if p.rsplit(":", 1)[-1].endswith("_id")}
    assert ids
    assert all(UUID_PARAM.fullmatch("{" + p + "}") for p in ids), ids


@pytest.mark.parametrize(
    ("method", "path"),
    sorted((method, path) for path, methods in _routes().items() if UUID_PARAM.search(path) for method in methods),
)
def test_every_id_route_answers_404_for_bad_or_unknown_ids(engine, client, method, path):
    for value in ("not-a-uuid", str(uuid.uuid4())):
        url = BASE + re.sub(r"\{[^}]+\}", "runs.csv", UUID_PARAM.sub(value, path))
        if method == "get":
            res = client.get(url)
        else:
            res = getattr(client, method)(url, data="{}", content_type="application/json")
        assert res.status_code in (404, 422), (method, url, res.status_code, res.content[:200])


def test_study_list_reads_the_report_state_without_loading_reports(engine, client, user):
    ready = AnalysisStudyFactory(
        created_by=user,
        status=AnalysisStudy.Status.COMPLETED,
        report={"kind": report_mod.REPORT_KIND, "status": "ready", "sections": [{"key": "x" * 1000}]},
        report_hash="r" * 64,
    )
    failed = AnalysisStudyFactory(
        created_by=user,
        status=AnalysisStudy.Status.COMPLETED,
        report={"status": "failed", "request_id": "q1", "requested_at": "2026-10-22T10:00:00+00:00", "error": "boom"},
    )
    AnalysisStudyFactory(created_by=user)

    items = {i["id"]: i for i in client.get(f"{BASE}/studies/").json()["items"]}

    assert items[str(ready.id)]["report_state"]["status"] == "ready"
    assert items[str(ready.id)]["report_state"]["report_hash"] == "r" * 64
    assert items[str(failed.id)]["report_state"] == {
        "status": "failed",
        "request_id": "q1",
        "requested_at": "2026-10-22T10:00:00+00:00",
        "generated_at": None,
        "error": "boom",
        "report_hash": None,
    }
    assert sum(1 for i in items.values() if i["report_state"] is None) == 1
    # The list item and the detail agree.
    assert client.get(f"{BASE}/studies/{failed.id}/").json()["report_state"] == items[str(failed.id)]["report_state"]


# --------------------------------------------------------------------------
# Confirmatory outcomes, integrity of the Markdown documents, CSV escaping, unstorable text
# --------------------------------------------------------------------------


def _study_payload(profile, **extra):
    return {
        "name": "Security density by model",
        "target_spec": {"kind": "filter", "filters": {"app_slugs": ["todo", "blog"]}},
        "profile_id": str(profile.id),
        "analysis_design": {"group_by": "model", "bootstrap_resamples": 50},
        **extra,
    }


def test_confirmatory_questions_name_outcomes_that_join_the_confirmatory_family(engine, client, user, design_jobs):
    profile = make_profile(user, slug="rq-outcomes")
    questions = [
        {
            "text": "Do models differ in security density?",
            "kind": "confirmatory",
            "outcomes": ["security_issues_per_kloc"],
        },
        {"text": "And in high+ density?", "kind": "confirmatory", "outcome": "high_plus_issues_per_kloc"},
        {"text": "What else differs?", "kind": "exploratory"},
    ]
    created = _send(client, "post", "/studies/", _study_payload(profile, research_questions=questions))
    assert created.status_code == 201, created.content
    sid = created.json()["id"]
    stored = created.json()["research_questions"]
    assert stored[0]["outcomes"] == ["security_issues_per_kloc"]
    assert stored[1]["outcomes"] == ["high_plus_issues_per_kloc"]  # the singular form is normalized
    assert "outcomes" not in stored[2]

    def errors(response):
        assert response.status_code == 422, response.content
        return response.json()["field_errors"]

    exploratory = [{"text": "q", "kind": "exploratory", "outcomes": ["security_issues_per_kloc"]}]
    assert list(errors(_send(client, "patch", f"/studies/{sid}/", {"research_questions": exploratory}))) == [
        "research_questions.0.outcomes",
    ]
    unknown = [{"text": "q", "kind": "confirmatory", "outcomes": ["vibes_per_kloc"]}]
    assert (
        "unknown outcome"
        in errors(_send(client, "patch", f"/studies/{sid}/", {"research_questions": unknown}))[
            "research_questions.0.outcomes"
        ]
    )
    # A named outcome must be analysed: a design subset without it is refused, whichever side is edited.
    subset = {"group_by": "model", "outcomes": ["static_issues_per_kloc", "security_issues_per_kloc"]}
    assert errors(_send(client, "patch", f"/studies/{sid}/", {"analysis_design": subset})) == {
        "research_questions.1.outcomes": "not analysed by the design: high_plus_issues_per_kloc "
        "(add to analysis_design.outcomes)",
    }
    assert "research_questions.0.outcomes" in errors(
        _send(
            client,
            "post",
            "/studies/",
            _study_payload(profile, research_questions=questions[:1], analysis_design={"outcomes": ["measured_sloc"]}),
        ),
    )

    locked = _send(client, "post", f"/studies/{sid}/lock/")
    assert locked.status_code == 200, locked.content
    outcomes = client.get(f"{BASE}/studies/{sid}/protocol/").json()["protocol"]["outcomes"]
    assert set(outcomes["confirmatory"]) == {
        "static_issues_per_kloc",  # the primary outcome
        "security_issues_per_kloc",
        "high_plus_issues_per_kloc",
    }
    assert "security_issues_per_kloc" not in outcomes["exploratory"]


def test_lock_blocks_on_stored_questions_whose_outcomes_the_design_drops(engine, client, user, design_jobs):
    """An edit outside the API (e.g. the admin of a draft) cannot freeze an outcome the report would not analyse."""
    profile = make_profile(user, slug="rq-stored")
    study = AnalysisStudyFactory(
        created_by=user,
        profile=profile,
        target_spec={"kind": "filter", "filters": {"app_slugs": ["todo"]}},
        research_questions=[{"id": "RQ1", "text": "q", "kind": "confirmatory", "outcomes": ["measured_sloc"]}],
        analysis_design={"group_by": ["model"], "outcomes": ["static_issues_per_kloc"]},
    )

    refused = _send(client, "post", f"/studies/{study.id}/lock/")

    assert refused.status_code == 409
    blocking = {b["code"]: b for b in refused.json()["blocking"]}
    assert set(blocking["invalid_research_questions"]["field_errors"]) == {"research_questions.0.outcomes"}


def _completed_study(client, user, callbacks) -> str:
    profile = make_profile(user, slug="integrity")
    sid = _send(client, "post", "/studies/", _study_payload(profile)).json()["id"]
    assert _send(client, "post", f"/studies/{sid}/lock/").status_code == 200
    with callbacks(execute=True):
        assert _send(client, "post", f"/studies/{sid}/launch/").status_code == 202
    for run in AnalysisRun.objects.filter(study_id=sid):
        _finish(run)
    assert client.get(f"{BASE}/studies/{sid}/").json()["status"] == "completed"
    return sid


def test_markdown_documents_carry_their_integrity_and_a_tampered_protocol_gets_no_report(
    engine,
    client,
    user,
    design_jobs,
    django_capture_on_commit_callbacks,
):
    from datetime import timedelta

    from django.utils import timezone

    sid = _completed_study(client, user, django_capture_on_commit_callbacks)
    protocol_md = client.get(f"{BASE}/studies/{sid}/protocol.md").content.decode()
    assert protocol_md.splitlines()[:3] == [
        "# Study protocol: Security density by model",
        "",
        "> Integrity at download: protocol sha256 verified.",
    ]
    with django_capture_on_commit_callbacks(execute=True):
        assert _send(client, "post", f"/studies/{sid}/report/").status_code == 202
    report_md = client.get(f"{BASE}/studies/{sid}/report.md").content.decode()
    assert report_md.startswith("# Analysis report")
    assert report_md.splitlines()[2] == (
        "> Integrity at download: report sha256 verified; protocol sha256 verified; "
        "study data unchanged since the report."
    )

    # A run changed after the report: the downloaded document says so (as GET /report/'s ``stale``).
    AnalysisRun.objects.filter(study_id=sid).update(updated_at=timezone.now() + timedelta(seconds=5))
    assert (
        "study data CHANGED after the report was generated"
        in client.get(
            f"{BASE}/studies/{sid}/report.md",
        ).content.decode()
    )

    # The stored protocol no longer matches its hash (edited in the database).
    study = AnalysisStudy.objects.get(pk=sid)
    AnalysisStudy.objects.filter(pk=sid).update(protocol={**study.protocol, "research_questions": POST_HOC})
    assert (
        "protocol sha256 DOES NOT MATCH the stored protocol"
        in client.get(
            f"{BASE}/studies/{sid}/protocol.md",
        ).content.decode()
    )
    assert "protocol sha256 DOES NOT MATCH" in client.get(f"{BASE}/studies/{sid}/report.md").content.decode()
    state = client.get(f"{BASE}/studies/{sid}/report/").json()
    assert (state["protocol_verified"], state["verified"]) == (False, True)
    refused = _send(client, "post", f"/studies/{sid}/report/")
    assert refused.status_code == 409
    assert refused.json()["code"] == "protocol_integrity"
    assert AnalysisStudy.objects.get(pk=sid).report_hash == study.report_hash  # the stored report is kept


def test_study_csv_exports_escape_formulas_on_request(
    engine,
    client,
    user,
    design_jobs,
    django_capture_on_commit_callbacks,
):
    sid = _completed_study(client, user, django_capture_on_commit_callbacks)
    run = AnalysisRun.objects.filter(study_id=sid).first()
    FindingFactory(
        result=run.results.get(tool_slug="bandit"),
        run=run,
        title='=HYPERLINK("http://example.invalid","x")',
        code_snippet='@app.route("/admin")',
        severity="low",
        file_path="app.py",
        line_number=3,
        file_origin="authored",
    )

    verbatim = client.get(f"{BASE}/studies/{sid}/export/findings.csv")
    escaped = client.get(f"{BASE}/studies/{sid}/export/findings.csv?escape=true")

    def cells(response, column):
        return {row[column] for row in csv.DictReader(io.StringIO(response.content.decode()))}

    assert verbatim["X-Formula-Escaping"].startswith("off")
    assert '@app.route("/admin")' in cells(verbatim, "code_snippet")  # research data stays exact by default
    assert escaped["X-Formula-Escaping"] == "on"
    assert '\'@app.route("/admin")' in cells(escaped, "code_snippet")
    assert '\'=HYPERLINK("http://example.invalid","x")' in cells(escaped, "title")
    assert cells(escaped, "run_id") == cells(verbatim, "run_id")  # identifiers are never altered
    tables = zipfile.ZipFile(io.BytesIO(client.get(f"{BASE}/studies/{sid}/export/tables.zip?escape=true").content))
    assert b"'@app.route" in tables.read("findings.csv")


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "a\x00b"},
        {"name": "ok", "description": "x\x00"},
        {"name": "ok", "research_questions": [{"text": "why\x00", "kind": "exploratory"}]},
        {"name": "ok", "target_spec": {"kind": "filter", "filters": {"app_slugs": ["to\x00do"]}}},
    ],
)
def test_unstorable_text_in_a_study_is_a_422(engine, client, payload):
    response = _send(client, "post", "/studies/", payload)

    assert response.status_code == 422, response.content
    detail = response.json()["detail"][0]
    assert detail["loc"][:2] == ["body", "payload"]
    assert "NUL character" in detail["msg"]
    assert not AnalysisStudy.objects.exists()
