"""lizard / jscpd metrics in the study's per-app table, code-metrics section, validity checks and table export."""

from __future__ import annotations

import pytest

from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.models import GenerationRun
from backend.llm_models.tests.factories import LLMModelFactory
from backend.reports.services import study_report
from backend.reports.tests.helpers import make_run
from backend.statistics.services.study import code_metrics_by_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LIZARD = {"functions": 10, "avg_ccn": 2.5, "max_ccn": 9, "functions_over_15": 0, "total_nloc": 120}
JSCPD = {"clones": 3, "duplication_percentage": 4.2, "duplicated_lines": 12, "total_lines": 300}


def _run(user, model):
    return make_run(user, model, code=None, files={"app.py": "x = 1\n" * 40, "frontend/src/App.jsx": "let a\n" * 10})


def _results(gen_run, specs):
    run = AnalysisRunFactory(created_by=gen_run.created_by, generation_job=gen_run, source_code={}, status="completed")
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
    # One task each: four apps (repeats of one app would be averaged into one unit).
    a, b, c, d = (_run(user, models[i % 2]) for i in range(4))
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
    rows, _issues = study_report.app_table(list(GenerationRun.objects.filter(created_by=user)))
    by_job = {r["run_id"]: r for r in rows}
    a, b, c, d = (by_job[str(jobs[k].id)] for k in "abcd")
    # The later lizard result of a counts, not the first one (avg_ccn 9.9).
    assert (a["lizard_avg_ccn"], a["lizard_functions"], a["lizard_max_ccn"]) == (2.5, 10, 9)
    assert (a["jscpd_duplication_percentage"], a["jscpd_clones"]) == (4.2, 3)
    assert (a["avg_complexity"], a["max_complexity"]) == (3.0, 7)
    assert b["lizard_functions_over_15"] == 2
    # A measured zero stays zero.
    assert (b["jscpd_duplication_percentage"], b["jscpd_clones"]) == (0.0, 0)
    # lizard failed on c and jscpd had nothing to read: both unmeasured, never 0 (spec 8.8).
    assert c["lizard_avg_ccn"] is None
    assert c["lizard_functions"] is None
    assert (c["jscpd_clones"], c["jscpd_duplication_percentage"]) == (None, None)
    assert all(d[k] is None for k in study_report.TOOL_METRIC_KEYS)


def test_metrics_by_run_records_the_instrument(apps):
    from backend.analysis.models import ToolResult

    _user, jobs = apps
    metrics = code_metrics_by_run(ToolResult.latest_ids_per_job([j.id for j in jobs.values()]))
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
    assert overall["jscpd_clones"]["n"] == 2  # a and b; c's jscpd had nothing applicable to read
    assert overall["jscpd_duplication_percentage"]["n"] == 2
    assert overall["avg_complexity"]["n"] == 1
    assert "code_metrics_tools" in data["definitions"]
    per_app = {r["run_id"]: r for r in data["per_app"]}
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
