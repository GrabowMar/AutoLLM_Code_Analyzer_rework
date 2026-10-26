"""Contract check against the measure stage's real engine (no fakes).

A run is measured with the real ``measurement.compute`` / ``summarize`` from
a sample resolved by the real ``sample.resolve_job``; the consumers' canonical
selection and empirical inputs must read it exactly as §4.4 describes.
"""

from __future__ import annotations

import pytest

from backend.analysis.engine import measurement
from backend.analysis.engine import sample
from backend.analysis.models import AnalysisRun
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.rankings import services
from backend.rankings.services.measured import select_measured
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

APP = "\n".join(
    [
        "import os",
        "import sqlite3",
        "",
        "",
        "def handler(name):",
        "    conn = sqlite3.connect(os.environ['DB'])",
        "    query = 'SELECT * FROM users WHERE name = %s' % name",
        "    return conn.execute(query).fetchall()",
    ]
    + [f"VALUE_{i} = {i}" for i in range(80)],
)


def _measured_job(user, model, *, high: int, plan_hash: str = "c" * 64):
    job = GenerationJobFactory(
        created_by=user,
        model=model,
        mode="scaffolding",
        status=GenerationJob.Status.COMPLETED,
        result_data={"files": {"app.py": APP, "frontend/package-lock.json": "{\n" * 2000}},
    )
    resolved = sample.resolve_job(job)
    run = AnalysisRunFactory(
        created_by=user,
        generation_job=job,
        source_code=None,
        plan={"schema_version": 1, "tools": [{"slug": "bandit", "required": True}], "reviewer": None},
        plan_hash=plan_hash,
        sample_manifest=resolved.manifest(),
        sample_hash=resolved.sample_hash,
        target_meta=sample.target_meta(job),
        status=AnalysisRun.Status.COMPLETED,
        is_complete=True,
    )
    result = ToolResultFactory(run=run, tool_slug="bandit", required=True, status=ToolResult.Status.SUCCEEDED)
    for i in range(high):
        FindingFactory(
            result=result,
            severity="high",
            category="security",
            file_path="app.py",
            line_number=7,
            file_origin="authored",
            counted=True,
            rule_id=f"B60{i}",
            fingerprint=f"{i:064x}",
            issue_key=f"{i:064x}",
        )
    run.measurement = measurement.compute(run)
    run.summary = measurement.summarize(run)
    run.save(update_fields=["measurement", "summary"])
    return job, run


def test_consumers_read_the_real_measurement(real_engine):
    user = UserFactory()
    model = LLMModelFactory(model_id="real/model")
    job, run = _measured_job(user, model, high=2)

    selection = select_measured([job.id])

    measured = selection.measured[job.id]
    assert measured.run_id == run.id
    assert measured.static_available is True
    assert measured.findings_by_severity()["high"] == 2
    assert measured.issues_total() == 2
    # the lockfile is excluded from SLOC: only app.py's code lines count
    assert measured.measured_sloc() == run.sample_manifest["totals"]["measured_sloc"]
    assert 0 < measured.measured_sloc() < 200
    assert set(measurement.OUTCOMES) <= set(measured.outcomes)
    assert run.summary["severity_counts"]["high"] == 2

    row = next(r for r in services.aggregate_rankings(user) if r["model_id"] == model.model_id)
    assert row["n_trials"] == 1
    assert row["measured_sloc"] == measured.measured_sloc()
    assert row["empirical_quality_score"] is not None


def test_real_failed_required_tool_makes_static_missing(real_engine):
    user = UserFactory()
    model = LLMModelFactory(model_id="real/failed")
    job, run = _measured_job(user, model, high=1)
    run.results.filter(tool_slug="bandit").update(status=ToolResult.Status.FAILED)
    run.measurement = measurement.compute(run)
    run.status = AnalysisRun.Status.PARTIAL
    run.is_complete = False
    run.save(update_fields=["measurement", "status", "is_complete"])

    complete_only = select_measured([job.id])
    any_status = select_measured([job.id], complete_only=False)

    assert complete_only.incomplete == {job.id}
    partial = any_status.measured[job.id]
    assert partial.static_available is False
    assert partial.findings_by_severity() is None
    assert partial.outcomes["static_issues_per_kloc"] is None
