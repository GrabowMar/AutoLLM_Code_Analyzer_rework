from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerWorkspace
from backend.analysis.services import runner
from backend.analysis.tests.factories import AnalyzerToolFactory

pytestmark = pytest.mark.django_db


def test_list_tools(api_client):
    AnalyzerToolFactory(slug="bandit", name="Bandit", is_enabled=True)
    AnalyzerToolFactory(slug="hidden", is_enabled=False)
    resp = api_client.get("/api/analyzers/tools/")
    assert resp.status_code == 200
    slugs = {t["slug"] for t in resp.json()}
    assert "bandit" in slugs
    assert "hidden" not in slugs


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
