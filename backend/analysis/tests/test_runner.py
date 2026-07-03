from __future__ import annotations

import json

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.services import runner
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import AnalyzerWorkspaceFactory

pytestmark = pytest.mark.django_db


def test_execute_container_tool(monkeypatch, user):
    # Non-seeded slug: the factory's django_get_or_create=("slug",) would
    # silently return the auto-seeded "bandit" row and discard the overrides.
    AnalyzerToolFactory(
        slug="bandit-fixture",
        kind="container",
        parser_key="bandit",
        run_cmd="bandit -r {target} -f json",
    )
    workspace = AnalyzerWorkspaceFactory(user=user)
    run = AnalysisRunFactory(
        created_by=user,
        workspace=workspace,
        tool_slugs=["bandit-fixture"],
        source_code={"backend": "import hashlib"},
    )

    monkeypatch.setattr(
        runner.workspace_service,
        "require_ready_container",
        lambda ws: "cname",
    )
    monkeypatch.setattr(
        runner.docker_manager,
        "copy_files_in",
        lambda *a, **k: {"status": "ok"},
    )
    bandit_out = json.dumps(
        {
            "results": [
                {
                    "filename": "backend.py",
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "issue_text": "md5",
                    "test_id": "B303",
                    "line_number": 1,
                },
            ],
        },
    )
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda *a, **k: {"exit_code": 1, "output": bandit_out},
    )

    runner.execute(run)
    run.refresh_from_db()

    assert run.status == AnalysisRun.Status.COMPLETED
    assert run.summary["total_findings"] == 1
    assert Finding.objects.filter(result__run=run, severity="high").count() == 1
    assert run.duration_seconds is not None


def test_execute_metrics_tool(monkeypatch, user):
    AnalyzerToolFactory(
        slug="radon-fixture",
        kind="container",
        parser_key="radon",
        run_cmd="radon cc {target} -j",
    )
    workspace = AnalyzerWorkspaceFactory(user=user)
    run = AnalysisRunFactory(
        created_by=user,
        workspace=workspace,
        tool_slugs=["radon-fixture"],
        source_code={"backend": "def f(): pass"},
    )

    monkeypatch.setattr(runner.workspace_service, "require_ready_container", lambda ws: "cname")
    monkeypatch.setattr(runner.docker_manager, "copy_files_in", lambda *a, **k: {"status": "ok"})
    radon_out = json.dumps(
        {
            "backend.py": [
                {"type": "function", "name": "simple", "rank": "B", "complexity": 6, "lineno": 1},
                {"type": "function", "name": "monster", "rank": "F", "complexity": 41, "lineno": 10},
            ],
        },
    )
    monkeypatch.setattr(
        runner.docker_manager,
        "exec_in",
        lambda *a, **k: {"exit_code": 0, "output": radon_out},
    )

    runner.execute(run)
    run.refresh_from_db()

    assert run.status == AnalysisRun.Status.COMPLETED
    result = run.results.get()
    assert result.metrics["max_complexity"] == 41
    assert result.metrics["total_blocks"] == 2
    assert result.metrics["rank_distribution"]["F"] == 1
    # Only the F-rank block surfaces as a finding; the B block is metric data.
    assert Finding.objects.filter(result__run=run).count() == 1
    assert run.summary["metrics_by_tool"]["radon-fixture"]["average_complexity"] == 23.5


def test_execute_skips_unknown_tool(monkeypatch, user):
    run = AnalysisRunFactory(created_by=user, tool_slugs=["ghost"], source_code={"backend": "x"})
    runner.execute(run)
    run.refresh_from_db()
    result = run.results.get()
    assert result.status == "skipped"


def test_materialize_scaffolding_keys():
    """Scaffolding blobs get sensible filenames/extensions (JS frontend != .py)."""
    files = runner._materialize(
        {"backend_code": "import os\n", "frontend_code": "const x = 1;\n", "content": "x = 1\n"},
    )
    assert set(files) == {"backend_code.py", "frontend_code.jsx", "content.py"}


def test_materialize_preserves_real_paths():
    """Copilot file maps keep their true relative paths/extensions."""
    files = runner._materialize(
        {"frontend/src/App.jsx": "x", "backend/app.py": "y", "/leading/slash.py": "z"},
    )
    assert "frontend/src/App.jsx" in files
    assert "backend/app.py" in files
    assert "leading/slash.py" in files  # leading slash stripped


def test_get_code_for_analysis_from_scaffolding_job(user):
    from backend.generation.tests.factories import GenerationJobFactory

    job = GenerationJobFactory(
        created_by=user,
        result_data={
            "backend_code": "import os\n",
            "frontend_code": "const x = 1;\n",
            "backend_truncated": False,
            "backend_files": 3,
        },
    )
    run = AnalysisRunFactory(created_by=user, source_code=None, generation_job=job)
    code = run.get_code_for_analysis()
    assert code == {"backend_code": "import os\n", "frontend_code": "const x = 1;\n"}
    # And materialization yields proper extensions end to end.
    files = runner._materialize(code)
    assert set(files) == {"backend_code.py", "frontend_code.jsx"}


def test_get_code_for_analysis_prefers_copilot_files(user):
    from backend.generation.tests.factories import GenerationJobFactory

    job = GenerationJobFactory(
        created_by=user,
        result_data={
            "content": "ignored blob",
            "backend_code": "ignored blob",
            "files": {"backend/app.py": "print('hi')", "frontend/src/App.jsx": "x"},
        },
    )
    run = AnalysisRunFactory(created_by=user, source_code=None, generation_job=job)
    code = run.get_code_for_analysis()
    assert code == {"backend/app.py": "print('hi')", "frontend/src/App.jsx": "x"}


def test_execute_ai_tool(monkeypatch, user):
    AnalyzerToolFactory(slug="llm_review", kind="ai", parser_key="")
    run = AnalysisRunFactory(created_by=user, tool_slugs=["llm_review"], source_code={"backend": "x"})

    from backend.analysis.services.base import AnalyzerOutput
    from backend.analysis.services.base import FindingData

    monkeypatch.setattr(
        runner.ai_runner,
        "run",
        lambda *a, **k: AnalyzerOutput(
            findings=[FindingData(severity="medium", category="quality", title="ai")],
        ),
    )
    runner.execute(run)
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.COMPLETED
    assert Finding.objects.filter(result__run=run).count() == 1
