"""Shop coverage: technologies in generated apps and which tools fit them."""

from __future__ import annotations

import json

import pytest

from backend.analysis.models import ToolResult
from backend.analysis.services import runner
from backend.analysis.services.coverage import app_coverage
from backend.analysis.services.coverage import detect_technologies
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import AnalyzerWorkspaceFactory
from backend.analysis.tests.factories import InstalledToolFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory

pytestmark = pytest.mark.django_db

FLASK_REACT = {
    "backend/app.py": "from flask import Flask\napp = Flask(__name__)\n",
    "backend/requirements.txt": "flask==3.0.0\n",
    "backend/templates/index.html": "<p>{{ name }}</p>",
    "frontend/package.json": json.dumps({"dependencies": {"react": "18.2.0"}}),
    "frontend/src/App.jsx": "import React from 'react'\nexport default () => <div/>\n",
    "frontend/src/index.css": "body{}",
}


def test_detect_flask_react_app():
    assert detect_technologies(FLASK_REACT) == {
        "python",
        "flask",
        "pypi",
        "html",
        "jinja",
        "npm",
        "javascript",
        "react",
        "css",
        "http",
        "rest-api",
        "generic",
    }


def test_detect_fastapi_vue_and_infra():
    files = {
        "main.py": "from fastapi import FastAPI\n",
        "web/App.vue": "<template><div/></template>",
        "Dockerfile": "FROM python:3.12\n",
        "deploy.sh": "echo hi",
        "k8s/app.yaml": "apiVersion: v1\nkind: Service\n",
    }
    techs = detect_technologies(files)
    assert {"fastapi", "vue", "javascript", "docker", "shell", "kubernetes", "rest-api"} <= techs
    assert "flask" not in techs


def test_detect_empty_map():
    assert detect_technologies({}) == set()


def test_app_coverage_counts_apps_and_tool_fit(user):
    GenerationJobFactory(created_by=user, status=GenerationJob.Status.COMPLETED, result_data={"files": FLASK_REACT})
    GenerationJobFactory(
        created_by=user,
        status=GenerationJob.Status.COMPLETED,
        result_data={"files": {"main.py": "from fastapi import FastAPI\n"}},
    )
    # Not counted: failed, or someone else's.
    GenerationJobFactory(created_by=user, status=GenerationJob.Status.FAILED, result_data={"files": FLASK_REACT})
    GenerationJobFactory(status=GenerationJob.Status.COMPLETED, result_data={"files": FLASK_REACT})

    cov = app_coverage(user)

    assert cov["apps_total"] == 2
    assert cov["technologies"]["python"] == 2
    assert cov["technologies"]["react"] == 1
    assert cov["technologies"]["fastapi"] == 1
    assert cov["tools"]["ruff"] == 2
    assert cov["tools"]["eslint"] == 1
    assert cov["tools"]["hadolint"] == 0


def test_coverage_endpoint(api_client):
    resp = api_client.get("/api/analyzers/coverage/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["apps_total"] == 0
    assert "ruff" in body["tools"]


def test_results_record_installed_tool_version(user, monkeypatch):
    tool = AnalyzerToolFactory(slug="ruff-fixture", parser_key="ruff", run_cmd="ruff {target}")
    workspace = AnalyzerWorkspaceFactory(user=user)
    InstalledToolFactory(workspace=workspace, tool=tool, installed_version="ruff 0.6.9")
    run = AnalysisRunFactory(
        created_by=user,
        workspace=workspace,
        tool_slugs=["ruff-fixture"],
        source_code={"a.py": "x = 1"},
    )
    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(runner.docker_manager, "exec_in", lambda *a, **k: {"exit_code": 0, "output": "[]"})

    runner.execute(run)

    assert ToolResult.objects.get(run=run).summary["tool_version"] == "ruff 0.6.9"
