"""lizard / jscpd metrics in the study's per-app table, code-metrics section, validity checks and table export."""

from __future__ import annotations

import csv

import pytest
from django.core.management import call_command

from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationJob
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.generation.tests.factories import GenerationJobFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.services import study_report
from backend.statistics.services.study import CODE_METRIC_PER_APP_KEYS
from backend.statistics.services.study import code_metrics_by_job
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LIZARD = {"functions": 10, "avg_ccn": 2.5, "max_ccn": 9, "functions_over_15": 0, "total_nloc": 120}
JSCPD = {"clones": 3, "duplication_percentage": 4.2, "duplicated_lines": 12, "total_lines": 300}


def _job(user, model, template):
    return GenerationJobFactory(
        created_by=user,
        mode="scaffolding",
        status=GenerationJob.Status.COMPLETED,
        model=model,
        app_requirement=template,
        result_data={"files": {"app.py": "x = 1\n" * 40, "frontend/src/App.jsx": "let a\n" * 10}},
    )


def _results(job, specs):
    run = AnalysisRunFactory(created_by=job.created_by, generation_job=job, source_code=None, status="completed")
    for tool, status, metrics, summary in specs:
        ToolResultFactory(run=run, tool_slug=tool, category="quality", status=status, metrics=metrics, summary=summary)
    return run


@pytest.fixture
def apps():
    """Four apps of two models.

    a: every metric tool measured (lizard re-run later with other figures: the later one counts).
    b: lizard and jscpd measured with a different lizard parser.
    c: lizard failed, jscpd skipped as not applicable.
    d: no metric tool ran.
    """
    user = UserFactory()
    models = [LLMModelFactory(model_id=f"vendor/cm{i}") for i in range(2)]
    # One template each: four apps (repeats of one app would be averaged into one unit).
    a, b, c, d = (_job(user, models[i % 2], AppRequirementTemplateFactory(slug=f"cm-app-{i}")) for i in range(4))
    _results(
        a,
        [
            ("radon", "completed", {"average_complexity": 3.0, "max_complexity": 7}, {}),
            ("lizard", "completed", {**LIZARD, "avg_ccn": 9.9}, {"parser_sha": "p1"}),
            ("jscpd", "completed", JSCPD, {"parser_sha": "j1"}),
            ("bandit", "completed", {}, {}),
        ],
    )
    _results(a, [("lizard", "completed", LIZARD, {"parser_sha": "p1", "tool_version": "1.17.31"})])
    _results(
        b,
        [
            ("lizard", "completed", {**LIZARD, "functions_over_15": 2, "max_ccn": 22}, {"parser_sha": "p2"}),
            ("jscpd", "completed", {**JSCPD, "duplication_percentage": 0.0, "clones": 0}, {"parser_sha": "j1"}),
            ("bandit", "completed", {}, {}),
        ],
    )
    _results(
        c,
        [
            ("lizard", "failed", {}, {}),
            ("jscpd", "skipped", {}, {"skip_reason": "not_applicable"}),
            ("bandit", "completed", {}, {}),
        ],
    )
    _results(d, [("bandit", "completed", {}, {})])
    return user, {"a": a, "b": b, "c": c, "d": d}


def test_app_rows_carry_each_tools_latest_metrics_and_none_when_unmeasured(apps):
    user, jobs = apps
    rows, _issues = study_report.app_table(list(GenerationJob.objects.filter(created_by=user)))
    by_job = {r["job_id"]: r for r in rows}
    a, b, c, d = (by_job[str(jobs[k].id)] for k in "abcd")
    # The later lizard result of a counts, not the first one (avg_ccn 9.9).
    assert (a["lizard_avg_ccn"], a["lizard_functions"], a["lizard_max_ccn"]) == (2.5, 10, 9)
    assert (a["jscpd_duplication_percentage"], a["jscpd_clones"]) == (4.2, 3)
    assert (a["avg_complexity"], a["max_complexity"]) == (3.0, 7)
    assert b["lizard_functions_over_15"] == 2
    # A measured zero stays zero.
    assert (b["jscpd_duplication_percentage"], b["jscpd_clones"]) == (0.0, 0)
    # lizard failed on c: unmeasured, never 0. jscpd had nothing to read: zero clones, no percentage.
    assert c["lizard_avg_ccn"] is None
    assert c["lizard_functions"] is None
    assert (c["jscpd_clones"], c["jscpd_duplication_percentage"]) == (0, None)
    assert all(d[k] is None for k in study_report.TOOL_METRIC_KEYS)


def test_metrics_by_job_records_the_instrument(apps):
    from backend.analysis.models import ToolResult

    _user, jobs = apps
    metrics = code_metrics_by_job(ToolResult.latest_ids_per_job([j.id for j in jobs.values()]))
    a, c = metrics[str(jobs["a"].id)], metrics[str(jobs["c"].id)]
    assert (a["lizard_status"], a["lizard_version"], a["lizard_parser_sha"]) == ("measured", "1.17.31", "p1")
    assert (a["radon_avg_complexity"], a["radon_status"]) == (3.0, "measured")
    assert (c["lizard_status"], c["jscpd_status"], c["radon_status"]) == ("failed", "not_applicable", None)
    assert str(jobs["d"].id) not in metrics


def test_code_metrics_section_describes_the_apps_each_tool_measured(apps):
    user, _jobs = apps
    data = study_report.generate_study_report({"seed": 0, "bootstrap": False}, user=user)
    overall = data["code_metrics"]["overall"]
    assert overall["lizard_avg_ccn"]["n"] == 2
    assert overall["lizard_avg_ccn"]["mean"] == pytest.approx(2.5)
    assert overall["jscpd_clones"]["n"] == 3  # a, b, and c's measured zero
    assert overall["jscpd_duplication_percentage"]["n"] == 2
    assert overall["avg_complexity"]["n"] == 1
    assert "code_metrics_tools" in data["definitions"]
    per_app = {r["job_id"]: r for r in data["per_app"]}
    assert set(study_report.TOOL_METRIC_KEYS) <= set(next(iter(per_app.values())))


def test_mixed_metric_instruments_are_flagged_for_the_code_metrics(apps):
    user, _jobs = apps
    data = study_report.generate_study_report({"seed": 0, "bootstrap": False}, user=user)
    warning = next(v for v in data["validity"] if v["code"] == "mixed_metric_instruments")
    assert warning["section"] == "code_metrics"
    assert "lizard (parsers)" in warning["message"]
    assert "jscpd" not in warning["message"]


def test_one_instrument_raises_no_metric_warning(apps):
    from backend.analysis.models import ToolResult

    user, _jobs = apps
    ToolResult.objects.filter(tool_slug="lizard", summary__parser_sha="p2").update(summary={"parser_sha": "p1"})
    data = study_report.generate_study_report({"seed": 0, "bootstrap": False}, user=user)
    assert "mixed_metric_instruments" not in {v["code"] for v in data["validity"]}


def test_export_writes_code_metrics_per_app(apps, tmp_path):
    _user, jobs = apps
    call_command("export_study_tables", out=str(tmp_path))
    with (tmp_path / "code_metrics_per_app.csv").open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames or ()) == CODE_METRIC_PER_APP_KEYS
        rows = {r["job_id"]: r for r in reader}
    assert set(rows) == {str(jobs[k].id) for k in "abc"}  # d: no metric tool ran
    a, c = rows[str(jobs["a"].id)], rows[str(jobs["c"].id)]
    assert (a["lizard_avg_ccn"], a["jscpd_clones"], a["lizard_parser_sha"]) == ("2.5", "3", "p1")
    assert a["loc"] == "50"
    # Unmeasured is an empty cell, never 0.
    assert (c["lizard_avg_ccn"], c["jscpd_clones"], c["lizard_status"]) == ("", "0", "failed")


def test_export_of_an_empty_scope_keeps_the_header(tmp_path):
    call_command("export_study_tables", out=str(tmp_path), experiment="00000000-0000-0000-0000-000000000000")
    header = (tmp_path / "code_metrics_per_app.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",") == list(CODE_METRIC_PER_APP_KEYS)
