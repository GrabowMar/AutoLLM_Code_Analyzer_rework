"""Tests for research/report.py: protocol, post-run report and Markdown.

``engine.measurement.outcomes`` belongs to the measure stage; it is patched
at its module boundary with ``research_factories.fake_outcomes`` (a local
extractor of the §4.4 measurement shape).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.analysis.engine import measurement
from backend.analysis.engine.reviewer.prompt import CATEGORIES as REVIEW_CATEGORIES
from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalysisStudy
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult
from backend.analysis.research import report as report_mod
from backend.analysis.research import stats
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import AnalysisStudyFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.analysis.tests.research_factories import PLAN
from backend.analysis.tests.research_factories import PLAN_HASH
from backend.analysis.tests.research_factories import add_results
from backend.analysis.tests.research_factories import ai_finding
from backend.analysis.tests.research_factories import fake_outcomes
from backend.analysis.tests.research_factories import make_study
from backend.analysis.tests.research_factories import measurement as make_measurement
from backend.analysis.tests.research_factories import target_meta
from backend.generation.tests.factories import GenerationJobFactory

pytestmark = pytest.mark.django_db

SECTION_KEYS = [
    "overview",
    "deviations",
    "data_quality",
    "sample",
    "descriptives",
    "findings_profile",
    "inferential",
    "ai_reliability",
    "tool_overlap",
    "cost",
    "provenance",
]
COLUMN_KINDS = {"text", "int", "float", "pct", "p", "ci", "pct_ci", "usd", "bool", "hash", "list", "status", "code"}
PRIMARY = "static_issues_per_kloc"


@pytest.fixture(autouse=True)
def outcomes_boundary(monkeypatch):
    monkeypatch.setattr(measurement, "outcomes", fake_outcomes)
    monkeypatch.setattr(measurement, "OUTCOMES", {})
    monkeypatch.setenv("LLMLAB_GIT_SHA", "deadbeef")


def rows(report, section, table):
    found = report_mod.get_table(report, section, table)
    assert found is not None, f"missing table {section}.{table}"
    return found["rows"]


def row(report, section, table, **match):
    hits = [r for r in rows(report, section, table) if all(r.get(k) == v for k, v in match.items())]
    assert len(hits) == 1, f"{section}.{table} {match}: {len(hits)} rows"
    return hits[0]


def deviation_codes(report):
    return {r["code"] for r in rows(report, "deviations", "deviations")}


def issue_count(model, app, rep=0):
    """Mirror of make_study's default issue function (SLOC is 1000, so issues == issues/KLOC)."""
    return 3 + 2 * ("m-a", "m-b", "m-c").index(model) + int(app[-1]) % 2 + rep


# --------------------------------------------------------------------------
# Structure, serialization, determinism
# --------------------------------------------------------------------------


def test_report_structure_is_typed_and_json_serializable(user):
    study, _ = make_study(user)
    report = report_mod.build_report(study)

    json.dumps(report, allow_nan=False)
    assert report["schema_version"] == report_mod.REPORT_SCHEMA_VERSION
    assert report["kind"] == "analysis_study_report"
    assert [s["key"] for s in report["sections"]] == SECTION_KEYS
    assert report["protocol_hash"] == study.protocol_hash
    assert report["protocol_integrity"]["match"] is True
    assert report["plan_hash"] == PLAN_HASH
    for section in report["sections"]:
        assert section["title"]
        for table in section["tables"]:
            assert table["key"]
            assert table["title"]
            keys = [c["key"] for c in table["columns"]]
            assert len(keys) == len(set(keys))
            for column in table["columns"]:
                assert column["kind"] in COLUMN_KINDS, (table["key"], column)
            for r in table["rows"]:
                assert set(keys) <= set(r), (table["key"], set(keys) - set(r))
        for series in section["series"]:
            assert series["key"]
            assert series["kind"]


def test_report_hash_is_deterministic_and_content_sensitive(user):
    study, runs = make_study(user)
    first = report_mod.build_report(study)
    second = report_mod.build_report(study)
    assert first["report_hash"] == second["report_hash"]
    assert report_mod.report_hash(first) == first["report_hash"]
    strip = {k: v for k, v in first.items() if k not in report_mod.VOLATILE_REPORT_KEYS}
    assert strip == {k: v for k, v in second.items() if k not in report_mod.VOLATILE_REPORT_KEYS}

    run = runs[("m-b", "app-2", 0)]
    run.measurement = make_measurement(issues=40)
    run.save()
    assert report_mod.build_report(study)["report_hash"] != first["report_hash"]


def test_empty_study_still_builds(user):
    study = AnalysisStudyFactory(created_by=user, plan=PLAN, plan_hash=PLAN_HASH)
    report = report_mod.build_report(study)
    json.dumps(report, allow_nan=False)
    assert report["summary"]["analysed"] == 0
    assert "no_protocol" in deviation_codes(report)
    primary = row(report, "inferential", "omnibus", key=PRIMARY)
    assert primary["statistic"] is None
    assert primary["p"] is None
    md = report_mod.to_markdown(report)
    assert "## Deviations from protocol" in md


# --------------------------------------------------------------------------
# Design: blocked vs independent
# --------------------------------------------------------------------------


def test_complete_design_uses_friedman_and_wilcoxon(user):
    study, _ = make_study(user)
    report = report_mod.build_report(study)
    assert report["settings"]["mode"] == "blocked"
    assert report["settings"]["planned_mode"] == "blocked"

    models, apps = ("m-a", "m-b", "m-c"), ("app-1", "app-2", "app-3", "app-4")
    expected = stats.friedman([[issue_count(m, a) for m in models] for a in apps])
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert omnibus["test"] == "friedman"
    assert omnibus["family"] == "confirmatory"
    assert omnibus["blocks_used"] == 4
    assert omnibus["blocks_dropped"] == 0
    assert omnibus["statistic"] == pytest.approx(expected["statistic"])
    assert omnibus["p"] == pytest.approx(expected["p"])
    assert omnibus["effect"] == pytest.approx(expected["kendalls_w"])
    assert omnibus["p_adj"] == pytest.approx(expected["p"])  # single confirmatory outcome
    sensitivity = row(report, "inferential", "omnibus_unblocked", key=PRIMARY)
    unblocked = stats.kruskal_wallis([[issue_count(m, a) for a in apps] for m in models])
    assert sensitivity["statistic"] == pytest.approx(unblocked["statistic"])
    assert sensitivity["n"] == 12

    pairs = [r for r in rows(report, "inferential", "pairwise") if r["key"] == PRIMARY]
    assert [(r["group_a"], r["group_b"]) for r in pairs] == [("m-a", "m-b"), ("m-a", "m-c"), ("m-b", "m-c")]
    ab = pairs[0]
    xa = [issue_count("m-a", a) for a in apps]
    xb = [issue_count("m-b", a) for a in apps]
    assert ab["test"] == "wilcoxon_signed_rank"
    assert ab["n_pairs"] == 4
    assert ab["p"] == pytest.approx(stats.wilcoxon_signed_rank(xa, xb)["p"])
    assert ab["delta"] == pytest.approx(stats.cliffs_delta(xa, xb))
    assert ab["magnitude"] == "large"
    assert ab["better"] == "m-a"  # lower density is better
    assert ab["median_diff"] == pytest.approx(-2.0)
    assert ab["delta_ci"] is not None  # primary outcome gets a bootstrap CI for delta
    assert [r["p_adj"] for r in pairs] == pytest.approx(stats.holm([r["p"] for r in pairs]))
    secondary = [r for r in rows(report, "inferential", "pairwise") if r["key"] == "complexity_mean"]
    assert all(r["delta_ci"] is None for r in secondary)


def test_incomplete_design_falls_back_to_kruskal_wallis(user):
    study, _ = make_study(user, planned_skip={("m-c", "app-4")})
    assert study.protocol["design"]["complete"] is False
    report = report_mod.build_report(study)
    assert report["settings"]["mode"] == "independent"

    values = {
        m: [issue_count(m, a) for a in ("app-1", "app-2", "app-3", "app-4") if (m, a) != ("m-c", "app-4")]
        for m in ("m-a", "m-b", "m-c")
    }
    expected = stats.kruskal_wallis(list(values.values()))
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert omnibus["test"] == "kruskal_wallis"
    assert omnibus["statistic"] == pytest.approx(expected["statistic"])
    assert omnibus["effect"] == pytest.approx(expected["epsilon_squared"])
    assert omnibus["n"] == 11
    pair = row(report, "inferential", "pairwise", key=PRIMARY, group_a="m-b", group_b="m-c")
    assert pair["test"] == "mann_whitney_u"
    assert (pair["n_a"], pair["n_b"]) == (4, 3)
    assert pair["p"] == pytest.approx(stats.mann_whitney_u(values["m-b"], values["m-c"])["p"])


def test_group_by_several_factors(user):
    study, _ = make_study(user, design={"group_by": ["model", "mode"]})
    report = report_mod.build_report(study)
    assert report["summary"]["groups"] == [f"{m} {report_mod.TIMES} agent" for m in ("m-a", "m-b", "m-c")]


# --------------------------------------------------------------------------
# Missing data, attrition, n = 0 / 1
# --------------------------------------------------------------------------


def test_missing_data_and_attrition_are_explicit(user):
    study, runs = make_study(user, skip={("m-b", "app-2")})
    failed = runs[("m-a", "app-1", 0)]
    failed.status = AnalysisRun.Status.FAILED
    failed.is_complete = False
    failed.save()
    partial = runs[("m-c", "app-3", 0)]
    partial.status = AnalysisRun.Status.PARTIAL
    partial.is_complete = False
    partial.measurement = make_measurement(static_ok=False)
    partial.save()
    ToolResult.objects.filter(run=partial, tool_slug="semgrep").update(status="failed", failure_reason="parse_error")

    report = report_mod.build_report(study)
    codes = deviation_codes(report)
    assert {"target_not_run", "target_run_failed", "required_tool_failed", "design_incomplete"} <= codes

    funnel = {r["key"]: r["n"] for r in rows(report, "data_quality", "funnel")}
    assert funnel == {
        "planned": 12,
        "eligible": 12,
        "submitted": 11,
        "analysed": 10,
        "complete": 9,
        "primary_available": 9,
    }
    steps = list(funnel.values())
    assert steps == sorted(steps, reverse=True)  # nested stages never grow
    attrition = {r["group"]: r for r in rows(report, "data_quality", "attrition")}
    assert (attrition["m-c"]["analysed"], attrition["m-c"]["complete"], attrition["m-c"]["primary_available"]) == (
        4,
        3,
        3,
    )
    assert (attrition["m-a"]["with_run"], attrition["m-a"]["analysed"]) == (4, 3)
    assert (attrition["m-b"]["with_run"], attrition["m-b"]["analysed"]) == (3, 3)

    desc = report_mod.get_table(report, "descriptives", f"outcome.{PRIMARY}")["rows"]
    by_group = {r["group"]: r for r in desc}
    assert (by_group["m-c"]["n"], by_group["m-c"]["n_missing"]) == (3, 1)
    assert (by_group["m-a"]["n"], by_group["m-a"]["n_missing"]) == (3, 0)  # the failed run is attrition
    assert (by_group["All"]["n"], by_group["All"]["n_missing"]) == (9, 1)
    missing = row(report, "data_quality", "outcome_missing", key=PRIMARY)
    assert (missing["n"], missing["available"], missing["missing"]) == (10, 9, 1)

    # Blocked analysis keeps only blocks present in every group: app-4 alone -> Friedman undefined, never 0.
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert omnibus["blocks_used"] == 1
    assert omnibus["statistic"] is None
    assert omnibus["p"] is None
    assert "Friedman undefined" in omnibus["note"]
    pair = row(report, "inferential", "pairwise", key=PRIMARY, group_a="m-a", group_b="m-b")
    assert pair["n_pairs"] == 2  # app-3 and app-4
    pair_c = row(report, "inferential", "pairwise", key=PRIMARY, group_a="m-a", group_b="m-c")
    assert (pair_c["n_a"], pair_c["n_missing_a"], pair_c["n_b"], pair_c["n_missing_b"]) == (3, 0, 3, 1)

    matrix = report_mod.get_section(report, "data_quality")["series"]
    status = next(s for s in matrix if s["key"] == "tool_status_matrix")
    labels = [r["label"] for r in status["rows"]]
    not_run = status["cells"][labels.index("m-b/app-2#0")]
    assert set(not_run) == {"not_run"}
    partial_row = status["cells"][labels.index("m-c/app-3#0")]
    assert partial_row[status["columns"].index("semgrep")] == "failed"
    tools = row(report, "data_quality", "tools", tool="semgrep")
    assert tools["failed"] == 1
    assert tools["failure_reasons"] == ["parse_error: 1"]


def test_singleton_groups_have_no_spread(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1",))
    report = report_mod.build_report(study)
    assert report["settings"]["mode"] == "independent"  # one block: blocking is impossible
    desc = {r["group"]: r for r in report_mod.get_table(report, "descriptives", f"outcome.{PRIMARY}")["rows"]}
    assert desc["m-a"]["n"] == 1
    assert desc["m-a"]["sd"] is None
    assert desc["m-a"]["mean_ci"] is None
    assert desc["m-a"]["median"] == pytest.approx(issue_count("m-a", "app-1"))
    pair = row(report, "inferential", "pairwise", key=PRIMARY)
    assert pair["test"] == "mann_whitney_u"
    assert pair["p"] == pytest.approx(1.0)
    assert report_mod.get_table(report, "inferential", "omnibus_unblocked") is None  # independent design


def test_single_group_has_no_inferential_test(user):
    study, _ = make_study(user, models=("m-a",))
    report = report_mod.build_report(study)
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert omnibus["statistic"] is None
    assert "fewer than 2 groups" in omnibus["note"]
    assert rows(report, "inferential", "pairwise") == []


def test_group_without_data_is_not_compared(user):
    study, runs = make_study(user)
    for (model, _app, _rep), run in runs.items():
        if model == "m-c":
            run.measurement = make_measurement(ai_available=False)
            run.save()
    report = report_mod.build_report(study)
    omnibus = row(report, "inferential", "omnibus", key="ai_consensus_per_kloc")
    assert omnibus["groups"] == 2
    assert "No data: m-c" in omnibus["note"]
    pair = row(report, "inferential", "pairwise", key="ai_consensus_per_kloc", group_a="m-a", group_b="m-c")
    assert pair["p"] is None
    assert pair["p_adj"] is None
    assert pair["delta"] is None
    assert "not compared" in pair["note"]
    desc = {
        r["group"]: r for r in report_mod.get_table(report, "descriptives", "outcome.ai_consensus_per_kloc")["rows"]
    }
    assert (desc["m-c"]["n"], desc["m-c"]["n_missing"], desc["m-c"]["mean"]) == (0, 4, None)


def test_binary_outcome_uses_wilson_interval(user):
    study, runs = make_study(user)
    run = runs[("m-a", "app-1", 0)]
    run.measurement = make_measurement(has_high=False)
    run.save()
    report = report_mod.build_report(study)
    table = report_mod.get_table(report, "descriptives", "outcome.has_high_security")
    m_a = next(r for r in table["rows"] if r["group"] == "m-a")
    assert (m_a["n"], m_a["successes"], m_a["share"]) == (4, 3, 0.75)
    assert m_a["share_ci"] == pytest.approx(list(stats.wilson_ci(3, 4)))
    assert any(c["kind"] == "pct_ci" for c in table["columns"])
    assert report_mod.format_cell(m_a["share_ci"], "pct_ci").startswith("[30.1%, ")


def test_outcome_extraction_errors_become_missing(user, monkeypatch):
    study, runs = make_study(user)
    broken = runs[("m-a", "app-2", 0)]
    broken.measurement = {"schema_version": 1, "unexpected": True}
    broken.save()
    report = report_mod.build_report(study)
    assert "outcome_extraction" in deviation_codes(report)
    desc = {r["group"]: r for r in report_mod.get_table(report, "descriptives", f"outcome.{PRIMARY}")["rows"]}
    assert desc["m-a"]["n_missing"] == 1


def test_measure_stage_outcome_catalog_is_respected(user, monkeypatch):
    monkeypatch.setattr(
        measurement,
        "OUTCOMES",
        {
            PRIMARY: {"label": "Issues per KLOC", "kind": "rate", "unit": "per KLOC", "higher_is_better": False},
            "measured_sloc": {"label": "SLOC", "kind": "count", "unit": "lines", "higher_is_better": None},
        },
    )
    study, _ = make_study(user)
    report = report_mod.build_report(study)
    assert report["settings"]["outcomes"] == [PRIMARY, "measured_sloc"]
    assert report_mod.get_table(report, "descriptives", f"outcome.{PRIMARY}")["title"] == "Issues per KLOC (primary)"


# --------------------------------------------------------------------------
# Canonical runs, plans, deviations
# --------------------------------------------------------------------------


def test_superseded_and_foreign_plan_runs_are_not_counted(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    old = runs[("m-a", "app-1", 0)]
    newer = AnalysisRunFactory(
        created_by=user,
        study=study,
        generation_job=old.generation_job,
        purpose=AnalysisRun.Purpose.RESEARCH,
        plan=PLAN,
        plan_hash=PLAN_HASH,
        target_meta=old.target_meta,
        status=AnalysisRun.Status.COMPLETED,
        is_complete=True,
        measurement=make_measurement(issues=50),
    )
    add_results(newer)
    foreign = AnalysisRunFactory(
        created_by=user,
        study=study,
        generation_job=runs[("m-b", "app-2", 0)].generation_job,
        purpose=AnalysisRun.Purpose.RESEARCH,
        plan_hash="b" * 64,
        target_meta=runs[("m-b", "app-2", 0)].target_meta,
        status=AnalysisRun.Status.COMPLETED,
        is_complete=True,
        measurement=make_measurement(issues=99),
    )
    report = report_mod.build_report(study)
    devs = {r["code"]: r for r in rows(report, "deviations", "deviations")}
    assert devs["superseded_runs"]["items"] == [str(old.id)]
    assert devs["plan_mismatch"]["items"] == [str(foreign.id)]

    desc = {r["group"]: r for r in report_mod.get_table(report, "descriptives", f"outcome.{PRIMARY}")["rows"]}
    assert desc["m-a"]["n"] == 2
    assert desc["m-a"]["max"] == pytest.approx(50.0)
    assert desc["m-b"]["max"] < 99
    runs_table = {r["run_id"]: r for r in rows(report, "provenance", "runs")}
    assert runs_table[str(newer.id)]["in_analysis"] is True
    assert runs_table[str(old.id)]["in_analysis"] is False
    assert runs_table[str(foreign.id)]["canonical"] is False
    assert report["summary"]["analysed"] == 4


def test_unlocked_study_reports_missing_protocol(user):
    study, _ = make_study(user, lock=False)
    study.status = AnalysisStudy.Status.DRAFT
    study.save()
    report = report_mod.build_report(study)
    codes = deviation_codes(report)
    assert "no_protocol" in codes
    assert "study_not_completed" in codes
    assert report["protocol_hash"] is None
    assert report["settings"]["planned_mode"] is None
    assert report["settings"]["mode"] == "blocked"  # realized design is complete
    assert report["summary"]["targets_planned"] == 12  # preflight targets


def test_toolchain_drift_is_a_deviation(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    ToolResult.objects.filter(run=runs[("m-a", "app-1", 0)], tool_slug="bandit").update(
        tool_version="1.9.5",
        image_ref="llmlab/analyzer:000000000000@sha256:1",
    )
    report = report_mod.build_report(study)
    codes = deviation_codes(report)
    assert {"tool_version_changed", "image_changed"} <= codes
    toolchain = row(report, "provenance", "toolchain", tool="bandit")
    assert toolchain["observed_versions"] == ["1.9.4", "1.9.5"]
    assert toolchain["planned_version"] == "1.9.4"


def test_runs_of_unplanned_targets_are_excluded(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    job = GenerationJobFactory(created_by=user)
    extra = AnalysisRunFactory(
        created_by=user,
        study=study,
        generation_job=job,
        purpose=AnalysisRun.Purpose.RESEARCH,
        plan=PLAN,
        plan_hash=PLAN_HASH,
        target_meta=target_meta(job, "m-a", "app-9", 0),
        status=AnalysisRun.Status.COMPLETED,
        is_complete=True,
        measurement=make_measurement(),
    )
    report = report_mod.build_report(study)
    assert "target_not_planned" in deviation_codes(report)
    assert str(extra.id) not in report["inputs"]["analysis_run_ids"]
    funnel = {r["key"]: r["n"] for r in rows(report, "data_quality", "funnel")}
    assert funnel["submitted"] == 4  # the unplanned target is not part of the eligible funnel


def test_unknown_block_is_never_paired(user):
    study, runs = make_study(user)
    run = runs[("m-a", "app-1", 0)]
    run.target_meta = {k: v for k, v in run.target_meta.items() if k != "app_slug"}
    run.save()
    report = report_mod.build_report(study)
    assert report["settings"]["mode"] == "blocked"  # the locked protocol's design
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert "1 value(s) without a known app left out of paired tests" in omnibus["note"]
    assert omnibus["blocks_used"] == 3  # app-1 lost m-a, so it is no longer complete
    assert "design_incomplete" in deviation_codes(report)
    matrix = report_mod.design_matrix(
        [("g1", "b1"), ("g2", None), ("g1", "b2"), ("g2", "b2")],
        {"group_by": ["model"], "block_by": "app"},
    )
    assert matrix["complete"] is False
    assert matrix["reason"] == "1 target(s) without a known app"


def test_locked_design_wins_over_later_edits(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    study.analysis_design = {**study.analysis_design, "alpha": 0.1, "group_by": "app"}
    study.save()
    report = report_mod.build_report(study)
    assert report["settings"]["alpha"] == 0.05
    assert report["settings"]["group_by"] == ["model"]
    notes = [r["detail"] for r in rows(report, "deviations", "deviations") if r["code"] == "design_note"]
    assert notes == ["analysis_design changed after the protocol was locked; the locked design is used."]


def test_image_drift_by_image_id(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    ToolResult.objects.filter(run__study=study, tool_kind="static").update(image_ref="sha256:" + "1" * 64)
    assert "image_changed" not in deviation_codes(report_mod.build_report(study))
    ToolResult.objects.filter(run=runs[("m-a", "app-1", 0)], tool_kind="static").update(image_ref="sha256:" + "2" * 64)
    assert "image_changed" in deviation_codes(report_mod.build_report(study))


# --------------------------------------------------------------------------
# Findings profile, AI reliability, tool overlap, cost, provenance
# --------------------------------------------------------------------------


def test_findings_profile_counts_issues_per_cwe(user):
    study, _ = make_study(user)
    report = report_mod.build_report(study)
    top = [r for r in rows(report, "findings_profile", "top_cwes") if r["group"] == "m-a"]
    # Equal counts are ordered by CWE id so the ranking is deterministic.
    assert [(r["cwe"], r["issues"], r["runs_affected"]) for r in top] == [("CWE-79", 4, 4), ("CWE-89", 4, 4)]
    assert top[0]["share_of_runs"] == 1.0
    severity = row(report, "findings_profile", "severity_by_group", group="m-b")
    assert severity["runs"] == 4
    assert severity["total"] == sum(issue_count("m-b", a) for a in ("app-1", "app-2", "app-3", "app-4"))
    heatmap = report_mod.get_section(report, "findings_profile")["series"][0]
    assert heatmap["columns"] == ["CWE-79", "CWE-89"]
    assert heatmap["values"] == [[4, 4], [4, 4], [4, 4]]


def test_ai_reliability_alpha_matches_unit_matrix(user):
    study, runs = make_study(user)
    run = runs[("m-a", "app-1", 0)]
    sample = run.results.get(tool_slug="ai_review").review_samples.get(index=2)
    sample.status = "invalid_output"
    sample.save()
    report = report_mod.build_report(study)

    # The samples record no coverage, so a run's files are the ones reported (app.py, App.jsx);
    # every file is crossed with all 10 categories. (App.jsx, bug) only in sample 0, (app.py,
    # security) in all samples, the other 18 units are agreed absences.
    agreed_absent = len(REVIEW_CATEGORIES) * 2 - 2
    per_run = [[1, 0, 0], [1, 1, 1]] + [[0, 0, 0]] * agreed_absent
    failed_run = [[1, 0, None], [1, 1, None]] + [[0, 0, None]] * agreed_absent
    units = per_run * 11 + failed_run
    overall = row(report, "ai_reliability", "reliability_by_group", group="All")
    assert overall["alpha_units"] == 12 * 20
    assert overall["alpha_files"] == 12 * 2
    assert overall["alpha_flagged_units"] == 12 * 2
    assert overall["alpha"] == pytest.approx(stats.krippendorff_alpha_nominal(units))
    assert overall["samples"] == 36
    assert overall["succeeded"] == 35
    assert overall["jaccard_median"] == pytest.approx(0.5)
    m_b = row(report, "ai_reliability", "reliability_by_group", group="m-b")
    assert m_b["alpha"] == pytest.approx(stats.krippendorff_alpha_nominal(per_run * 4))
    samples = {r["status"]: r["n"] for r in rows(report, "data_quality", "ai_samples")}
    assert samples == {"succeeded": 35, "invalid_output": 1}


def test_ai_section_when_reviewer_disabled(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    plan = {**PLAN, "reviewer": None}
    study.plan = plan
    study.save()
    from backend.analysis.models import ReviewSample

    ReviewSample.objects.filter(result__run__study=study).delete()
    report = report_mod.build_report(study)
    section = report_mod.get_section(report, "ai_reliability")
    assert section["tables"] == []
    assert section["notes"] == ["The AI reviewer is disabled in the plan."]


def test_tool_overlap_jaccard_and_unique_contributions(user):
    study, runs = make_study(user)
    ToolResult.objects.filter(run=runs[("m-a", "app-1", 0)], tool_slug="semgrep").update(status="failed")
    report = report_mod.build_report(study)
    pair = row(report, "tool_overlap", "pairwise_jaccard", tool_a="bandit", tool_b="semgrep")
    assert pair["runs"] == 11  # semgrep failed on one run: not compared there
    assert (pair["issues_a"], pair["issues_b"], pair["shared"]) == (11, 22, 11)
    assert pair["jaccard"] == pytest.approx(0.5)
    unique = {r["tool"]: r for r in rows(report, "tool_overlap", "unique_contributions")}
    assert unique["bandit"]["unique"] == 0
    assert unique["semgrep"]["unique"] == 11
    assert unique["semgrep"]["share_of_all"] == pytest.approx(1.0)
    upset = next(s for s in report_mod.get_section(report, "tool_overlap")["series"] if s["key"] == "upset")
    assert upset["intersections"] == [
        {"tools": ["bandit", "semgrep"], "count": 11},
        {"tools": ["semgrep"], "count": 11},
    ]


def test_cost_section_sums_samples(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    report = report_mod.build_report(study)
    total = row(report, "cost", "cost_by_group", group="All")
    assert total["ai_cost_total"] == pytest.approx(4 * 3 * 0.004)
    assert total["ai_cost_per_run"] == pytest.approx(0.012)
    assert total["generation_cost_total"] == pytest.approx(4 * 0.25)
    assert total["prompt_tokens"] == 4 * 3 * 1000
    per_finding = row(report, "cost", "cost_summary", metric="AI cost per counted AI finding (analysis set)")
    assert per_finding["value"] is None  # no AI finding is counted: undefined, not 0
    assert report["summary"]["ai_cost_total"] == pytest.approx(0.048)


def test_provenance_records_plan_and_reviewer(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    report = report_mod.build_report(study)
    kv = {r["key"]: r["value"] for r in rows(report, "provenance", "provenance")}
    assert kv["plan_hash"] == PLAN_HASH
    assert kv["analyzer_image"] == PLAN["analyzer_image"]["tag"]
    assert kv["platform_git_sha"] == "deadbeef"
    reviewer = row(report, "provenance", "reviewer", key="model_canonical")
    assert reviewer["planned"] == "deepseek/deepseek-v4.1-flash-1017"
    assert reviewer["observed"] == ["deepseek/deepseek-v4.1-flash-1017"]
    assert len(rows(report, "provenance", "runs")) == 4


# --------------------------------------------------------------------------
# Protocol
# --------------------------------------------------------------------------


def test_build_protocol_contents(user):
    study, _ = make_study(user)
    protocol = study.protocol
    json.dumps(protocol, allow_nan=False)
    assert protocol["kind"] == "analysis_study_protocol"
    assert protocol["targets"]["counts"] == {"total": 12, "eligible": 12, "excluded": 0, "by_reason": {}}
    assert protocol["design"]["complete"] is True
    assert protocol["design"]["mode"] == "blocked"
    assert protocol["design"]["groups"] == {"m-a": 4, "m-b": 4, "m-c": 4}
    assert protocol["analysis_plan"]["omnibus"] == "friedman"
    assert protocol["analysis_plan"]["pairwise"] == "wilcoxon_signed_rank"
    assert protocol["outcomes"]["primary"] == PRIMARY
    assert protocol["outcomes"]["confirmatory"] == [PRIMARY]
    assert protocol["controls"] == {
        "source": "preflight",
        "passed": True,
        "tools": [
            {
                "slug": slug,
                "status": "ready",
                "passed": True,
                "verdict": "passed",
                "config_hash": "",
                "missing": [],
                "unexpected_in_clean": [],
            }
            for slug in ("bandit", "semgrep")
        ],
    }
    assert protocol["platform"]["git"] == {"sha": "deadbeef", "ref": None, "source": "env:LLMLAB_GIT_SHA"}
    assert protocol["plan_hash"] == PLAN_HASH
    assert [t["slug"] for t in protocol["toolchain"]] == ["bandit", "semgrep"]
    assert protocol["reviewer"]["samples"] == 3
    assert protocol["exclusion_rules"]["exclude_invalid"] is True
    assert protocol["study"]["researcher"]["id"] == user.id
    assert "email" not in json.dumps(protocol["study"])
    assert {rq["kind"] for rq in protocol["research_questions"]} == {"confirmatory", "exploratory"}
    assert report_mod.protocol_hash(protocol) == study.protocol_hash
    assert report_mod.protocol_hash({**protocol, "protocol_hash": "x"}) == study.protocol_hash


def test_protocol_records_excluded_targets(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"), lock=False)
    targets = study.preflight["targets"]
    targets[0] = {**targets[0], "eligible": False, "reasons": [{"code": "empty_output", "severity": "exclude"}]}
    study.preflight = {**study.preflight, "targets": targets}
    protocol = report_mod.build_protocol(study)
    assert protocol["targets"]["counts"]["excluded"] == 1
    assert protocol["targets"]["counts"]["by_reason"] == {"empty_output": 1}
    assert protocol["design"]["complete"] is False  # m-a lost app-1
    assert protocol["analysis_plan"]["omnibus"] == "kruskal_wallis"


def test_protocol_controls_fall_back_to_per_tool_canaries_at_the_plan_config(user, monkeypatch):
    """Without preflight tool rows the controls come from the newest self-tests of the plan's exact configs."""
    from backend.analysis.engine import canary

    study, _ = make_study(user, models=("m-a",), apps=("app-1",), lock=False)
    study.preflight = {**study.preflight, "tools": []}
    asked = {}

    def per_tool(image_tag=None, *, config_hashes=None):
        asked.update(image_tag=image_tag, config_hashes=config_hashes)
        return {"bandit": {"passed": False, "verdict": "failed", "status": "succeeded", "missing": ["B608"]}}

    monkeypatch.setattr(canary, "latest_per_tool", per_tool)
    controls = report_mod.build_protocol(study)["controls"]
    assert asked == {
        "image_tag": PLAN["analyzer_image"]["tag"],
        "config_hashes": {"bandit": "c1" * 32, "semgrep": "c2" * 32},
    }
    assert controls["source"] == "canary.latest_per_tool"
    assert controls["passed"] is False
    tools = {t["slug"]: t for t in controls["tools"]}
    assert tools["bandit"]["missing"] == ["B608"]
    assert (tools["semgrep"]["passed"], tools["semgrep"]["verdict"]) == (None, "untested")

    def boom(image_tag=None, *, config_hashes=None):
        raise NotImplementedError

    monkeypatch.setattr(canary, "latest_per_tool", boom)
    assert report_mod.build_protocol(study)["controls"]["source"] is None


def test_protocol_controls_never_pass_with_untested_configs(user, monkeypatch):
    """Regression: config_untested rows are no evidence; the default-config self-test must not stamp 'passed'."""
    from backend.analysis.engine import canary

    def latest(image_tag=None):  # the old fallback: a passing default-config self-test
        return {"passed": True, "tools": {"bandit": {"passed": True}, "semgrep": {"passed": True}}}

    monkeypatch.setattr(canary, "latest", latest)
    study, _ = make_study(user, models=("m-a",), apps=("app-1",), lock=False)
    untested = {"passed": None, "verdict": "config_untested", "other_config": {"run_id": "r1", "verdict": "passed"}}
    study.preflight = {
        **study.preflight,
        "tools": [
            {"slug": "bandit", "status": "canary_unknown", "config_hash": "c1" * 32, "canary": untested},
            {"slug": "semgrep", "status": "canary_unknown", "config_hash": "c2" * 32, "canary": untested},
        ],
    }
    controls = report_mod.build_protocol(study)["controls"]
    assert controls["source"] == "preflight"
    assert controls["passed"] is None
    assert {t["verdict"] for t in controls["tools"]} == {"config_untested"}
    assert controls["tools"][0]["other_config"]["run_id"] == "r1"

    # One passing tool and one untested one: still not an overall pass.
    tools = study.preflight["tools"]
    tools[0] = {**tools[0], "status": "ready", "canary": {"passed": True, "verdict": "passed"}}
    controls = report_mod.build_protocol(study)["controls"]
    assert controls["passed"] is None
    text = report_mod.protocol_to_markdown(report_mod.build_protocol(study))
    assert "No passing control at the plan's configuration for: semgrep" in text
    assert "config_untested" in text


def test_resolve_design_defaults_and_validation():
    design, notes = report_mod.resolve_design(None)
    assert design["group_by"] == ["model"]
    assert design["block_by"] == "app"
    assert notes == []
    design, notes = report_mod.resolve_design(
        {"group_by": "app", "block_by": "app", "alpha": 2, "correction": "sidak", "bootstrap_resamples": -1},
    )
    assert design["group_by"] == ["app"]
    assert design["block_by"] is None
    assert design["alpha"] == 0.05
    assert design["correction"] == "holm"
    assert design["bootstrap_resamples"] == 10_000
    assert len(notes) == 4
    design, notes = report_mod.resolve_design({"group_by": ["nonsense"], "block_by": "none"})
    assert design["group_by"] == ["model"]
    assert design["block_by"] is None
    assert len(notes) == 1


def test_platform_git_sha_reads_git_directories(tmp_path, settings, monkeypatch):
    for var in ("LLMLAB_GIT_SHA", "GIT_SHA", "GIT_COMMIT", "SOURCE_COMMIT"):
        monkeypatch.delenv(var, raising=False)
    repo = tmp_path / "repo"
    (repo / ".git" / "refs" / "heads").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git" / "refs" / "heads" / "main").write_text("1" * 40 + "\n")
    settings.BASE_DIR = repo
    assert report_mod.platform_git_sha() == {"sha": "1" * 40, "ref": "refs/heads/main", "source": "git"}

    # A worktree: .git is a file; the branch ref lives in the common dir's packed-refs.
    common = tmp_path / "main.git"
    worktree_git = common / "worktrees" / "wt"
    worktree_git.mkdir(parents=True)
    (worktree_git / "HEAD").write_text("ref: refs/heads/feature\n")
    (worktree_git / "commondir").write_text("../..\n")
    (common / "packed-refs").write_text(f"# pack-refs\n{'2' * 40} refs/heads/feature\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {worktree_git}\n")
    settings.BASE_DIR = wt
    assert report_mod.platform_git_sha()["sha"] == "2" * 40

    settings.BASE_DIR = tmp_path / "nowhere"
    assert report_mod.platform_git_sha() == {"sha": None, "ref": None, "source": None}
    assert isinstance(Path(settings.BASE_DIR), Path)


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def test_markdown_is_thesis_ready(user):
    study, runs = make_study(user)
    run = runs[("m-c", "app-1", 0)]
    run.measurement = make_measurement(static_ok=False)
    run.save()
    report = report_mod.build_report(study)
    md = report_mod.to_markdown(report, study.protocol)
    assert md.startswith("# Analysis report: Synthetic study")
    assert f"`{report['report_hash']}`" in md
    for title in ("## Protocol", "### Design (T1)", "## Deviations from protocol", "## Descriptive results"):
        assert title in md
    assert "| Group | n | Missing | Median |" in md
    assert "NA" in md
    assert "None" not in md
    assert "| nan |" not in md.lower()
    assert "| inf |" not in md.lower()
    protocol_md = report_mod.protocol_to_markdown(study.protocol)
    assert protocol_md.startswith("# Study protocol: Synthetic study")
    assert "## Toolchain (T2)" in protocol_md
    assert "| bandit | 1.9.4 |" in protocol_md


@pytest.mark.parametrize(
    ("value", "kind", "expected"),
    [
        (None, "float", "NA"),
        (None, "text", "NA"),
        (0, "int", "0"),
        (0.0, "float", "0"),
        (12.3456, "float", "12.35"),
        (1234.5, "float", "1234.5"),
        (0.000123, "float", "1.23e-04"),
        (0.25, "pct", "25.0%"),
        (0.0004, "p", "<0.001"),
        (0.04321, "p", "0.043"),
        ([0.1, 0.9], "ci", "[0.100, 0.900]"),
        ([None, 0.9], "ci", "NA"),
        (0.004, "usd", "$0.0040"),
        (12.5, "usd", "$12.50"),
        (True, "bool", "yes"),
        ("a" * 64, "hash", "a" * 12),
        (["x", "y"], "list", "x, y"),
        ([], "list", ""),
    ],
)
def test_format_cell(value, kind, expected):
    assert report_mod.format_cell(value, kind) == expected


def test_markdown_table_escapes_pipes():
    table = {
        "title": "T",
        "columns": [{"key": "a", "label": "A|B", "kind": "text"}],
        "rows": [{"a": "x|y\nz"}],
        "notes": ["n"],
    }
    md = report_mod.table_to_markdown(table)
    assert "| A\\|B |" in md
    assert "| x\\|y z |" in md


def test_malformed_stored_json_does_not_break_the_report(user):
    from backend.analysis.models import Finding
    from backend.analysis.models import ReviewSample
    from backend.analysis.research import export

    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    run = runs[("m-a", "app-1", 0)]
    run.measurement = {"sloc": "lots", "static": [], "ai": "x", "coverage": [], "functional": 3}
    run.sample_manifest = {"totals": []}
    run.save()
    ToolResult.objects.filter(run=run).update(coverage="bad")
    ReviewSample.objects.filter(result__run=run).update(grounding=[1], providers="x", usage=[], coverage=[])
    Finding.objects.filter(run=run, is_ai=False).update(cwe="CWE-79")

    report = report_mod.build_report(study)
    json.dumps(report, allow_nan=False)
    assert "outcome_extraction" in deviation_codes(report)
    top = [r for r in rows(report, "findings_profile", "top_cwes") if r["group"] == "m-a"]
    assert {r["cwe"] for r in top} == {"CWE-79", "CWE-89"}
    tables = export.tables(study)
    assert {f["cwe"] for f in tables["findings"] if f["run_id"] == run.id and not f["is_ai"]} == {"CWE-79"}
    report_mod.to_markdown(report, study.protocol)


def test_unblocked_analysis_flags_replicates(user):
    study, _ = make_study(
        user,
        models=("m-a", "m-b"),
        apps=("app-1", "app-2"),
        reps=2,
        planned_skip={("m-b", "app-2")},
    )
    report = report_mod.build_report(study)
    assert report["settings"]["mode"] == "independent"
    assert report["settings"]["analysis_plan"]["test_unit"].startswith(f"group {report_mod.TIMES} app cell (mean")
    notes = report_mod.get_section(report, "inferential")["notes"]
    assert any(
        n.startswith(f"Replicates of one group {report_mod.TIMES} app cell are aggregated (mean)") for n in notes
    )
    desc = {r["group"]: r for r in report_mod.get_table(report, "descriptives", f"outcome.{PRIMARY}")["rows"]}
    assert desc["m-a"]["n"] == 4  # descriptives stay per run (with a bootstrap clustered by app)
    assert desc["m-b"]["n"] == 2

    # The tests use one value per (model, app) cell: replicates are correlated, never independent runs.
    cell = {
        (m, a): stats.mean([issue_count(m, a, rep) for rep in (0, 1)])
        for m, a in (("m-a", "app-1"), ("m-a", "app-2"), ("m-b", "app-1"))
    }
    xa = [cell[("m-a", "app-1")], cell[("m-a", "app-2")]]
    xb = [cell[("m-b", "app-1")]]
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert (omnibus["n"], omnibus["units"]) == (6, 3)
    assert omnibus["statistic"] == pytest.approx(stats.kruskal_wallis([xa, xb])["statistic"])
    pair = row(report, "inferential", "pairwise", key=PRIMARY, group_a="m-a", group_b="m-b")
    assert (pair["n_a"], pair["n_b"], pair["units_a"], pair["units_b"]) == (4, 2, 2, 1)
    assert pair["p"] == pytest.approx(stats.mann_whitney_u(xa, xb)["p"])
    assert pair["delta"] == pytest.approx(stats.cliffs_delta(xa, xb))
    assert pair["median_diff"] == pytest.approx(stats.median(xa) - stats.median(xb))


def test_launch_skips_are_deviations_with_their_reasons(user):
    study, _ = make_study(user, skip={("m-b", "app-2"), ("m-c", "app-3")})
    skipped_job = next(t for t in study.protocol["targets"]["resolved"] if t["label"] == "m-b/app-2#0")
    study.preflight = {
        **study.preflight,
        "launch": {
            "status": "submitted",
            "skipped": [
                {
                    "job_id": skipped_job["job_id"],
                    "label": "m-b/app-2#0",
                    "reason": "sample_changed",
                    "detail": "sample hash aaaa != protocol bbbb",
                },
            ],
        },
    }
    study.save()

    devs = {d["code"]: d for d in rows(report_mod.build_report(study), "deviations", "deviations")}

    assert devs["launch_skipped"]["severity"] == "major"
    assert devs["launch_skipped"]["items"] == ["m-b/app-2#0: sample_changed (sample hash aaaa != protocol bbbb)"]
    # The other unrun target has no recorded reason and stays "not run".
    assert devs["target_not_run"]["items"] == ["m-c/app-3#0"]


def test_correction_methods_are_a_public_constant():
    assert report_mod.CORRECTIONS == ("holm", "bh", "none")
    design, notes = report_mod.resolve_design({"correction": "bonferroni"})
    assert design["correction"] == "holm"
    assert notes


# --------------------------------------------------------------------------
# Regression tests (review fixes)
# --------------------------------------------------------------------------


def test_deleting_a_job_keeps_its_completed_target(user):
    """generation_job is SET_NULL: the target_meta job id keeps the analysed target in the report."""
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    before = report_mod.build_report(study)
    run = runs[("m-a", "app-1", 0)]
    updated_at = run.updated_at
    run.generation_job.delete()
    run.refresh_from_db()
    assert run.generation_job_id is None
    assert run.updated_at == updated_at  # the collector's UPDATE does not touch updated_at

    after = report_mod.build_report(study)
    assert after["summary"]["analysed"] == 4
    assert after["summary"]["targets_text"] == "4 / 4 / 4"
    assert str(run.id) in after["inputs"]["analysis_run_ids"]
    assert not {"target_not_run", "target_not_planned", "design_incomplete"} & deviation_codes(after)
    assert row(after, "provenance", "runs", run_id=str(run.id))["job_id"] == run.target_meta["job_id"]
    # Nothing the report reads changed, so the regenerated report is the same report.
    assert after["report_hash"] == before["report_hash"]


def _single_run_review(user, reported, sent):
    """One analysed run with three successful AI samples.

    *reported*: per sample index, the (file, category) pairs it reported;
    *sent*: per sample index, the files the reviewer showed it.
    """
    study, runs = make_study(user, models=("m-a",), apps=("app-1",), with_findings=False)
    run = runs[("m-a", "app-1", 0)]
    for sample in ReviewSample.objects.filter(result__run=run).order_by("index"):
        sample.coverage = {"files_sent": list(sent[sample.index])}
        sample.save()
        for path, category in reported[sample.index]:
            ai_finding(sample, file_path=path, category=category)
    return report_mod.build_report(study)


def test_alpha_is_one_for_perfect_agreement(user):
    """Units cover every file sent x category, so unanimous answers are perfect agreement, not NA."""
    same = [("app.py", "security"), ("util.py", "bug")]
    files = ["app.py", "util.py", "models.py"]
    report = _single_run_review(user, dict.fromkeys(range(3), same), dict.fromkeys(range(3), files))
    overall = row(report, "ai_reliability", "reliability_by_group", group="All")
    assert overall["alpha_files"] == 3
    assert overall["alpha_units"] == 3 * len(REVIEW_CATEGORIES)
    assert overall["alpha_flagged_units"] == 2
    assert overall["alpha"] == pytest.approx(1.0)
    assert overall["alpha_reading"] == "reliable"


def test_alpha_near_perfect_agreement_is_reliable(user):
    """One extra single-sample opinion among many agreed judgements stays reliable."""
    same = [("app.py", "security"), ("util.py", "bug")]
    files = ["app.py", "util.py", "models.py"]
    reported = {0: [*same, ("models.py", "style")], 1: same, 2: same}
    report = _single_run_review(user, reported, dict.fromkeys(range(3), files))
    units = [[0, 0, 0]] * (3 * len(REVIEW_CATEGORIES) - 3) + [[1, 1, 1], [1, 1, 1], [1, 0, 0]]
    overall = row(report, "ai_reliability", "reliability_by_group", group="All")
    assert overall["alpha"] == pytest.approx(stats.krippendorff_alpha_nominal(units))
    assert overall["alpha"] > 0.8
    assert overall["alpha_reading"] == "reliable"
    # Units conditioned on "reported by some sample" read the same answers as unreliable.
    assert stats.krippendorff_alpha_nominal([[1, 1, 1], [1, 1, 1], [1, 0, 0]]) < 0.5


def test_alpha_codes_unsent_files_as_missing(user):
    """A file a sample was never shown is missing for that sample, not an absence."""
    reported = {
        0: [("app.py", "security"), ("util.py", "bug")],
        1: [("app.py", "security")],
        2: [("app.py", "security")],
    }
    sent = {0: ["app.py", "util.py"], 1: ["app.py", "util.py"], 2: ["app.py"]}
    report = _single_run_review(user, reported, sent)
    others = len(REVIEW_CATEGORIES) - 1
    units = [[1, 1, 1]] + [[0, 0, 0]] * others + [[1, 0, None]] + [[0, 0, None]] * others
    overall = row(report, "ai_reliability", "reliability_by_group", group="All")
    assert overall["alpha_units"] == 2 * len(REVIEW_CATEGORIES)
    assert overall["alpha"] == pytest.approx(stats.krippendorff_alpha_nominal(units))


def test_blocked_sensitivity_uses_cells_not_replicates(user):
    models, apps = ("m-a", "m-b"), ("app-1", "app-2", "app-3")
    study, _ = make_study(user, models=models, apps=apps, reps=2)
    report = report_mod.build_report(study)
    assert report["settings"]["mode"] == "blocked"
    cells = [[stats.mean([issue_count(m, a, r) for r in (0, 1)]) for a in apps] for m in models]
    sensitivity = row(report, "inferential", "omnibus_unblocked", key=PRIMARY)
    assert (sensitivity["n"], sensitivity["units"]) == (12, 6)
    assert sensitivity["statistic"] == pytest.approx(stats.kruskal_wallis(cells)["statistic"])
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert omnibus["units"] == 6


def test_without_blocking_factor_runs_are_units(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"), reps=2, design={"block_by": None})
    report = report_mod.build_report(study)
    assert report["settings"]["mode"] == "independent"
    assert report["settings"]["analysis_plan"]["cell_aggregate"] is None
    omnibus = row(report, "inferential", "omnibus", key=PRIMARY)
    assert omnibus["n"] == omnibus["units"] == 8
    notes = report_mod.get_section(report, "inferential")["notes"]
    assert any(n.startswith("No blocking factor: every run is a test unit.") for n in notes)


def test_engine_version_drift_is_a_deviation(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    assert "engine_version_changed" not in deviation_codes(report_mod.build_report(study))

    drifted = runs[("m-a", "app-1", 0)]
    drifted.measurement = {**drifted.measurement, "version": "measure-v2"}
    drifted.save()
    resolved = runs[("m-b", "app-2", 0)]
    resolved.sample_manifest = {"resolver_version": "sample-v2", "sloc_version": "sloc-v1"}
    resolved.save()
    report = report_mod.build_report(study)
    devs = {
        d["detail"].split(":", 1)[0]: d
        for d in rows(report, "deviations", "deviations")
        if d["code"] == "engine_version_changed"
    }
    assert set(devs) == {"measurement", "sample_resolver"}  # sloc has no planned version and one observed
    assert devs["measurement"]["severity"] == "major"
    assert devs["measurement"]["detail"].startswith("measurement: planned measure-v1, observed measure-v1, measure-v2")
    assert devs["measurement"]["items"] == ["m-a/app-1#0: measure-v2"]
    assert devs["sample_resolver"]["items"] == ["m-b/app-2#0: sample-v2"]
    kv = {r["key"]: r["value"] for r in rows(report, "provenance", "provenance")}
    assert kv["engine_versions_observed"] == {
        "measurement": ["measure-v1", "measure-v2"],
        "sample_resolver": ["sample-v2"],
        "sloc": ["sloc-v1"],
    }


def test_platform_change_since_lock_is_noted(user, monkeypatch):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    assert "platform_changed" not in deviation_codes(report_mod.build_report(study))
    monkeypatch.setenv("LLMLAB_GIT_SHA", "cafef00d")
    devs = {d["code"]: d for d in rows(report_mod.build_report(study), "deviations", "deviations")}
    assert devs["platform_changed"]["severity"] == "info"
    assert "deadbeef" in devs["platform_changed"]["detail"]
    assert "cafef00d" in devs["platform_changed"]["detail"]


def test_conditions_sharing_a_label_stay_distinct_groups():
    metas = [
        {"condition_id": "11", "condition_label": "low temperature", "app_slug": "todo"},
        {"condition_id": "12", "condition_label": "low temperature", "app_slug": "todo"},
        {"condition_id": "13", "app_slug": "todo"},
    ]
    labels = [report_mod.group_label(m, ["condition"]) for m in metas]
    assert labels == ["low temperature (#11)", "low temperature (#12)", "13"]
    assert report_mod.factor_value({"condition_label": "baseline"}, "condition") == "baseline"
    assert report_mod.factor_value({}, "condition") == report_mod.NONE_LABEL
    design = {"group_by": ["condition"], "block_by": "app"}
    matrix = report_mod.design_matrix(
        [(label, report_mod.block_label(m, "app")) for label, m in zip(labels, metas, strict=True)],
        design,
    )
    assert matrix["groups"] == {"13": 1, "low temperature (#11)": 1, "low temperature (#12)": 1}
    assert matrix["replicates"] == {"min": 1, "max": 1}


# --------------------------------------------------------------------------
# Regression tests (integration round)
# --------------------------------------------------------------------------


def test_procedure_drift_and_unverified_procedures_are_deviations(user):
    from backend.analysis.models import AnalysisRun
    from backend.analysis.models import ToolResult

    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    assert not {"procedure_drift", "procedure_unverified"} & deviation_codes(report_mod.build_report(study))

    refused = runs[("m-a", "app-1", 0)]
    AnalysisRun.objects.filter(pk=refused.pk).update(
        status=AnalysisRun.Status.FAILED,
        is_complete=False,
        error_message="procedure_drift: the running analysis engine no longer implements the procedure",
    )
    tool = runs[("m-b", "app-1", 0)]
    ToolResult.objects.filter(run=tool, tool_slug="semgrep").update(
        status=ToolResult.Status.FAILED,
        diagnostics={"procedure_drift": "the semgrep spec changed"},
    )
    unverified = runs[("m-b", "app-2", 0)]
    AnalysisRun.objects.filter(pk=unverified.pk).update(summary={"procedure": {"verified": False}})

    report = report_mod.build_report(study)

    drift = row(report, "deviations", "deviations", code="procedure_drift")
    assert drift["severity"] == "major"
    assert len(drift["items"]) == 2
    assert any(item.endswith(": semgrep") for item in drift["items"])
    assert len(row(report, "deviations", "deviations", code="procedure_unverified")["items"]) == 1


def test_protocol_integrity_mismatch_is_a_deviation_and_printed(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    report = report_mod.build_report(study)
    assert "protocol_integrity" not in deviation_codes(report)
    assert "the protocol matched its sha256" in report_mod.to_markdown(report)

    # A direct database edit of the frozen protocol (its hash no longer matches).
    AnalysisStudy.objects.filter(pk=study.pk).update(protocol={**study.protocol, "threats": []})
    study.refresh_from_db()
    report = report_mod.build_report(study)
    assert report["protocol_integrity"]["match"] is False
    dev = row(report, "deviations", "deviations", code="protocol_integrity")
    assert dev["severity"] == "major"
    text = report_mod.to_markdown(report)
    assert "DID NOT MATCH its sha256" in text
    assert "| major | protocol_integrity |" in text


def test_report_analyses_the_locked_research_questions(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    report = report_mod.build_report(study)
    families = {d["key"]: d["family"] for d in report["settings"]["outcome_definitions"]}
    assert families["complexity_mean"] == "exploratory"
    assert report["settings"]["research_questions_source"] == "protocol"
    assert "research_questions_changed" not in deviation_codes(report)

    # Edited after lock (only a direct database write can do that): the protocol's questions still rule.
    AnalysisStudy.objects.filter(pk=study.pk).update(
        research_questions=[
            *study.research_questions,
            {"id": "RQ3", "text": "Complexity?", "kind": "confirmatory", "outcomes": ["complexity_mean"]},
        ],
    )
    study.refresh_from_db()
    report = report_mod.build_report(study)
    families = {d["key"]: d["family"] for d in report["settings"]["outcome_definitions"]}
    assert families["complexity_mean"] == "exploratory"
    assert [rq["id"] for rq in report["settings"]["research_questions"]] == ["RQ1", "RQ2"]
    assert row(report, "deviations", "deviations", code="research_questions_changed")["severity"] == "major"

    # Without a protocol the live questions are the only ones.
    study.protocol, study.protocol_hash = {}, ""
    report = report_mod.build_report(study)
    families = {d["key"]: d["family"] for d in report["settings"]["outcome_definitions"]}
    assert families["complexity_mean"] == "confirmatory"
    assert report["settings"]["research_questions_source"] == "study"


def test_engine_drift_reads_the_measurement_stamp(user):
    """measurement.engine_versions (normalizer, dedup, ...) is what finalized the run; it is drift evidence."""
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    study.plan = {**PLAN, "engine_versions": {**PLAN["engine_versions"], "normalizer": "norm-v1", "dedup": "dedup-v1"}}
    for run in runs.values():
        run.measurement = {**run.measurement, "engine_versions": {"normalizer": "norm-v1", "dedup": "dedup-v1"}}
        run.save()
    assert "engine_version_changed" not in deviation_codes(report_mod.build_report(study))

    drifted = runs[("m-a", "app-2", 0)]
    drifted.measurement = {**drifted.measurement, "engine_versions": {"normalizer": "norm-v2", "dedup": "dedup-v1"}}
    drifted.save()
    mixed = runs[("m-b", "app-1", 0)]
    # The measurement's stamp and the stored manifest disagree: the run mixes resolver versions.
    mixed.measurement = {**mixed.measurement, "engine_versions": {"sample_resolver": "sample-v2"}}
    mixed.sample_manifest = {"resolver_version": "sample-v1"}
    mixed.save()
    assert report_mod.run_engine_versions(mixed)["sample_resolver"] == "sample-v1 / sample-v2"
    devs = {
        d["detail"].split(":", 1)[0]: d
        for d in rows(report_mod.build_report(study), "deviations", "deviations")
        if d["code"] == "engine_version_changed"
    }
    assert set(devs) == {"normalizer", "sample_resolver"}
    assert devs["normalizer"]["items"] == ["m-a/app-2#0: norm-v2"]
    assert devs["sample_resolver"]["items"] == ["m-b/app-1#0: sample-v1 / sample-v2"]


def test_engine_drift_reads_the_executor_procedure(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1",))
    run = runs[("m-b", "app-1", 0)]
    run.summary = {"procedure": {"verified": True, "engine_versions": {"measurement": "measure-v2"}}}
    run.save()
    devs = [
        d
        for d in rows(report_mod.build_report(study), "deviations", "deviations")
        if d["code"] == "engine_version_changed"
    ]
    assert [d["items"] for d in devs] == [["m-b/app-1#0: measure-v1 / measure-v2"]]


def test_cost_is_a_lower_bound_or_unknown_never_zero(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1",), with_findings=False)
    report = report_mod.build_report(study)
    total = row(report, "cost", "cost_by_group", group="All")
    assert (total["ai_cost_total"], total["ai_cost_complete"]) == (pytest.approx(0.024), True)

    # m-a: one replicate's spend unknown, another flagged incomplete -> a lower bound.
    a = runs[("m-a", "app-1", 0)]
    samples = list(ReviewSample.objects.filter(result__run=a).order_by("index"))
    samples[0].usage = {**samples[0].usage, "cost_usd": None}
    samples[0].save()
    samples[1].usage = {**samples[1].usage, "cost_complete": False}
    samples[1].save()
    # m-b: the review was interrupted before any replicate row existed; the measurement's 0.0 is not spend.
    b = runs[("m-b", "app-1", 0)]
    ReviewSample.objects.filter(result__run=b).delete()
    ToolResult.objects.filter(run=b, tool_kind="ai").update(
        summary={"cost_usd": None, "cost_unknown": True, "max_cost_usd": 0.5},
    )
    b.measurement = {**b.measurement, "ai": {**b.measurement["ai"], "cost_usd": 0.0}}
    b.save()

    report = report_mod.build_report(study)
    by_group = {r["group"]: r for r in rows(report, "cost", "cost_by_group")}
    assert by_group["m-a"]["ai_cost_total"] == pytest.approx(0.008)
    assert by_group["m-a"]["ai_cost_complete"] is False
    assert by_group["m-b"]["ai_cost_total"] is None  # unknown, never 0
    assert by_group["m-b"]["ai_cost_complete"] is False
    assert by_group["m-b"]["prompt_tokens"] is None  # no replicate reported tokens: NA, not 0
    assert (by_group["All"]["ai_cost_total"], by_group["All"]["ai_cost_complete"]) == (pytest.approx(0.008), False)
    assert report["summary"]["ai_cost_complete"] is False
    summary = row(report, "cost", "cost_summary", metric="AI review cost (analysis set)")
    assert (summary["value"], summary["complete"]) == (pytest.approx(0.008), False)


def test_cost_counts_spend_of_replaced_attempts(user):
    study, runs = make_study(user, models=("m-a",), apps=("app-1",), with_findings=False)
    run = runs[("m-a", "app-1", 0)]
    ToolResult.objects.filter(run=run, tool_kind="ai").update(
        summary={"cost_usd": 0.012, "prior_attempts": 1, "prior_attempts_cost_usd": 0.02},
    )
    total = row(report_mod.build_report(study), "cost", "cost_by_group", group="All")
    assert (total["ai_cost_total"], total["ai_cost_complete"]) == (pytest.approx(0.032), True)
    ToolResult.objects.filter(run=run, tool_kind="ai").update(
        summary={
            "cost_usd": 0.012,
            "prior_attempts": 2,
            "prior_attempts_cost_usd": 0.02,
            "prior_attempts_cost_unknown": True,
        },
    )
    total = row(report_mod.build_report(study), "cost", "cost_by_group", group="All")
    assert (total["ai_cost_total"], total["ai_cost_complete"]) == (pytest.approx(0.032), False)


def test_run_ai_cost_without_review():
    class Run:
        measurement = {}

    assert report_mod.run_ai_cost(Run(), [], []) == (None, None)
    Run.measurement = {"ai": {"cost_usd": 0.5, "cost_complete": False}}
    assert report_mod.run_ai_cost(Run(), [], []) == (0.5, False)


def test_funnel_shares_never_exceed_one(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"), lock=False)
    targets = study.preflight["targets"]
    targets[0] = {**targets[0], "eligible": False, "reasons": [{"code": "empty_output", "severity": "exclude"}]}
    study.preflight = {**study.preflight, "targets": targets}
    study.protocol = report_mod.build_protocol(study)
    study.protocol_hash = report_mod.protocol_hash(study.protocol)
    study.save()
    funnel = {r["key"]: r for r in rows(report_mod.build_report(study), "data_quality", "funnel")}
    assert (funnel["planned"]["n"], funnel["eligible"]["n"]) == (4, 3)
    assert funnel["planned"]["share"] is None  # a superset of the eligible targets: undefined, not 133%
    assert funnel["planned"]["share_of_protocol"] == pytest.approx(1.0)
    assert funnel["eligible"]["share"] == pytest.approx(1.0)
    assert funnel["eligible"]["share_of_protocol"] == pytest.approx(0.75)
    shares = [r[k] for r in funnel.values() for k in ("share", "share_of_protocol") if r[k] is not None]
    assert max(shares) <= 1.0


def test_exclusions_count_only_excluding_reasons(user):
    study, _ = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"), lock=False)
    targets = study.preflight["targets"]
    targets[0] = {
        **targets[0],
        "eligible": False,
        "reasons": [
            {"code": "empty_output", "severity": "exclude"},
            {"code": "functional_unknown", "severity": "info"},
            {"code": "truncation_unknown", "severity": "warn"},
        ],
    }
    study.preflight = {**study.preflight, "targets": targets}
    study.protocol = report_mod.build_protocol(study)
    study.protocol_hash = report_mod.protocol_hash(study.protocol)
    study.save()
    exclusions = rows(report_mod.build_report(study), "data_quality", "exclusions")
    assert [(r["stage"], r["reason"], r["n"]) for r in exclusions if r["stage"] == "protocol"] == [
        ("protocol", "empty_output", 1),
    ]


def test_tool_overlap_table_keeps_pairs_with_issues(user):
    plan = {
        **PLAN,
        "tools": [*PLAN["tools"], {**PLAN["tools"][0], "slug": "radon", "name": "Radon", "config_hash": "c3" * 32}],
    }
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1",))
    study.plan = plan
    for run in runs.values():
        ToolResultFactory(run=run, tool_slug="radon", category="complexity", position=3, status="succeeded")
    report = report_mod.build_report(study)
    pairs = [(r["tool_a"], r["tool_b"]) for r in rows(report, "tool_overlap", "pairwise_jaccard")]
    assert pairs == [("bandit", "semgrep"), ("bandit", "radon"), ("semgrep", "radon")]
    # radon found nothing, but the pairs with bandit/semgrep are kept (one side has issues).
    study.plan = {**plan, "tools": [*plan["tools"], {**plan["tools"][2], "slug": "lizard", "config_hash": "c4" * 32}]}
    for run in runs.values():
        ToolResultFactory(run=run, tool_slug="lizard", category="complexity", position=4, status="succeeded")
    report = report_mod.build_report(study)
    pairs = [(r["tool_a"], r["tool_b"]) for r in rows(report, "tool_overlap", "pairwise_jaccard")]
    assert ("radon", "lizard") not in pairs
    table = report_mod.get_table(report, "tool_overlap", "pairwise_jaccard")
    assert any("1 pair(s) where neither tool found a counted issue are omitted" in n for n in table["notes"])
    heatmap = next(s for s in report_mod.get_section(report, "tool_overlap")["series"] if s["key"] == "jaccard_matrix")
    assert len(heatmap["pairs"]) == 6  # every pair of the 4 tools stays in the chart data
    assert ("radon", "lizard") in [(p["tool_a"], p["tool_b"]) for p in heatmap["pairs"]]


def test_descriptive_series_carry_the_true_missing_count(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1", "app-2"))
    run = runs[("m-a", "app-1", 0)]
    run.measurement = make_measurement(static_ok=False)
    run.save()
    report = report_mod.build_report(study)
    section = report_mod.get_section(report, "descriptives")
    box = next(s for s in section["series"] if s["key"] == f"box.{PRIMARY}")
    groups = {g["group"]: g for g in box["groups"]}
    assert (groups["m-a"]["n"], groups["m-a"]["n_missing"]) == (1, 1)
    assert groups["m-b"]["n_missing"] == 0
    table = {r["group"]: r for r in report_mod.get_table(report, "descriptives", f"outcome.{PRIMARY}")["rows"]}
    assert groups["m-a"]["n_missing"] == table["m-a"]["n_missing"]
    proportion = next(s for s in section["series"] if s["key"] == "box.has_high_security")
    assert proportion["kind"] == "proportion"
    pa = {g["group"]: g for g in proportion["groups"]}["m-a"]
    assert (pa["n"], pa["n_missing"], pa["successes"]) == (1, 1, 1)
    assert pa["share"] == pytest.approx(1.0)


def test_protocol_text_describes_run_limits(user):
    study, runs = make_study(user, models=("m-a", "m-b"), apps=("app-1",), lock=False)
    targets = study.preflight["targets"]
    targets[0] = {
        **targets[0],
        "reasons": [{"code": "run_stopped", "severity": "info", "detail": "the agent run ended at a run-level limit"}],
    }
    study.preflight = {**study.preflight, "targets": targets}
    study.protocol = report_mod.build_protocol(study)
    study.protocol_hash = report_mod.protocol_hash(study.protocol)
    study.save()
    text = report_mod.protocol_to_markdown(study.protocol)
    assert "`exclude_run_limits` = no: Agent runs that ended at a run-level limit (run_stopped" in text
    assert "run_stopped is not output truncation" in text
    assert "1 target(s) ended at a run-level limit (run_stopped): 1 kept, 0 excluded." in text
    assert "`exclude_truncated` = yes" in text

    stopped = runs[("m-a", "app-1", 0)]
    stopped.target_meta = {**stopped.target_meta, "run_stop_reasons": ["termination:timeout"]}
    stopped.save()
    notes = report_mod.get_section(report_mod.build_report(study), "data_quality")["notes"]
    assert any(n.startswith("1 analysed target(s) ended at a run-level limit") for n in notes)
    assert any("exclude_run_limits off" in n for n in notes)
