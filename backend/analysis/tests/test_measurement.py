"""Measurement (measure-v2): availability, NA rules, densities, issues, AI block, summary, outcomes."""

from __future__ import annotations

import pytest

from backend.analysis.engine import measurement
from backend.analysis.engine.types import MEASUREMENT_VERSION
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisStudyFactory
from backend.analysis.tests.support import measure as fx

BASE_APP = "import os\n\napp = None\n"
FINAL_APP = "import os\nimport subprocess  # noqa: F401\n\napp = None\n" + "".join(f"x{i} = {i}\n" for i in range(20))

PLAN = {
    "schema_version": 1,
    "tools": [
        {"slug": s, "required": r}
        for s, r in [
            ("ruff", True),
            ("bandit", True),
            ("semgrep", True),
            ("mypy", True),
            ("eslint", False),
            ("gitleaks", True),
            ("radon", True),
            ("jscpd", True),
        ]
    ],
    "reviewer": {"enabled": True, "samples": 3, "engine": "single_shot", "model": {"slug": "deepseek/x"}},
    "outcomes": {"min_sloc_for_density": 10},
}
CATEGORIES = {
    "ruff": "lint",
    "bandit": "security",
    "semgrep": "security",
    "mypy": "types",
    "eslint": "lint",
    "gitleaks": "secrets",
    "radon": "complexity",
    "jscpd": "duplication",
}
META = {
    "status": "completed",
    "truncated": False,
    "truncation_reasons": [],
    "empty_output": False,
    "validity": {"valid": True, "exclude": False, "notes": []},
    "functional": {"status": "passed", "pass_rate": 0.9333, "source": "conformance", "evaluator_version": 1},
}


@pytest.fixture
def sample():
    return fx.build_sample(
        [
            ("app.py", FINAL_APP, "modified"),
            ("frontend/src/App.jsx", "export default function App() {\n  return <p/>;\n}\n", "added"),
            ("frontend/src/main.jsx", "import './App.css';\n", "scaffold"),
            ("frontend/package-lock.json", "{}\n", "added"),
            ("README.md", "# Todo\n", "added"),
        ],
        baseline={"app.py": BASE_APP.encode()},
    )


def build_run(sample, *, plan=None, meta=None, statuses=None, ai_status="succeeded", **kwargs):
    run = fx.make_run(sample=sample, plan=plan or PLAN, meta=META if meta is None else meta, **kwargs)
    results = {}
    for position, tool in enumerate((plan or PLAN)["tools"]):
        slug = tool["slug"]
        results[slug] = fx.add_result(
            run,
            slug,
            status=(statuses or {}).get(slug, ToolResult.Status.SUCCEEDED),
            required=tool["required"],
            kind="metrics" if slug in ("radon", "jscpd") else "static",
            category=CATEGORIES.get(slug, "lint"),
            position=position,
        )
    if ((plan or PLAN).get("reviewer") or {}).get("enabled") and ai_status is not None:
        results["ai_review"] = fx.add_result(
            run,
            "ai_review",
            status=ai_status,
            required=True,
            kind="ai",
            category="ai",
            position=99,
            metrics={"agreement": {"mean_pairwise_jaccard": 0.5, "threshold": 2}},
        )
    return run, results


def populate(results):
    """A realistic finding mix: one cross-tool issue, a secret, dead code, style, scaffold noise, AI."""
    fx.add_finding(
        results["bandit"],
        rule_id="B602",
        severity="high",
        category="security",
        cwe=["CWE-78"],
        line_number=2,
        file_origin="modified",
        issue_key="k-shell",
    )
    fx.add_finding(
        results["semgrep"],
        rule_id="x.shell",
        severity="high",
        category="security",
        cwe=["CWE-78"],
        line_number=2,
        file_origin="modified",
        issue_key="k-shell",
    )
    fx.add_finding(
        results["gitleaks"],
        rule_id="github-pat",
        severity="high",
        category="secrets",
        cwe=["CWE-798"],
        line_number=6,
        file_origin="modified",
        issue_key="k-secret",
    )
    fx.add_finding(
        results["ruff"],
        rule_id="F401",
        severity="low",
        category="dead_code",
        line_number=2,
        file_origin="modified",
        issue_key="k-unused",
    )
    fx.add_finding(
        results["ruff"],
        rule_id="E501",
        severity="info",
        category="style",
        line_number=7,
        counted=False,
        file_origin="modified",
        issue_key="u-style",
    )
    fx.add_finding(
        results["ruff"],
        rule_id="F401",
        severity="low",
        category="dead_code",
        line_number=1,
        counted=False,
        in_measured_scope=False,
        file_origin="scaffold",
        issue_key="u-scaffold",
    )
    ai = results.get("ai_review")
    if ai is not None:
        for index in range(3):
            fx.add_review_sample(
                ai,
                index,
                status="succeeded",
                usage={
                    "prompt_tokens": 1000,
                    "completion_tokens": 200,
                    "reasoning_tokens": 50,
                    "cached_tokens": 10,
                    "cost_usd": 0.0015,
                },
                grounding={"exact": 2, "relocated": 1, "ungrounded": 1, "invalid_file": 0, "out_of_range": 0},
            )
        for _ in range(2):
            fx.add_finding(
                ai,
                rule_id="security",
                severity="high",
                category="security",
                cwe=["CWE-78"],
                line_number=5,
                is_ai=True,
                is_consensus=True,
                issue_key="a-shell",
                file_origin="modified",
            )
        fx.add_finding(
            ai,
            rule_id="bug",
            severity="low",
            category="bug",
            line_number=9,
            is_ai=True,
            counted=False,
            issue_key="a-noise",
            file_origin="modified",
        )


@pytest.mark.django_db
def test_compute_full_run(sample):
    run, results = build_run(sample)
    populate(results)
    results["radon"].metrics = {
        "functions": [
            {"path": "app.py", "name": "f", "line": 5, "complexity": 4},
            {"path": "/src/app.py", "name": "g", "line": 9, "complexity": 12},
            {"path": "frontend/src/main.jsx", "name": "scaffold", "line": 1, "complexity": 40},
        ],
    }
    results["radon"].save()
    results["jscpd"].metrics = {
        "percentage": 50.0,
        "clones": 2,
        "files": {
            "app.py": {"lines": 24, "duplicated_lines": 6},
            "frontend/src/main.jsx": {"lines": 1, "duplicated_lines": 1},
        },
    }
    results["jscpd"].save()
    # A file outside the measured scope that a tool could not parse: no share of the measured SLOC.
    results["eslint"].coverage = {
        "files_in_scope": 2,
        "files_scanned": 1,
        "unparseable": [{"path": "frontend/src/main.jsx", "reason": "x"}],
    }
    results["eslint"].save()

    m = measurement.compute(run)
    measured = sample.manifest()["totals"]["measured_sloc"]
    assert measured == 24  # 21 model lines of app.py (line-level) + 3 of App.jsx
    kloc = measured / 1000

    assert m["schema_version"] == 1
    assert m["version"] == MEASUREMENT_VERSION
    assert (m["plan_hash"], m["sample_hash"]) == (run.plan_hash, sample.sample_hash)
    assert m["complete"] is True
    assert m["required_failed"] == []
    assert m["tool_status"]["ai_review"] == "succeeded"
    assert m["sloc"]["measured"] == 24
    assert m["sloc"]["by_language"] == {"jsx": 3, "python": 21}
    assert m["sloc"]["by_origin"] == {"added": 3, "modified": 21, "scaffold": 3}
    assert m["files"] == {"measured": 2, "included": 4, "excluded": 1}

    static = m["static"]
    assert static["available"] is True
    assert (static["unavailable_reasons"], static["incomplete_tools"]) == ([], [])
    assert static["findings"]["total"] == 4
    assert static["findings"]["by_tool"] == {"bandit": 1, "gitleaks": 1, "ruff": 1, "semgrep": 1}
    assert static["issues"]["total"] == 3
    assert static["issues"]["by_severity"] == {"critical": 0, "high": 2, "medium": 0, "low": 1, "info": 0}
    assert static["issues"]["by_category"] == {"dead_code": 1, "secrets": 1, "security": 1}
    assert static["weighted"] == 11.0
    assert static["findings_per_kloc"] == round(4 / kloc, 4)
    assert static["issues_per_kloc"] == round(3 / kloc, 4)
    assert static["security_issues"] == 2
    assert static["high_plus_issues"] == 2
    assert static["has_high_security"] is True
    assert static["category_available"]["security"] is True
    assert static["category_available"]["type"] is True
    assert static["category_available"]["container"] is False  # no hadolint planned
    assert (static["style_findings"], static["out_of_scope_findings"], static["scaffold_findings"]) == (1, 1, 1)
    assert static["uncounted_findings"] == 2

    ai = m["ai"]
    assert (ai["available"], ai["samples_ok"], ai["samples"], ai["threshold"]) == (True, 3, 3, 2)
    assert ai["consensus"]["total"] == 1
    assert ai["consensus"]["by_severity"]["high"] == 1
    assert ai["per_kloc"] == round(1 / kloc, 4)
    assert (ai["per_kloc_unavailable_reasons"], ai["uncovered_measured_sloc"]) == ([], 0)
    assert ai["grounding"] == {"exact": 6, "relocated": 3, "ungrounded": 3, "invalid_file": 0, "out_of_range": 0}
    assert ai["grounding_rate"] == 0.75
    assert ai["agreement"] == {
        "mean_pairwise_jaccard": 0.5,
        "planned_samples": 3,
        "successful_samples": 3,
        "replicates_complete": True,
    }
    assert (ai["planned_samples"], ai["replicates_complete"]) == (3, True)
    assert ai["cost_usd"] == pytest.approx(0.0045)
    assert (ai["cost_complete"], ai["cost_incomplete_samples"], ai["cost_upper_bound_usd"]) == (True, 0, None)
    assert ai["tokens"] == {"prompt": 3000, "completion": 600, "reasoning": 150, "cached": 30, "total": 3600}
    assert ai["tokens_complete"] is True
    assert ai["replaced"] == {"attempts": 0, "cost_usd": 0.0, "cost_unknown": False}
    assert m["engine_versions"] == measurement.engine_versions()
    assert ai["overlap_with_static"]["matched"] == 1

    complexity = m["metrics"]["complexity"]
    assert complexity["source"] == "radon"
    assert complexity["scope"] == "measured_files"
    assert complexity["attribution"] == "line_level"  # app.py is attributed line by line
    assert (complexity["functions"], complexity["max"], complexity["mean"]) == (2, 12, 8.0)
    assert complexity["rank_distribution"]["A"] == 1
    assert complexity["rank_distribution"]["C"] == 1
    duplication = m["metrics"]["duplication"]
    # No clone findings to restrict app.py's duplicated lines with: jscpd's per-file counts.
    assert (duplication["scope"], duplication["attribution"], duplication["percentage"]) == (
        "measured_files",
        "file_level",
        25.0,
    )

    assert m["coverage"]["eslint"] == {
        "status": "succeeded",
        "files_in_scope": 2,
        "files_scanned": 1,
        "unparseable": 1,
    }
    assert m["parse_coverage"]["unparseable_files"] == ["frontend/src/main.jsx"]
    assert m["parse_coverage"]["unparseable_by_tool"] == {"eslint": ["frontend/src/main.jsx"]}
    assert m["parse_coverage"]["share_unparseable_sloc"] == 0.0
    assert m["parse_coverage"]["exceeds_limit"] is False
    assert m["suppression_markers"] == 1
    assert m["suppression_markers_per_kloc"] == round(1 / kloc, 4)
    assert m["functional"]["pass_rate"] == 0.9333
    assert m["flags"] == {
        "truncated": False,
        "truncation_reasons": [],
        "run_stopped": False,
        "run_stop_reasons": [],
        "empty_output": False,
        "validity": {"valid": True, "exclude": False},
        "excluded_reasons": [],
        "unparseable_code": False,
        "attribution": "line_level",
        "attribution_mismatch": False,
        "mapping_drift": None,  # the test plan records no mapping hashes
    }
    assert m["integrity"]["attribution"] == {"files": [], "static": [], "ai": []}
    assert m["outcome_config"]["max_unparseable_share"] == 0.1

    out = measurement.outcomes(m)
    assert set(out) == set(measurement.OUTCOMES)
    assert out["static_issues_per_kloc"] == round(3 / kloc, 4)
    assert out["security_issues_per_kloc"] == round(2 / kloc, 4)
    assert out["high_plus_issues_per_kloc"] == round(2 / kloc, 4)
    assert out["has_high_security"] is True
    assert out["ai_consensus_per_kloc"] == round(1 / kloc, 4)
    assert (out["complexity_max"], out["complexity_mean"], out["duplication_pct"]) == (12.0, 8.0, 25.0)
    assert out["functional_pass_rate"] == 0.9333
    assert out["measured_sloc"] == 24
    assert out["static_issues"] == 3
    assert out["weighted_per_kloc"] == round(11 / kloc, 4)
    assert out["ai_grounding_rate"] == 0.75
    assert out["ai_mean_pairwise_jaccard"] == 0.5


@pytest.mark.django_db
def test_required_security_tool_failed_is_na_never_zero(sample):
    run, results = build_run(sample, statuses={"bandit": ToolResult.Status.FAILED})
    m = measurement.compute(run)
    static = m["static"]
    assert m["complete"] is False
    assert m["required_failed"] == ["bandit"]
    assert static["available"] is False
    assert static["required_failed"] == ["bandit"]
    assert static["category_available"]["security"] is False
    assert static["category_available"]["secrets"] is False
    assert static["category_available"]["type"] is True
    for key in ("findings_per_kloc", "issues_per_kloc", "security_issues", "high_plus_issues", "weighted"):
        assert static[key] is None
    assert static["has_high_security"] is None  # nothing found, and the finder failed
    out = measurement.outcomes(m)
    for key in ("static_findings_per_kloc", "security_issues_per_kloc", "has_high_security", "static_issues"):
        assert out[key] is None
    assert out["measured_sloc"] == 24  # the sample is still known

    # A high security issue found by another tool is a fact: the binary outcome is True.
    fx.add_finding(results["semgrep"], rule_id="x", severity="critical", category="security", issue_key="k")
    assert measurement.compute(run)["static"]["has_high_security"] is True


@pytest.mark.django_db
def test_per_category_availability(sample):
    run, results = build_run(sample, statuses={"mypy": ToolResult.Status.TIMED_OUT})
    populate(results)
    static = measurement.compute(run)["static"]
    assert static["available"] is False  # mypy is required
    assert static["unavailable_reasons"] == ["required_tool_failed"]
    assert static["category_available"]["type"] is False
    assert static["category_available"]["security"] is True  # mypy emits no security findings
    assert static["security_issues"] == 2
    assert static["security_issues_per_kloc"] is not None
    assert static["high_plus_issues"] is None


@pytest.mark.django_db
def test_optional_tool_failure_is_na_never_zero(sample):
    """An optional tool that could have counted findings on the measured code makes the counts
    NA: the same plan must never compare a count made with the tool against one made without it."""
    run, results = build_run(sample, statuses={"eslint": ToolResult.Status.FAILED})
    populate(results)
    m = measurement.compute(run)
    static = m["static"]
    assert m["complete"] is True  # `required` only decides the run status
    assert static["available"] is False
    assert (static["unavailable_reasons"], static["incomplete_tools"]) == (["optional_tool_failed"], ["eslint"])
    assert static["category_available"]["security"] is False  # eslint emits security findings
    for key in ("findings_per_kloc", "issues_per_kloc", "security_issues", "security_issues_per_kloc"):
        assert static[key] is None
    assert static["has_high_security"] is True  # a high security issue was found all the same
    out = measurement.outcomes(m)
    assert out["static_issues_per_kloc"] is None
    assert out["security_issues_per_kloc"] is None

    # The same failure where nothing it covers is measured (Python-only measured scope) changes no count.
    plan = {
        **PLAN,
        "tools": [
            {**t, "languages": ["javascript", "jsx", "typescript", "tsx", "vue"]} if t["slug"] == "eslint" else t
            for t in PLAN["tools"]
        ],
    }
    py_only = fx.build_sample([("app.py", FINAL_APP, "added")])
    run, results = build_run(py_only, plan=plan, statuses={"eslint": ToolResult.Status.FAILED})
    static = measurement.compute(run)["static"]
    assert (static["available"], static["incomplete_tools"]) == (True, [])
    assert static["category_available"]["security"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(("count_style", "available"), [(False, True), (True, False)])
def test_optional_style_tool_failure_matters_only_when_style_is_counted(sample, count_style, available):
    plan = {
        **PLAN,
        "tools": [*PLAN["tools"], {"slug": "codespell", "required": False, "languages": ["*"]}],
        "outcomes": {"min_sloc_for_density": 10, "count_style": count_style},
    }
    run, _ = build_run(sample, plan=plan, statuses={"codespell": ToolResult.Status.TIMED_OUT})
    static = measurement.compute(run)["static"]
    assert static["available"] is available
    assert static["category_available"]["style"] is False
    assert static["incomplete_tools"] == ([] if available else ["codespell"])


@pytest.mark.django_db
def test_not_applicable_is_ok(sample):
    run, _results = build_run(sample, statuses={"mypy": ToolResult.Status.NOT_APPLICABLE})
    m = measurement.compute(run)
    assert m["complete"] is True
    assert m["static"]["available"] is True


@pytest.mark.django_db
def test_density_none_below_minimum_and_without_manifest(sample):
    plan = {**PLAN, "outcomes": {"min_sloc_for_density": 50}}
    run, results = build_run(sample, plan=plan)
    populate(results)
    m = measurement.compute(run)
    assert m["static"]["issues"]["total"] == 3  # counts are still reported
    assert m["static"]["issues_per_kloc"] is None
    assert m["static"]["findings_per_kloc"] is None
    assert m["ai"]["per_kloc"] is None
    assert m["suppression_markers_per_kloc"] is None
    assert measurement.outcomes(m)["static_issues"] == 3

    bare = fx.make_run(plan=PLAN)
    m = measurement.compute(bare)
    assert m["sloc"]["measured"] is None
    assert m["files"] == {"measured": None, "included": None, "excluded": None}
    assert m["complete"] is False  # no tool results at all
    out = measurement.outcomes(m)
    assert out["measured_sloc"] is None
    assert out["static_findings_per_kloc"] is None


@pytest.mark.django_db
def test_ai_failed_and_not_planned(sample):
    run, results = build_run(sample, ai_status=ToolResult.Status.FAILED)
    populate(results)
    m = measurement.compute(run)
    assert m["ai"]["available"] is False
    assert m["ai"]["per_kloc"] is None
    assert m["complete"] is False
    assert m["required_failed"] == ["ai_review"]
    assert measurement.outcomes(m)["ai_consensus_per_kloc"] is None
    assert m["static"]["available"] is True  # AI failure never touches static outcomes

    static_only = {**PLAN, "reviewer": None}
    run, _ = build_run(sample, plan=static_only)
    m = measurement.compute(run)
    assert m["ai"] is None
    assert measurement.outcomes(m)["ai_consensus_per_kloc"] is None


@pytest.mark.django_db
def test_ai_counts_only_what_consensus_counted(sample):
    """The AI numerator is the consensus' ``counted`` marks (grounded, in scope, counted category);
    a consensus cluster left uncounted (e.g. style without count_style) is reported, never counted."""
    run, results = build_run(sample)
    populate(results)
    ai = results["ai_review"]
    for index in range(2):
        # A grounded, in-scope style consensus: consensus left it uncounted under the plan's rules.
        fx.add_finding(
            ai,
            rule_id="style",
            severity="low",
            category="style",
            line_number=7 + index,
            is_ai=True,
            is_consensus=True,
            counted=False,
            grounding="exact",
            issue_key="a-style",
            file_origin="modified",
        )
    m = measurement.compute(run)
    kloc = m["sloc"]["measured"] / 1000
    assert m["ai"]["consensus"]["total"] == 1
    assert m["ai"]["consensus"]["by_category"] == {"security": 1}
    assert m["ai"]["per_kloc"] == round(1 / kloc, 4)
    assert m["ai"]["uncounted_consensus"] == 1
    assert m["ai"]["findings_total"] == 5
    summary = measurement.summarize(run)
    assert summary["ai_findings"] == 1


@pytest.mark.django_db
def test_complexity_sources(sample):
    plan = {**PLAN, "tools": [*PLAN["tools"], {"slug": "lizard", "required": False}]}
    run, results = build_run(sample, plan=plan)
    results["lizard"].metrics = {"functions": [{"file": "app.py", "ccn": 7}]}
    results["lizard"].save()
    results["radon"].metrics = {"max_complexity": 30, "average_complexity": 3.5}
    results["radon"].save()
    complexity = measurement.compute(run)["metrics"]["complexity"]
    assert (complexity["source"], complexity["max"]) == ("lizard", 7)

    # The planned instrument failed: NA, never radon (Python only) standing in for lizard.
    results["lizard"].status = ToolResult.Status.FAILED
    results["lizard"].save()
    assert measurement.compute(run)["metrics"]["complexity"] is None
    assert measurement.outcomes(measurement.compute(run))["complexity_max"] is None

    # Without lizard in the plan, radon is the plan's complexity instrument.
    run, results = build_run(sample)
    results["radon"].metrics = {"max_complexity": 30, "average_complexity": 3.5}
    results["radon"].save()
    complexity = measurement.compute(run)["metrics"]["complexity"]
    assert (complexity["source"], complexity["scope"], complexity["max"], complexity["mean"]) == (
        "radon",
        "all_scanned",
        30.0,
        3.5,
    )
    results["radon"].status = ToolResult.Status.FAILED
    results["radon"].save()
    assert measurement.compute(run)["metrics"]["complexity"] is None


@pytest.mark.django_db
def test_flags_and_study_exclusion_rules(sample):
    meta = {**META, "truncated": True, "truncation_reasons": ["truncated_calls"], "empty_output": True}
    run, _ = build_run(sample, meta=meta)
    flags = measurement.compute(run)["flags"]
    assert flags["truncated"] is True
    assert flags["empty_output"] is True
    assert sorted(flags["excluded_reasons"]) == ["empty_output", "truncated"]

    study = AnalysisStudyFactory(exclusion_rules={"exclude_truncated": False})
    run, _ = build_run(sample, meta=meta, study=study)
    assert measurement.compute(run)["flags"]["excluded_reasons"] == ["empty_output"]


@pytest.mark.django_db
def test_run_limit_stops_are_not_truncation(sample):
    """A run stopped at its request budget / wall clock is a valid sample (the generator keeps it):
    reported as run_stopped, excluded only when a study opts in. Rows written before the split
    (run-stop codes in truncation_reasons) get the same verdict."""
    new = {**META, "run_stopped": True, "run_stop_reasons": ["termination:timeout"]}
    legacy = {
        **META,
        "truncated": True,
        "truncation_reasons": ["termination:budget_exhausted", "budget_exhausted:max_llm_requests"],
    }
    for meta in (new, legacy):
        run, _ = build_run(sample, meta=meta)
        m = measurement.compute(run)
        flags = m["flags"]
        assert (flags["truncated"], flags["truncation_reasons"]) == (False, [])
        assert flags["run_stopped"] is True
        assert flags["run_stop_reasons"]
        assert flags["excluded_reasons"] == []
        assert not any("truncated" in w for w in measurement.summarize(run)["warnings"])
        study = AnalysisStudyFactory(exclusion_rules={"exclude_run_limits": True})
        run, _ = build_run(sample, meta=meta, study=study)
        assert measurement.compute(run)["flags"]["excluded_reasons"] == ["run_stopped"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "functional",
    [
        {"status": "not_applicable", "pass_rate": 0.0},
        {"status": "infra_error", "pass_rate": None},
        {"status": "passed", "pass_rate": 1.7},
        {},
    ],
)
def test_functional_is_none_safe(sample, functional):
    run, _ = build_run(sample, meta={**META, "functional": functional})
    m = measurement.compute(run)
    assert m["functional"]["pass_rate"] is None
    assert measurement.outcomes(m)["functional_pass_rate"] is None


@pytest.mark.django_db
def test_summarize(sample):
    run, results = build_run(sample, statuses={"eslint": ToolResult.Status.FAILED})
    results["eslint"].failure_reason = "parse_error"
    results["eslint"].error_message = "invalid JSON"
    results["eslint"].save()
    populate(results)
    summary = measurement.summarize(run)
    assert summary["schema_version"] == 2
    assert summary["tools"] == {
        "total": 9,
        "succeeded": 8,
        "failed": 1,
        "timed_out": 0,
        "not_applicable": 0,
        "skipped": 0,
        "cancelled": 0,
        "pending": 0,
        "running": 0,
    }
    assert summary["complete"] is True  # eslint is optional
    assert summary["total_findings"] == 4
    assert summary["severity_counts"] == {"critical": 0, "high": 3, "medium": 0, "low": 1, "info": 0}
    assert summary["ai_findings"] == 1
    assert summary["ai_severity_counts"]["high"] == 1
    assert summary["issues"] == 3
    assert summary["sloc"] == {"measured": 24, "total": 27}
    assert summary["cost_usd"] == pytest.approx(0.0045)
    assert summary["cost_complete"] is True
    assert summary["static_measured"] is True
    assert summary["warnings"][0] == "eslint failed (parse_error): invalid JSON"
    assert (summary["tools_run"], summary["tools_completed"], summary["tools_failed"]) == (9, 8, 1)


def test_outcomes_of_empty_measurement():
    out = measurement.outcomes({})
    assert set(out) == set(measurement.OUTCOMES)
    assert all(value is None for value in out.values())
    for spec in measurement.OUTCOMES.values():
        assert {"label", "kind", "unit", "higher_is_better", "description", "requires"} <= set(spec)
        assert spec["kind"] in ("rate", "count", "binary", "metric")


@pytest.mark.django_db
def test_duplication_totals_and_coverage_list_forms(sample):
    run, results = build_run(sample)
    results["jscpd"].metrics = {
        "duplication_percentage": 3.25,
        "clones": 4,
        "duplicated_lines": 13,
        "total_lines": 400,
    }
    results["jscpd"].save()
    results["semgrep"].coverage = {"selected": ["app.py", "frontend/src/App.jsx"], "scanned": ["app.py"]}
    results["semgrep"].save()
    m = measurement.compute(run)
    assert m["metrics"]["duplication"] == {
        "source": "jscpd",
        "scope": "all_scanned",
        "percentage": 3.25,
        "clones": 4,
        "duplicated_lines": 13,
        "total_lines": 400,
    }
    assert m["coverage"]["semgrep"] == {
        "status": "succeeded",
        "files_in_scope": 2,
        "files_scanned": 1,
        "unparseable": 0,
    }
    assert m["coverage"]["bandit"]["files_in_scope"] is None

    results["jscpd"].status = ToolResult.Status.FAILED
    results["jscpd"].save()
    m = measurement.compute(run)
    assert m["metrics"]["duplication"] is None
    assert measurement.outcomes(m)["duplication_pct"] is None


# --------------------------------------------------------------------------
# Integrity rules (regressions of the analysis-v2 validity review)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_mostly_unparseable_code_is_na_never_clean(sample):
    """A file a tool could not parse yields none of its findings while its lines stay in the
    denominator: above the share limit the static outcomes are NA, not a low density."""
    run, results = build_run(sample)
    results["ruff"].coverage = {"unparseable": [{"path": "app.py", "reason": "SyntaxError"}]}
    results["ruff"].save()
    m = measurement.compute(run)
    parse = m["parse_coverage"]
    assert parse["share_unparseable_sloc"] == round(21 / 24, 4)
    assert (parse["max_unparseable_share"], parse["exceeds_limit"]) == (0.1, True)
    static = m["static"]
    assert static["available"] is False
    assert static["unavailable_reasons"] == ["unparseable_code"]
    for key in ("findings_per_kloc", "issues_per_kloc", "security_issues_per_kloc", "high_plus_issues_per_kloc"):
        assert static[key] is None
    assert static["has_high_security"] is None  # nothing found, most of the code never parsed
    assert m["flags"]["unparseable_code"] is True
    assert m["complete"] is True  # every tool ran: the code is what is broken
    out = measurement.outcomes(m)
    assert out["static_issues_per_kloc"] is None
    assert out["has_high_security"] is None
    assert out["measured_sloc"] == 24
    assert any(w.startswith("unparseable code") for w in measurement.summarize(run)["warnings"])

    # A small unparseable share (one 3-line file of 24 is above 10%, one of ~120 is not) keeps the outcomes.
    big = fx.build_sample(
        [
            ("app.py", "".join(f"x{i} = {i}\n" for i in range(120)), "added"),
            ("broken.py", "def broken(:\n", "added"),
        ],
    )
    run, results = build_run(big)
    results["ruff"].coverage = {"unparseable": [{"path": "broken.py", "reason": "SyntaxError"}]}
    results["ruff"].save()
    m = measurement.compute(run)
    assert m["parse_coverage"]["exceeds_limit"] is False
    assert m["static"]["available"] is True
    assert m["flags"]["unparseable_code"] is False


@pytest.mark.django_db
def test_ai_density_needs_the_reviewer_to_see_the_measured_code(sample):
    run, results = build_run(sample, ai_status=ToolResult.Status.NOT_APPLICABLE)
    ai = measurement.compute(run)["ai"]
    assert ai["available"] is True  # the reviewer did what it was asked
    assert ai["per_kloc"] is None  # but never looked at the measured code: not "0 issues"
    assert ai["per_kloc_unavailable_reasons"] == ["reviewer_scope"]

    # A reviewer scope narrower than the measured scope: app.py (modified) is measured, never shown.
    plan = {**PLAN, "reviewer": {**PLAN["reviewer"], "include_origins": ["authored", "added"]}}
    run, results = build_run(sample, plan=plan)
    populate(results)
    m = measurement.compute(run)
    assert m["ai"]["uncovered_measured_sloc"] == 21
    assert m["ai"]["per_kloc"] is None
    assert measurement.outcomes(m)["ai_consensus_per_kloc"] is None
    assert measurement.outcomes(m)["ai_grounding_rate"] == 0.75  # the reviewer's own behaviour is still measured


@pytest.mark.django_db
def test_findings_attributed_unlike_the_stored_manifest_are_na(sample):
    """SLOC comes from the manifest stored at submit; findings from a re-resolution at execution.
    If that re-resolution lost the scaffold baseline, a scaffold line is counted as the model's."""
    run, results = build_run(sample)
    populate(results)
    # app.py line 1 is a scaffold line in the stored manifest (outside the denominator).
    fx.add_finding(
        results["bandit"],
        rule_id="B104",
        severity="medium",
        category="security",
        line_number=1,
        file_origin="modified",
        in_measured_scope=True,
        counted=True,
        issue_key="k-bind",
    )
    m = measurement.compute(run)
    assert m["integrity"]["attribution"] == {"files": ["app.py"], "static": ["app.py"], "ai": []}
    assert m["flags"]["attribution_mismatch"] is True
    assert m["static"]["available"] is False
    assert m["static"]["unavailable_reasons"] == ["attribution_mismatch"]
    assert m["static"]["security_issues"] is None
    assert m["ai"]["per_kloc"] is not None  # AI findings agree with the manifest
    assert any("line attribution" in w for w in measurement.summarize(run)["warnings"])


@pytest.mark.django_db
def test_findings_mapped_with_other_tables_than_the_plan_are_na(sample):
    from backend.analysis.engine import normalize

    plan = {**PLAN, "mapping_hashes": normalize.mapping_hashes()}
    run, results = build_run(sample, plan=plan)
    populate(results)
    fx.add_finding(
        results["ruff"],
        rule_id="F841",
        severity="low",
        category="dead_code",
        line_number=7,
        file_origin="modified",
        issue_key="k-var",
        tool_specific_data={"mapping_digest": normalize.mapping_digest()},
    )
    m = measurement.compute(run)
    assert m["flags"]["mapping_drift"] is False
    assert m["static"]["available"] is True

    fx.add_finding(
        results["ruff"],
        rule_id="F841",
        severity="low",
        category="dead_code",
        line_number=8,
        file_origin="modified",
        issue_key="k-var2",
        tool_specific_data={"mapping_digest": "0" * 16},
    )
    m = measurement.compute(run)
    assert m["flags"]["mapping_drift"] is True
    assert m["integrity"]["mapping_drift"] == {"static": True, "ai": False}
    assert m["static"]["unavailable_reasons"] == ["mapping_drift"]
    assert measurement.outcomes(m)["static_findings_per_kloc"] is None


@pytest.mark.django_db
def test_metrics_exclude_scaffold_lines_of_modified_files():
    base = (
        "def health():\n"  # 1
        "    return 'ok'\n"  # 2
        "\n"  # 3
        "\n"  # 4
        "def spa(path):\n"  # 5
        "    if path:\n"  # 6
        "        return path\n"  # 7
        "    return 'index'\n"  # 8
    )
    final = (
        base.replace("        return path\n", "        return path.strip('/')\n")
        + "\n\ndef model_fn(x):\n    if x:\n        return 1\n    return 2\n"  # 9-14
    )
    sample = fx.build_sample([("app.py", final, "modified")], baseline={"app.py": base.encode()})
    app = sample.by_path()["app.py"]
    assert app.changed_lines == [7, 9, 10, 11, 12, 13, 14]
    plan = {**PLAN, "tools": [*PLAN["tools"], {"slug": "lizard", "required": True}], "reviewer": None}
    run, results = build_run(sample, plan=plan)
    results["lizard"].metrics = {
        "functions": [
            {"path": "app.py", "name": "health", "line": 1, "end_line": 2, "ccn": 1},  # untouched scaffold
            {"path": "app.py", "name": "spa", "line": 5, "end_line": 8, "ccn": 2},  # edited by the model
            {"path": "app.py", "name": "model_fn", "line": 11, "end_line": 14, "ccn": 3},
        ],
    }
    results["lizard"].save()
    results["jscpd"].metrics = {"clones": 1, "files": {"app.py": {"lines": 14, "duplicated_lines": 4}}}
    results["jscpd"].save()
    fx.add_finding(
        results["jscpd"],
        rule_id="duplicate-code",
        severity="info",
        category="duplication",
        line_number=1,
        end_line=2,
        counted=False,
        file_origin="scaffold",
        in_measured_scope=False,
        issue_key="u-clone",
        tool_specific_data={"second_file": "app.py", "second_start": 11, "second_end": 12},
    )
    metrics = measurement.compute(run)["metrics"]
    complexity = metrics["complexity"]
    assert (complexity["source"], complexity["attribution"]) == ("lizard", "line_level")
    assert (complexity["functions"], complexity["max"], complexity["mean"]) == (2, 3, 2.5)
    duplication = metrics["duplication"]
    assert duplication["attribution"] == "line_level"
    # Measured lines: 7, 9-14 (7 lines); duplicated among them: 11, 12.
    assert (duplication["total_lines"], duplication["duplicated_lines"]) == (7, 2)
    assert duplication["percentage"] == round(100 * 2 / 7, 4)


# --------------------------------------------------------------------------
# AI spend / replicates and provenance (integration-round regressions)
# --------------------------------------------------------------------------


def _ai_samples(result, usages, statuses=None):
    for index, usage in enumerate(usages):
        fx.add_review_sample(
            result,
            index,
            status=(statuses or {}).get(index, "succeeded"),
            usage=usage,
            grounding={"exact": 1, "relocated": 0, "ungrounded": 0, "invalid_file": 0, "out_of_range": 0},
        )


@pytest.mark.django_db
def test_unknown_ai_spend_is_none_never_zero(sample):
    """Rule 2: a replicate that reported no cost or tokens is missing data, not $0 / 0 tokens."""
    run, results = build_run(sample)
    _ai_samples(results["ai_review"], [{}, {"cost_usd": None}, {}])
    ai = measurement.compute(run)["ai"]
    assert ai["cost_usd"] is None
    assert (ai["cost_complete"], ai["cost_incomplete_samples"]) == (False, 3)
    assert ai["tokens"] == {"prompt": None, "completion": None, "reasoning": None, "cached": None, "total": None}
    assert ai["tokens_complete"] is False
    summary = measurement.summarize(run)
    assert (summary["cost_usd"], summary["cost_complete"]) == (None, False)


@pytest.mark.django_db
def test_partly_known_ai_spend_is_a_flagged_lower_bound(sample):
    run, results = build_run(sample)
    known = {"prompt_tokens": 100, "completion_tokens": 20, "cost_usd": 0.002}
    _ai_samples(results["ai_review"], [known, {"cost_usd": None}, {**known, "cost_complete": False}])
    ai = measurement.compute(run)["ai"]
    assert ai["cost_usd"] == pytest.approx(0.004)
    assert (ai["cost_complete"], ai["cost_incomplete_samples"]) == (False, 2)
    assert (ai["tokens"]["prompt"], ai["tokens"]["total"], ai["tokens"]["reasoning"]) == (200, 240, None)
    assert ai["tokens_complete"] is False


@pytest.mark.django_db
def test_ai_cost_complete_is_copied_from_the_reviewer_usage_and_lost_spend(sample):
    run, results = build_run(sample)
    ai_result = results["ai_review"]
    _ai_samples(ai_result, [{"prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.001}] * 3)
    ai_result.metrics = {**ai_result.metrics, "usage": {"cost_usd": 0.003, "cost_complete": False}}
    ai_result.save()
    assert measurement.compute(run)["ai"]["cost_complete"] is False

    # An interrupted review whose replicates were never stored: spend unknown, bounded by the budget.
    ai_result.metrics = {"agreement": {"threshold": 2}}
    ai_result.summary = {"cost_usd": None, "cost_unknown": True, "max_cost_usd": 0.75}
    ai_result.save()
    ai_result.review_samples.all().delete()
    ai = measurement.compute(run)["ai"]
    assert (ai["cost_usd"], ai["cost_complete"], ai["cost_upper_bound_usd"]) == (None, False, 0.75)
    assert measurement.summarize(run)["cost_usd"] is None


@pytest.mark.django_db
def test_replaced_attempts_spend_is_reported(sample):
    run, results = build_run(sample)
    ai_result = results["ai_review"]
    ai_result.summary = {"prior_attempts": 2, "prior_attempts_cost_usd": 0.01, "prior_attempts_cost_unknown": True}
    ai_result.save()
    ai = measurement.compute(run)["ai"]
    assert ai["replaced"] == {"attempts": 2, "cost_usd": 0.01, "cost_unknown": True}


@pytest.mark.django_db
def test_ai_replicates_are_copied_from_the_agreement(sample):
    run, results = build_run(sample)
    ai_result = results["ai_review"]
    ai_result.metrics = {
        "agreement": {
            "mean_pairwise_jaccard": 0.4,
            "threshold": 2,
            "planned_samples": 3,
            "successful_samples": 2,
            "replicates_complete": False,
            "threshold_rule": "majority of the k planned samples (k // 2 + 1); a failed sample votes for nothing",
        },
    }
    ai_result.save()
    _ai_samples(ai_result, [{"cost_usd": 0.001}] * 3, statuses={2: "failed"})
    ai = measurement.compute(run)["ai"]
    assert (ai["planned_samples"], ai["samples_ok"], ai["threshold"]) == (3, 2, 2)
    assert ai["replicates_complete"] is False
    assert ai["agreement"]["replicates_complete"] is False
    assert ai["threshold_rule"].startswith("majority of the k planned samples")


@pytest.mark.parametrize(
    ("planned", "ok", "threshold", "complete"),
    [(3, 1, 2, False), (3, 3, 2, True), (4, 4, 3, True), (5, 3, 3, False)],
)
@pytest.mark.django_db
def test_ai_threshold_fallback_is_a_majority_of_the_planned_samples(sample, planned, ok, threshold, complete):
    """Rows without agreement.threshold use the reviewer's quorum (k // 2 + 1 of the k planned),
    never ceil(m / 2) of the m that succeeded (1 of 1 would make a lone replicate a consensus)."""
    plan = {**PLAN, "reviewer": {**PLAN["reviewer"], "samples": planned}}
    run, results = build_run(sample, plan=plan)
    ai_result = results["ai_review"]
    ai_result.metrics = {"agreement": {"mean_pairwise_jaccard": None}}
    ai_result.save()
    statuses = {i: "failed" for i in range(ok, planned)}
    _ai_samples(ai_result, [{"cost_usd": 0.001}] * planned, statuses=statuses)
    ai = measurement.compute(run)["ai"]
    assert (ai["threshold"], ai["planned_samples"], ai["replicates_complete"]) == (threshold, planned, complete)


@pytest.mark.django_db
def test_measurement_records_the_engine_versions(sample):
    from backend.analysis.engine import types

    run, _ = build_run(sample)
    m = measurement.compute(run)
    assert m["engine_versions"] == {
        "sample_resolver": types.SAMPLE_RESOLVER_VERSION,
        "sloc": types.SLOC_VERSION,
        "normalizer": types.NORMALIZER_VERSION,
        "dedup": types.DEDUP_VERSION,
        "measurement": types.MEASUREMENT_VERSION,
    }
    assert m["engine_versions"]["measurement"] == m["version"]
    assert types.SLOC_VERSION in measurement.OUTCOMES["measured_sloc"]["unit"]


@pytest.mark.parametrize("status", [ToolResult.Status.FAILED, ToolResult.Status.NOT_APPLICABLE])
@pytest.mark.django_db
def test_summary_static_counts_are_none_when_no_static_tool_succeeded(sample, status):
    """A run whose static tools all failed (or had nothing to scan) is not a clean scan."""
    statuses = {t["slug"]: status for t in PLAN["tools"]}
    run, results = build_run(sample, statuses=statuses)
    populate(results)
    summary = measurement.summarize(run)
    assert (summary["total_findings"], summary["severity_counts"], summary["issues"]) == (None, None, None)
    assert summary["static_measured"] is False
    assert "no static tool succeeded: static finding counts are NA" in summary["warnings"]
    assert summary["ai_findings"] == 1  # the AI review still measured


@pytest.mark.django_db
def test_summary_of_an_ai_only_plan_has_no_static_counts(sample):
    plan = {**PLAN, "tools": []}
    run, results = build_run(sample, plan=plan)
    _ai_samples(results["ai_review"], [{"cost_usd": 0.001}] * 3)
    summary = measurement.summarize(run)
    assert (summary["total_findings"], summary["severity_counts"], summary["issues"]) == (None, None, None)
    assert summary["cost_usd"] == pytest.approx(0.003)


@pytest.mark.django_db
def test_summary_cost_of_a_static_only_plan_is_a_known_zero(sample):
    plan = {**PLAN, "reviewer": None}
    run, _ = build_run(sample, plan=plan)
    summary = measurement.summarize(run)
    assert (summary["cost_usd"], summary["cost_complete"]) == (0.0, True)
    assert summary["total_findings"] == 0  # every static tool succeeded: zero is measured
