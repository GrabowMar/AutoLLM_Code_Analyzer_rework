"""Run insights: code profile, phase timeline and quality metrics (read-only, derived on read)."""

from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation import blobs
from backend.generation.api.job_insights import build_timeline
from backend.generation.api.job_insights import quality_metrics
from backend.generation.models import GenerationRun
from backend.generation.models.choices import FileExclusion
from backend.generation.models.choices import FileOrigin
from backend.generation.models.choices import RunStatus
from backend.generation.services.code_profile import code_profile
from backend.generation.services.code_profile import file_layer
from backend.generation.testing.content import starter_content
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import RunAttemptFactory
from backend.generation.tests.factories import RunEvaluationFactory
from backend.generation.tests.factories import RunFileFactory
from backend.generation.tests.factories import StackProfileFactory
from backend.generation.tests.factories import StarterFactory
from backend.generation.tests.factories import StudyCellFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"

APP_PY = """\
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
app = Flask(__name__)

# a comment line, not LOC


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80))


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)


@app.route("/api/health")
def health():
    return {"ok": True}


@app.get("/api/notes")
def notes():
    return []
"""

APP_JSX = """\
import React from 'react';
// comment
export default function App() {
  return <NoteList />;
}
const NoteList = () => <ul></ul>;
function helper() { return 1; }
"""

PACKAGE_JSON = json.dumps(
    {"dependencies": {"react": "^18.2.0", "axios": "1.6.8", "zod": "^3.0.0"}, "devDependencies": {"vite": "^5.0"}},
)
REQUIREMENTS = "Flask==3.0.0\nrequests==2.31.0\n"
STARTER_REQUIREMENTS = "Flask==3.0.0\nbcrypt==4.1.2\n"
STARTER_PACKAGE_JSON = json.dumps(
    {"dependencies": {"react": "^18.2", "axios": "^1.6.0"}, "devDependencies": {"vite": "^5.0.0"}},
)

# path -> (text, origin, excluded)
APP_FILES = {
    "app.py": (APP_PY, FileOrigin.MODIFIED, ""),
    "requirements.txt": (REQUIREMENTS, FileOrigin.MODIFIED, ""),
    "frontend/package.json": (PACKAGE_JSON, FileOrigin.STARTER, ""),
    "frontend/src/App.jsx": (APP_JSX, FileOrigin.MODIFIED, ""),
    "frontend/src/components/NoteCard.jsx": ("export function NoteCard() { return null; }\n", FileOrigin.AUTHORED, ""),
    "frontend/src/pages/Home.jsx": ("const Home = () => null;\nexport default Home;\n", FileOrigin.AUTHORED, ""),
    "tests/test_api.py": ("def test_ok():\n    assert True\n", FileOrigin.AUTHORED, ""),
    "README.md": ("# Notes\n\nSome docs\n", FileOrigin.STARTER, ""),
    "frontend/node_modules/x/index.js": ("x\n", FileOrigin.AUTHORED, FileExclusion.VENDORED),
}


def _starter():
    profile = StackProfileFactory()
    files = {
        "requirements.txt": {"blob": blobs.put_text(STARTER_REQUIREMENTS), "mode": 0o644},
        "frontend/package.json": {"blob": blobs.put_text(STARTER_PACKAGE_JSON), "mode": 0o644},
        "frontend/postcss.config.js": {"blob": blobs.put_text("module.exports = {};\n"), "mode": 0o644},
    }
    return StarterFactory(stack_profile=profile, content=starter_content(profile.pinned_ref, files=files))


def _app_run(user=None, *, with_files=True, **kw):
    run = GenerationRunFactory(tested=True, starter=_starter(), created_by=user or UserFactory(), **kw)
    if with_files:
        for path, (text, origin, excluded) in APP_FILES.items():
            RunFileFactory(run=run, path=path, blob=blobs.put_text(text), origin=origin, excluded=excluded)
        RunFileFactory(run=run, path="frontend/postcss.config.js", blob="", origin=FileOrigin.DELETED)
    return run


# ── code profile ─────────────────────────────────────────────────────────


def test_file_layer_follows_loc_classifiers():
    assert file_layer("app.py") == "backend"
    assert file_layer("frontend/src/App.jsx") == "frontend"
    assert file_layer("tests/test_api.py") == "tests"
    assert file_layer("README.md") == "config"
    assert file_layer("frontend/node_modules/x/index.js") == "excluded"


def test_code_profile_loc_layers_and_origins():
    run = _app_run()
    profile = code_profile(run)
    assert profile["run_id"] == str(run.id)
    assert profile["kind"] == "app_agentic"
    # Model scope: authored + modified, excluded rows left out; the starter's README and package.json are not
    # the model's, so they only count in the app scope (and are not source).
    assert profile["loc"]["total_loc"] > 0
    assert profile["loc"]["test_loc"] == 2
    assert profile["loc_app"]["total_loc"] == profile["loc"]["total_loc"]
    layers = {row["layer"]: row for row in profile["layers"]}
    assert layers["tests"]["files"] == 1
    assert layers["config"]["source_loc"] is None  # not source: no LOC, by definition
    assert layers["excluded"]["files"] == 1
    origins = profile["origins"]
    assert origins["known"] is True
    assert origins["counts"] == {"modified": 3, "starter": 2, "authored": 4}
    assert origins["per_file"]["frontend/package.json"] == "starter"
    assert origins["deleted_starter_files"] == ["frontend/postcss.config.js"]
    assert origins["excluded_by_reason"] == {"vendored": 1}
    structure = profile["structure"]
    assert structure["models"] == 2
    assert structure["model_names"] == ["User", "Note"]
    assert structure["route_decorators"] == 2
    assert structure["frontend_component_files"] == 1
    assert structure["frontend_page_files"] == 1


def test_code_profile_dependencies_against_the_starter():
    deps = code_profile(_app_run())["dependencies"]
    assert deps["has_manifests"] is True
    assert deps["starter_ref"]
    status = {row["package"]: row["status"] for row in deps["declared"]}
    assert status["flask"] == "starter"
    assert status["requests"] == "added"
    assert status["axios"] == "repinned"  # 1.6.8 is not ^1.6.0's range
    assert status["react"] == "starter"  # ^18.2.0 is ^18.2
    assert "requests" in deps["model_added"]
    assert deps["dropped_starter"] == ["bcrypt"]
    assert deps["resolved_python"] is None  # the lock step never ran: unknown, not empty


def test_code_profile_resolved_lock():
    run = _app_run()
    lock = {
        "ecosystems": {"pip": [{"name": "Flask", "version": "3.0.0"}, {"name": "Werkzeug", "version": "3.0.1"}]},
        "files": {},
        "resolved_at": "2026-11-03T00:00:00Z",
    }
    GenerationRun.objects.filter(pk=run.pk).update(resolved_lock_blob=blobs.put_json(lock))
    run.refresh_from_db()
    resolved = {row["package"]: row for row in code_profile(run)["dependencies"]["resolved_python"]}
    assert resolved["Flask"]["declared"] is True
    assert resolved["Werkzeug"]["declared"] is False


def test_code_profile_without_files_is_not_measured():
    profile = code_profile(_app_run(with_files=False))
    assert profile["loc"] == {"total_loc": None, "backend_loc": None, "frontend_loc": None, "test_loc": None}
    assert profile["all_lines"] is None
    assert profile["origins"]["known"] is False
    assert profile["structure"] is None
    assert profile["dependencies"]["model_added"] is None
    assert profile["notes"]


@pytest.mark.parametrize("endpoint", ["code-profile", "timeline", "quality-metrics"])
def test_insight_endpoints_are_owner_only(client, endpoint):
    owner = UserFactory()
    run = _app_run(owner)
    client.force_login(UserFactory())
    assert client.get(f"{API}/runs/{run.id}/{endpoint}/").status_code == 404
    assert client.get(f"{API}/runs/not-a-uuid/{endpoint}/").status_code == 404
    client.force_login(owner)
    assert client.get(f"{API}/runs/{run.id}/{endpoint}/").status_code == 200


def test_code_profile_endpoint(client):
    user = UserFactory()
    run = _app_run(user)
    client.force_login(user)
    body = client.get(f"{API}/runs/{run.id}/code-profile/").json()
    assert body["run_id"] == str(run.id)
    assert body["origins"]["known"] is True


# ── timeline ─────────────────────────────────────────────────────────────


def _timed_run(**kw):
    started = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
    run = GenerationRunFactory(
        tested=True,
        started_at=started,
        completed_at=started + timedelta(seconds=665),
        duration_seconds=665.0,
        execution={"queue_seconds": 3600.0, "claimed_at": started.isoformat(), "infra_errors": [{"error": "x" * 900}]},
        **kw,
    )
    GenerationRun.objects.filter(pk=run.pk).update(created_at=started - timedelta(hours=1))
    run.refresh_from_db()
    return run, started


def test_timeline_attempts_and_evaluation():
    run, started = _timed_run()
    RunAttemptFactory(
        run=run,
        started_at=started,
        ended_at=started + timedelta(seconds=100),
        outcome="infra_error",
        infra_error="sandbox_lost",
    )
    RunAttemptFactory(
        run=run,
        started_at=started + timedelta(seconds=120),
        ended_at=started + timedelta(seconds=600),
        outcome="tested",
        termination="completed",
        time_split={"llm_s": 300.0, "tool_s": 150.0, "install_s": 20.0, "idle_s": 10.0},
    )
    RunEvaluationFactory(
        run=run,
        stages={"lock": {"status": "ok", "seconds": 5.0}, "build": {"status": "ok", "seconds": 40.0}},
    )
    body = build_timeline(run)
    assert [s["key"] for s in body["segments"]] == ["queue", "attempt_1", "attempt_2", "evaluation"]
    queue = body["segments"][0]
    assert queue["seconds"] == 3600.0
    assert queue["parts"][0]["seconds"] == 3600.0
    second = body["segments"][2]
    assert second["seconds"] == 480.0
    assert {p["key"]: p["seconds"] for p in second["parts"]} == {
        "llm_s": 300.0,
        "tool_s": 150.0,
        "install_s": 20.0,
        "idle_s": 10.0,
    }
    assert body["segments"][1]["note"] == "sandbox_lost"
    evaluation = body["segments"][3]
    assert evaluation["seconds"] == 45.0
    assert [p["key"] for p in evaluation["parts"]] == ["lock", "build"]
    assert body["run_seconds"] == 665.0
    assert body["execution"]["infra_error_count"] == 1
    assert len(body["execution"]["infra_errors"][0]["error"]) <= 301
    assert body["notes"]  # retried
    assert body["study"] is None
    assert [m["key"] for m in body["milestones"]] == ["created", "claimed", "started", "finished"]


def test_timeline_unknown_durations_stay_none():
    run, started = _timed_run()
    RunAttemptFactory(run=run, started_at=started + timedelta(seconds=10), ended_at=started)  # clock skew
    RunEvaluationFactory(run=run, stages={"build": {"status": "running"}})
    body = build_timeline(run)
    attempt = next(s for s in body["segments"] if s["key"] == "attempt_1")
    assert attempt["seconds"] is None
    evaluation = next(s for s in body["segments"] if s["key"] == "evaluation")
    assert evaluation["seconds"] is None


def test_timeline_cancelled_before_start_shows_its_queue_time():
    run = GenerationRunFactory(status=RunStatus.CANCELLED)
    GenerationRun.objects.filter(pk=run.pk).update(completed_at=run.created_at + timedelta(seconds=30))
    run.refresh_from_db()
    body = build_timeline(run)
    assert body["segments"][0]["label"] == "Queued until cancelled"
    assert body["segments"][0]["seconds"] == 30.0


def test_timeline_running_run_without_attempts():
    run = GenerationRunFactory(status=RunStatus.RUNNING, started_at=datetime(2026, 11, 2, 7, 0, tzinfo=UTC))
    body = build_timeline(run)
    assert body["live"] is True
    assert body["segments"][-1]["key"] == "run"
    assert body["segments"][-1]["approximate"] is True


def test_timeline_study_cell():
    cell = StudyCellFactory(repeat_index=1)
    run = GenerationRunFactory(
        tested=True,
        study=cell.study,
        condition=cell.condition,
        cell=cell,
        repeat_index=1,
        stage="main",
        cell_attempt=1,
    )
    cell.current_run = run
    cell.save(update_fields=["current_run"])
    study = build_timeline(run)["study"]
    assert study["study_id"] == str(cell.study_id)
    assert study["condition_label"] == cell.condition.label
    assert study["unit_key"] == cell.unit.unit_key
    assert study["repeat_number"] == 2
    assert study["cell_runs"] == 1
    assert study["is_current_in_cell"] is True


# ── quality metrics ──────────────────────────────────────────────────────


def _result(run, user, slug, *, status=ToolResult.Status.COMPLETED, metrics=None, summary=None, analysis=None):
    analysis = analysis or AnalysisRunFactory(generation_job=run, created_by=user, status=AnalysisRun.Status.COMPLETED)
    return ToolResultFactory(run=analysis, tool_slug=slug, status=status, metrics=metrics or {}, summary=summary or {})


def test_quality_metrics_latest_result_per_tool(client):
    user = UserFactory()
    run = GenerationRunFactory(tested=True, created_by=user)
    first = _result(
        run,
        user,
        "lizard",
        metrics={"avg_ccn": 2.5, "max_ccn": 9, "functions": 10, "total_nloc": 95},
        summary={"tool_version": "1.17.31", "parser_sha": "abc"},
    )
    # A later lizard result that measured nothing does not replace the measured one.
    _result(run, user, "lizard", status=ToolResult.Status.FAILED)
    _result(run, user, "jscpd", metrics={"duplication_percentage": 2.0, "clones": 1}, analysis=first.run)
    # A dependency scanner's counter is not a code metric; a performance-category tool's is.
    AnalyzerToolFactory(slug="osv-scanner", category="dependencies")
    AnalyzerToolFactory(slug="complexipy", name="Complexipy", category="performance")
    _result(run, user, "osv-scanner", metrics={"dev_only_advisories_skipped": 0}, analysis=first.run)
    _result(run, user, "complexipy", metrics={"cognitive_total": 41}, analysis=first.run)
    _result(run, user, "bandit", analysis=first.run)  # no metrics: not listed
    other_user_analysis = AnalysisRunFactory(generation_job=run, status=AnalysisRun.Status.COMPLETED)
    _result(
        run,
        user,
        "radon",
        status=ToolResult.Status.SKIPPED,
        summary={"skip_reason": "no_python"},
        analysis=other_user_analysis,
    )

    client.force_login(user)
    body = client.get(f"{API}/runs/{run.id}/quality-metrics/").json()
    tools = {t["tool_slug"]: t for t in body["tools"]}
    assert list(tools) == ["lizard", "radon", "jscpd", "complexipy"]

    lizard = tools["lizard"]
    assert lizard["measured"] is True
    assert lizard["status"] == "completed"
    assert lizard["metrics"]["avg_ccn"] == 2.5
    assert lizard["derived"] == {"avg_function_nloc": 9.5}
    assert lizard["tool_version"] == "1.17.31"
    assert lizard["parser_sha"] == "abc"
    assert lizard["run"]["id"] == str(first.run_id)
    assert lizard["run"]["accessible"] is True
    assert lizard["kind"] == "complexity"

    radon = tools["radon"]
    assert radon["measured"] is False  # skipped: measured nothing
    assert radon["metrics"] == {}
    assert radon["skip_reason"] == "no_python"
    assert radon["run"]["accessible"] is False  # another user's analysis
    assert radon["run"]["name"] is None  # its name is not disclosed

    assert tools["jscpd"]["kind"] == "duplication"
    assert tools["complexipy"]["kind"] == "other"
    assert tools["complexipy"]["tool_name"] == "Complexipy"
    assert body["measured"] == 3


def test_quality_metrics_not_run():
    run = GenerationRunFactory()
    body = quality_metrics(run, run.created_by)
    assert body["measured"] == 0
    assert [(t["tool_slug"], t["status"], t["measured"]) for t in body["tools"]] == [
        ("lizard", "not_run", False),
        ("radon", "not_run", False),
        ("jscpd", "not_run", False),
    ]


def test_quality_metrics_not_applicable_is_not_measured():
    """A tool skipped as not applicable measured nothing (spec section 8.8): never a measured zero."""
    user = UserFactory()
    run = GenerationRunFactory(created_by=user)
    radon = _result(
        run,
        user,
        "radon",
        status=ToolResult.Status.SKIPPED,
        summary={"skip_reason": "not_applicable", "measurement": "not_measured", "tool_applicable": False},
    )
    # A completed result the runner marked not_measured is not a measurement either.
    _result(run, user, "jscpd", summary={"measurement": "not_measured"}, analysis=radon.run)
    body = quality_metrics(run, user)
    tools = {t["tool_slug"]: t for t in body["tools"]}
    assert tools["radon"]["measured"] is False
    assert tools["radon"]["skip_reason"] == "not_applicable"
    assert tools["radon"]["metrics"] == {}
    assert tools["jscpd"]["measured"] is False
    assert body["measured"] == 0
