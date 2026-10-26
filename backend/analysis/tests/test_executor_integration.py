"""Executor + the real measure-stage modules (sample, normalize, dedup, measurement).

Only Docker, the tool adapters and the LLM reviewer are faked: this checks
the cross-stage data contracts (ToolResult coverage/metrics/summary shapes,
Finding rows, ReviewSample rows) that measurement reads at finalize.
"""

from __future__ import annotations

import pytest

from backend.analysis.engine import adapters
from backend.analysis.engine import executor
from backend.analysis.engine import measurement
from backend.analysis.engine import reviewer
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine.types import RawFinding
from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ToolResult
from backend.analysis.tests import executor_fakes as fx
from backend.credentials.services import resolver as credentials

APP = {
    "app.py": (
        "import os\n"
        "import subprocess\n"
        "\n"
        "def run(cmd):\n"
        "    os.system(cmd)\n"
        "    return subprocess.call(cmd, shell=True)\n"
    ),
    "package-lock.json": '{"lockfileVersion": 3}\n',
}


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.ANALYSIS_HEARTBEAT_THREAD = False
    settings.ANALYSIS_USE_CELERY = False


@pytest.fixture
def real_world(monkeypatch):
    try:
        sample_mod.resolve_inline({"a.py": "x = 1\n"}, dict(sample_mod.DEFAULT_SCOPE))
        measurement.outcomes({})
    except NotImplementedError:
        pytest.skip("measure stage not implemented yet")
    fake_sandbox = fx.FakeSandbox().install(monkeypatch)
    fake_sandbox.manifest = {"tools": {"bandit": {"version": "1.0"}, "ruff": {"version": "1.0"}}}
    registry = {
        "bandit": fx.FakeAdapter(
            "bandit",
            findings=[
                RawFinding(
                    rule_id="B605",
                    message="Starting a process with a shell",
                    file_path="app.py",
                    line=5,
                    native_severity="HIGH",
                    native_confidence="HIGH",
                    cwe=["CWE-78"],
                ),
            ],
        ),
        "ruff": fx.FakeAdapter(
            "ruff",
            findings=[
                RawFinding(rule_id="S602", message="subprocess call with shell=True", file_path="app.py", line=6),
            ],
        ),
    }
    monkeypatch.setattr(adapters, "get_adapter", registry.__getitem__)
    monkeypatch.setattr(executor, "_publish", lambda run_id, event: None)
    monkeypatch.setattr(executor, "enqueue", lambda run_id, *, countdown=0: None)
    review = {"outcome": fx.review_outcome(ok=3, findings_per_sample=1)}

    def run_review(**kwargs):
        return review["outcome"]

    monkeypatch.setattr(reviewer, "run_review", run_review)
    monkeypatch.setattr(credentials, "get_openrouter_key", lambda user, **kwargs: "sk-test")
    return fake_sandbox


def _plan(*, with_review: bool) -> dict:
    plan = fx.make_plan(
        [fx.tool_entry("bandit"), fx.tool_entry("ruff")],
        reviewer_cfg=fx.reviewer_entry() if with_review else None,
    )
    plan["scope"] = dict(sample_mod.DEFAULT_SCOPE)
    return plan


def test_a_run_through_the_real_measure_stage(user, real_world):
    run = executor.submit_run(user=user, plan=_plan(with_review=True), source_code=APP, dispatch=False)
    manifest_files = {f["path"]: f for f in run.sample_manifest["files"]}
    assert not manifest_files["package-lock.json"]["included"]  # lockfiles are excluded, never scanned
    executor.process_run(str(run.pk))
    run.refresh_from_db()

    assert run.status == AnalysisRun.Status.COMPLETED, run.error_message
    assert set(real_world.volumes[0]) == {"app.py"}
    static = Finding.objects.filter(run=run, is_ai=False)
    assert static.count() == 2
    assert all(f.fingerprint and f.issue_key for f in static)
    bandit = static.get(tool_slug="bandit")
    assert bandit.severity in ("high", "critical")
    assert bandit.cwe == ["CWE-78"]
    assert bandit.file_origin == "authored"

    m = run.measurement
    assert m["schema_version"] == 1
    assert m["plan_hash"] == run.plan_hash
    assert m["sample_hash"] == run.sample_hash
    assert m["complete"] is True
    assert m["tool_status"] == {"bandit": "succeeded", "ruff": "succeeded", "ai_review": "succeeded"}
    assert m["sloc"]["measured"] > 0
    assert m["static"]["available"] is True
    assert m["static"]["findings"]["total"] >= 1
    assert m["ai"]["available"] is True
    assert m["ai"]["samples_ok"] == 3
    assert m["coverage"]["bandit"]["files_in_scope"] == 1

    s = run.summary
    assert s["schema_version"] == 2
    assert s["complete"] is True
    assert s["tools"]["succeeded"] == 3
    assert s["total_findings"] == Finding.objects.filter(run=run, is_ai=False, counted=True).count()

    outcomes = measurement.outcomes(m)
    assert outcomes["measured_sloc"] == m["sloc"]["measured"]
    assert "static_findings_per_kloc" in outcomes


def test_a_failed_required_tool_is_missing_data_in_the_measurement(user, real_world):
    real_world.outcomes["bandit"] = [fx.ok_outcome(exit_code=2)]
    run = executor.submit_run(user=user, plan=_plan(with_review=False), source_code=APP, dispatch=False)
    executor.process_run(str(run.pk))
    run.refresh_from_db()
    assert run.status == AnalysisRun.Status.PARTIAL
    m = run.measurement
    assert m["complete"] is False
    assert m["required_failed"] == ["bandit"]
    assert m["tool_status"]["bandit"] == ToolResult.Status.FAILED
    assert m["static"]["available"] is False
    outcomes = measurement.outcomes(m)
    assert outcomes["static_findings_per_kloc"] is None  # never 0 for a failed instrument
    assert run.summary["tools_failed"] == 1
