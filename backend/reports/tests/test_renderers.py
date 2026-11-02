"""Markdown/LaTeX rendering of stored reports: legacy payloads, labels, ranks, escapes and nested data."""

from __future__ import annotations

from backend.reports.models import Report
from backend.reports.services import renderers


def _md(report_type: str, data: dict) -> str:
    return renderers.render_markdown(Report(report_type=report_type, title="T", report_data=data))


def _experiment_payload(**results) -> dict:
    return {
        "experiment": {"name": "E", "status": "completed", "repeats": 2},
        "conditions": [
            {
                "condition_id": 1,
                "label": "vendor/a / profile",
                "issues_dedup": {"apps": 4, "unique_issues": 182, "unique_per_kloc": 39.66},
            },
            {
                "condition_id": 2,
                "label": "vendor/b / profile",
                "issues_dedup": {"apps": 4, "unique_issues": 90, "unique_per_kloc": 23.21},
            },
        ],
        "results": {
            "baseline_condition_id": 1,
            "baseline_source": "preregistered",
            "leaderboard": [
                {"condition_id": 1, "label": "Vendor A / profile", "rank": 1, "pass_rate_mean": 1.0},
                {"condition_id": 2, "label": "Vendor B / profile", "rank": 2, "pass_rate_mean": 1.0},
            ],
            **results,
        },
    }


def test_legacy_experiment_report_is_not_relabelled():
    """A payload from before the code/dependency split and Holm-only paired rows (rpt_272a51a8cb1db9da's shape)."""
    data = _experiment_payload(
        paired={"rows": [{"condition_id": 2, "n_pairs": 2, "mean_delta": 0.0, "wilcoxon_p": None, "p_holm": 0.5}]},
    )
    md = _md(Report.Type.EXPERIMENT_REPORT, data)
    assert "Unique issues / kLOC (incl. dependency advisories)" in md
    assert "| Code issues / kLOC |" not in md
    assert "regenerate the report for code-issue densities" in md
    assert "| p (holm) | Significant |" in md
    assert "< None" not in md
    assert "p (none)" not in md
    assert "| Vendor B / profile | 2 | 0 | - | - | - | - | - | 0.5 | - |" in md


def test_experiment_report_names_each_condition_once_and_ties_share_a_rank():
    data = _experiment_payload(
        paired={
            "status": "confirmatory",
            "correction": "holm",
            "alpha": 0.05,
            "rows": [{"condition_id": 2, "n_pairs": 2, "mean_delta": 0.0, "wilcoxon_p": None, "p_adjusted": None}],
        },
    )
    md = _md(Report.Type.EXPERIMENT_REPORT, data)
    # The leaderboard's (Results tab's) name everywhere, not the model-id fallback.
    assert "vendor/a / profile" not in md
    assert "| Baseline | Vendor A / profile (preregistered) |" in md
    assert "| Vendor B / profile | 2 | 0 |" in md
    # Equal mean pass rates share rank 1.
    assert "| 1 | Vendor A / profile |" in md
    assert "| 1 | Vendor B / profile |" in md


def test_repeats_beyond_the_plan_are_not_missing():
    data = _experiment_payload()
    data["results"]["leaderboard"][0]["n_missing"] = {"failed": 1, "ungraded": 0, "beyond_plan": 2}
    data["results"]["leaderboard"][1]["n_missing"] = {"failed": 0, "ungraded": 0, "beyond_plan": 0}
    md = _md(Report.Type.EXPERIMENT_REPORT, data)
    assert "| Rank | Condition | Apps | Graded | Missing | Beyond plan |" in md
    assert "| 1 | Vendor A / profile | - | - | 1 | 2 |" in md
    data["results"]["leaderboard"][0]["n_missing"]["beyond_plan"] = 0
    assert "Beyond plan" not in _md(Report.Type.EXPERIMENT_REPORT, data)


def test_latex_escapes_comparison_and_bar_characters():
    assert renderers.tex_escape("p < 0.05 | a > b") == r"p \textless{} 0.05 \textbar{} a \textgreater{} b"


def test_legacy_study_payload_keeps_its_old_meaning():
    """Study payloads stored before report version 2: normalized severity of every kind, labelled as such."""
    data = {
        "protocol": {"jobs": 2, "models": ["m"]},
        "issues": {
            "by_model": [{"model": "m", "apps": 2, "unique_issues": 5, "unique_per_kloc": 9.0, "unique_critical": 1}],
            "by_tool_rule": [{"tool": "osv-scanner", "rule_id": "X", "findings": 1, "max_severity": "high"}],
        },
        "security_bounds": {"overall": [{"severity": "critical", "issues": 1, "apps_affected": 1, "apps": 2}]},
    }
    md = _md(Report.Type.STUDY_REPORT, data)
    assert "Unique issues / kLOC (incl. dependency advisories)" in md
    assert "Critical (normalized, all kinds)" in md
    assert "Max severity (normalized, heuristic)" in md
    assert "## Issues of every kind by normalized severity (heuristic), with bounds" in md


def test_template_comparison_keeps_nested_metrics():
    data = {
        "template": {"slug": "crud", "name": "Todo"},
        "models": [
            {
                "model_id": "vendor/a",
                "bundle_key": "agent:x@1",
                "total_findings": 212,
                "findings": {"critical": 4, "high": 41, "medium": 154, "low": 13, "info": 0},
                "generation": {"total_jobs": 5, "completed_jobs": 5, "total_cost": 0.23},
                "loc": {"total_loc": 5782, "per_job": [{"job_id": "j1", "total_loc": 1141}]},
                "issues_dedup": {"code_unique": 8, "unique_per_kloc": 3.74, "dependency_unique": 82},
                "stats": {"analyzed": 2, "functional": {"pass_rate": {"n": 5, "mean": 1.0}}},
            },
            {
                "model_id": "vendor/b",
                "bundle_key": "agent:x@1",
                "total_findings": 0,
                "generation": {"total_jobs": 1, "completed_jobs": 1},
                "loc": {"total_loc": 900},
                "issues_dedup": {"code_unique": 0},
                "stats": {"analyzed": 0},
            },
        ],
        "sensitivity": {
            "schemes": [
                {
                    "scheme": "security_heavy",
                    "weights": {"critical": 10, "high": 5, "medium": 1, "low": 0, "info": 0},
                    "ranking": [{"model_id": "vendor/a", "empirical_quality": 0.54}],
                },
            ],
        },
    }
    md = _md("template_comparison", data)
    # Curated: success, LOC, code issues, density; an unanalyzed model reads '-', not 0 findings.
    assert "| vendor/a | agent:x@1 | 5 | 5 | 2 | 5782 | 212 | - | 8 | 3.74 |" in md
    assert "| vendor/b | agent:x@1 | 1 | 1 | 0 | 900 | - | - | - | - |" in md
    # dependency_added compares the version too (P2): the column says so.
    assert "| In versions the model chose |" in md
    # Every nested field is somewhere: generation, LOC per job, pass rate, the per-scheme rankings.
    assert "generation.total_jobs" in md
    assert "stats.functional.pass_rate.mean" in md
    assert "## models / loc.per_job" in md
    assert "| vendor/a | j1 | 1141 |" in md
    assert "## sensitivity / schemes / ranking" in md
    assert "| security_heavy | vendor/a | 0.54 |" in md
    # Normalized severity buckets are labelled.
    assert "findings.high (normalized)" in md
    assert "weights.critical (normalized)" in md
    assert renderers._NORMALIZED_NOTE in md


def test_generic_render_recurses_and_tabulates_mappings():
    data = {
        "generation_analytics": {
            "window_days": 30,
            "summary": {"total_jobs": 47, "success_rate": 0.68, "by_status": {"completed": 32, "failed": 1}},
            "loc": {"total_loc": 46500},
        },
        "metrics_by_tool": {
            "radon": {
                "numeric": {"max_complexity": {"avg": 15.2, "max": 35, "min": 6}},
                "distributions": {"rank_distribution": {"A": 264, "B": 44}},
                "results_with_metrics": 10,
            },
        },
        "tools": [{"analyzer": "osv-scanner", "total": 3, "by_severity": {"critical": 1, "high": 2, "low": 0}}],
    }
    md = _md("comprehensive", data)
    assert "## generation_analytics / summary" in md
    assert "| total_jobs | 47 |" in md
    assert "## generation_analytics / summary / by_status" in md
    assert "## generation_analytics / loc" in md
    assert "## metrics_by_tool" in md
    assert "| radon / numeric / max_complexity |" in md
    assert "| radon / distributions / rank_distribution |" in md
    assert "by_severity.critical (normalized)" in md
    tex = renderers.render_latex(Report(report_type="comprehensive", title="T", report_data=data))
    assert "radon / numeric / max\\_complexity" in tex


def test_wide_record_lists_are_split_not_cut():
    rows = [{"name": "a", **{f"c{i:02d}": i for i in range(30)}}]
    tables = renderers._generic_tables({"rows": rows})
    split = [t for t in tables if t.title.startswith("rows")]
    assert len(split) == 3
    assert all(t.columns[0] == ("name", "name") for t in split)
    shown = {key for t in split for key, _ in t.columns}
    assert shown == {"name", *(f"c{i:02d}" for i in range(30))}


def test_protocol_prints_the_recorded_builds_not_catalog_pins():
    data = {
        "protocol": {
            "jobs": 3,
            "models": ["m"],
            "tools": [
                {"slug": "osv-scanner", "version": "2.6", "installed_versions": {"osv-scanner version: 2.6.0": 3}},
                {"slug": "eslint", "version": "8", "installed_versions": {"v8.57.1": 2, "v9.1.0": 1}},
                {"slug": "hadolint", "version": "2.12", "installed_versions": {}},
            ],
        },
    }
    md = _md(Report.Type.STUDY_REPORT, data)
    assert (
        "| Tools run (recorded builds) | osv-scanner 2.6.0, eslint v8.57.1 (2 runs) / v9.1.0 (1 run), "
        "hadolint 2.12 (catalog pin; build not recorded) |"
    ) in md


def test_levels_the_tools_printed_are_never_labelled_normalized():
    """npm audit and nuclei print lowercase level names; their counts are still the tool's own labels (P1)."""
    data = {
        "tools": [{"analyzer": "npm-audit", "reported_levels": {"high": 28, "low": 2, "critical": 1}}],
        "reported_levels": {"high": 3, "medium": 2, "low": 1},
        "by_tool": {"nuclei": {"reported_levels": {"info": 3, "low": 1, "medium": 2}}},
    }
    md = _md("tool_analysis", data)
    assert "(normalized" not in md
    assert renderers._NORMALIZED_NOTE not in md
    assert "| npm-audit | high: 28, low: 2, critical: 1 |" in md
    # Normalized buckets elsewhere keep their label.
    md = _md("tool_analysis", {"tools": [{"analyzer": "x", "by_severity": {"high": 1, "low": 2, "info": 0}}]})
    assert "by_severity.high (normalized)" in md


def test_a_field_unmeasured_in_some_rows_is_not_an_empty_column():
    data = {
        "models": [
            {"model_id": "a", "x": 1, "reported_levels": None, "severity_normalized": None},
            {
                "model_id": "b",
                "x": 2,
                "reported_levels": {"MODERATE": 3},
                "severity_normalized": {"critical": 0, "high": 1, "medium": 2},
            },
        ],
    }
    (table,) = [t for t in renderers._generic_tables(data) if t.title == "models"]
    columns = [k for k, _ in table.columns]
    assert "severity_normalized" not in columns  # its values are in the dotted columns
    assert "severity_normalized.high (normalized)" in columns
    md = _md("tool_analysis", data)
    assert "| b | 2 | MODERATE: 3 | 0 | 1 | 2 |" in md
    assert "| a | 1 | - | - | - | - |" in md


def test_experiment_code_issue_table_shows_kinds_apart_and_the_sample():
    data = _experiment_payload()
    data["conditions"][0]["issues_dedup"] = {
        "apps": 2,
        "runs": 4,
        "unique_issues": 178,
        "code_unique": 18,
        "unique_per_kloc": 3.92,
        "security_unique": 5,
        "code_unique_tests": 0,
        "runtime_unique": None,
        "dependency_unique": 160,
        "dependency_added": 0,
        "dependency_inherited": 160,
    }
    data["sample"] = {
        "planned_repeats": 2,
        "exclude_truncated": True,
        "truncation_rule": "analysis_plan",
        "truncated_excluded": 1,
    }
    md = _md(Report.Type.EXPERIMENT_REPORT, data)
    assert "| Planned repeats (the analyzed sample) | 2 |" in md
    assert "| Truncated runs | excluded (frozen analysis plan; 1 excluded) |" in md
    assert (
        "| Condition | Apps analyzed | Runs analyzed | Raw findings | Unique issues (all kinds) | Code issues "
        "| Code issues / kLOC | Security (code) | In test code | Runtime (DAST) | Dependency advisories "
        "| In versions the model chose | Inherited (scaffold pins or transitive) | Generation cost (USD) |"
    ) in md
    assert "| Vendor A / profile | 2 | 4 | - | 178 | 18 | 3.92 | 5 | 0 | - | 160 | 0 | 160 | - |" in md
    assert "counted only from the tools that measured every analyzed run" in md


def test_condition_grouped_payloads_without_by_group_use_the_group_labels():
    """Stored before ``by_group``: per-condition rows get the funnel's labels; jobs outside an experiment say so."""
    data = {
        "protocol": {"jobs": 4, "models": ["m"], "grouped_by": "condition", "report_version": 2},
        "per_app": [
            {"condition_id": 1, "group": "Same (#1)"},
            {"condition_id": 2, "group": "Same (#2)"},
            {"condition_id": None, "group": "m"},
        ],
        "issues": {
            "by_condition": [
                {"condition_id": 1, "condition_label": "Same", "apps": 1, "code_unique": 3},
                {"condition_id": 2, "condition_label": "Same", "apps": 1, "code_unique": 4},
                {"condition_id": None, "condition_label": "", "apps": 2, "code_unique": 5},
            ],
        },
        "healing": {
            "baseline": "full",
            "modes": ["full", "none"],
            "by_condition": [{"condition_id": 2, "condition_label": "Same", "apps": 2, "deploy_rate_full": 100.0}],
            "totals": {"apps": 2, "deploy_rate_full": 100.0},
        },
    }
    md = _md(Report.Type.STUDY_REPORT, data)
    issues = md.split("## Issues by condition (deduplicated)")[1].split("\n## ")[0]
    assert "| Same (#1) | 1 |" in issues
    assert "| Same (#2) | 1 |" in issues
    assert "| outside an experiment (all models) | 2 |" in issues
    healing = md.split("## Deploy rate with and without build healing")[1].split("\n## ")[0]
    assert "| Condition | Runs | Deploy % (full) |" in healing
    assert "| Same (#2) | 2 | 100 |" in healing


def test_healing_table_shows_apps_and_runs_when_the_payload_has_both():
    data = {
        "protocol": {"jobs": 2, "models": ["m"], "report_version": 2},
        "healing": {
            "baseline": "full",
            "modes": ["full", "none"],
            "by_model": [{"model": "m", "apps": 1, "runs": 2, "deploy_rate_full": 50.0}],
            "totals": {"apps": 1, "runs": 2, "deploy_rate_full": 50.0},
        },
    }
    healing = _md(Report.Type.STUDY_REPORT, data).split("## Deploy rate with and without build healing")[1]
    healing = healing.split("\n## ")[0]
    assert "| Model | Apps | Runs | Deploy % (full) |" in healing
    assert "| m | 1 | 2 | 50 |" in healing


def test_manifest_findings_are_shown_apart_from_advisories():
    data = {
        "protocol": {"jobs": 2, "models": ["m"], "report_version": 2},
        "issues": {
            "by_model": [
                {"model": "m", "apps": 2, "code_unique": 5, "manifest_unique": 3, "manifest_unused_scaffold": 2},
            ],
        },
    }
    md = _md(Report.Type.STUDY_REPORT, data)
    assert "## Dependency manifest findings by model (code issues)" in md
    assert "| m | 2 | 3 | 2 |" in md
    data["issues"]["by_model"][0]["manifest_unique"] = None  # no manifest checker measured the apps
    assert "Dependency manifest findings" not in _md(Report.Type.STUDY_REPORT, data)


def test_correlations_without_any_model_are_left_out_with_a_note():
    data = {
        "protocol": {"jobs": 2, "models": ["m"], "report_version": 2},
        "correlations": {
            "tests": [
                {"predictor": "context_window_nominal", "outcome": "pass_rate", "n_models": 0},
                {"predictor": "max_output_tokens", "outcome": "pass_rate", "n_models": 3, "rho": 0.5},
            ],
            "notes": ["Observational."],
        },
    }
    md = _md(Report.Type.STUDY_REPORT, data)
    section = md.split("## Model parameters vs outcomes (Spearman, exploratory)")[1].split("\n## ")[0]
    assert "| max_output_tokens | pass_rate | 3 | 0.5 |" in section
    assert "context_window_nominal | pass_rate" not in section
    assert "Not tested (1 of 2)" in section
    assert "predictors: context_window_nominal" in section


def test_an_empty_table_still_shows_why():
    data = {
        "protocol": {"jobs": 2, "models": ["m"], "report_version": 2},
        "correlations": {"tests": [{"predictor": "max_output_tokens", "outcome": "pass_rate", "n_models": 0}]},
    }
    section = _md(Report.Type.STUDY_REPORT, data).split("## Model parameters vs outcomes (Spearman, exploratory)")[1]
    section = section.split("\n## ")[0]
    assert "_No data._" in section
    assert "Not tested (1 of 1)" in section


def test_code_size_table_shows_lizard_and_jscpd_with_their_n_and_a_dash_when_unmeasured():
    """The study's lizard / jscpd code metrics reach the rendered table; unmeasured is '-', never 0."""
    measured = {"n": 3, "mean": 2.7133, "median": 2.5, "q1": 2.0, "q3": 3.0, "min": 1.5, "max": 4.0}
    duplication = {"n": 2, "mean": 3.1, "median": 2.41, "q1": 1.0, "q3": 4.0, "min": 0.0, "max": 8.45}
    none = {"n": 0, "mean": None, "median": None, "q1": None, "q3": None, "min": None, "max": None}
    data = {
        "protocol": {"jobs": 4, "models": ["a", "b"], "report_version": 2},
        "code_metrics": {
            "by_model": [
                {"model": "a", "apps": 3, "lizard_avg_ccn": measured, "jscpd_duplication_percentage": duplication},
                {"model": "b", "apps": 1, "lizard_avg_ccn": none, "jscpd_duplication_percentage": none},
            ],
        },
    }
    md = _md(Report.Type.STUDY_REPORT, data)
    section = md.split("## Code size and complexity by model")[1].split("\n## ")[0]
    assert "| Mean CC | Max CC | Mean CCN (lizard) | Duplication % (jscpd, median) |" in section
    assert "| 2.713 (n=3) | 2.41 (n=2) |" in section
    assert "| b | 1 | - | - | - | - | - | - | - | - | - |" in section
    assert "'-' = it measured no app of the group" in section
    latex = renderers.render_latex(Report(report_type=Report.Type.STUDY_REPORT, title="T", report_data=data))
    assert "Mean CCN (lizard) & Duplication \\% (jscpd, median)" in latex
    assert "2.713 (n=3) & 2.41 (n=2)" in latex
    # A payload stored before these metrics has no such keys: no column claiming they were unmeasured.
    for row in data["code_metrics"]["by_model"]:
        del row["lizard_avg_ccn"], row["jscpd_duplication_percentage"]
    legacy = _md(Report.Type.STUDY_REPORT, data).split("## Code size and complexity by model")[1].split("\n## ")[0]
    assert "lizard" not in legacy
    assert "| Model | Apps | LOC median | Q1 | Q3 | Backend share | Files | Mean CC | Max CC |" in legacy
