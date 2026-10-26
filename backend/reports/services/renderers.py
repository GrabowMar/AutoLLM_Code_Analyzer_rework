"""Render a report as Markdown or LaTeX (booktabs) tables.

The study report has curated tables, one per section, in the order a paper
reports them. Every other report type falls back to a generic rendering:
top-level scalars as a key/value table, and every list of objects as a
table of its scalar columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from backend.reports.models import Report

_MAX_GENERIC_COLUMNS = 12
_MAX_ROWS = 500


@dataclass
class Table:
    title: str
    columns: list[tuple[str, str]]  # (key, header)
    rows: list[dict[str, Any]]
    note: str = ""


def _fmt(value: Any) -> str:  # noqa: PLR0911 - one branch per value kind
    if value is None:
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
    if not cell or cell.get("rate") is None:
        return "-"
    ci = cell.get("ci95")
    text = f"{cell['rate'] * 100:.1f}% ({cell['k']}/{cell['n']})"
    return f"{text} [{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]" if ci else text


def _flatten(row: dict[str, Any], path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _study_tables(data: dict[str, Any]) -> list[Table]:
    tables: list[Table] = []
    protocol = data.get("protocol") or {}
    experiment = protocol.get("experiment") or {}
    kv = [
        ("Jobs", protocol.get("jobs")),
        ("Models", len(protocol.get("models") or [])),
        ("Templates", len(protocol.get("templates") or [])),
        ("Modes", protocol.get("modes")),
        ("Stacks", protocol.get("stacks")),
        ("Build healing", protocol.get("build_healing")),
        ("Prompt versions", protocol.get("prompt_versions")),
        ("Cells by trial count (trials: cells)", protocol.get("trials_per_cell")),
        ("Truncated (excluded)", protocol.get("truncated_excluded")),
        ("Tools", ", ".join(f"{t['slug']} {t['version']}".strip() for t in protocol.get("tools") or [])),
        ("Evaluators", protocol.get("evaluators")),
        ("Analysis plan hash", experiment.get("analysis_plan_hash")),
        ("Plan frozen at", experiment.get("analysis_plan_frozen_at")),
        ("Data fingerprint", (protocol.get("data_fingerprint") or "")[:16]),
        ("Seed", (protocol.get("settings") or {}).get("seed")),
    ]
    tables.append(Table("Protocol", [("k", "Item"), ("v", "Value")], [{"k": k, "v": v} for k, v in kv]))

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
            "jobs": r["jobs"],
            "generated": r["generated"],
            "evaluated": r["evaluated"],
            "deploy": _rate(r["deploy_rate"]),
            "functional": _rate(r["functional_rate"]),
            "given": _rate(r["functional_given_deployed"]),
            "pass": r["mean_pass_rate"],
        }
        for r in [*(funnel.get("by_model") or []), {**(funnel.get("overall") or {}), "model": "All"}]
        if r.get("jobs") is not None
    ]
    tables.append(
        Table(
            "Outcome funnel by model",
            [
                ("model", "Model"),
                ("jobs", "Jobs"),
                ("generated", "Generated"),
                ("evaluated", "Evaluated"),
                ("deploy", "Deployed [95% CI]"),
                ("functional", "Correct [95% CI]"),
                ("given", "Correct | deployed"),
                ("pass", "Mean pass rate"),
            ],
            funnel_rows,
            "Rates are over evaluated apps; intervals are Wilson 95%.",
        ),
    )

    issues = data.get("issues") or {}
    issue_cols = [
        ("apps", "Apps"),
        ("raw_findings", "Raw findings"),
        ("unique_issues", "Unique issues"),
        ("duplicate_findings", "Duplicates"),
        ("mean_unique_per_app", "Mean/app"),
        ("median_unique_per_app", "Median/app"),
        ("unique_per_kloc", "Per kLOC"),
        ("security_unique", "Security"),
        ("unique_critical", "Critical"),
        ("unique_high", "High"),
    ]
    tables.append(
        Table("Issues by model (deduplicated)", [("model", "Model"), *issue_cols], issues.get("by_model") or []),
    )
    tables.append(
        Table(
            "Issues by template (deduplicated)",
            [("template", "Template"), *issue_cols],
            issues.get("by_template") or [],
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
                ("apps_with_findings", "Apps"),
            ],
            issues.get("by_tool") or [],
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
    tables.append(
        Table(
            "Issue types by tool (top 40)",
            [
                ("tool", "Tool"),
                ("rule_id", "Rule"),
                ("issue_class", "Class"),
                ("findings", "Findings"),
                ("apps", "Apps"),
                ("max_severity", "Max severity"),
            ],
            (issues.get("by_tool_rule") or [])[:40],
        ),
    )

    security = data.get("security_bounds") or {}
    tables.append(
        Table(
            "Security severity with bounds",
            [
                ("severity", "Severity"),
                ("issues", "Issues"),
                ("apps_affected", "Apps affected"),
                ("apps", "Apps"),
                ("apps_affected_ci95", "Share [95% CI]"),
                ("zero_rate_upper95", "Upper bound if zero"),
            ],
            security.get("overall") or [],
            "For a zero count the per-app rate is bounded (exact one-sided 95%), not zero.",
        ),
    )

    code = data.get("code_metrics") or {}
    tables.append(
        Table(
            "Code size and complexity by model",
            [
                ("model", "Model"),
                ("apps", "Apps"),
                ("loc.median", "LOC median"),
                ("loc.q1", "Q1"),
                ("loc.q3", "Q3"),
                ("backend_share.median", "Backend share"),
                ("files.median", "Files"),
                ("avg_complexity.mean", "Mean CC"),
                ("max_complexity.max", "Max CC"),
            ],
            code.get("by_model") or [],
        ),
    )
    tables.append(
        Table(
            "Spread across models on the same template",
            [
                ("template", "Template"),
                ("models", "Models"),
                ("loc_cv_across_models", "LOC CV"),
                ("complexity_cv_across_models", "CC CV"),
                ("loc_min_model", "Smallest"),
                ("loc_max_model", "Largest"),
            ],
            code.get("spread_across_models") or [],
        ),
    )

    corr = data.get("correlations") or {}
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
            corr.get("tests") or [],
            " ".join(corr.get("notes") or []),
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
                ("model", "Model"),
                ("topsis_rank", "TOPSIS"),
                ("topsis_score", "Score"),
                ("wsm_rank", "WSM"),
                ("near", "Rank 95% (weights ±)"),
                ("any", "Rank 95% (any weights)"),
                ("top3", "P(top 3)"),
                ("boot", "Rank 95% (data)"),
            ],
            rank_rows,
            f"Weights: {weights}. TOPSIS vs WSM Kendall tau = {_fmt(ranking.get('topsis_vs_wsm_kendall_tau'))}. "
            f"{ranking.get('weights_note', '')}",
        ),
    )

    planned = data.get("planned_comparisons") or {}
    for family in planned.get("families") or []:
        tables.append(
            Table(
                f"Planned comparison: {family['metric']} ({family['role']}, {family['status']})",
                [
                    ("condition", "Condition"),
                    ("n_pairs", "Apps"),
                    ("mean_delta", "Mean delta"),
                    ("median_delta", "Median delta"),
                    ("wins", "Wins"),
                    ("losses", "Losses"),
                    ("p_wilcoxon", "p (Wilcoxon)"),
                    ("p_adjusted", f"p ({planned.get('correction')})"),
                    ("significant", f"< {planned.get('alpha')}"),
                ],
                family["comparisons"],
                " ".join(
                    note
                    for note in (
                        "Interim: do not interpret p-values yet." if planned.get("interim") else "",
                        (
                            f"Not tested (fewer than {planned.get('min_apps_per_condition')} apps): "
                            + ", ".join(c["condition"] for c in family["comparisons"] if c.get("skipped"))
                            + "."
                        )
                        if any(c.get("skipped") for c in family["comparisons"])
                        else "",
                    )
                    if note
                ),
            ),
        )

    healing = data.get("healing") or {}
    modes = healing.get("modes") or []
    if len(modes) > 1:
        tables.append(
            Table(
                "Deploy rate with and without build healing",
                [("model", "Model"), ("apps", "Apps")]
                + [(f"deploy_rate_{m}", f"Deploy % ({m})") for m in modes]
                + [(f"rescued_vs_{m}", f"Rescued vs {m}") for m in modes if m != healing.get("baseline")]
                + [(f"mcnemar_p_vs_{m}", f"McNemar p vs {m}") for m in modes if m != healing.get("baseline")],
                [*(healing.get("by_model") or []), {"model": "All", **(healing.get("totals") or {})}],
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


def _generic_tables(data: dict[str, Any], prefix: str = "") -> list[Table]:
    tables: list[Table] = []
    scalars = [{"k": k, "v": v} for k, v in data.items() if not isinstance(v, (dict, list))]
    if scalars:
        tables.append(Table(prefix or "Summary", [("k", "Item"), ("v", "Value")], scalars))
    for key, value in data.items():
        title = f"{prefix} / {key}" if prefix else key
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            columns = []
            for row in value:
                for k, v in row.items():
                    if k not in columns and not isinstance(v, (dict, list)):
                        columns.append(k)
            tables.append(Table(title, [(c, c) for c in columns[:_MAX_GENERIC_COLUMNS]], value))
        elif isinstance(value, dict) and not prefix:
            tables.extend(_generic_tables(value, title))
    return tables


def report_tables(report: Report) -> list[Table]:
    data = report.report_data or {}
    if report.report_type == Report.Type.STUDY_REPORT:
        return _study_tables(data)
    return _generic_tables(data)


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
            lines += ["_No data._", ""]
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
    for table in report_tables(report):
        if not table.rows:
            continue
        spec = "l" * len(table.columns)
        caption = tex_escape(table.title)
        if table.note:
            caption += r". \footnotesize " + tex_escape(table.note)
        out += [
            f"\\begin{{longtable}}{{{spec}}}",
            f"\\caption{{{caption}}}\\label{{{_label(table.title)}}}\\\\",
            "\\toprule",
            " & ".join(tex_escape(h) for _, h in table.columns) + r" \\",
            "\\midrule",
            "\\endhead",
        ]
        for row in table.rows[:_MAX_ROWS]:
            out.append(" & ".join(tex_escape(_fmt(_flatten(row, k))) for k, _ in table.columns) + r" \\")
        out += ["\\bottomrule", "\\end{longtable}", ""]
    return "\n".join(out)
