"""Consumer regressions checked against the measure stage's real engine (no fakes).

Each test reproduces a defect found in review with the real
``sample.target_meta`` / ``measurement.compute`` / ``summarize`` writers and
asserts the fixed behaviour: functional pass rates read one way everywhere,
canonical exports under a plan, plan auto-selection, descriptive totals,
tool effectiveness and the analysis-tasks export severities.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from backend.analysis.engine import measurement as measurement_engine
from backend.analysis.engine import sample as sample_engine
from backend.analysis.models import AnalysisRun
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("real_engine")]

PLAN_A = "a" * 64
PLAN_B = "b" * 64


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _job(user, model, **kw):
    return GenerationJobFactory(
        created_by=user,
        model=model,
        status="completed",
        mode="scaffolding",
        result_data={"files": {"app.py": "def f():\n    return 1\n" * 40}},
        **kw,
    )


def _measure(run):
    run.measurement = measurement_engine.compute(run)
    run.summary = measurement_engine.summarize(run)
    run.save(update_fields=["measurement", "summary"])
    return run


def _run(job, plan_hash, *, status="completed", complete=True, tool_status="succeeded", findings=0):
    run = AnalysisRunFactory(
        created_by=job.created_by,
        generation_job=job,
        source_code=None,
        plan_hash=plan_hash,
        status=status,
        is_complete=complete,
        target_meta=sample_engine.target_meta(job),
    )
    result = ToolResultFactory(run=run, tool_slug="bandit", status=tool_status, required=True)
    for _ in range(findings):
        FindingFactory(result=result, severity="high")
    return _measure(run)


def _ai_only_run(job, plan_hash):
    run = AnalysisRunFactory(
        created_by=job.created_by,
        generation_job=job,
        source_code=None,
        plan_hash=plan_hash,
        plan={"schema_version": 1, "tools": [], "reviewer": {"enabled": True, "tool_slug": "ai_review"}},
        status="completed",
        is_complete=True,
        target_meta=sample_engine.target_meta(job),
    )
    ToolResultFactory(run=run, tool_slug="ai_review", tool_kind="ai", category="ai", status="succeeded", required=True)
    return _measure(run)


def _age(run, hours: int) -> None:
    AnalysisRun.objects.filter(id=run.id).update(created_at=timezone.now() - timedelta(hours=hours))


# ── functional pass rate: one instrument, one reader ──────────────────────────


def test_report_treats_not_applicable_functional_as_missing():
    from backend.reports.services.generators import generate_model_analysis

    user = UserFactory()
    model = LLMModelFactory()
    na = {"status": "not_applicable", "pass_rate": 0.0, "passed": False, "source": "conformance"}
    ok = {"status": "passed", "pass_rate": 1.0, "passed": True, "source": "conformance", "evaluator_version": 3}
    _run(_job(user, model, metrics={"functional": na}), PLAN_A)
    _run(_job(user, model, metrics={"functional": ok}), PLAN_A)

    data = generate_model_analysis({"model_id": model.model_id}, user=user)
    functional = data["stats"]["functional"]

    assert functional["pass_rate"]["mean"] == 1.0
    assert functional["pass_rate"]["n"] == 1
    assert functional["pass_rate"]["n_missing"] == 1
    # One row, one functional pass rate.
    assert data["empirical"]["functional_pass_rate"] == 1.0
    assert data["stats"]["outcomes"]["functional_pass_rate"]["mean"] == 1.0


def test_report_and_rankings_agree_after_a_later_evaluation():
    from backend.rankings.services import build_rankings
    from backend.reports.services.generators import generate_model_analysis

    user = UserFactory()
    model = LLMModelFactory()
    job = _job(user, model)
    _run(job, PLAN_A)  # analysed before the (manual) conformance evaluation
    job.metrics = {"functional": {"status": "passed", "pass_rate": 1.0, "passed": True, "source": "conformance"}}
    job.save(update_fields=["metrics"])

    row = next(r for r in build_rankings(user)["rows"] if r["model_id"] == model.model_id)
    report = generate_model_analysis({"model_id": model.model_id}, user=user)

    assert row["functional_pass_rate"] is None
    assert report["stats"]["functional"]["pass_rate"]["mean"] is None
    assert report["empirical"]["functional_pass_rate"] is None


def test_report_never_counts_the_smoke_probe_as_conformance():
    from backend.reports.services.generators import generate_model_analysis

    user = UserFactory()
    model = LLMModelFactory()
    smoke = {"passed": True, "health_ok": True, "endpoints_checked": 0, "endpoints_ok": 0, "pass_rate": 1.0}
    job = _job(user, model, metrics={"functional": smoke})  # legacy data: the probe overwrote the evaluator
    job.result_data = {
        **job.result_data,
        "evaluation": {
            "status": "failed",
            "summary": {"total": 8, "passed": 2, "failed": 6, "pass_rate": 0.25},
            "evaluator_version": 3,
        },
    }
    job.save(update_fields=["result_data"])

    data = generate_model_analysis({"model_id": model.model_id}, user=user)
    functional = data["stats"]["functional"]

    assert functional["pass_rate"]["mean"] == 0.25
    assert functional["other_instruments"] == {"smoke": 1}


# ── exports ───────────────────────────────────────────────────────────────────


def test_canonical_findings_export_under_a_plan_keeps_rerun_jobs(client):
    user = UserFactory()
    client.force_login(user)
    job = _job(user, LLMModelFactory())
    old = _run(job, PLAN_A, findings=3)
    _age(old, 2)
    _run(job, PLAN_B, findings=1)  # a newer complete run under another plan

    canonical = json.loads(client.get(f"/api/export/findings.json?canonical=true&plan_hash={PLAN_A}").content)

    assert len(canonical) == 3


def test_analysis_tasks_export_failed_run_has_empty_severities(client):
    user = UserFactory()
    client.force_login(user)
    run = _run(_job(user, LLMModelFactory()), PLAN_A, status="failed", complete=False, tool_status="failed")
    assert run.measurement["static"]["available"] is False

    rows = list(csv.DictReader(io.StringIO(client.get("/api/export/analysis-tasks.csv").content.decode())))

    row = rows[0]
    assert row["status"] == "failed"
    assert [row[s] for s in ("critical", "high", "medium", "low", "info")] == [""] * 5
    assert row["total_findings"] == ""


# ── plan choice and descriptive totals ────────────────────────────────────────


def test_ai_only_plan_never_becomes_the_reference_plan():
    from backend.rankings.services import build_rankings

    user = UserFactory()
    model = LLMModelFactory()
    jobs = [_job(user, model) for _ in range(3)]
    for job in jobs[:2]:
        _run(job, PLAN_A, findings=2)
    for job in jobs:
        _ai_only_run(job, PLAN_B)

    built = build_rankings(user)
    row = next(r for r in built["rows"] if r["model_id"] == model.model_id)

    assert built["comparability"]["plan_hash"] == PLAN_A
    assert row["n_static"] == 2
    assert row["findings"]["high"] == 4  # the static measurement is used, not discarded


def test_statistics_totals_survive_a_narrower_re_run():
    from backend.statistics.services.aggregations import get_system_overview

    user = UserFactory()
    job = _job(user, LLMModelFactory())
    full = _run(job, PLAN_A, findings=5)
    _age(full, 1)
    before = get_system_overview(user)["total_findings"]
    _ai_only_run(job, PLAN_B)
    cache.clear()

    after = get_system_overview(user)

    assert before == 5
    assert after["total_findings"] == 5
    assert after["static_measured_jobs"] == 1


def test_tool_effectiveness_counts_failures_of_failed_runs():
    from backend.statistics.services.aggregations import get_tool_effectiveness

    user = UserFactory()
    model = LLMModelFactory()
    _run(_job(user, model), PLAN_A, findings=1)
    for _ in range(3):
        _run(_job(user, model), PLAN_A, status="failed", complete=False, tool_status="failed")

    bandit = {r["name"]: r for r in get_tool_effectiveness(user)}["bandit"]

    assert bandit["failed"] == 3
    assert bandit["success_rate"] == 0.25
