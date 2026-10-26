from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerWorkspace
from backend.analysis.services import runner
from backend.analysis.tests.factories import AnalyzerToolFactory

pytestmark = pytest.mark.django_db


def test_list_tools(api_client):
    # Non-seeded slugs so the factory overrides actually apply (the catalog
    # is auto-seeded by post_migrate).
    AnalyzerToolFactory(slug="visible-tool", name="Visible", is_enabled=True)
    AnalyzerToolFactory(slug="hidden", is_enabled=False)
    resp = api_client.get("/api/analyzers/tools/")
    assert resp.status_code == 200
    slugs = {t["slug"] for t in resp.json()}
    assert "visible-tool" in slugs
    assert "hidden" not in slugs


def test_tools_expose_mode_and_technologies(api_client):
    resp = api_client.get("/api/analyzers/tools/zap/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["analysis_mode"] == "dynamic"
    assert "http" in body["technologies"]


def test_catalog_meta(api_client):
    resp = api_client.get("/api/analyzers/catalog/")
    assert resp.status_code == 200
    body = resp.json()
    assert {m["slug"] for m in body["modes"]} == {"static", "dynamic"}
    assert {"python", "docker", "http"} <= {t["slug"] for t in body["technologies"]}
    assert all(t["group"] for t in body["technologies"])
    assert body["categories"][0]["slug"] == "security"


def test_get_workspace_creates_record(api_client, user):
    resp = api_client.get("/api/analyzers/workspace/")
    assert resp.status_code == 200
    assert resp.json()["status"] in {"absent", "ready", "provisioning", "stopped", "error"}
    assert AnalyzerWorkspace.objects.filter(user=user).exists()


def test_create_run(api_client, monkeypatch):
    AnalyzerToolFactory(slug="bandit")
    monkeypatch.setattr(runner, "dispatch", lambda run_id: None)
    resp = api_client.post(
        "/api/analysis/runs/",
        data={"tool_slugs": ["bandit"], "source_code": {"backend": "print(1)"}},
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["status"] == "pending"
    assert AnalysisRun.objects.filter(id=body["id"]).exists()


def test_create_run_requires_tools(api_client):
    resp = api_client.post(
        "/api/analysis/runs/",
        data={"tool_slugs": [], "source_code": {"backend": "x"}},
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_run_detail_exposes_tool_metrics(api_client, user):
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.analysis.tests.factories import ToolResultFactory

    run = AnalysisRunFactory(created_by=user)
    ToolResultFactory(
        run=run,
        tool_slug="radon",
        status="completed",
        metrics={"average_complexity": 3.5, "max_complexity": 12},
    )
    resp = api_client.get(f"/api/analysis/runs/{run.id}/")
    assert resp.status_code == 200
    result = resp.json()["results"][0]
    assert result["metrics"] == {"average_complexity": 3.5, "max_complexity": 12}


def test_list_runs_scoped_to_user(api_client, user):
    from backend.analysis.tests.factories import AnalysisRunFactory
    from backend.users.tests.factories import UserFactory

    AnalysisRunFactory(created_by=user)
    AnalysisRunFactory(created_by=UserFactory())
    resp = api_client.get("/api/analysis/runs/")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_install_tool_dispatches(api_client, monkeypatch):
    AnalyzerToolFactory(slug="ruff")
    monkeypatch.setattr(
        "backend.analysis.api.views.installed.dispatch_in_thread",
        lambda *a, **k: None,
    )
    resp = api_client.post(
        "/api/analyzers/workspace/tools/",
        data={"tool_slug": "ruff"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["tool_slug"] == "ruff"
    assert resp.json()["status"] == "installing"


def _job_create(api_client, job_id):
    return api_client.post(
        "/api/analysis/runs/",
        data={"tool_slugs": ["bandit"], "generation_job_id": str(job_id)},
        content_type="application/json",
    )


def test_create_run_rejects_another_users_job(api_client, user, monkeypatch):
    from backend.generation.tests.factories import GenerationJobFactory
    from backend.users.tests.factories import UserFactory

    monkeypatch.setattr(runner, "dispatch", lambda run_id: None)
    foreign = GenerationJobFactory(created_by=UserFactory())
    assert _job_create(api_client, foreign.id).status_code == 404
    assert _job_create(api_client, "not-a-uuid").status_code == 404
    assert not AnalysisRun.objects.exists()

    own = GenerationJobFactory(created_by=user)
    assert _job_create(api_client, own.id).status_code == 200


def test_staff_may_analyze_any_job(api_client, user, monkeypatch):
    from backend.generation.tests.factories import GenerationJobFactory
    from backend.users.tests.factories import UserFactory

    monkeypatch.setattr(runner, "dispatch", lambda run_id: None)
    user.is_staff = True
    user.save()
    foreign = GenerationJobFactory(created_by=UserFactory())
    assert _job_create(api_client, foreign.id).status_code == 200


def test_cancel_endpoint_marks_cancelled(api_client, user):
    from backend.analysis.tests.factories import AnalysisRunFactory

    run = AnalysisRunFactory(created_by=user, status="running")
    resp = api_client.post(f"/api/analysis/runs/{run.id}/cancel/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.CANCELLED
    assert run.completed_at is not None
