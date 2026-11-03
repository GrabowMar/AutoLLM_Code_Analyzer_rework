"""Render a report as Markdown or LaTeX (booktabs) tables.

The study and study-conditions reports have curated tables, one per section, in
the order a paper reports them. Every other report type falls back to a
generic rendering that keeps every value: top-level scalars as a key/value
table, nested objects as their own tables (a mapping of records, such as
``metrics_by_tool``, as one table with a row per record), and every list of
objects as a table whose nested objects become dotted columns, split into
several tables when there are more columns than fit, with nested lists of
objects as child tables. A task comparison also gets a curated table of
its models.

Severity buckets in any report are the platform's normalized severity (a
heuristic), never the tools' own labels, and every table says so; fields
that hold the levels the tools printed (``reported_levels``,
``tool_severity``) are shown as printed and never labelled normalized, even
when a tool prints the same lowercase words.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from backend.reports.models import Report

_MAX_GENERIC_COLUMNS = 12
_MAX_ROWS = 500
_SEVERITY_KEYS = frozenset(("critical", "high", "medium", "low", "info"))
_NORMALIZED_NOTE = "Severity buckets are the platform's normalized severity (a heuristic), not the tools' own labels."
_METRIC_HEADERS = {
    "deploy_rate": "Deploy rate",
    "pass_rate": "Pass rate",
    "functional_passed": "Fully correct",
    "unique_issues_per_app": "Unique issues / app",
    "issues_per_kloc": "Code issues / kLOC",
    "security_per_app": "Security issues / app",
    "cost_per_app": "Cost / app (USD)",
}


@dataclass
class Table:
    title: str
    columns: list[tuple[str, str]]  # (key, header)
    rows: list[dict[str, Any]]
    note: str = ""


def _fmt(value: Any) -> str:  # noqa: PLR0911 - one branch per value kind
    if value is None or value == {}:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != 0 and abs(value) < 0.001:
            return f"{value:.2e}"
        return f"{value:.3f}".rstrip("0").rstrip(".") if abs(value) < 1000 else f"{value:,.0f}"
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and all(isinstance(v, (int, float)) or v is None for v in value):
            return f"[{_fmt(value[0])}, {_fmt(value[1])}]"
        return ", ".join(_fmt(v) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items())
    return str(value)


def _rate(cell: dict[str, Any] | None) -> str:
    """``rate% (k/n) [ci]``; with repeats, k/n are runs and the rate and interval are over apps."""
    if not cell or cell.get("rate") is None:
        return "-"
    ci = cell.get("ci95")
    apps = cell.get("apps")
    count = f"{cell['k']}/{cell['n']}"
    if apps is not None and apps != cell["n"]:
        count = f"{count} runs, {apps} apps"
    text = f"{cell['rate'] * 100:.1f}% ({count})"
    return f"{text} [{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]" if ci else text


def _measured_stat(cell: Any, stat: str) -> str | None:
    """``value (n=k)`` of a ``describe()`` cell over the apps a tool measured; None ('-') when it measured none."""
    if not isinstance(cell, dict) or not cell.get("n") or cell.get(stat) is None:
        return None
    return f"{_fmt(cell[stat])} (n={cell['n']})"


def _flatten(row: dict[str, Any], path: str) -> Any:
    """``row[path]`` when the row has that exact key (generic tables flatten to dotted keys), else a dotted walk."""
    if path in row:
        return row[path]
    value: Any = row
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _join(items) -> str:
    return ", ".join(str(i) for i in items if i not in (None, ""))


_VERSION_PREFIX_RE = re.compile(r"^\D*?\bversion\b[:,]?\s*", re.IGNORECASE)


def _build(slug: str, version: str) -> str:
    """A recorded build without its banner, e.g. ``osv-scanner version: 2.6.0`` or ``ruff 0.6.9`` -> the number."""
    text = _VERSION_PREFIX_RE.sub("", version.strip(), count=1) or version.strip()
    if text.lower().startswith(f"{slug.lower()} "):
        text = text[len(slug) + 1 :]
    return text


def _tool_builds(tool: dict[str, Any]) -> str:
    """The builds that produced the counted results (with run counts when several); else the catalog pin, said so."""
    slug = tool.get("slug", "")
    builds = tool.get("installed_versions") or {}
    if len(builds) == 1:
        return f"{slug} {_build(slug, next(iter(builds)))}"
    if builds:
        return f"{slug} " + " / ".join(
            f"{_build(slug, b)} ({n} run{'' if n == 1 else 's'})" for b, n in sorted(builds.items())
        )
    pin = tool.get("version") or ""
    return f"{slug} {pin} (catalog pin; build not recorded)" if pin else f"{slug} (build not recorded)"


def _left_out(coverage: dict[str, Any]) -> str:
    """Tools left out of every count: runs measured, and why the others were not (failed, app not running, not run)."""
    total = coverage.get("apps")
    parts = []
    for tool, info in sorted((coverage.get("tools_partial") or {}).items()):
        reasons = {k: v for k, v in info.items() if k not in ("apps_measured", "apps_missing")}
        why = [f"{v} {str(k).replace('_', ' ')}" for k, v in sorted(reasons.items())]
        not_run = (info.get("apps_missing") or 0) - sum(v for v in reasons.values() if isinstance(v, int))
        if not_run > 0:
            why.append(f"not run on {not_run}")
        parts.append(f"{tool} ({info.get('apps_measured')} of {total} runs" + (f": {', '.join(why)})" if why else ")"))
    return "; ".join(parts) or "none"


def _protocol_rows(data: dict[str, Any]) -> list[tuple[str, Any]]:
    protocol = data.get("protocol") or {}
    study = protocol.get("study") or {}
    plan = study.get("analysis_plan") or {}
    planned = data.get("planned_comparisons") or {}
    coverage = protocol.get("instrument_coverage")
    settings = protocol.get("settings") or {}
    kv: list[tuple[str, Any]] = []
    if study:
        kv += [
            ("Study", study.get("name")),
            ("Kind", study.get("kind")),
            ("Hypothesis", study.get("hypothesis") or "-"),
            ("Status", study.get("status")),
            ("Baseline condition", study.get("baseline_condition") or planned.get("baseline_condition")),
            ("Planned repeats", study.get("planned_repeats") or study.get("repeats")),
        ]
    kv += [
        ("Runs", protocol.get("runs")),
        ("Apps (unit x condition; repeats averaged)", protocol.get("apps")),
        ("Models", len(protocol.get("models") or [])),
    ]
    if protocol.get("grouped_by") == "condition":
        kv.append(("Grouped by", "condition: a model ran under several conditions, so arms are never pooled"))
    kv += [
        ("Tasks", len(protocol.get("tasks") or [])),
        ("Run kinds", protocol.get("kinds")),
        ("Stacks", protocol.get("stacks")),
        ("Starters", protocol.get("starters")),
        ("Harnesses", protocol.get("harnesses")),
        ("Prompt versions", protocol.get("prompt_versions")),
        ("Configuration fingerprints", protocol.get("config_fingerprints")),
        ("Cells by trial count (trials: cells)", protocol.get("trials_per_cell")),
        ("Truncated runs excluded (sensitivity analysis)", protocol.get("truncated_excluded")),
        ("Attrition (no usable run; excluded)", protocol.get("attrition_excluded") or "-"),
        (
            "Beyond-plan / pilot runs excluded",
            f"{protocol.get('beyond_plan_excluded') or 0} / {protocol.get('pilot_excluded') or 0}",
        ),
        # The builds the counted results were produced with, as recorded on them (not today's catalog pins).
        ("Tools run (recorded builds)", ", ".join(_tool_builds(t) for t in protocol.get("tools") or [])),
    ]
    if coverage is not None:
        kv += [
            ("Tools counted (measured every analyzed run)", _join(coverage.get("tools_common") or []) or "none"),
            ("Tools left out (did not measure every run)", _left_out(coverage)),
        ]
    ai_counted = ((data.get("issues") or {}).get("params") or {}).get("ai_counted")
    if not settings.get("include_ai"):
        ai = "excluded"
    elif ai_counted:
        ai = f"counted ({_join(ai_counted)})"
    else:
        ai = "requested, but none counted (no AI reviewer reviewed every analyzed run)"
    kv += [
        ("AI-reviewer findings", ai),
        ("Evaluation suites", protocol.get("evaluators")),
    ]
    if plan:
        questions = plan.get("research_questions") or []
        multiplicity = planned.get("multiplicity") or plan.get("multiplicity") or "all"
        kv += [
            (
                "Research questions",
                "; ".join(
                    f"{q.get('id')}: {q.get('question')} ({q.get('metric')}, {q.get('direction')})" for q in questions
                )
                or "-",
            ),
            ("Primary metric", plan.get("primary_metric")),
            ("Secondary metrics", _join(plan.get("secondary_metrics") or []) or "-"),
            ("Alpha / correction / multiplicity", f"{plan.get('alpha')} / {plan.get('correction')} / {multiplicity}"),
        ]
    kv += [
        ("Analysis plan hash", study.get("analysis_plan_hash")),
        ("Pre-registration hash", study.get("prereg_hash")),
        ("Locked at", study.get("locked_at")),
    ]
    confirm = protocol.get("confirmatory")
    if confirm is not None:
        kv.append(
            (
                "Confirmatory analysis",
                "yes" if confirm.get("design") else "no: " + "; ".join(confirm.get("reasons") or []),
            ),
        )
    kv += [
        ("Data fingerprint", (protocol.get("data_fingerprint") or "")[:16]),
        ("Resampling seed (bootstrap/permutation)", settings.get("seed")),
    ]
    return kv


def _issue_tables(data: dict[str, Any], group: str, group_word: str) -> list[Table]:
    """The deduplicated issue tables: code issues by group and task, advisories, tools, rules."""
    protocol = data.get("protocol") or {}
    issues = data.get("issues") or {}
    by_key = "by_group" if protocol.get("grouped_by") == "condition" else "by_model"
    by_group = issues.get(by_key) or []
    tables: list[Table] = []
    code_cols = [
        ("apps", "Apps"),
        ("runs", "Runs"),
        ("raw_findings", "Raw findings"),
        ("unique_issues", "Unique issues (all kinds)"),
        ("duplicate_findings", "Duplicates"),
        ("mean_unique_per_app", "Unique / app"),
        ("code_unique", "Code issues"),
        ("unique_per_kloc", "Code issues / kLOC"),
        ("security_unique", "Security (code)"),
        ("code_unique_tests", "In test code"),
        ("runtime_unique", "Runtime (DAST)"),
    ]
    note = (
        "Apps are unit x condition units with repeats averaged; counts are totals over runs. Code issues are in "
        "the app's own non-test code (deptry/depcheck manifest findings included); dependency advisories, "
        "runtime (DAST) issues and test code are counted apart. Code issues / kLOC is over non-test source "
        "lines. '-' means not measured (no tool of that kind counted), not zero."
    )
    tables.append(Table(f"Issues by {group_word} (deduplicated)", [("model", group), *code_cols], by_group, note))
    tables.append(
        Table(
            "Issues by task (deduplicated)",
            [
                ("task", "Task"),
                *code_cols,
                ("dependency_unique", "Dependency advisories"),
                ("dependency_added", "In versions the model chose"),
            ],
            issues.get("by_task") or [],
            note,
        ),
    )
    tables.append(
        Table(
            f"Dependency advisories by {group_word} (package vulnerabilities, not code issues)",
            [
                ("model", group),
                ("apps", "Apps"),
                ("dependency_unique", "Advisories"),
                ("dependency_added", "In versions the model chose"),
                ("dependency_inherited", "Inherited (starter pins or transitive)"),
            ],
            by_group,
            "Known-vulnerability scanner findings about package versions. Inherited advisories are in versions "
            "the starter pinned, or transitive packages it pulls in: they measure the starter, not the model. "
            "'-' means no vulnerability scanner measured the apps.",
        ),
    )
    if any(r.get("manifest_unique") is not None for r in by_group):
        tables.append(
            Table(
                f"Dependency manifest findings by {group_word} (code issues)",
                [
                    ("model", group),
                    ("apps", "Apps"),
                    ("manifest_unique", "Manifest issues (deptry/depcheck)"),
                    ("manifest_unused_starter", "Of which: unused packages the starter preinstalls"),
                ],
                by_group,
                "Undeclared, unused or transitive dependencies in the app's own manifest; they are code issues "
                "and in the counts above. 'Unused' findings on packages the starter preinstalls are the "
                "starter's, not the model's choice. '-' means no manifest checker measured the apps.",
            ),
        )
    tables.append(
        Table(
            "Issues by tool",
            [
                ("tool", "Tool"),
                ("raw_findings", "Raw"),
                ("unique_issues", "Unique"),
                ("exclusive", "Only this tool"),
                ("shared", "Shared"),
                ("apps_with_findings", "Apps with findings"),
                ("apps_measured", "Apps measured"),
                ("reported_levels", "Levels the tool printed"),
            ],
            issues.get("by_tool") or [],
            "Levels as each tool printed them, with counts ('unrated': the tool prints no level).",
        ),
    )
    tables.append(
        Table(
            "Tool overlap on comparable issue classes",
            [
                ("tool_a", "Tool A"),
                ("tool_b", "Tool B"),
                ("both", "Both"),
                ("only_a", "Only A"),
                ("only_b", "Only B"),
                ("agreement", "Jaccard"),
            ],
            issues.get("tool_pairs") or [],
        ),
    )
    rules = (issues.get("by_tool_rule") or [])[:40]
    normalized = (
        [("max_severity_normalized", "Normalized max (heuristic)")]
        if any("max_severity_normalized" in r for r in rules)
        else [("max_severity", "Max severity (normalized, heuristic)")]
        if any("max_severity" in r for r in rules)
        else []
    )
    tables.append(
        Table(
            "Issue types by tool (top 40)",
            [
                ("tool", "Tool"),
                ("rule_id", "Rule"),
                ("issue_class", "Class"),
                ("findings", "Findings"),
                ("apps", "Apps"),
                ("reported_levels", "Levels the tool printed"),
                *normalized,
            ],
            rules,
            "Levels as the tool printed them ('unrated': none). The normalized maximum is the platform's heuristic, "
            "not a level the tool reported.",
        ),
    )
    return tables


def _skip_note(family: dict[str, Any], planned: dict[str, Any]) -> str:
    reasons: dict[str, list[str]] = defaultdict(list)
    for c in family["comparisons"]:
        if c.get("skipped"):
            reasons[c["skipped"]].append(str(c["condition"]))
    texts = {
        "below_min_apps": f"Not tested (fewer than {planned.get('min_apps_per_condition')} apps)",
        "no_baseline_data": "Not tested (the baseline has no values for this metric)",
        "no_pairs": "Not tested (no app has a value in both this condition and the baseline)",
    }
    return " ".join(
        f"{texts.get(reason, f'Not tested ({reason})')}: {', '.join(names)}." for reason, names in reasons.items()
    )


def _no_p_note(family: dict[str, Any]) -> str:
    n = sum(1 for c in family["comparisons"] if not c.get("skipped") and c.get("in_family") is False)
    return (
        f"{n} comparison(s) here have no p-value (no non-zero difference) and are not in the correction." if n else ""
    )


def _study_tables(data: dict[str, Any]) -> list[Table]:
    tables: list[Table] = []
    protocol = data.get("protocol") or {}
    by_condition = protocol.get("grouped_by") == "condition"
    group, group_word = ("Condition", "condition") if by_condition else ("Model", "model")
    tables.append(
        Table("Protocol", [("k", "Item"), ("v", "Value")], [{"k": k, "v": v} for k, v in _protocol_rows(data)]),
    )

    tables.append(
        Table(
            "Definitions",
            [("k", "Term"), ("v", "Meaning")],
            [{"k": k, "v": v} for k, v in (data.get("definitions") or {}).items()],
        ),
    )

    funnel = data.get("funnel") or {}
    funnel_rows = [
        {
            "model": r.get("model", "all"),
            "apps": r.get("apps"),
            "runs": r["runs"],
            "generated": r["generated"],
            "evaluated": r["evaluated"],
            "deploy": _rate(r["deploy_rate"]),
            "functional": _rate(r["functional_rate"]),
            "given": _rate(r["functional_given_deployed"]),
            "pass": r["mean_pass_rate"],
        }
        for r in [*(funnel.get("by_model") or []), {**(funnel.get("overall") or {}), "model": "All"}]
        if r.get("runs") is not None
    ]
    tables.append(
        Table(
            f"Outcome funnel by {group_word}",
            [
                ("model", group),
                ("apps", "Apps"),
                ("runs", "Runs"),
                ("generated", "Generated"),
                ("evaluated", "Evaluated"),
                ("deploy", "Deployed [95% CI]"),
                ("functional", "Correct [95% CI]"),
                ("given", "Correct | deployed"),
                ("pass", "Mean pass rate"),
            ],
            funnel_rows,
            "Rates are over evaluated apps, each app's repeats averaged first (the app is the unit); counts are "
            "runs. Intervals are Wilson 95% over apps when every app's repeats agree, else a cluster bootstrap over "
            "apps (none below 3 apps).",
        ),
    )

    outcomes = data.get("model_outcomes") or []
    if outcomes:
        metrics = [k for k in _METRIC_HEADERS if any(k in o for o in outcomes)]
        status = outcomes[0].get("status") or {}
        confirmatory = [k for k in metrics if status.get(k) == "confirmatory"]
        reasons = (protocol.get("confirmatory") or {}).get("reasons") or []
        tables.append(
            Table(
                f"Plan metrics by {group_word}",
                [("model", group), ("apps", "Apps"), ("runs", "Runs"), *((k, _METRIC_HEADERS[k]) for k in metrics)],
                outcomes,
                "Each app's repeats are averaged first, then the mean over apps; code issues / kLOC is the apps' "
                "code issues over their non-test LOC. "
                + (
                    f"Confirmatory (pre-registered, planned sample): {', '.join(confirmatory)}; the rest exploratory."
                    if confirmatory
                    else "All exploratory" + (f" ({'; '.join(reasons)})." if reasons else ".")
                ),
            ),
        )

    tables.extend(_issue_tables(data, group, group_word))

    security = data.get("security_bounds") or {}
    title = "Code security issues by normalized severity (heuristic), with bounds"
    note = (
        "Security and secret issues in the app's own non-test code; dependency advisories, runtime (DAST) "
        "issues and test code are left out. Severity is the platform's normalized level (a heuristic; many "
        "tools print none). Apps are app units (affected when any repeat has the issue); issues total the runs. "
        "For a zero count the per-app rate is bounded (exact one-sided 95%), not zero."
    )
    tables.append(
        Table(
            title,
            [
                ("severity", "Severity (normalized)"),
                ("issues", "Issues"),
                ("apps_affected", "Apps affected"),
                ("apps", "Apps"),
                ("apps_affected_ci95", "Share [95% CI]"),
                ("zero_rate_upper95", "Upper bound if zero"),
            ],
            security.get("overall") or [],
            note,
        ),
    )

    code = data.get("code_metrics") or {}
    code_rows = code.get("by_model") or []
    # lizard / jscpd figures (study reports since they were recorded; older payloads have no such keys,
    # and a '-' there would wrongly say the tool measured nothing).
    tool_columns = [
        (key, label, stat)
        for key, label, stat in (
            ("lizard_avg_ccn", "Mean CCN (lizard)", "mean"),
            ("jscpd_duplication_percentage", "Duplication % (jscpd, median)", "median"),
        )
        if any(key in r for r in code_rows)
    ]
    tables.append(
        Table(
            f"Code size and complexity by {group_word}",
            [
                ("model", group),
                ("apps", "Apps"),
                ("loc.median", "LOC median"),
                ("loc.q1", "Q1"),
                ("loc.q3", "Q3"),
                ("backend_share.median", "Backend share"),
                ("files.median", "Files"),
                ("avg_complexity.mean", "Mean CC"),
                ("max_complexity.max", "Max CC"),
                *((f"_{key}", label) for key, label, _stat in tool_columns),
            ],
            [
                {**r, **{f"_{key}": _measured_stat(r.get(key), stat) for key, _label, stat in tool_columns}}
                for r in code_rows
            ],
            "Per app (repeats averaged); LOC counts non-test model-scope source lines (authored, modified and slot "
            "files)."
            + (
                " Mean CC is radon's (Python functions only); mean CCN is lizard's (Python and JS/TS functions) "
                "and duplication jscpd's share of duplicated lines, as the tools printed them over the analyzed "
                "tree (test files included); n = apps the tool measured, '-' = it measured no app of the group."
                if tool_columns
                else ""
            ),
        ),
    )
    tables.append(
        Table(
            f"Spread across {group_word}s on the same task",
            [
                ("task", "Task"),
                ("models", f"{group}s"),
                ("loc_cv_across_models", "LOC CV"),
                ("complexity_cv_across_models", "CC CV"),
                ("loc_min_model", "Smallest"),
                ("loc_max_model", "Largest"),
            ],
            code.get("spread_across_models") or [],
        ),
    )

    corr = data.get("correlations") or {}
    all_tests = corr.get("tests") or []
    # A test with no model to correlate (no recorded parameter, e.g. endpoint
    # models without catalog metadata) says nothing: list only the testable ones.
    corr_tests = [t for t in all_tests if t.get("n_models")]
    corr_notes = list(corr.get("notes") or [])
    if len(corr_tests) < len(all_tests):
        missing = sorted({t.get("predictor") for t in all_tests if not t.get("n_models")} - {None})
        corr_notes.append(
            f"Not tested ({len(all_tests) - len(corr_tests)} of {len(all_tests)}): no model in scope has both the "
            f"predictor and the outcome (predictors: {', '.join(missing)}; endpoint models carry no catalog metadata)."
            if missing
            else "",
        )
    tables.append(
        Table(
            "Model parameters vs outcomes (Spearman, exploratory)",
            [
                ("predictor", "Predictor"),
                ("outcome", "Outcome"),
                ("n_models", "n"),
                ("rho", "rho"),
                ("ci95_bootstrap", "95% CI"),
                ("p_permutation", "p"),
                ("p_holm", "p (Holm)"),
                ("q_bh", "q (BH)"),
                ("leave_one_out.most_influential", "Most influential"),
                ("leave_one_out.sign_flips", "Sign flips (LOO)"),
            ],
            corr_tests,
            " ".join(n for n in corr_notes if n),
        ),
    )

    ranking = data.get("ranking") or {}
    near = {r["model"]: r for r in (ranking.get("monte_carlo_near_weights") or {}).get("ranks") or []}
    anyw = {r["model"]: r for r in (ranking.get("monte_carlo_any_weights") or {}).get("ranks") or []}
    boot = {r["model"]: r for r in ((ranking.get("data_uncertainty") or {}).get("ranks") or [])}
    rank_rows = [
        {
            **r,
            "near": (near.get(r["model"]) or {}).get("rank_ci95"),
            "any": (anyw.get(r["model"]) or {}).get("rank_ci95"),
            "top1": (near.get(r["model"]) or {}).get("p_rank_1"),
            "top3": (near.get(r["model"]) or {}).get("p_top_3"),
            "boot": (boot.get(r["model"]) or {}).get("rank_ci95"),
        }
        for r in ranking.get("ranking") or []
    ]
    weights = ", ".join(f"{c['label']} {c['weight']:.2f} ({c['direction']})" for c in ranking.get("criteria") or [])
    tables.append(
        Table(
            f"Ranking ({ranking.get('status', 'exploratory')})",
            [
                ("model", group),
                ("topsis_rank", "TOPSIS"),
                ("topsis_score", "Score"),
                ("wsm_rank", "WSM"),
                ("near", "Rank 95% (weights ±)"),
                ("any", "Rank 95% (any weights)"),
                ("top1", "P(rank 1)"),
                # With 3 or fewer models every model is in the top 3.
                *([("top3", "P(top 3)")] if len(rank_rows) > 3 else []),
                ("boot", "Rank 95% (data)"),
            ],
            rank_rows,
            f"Weights: {weights}. TOPSIS vs WSM Kendall tau = {_fmt(ranking.get('topsis_vs_wsm_kendall_tau'))}. "
            f"{ranking.get('weights_note', '')}"
            + (
                f" Tied: {'; '.join(' = '.join(g) for g in ranking.get('tied_groups') or [])}."
                f" {ranking.get('ties_note', '')}"
                if ranking.get("models_tied")
                else ""
            ),
        ),
    )

    planned = data.get("planned_comparisons") or {}
    baseline = planned.get("baseline_condition")
    for family in planned.get("families") or []:
        tables.append(
            Table(
                f"Planned comparison: {family['metric']} ({family['role']}, {family['status']}, "
                f"{family.get('alternative', 'two-sided')})" + (f" vs baseline {baseline}" if baseline else ""),
                [
                    ("condition", "Condition"),
                    ("n_pairs", "Apps"),
                    ("mean_delta", "Mean delta"),
                    ("median_delta", "Median delta"),
                    # Sign counts of (condition - baseline): "above" is worse for cost-type metrics.
                    ("wins", "Above baseline"),
                    ("losses", "Below baseline"),
                    ("p_wilcoxon", "p (Wilcoxon)"),
                    ("p_adjusted", f"p ({planned.get('correction')})"),
                    ("significant", f"< {planned.get('alpha')}"),
                ],
                family["comparisons"],
                " ".join(
                    note
                    for note in (
                        "Deltas are condition minus baseline, per app (repeats averaged).",
                        "Interim: p-values are withheld until the study finishes." if planned.get("interim") else "",
                        "Stopped early: planned cells were cancelled or stopped, so these p-values are exploratory."
                        if planned.get("stopped_early")
                        else "",
                        f"Adjusted over all {planned.get('family_size')} planned tests (every declared metric)."
                        if planned.get("multiplicity") == "all"
                        else "Adjusted within this metric only.",
                        _no_p_note(family),
                        _skip_note(family, planned),
                    )
                    if note
                ),
            ),
        )

    tables.append(
        Table(
            "Threats to validity detected in this data",
            [("level", "Level"), ("section", "Affects"), ("message", "Finding")],
            data.get("validity") or [],
        ),
    )
    return tables


def _competition_ranks(rows: list[dict[str, Any]], key: str) -> dict[int, int]:
    """Rank per row index, equal values sharing the best rank (1-1-1-4); rows without a value rank last together."""
    values = [r.get(key) for r in rows]
    scored = sorted(v for v in values if v is not None)
    ranks = {}
    for i, v in enumerate(values):
        ranks[i] = 1 + sum(1 for s in scored if s > v) if v is not None else len(scored) + 1
    return ranks


def _study_conditions_tables(data: dict[str, Any]) -> list[Table]:
    """Curated tables of a study conditions report: design, leaderboard, paired comparison, notes, code issues."""
    study = data.get("study") or {}
    results = data.get("results") or {}
    conditions = data.get("conditions") or []
    leaderboard = results.get("leaderboard") or []
    sample = data.get("sample") or {}
    # One name per condition: the leaderboard's (the Results tab's), else the report's own.
    labels = {c["condition_id"]: c["label"] for c in conditions}
    labels.update({r["condition_id"]: r["label"] for r in leaderboard if r.get("condition_id") and r.get("label")})
    kv = [
        ("Study", study.get("name")),
        ("Kind", study.get("kind")),
        ("Hypothesis", study.get("hypothesis")),
        ("Status", study.get("status")),
        ("Repeats", study.get("repeats")),
    ]
    if sample:
        rule = "locked analysis plan" if sample.get("truncation_rule") == "analysis_plan" else "report setting"
        kv += [
            ("Runs (current run per main-stage cell)", sample.get("runs")),
            (
                "Truncated runs",
                f"{'excluded' if sample.get('exclude_truncated') else 'kept (intention-to-treat)'} ({rule}"
                + (f"; {sample['truncated_excluded']} excluded)" if sample.get("truncated_excluded") else ")"),
            ),
            ("Beyond-plan runs excluded", sample.get("beyond_plan_excluded")),
            ("Pilot runs excluded", sample.get("pilot_excluded")),
        ]
    baseline = next((c for c in conditions if c.get("is_baseline")), None)
    kv.append(("Baseline", labels.get(baseline["condition_id"], baseline.get("label")) if baseline else "-"))
    if results:
        kv += [
            ("Interim", results.get("interim")),
            ("Balanced", results.get("balanced")),
            ("Results computed at", results.get("generated_at")),
        ]
    tables = [Table("Study", [("k", "Item"), ("v", "Value")], [{"k": k, "v": v} for k, v in kv])]

    if leaderboard:
        ranks = _competition_ranks(leaderboard, "pass_rate_mean")
        tables.append(
            Table(
                "Leaderboard (pass rate)",
                [
                    ("rank", "Rank"),
                    ("label", "Condition"),
                    ("n_apps", "Apps"),
                    ("n_graded", "Graded"),
                    ("k_passed", "Fully passed"),
                    ("pass_rate_mean", "Mean pass rate"),
                    ("pass_rate_ci95", "95% CI (app bootstrap)"),
                    ("cost_usd", "Cost (USD)"),
                ],
                [{**r, "rank": ranks[i]} for i, r in enumerate(leaderboard)],
                "The mean is over apps (each app's repeats averaged first); no interval below 3 apps. Equal means "
                "share a rank.",
            ),
        )
    paired = results.get("paired") or {}
    if paired.get("rows"):
        alpha = paired.get("alpha")
        kind = f"{paired.get('status', 'exploratory')}, {paired.get('alternative', 'two-sided')}"
        tables.append(
            Table(
                f"Paired vs baseline ({kind})",
                [
                    ("condition", "Condition"),
                    ("n_pairs", "Apps"),
                    ("mean_delta", "Mean delta"),
                    ("ci95", "95% CI"),
                    ("wins", "Above baseline"),
                    ("losses", "Below baseline"),
                    ("ties", "Ties"),
                    ("wilcoxon_p", "p (Wilcoxon)"),
                    ("p_adjusted", f"p ({paired.get('correction') or 'holm'})"),
                    ("significant", f"< {alpha}" if alpha is not None else "Significant"),
                ],
                [{**r, "condition": labels.get(r["condition_id"], r["condition_id"])} for r in paired["rows"]],
                "App-blocked: each delta is one app's mean pass rate minus the baseline's on the same app.",
            ),
        )
    notes = [*(data.get("notes") or []), *(results.get("notes") or [])]
    if notes:
        tables.append(Table("Notes", [("note", "Note")], [{"note": n} for n in notes]))
    tables.append(
        Table(
            "Code issues per condition (deduplicated across tools)",
            [
                ("label", "Condition"),
                ("issues_dedup.apps", "Apps analyzed"),
                ("issues_dedup.runs", "Runs analyzed"),
                ("issues_dedup.raw_findings", "Raw findings"),
                ("issues_dedup.unique_issues", "Unique issues (all kinds)"),
                ("issues_dedup.code_unique", "Code issues"),
                ("issues_dedup.unique_per_kloc", "Code issues / kLOC"),
                ("issues_dedup.security_unique", "Security (code)"),
                ("issues_dedup.code_unique_tests", "In test code"),
                ("issues_dedup.runtime_unique", "Runtime (DAST)"),
                ("issues_dedup.dependency_unique", "Dependency advisories"),
                ("issues_dedup.dependency_added", "In versions the model chose"),
                ("issues_dedup.dependency_inherited", "Inherited (starter pins or transitive)"),
                ("generation.total_cost", "Generation cost (USD)"),
            ],
            [{**c, "label": labels.get(c.get("condition_id"), c.get("label"))} for c in conditions],
            "Each run's latest result per tool, counted only from the tools that measured every analyzed run "
            "of the study (see Notes); AI-reviewer findings excluded. Code issues leave out dependency "
            "advisories, runtime (DAST-only) issues and test code; code issues / kLOC is over the apps' "
            "non-test model-scope source lines (repeats averaged). '-' means not measured, not zero.",
        ),
    )
    return tables


# ── generic (every other report type) ────────────────────────────────────


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (dict, list))


def _is_records(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _is_buckets(value: Any) -> bool:
    """Counts per normalized severity (``{"critical": 1, "high": 3, ...}``)."""
    return (
        isinstance(value, dict)
        and len(value) >= 3  # a bucket dict names at least three levels
        and set(value) <= _SEVERITY_KEYS
        and all(isinstance(v, (int, float)) for v in value.values())
    )


def _tool_labelled(path: str) -> bool:
    """Whether the field at *path* holds levels as the tools printed them (``reported_levels``, ``tool_severity``).

    Some tools print exactly the lowercase level names (nuclei: info..critical;
    npm audit: low/high/critical), so key names alone cannot tell such a dict
    from normalized buckets; it must never be labelled normalized.
    """
    last = path.rsplit(".", 1)[-1].rsplit(" / ", 1)[-1]
    return "reported" in last or "tool_severity" in last or last == "levels"


def _cell_value(value: Any) -> bool:
    """A value one table cell can hold: a scalar, or a list that is not a list of objects."""
    return _is_scalar(value) or (isinstance(value, list) and not _is_records(value))


def _leaves(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """A row's cell values under dotted keys; severity buckets are labelled normalized; lists of objects left out."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and _tool_labelled(key):
            out[key] = v  # the tool's own labels with counts, in one cell ("MODERATE: 12, HIGH: 3")
        elif _is_buckets(v):
            out.update({f"{key}.{s} (normalized)": n for s, n in v.items()})
        elif isinstance(v, dict):
            out.update(_leaves(v, f"{key}."))
        elif _cell_value(v):
            out[key] = v
    return out


def _severity_note(columns: list[str]) -> str:
    return _NORMALIZED_NOTE if any(c.endswith("(normalized)") for c in columns) else ""


def _identity(rows: list[dict[str, Any]], columns: list[str]) -> str | None:
    """The column that names a row (the first top-level one whose values are all strings)."""
    for c in columns:
        if "." not in c and all(isinstance(r.get(c), str) for r in rows):
            return c
    return None


def _record_tables(title: str, rows: list[dict[str, Any]]) -> list[Table]:
    """A list of objects: dotted columns split into tables of at most ``_MAX_GENERIC_COLUMNS``; nested lists below."""
    flat = [_leaves(r) for r in rows]
    columns: list[str] = []
    for r in flat:
        columns += [k for k in r if k not in columns]
    # A field that is None in some rows (unmeasured) and an object in others: its dotted
    # columns carry the values, and the None-only column would read as missing data.
    columns = [
        c for c in columns if not (any(o.startswith(f"{c}.") for o in columns) and all(r.get(c) is None for r in flat))
    ]
    # Top-level columns first, then nested ones; within each, the order seen.
    columns.sort(key=lambda c: c.count("."))
    ident = _identity(flat, columns)
    rest = [c for c in columns if c != ident]
    width = _MAX_GENERIC_COLUMNS - (1 if ident else 0)
    chunks = [rest[i : i + width] for i in range(0, len(rest), width)] or [[]]
    tables = []
    for n, chunk in enumerate(chunks, start=1):
        cols = ([ident] if ident else []) + chunk
        suffix = f" ({n} of {len(chunks)})" if len(chunks) > 1 else ""
        tables.append(Table(f"{title}{suffix}", [(c, c) for c in cols], flat, _severity_note(cols)))
    # Lists of objects inside the rows (e.g. a ranking per scheme): child tables keyed by the parent row.
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def collect(row: dict[str, Any], parent: dict[str, Any], prefix: str = "") -> None:
        for k, v in row.items():
            if isinstance(v, dict):
                collect(v, parent, f"{prefix}{k}.")
            elif _is_records(v):
                children[f"{prefix}{k}"] += [{**parent, **child} for child in v]

    for raw, row in zip(rows, flat, strict=True):
        collect(raw, {ident: row.get(ident)} if ident else {})
    for path, items in children.items():
        tables.extend(_record_tables(f"{title} / {path}", items))
    return tables


def _mapping_rows(value: dict[str, Any], path: str = "") -> list[dict[str, Any]]:
    """Nested mappings as rows: one per object that holds cell values, keyed by its path (``radon / numeric``)."""
    rows = []
    cells = {k: v for k, v in value.items() if _cell_value(v)}
    if _is_buckets(cells) and not _tool_labelled(path):
        cells = {f"{k} (normalized)": v for k, v in cells.items()}
    if cells and path:
        rows.append({"key": path, **cells})
    for k, v in value.items():
        if isinstance(v, dict):
            rows.extend(_mapping_rows(v, f"{path} / {k}" if path else str(k)))
        elif _is_records(v):
            rows.extend({"key": f"{path} / {k}" if path else str(k), **_leaves(item)} for item in v)
    return rows


def _is_mapping_of_objects(value: dict[str, Any]) -> bool:
    """A dict keyed by name whose values are all objects (``metrics_by_tool``), not a record of fields."""
    return bool(value) and all(isinstance(v, dict) for v in value.values())


def _generic_tables(data: dict[str, Any], prefix: str = "") -> list[Table]:
    tables: list[Table] = []
    scalars = [{"k": k, "v": v} for k, v in data.items() if _cell_value(v)]
    if scalars:
        severity = bool(prefix) and not _tool_labelled(prefix) and _is_buckets({s["k"]: s["v"] for s in scalars})
        tables.append(
            Table(
                (prefix or "Summary") + (" (normalized severity)" if severity else ""),
                [("k", "Item"), ("v", "Value")],
                scalars,
                _NORMALIZED_NOTE if severity else "",
            ),
        )
    for key, value in data.items():
        title = f"{prefix} / {key}" if prefix else str(key)
        if _is_records(value):
            tables.extend(_record_tables(title, value))
        elif isinstance(value, dict) and _is_mapping_of_objects(value):
            rows = _mapping_rows(value)
            columns: list[str] = []
            for r in rows:
                columns += [k for k in r if k != "key" and k not in columns]
            if rows:
                tables.extend(
                    _record_tables(title, [{"key": r["key"], **{c: r.get(c) for c in columns}} for r in rows]),
                )
        elif isinstance(value, dict):
            tables.extend(_generic_tables(value, title))
    return tables


def _task_comparison_tables(data: dict[str, Any]) -> list[Table]:
    """A curated models table first; every field follows in the generic tables."""

    def measured(block: dict[str, Any], key: str, count: str) -> Any:
        """``block[key]``, or None when the block says nothing was counted (unmeasured is not 0)."""
        return block.get(key) if block.get(count, 1) else None

    def row(m: dict[str, Any]) -> dict[str, Any]:
        stats = m.get("stats") or {}
        analyzed = stats.get("analyzed")
        dedup = m.get("issues_dedup") or {}
        unmeasured = analyzed == 0
        return {
            **m,
            "runs": (m.get("generation") or {}).get("total_runs"),
            "completed": (m.get("generation") or {}).get("completed_runs"),
            "treatment": (m.get("treatment_hash") or "")[:12],
            "analyzed": analyzed,
            "loc": measured(m.get("loc") or {}, "total_loc", "counted"),
            # Not analyzed is unmeasured, not zero findings.
            "raw": None if unmeasured else m.get("total_findings"),
            "unique": None if unmeasured else dedup.get("unique_issues"),
            "code": None if unmeasured else dedup.get("code_unique"),
            "per_kloc": None if unmeasured else dedup.get("unique_per_kloc"),
            "security": None if unmeasured else dedup.get("security_unique"),
            "deps": None if unmeasured else dedup.get("dependency_unique"),
            "deps_added": None if unmeasured else dedup.get("dependency_added"),
            "pass_rate": measured((stats.get("functional") or {}).get("pass_rate") or {}, "mean", "n"),
            "cost": (m.get("generation") or {}).get("total_cost"),
        }

    models = data.get("models") or []
    return [
        Table(
            "Models",
            [
                ("model_id", "Model"),
                ("treatment", "Treatment"),
                ("runs", "Runs"),
                ("completed", "Completed"),
                ("analyzed", "Analyzed"),
                ("loc", "LOC"),
                ("raw", "Raw findings"),
                ("unique", "Unique issues"),
                ("code", "Code issues"),
                ("per_kloc", "Code issues / kLOC"),
                ("security", "Security"),
                ("deps", "Dependency advisories"),
                ("deps_added", "In versions the model chose"),
                ("pass_rate", "Mean pass rate"),
                ("cost", "Cost (USD)"),
            ],
            [row(m) for m in models],
            "Counts are deduplicated across tools over each app's latest result per tool; '-' means no app of "
            "that model was analyzed (unmeasured, not zero). Every field follows below.",
        ),
    ]


def report_tables(report: Report) -> list[Table]:
    data = report.report_data or {}
    if report.report_type == Report.Type.STUDY_REPORT:
        tables = _study_tables(data)
    elif report.report_type == Report.Type.STUDY_CONDITIONS and "conditions" in data:
        tables = _study_conditions_tables(data)
    elif report.report_type == Report.Type.TASK_COMPARISON and _is_records(data.get("models")):
        tables = _task_comparison_tables(data) + _generic_tables(data)
    else:
        tables = _generic_tables(data)
    for table in tables:
        if len(table.rows) > _MAX_ROWS:
            cut = f"First {_MAX_ROWS} of {len(table.rows)} rows; the JSON export has all of them."
            table.note = f"{table.note} {cut}".strip()
    return tables


# ── Markdown ─────────────────────────────────────────────────────────────


def _md_cell(value: Any) -> str:
    return _fmt(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Report) -> str:
    lines = [f"# {report.title}", ""]
    if report.description:
        lines += [report.description, ""]
    when = report.completed_at or report.created_at
    stamp = f", generated {when:%Y-%m-%d %H:%M UTC}" if when else ""
    lines += [f"Report `{report.report_id}` ({report.report_type}){stamp}.", ""]
    for table in report_tables(report):
        lines += [f"## {table.title}", ""]
        if not table.rows:
            # The note says why a table is empty (e.g. nothing could be tested).
            lines += ["_No data._", ""] + ([f"_{table.note}_", ""] if table.note else [])
            continue
        lines.append("| " + " | ".join(_md_cell(h) for _, h in table.columns) + " |")
        lines.append("|" + "|".join("---" for _ in table.columns) + "|")
        for row in table.rows[:_MAX_ROWS]:
            lines.append("| " + " | ".join(_md_cell(_flatten(row, k)) for k, _ in table.columns) + " |")
        if table.note:
            lines += ["", f"_{table.note}_"]
        lines.append("")
    return "\n".join(lines)


# ── LaTeX ────────────────────────────────────────────────────────────────

_TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    # Text-mode < > | print as ¡ ¿ — under the default OT1 font encoding.
    "<": r"\textless{}",
    ">": r"\textgreater{}",
    "|": r"\textbar{}",
}
_TEX_RE = re.compile("|".join(re.escape(k) for k in _TEX_ESCAPES))


def tex_escape(text: str) -> str:
    return _TEX_RE.sub(lambda m: _TEX_ESCAPES[m.group(0)], text)


def _label(title: str) -> str:
    return "tab:" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]


def render_latex(report: Report) -> str:
    """LaTeX table environments (needs ``\\usepackage{booktabs}`` and ``\\usepackage{longtable}``)."""
    out = [
        f"% {tex_escape(report.title)} -- report {report.report_id} ({report.report_type})",
        "% Requires \\usepackage{booktabs} and \\usepackage{longtable}.",
        "",
    ]
    used_labels: set[str] = set()
    for table in report_tables(report):
        if not table.rows:
            continue
        label = _label(table.title)
        while label in used_labels:
            label += "-x"
        used_labels.add(label)
        spec = "l" * len(table.columns)
        caption = tex_escape(table.title)
        if table.note:
            caption += r". \footnotesize " + tex_escape(table.note)
        out += [
            f"\\begin{{longtable}}{{{spec}}}",
            f"\\caption{{{caption}}}\\label{{{label}}}\\\\",
            "\\toprule",
            " & ".join(tex_escape(h) for _, h in table.columns) + r" \\",
            "\\midrule",
            "\\endhead",
        ]
        for row in table.rows[:_MAX_ROWS]:
            out.append(" & ".join(tex_escape(_fmt(_flatten(row, k))) for k, _ in table.columns) + r" \\")
        out += ["\\bottomrule", "\\end{longtable}", ""]
    return "\n".join(out)
