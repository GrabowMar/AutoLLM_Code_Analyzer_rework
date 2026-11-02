"""Application-page insights: code profile, phase timeline and quality metrics (read-only, derived on read)."""

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
from backend.generation.models import GenerationJob
from backend.generation.services.code_profile import code_profile
from backend.generation.services.code_profile import file_layer
from backend.generation.tests.factories import ExperimentConditionFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.reports.services.loc import loc_from_result_data
from backend.runtime.models import Stack
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


@app.post("/api/notes")
def create_note():
    return {}, 201
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

LOCKFILE = json.dumps({"name": "fe", "lockfileVersion": 3, "packages": {"": {}}}, indent=2)

PACKAGE_JSON = json.dumps(
    {"dependencies": {"react": "^18.2.0", "axios": "1.6.8", "zod": "^3.0.0"}, "devDependencies": {"vite": "^5.0"}},
)
REQUIREMENTS = "Flask==3.0.0\nrequests==2.31.0\n"

AGENT_FILES = {
    "app.py": APP_PY,
    "requirements.txt": REQUIREMENTS,
    "frontend/package.json": PACKAGE_JSON,
    "frontend/package-lock.json": LOCKFILE,
    "frontend/src/App.jsx": APP_JSX,
    "frontend/src/components/NoteCard.jsx": "export function NoteCard() { return null; }\n",
    "frontend/src/pages/Home.jsx": "const Home = () => null;\nexport default Home;\n",
    "frontend/dist/app.min.js": "var a=1;\n",
    "tests/test_api.py": "def test_ok():\n    assert True\n",
    "README.md": "# Notes\n\nSome docs\n",
    ".tmp/rr/package/index.js": "module.exports = 1;\nmodule.exports.b = 2;\n",
    "frontend/node_modules/x/index.js": "x\n",
}
MANIFEST = {
    "app.py": {"origin": "modified", "size": 1, "sha256": "a"},
    "requirements.txt": {"origin": "modified", "size": 1, "sha256": "b"},
    "frontend/package.json": {"origin": "scaffold", "size": 1, "sha256": "c"},
    "frontend/package-lock.json": {"origin": "added", "size": 1, "sha256": "d"},
    "frontend/src/App.jsx": {"origin": "modified", "size": 1, "sha256": "e"},
    "frontend/src/components/NoteCard.jsx": {"origin": "added", "size": 1, "sha256": "f"},
    "frontend/src/pages/Home.jsx": {"origin": "added", "size": 1, "sha256": "g"},
    "tests/test_api.py": {"origin": "added", "size": 1, "sha256": "h"},
    "README.md": {"origin": "scaffold", "size": 1, "sha256": "i"},
    ".tmp/rr/package/index.js": {"origin": "added", "size": 1, "sha256": "j"},
    "frontend/node_modules/x/index.js": {"origin": "added", "size": 1, "sha256": "k"},
}
SCAFFOLD_FILES = {
    "requirements.txt": "Flask==3.0.0\nbcrypt==4.1.2\n",
    "frontend/package.json": json.dumps(
        {"dependencies": {"react": "^18.2", "axios": "^1.6.0"}, "devDependencies": {"vite": "^5.0.0"}},
    ),
}


@pytest.fixture
def stack():
    return Stack.objects.create(slug="profile-stack", version=1, files=SCAFFOLD_FILES)


def _agent_job(user=None, **kw):
    started = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
    result = {
        "files": dict(AGENT_FILES),
        "agent": {
            "harness": "opencode",
            "wall_seconds": 600.0,
            "llm_seconds": 200.0,
            "workspace": {
                "manifest": MANIFEST,
                "origins": {"added": 6, "modified": 3, "scaffold": 2},
                "deleted_scaffold_files": ["frontend/postcss.config.js"],
                "protected_modified": [],
                "skipped": [{"path": ".tmp/big.tgz", "reason": "too_large", "size": 9, "origin": "added"}],
            },
            "schedule": {"queue_seconds": 3600.0, "run_order": 4, "queued_at": "2026-11-02T06:00:00+00:00"},
        },
        "evaluation": {
            "status": "passed",
            "seconds": 50.0,
            "build": {"ok": True, "attempts": [{"ok": True, "seconds": 40.0}], "image_bytes": 1000},
            "resolved_python_deps": ["Flask==3.0.0", "Werkzeug==3.0.1", "requests==2.31.0"],
        },
    }
    defaults = {
        "mode": GenerationJob.Mode.AGENT,
        "status": GenerationJob.Status.COMPLETED,
        "stack_slug": "profile-stack",
        "resolved_bundle": {"stack": {"slug": "profile-stack", "version": 1}},
        "result_data": result,
        "metrics": {"lines_of_code": sum(t.count("\n") + 1 for t in AGENT_FILES.values())},
        "started_at": started,
        "completed_at": started + timedelta(seconds=665),
        "duration_seconds": 665.0,
        "execution": {
            "queued_at": "2026-11-02T06:00:00+00:00",
            "claimed_at": started.isoformat(),
            "queue_seconds": 3600.0,
            "llm_queue_wait_s": 3.0,
            "policy": "interleaved",
            "attempts": [{"attempt": 0, "reason": "infra_error", "error": "boom" * 200, "cost": 0.5}],
            "prior_attempts_cost": 0.5,
        },
        "attempt": 1,
    }
    defaults.update(kw)
    if user is not None:
        defaults["created_by"] = user
    job = GenerationJobFactory(**defaults)
    # created_at is auto_now_add: place it an hour before the start.
    GenerationJob.objects.filter(id=job.id).update(created_at=started - timedelta(hours=1))
    job.refresh_from_db()
    return job


# ── code profile ─────────────────────────────────────────────────────────


def test_file_layer_follows_loc_classifiers():
    assert file_layer("app.py") == "backend"
    assert file_layer("frontend/src/App.jsx") == "frontend"
    assert file_layer("static/app.js") == "frontend"  # web assets are frontend anywhere
    assert file_layer("tests/test_api.py") == "tests"
    assert file_layer("frontend/package-lock.json") == "config"
    assert file_layer("frontend/dist/app.min.js") == "config"  # minified bundle: not source
    assert file_layer(".tmp/rr/package/index.js") == "excluded"
    assert file_layer("frontend/node_modules/x/index.js") == "excluded"


def test_code_profile_layers_add_up_to_study_loc(stack):
    job = _agent_job()
    profile = code_profile(job)
    loc = loc_from_result_data(job.result_data)

    assert profile["layout"] == "files"
    assert profile["loc"] == loc
    layers = {row["layer"]: row for row in profile["layers"]}
    assert layers["backend"]["source_loc"] == loc["backend_loc"]
    assert layers["frontend"]["source_loc"] == loc["frontend_loc"]
    assert layers["tests"]["source_loc"] == loc["test_loc"] == 2
    assert layers["config"]["source_loc"] is None
    assert layers["excluded"]["source_loc"] is None
    assert layers["excluded"]["files"] == 2
    assert sum(row["files"] for row in profile["layers"]) == len(AGENT_FILES)

    # All lines = metrics.lines_of_code's count, lockfile and scratch folders included.
    assert profile["all_lines"] == job.metrics["lines_of_code"]
    assert profile["all_lines"] > loc["total_loc"]
    assert profile["all_lines_ratio"] == round(profile["all_lines"] / loc["total_loc"], 2)

    langs = {row["language"]: row for row in profile["languages"]}
    assert langs["Python"]["files"] == 1  # tests/test_api.py is apart
    assert langs["JSX"]["files"] == 3
    assert sum(r["source_loc"] for r in profile["languages"]) == loc["total_loc"]

    roots = {g["root"]: g for g in profile["excluded"]}
    assert set(roots) == {".tmp", "frontend/node_modules"}
    kinds = {r["path"]: r["kind"] for r in profile["non_source_files"]}
    assert kinds["frontend/package-lock.json"] == "lockfile"
    assert kinds["frontend/package.json"] == "manifest"
    assert kinds["README.md"] == "docs"
    assert kinds["frontend/dist/app.min.js"] == "minified"


def test_code_profile_origins_and_structure(stack):
    profile = code_profile(_agent_job())
    origins = profile["origins"]
    assert origins["known"] is True
    assert origins["counts"] == {"added": 6, "modified": 3, "scaffold": 2}  # as the workspace recorded them
    assert origins["per_file"]["frontend/src/App.jsx"] == "modified"
    by_origin = {row["origin"]: row for row in origins["by_origin"]}
    assert by_origin["added"]["test_files"] == 1
    assert by_origin["modified"]["source_files"] == 2  # app.py + App.jsx
    assert sum(r["source_loc"] for r in origins["by_origin"]) == profile["loc"]["total_loc"]
    assert origins["deleted_scaffold_files"] == ["frontend/postcss.config.js"]
    assert origins["skipped"][0]["reason"] == "too_large"

    structure = profile["structure"]
    assert structure["approximate"] is True
    assert structure["models"] == 2
    assert structure["model_names"] == ["User", "Note"]
    assert structure["route_decorators"] == 3  # @app.route + @app.get + @app.post
    assert structure["frontend_component_files"] == 1
    assert structure["frontend_page_files"] == 1
    assert structure["component_definitions"] == 4  # App, NoteList, NoteCard, Home (not helper)


def test_code_profile_dependencies(stack):
    deps = code_profile(_agent_job())["dependencies"]
    assert deps["has_manifests"] is True
    assert deps["scaffold_stack"] == {"slug": "profile-stack", "pinned_version": 1, "version": 1, "source": "pinned"}
    assert deps["model_added"] == ["requests", "zod"]
    # ^18.2 == ^18.2.0 and ^5.0 == ^5.0.0 (same range): not re-pinned; 1.6.8 vs ^1.6.0 is.
    assert [r["package"] for r in deps["repinned"]] == ["axios"]
    assert deps["repinned"][0] == {"package": "axios", "spec": "1.6.8", "scaffold_spec": "^1.6.0", "ecosystem": "npm"}
    assert deps["dropped_scaffold"] == ["bcrypt"]
    status = {r["package"]: (r["status"], r["ecosystem"]) for r in deps["declared"]}
    assert status["flask"] == ("scaffold", "pypi")
    assert status["requests"] == ("added", "pypi")
    assert status["axios"] == ("repinned", "npm")
    assert status["react"] == ("scaffold", "npm")
    resolved = {r["package"]: r for r in deps["resolved_python"]}
    assert resolved["Werkzeug"] == {"package": "Werkzeug", "version": "3.0.1", "declared": False, "declared_by": None}
    assert resolved["Flask"]["declared"] is True
    assert resolved["Flask"]["declared_by"] == "app"
    assert deps["counts"] == {
        "declared": 6,
        "scaffold": 5,
        "model_added": 2,
        "repinned": 1,
        "dropped_scaffold": 1,
        "resolved_python": 3,
    }


def test_code_profile_scaffold_falls_back_to_latest_stack_version(stack):
    job = _agent_job(resolved_bundle={"stack": {"slug": "profile-stack", "version": 9}})
    assert code_profile(job)["dependencies"]["scaffold_stack"]["source"] == "latest"


def test_code_profile_agent_run_without_manifest_has_unknown_origins(stack):
    job = _agent_job()
    job.result_data["agent"].pop("workspace")
    profile = code_profile(job)
    assert profile["origins"] == {"known": False, "reason": "no_manifest"}
    assert any("predates the workspace manifest" in n for n in profile["notes"])


def test_code_profile_template_job_without_manifests(stack):
    job = GenerationJobFactory(
        mode=GenerationJob.Mode.SCAFFOLDING,
        stack_slug="profile-stack",
        result_data={
            "files": {"app.py": APP_PY, "frontend/src/App.jsx": APP_JSX},
            "backend_dependencies": ["flask", "pyjwt"],
        },
    )
    profile = code_profile(job)
    assert profile["origins"] == {"known": False, "reason": "not_agent"}
    deps = profile["dependencies"]
    assert deps["has_manifests"] is False
    # Nothing declared by the app itself: added / re-pinned are unknown, not 0.
    assert deps["model_added"] is None
    assert deps["repinned"] is None
    assert deps["counts"]["declared"] is None
    assert deps["counts"]["scaffold"] == 5
    assert deps["reported_by_generator"] == ["flask", "pyjwt"]
    assert profile["loc"]["test_loc"] == 0


def test_code_profile_template_job_resolved_deps_follow_the_scaffold_manifests(stack):
    """Template images install the scaffold's manifests: Flask is declared (by the scaffold), not transitive."""
    job = GenerationJobFactory(
        mode=GenerationJob.Mode.SCAFFOLDING,
        stack_slug="profile-stack",
        result_data={
            "files": {"app.py": APP_PY, "frontend/src/App.jsx": APP_JSX},
            "evaluation": {
                "build": {"healing": {"added_requirements": ["Flask-Cors>=4.0"]}},
                "resolved_python_deps": ["Flask==3.0.0", "bcrypt==4.1.2", "flask_cors==4.0.1", "Werkzeug==3.0.1"],
            },
        },
    )
    resolved = {r["package"]: r for r in code_profile(job)["dependencies"]["resolved_python"]}
    assert resolved["Flask"]["declared_by"] == "scaffold"
    assert resolved["bcrypt"]["declared_by"] == "scaffold"
    assert resolved["flask_cors"]["declared_by"] == "healing"
    assert resolved["Werkzeug"] == {"package": "Werkzeug", "version": "3.0.1", "declared": False, "declared_by": None}


def test_code_profile_stage_blobs_and_single_blob():
    blobs = GenerationJobFactory(
        mode=GenerationJob.Mode.SCAFFOLDING,
        result_data={"backend_code": APP_PY, "frontend_code": APP_JSX},
    )
    profile = code_profile(blobs)
    assert profile["layout"] == "stage_blobs"
    assert profile["loc"] == loc_from_result_data(blobs.result_data)
    assert profile["loc"]["test_loc"] is None
    assert {r["layer"]: r["source_loc"] for r in profile["layers"]}["tests"] is None

    blob = GenerationJobFactory(result_data={"content": "```python\nprint(1)\n```\n"})
    profile = code_profile(blob)
    assert profile["layout"] == "single_blob"
    assert profile["loc"]["total_loc"] is None  # unmeasured, never 0
    assert profile["all_lines"] == 4
    assert profile["layers"] == []
    assert profile["structure"] is None

    empty = GenerationJobFactory(result_data={})
    profile = code_profile(empty)
    assert profile["layout"] == "none"
    assert profile["all_lines"] is None
    assert profile["loc"]["total_loc"] is None


# ── endpoints: access ────────────────────────────────────────────────────


@pytest.mark.parametrize("endpoint", ["code-profile", "timeline", "quality-metrics"])
def test_insight_endpoints_are_owner_only(client, stack, endpoint):
    owner = UserFactory()
    job = _agent_job(user=owner)

    assert client.get(f"{API}/jobs/{job.id}/{endpoint}/").status_code == 401

    client.force_login(UserFactory())
    assert client.get(f"{API}/jobs/{job.id}/{endpoint}/").status_code == 404

    client.force_login(owner)
    assert client.get(f"{API}/jobs/not-a-uuid/{endpoint}/").status_code == 404
    res = client.get(f"{API}/jobs/{job.id}/{endpoint}/")
    assert res.status_code == 200
    assert res.json()["job_id"] == str(job.id)


def test_code_profile_endpoint(client, stack):
    user = UserFactory()
    job = _agent_job(user=user)
    client.force_login(user)
    body = client.get(f"{API}/jobs/{job.id}/code-profile/").json()
    assert body["loc"]["total_loc"] == loc_from_result_data(job.result_data)["total_loc"]
    assert body["origins"]["per_file"]["app.py"] == "modified"


# ── timeline ─────────────────────────────────────────────────────────────


def test_timeline_agent_job_phases(client, stack):
    user = UserFactory()
    job = _agent_job(user=user)
    client.force_login(user)
    body = client.get(f"{API}/jobs/{job.id}/timeline/").json()

    segments = {s["key"]: s for s in body["segments"]}
    assert list(segments) == ["queue", "generation", "other", "evaluation"]
    assert segments["queue"]["seconds"] == 3600.0
    assert segments["queue"]["note"] == "Includes earlier attempts."  # attempt 1
    assert {p["key"]: p["seconds"] for p in segments["queue"]["parts"]} == {"scheduler_queue": 3600.0}
    gen = segments["generation"]
    assert gen["seconds"] == 600.0
    # The endpoint-slot wait accrues during the calls: a part of the agent run, not of the queue.
    assert [(p["key"], p["seconds"]) for p in gen["parts"]] == [
        ("llm", 200.0),
        ("llm_slot_wait", 3.0),
        ("tools_other", 400.0),
    ]
    assert gen["trajectory_time"] is None
    # 665 s from start to finish = 600 agent + 50 evaluation + 15 residual.
    assert segments["other"]["seconds"] == 15.0
    assert segments["other"]["approximate"] is True
    ev = segments["evaluation"]
    assert ev["seconds"] == 50.0
    assert {p["key"]: p["seconds"] for p in ev["parts"]} == {"build": 40.0, "tests": 10.0}
    assert ev["end"] == job.completed_at.isoformat()
    assert body["evaluation_in_run"] is True
    assert body["total_seconds"] == 3600.0 + 665.0

    ex = body["execution"]
    assert ex["policy"] == "interleaved"
    assert ex["attempt"] == 1
    assert ex["run_order"] == 4
    assert ex["prior_attempts"] == {"cost": 0.5}
    assert ex["attempts"][0]["reason"] == "infra_error"
    assert len(ex["attempts"][0]["error"]) <= 301  # truncated
    assert body["experiment"] is None
    assert [m["key"] for m in body["milestones"]] == ["created", "queued", "claimed", "started", "finished"]


def test_timeline_reads_trajectory_time_split(stack):
    from backend.generation.api.views.job_insights import build_timeline

    job = _agent_job()
    job.result_data["agent"]["trajectory"] = {"time": {"llm_s": 190.5, "tools_s": 300.25, "label": "x"}}
    gen = next(s for s in build_timeline(job)["segments"] if s["key"] == "generation")
    assert gen["trajectory_time"] == {"llm_s": 190.5, "tools_s": 300.25}


def test_timeline_evaluation_outside_the_run_is_flagged():
    from backend.generation.api.views.job_insights import build_timeline

    started = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    job = GenerationJobFactory(
        mode=GenerationJob.Mode.SCAFFOLDING,
        status=GenerationJob.Status.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(seconds=215),
        metrics={"total_duration": 214.0, "backend_duration": 17.0, "frontend_duration": 197.0},
        result_data={"evaluation": {"seconds": 66.0, "build": {"attempts": [{"seconds": 62.0}]}}},
    )
    body = build_timeline(job)
    segments = {s["key"]: s for s in body["segments"]}
    assert {p["key"]: p["seconds"] for p in segments["generation"]["parts"]} == {
        "backend_stage": 17.0,
        "frontend_stage": 197.0,
    }
    assert body["evaluation_in_run"] is False
    assert segments["evaluation"]["separate"] is True
    assert segments["evaluation"]["start"] is None
    assert "other" in segments  # 1 s residual without the evaluation
    assert any("no queue timestamps" in n for n in body["notes"])


def test_timeline_pre_executor_sandbox_wait_inside_the_run():
    """Started on dispatch, then waited for a sandbox: the wait is its own segment, not part of 'Queued'."""
    from backend.generation.api.views.job_insights import build_timeline

    started = datetime(2026, 10, 22, 14, 56, 2, tzinfo=UTC)
    agent_start = started + timedelta(seconds=469.2)
    job = GenerationJobFactory(
        mode=GenerationJob.Mode.AGENT,
        status=GenerationJob.Status.COMPLETED,
        started_at=started,
        completed_at=started + timedelta(seconds=682.0),
        execution={},
        result_data={
            "agent": {
                "harness": "aider",
                "wall_seconds": 173.2,
                "llm_seconds": 166.9,
                "schedule": {
                    "queued_at": started.timestamp(),
                    "started_at": agent_start.timestamp(),
                    "queue_seconds": 469.18,
                },
            },
            "evaluation": {"seconds": 31.0, "build": {"attempts": [{"seconds": 28.3}]}},
        },
    )
    GenerationJob.objects.filter(id=job.id).update(created_at=started - timedelta(seconds=0.07))
    job.refresh_from_db()
    body = build_timeline(job)
    segments = {s["key"]: s for s in body["segments"]}
    assert list(segments) == ["queue", "sandbox_wait", "generation", "other", "evaluation"]
    # The recorded 469 s wait never sits inside a 0.07 s queue segment.
    assert segments["queue"]["parts"] == []
    wait = segments["sandbox_wait"]
    assert wait["seconds"] == 469.2
    assert wait["kind"] == "wait"
    assert [(p["seconds"], p["source"]) for p in wait["parts"]] == [
        (469.18, "result_data.agent.schedule.queue_seconds"),
    ]
    assert segments["generation"]["start"] == agent_start.isoformat()
    # 682 = 469.2 sandbox wait + 173.2 agent + 31 evaluation + 8.6 residual.
    assert segments["other"]["seconds"] == 8.6
    assert body["evaluation_in_run"] is True
    # A queued timestamp (agent.schedule.queued_at) exists: no "no queue timestamps" note.
    assert not any("no queue timestamps" in n for n in body["notes"])
    assert "queued" in [m["key"] for m in body["milestones"]]


def test_timeline_cancelled_before_start_shows_its_queue_time():
    from backend.generation.api.views.job_insights import build_timeline

    created = datetime(2026, 10, 26, 10, 41, tzinfo=UTC)
    job = GenerationJobFactory(
        mode=GenerationJob.Mode.AGENT,
        status=GenerationJob.Status.CANCELLED,
        completed_at=created + timedelta(seconds=5721),
    )
    GenerationJob.objects.filter(id=job.id).update(created_at=created)
    job.refresh_from_db()
    segments = build_timeline(job)["segments"]
    assert [(s["key"], s["label"], s["seconds"], s["kind"]) for s in segments] == [
        ("queue", "Queued until cancelled", 5721.0, "wait"),
    ]
    assert segments[0]["end"] == job.completed_at.isoformat()
    assert segments[0]["source"] == "completed_at - created_at"


def test_timeline_running_and_failed_jobs_without_results():
    from backend.generation.api.views.job_insights import build_timeline

    started = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    failed = GenerationJobFactory(
        mode=GenerationJob.Mode.AGENT,
        status=GenerationJob.Status.FAILED,
        started_at=started,
        completed_at=started + timedelta(seconds=100),
    )
    run = next(s for s in build_timeline(failed)["segments"] if s["key"] == "run")
    assert run["seconds"] == 100.0
    assert run["label"] == "Run (no phase detail recorded)"

    running = GenerationJobFactory(
        mode=GenerationJob.Mode.AGENT,
        status=GenerationJob.Status.RUNNING,
        started_at=started,
    )
    body = build_timeline(running)
    run = next(s for s in body["segments"] if s["key"] == "run")
    assert run["approximate"] is True
    assert run["end"] is None
    assert body["live"] is True

    pending = GenerationJobFactory(status=GenerationJob.Status.PENDING)
    queue = build_timeline(pending)["segments"][0]
    assert queue["key"] == "queue"
    assert queue["end"] is None
    assert queue["seconds"] is not None


def test_timeline_experiment_cell(stack):
    from backend.generation.api.views.job_insights import build_timeline

    condition = ExperimentConditionFactory(label="", experiment__repeats=3)
    job = _agent_job(experiment=condition.experiment, condition=condition, repeat_index=1)
    cell = build_timeline(job)["experiment"]
    assert cell["experiment_name"] == condition.experiment.name
    assert cell["repeat_index"] == 1
    assert cell["repeat_number"] == 2
    assert cell["planned_repeats"] == 3
    assert cell["condition_label"].startswith(condition.model.model_name)
    assert cell["condition_method"] == condition.method_label()
    assert cell["cell_jobs"] == 1
    assert cell["is_current_in_cell"] is True


# ── quality metrics ──────────────────────────────────────────────────────


def _result(job, user, slug, *, status=ToolResult.Status.COMPLETED, metrics=None, summary=None, run=None):
    run = run or AnalysisRunFactory(generation_job=job, created_by=user, status=AnalysisRun.Status.COMPLETED)
    return ToolResultFactory(run=run, tool_slug=slug, status=status, metrics=metrics or {}, summary=summary or {})


def test_quality_metrics_latest_result_per_tool(client):
    user = UserFactory()
    job = GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED)
    first = _result(
        job,
        user,
        "lizard",
        metrics={"avg_ccn": 2.5, "max_ccn": 9, "functions": 10, "total_nloc": 95},
        summary={"tool_version": "1.17.31", "parser_sha": "abc"},
    )
    # A later lizard result that measured nothing does not replace the measured one.
    _result(job, user, "lizard", status=ToolResult.Status.FAILED)
    _result(job, user, "jscpd", metrics={"duplication_percentage": 2.0, "clones": 1}, run=first.run)
    # A dependency scanner's counter is not a code metric; a performance-category tool's is.
    AnalyzerToolFactory(slug="osv-scanner", category="dependencies")
    AnalyzerToolFactory(slug="complexipy", name="Complexipy", category="performance")
    _result(job, user, "osv-scanner", metrics={"dev_only_advisories_skipped": 0}, run=first.run)
    _result(job, user, "complexipy", metrics={"cognitive_total": 41}, run=first.run)
    _result(job, user, "bandit", run=first.run)  # no metrics: not listed
    other_user_run = AnalysisRunFactory(generation_job=job, status=AnalysisRun.Status.COMPLETED)
    _result(
        job,
        user,
        "radon",
        status=ToolResult.Status.SKIPPED,
        summary={"skip_reason": "no_python"},
        run=other_user_run,
    )

    client.force_login(user)
    body = client.get(f"{API}/jobs/{job.id}/quality-metrics/").json()
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
    assert radon["measured"] is False  # skipped for a reason other than not_applicable
    assert radon["metrics"] == {}
    assert radon["skip_reason"] == "no_python"
    assert radon["run"]["accessible"] is False  # another user's run
    assert radon["run"]["name"] is None  # its name is not disclosed

    assert tools["jscpd"]["kind"] == "duplication"
    assert tools["complexipy"]["kind"] == "other"
    assert tools["complexipy"]["tool_name"] == "Complexipy"
    assert body["measured"] == 3


def test_quality_metrics_not_run():
    from backend.generation.api.views.job_insights import quality_metrics

    job = GenerationJobFactory()
    body = quality_metrics(job, job.created_by)
    assert body["measured"] == 0
    assert [(t["tool_slug"], t["status"], t["measured"]) for t in body["tools"]] == [
        ("lizard", "not_run", False),
        ("radon", "not_run", False),
        ("jscpd", "not_run", False),
    ]
