"""Statistics page aggregates: measured scans, finished-work success rates, source LOC."""

from __future__ import annotations

import pytest

from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.statistics import services
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

S = GenerationJob.Status


def test_tool_scans_count_only_executions_that_measured_something():
    user = UserFactory()

    def result(tool, status, **kw):
        run = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.COMPLETED)
        return ToolResultFactory(run=run, tool_slug=tool, status=status, **kw)

    FindingFactory.create_batch(4, result=result("zap", ToolResult.Status.COMPLETED))
    result("zap", ToolResult.Status.SKIPPED, summary={"skip_reason": "not_applicable"})
    result("zap", ToolResult.Status.FAILED)
    result("zap", ToolResult.Status.SKIPPED, summary={"skip_reason": "cancelled"})
    result("zap", ToolResult.Status.RUNNING)
    result("nuclei", ToolResult.Status.FAILED)

    tools = {t["name"]: t for t in services.get_tool_effectiveness(user)}

    zap = tools["zap"]
    assert (zap["scans"], zap["not_measured"], zap["findings"]) == (2, 2, 4)
    assert zap["avg_per_scan"] == 2.0
    # Nothing measured: no rate, not a rate of 0.
    assert tools["nuclei"]["scans"] == 0
    assert tools["nuclei"]["avg_per_scan"] is None


def test_success_rates_count_only_finished_work():
    user = UserFactory()
    model = LLMModelFactory()
    for status, n in ((S.COMPLETED, 3), (S.FAILED, 1), (S.PENDING, 2), (S.RUNNING, 1), (S.CANCELLED, 2)):
        GenerationJobFactory.create_batch(n, created_by=user, model=model, status=status)
    for status in (
        AnalysisRun.Status.COMPLETED,
        AnalysisRun.Status.PARTIAL,
        AnalysisRun.Status.CANCELLED,
        AnalysisRun.Status.PENDING,
    ):
        AnalysisRunFactory(created_by=user, status=status)

    overview = services.get_system_overview(user)
    codegen = services.get_code_generation_stats(user)
    row = next(r for r in services.get_model_comparison(user) if r["model_id"] == model.model_id)

    assert overview["apps_success_rate"] == 75.0
    assert overview["apps_cancelled"] == 2
    assert overview["analyses_success_rate"] == 50.0  # 1 completed of completed + partial
    assert (overview["analyses_partial"], overview["analyses_cancelled"]) == (1, 1)
    assert codegen["success_rate"] == 75.0
    assert (codegen["pending"], codegen["cancelled"]) == (2, 2)
    assert row["success_rate"] == 75.0
    assert row["apps_failed"] == 1


def test_success_rate_is_unknown_before_anything_finished():
    user = UserFactory()
    model = LLMModelFactory()
    GenerationJobFactory.create_batch(2, created_by=user, model=model, status=S.PENDING)

    assert services.get_system_overview(user)["apps_success_rate"] is None
    assert services.get_system_overview(user)["analyses_success_rate"] is None
    assert services.get_code_generation_stats(user)["success_rate"] is None
    row = next(r for r in services.get_model_comparison(user) if r["model_id"] == model.model_id)
    assert row["success_rate"] is None


def test_lines_of_code_are_source_loc():
    user = UserFactory()
    files = {"app.py": "x = 1\n" * 100, "frontend/package-lock.json": "{}\n" * 5000}
    GenerationJobFactory(
        created_by=user,
        status=S.COMPLETED,
        result_data={"files": files},
        metrics={"lines_of_code": 5100},
    )
    # A legacy job without stored code falls back to the generation-time count.
    GenerationJobFactory(created_by=user, status=S.COMPLETED, result_data={}, metrics={"lines_of_code": 40})

    assert services.get_code_generation_stats(user)["total_lines_of_code"] == 140


def test_top_findings_carry_the_tools_own_label_and_tool():
    """P1: one row per (title, tool, the tool's label); the normalized severity is never the shown level."""
    user = UserFactory()
    run = AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.COMPLETED)
    bandit = ToolResultFactory(run=run, tool_slug="bandit")
    semgrep = ToolResultFactory(run=run, tool_slug="semgrep")
    pip_audit = ToolResultFactory(run=run, tool_slug="pip-audit")
    # One rule printed with two labels: two rows, each with its own label.
    FindingFactory.create_batch(3, result=bandit, title="Use of assert", severity="low", tool_severity="LOW")
    FindingFactory.create_batch(2, result=bandit, title="Use of assert", severity="low", tool_severity="MEDIUM")
    # Same title from another tool stays apart.
    FindingFactory(result=semgrep, title="Use of assert", severity="medium", tool_severity="WARNING")
    # A tool that rates nothing: no label, whatever the normalized severity says.
    FindingFactory.create_batch(
        4,
        result=pip_audit,
        title="werkzeug: CVE-1",
        category="dependency",
        severity="medium",
        tool_severity="",
    )

    rows = services.get_top_findings(10, user)

    assert [(r["title"], r["tool"], r["tool_severity"], r["count"]) for r in rows] == [
        ("werkzeug: CVE-1", "pip-audit", "", 4),
        ("Use of assert", "bandit", "LOW", 3),
        ("Use of assert", "bandit", "MEDIUM", 2),
        ("Use of assert", "semgrep", "WARNING", 1),
    ]
    assert all("severity" not in r for r in rows)
    # P2: the advisory row is marked as such, the code issues are not.
    assert [r["dependency_advisory"] for r in rows] == [True, False, False, False]


def test_average_durations_are_unknown_until_something_completed():
    user = UserFactory()
    GenerationJobFactory(created_by=user, status=S.FAILED, duration_seconds=10)
    AnalysisRunFactory(created_by=user, status=AnalysisRun.Status.FAILED, duration_seconds=5)

    assert services.get_system_overview(user)["avg_analysis_duration_seconds"] is None
    assert services.get_code_generation_stats(user)["avg_duration_seconds"] is None

    timed = UserFactory()
    GenerationJobFactory(created_by=timed, status=S.COMPLETED, duration_seconds=12.34)
    AnalysisRunFactory(created_by=timed, status=AnalysisRun.Status.COMPLETED, duration_seconds=0)

    # A measured 0 s stays 0, not "unknown".
    assert services.get_system_overview(timed)["avg_analysis_duration_seconds"] == 0.0
    assert services.get_code_generation_stats(timed)["avg_duration_seconds"] == 12.3


def test_model_comparison_leaves_unmeasured_counts_and_duration_empty():
    user = UserFactory()
    model = LLMModelFactory()
    GenerationJobFactory(created_by=user, model=model, status=S.FAILED)

    row = next(r for r in services.get_model_comparison(user) if r["model_id"] == model.model_id)

    assert row["findings"] is None
    assert row["avg_duration_seconds"] is None
