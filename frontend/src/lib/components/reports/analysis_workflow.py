"""Portable analysis of a saved report; uses the report's recorded statistical results."""

# This source is embedded into a notebook cell rather than imported as a package.
# ruff: noqa: INP001

import hashlib
import json
import linecache
import platform
import zipfile
from collections import defaultdict
from pathlib import Path
from textwrap import fill

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.colors import to_rgb
from matplotlib.patches import Patch

WORKFLOW_VERSION = 5
SEVERITIES = ("critical", "high", "medium", "low", "info")
# Cross-experiment selection cells as drawn: (code, legend label, colour-blind-safe colour, letter).
SELECTION_CATEGORIES = (
    ("tested", "Selected, tested", "#0072B2", "T"),
    ("failed", "Selected, output invalid / build or start failed", "#D55E00", "F"),
    ("selected", "Selected, outcome not recorded", "#56B4E9", "S"),
    ("excluded", "Selected, left out: task dropped or truncated", "#999999", "L"),
    ("attrition", "Attrition: no usable run", "#E69F00", "A"),
    ("in_flight", "Still running: not in the sample", "#CC79A7", "R"),
    ("missing", "No attempt", "#EEEEEE", "-"),
)
SELECTION_MATRIX_COLUMNS = (
    "group",
    "task",
    "status",
    "category",
    "in_sample",
    "excluded",
    "outcome",
    "run_id",
    "study_id",
    "study_name",
    "created_at",
    "attempts",
    "superseded_run_ids",
)
SELECTION_SUPERSEDED_COLUMNS = (
    "run_id",
    "group",
    "task",
    "study_id",
    "study_name",
    "outcome",
    "usable",
    "created_at",
    "replaced_by",
    "recorded_attempt",
    "reason",
)


def _mean(values):
    values = [float(v) for v in values if v is not None and np.isfinite(float(v))]
    return float(np.mean(values)) if values else None


def numeric_data_fingerprint(value):
    """Hash JSON values with equivalent integral numeric representations normalized."""

    def normalized(item):
        if isinstance(item, dict):
            return {key: normalized(entry) for key, entry in item.items()}
        if isinstance(item, list):
            return [normalized(entry) for entry in item]
        if isinstance(item, float) and np.isfinite(item) and item.is_integer():
            return int(item)
        return item

    raw = json.dumps(normalized(value), sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def deployment_outcome(row):
    """Use final-outcome evidence, including explicit start evidence for untested runs."""
    outcome = row.get("outcome")
    if outcome in {"output_invalid", "build_fail", "start_fail"}:
        return False
    if outcome == "tested":
        if str(row.get("outcome_reason") or "").startswith("not_tested:"):
            return True if row.get("start_stage_status") == "ok" else None
        return True
    return None


def app_observations(rows):
    """Average repeats using exactly the app identity used by the report service."""
    groups = defaultdict(list)
    for row in rows:
        identity = (
            row.get("condition_id"),
            row.get("model"),
            row.get("kind"),
            row.get("unit_key") or row.get("run_id"),
        )
        groups[identity].append(row)
    output = []
    measured = (
        "unique_issues",
        "code_unique_model",
        "code_unique_tests",
        "dependency_added",
        "dependency_inherited",
        "runtime_unique",
        "security_unique_model",
        *(f"unique_normalized_{s}" for s in SEVERITIES),
    )
    numeric = (
        "loc",
        "loc_backend",
        "loc_frontend",
        "avg_complexity",
        "max_complexity",
        "lizard_avg_ccn",
        "jscpd_duplication_percentage",
    )
    for key, repeats in groups.items():
        first = repeats[0]
        generated = [r for r in repeats if r.get("generated")]
        analyzed = [r for r in repeats if r.get("analyzed")]
        evaluated = [r for r in repeats if r.get("evaluated")]
        deployment = [deployment_outcome(r) for r in repeats]
        row = {
            "group": first.get("group") or first.get("model") or "Unlabelled",
            "task": first.get("task") or first.get("unit_key") or "Unlabelled",
            "unit_key": key[-1],
            "condition_id": key[0],
            "model": key[1],
            "kind": key[2],
            "repeats": len(repeats),
            "deployment_unknown_runs": sum(value is None for value in deployment),
            "observed_deployment": _mean(deployment) if all(value is not None for value in deployment) else None,
        }
        row.update({k: _mean(r.get(k) for r in generated) for k in numeric})
        row.update(
            {f"collected_{k}": _mean(r.get(k) for r in repeats) for k in ("loc", "loc_backend", "loc_frontend")},
        )
        row["cost"] = _mean(r.get("cost") for r in generated)
        row.update({k: _mean(r.get(k) for r in analyzed) for k in measured})
        row.update({k: _mean(r.get(k) for r in evaluated) for k in ("deployed", "pass_rate", "functional_passed")})
        code = row["code_unique_model"]
        loc = _mean(r.get("loc") for r in analyzed)
        row["issues_per_kloc"] = code * 1000 / loc if code is not None and loc else None
        output.append(row)
    return pd.DataFrame(output)


def _frame(rows):
    return pd.DataFrame(rows or [])


def _numeric(frame, columns):
    return frame.reindex(columns=columns).apply(pd.to_numeric, errors="coerce")


def threshold_sensitivity(rows, thresholds=None):
    """Threshold each measured run, then average repeats within app and apps within group."""
    thresholds = [value / 100 for value in range(50, 101, 5)] if thresholds is None else thresholds
    units = defaultdict(list)
    for row in rows:
        value = row.get("pass_rate")
        if not row.get("evaluated") or value is None:
            continue
        identity = (
            row.get("condition_id"),
            row.get("model"),
            row.get("kind"),
            row.get("unit_key") or row.get("run_id"),
        )
        group = row.get("group") or row.get("model") or "Unlabelled"
        units[(group, identity)].append(float(value))
    result = []
    for threshold in thresholds:
        grouped = defaultdict(list)
        for (group, _identity), values in units.items():
            grouped[group].append((float(np.mean(np.asarray(values) >= threshold)), len(values)))
        for group, values in grouped.items():
            result.append(
                {
                    "group": group,
                    "threshold": float(threshold),
                    "rate": float(np.mean([v[0] for v in values])),
                    "apps": len(values),
                    "measured_runs": sum(v[1] for v in values),
                },
            )
    return pd.DataFrame(result)


def task_sensitivity(apps):
    """Descriptive task deletions within each group's own observed task set; not confidence intervals."""
    records = []
    if apps.empty:
        return pd.DataFrame()
    for metric in ("pass_rate", "unique_issues", "avg_complexity"):
        for group, rows in apps.dropna(subset=[metric]).groupby("group"):
            tasks = sorted(rows["task"].unique())
            if len(tasks) < 2:
                records.append(
                    {
                        "metric": metric,
                        "group": group,
                        "n_tasks": len(tasks),
                        "mean_full": rows[metric].mean(),
                        "omitted_task": None,
                        "mean_without_task": None,
                        "n_apps_remaining": 0,
                        "status": "insufficient_tasks",
                    },
                )
                continue
            for task in tasks:
                subset = rows.loc[rows["task"] != task]
                records.append(
                    {
                        "metric": metric,
                        "group": group,
                        "n_tasks": len(tasks),
                        "mean_full": rows[metric].mean(),
                        "omitted_task": task,
                        "mean_without_task": subset[metric].mean(),
                        "n_apps_remaining": len(subset),
                        "status": "descriptive",
                    },
                )
    return pd.DataFrame(records)


def model_observations(data):
    """Match the backend's one-point-per-model correlations, averaging conditions equally."""
    params = _frame((data.get("correlations") or {}).get("model_parameters"))
    outcomes = _frame(data.get("model_outcomes"))
    if params.empty or outcomes.empty:
        return pd.DataFrame()
    outcomes["model_identity"] = (
        outcomes["model_id"].fillna(outcomes["model"]) if "model_id" in outcomes else outcomes["model"]
    )
    declared = [t["outcome"] for t in (data.get("correlations") or {}).get("tests") or []]
    columns = [
        k
        for k in dict.fromkeys([*declared, "deploy_rate", "pass_rate", "issues_per_kloc", "security_per_app"])
        if k in outcomes
    ]
    means = outcomes.groupby("model_identity")[columns].mean().round(4).rename_axis("model").reset_index()
    return params.merge(means, on="model", how="inner", validate="one_to_one")


def selection_in_sample(cell, dropped=frozenset()):
    """Whether the unit's run is in the analysed sample (payloads without ``in_sample``: not on a dropped task)."""
    if not cell or cell.get("status") != "selected":
        return False
    if isinstance(cell.get("in_sample"), bool):
        return cell["in_sample"]
    return cell.get("task") not in dropped


def selection_category(cell, dropped=frozenset()):
    """Drawn category of one selection unit; a unit without a row is a missing attempt.

    A selected run left out of the sample (its task dropped for complete cases, or truncated with truncated
    runs excluded) is ``excluded``, so the selected categories add up to the analysed sample.
    """
    if not cell:
        return "missing"
    status = cell.get("status")
    if status == "selected":
        if not selection_in_sample(cell, dropped):
            return "excluded"
        outcome = cell.get("outcome")
        if outcome == "tested":
            return "tested"
        return "failed" if outcome in {"output_invalid", "build_fail", "start_fail"} else "selected"
    return status if status in {"attrition", "in_flight"} else "missing"


def selection_tables(selection):
    """Long matrix, wide group x task categories and superseded attempts of a cross-experiment selection.

    Groups and tasks keep the report's order; units naming a group or task the lists lack are appended,
    and a unit the matrix omits is a missing attempt rather than a dropped row.
    """
    matrix = [cell for cell in selection.get("matrix") or [] if isinstance(cell, dict)]
    groups = list(
        dict.fromkeys(
            [g.get("group") for g in selection.get("groups") or [] if isinstance(g, dict)]
            + [cell.get("group") for cell in matrix],
        ),
    )
    tasks = list(
        dict.fromkeys(
            list(selection.get("tasks") or [])
            + list(selection.get("dropped_tasks") or [])
            + [cell.get("task") for cell in matrix],
        ),
    )
    units = {(cell.get("group"), cell.get("task")): cell for cell in matrix}
    dropped = frozenset(selection.get("dropped_tasks") or [])
    long_rows = []
    for group in groups:
        for task in tasks:
            cell = units.get((group, task)) or {}
            in_sample = selection_in_sample(cell, dropped)
            excluded = cell.get("excluded")
            if excluded is None and cell.get("status") == "selected" and not in_sample:
                excluded = "dropped_task"
            long_rows.append(
                {
                    "group": group,
                    "task": task,
                    "status": cell.get("status") or "missing",
                    "category": selection_category(cell, dropped),
                    "in_sample": in_sample,
                    "excluded": excluded,
                    "outcome": cell.get("outcome"),
                    "run_id": cell.get("run_id"),
                    "study_id": cell.get("study_id"),
                    "study_name": cell.get("study_name"),
                    "created_at": cell.get("created_at"),
                    "attempts": cell.get("attempts") or 0,
                    "superseded_run_ids": ";".join(cell.get("superseded_run_ids") or []),
                },
            )
    long = pd.DataFrame(long_rows, columns=list(SELECTION_MATRIX_COLUMNS))
    wide = (
        long.pivot(index="group", columns="task", values="category")  # noqa: PD010 - duplicate units must fail
        .reindex(index=groups, columns=tasks)
        .fillna("missing")
        if not long.empty
        else pd.DataFrame()
    )
    superseded = pd.DataFrame(
        [r for r in selection.get("superseded") or [] if isinstance(r, dict)],
    ).reindex(columns=list(SELECTION_SUPERSEDED_COLUMNS))
    return long, wide, superseded


def task_oracle_evidence(protocol):
    """Describe task-specific control records without treating their presence as proof of correctness."""
    tasks = (protocol.get("requirements") or {}).get("tasks") or []
    entries, scope_checks = [], []

    def canonical(value):
        return str(value or "").split("#", 1)[0]

    for task in tasks:
        oracle = task.get("oracle") or {}
        records = [
            {"task": task.get("task"), "stack": stack, **entry}
            for stack, entry in oracle.items()
            if isinstance(entry, dict) and entry.get("status")
        ]
        entries.extend(records)
        instruments = task.get("selected_instruments") or []
        if not instruments:
            scope_checks.append({"task": task.get("task"), "status": "scope_not_recorded"})
        for instrument in instruments:
            matches = [
                r
                for r in records
                if canonical(r["stack"]) == canonical(instrument.get("stack"))
                and instrument.get("eval_suite")
                and canonical(r.get("eval_suite_ref")) == canonical(instrument["eval_suite"])
            ]
            # Conflicting aliases for the same pinned instrument are not a passing validation.
            statuses = {r["status"] for r in matches}
            scope_checks.append(
                {
                    "task": task.get("task"),
                    **instrument,
                    "status": next(iter(statuses)) if len(statuses) == 1 else "conflicting" if statuses else "missing",
                    "matching_run_ids": [r.get("run_id") for r in matches],
                },
            )
    statuses = pd.Series([entry["status"] for entry in scope_checks], dtype="string").value_counts().to_dict()
    recorded = sum(n for state, n in statuses.items() if state not in ("missing", "scope_not_recorded"))
    if not recorded:
        status = "unavailable"
    elif any(state in statuses for state in ("fail", "error", "conflicting")):
        status = "warning"
    elif any(state in statuses for state in ("missing", "scope_not_recorded")):
        status = "partial"
    else:
        status = "recorded"
    return {
        "check": "task_oracle_validation",
        "status": status,
        "detail": f"{len(tasks)} task specifications; selected stack/suite control statuses: {json.dumps(statuses)}. "
        "Only matching task-specific stack and evaluation-suite records count; kit-only controls do not. "
        "Generic evaluator conformance or mutant tests do not establish task-specific ground truth; "
        "functional scores describe measured checks, not independent full requirements correctness.",
        "records": entries,
        "scope_checks": scope_checks,
    }


def selection_methods(selection):
    """Methods paragraph for a cross-experiment selection; nothing for a single-experiment report."""
    if not isinstance(selection, dict) or not (selection.get("matrix") or selection.get("groups")):
        return []
    counts = selection.get("counts") or {}
    filters = selection.get("filters") or {}
    analyses = selection.get("analyses") or {}
    return [
        "## Sample selection",
        "",
        (selection.get("rule") or "Newest usable run per group and task across the pooled experiments.")
        + f" Pooled experiments: {len(selection.get('studies') or [])}. "
        f"Candidate runs: {counts.get('candidates')}; runs in the analysed sample: {counts.get('selected')}; "
        f"superseded attempts: {counts.get('superseded')}; attrition units: {counts.get('attrition_cells')}; "
        f"units without an attempt: {counts.get('missing_cells')}; "
        f"units still running: {counts.get('in_flight_cells')}"
        + (
            f"; selected runs left out as truncated (sensitivity analysis): {counts.get('truncated_excluded')}"
            if counts.get("truncated_excluded")
            else ""
        )
        + (
            f"; selected runs left out with dropped tasks: {counts.get('dropped_runs')}"
            if counts.get("dropped_runs")
            else ""
        )
        + ". "
        f"Model filter: {filters.get('models') or 'all'}; task filter: {filters.get('tasks') or 'all'}; "
        f"complete cases: {'on' if filters.get('common_tasks_only') else 'off'}"
        + (f" (dropped tasks: {', '.join(selection['dropped_tasks'])})" if selection.get("dropped_tasks") else "")
        + ". A group pools one model across experiments and conditions only while its runs share one treatment; "
        "a newer unusable attempt never replaces a usable run, and unusable or unfinished units are attrition, not "
        "zeros. Without complete cases, groups can cover different task sets, so group means and rankings then "
        "compare unequal task sets. "
        + (analyses.get("rule") or "")
        + " The coverage figure, selection_matrix.csv and selection_superseded.csv list every unit and every "
        "attempt left out.",
        "",
    ]


def final_assessment_methods(data):
    """Methods paragraph for the whole-cohort rates and the cost at list price; nothing when the report has none."""
    protocol = data.get("protocol") or {}
    basis = protocol.get("cost_basis") or {}
    outcomes = data.get("model_outcomes") or []
    if not basis and not any("deploy_rate_itt" in r for r in outcomes):
        return []
    preset = protocol.get("ranking_preset")
    criteria = [c.get("key") for c in (data.get("ranking") or {}).get("criteria") or []]
    return [
        "## Whole-cohort rates and cost at list price",
        "",
        (
            "Conditional rates (deploy_rate, pass_rate) are over apps with a measured evaluation, so known "
            "output, build and start failures are left out of them. The whole-cohort rates (deploy_rate_itt, "
            "pass_rate_itt, functional_passed_itt) are over every usable app with those failures counted as 0; "
            "deployment uses final run outcomes as the end-to-end deployment does, and runs whose outcome or grade "
            "is genuinely unknown are left out and reported, never counted as failures."
        ),
        "",
        (
            f"Cost at list price (list_cost_per_app): {basis.get('source', 'see protocol.json')}. "
            f"{basis.get('formula', '')} Per-model prices are in cost_basis_per_model.csv "
            f"({basis.get('unit', 'USD per million tokens')}). " + " ".join(basis.get("notes") or [])
        )
        if basis
        else "",
        "",
        f"Ranking criteria: {preset or 'as recorded'} ({', '.join(c for c in criteria if c) or 'none recorded'}).",
        "",
    ]


def run_report_workflow(report, output_dir="report-output"):
    """Write graphs, complete data tables, evidence checks and a reproducible ZIP bundle.

    Missing measurements stay missing. Inferential results are exported from the
    report, including its selected scope, corrections, seed and analysis plan.
    Descriptive figures average repeats before averaging apps. A skipped figure
    is recorded explicitly; an exception fails that step without hiding it.
    """
    data = report.get("report_data") or {}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "workflow_version": WORKFLOW_VERSION,
        "report_id": report.get("report_id"),
        "report_type": report.get("report_type"),
        "steps": [],
        "files": [],
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "matplotlib": mpl.__version__,
        },
        "notes": [
            "Missing measurements are not zero.",
            "Repeated runs are averaged within app before descriptive summaries.",
            "Plotted source size covers valid generated outputs; collected_loc columns also retain "
            "recorded trees from invalid outputs, including zero LOC. "
            "Issue and functional summaries cover measured apps. "
            "Populations can differ.",
            "Statistical tests and intervals come from the saved report, not rounded tables.",
            "Associations are observational. Ranking weights are researcher-chosen.",
            "Threshold sweeps and task-deletion ranges are descriptive sensitivity analyses, not hypothesis tests.",
        ],
    }

    def write_json(name, value):
        path = output / name
        path.write_text(
            json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str),
            encoding="utf-8",
        )
        manifest["files"].append(name)

    def table(name, frame):
        name = name + ".csv"
        frame.to_csv(output / name, index=False)
        manifest["files"].append(name)

    def figure(name, fig):
        if fig.get_suptitle():
            fig.tight_layout(rect=(0, 0, 1, 1 - 0.6 / fig.get_figheight()))
        else:
            fig.tight_layout()
        for ext in ("png", "svg"):
            path = f"{name}.{ext}"
            fig.savefig(output / path, dpi=180, bbox_inches="tight")
            manifest["files"].append(path)
        plt.close(fig)

    def step(name, action):
        before = len(manifest["files"])
        try:
            reason = action()
            manifest["steps"].append(
                {
                    "name": name,
                    "status": "skipped" if reason else "completed",
                    "reason": reason,
                    "files": manifest["files"][before:],
                },
            )
        except Exception as exc:  # noqa: BLE001 - preserve all completed artifacts and report the failing step
            plt.close("all")
            manifest["steps"].append(
                {
                    "name": name,
                    "status": "error",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "files": manifest["files"][before:],
                },
            )

    def heatmap(name, frame, title, label, vmin=None, vmax=None, cmap="Blues"):
        frame = frame.apply(pd.to_numeric, errors="coerce")
        if frame.empty or not frame.notna().any().any():
            return "No measured values in this scope."
        fig, ax = plt.subplots(figsize=(max(7, len(frame.columns) * 0.8), max(3, len(frame) * 0.5)))
        palette = plt.get_cmap(cmap).with_extremes(bad="#e5e7eb")
        image = ax.imshow(
            np.ma.masked_invalid(frame.to_numpy(float)),
            cmap=palette,
            aspect="auto",
            vmin=vmin,
            vmax=vmax,
        )
        ax.set_xticks(range(len(frame.columns)), frame.columns, rotation=45, ha="right")
        ax.set_yticks(range(len(frame)), frame.index)
        if frame.size <= 250:
            for i in range(len(frame)):
                for j in range(len(frame.columns)):
                    value = frame.iloc[i, j]
                    rgb = palette(image.norm(value))[:3] if pd.notna(value) else (1, 1, 1)
                    luminance = float(np.dot(rgb, [0.2126, 0.7152, 0.0722]))
                    ax.text(
                        j,
                        i,
                        f"{value:.2g}" if pd.notna(value) else "N/A",
                        ha="center",
                        va="center",
                        fontsize=8,
                        color="white" if luminance < 0.45 else "black",
                    )
        ax.set_title(title)
        fig.colorbar(image, ax=ax, label=label)
        figure(name, fig)
        table(name, frame.reset_index())
        return None

    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
    manifest["data_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    manifest["numeric_data_sha256"] = numeric_data_fingerprint(data)
    write_json("report.json", report)
    write_json("protocol.json", data.get("protocol") or {})
    apps = app_observations(data.get("per_app") or [])
    table("run_observations", _frame(data.get("per_app")))
    table("app_observations", apps)

    def export_source():
        # IPython caches the executed cell under its code filename; modules use their file.
        # This retains notebook edits instead of attaching a separate, possibly stale copy.
        source = "".join(linecache.getlines(run_report_workflow.__code__.co_filename))
        if not source:
            return (
                "Executed workflow source is unavailable in this Python session. Retain the notebook to reproduce it."
            )
        (output / "analysis_workflow.py").write_text(source, encoding="utf-8")
        manifest["workflow_source_sha256"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
        manifest["files"].append("analysis_workflow.py")
        return None

    step("workflow_source", export_source)

    def export_tables():
        for section in (
            "funnel",
            "issues",
            "code_metrics",
            "correlations",
            "ranking",
            "security_bounds",
            "runtime_security",
            "planned_comparisons",
            "exploratory_comparisons",
            "accounting",
            "validity",
        ):
            value = data.get(section)
            if value is not None:
                write_json(f"{section}.json", value)
                if isinstance(value, list):
                    table(section, pd.json_normalize(value))
                elif isinstance(value, dict):
                    for key, rows in value.items():
                        if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
                            table(f"{section}_{key}", pd.json_normalize(rows))
        for i, item in enumerate(report.get("tables") or [], 1):
            table(f"display_table_{i:02d}", pd.DataFrame(item.get("rows") or [], columns=item.get("columns")))
        for key in ("execution", "requirements", "cost_basis"):
            section = (data.get("protocol") or {}).get(key) or {}
            if section:
                write_json(f"{key}.json", section)
            for name, rows in section.items():
                if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
                    table(f"{key}_{name}", pd.json_normalize(rows))
            history = section.get("study_history") or {}
            if history.get("nonselected"):
                table("execution_study_history_nonselected", pd.json_normalize(history["nonselected"]))

    step("data_tables", export_tables)

    def sample_selection():
        selection = data.get("selection")
        if not isinstance(selection, dict) or not (selection.get("matrix") or selection.get("groups")):
            return "No cross-experiment selection in this report (one experiment or all apps)."
        write_json("selection.json", selection)
        long, wide, superseded = selection_tables(selection)
        table("selection_matrix", long)
        table("selection_superseded", superseded)
        for key, name in (("groups", "selection_groups"), ("studies", "selection_studies")):
            rows = [r for r in selection.get(key) or [] if isinstance(r, dict)]
            if rows:
                table(name, pd.json_normalize(rows))
        analyses = selection.get("analyses") or {}
        for key, name in (
            ("by_launch", "selection_analyses_by_launch"),
            # Analysis rounds by name: what tells rounds apart when they were launched without a launch id.
            ("by_label", "selection_analyses_by_label"),
            ("tools_mixed_labels", "selection_tools_mixed_labels"),
            ("tools_mixed_launches", "selection_tools_mixed_launches"),
        ):
            rows = [r for r in analyses.get(key) or [] if isinstance(r, dict)]
            if rows:
                table(name, pd.json_normalize(rows))
        blocking = [r for r in selection.get("dropped_by_group") or [] if isinstance(r, dict)]
        if blocking:
            table("selection_dropped_by_group", pd.json_normalize(blocking))
        if wide.empty:
            return "The selection has no group or task in scope; tables exported without a coverage figure."
        table("selection_coverage", wide.reset_index())
        codes = {code: i for i, (code, *_rest) in enumerate(SELECTION_CATEGORIES)}
        values = wide.apply(lambda column: column.map(codes)).to_numpy(float)
        palette = ListedColormap([colour for _code, _label, colour, _letter in SELECTION_CATEGORIES])
        dropped = set(selection.get("dropped_tasks") or [])
        fig, ax = plt.subplots(
            figsize=(max(8, 0.34 * len(wide.columns) + 4), max(3, 0.42 * len(wide) + 2.4)),
        )
        ax.imshow(values, cmap=palette, vmin=-0.5, vmax=len(SELECTION_CATEGORIES) - 0.5, aspect="auto")
        ax.set_xticks(
            range(len(wide.columns)),
            [f"{task} (dropped)" if task in dropped else task for task in wide.columns],
            rotation=60,
            ha="right",
            fontsize=7,
        )
        ax.set_yticks(range(len(wide)), wide.index, fontsize=8)
        ax.set_xticks(np.arange(-0.5, len(wide.columns)), minor=True)
        ax.set_yticks(np.arange(-0.5, len(wide)), minor=True)
        ax.grid(which="minor", color="white", linewidth=1)
        ax.tick_params(which="minor", length=0)
        units = {(r["group"], r["task"]): r for r in long.to_dict("records")}
        if wide.size <= 900:
            letters = {code: letter for code, _label, _colour, letter in SELECTION_CATEGORIES}
            for i, group in enumerate(wide.index):
                for j, task in enumerate(wide.columns):
                    code = wide.iloc[i, j]
                    colour = to_rgb(palette(codes[code]))
                    luminance = float(np.dot(colour, [0.2126, 0.7152, 0.0722]))
                    older = "*" if units.get((group, task), {}).get("superseded_run_ids") else ""
                    ax.text(
                        j,
                        i,
                        letters[code] + older,
                        ha="center",
                        va="center",
                        fontsize=6,
                        color="white" if luminance < 0.5 else "black",
                    )
        counts = long["category"].value_counts()
        handles = [
            Patch(facecolor=colour, edgecolor="#666666", label=f"{letter}  {label} ({int(counts.get(code, 0))})")
            for code, label, colour, letter in SELECTION_CATEGORIES
        ]
        ax.legend(
            handles=handles,
            fontsize=7,
            title="* older attempts superseded",
            title_fontsize=7,
            loc="upper left",
            bbox_to_anchor=(1.01, 1),
        )
        studies = len(selection.get("studies") or [])
        ax.set_title(
            f"Sample coverage: newest usable run per group and task ({studies} experiments pooled)"
            + ("\nComplete cases: dropped tasks are labelled" if dropped else "")
            + (
                f"\n{int(counts.get('excluded', 0))} selected run(s) left out (L): not in the analysed sample"
                if counts.get("excluded", 0)
                else ""
            ),
            fontsize=10,
        )
        figure("selection_coverage", fig)
        return None

    step("sample_selection", sample_selection)

    def funnel():
        rows = (data.get("funnel") or {}).get("by_model") or []
        if not rows:
            return "No group outcome rates were recorded."
        fig, ax = plt.subplots(figsize=(max(10, len(rows) * 0.9), 4.5))
        x = np.arange(len(rows))
        metrics = [("generation_rate", "Generated / usable outcomes")]
        if any(r.get("end_to_end_deploy_rate") is not None for r in rows):
            metrics.append(("end_to_end_deploy_rate", "Observed final deployment / usable outcomes"))
        if any(r.get("deploy_rate_itt") is not None for r in rows):
            metrics.append(("deploy_rate_itt", "Deployment, failures counted as 0 / usable outcomes"))
        metrics.extend(
            [("deploy_rate", "Conditional deployment / evaluated"), ("functional_rate", "All checks pass / graded")],
        )
        width = 0.8 / len(metrics)
        for j, (key, label) in enumerate(metrics):
            positions = x + (j - (len(metrics) - 1) / 2) * width
            cells = [r.get(key) or {} for r in rows]
            values = [c.get("rate") if c.get("rate") is not None else np.nan for c in cells]
            ax.bar(positions, values, width=width, label=label)
            for pos, value, cell in zip(positions, values, cells, strict=True):
                ci = cell.get("ci95")
                if ci and np.isfinite(value) and all(v is not None for v in ci):
                    ax.vlines(pos, ci[0], ci[1], color="black", linewidth=1)
                    ax.plot([pos, pos], ci, "_", color="black")
        ax.set_xticks(x, [r["model"] for r in rows], rotation=35, ha="right")
        ax.set_ylim(0, 1.04)
        ax.set_ylabel("App-weighted rate; recorded 95% CI where available")
        ax.set_title("Generation, deployment and functional outcomes")
        ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1))
        figure("outcome_funnel", fig)
        return None

    step("outcome_funnel", funnel)

    def functional_bounds():
        rows = [
            {"group": row["model"], **row["end_to_end_functional_bounds"]}
            for row in (data.get("funnel") or {}).get("by_model") or []
            if row.get("end_to_end_functional_bounds")
        ]
        if not rows:
            return "No full-cohort functional measurement bounds; regenerate the report to collect them."
        table("full_cohort_functional_bounds", _frame(rows))
        measured = [r for r in rows if r.get("lower") is not None and r.get("upper") is not None]
        if not measured:
            return "No usable outcomes in the functional bound population."
        fig, ax = plt.subplots(figsize=(9, max(3, len(measured) * 0.4)))
        for i, row in enumerate(measured):
            ax.hlines(i, row["lower"], row["upper"], linewidth=4, color="#93c5fd")
            ax.plot([row["lower"], row["upper"]], [i, i], "|", color="#1d4ed8")
        ax.set_yticks(
            range(len(measured)),
            [f"{r['group']} (apps={r['apps']}, unknown={r['unknown_apps']})" for r in measured],
        )
        ax.set_xlim(0, 1.02)
        ax.set_xlabel("All-checks-pass rate over usable outcomes; unknown grades range from fail to pass")
        ax.set_title("Full-cohort functional bounds; not confidence intervals")
        figure("full_cohort_functional_bounds", fig)
        return None

    step("full_cohort_functional_bounds", functional_bounds)

    def scorecard():
        outcomes = _frame(data.get("model_outcomes"))
        if outcomes.empty or "model" not in outcomes:
            return "No group outcomes were recorded."
        # Next to the whole-cohort rates and the cost at list price, the conditional rates and the billed cost
        # say what they are; reports without them keep their labels.
        final = any(k in outcomes for k in ("deploy_rate_itt", "pass_rate_itt", "list_cost_per_app"))
        metrics = {
            "pass_rate": ("Functional pass rate | measured evaluation" if final else "Functional pass rate", False),
            "pass_rate_itt": ("Pass rate, failures counted as 0", False),
            "deploy_rate": ("Deployment | measured evaluation", False),
            "deploy_rate_itt": ("Deployment, failures counted as 0", False),
            "issues_per_kloc": ("Inverse issue density", True),
            "cost_per_app": ("Inverse billed cost" if final else "Inverse cost", True),
            "list_cost_per_app": ("Inverse cost at list price", True),
        }
        normalized = pd.DataFrame(index=outcomes["model"])
        outcomes = outcomes.set_index("model")
        for key, (label, inverse) in metrics.items():
            values = pd.to_numeric(outcomes.get(key, pd.Series(dtype=float)), errors="coerce")
            if not values.notna().any():
                continue
            span = values.max() - values.min()
            score = (values - values.min()) / span if span else values.where(values.isna(), 0.5)
            normalized[label] = 1 - score if inverse else score
        if not apps.empty and apps["loc"].notna().any():
            sizes = apps.groupby("group")["loc"].mean()
            span = sizes.max() - sizes.min()
            normalized["LOC footprint (descriptive)"] = (
                (sizes - sizes.min()) / span if span else sizes.where(sizes.isna(), 0.5)
            )
        if normalized.empty or not len(normalized.columns):
            return "No measured scorecard criteria."
        ax = normalized.plot.bar(figsize=(max(8, len(normalized) * 0.8), 5), width=0.8)
        for column_index, column in enumerate(normalized.columns):
            for row_index, value in enumerate(normalized[column]):
                if pd.isna(value):
                    position = row_index + (column_index - (len(normalized.columns) - 1) / 2) * 0.8 / len(
                        normalized.columns,
                    )
                    ax.text(position, 0, "N/A", rotation=90, va="bottom", ha="center", fontsize=6, color="grey")
        ax.set_ylabel("Min-max normalization (constant criterion = 0.5)")
        ax.set_ylim(0, 1.05)
        ax.set_xlabel("")
        ax.set_title("Descriptive model scorecard; no composite score")
        ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1))
        figure("normalized_scorecard", ax.figure)
        table("normalized_scorecard", normalized.reset_index())
        return None

    step("normalized_scorecard", scorecard)

    def tool_matrix():
        rows = [
            r
            for r in (data.get("issues") or {}).get("by_group_tool") or []
            if r.get("analysis_mode") == "static"
            and r.get("category") != "performance"
            and not r.get("is_dependency_scanner")
            and not r.get("is_ai")
        ]
        if not rows:
            return "No per-group static-tool measurements; regenerate the report to collect them."
        frame = _frame(rows)
        table("static_tool_coverage", frame)
        return heatmap(
            "static_tool_findings",
            frame.pivot(index="group", columns="tool", values="raw_findings_per_app"),  # noqa: PD010 - duplicate keys must fail
            "Static tool findings per measured app (repeats averaged)",
            "Raw findings / app",
        )

    step("static_tool_findings", tool_matrix)

    def size_density():
        if apps.empty or not apps["loc"].notna().any():
            return "No measured source size."
        frame = apps.groupby("group", sort=True)["loc"].mean().to_frame().sort_values("loc")
        outcomes = _frame(data.get("model_outcomes"))
        if not outcomes.empty and "issues_per_kloc" in outcomes:
            frame["issues_per_kloc"] = outcomes.set_index("model")["issues_per_kloc"]
        else:
            frame["issues_per_kloc"] = np.nan
        fig, ax = plt.subplots(figsize=(max(8, len(frame) * 0.8), 5))
        x = np.arange(len(frame))
        ax.bar(x, frame["loc"], color="#4c78a8", label="LOC / app")
        ax.set_ylabel("Mean source LOC / app with valid generated output", color="#4c78a8")
        other = ax.twinx()
        other.plot(x, frame["issues_per_kloc"], marker="o", color="#b45309")
        other.set_ylabel("Deduplicated model-code issues / kLOC", color="#b45309")
        ax.set_xticks(x, frame.index, rotation=35, ha="right")
        ax.set_title("Source size and issue density (different axes)")
        figure("size_and_density", fig)
        table("size_and_density", frame.reset_index())
        return None

    step("size_and_density", size_density)

    def severity():
        if apps.empty:
            return "No app observations."
        columns = [f"unique_normalized_{s}" for s in SEVERITIES]
        frame = apps.groupby("group", sort=True)[columns].mean()
        frame = frame.dropna(how="all")
        if frame.empty:
            return "No measured normalized issue severities."
        frame = frame.loc[frame.sum(axis=1, min_count=1).sort_values(ascending=False).index]
        table("severity_per_app", frame.reset_index())
        # A partially measured stack would visually imply zero in its missing segment.
        complete = frame.dropna()
        if complete.empty:
            return "Severity buckets are incomplete; measured values are exported without a misleading stack."
        complete = complete.rename(columns=dict(zip(columns, SEVERITIES, strict=True)))
        ax = complete.plot.bar(
            stacked=True,
            figsize=(max(8, len(complete) * 0.8), 5),
            color=["#7f1d1d", "#dc2626", "#d97706", "#60a5fa", "#94a3b8"],
        )
        ax.set_ylabel("Deduplicated issues / app (all issue kinds)")
        ax.set_xlabel("")
        ax.set_title("Normalized severity (platform heuristic); complete buckets only")
        figure("severity_per_app", ax.figure)
        return None

    step("severity_per_app", severity)

    def issue_populations():
        if apps.empty:
            return "No measured app observations."
        columns = {
            "code_unique_model": "Model-code issues",
            "code_unique_tests": "Test-code issues",
            "dependency_added": "Model-added dependency advisories",
            "dependency_inherited": "Inherited dependency advisories",
            "runtime_unique": "Runtime issue classes",
        }
        frame = apps.groupby("group")[list(columns)].mean()
        if not frame.notna().any().any():
            return "No measured issue-population counts."
        table("issue_populations_per_app", frame.reset_index())
        fig, ax = plt.subplots(figsize=(max(9, len(frame) * 0.8), 5))
        x = np.arange(len(frame))
        width = 0.8 / len(columns)
        for i, (key, label) in enumerate(columns.items()):
            positions = x + (i - (len(columns) - 1) / 2) * width
            ax.bar(positions, frame[key], width=width, label=label)
            for pos, value in zip(positions, frame[key], strict=True):
                if pd.isna(value):
                    ax.text(pos, 0, "N/A", rotation=90, va="bottom", ha="center", fontsize=6, color="grey")
                elif value == 0:
                    ax.text(pos, 0, "0", va="bottom", ha="center", fontsize=6)
        ax.set_xticks(x, frame.index, rotation=35, ha="right")
        ax.set_ylabel("Mean recorded issues/advisories/classes per measured app")
        ax.set_title("Issue populations under recorded deduplication rules; not confirmed defects")
        ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.02, 1))
        figure("issue_populations_per_app", fig)
        return None

    step("issue_populations_per_app", issue_populations)

    def functional():
        if apps.empty or not apps["pass_rate"].notna().any():
            return "No measured functional pass rates."
        frame = apps.groupby("group")["pass_rate"].agg(["mean", "std", "count"]).dropna(subset=["mean"])
        frame = frame.sort_values("mean", ascending=False)
        fig, ax = plt.subplots(figsize=(max(8, len(frame) * 0.8), 5))
        x = np.arange(len(frame))
        ax.bar(x, frame["mean"], color="#4c78a8")
        for pos, row in enumerate(frame.to_dict("records")):
            if pd.notna(row["std"]):
                ax.errorbar(pos, row["mean"], yerr=row["std"], color="black", capsize=3)
        ax.set_xticks(
            x,
            [f"{g} (n={n})" for g, n in zip(frame.index, frame["count"], strict=True)],
            rotation=35,
            ha="right",
        )
        ax.set_ylabel("Mean functional pass rate ± sample SD across apps")
        ax.set_title("Objective functional checks; SD describes spread, not confidence")
        figure("functional_pass_rate", fig)
        table("functional_pass_rate", frame.reset_index())
        return None

    step("functional_pass_rate", functional)

    def thresholds():
        frame = threshold_sensitivity(data.get("per_app") or [])
        if frame.empty:
            return "No measured functional pass rates."
        table("functional_threshold_sensitivity", frame)
        matrix = frame.pivot(index="threshold", columns="group", values="rate")  # noqa: PD010 - duplicate cells must fail
        counts = frame.groupby("group")["apps"].first()
        labels = {group: f"{group} (n={counts[group]})" for group in matrix.columns}
        ax = matrix.rename(columns=labels).plot(figsize=(9, 5), marker=".", colormap="tab20")
        ax.set_xlabel("Minimum fraction of objective checks passed")
        ax.set_ylabel("App-weighted share of measured runs meeting threshold")
        ax.set_ylim(0, 1.03)
        ax.set_title("Threshold sensitivity; every cutoff shown is descriptive")
        ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.02, 1))
        figure("functional_threshold_sensitivity", ax.figure)
        return None

    step("functional_threshold_sensitivity", thresholds)

    def task_deletions():
        frame = task_sensitivity(apps)
        if frame.empty:
            return "No measured app outcomes for task sensitivity."
        table("task_deletion_sensitivity", frame)
        measured = frame.dropna(subset=["mean_without_task"])
        if measured.empty:
            return "Each group has fewer than two measured tasks; task deletion cannot be estimated."
        for metric, rows in measured.groupby("metric"):
            summary = rows.groupby("group").agg(
                low=("mean_without_task", "min"),
                high=("mean_without_task", "max"),
                mean=("mean_full", "first"),
                tasks=("n_tasks", "first"),
            )
            fig, ax = plt.subplots(figsize=(9, max(3, len(summary) * 0.4)))
            for i, row in enumerate(summary.to_dict("records")):
                ax.hlines(i, row["low"], row["high"], color="#4c78a8")
                ax.plot(row["mean"], i, "o", color="black")
            ax.set_yticks(
                range(len(summary)),
                [f"{name} (tasks={n})" for name, n in zip(summary.index, summary["tasks"], strict=True)],
            )
            ax.set_xlabel(f"{metric}: full mean (dot), leave-one-task-out range (line; not a CI)")
            ax.set_title("Sensitivity to each group's own observed task mix")
            figure(f"task_sensitivity_{metric}", fig)
        return None

    step("task_deletion_sensitivity", task_deletions)

    def app_scatter():
        if apps.empty:
            return "No measured app observations."
        pairs = [("loc", "unique_issues"), ("avg_complexity", "issues_per_kloc"), ("loc", "pass_rate")]
        measured = [(x, y) for x, y in pairs if not apps.dropna(subset=[x, y]).empty]
        if not measured:
            return "No paired size, complexity and outcome measurements."
        fig, axes = plt.subplots(1, len(measured), figsize=(6 * len(measured), 5), squeeze=False)
        for ax, (x, y) in zip(axes[0], measured, strict=True):
            subset = apps.dropna(subset=[x, y])
            for group, values in subset.groupby("group"):
                ax.scatter(values[x], values[y], s=18, alpha=0.65, label=group)
            ax.set_xlabel(x)
            ax.set_ylabel(y)
            ax.set_title(f"{len(subset)} app units; repeats averaged")
        axes[0][-1].legend(fontsize=6, loc="upper left", bbox_to_anchor=(1.02, 1))
        fig.suptitle("Size, complexity and measured outcomes; apps share model and task clusters")
        figure("app_size_complexity_outcomes", fig)
        return None

    step("app_size_complexity_outcomes", app_scatter)

    for column, label, lower, upper in (
        ("observed_deployment", "Observed final deployment / usable apps", 0, 1),
        ("loc", "Source LOC (valid generated output)", None, None),
        ("avg_complexity", "Mean Python cyclomatic complexity", None, None),
        ("unique_issues", "Deduplicated issues / app", None, None),
    ):

        def task_matrix(column=column, label=label, lower=lower, upper=upper):
            if apps.empty:
                return "No app observations."
            name = "task_deployed" if column == "observed_deployment" else f"task_{column}"
            title = f"{label} by group and task"
            if column == "observed_deployment":
                coverage = (
                    apps.groupby(["group", "task"])
                    .agg(
                        apps=("observed_deployment", "size"),
                        apps_known=("observed_deployment", "count"),
                        runs=("repeats", "sum"),
                        unknown_runs=("deployment_unknown_runs", "sum"),
                        rate=("observed_deployment", "mean"),
                    )
                    .reset_index()
                )
                coverage.loc[coverage["apps_known"] != coverage["apps"], "rate"] = np.nan
                table("task_deployed_coverage", coverage)
                frame = coverage.pivot_table(index="group", columns="task", values="rate", dropna=False)
                title += "\nN/A = incomplete outcome evidence or no sampled apps; repeats averaged within apps"
            else:
                frame = apps.pivot_table(index="group", columns="task", values=column, aggfunc="mean", dropna=False)
            return heatmap(name, frame, title, label, lower, upper)

        step("task_deployed" if column == "observed_deployment" else f"task_{column}", task_matrix)

    def overlap():
        rows = (data.get("issues") or {}).get("tool_pairs") or []
        if not rows:
            return "No tool pairs with comparable issue classes."
        table("tool_overlap_pairs", pd.json_normalize(rows))
        names = sorted({r[k] for r in rows for k in ("tool_a", "tool_b")})
        matrix = pd.DataFrame(np.nan, index=names, columns=names)
        matrix.index.name = "tool"
        for row in rows:
            matrix.loc[row["tool_a"], row["tool_b"]] = row.get("agreement")
            matrix.loc[row["tool_b"], row["tool_a"]] = row.get("agreement")
        return heatmap(
            "tool_overlap",
            matrix,
            "Co-detection on comparable classes; not accuracy",
            "Jaccard overlap",
            0,
            1,
        )

    step("tool_overlap", overlap)

    def correlations():
        tests = _frame((data.get("correlations") or {}).get("tests"))
        if tests.empty:
            return "No recorded correlation tests."
        table("correlation_tests", pd.json_normalize(tests.to_dict("records")))
        return heatmap(
            "correlations",
            tests.pivot(index="predictor", columns="outcome", values="rho"),  # noqa: PD010 - duplicate tests must fail
            "Exploratory Spearman correlations (model-level n)",
            "Spearman rho; CIs and adjusted p in CSV",
            -1,
            1,
            "RdBu_r",
        )

    step("correlations", correlations)

    def parameter_scatter():
        observations = model_observations(data)
        tests = (data.get("correlations") or {}).get("tests") or []
        if observations.empty:
            return "No recorded model parameters matched to measured outcomes."
        observations.insert(0, "point_id", np.arange(1, len(observations) + 1))
        table("model_parameter_observations", observations)
        declared_predictors = list(dict.fromkeys(t["predictor"] for t in tests if t["predictor"] in observations))
        predictors = [key for key in declared_predictors if observations[key].notna().any()]
        unavailable_predictors = [key for key in declared_predictors if key not in predictors]
        outcomes = list(dict.fromkeys(t["outcome"] for t in tests if t["outcome"] in observations))
        if not predictors or not outcomes:
            return "No recorded predictor/outcome tests are available."
        lookup = {(t["predictor"], t["outcome"]): t for t in tests}
        fig, axes = plt.subplots(
            len(predictors),
            len(outcomes),
            figsize=(5 * len(outcomes), 3.5 * len(predictors)),
            squeeze=False,
        )
        for i, predictor in enumerate(predictors):
            for j, outcome in enumerate(outcomes):
                ax = axes[i, j]
                pairs = observations.dropna(subset=[predictor, outcome])
                ax.scatter(pairs[predictor], pairs[outcome], s=20)
                for (x, y), coincident in pairs.groupby([predictor, outcome]):
                    ax.annotate(
                        ",".join(str(value) for value in coincident["point_id"]),
                        (x, y),
                        xytext=(3, 3),
                        textcoords="offset points",
                        fontsize=7,
                    )
                test = lookup.get((predictor, outcome)) or {}
                ax.set_xlabel(predictor)
                ax.set_ylabel(outcome)
                rho, pvalue = test.get("rho"), test.get("p_holm")
                ax.set_title(
                    f"n={len(pairs)}; rho={rho if rho is not None else 'N/A'}; "
                    f"Holm p={pvalue if pvalue is not None else 'N/A'}",
                    fontsize=8,
                )
        title = "Exploratory associations, one point per model; identifiers in model_parameter_observations.csv"
        if unavailable_predictors:
            title += "\nEntirely unmeasured predictors omitted: " + ", ".join(unavailable_predictors)
        fig.suptitle(title, fontsize=10)
        figure("model_parameter_scatter", fig)
        predictor_values = observations[predictors].apply(pd.to_numeric, errors="coerce")
        # Pairwise descriptive rank correlations show collinearity without extra significance claims.
        rank_corr = predictor_values.corr(method="spearman", min_periods=3)
        step(
            "predictor_collinearity",
            lambda: heatmap(
                "predictor_collinearity",
                rank_corr,
                "Predictor collinearity (pairwise complete models)",
                "Descriptive rank correlation; at least three models per pair",
                -1,
                1,
                "RdBu_r",
            ),
        )
        return None

    step("model_parameter_diagnostics", parameter_scatter)

    def rankings():
        ranking = data.get("ranking") or {}
        frame = _frame(ranking.get("ranking"))
        if frame.empty:
            return "No models with complete ranking criteria."
        frame = frame.set_index("model")
        ranks = _numeric(frame, ["topsis_rank", "wsm_rank"])
        ranks["equal_weight_rank"] = pd.Series((ranking.get("equal_weights") or {}).get("ranks") or {})
        ax = ranks.plot.bar(figsize=(max(8, len(ranks) * 0.8), 5))
        ax.set_xlabel("")
        ax.set_ylabel("Rank (smaller is better; ties retain shared rank)")
        ax.set_title("Rankings under scoring methods and equal weights")
        figure("ranking_comparison", ax.figure)
        table("ranking_comparison", ranks.reset_index())
        for key in ("monte_carlo_near_weights", "monte_carlo_any_weights", "data_uncertainty"):
            simulation = ranking.get(key) or {}
            rows = simulation.get("ranks") or []
            if not rows:
                manifest["steps"].append(
                    {"name": key, "status": "skipped", "reason": "No recorded sensitivity draws.", "files": []},
                )
                continue
            fig, ax = plt.subplots(figsize=(8, max(3, len(rows) * 0.45)))
            for i, row in enumerate(rows):
                ci = row.get("rank_ci95")
                if ci:
                    ax.hlines(i, ci[0], ci[1], color="#4c78a8")
                ax.plot(row["median_rank"], i, "o", color="#4c78a8")
            ax.set_yticks(range(len(rows)), [r["model"] for r in rows])
            ax.set_xlabel("Median rank and 95% range across retained draws")
            title = key.replace("_", " ").capitalize()
            if simulation.get("draws") is not None:
                title += f"\n{simulation['draws']} retained draws"
                if simulation.get("attempted_draws") is not None:
                    title += f" / {simulation['attempted_draws']} attempted; all alternatives defined"
            ax.set_title(title)
            figure(key, fig)
            table(key, _frame(rows))
        return None

    step("rankings_and_sensitivity", rankings)

    def weight_changes():
        rows = (data.get("ranking") or {}).get("one_at_a_time") or []
        if not rows:
            return "No recorded one-at-a-time weight perturbations."
        frame = _frame([{**r, "reversal_count": len(r.get("rank_reversals") or [])} for r in rows])
        table("weight_perturbations", frame)
        fig, ax = plt.subplots(figsize=(9, max(3, len(frame) * 0.35)))
        ax.barh(np.arange(len(frame)), frame["reversal_count"])
        ax.set_yticks(np.arange(len(frame)), [f"{r['criterion']} x {r['factor']}" for r in rows])
        ax.set_xlabel("Recorded strict rank reversals vs chosen weights")
        ax.set_title("One weight changed at a time; all weights renormalized")
        figure("weight_perturbations", fig)
        return None

    step("weight_perturbations", weight_changes)

    def comparisons():
        declared = bool(data.get("planned_comparisons"))
        plan = data.get("planned_comparisons") or data.get("exploratory_comparisons") or {}
        prefix = "planned" if declared else "exploratory"
        rows = [
            {"metric": f["metric"], "status": f.get("status"), **r}
            for f in plan.get("families") or []
            for r in f.get("comparisons") or []
        ]
        if not rows:
            return "No paired comparisons in this scope; select a completed study or a declared baseline plan."
        table(f"{prefix}_comparison_tests", _frame(rows))
        measured = [r for r in rows if r.get("mean_delta") is not None]
        if not measured:
            return "No paired effect estimates; see exported missing-data and skipped-test reasons."
        for metric in dict.fromkeys(r["metric"] for r in measured):
            items = [r for r in measured if r["metric"] == metric]
            fig, ax = plt.subplots(figsize=(12, max(4, len(items) * 0.7)))
            for i, row in enumerate(items):
                ax.plot(row["mean_delta"], i, "o", color="#4c78a8")
                ci = row.get("mean_delta_ci95")
                if ci and all(value is not None for value in ci):
                    ax.hlines(i, ci[0], ci[1], color="#4c78a8")
            ax.axvline(0, color="grey", linewidth=1)
            ax.set_yticks(
                range(len(items)),
                [
                    fill(str(r["condition"]), width=52)
                    + "\n"
                    + f"n={r.get('n_pairs')}; adjusted p="
                    + (f"{r['p_adjusted']:.3g}" if r.get("p_adjusted") is not None else r.get("skipped") or "N/A")
                    for r in items
                ],
                fontsize=8,
            )
            orientation = f"vs {plan.get('baseline_condition', 'baseline')}" if declared else "condition B minus A"
            metric_label = {
                "deploy_rate": "conditional deployment / evaluated apps",
                "pass_rate": "functional pass fraction / graded apps",
                "functional_passed": "all-checks-pass rate / graded apps",
                "end_to_end_deploy_rate": "observed final deployment / usable apps",
                "deploy_rate_itt": "deployment, failures counted as 0 / usable apps",
                "pass_rate_itt": "functional pass fraction, failures counted as 0",
                "functional_passed_itt": "all-checks-pass rate, failures counted as 0 / usable apps",
                "cost_per_app": "billed cost per app (USD)",
                "list_cost_per_app": "cost per app at list price (USD)",
            }.get(metric, metric)
            ax.set_xlabel(f"Mean paired change in {metric_label}: {orientation}")
            kind = "Declared" if declared else "Exploratory"
            correction = plan.get("correction") or "recorded correction"
            test = "recorded primary test" if declared else "Wilcoxon"
            ax.set_title(
                f"{kind} paired effects; {test}, {correction}-adjusted p\n"
                "App-bootstrap 95% intervals; not multiplicity-adjusted",
            )
            figure(
                f"{prefix}_effect_{len([s for s in manifest['files'] if s.startswith(f'{prefix}_effect_')]) // 2 + 1}",
                fig,
            )
        return None

    step("paired_comparisons", comparisons)

    def lock_strata():
        rows = (((data.get("protocol") or {}).get("execution") or {}).get("lock_source_strata")) or []
        if not rows:
            return "No recorded lock-source deployment strata; regenerate the report to collect provenance."
        frame = _frame(rows)
        return heatmap(
            "deployment_by_lock_source",
            frame.pivot(index="group", columns="lock_source", values="deployed_rate"),  # noqa: PD010 - distinct strata
            "Conditional deployment by lock source (observational, not repair lift)",
            "Deployment / evaluated apps; denominators in execution_lock_source_strata.csv",
            0,
            1,
        )

    step("deployment_by_lock_source", lock_strata)

    def requirement_alignment():
        alignment = data.get("requirement_alignment") or {}
        rows = alignment.get("by_group") or []
        if not rows:
            return "No recorded prompt-tagged check sensitivity; regenerate the report to expose check attribution."
        write_json("requirement_alignment.json", alignment)
        frame = _frame(rows)
        table("requirement_alignment_by_group", frame)
        per_run = _frame(data.get("per_app") or [])
        columns = [
            c
            for c in per_run
            if c
            in {
                "run_id",
                "model",
                "group",
                "task",
                "prose_variant",
                "evaluated",
                "pass_rate",
                "functional_passed",
                "statedness_unknown_checks",
            }
            or c.startswith(("stated_", "unstated_", "all_stated_", "all_unstated_"))
        ]
        table("requirement_alignment_per_run", per_run[columns])
        for metric, label in (
            ("mean_pass_rate", "Mean check pass fraction"),
            ("all_pass_rate", "Fraction passing every check in the subset"),
        ):
            values = frame.set_index("group")[[f"{scope}_{metric}" for scope in ("strict", "stated", "unstated")]]
            values = values.apply(pd.to_numeric, errors="coerce")
            if values.isna().all().all():
                return "No measured check subsets; exported denominators retain the missing observations."
            values.columns = ["All counted checks", "Tagged stated", "Tagged unstated"]
            ax = values.plot.barh(figsize=(10, max(4, len(frame) * 0.65)), color=["#4c78a8", "#59a14f", "#b279a2"])
            ax.set(
                xlim=(0, 1.02),
                xlabel=f"{label}; measured app units only",
                ylabel="",
                title="Sensitivity to prompt exposure tags",
            )
            ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, fontsize=8)
            figure(f"requirement_alignment_{metric}", ax.figure)
        return None

    step("requirement_alignment", requirement_alignment)

    def matched_locks():
        evidence = (data.get("supplemental_evidence") or {}).get("lock_materialization") or {}
        if not evidence:
            return "No matched lock experiment was included in this snapshot."
        if evidence.get("complete") is not True:
            message = "Matched lock experiment is incomplete."
            raise ValueError(message)
        write_json("lock_materialization_evidence.json", evidence)
        observations = pd.json_normalize(evidence.get("observations") or [])
        table("lock_materialization_observations", observations)
        table("lock_materialization_pairs", pd.json_normalize(evidence.get("pairs") or []))
        transitions = []
        for metric in ("build", "deployment", "all_checks"):
            transitions.append({"outcome": metric, **(evidence.get("summary") or {}).get(metric, {})})
        frame = _frame(transitions)
        table("lock_materialization_transitions", frame)
        counts = _numeric(frame, ["pass_to_pass", "fail_to_fail", "fail_to_pass", "pass_to_fail", "pairs_missing"])
        counts.index = frame["outcome"].str.replace("_", " ")
        counts.columns = ["Both succeed", "Both fail", "Improved", "Worsened", "Unknown"]
        ax = counts.plot.barh(
            stacked=True,
            figsize=(9, 3.7),
            color=["#4c78a8", "#b9bec5", "#59a14f", "#e15759", "#edc948"],
        )
        ax.set_xlabel("Matched source artifacts; original → recorded platform locks")
        ax.set_ylabel("")
        ax.set_title("Dependency-lock replay: observed paired outcomes")
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=3, fontsize=8)
        figure("lock_materialization_transitions", ax.figure)
        measured = observations.loc[observations["functional_measured"].eq(other=True)]
        rates = (
            measured.pivot(index="run_id", columns="arm", values="functional_pass_rate")  # noqa: PD010 - reject duplicate arms
            .reindex(columns=["collected", "materialized"])
            .dropna()
        )
        table("lock_materialization_matched_rates", rates.reset_index())
        if not rates.empty:
            fig, ax = plt.subplots(figsize=(5.8, 5.3))
            grouped = rates.groupby(["collected", "materialized"]).size().reset_index(name="pairs")
            ax.scatter(grouped["collected"], grouped["materialized"], s=45 + 20 * grouped["pairs"], alpha=0.75)
            for row in grouped.itertuples():
                ax.annotate(
                    str(row.pairs),
                    (row.collected, row.materialized),
                    xytext=(5, 5),
                    textcoords="offset points",
                    fontsize=8,
                )
            ax.plot([0, 1], [0, 1], "--", color="#888888", linewidth=1)
            ax.set(
                xlim=(-0.05, 1.05),
                ylim=(-0.05, 1.05),
                xlabel="Original locks: measured pass fraction",
                ylabel="Recorded platform locks: measured pass fraction",
                title="Matched functional outcomes (both arms measured)",
            )
            figure("lock_materialization_matched_rates", fig)
        return None

    step("matched_lock_experiment", matched_locks)

    def feature_lifecycle():
        evidence = (data.get("supplemental_evidence") or {}).get("feature_lifecycle") or {}
        if not evidence:
            return "No supplemental stateful feature experiment was included in this snapshot."
        if evidence.get("complete") is not True:
            message = "Stateful feature experiment is incomplete."
            raise ValueError(message)
        write_json("feature_lifecycle_evidence.json", evidence)
        table("feature_lifecycle_observations", pd.json_normalize(evidence.get("observations") or []))
        table("feature_lifecycle_controls", pd.json_normalize(evidence.get("controls") or []))
        records = [
            {"run_id": row["run_id"], "task": row.get("task"), "group": row.get("group"), **check}
            for row in evidence.get("observations", [])
            for check in row.get("checks", [])
        ]
        checks = _frame(records)
        table("feature_lifecycle_checks", checks)
        if checks.empty:
            return "The selected apps produced no runnable supplemental checks; inspect build/start observations."
        counts = pd.crosstab(checks["id"], checks["status"]).reindex(
            columns=["pass", "fail", "blocked", "not_applicable"],
            fill_value=0,
        )
        table("feature_lifecycle_status_counts", counts.reset_index())
        ax = counts.plot.barh(
            stacked=True,
            figsize=(10, max(4, len(counts) * 0.36)),
            color=["#59a14f", "#e15759", "#b9bec5", "#edc948"],
        )
        ax.set_xlabel("Recorded checks; blocked means unobserved")
        ax.set_ylabel("")
        selected = len(evidence.get("observations") or [])
        runnable = sum(bool(row.get("checks")) for row in evidence.get("observations") or [])
        ax.set_title(
            f"Supplemental stateful API checks: {runnable}/{selected} apps measured\n"
            f"{selected - runnable} unavailable; see observation table for reasons",
        )
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=4, fontsize=8)
        figure("feature_lifecycle_status_counts", ax.figure)
        return None

    step("supplemental_feature_checks", feature_lifecycle)

    def runtime_bounds():
        rows = (data.get("runtime_security") or {}).get("by_probe") or []
        measured = [r for r in rows if r.get("apps_measured") and r.get("apps_exploited") is not None]
        if not measured:
            return "No successfully measured runtime security probes in this report."
        intervals = any(r.get("ci95") and all(v is not None for v in r["ci95"]) for r in measured)
        bounds = any(r.get("zero_rate_upper95") is not None for r in measured)
        fig, ax = plt.subplots(figsize=(9, max(3, len(measured) * 0.4)))
        for i, row in enumerate(measured):
            rate = row["apps_exploited"] / row["apps_measured"]
            ax.plot(rate, i, "o", color="#b45309")
            ci = row.get("ci95")
            if ci and all(v is not None for v in ci):
                ax.hlines(i, ci[0], ci[1], color="#b45309")
            upper = row.get("zero_rate_upper95")
            if upper is not None:
                ax.plot(upper, i, ">", color="#4c78a8")
        ax.set_yticks(
            range(len(measured)),
            [f"{r.get('task', '')} {r['probe_id']} (n={r['apps_measured']})".strip() for r in measured],
        )
        ax.set_xlim(0, 1.02)
        label = "Observed exploit rate"
        if intervals:
            label += " and recorded 95% CI"
        if bounds:
            label += "; blue > is the recorded one-sided zero-count upper bound"
        ax.set_xlabel(label)
        title = "Runtime probes: measured app population only"
        if not intervals and not bounds:
            title += "\nDescriptive rates; no inferential interval for the selected task/endpoint population"
        ax.set_title(title)
        figure("runtime_security_bounds", fig)
        return None

    step("runtime_security_bounds", runtime_bounds)

    def methods():
        protocol = data.get("protocol") or {}
        execution = protocol.get("execution") or {}
        requirements = protocol.get("requirements") or {}
        study = protocol.get("study") or {}
        confirmatory = protocol.get("confirmatory") or {}
        lines = [
            "# Analysis methods and interpretation",
            "",
            f"Report: {report.get('report_id', 'unidentified')}. Workflow version: {WORKFLOW_VERSION}.",
            f"Serialized data fingerprint (SHA-256): `{manifest['data_sha256']}`.",
            f"Numeric-value fingerprint (SHA-256): `{manifest['numeric_data_sha256']}`.",
            "The serialized fingerprint preserves Python JSON numeric types. Browser JSON serialization can "
            "change 1.0 to 1 without changing a measurement; the numeric-value fingerprint normalizes integral "
            "floats to integers for cross-runtime comparison. CSV numeric formatting can likewise differ "
            "without changing values. Neither fingerprint is a statistical validation.",
            "",
            "## Scope and denominators",
            "",
            f"The saved sample contains {len(data.get('per_app') or [])} run rows and {len(apps)} app units. "
            "An app is identified by condition, model, run kind and pinned unit "
            "(or run identifier when no unit exists). "
            "Repeats are averaged within app before group means; extra repeats do not increase an app's weight.",
            (
                "This sample has one run per app: variation across sampled task/app units can be described, "
                "but within-model stochastic repeat variability cannot be estimated. "
                "App-bootstrap intervals do not supply missing repeated generations."
                if not apps.empty and apps["repeats"].max() == 1
                else "The exported app table gives observed repeat counts; missing repeats are not synthesized."
            ),
            "",
            "Generation means a usable generated artifact. Deployment requires a measured successful build/start. "
            "Functional pass rate is the fraction of measured objective checks passed; "
            "the all-checks-pass label means every measured suite check passed, "
            "not independently established full requirements correctness. "
            "Unmeasured evaluations, tool failures and missing source trees remain missing rather than zero. "
            "Generation rates exclude infrastructure attrition. "
            "Conditional deployment and functional rates use their own measured populations. "
            "The task deployment matrix instead uses final outcomes over usable apps; an app with any "
            "unknown repeat or a task cell with any unknown app remains N/A. Cell denominators are in "
            "task_deployed_coverage.csv. "
            "Where recorded, observed final-deployment rates use all usable outcome rows; "
            "full-cohort functional bounds range unknown grades from failure to success "
            "and are not confidence intervals.",
            "",
            "Plotted source-size and complexity summaries cover non-missing measurements of valid generated "
            "outputs, matching code_metrics.json. The app export's collected_loc, collected_loc_backend and "
            "collected_loc_frontend columns also retain collected trees from invalid outputs: a recorded empty "
            "tree contributes zero there, while an uncollected tree remains missing. Billed cost summaries "
            "(cost_per_app) are also conditional on generated apps; the cost at list price, where recorded, covers "
            "every usable run (failed runs spent tokens too). "
            "Issue summaries cover measured analysis results. "
            "Code issue density is deduplicated model-code issues per thousand model-scope source lines. "
            f"The saved scope setting is `{(protocol.get('settings') or {}).get('code_scope', 'see protocol.json')}`. "
            "Dependency advisories, runtime findings and test-code issues are separated in the raw report tables.",
            "",
            *selection_methods(data.get("selection")),
            *final_assessment_methods(data),
            "## Statistical status and uncertainty",
            "",
            "Evaluation selection: "
            f"`{(protocol.get('evaluation_selection') or {}).get('suite_ref') or 'per-run planned suite'}`. "
            "An explicitly selected suite uses its latest final non-retest evaluation, without falling back "
            "to another instrument for missing grades. Changing the planned suite is a versioned exploratory "
            "reanalysis of the same source cohort; the protocol records how many runs changed instruments.",
            "",
            "Prompt-tagged sensitivity separates checks whose recorded stated_in annotation includes the "
            "selected prose variant from additional conventions. Strict all-check results remain unchanged. "
            "Missing tags/subsets remain unknown. A passing stated-tagged subset does not establish complete "
            "prompt compliance or interactive UI correctness; rates condition on measured app units.",
            "",
            f"Analysis plan hash: `{study.get('analysis_plan_hash') or 'not recorded'}`. "
            f"Design confirmatory status: `{confirmatory.get('design', 'not recorded')}`. "
            f"Recorded reasons: {json.dumps(confirmatory.get('reasons') or [])}.",
            "",
            "Model-parameter correlations are exploratory associations with one point per model; conditions sharing "
            "a model are averaged equally. Nominal and effective context windows are separate predictors. "
            "Where recorded, inspect per-model parameter sources and observed_values in correlations.json: "
            "frozen endpoint limits, budgets and prices differ from nominal capacity metadata, "
            "which can be a current-catalog fallback. The source report identifies excluded uncertain parameters. "
            "Regenerate older snapshots without parameter provenance before interpreting historical limits. "
            "The report supplies permutation p-values, bootstrap intervals, Holm and Benjamini-Hochberg corrections, "
            "and leave-one-model-out influence. These associations cannot isolate context from correlated prices, "
            "output budgets, model families, release dates or other unrecorded differences. "
            "The predictor collinearity plot is descriptive and does not adjust these confounders.",
            "",
            "Declared comparisons use the report's shared app/repeat cohort and recorded baseline, "
            "sign/Wilcoxon tests and multiplicity policy. Unavailable or interim tests remain unavailable. "
            "Where supplied, paired effect intervals resample app deltas "
            "and are descriptive percentile 95% intervals, "
            "not simultaneous intervals adjusted for multiplicity. Functional error bars show sample SD across apps, "
            "not a confidence interval. Outcome figure intervals are the report's recorded app-level intervals.",
            "Without a declared baseline plan, a selected completed study may have explicitly exploratory "
            "condition-pair comparisons. These pair shared task/slot units and repeat indices and show B minus A. "
            "Their Wilcoxon tests share one Holm family across all tested metrics and pairs; sign-test sensitivity "
            "has a separate Holm family. Significance is withheld below the recorded minimum pair count, "
            "although descriptive effects can be shown. Missing p-values, p=1 for all-zero differences, and "
            "zero-width empirical bootstrap ranges do not establish equivalence. "
            "These condition-system comparisons do not isolate model causation.",
            "",
            "Threshold sensitivity classifies each measured run at each shown objective pass fraction, then averages "
            "repeats within app and apps within group. No threshold is selected after seeing these curves. "
            "Task deletion ranges remove each observed task within a group's own sample; "
            "they are not confidence intervals. Groups may have different task sets, "
            "so these plots do not by themselves establish controlled model differences. "
            "App-level scatterplots share model/task clusters and are not independent model-level evidence.",
            "",
            "## Rankings",
            "",
            "Ranking criteria, directions and researcher-chosen weights are exported in ranking.json. "
            "TOPSIS, weighted-sum, equal-weight, one-at-a-time perturbation and recorded Monte Carlo/data-bootstrap "
            "results show dependence on methods, weights and sample. "
            "Missing criteria exclude a group; ties share rank. "
            "Data-bootstrap rank frequencies and ranges are conditional on retained draws in which every "
            "alternative has defined criteria. Attempted and retained draw counts, the resampling design and "
            "Monte Carlo standard errors are exported in ranking.json; missing-criterion draws are not zeros. "
            "LOC is shown as descriptive footprint, not a quality benefit in the default ranking. "
            "The normalized scorecard is not a composite score; constant columns take the neutral visual value 0.5.",
            "A recorded zero cost can reflect subscription, routing or accounting policy; "
            "it does not establish free generation or economic superiority. Catalog/list prices and "
            "reported usage-cost estimates are not necessarily realized marginal costs. "
            "Interpret any cost-based preference alongside recorded pricing provenance and sensitivity results.",
            "",
            "## Instruments and security",
            "",
            "Per-tool heatmaps count raw findings per measured app and exclude dependency scanners, AI reviewers "
            "and runtime/performance tools. Per-tool reports can overlap; they are not summed as unique defects. "
            "Issue summaries use recorded cross-tool deduplication. Co-detection uses only comparable issue classes "
            "and does not estimate precision or false-positive rates. "
            "Static severity labels are platform-normalized heuristics.",
            "Issue-population bars separate model code, test code, model-added/inherited dependency advisories "
            "and runtime issue classes. Each population follows its recorded deduplication rule "
            "and measurement cohort. "
            "The bars are not stacked or asserted to be additive; all-category severity totals are not counts of "
            "confirmed model defects. N/A means no measurement; a labelled zero is a measured zero.",
            "",
            "Zero detected code-security findings bound rates only under the recorded static instruments. "
            "Runtime summaries cover only app units on which the selected probe successfully measured an outcome; "
            "unmeasured deployments are outside that population. Intervals and zero-count upper bounds are drawn "
            "only when supplied by the report; fixed-task, selected-endpoint samples can instead require "
            "descriptive rates without an independent-sampling confidence bound. "
            "Zero observed exploits do not establish general security.",
            "",
            "## Execution provenance and task coverage",
            "",
            f"Execution provenance: {'recorded' if execution else 'not recorded; regenerate to collect'}. "
            f"Task and requirement mapping: {'recorded' if requirements else 'not recorded; regenerate to collect'}.",
            f"Recorded provenance coverage: {json.dumps(execution.get('coverage') or {})}.",
            "",
            "When present, execution_runs.csv and execution_attempts.csv describe generation retries, call classes, "
            "evaluation stages, tester/build attempts, lock source and registry snapshots. "
            "Model-generated source and platform-materialized dependency locks are distinct provenance. "
            "Lock-source deployment strata are observational: "
            "different apps, models or failures may select into a stratum. "
            "They cannot estimate causal repair lift without a matched original-versus-modified experiment.",
            "",
            "Requirements task/check/coverage CSVs retain pinned task definitions, "
            "criteria and available probe mappings. "
            "Fixed stacks, starters, harnesses, prompts and selected tasks limit generalization. "
            "The saved protocol and validity messages record attrition, instrument coverage, truncation, "
            "provider variation and plan deviations. Unresolved evidence checks remain in evidence_checks.csv.",
            "Task-specific oracle entries, where recorded, are exported separately and require matching "
            "the collected stack and evaluation suite. Missing oracle validation remains unavailable. "
            "Generic evaluator reference/mutant conformance tests exercise the evaluation machinery; "
            "they do not supply task-specific ground truth or validate complete application correctness.",
            "",
            "## Reproduction",
            "",
            "When included, supplemental_evidence contains separately collected frozen-source experiments. "
            "Matched lock observations compare the original collected locks to the exact recorded platform "
            "locks; paired transition counts and measured-both-arms rates describe that finite selected cohort. "
            "They have no automatic independent-pair confidence interval. Stateful feature checks retain failed, "
            "blocked and inapplicable checks separately and include control and protocol records. "
            "These imported source-bound experiments do not replace primary suite grades. This workflow "
            "regenerates their tables and figures from recorded outcomes; it does not launch Docker rebuilds "
            "or execute the feature tests in the browser. Raw experiment traces are separate provenance artifacts.",
            "",
            "The bundle includes the report snapshot, full raw run/app tables, source statistical payloads, "
            "CSV figure data and PNG/SVG figures. manifest.json records package versions, the data fingerprint, "
            "completed/skipped/error steps and their outputs. Display-table CSVs may be presentation-limited; "
            "raw report and run/app exports retain the full saved sample.",
            "When Python retains the executed workflow source, analysis_workflow.py and its SHA-256 are "
            "included in the bundle. Run it with json.load(open('report.json', encoding='utf-8')) as the "
            "report argument to run_report_workflow. Notebook source edits are retained; check the workflow_source "
            "step before treating an archive as independently reproducible.",
        ]
        (output / "methods.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest["files"].append("methods.md")

    step("methods_and_interpretation", methods)

    def checks():
        protocol = data.get("protocol") or {}
        oracle = task_oracle_evidence(protocol)
        if oracle["records"]:
            table("task_oracle_records", _frame(oracle["records"]))
        if oracle["scope_checks"]:
            table("task_oracle_scope_checks", _frame(oracle["scope_checks"]))
        records = [
            {k: value for k, value in oracle.items() if k not in ("records", "scope_checks")},
            {
                "check": "human_evaluator_validation",
                "status": "unavailable",
                "detail": (
                    "This report contains no matched human-label validation cohort; "
                    "evaluator accuracy and agreement are not established."
                ),
            },
            {
                "check": "scanner_precision",
                "status": "unavailable",
                "detail": "Tool overlap is not a false-positive audit; matched manual adjudication is required.",
            },
            {
                "check": "paired_repair_effect",
                "status": "recorded"
                if (data.get("supplemental_evidence") or {}).get("lock_materialization", {}).get("complete")
                else "unavailable",
                "detail": (
                    "A separate matched lock replay is included; its finite-cohort outcomes do not establish "
                    "population repair benefit or replace primary grades."
                    if (data.get("supplemental_evidence") or {}).get("lock_materialization", {}).get("complete")
                    else "No paired source/lock intervention is recorded. Source policy and lock-source strata "
                    "describe provenance, not causal deployment lift."
                ),
            },
            {
                "check": "cost_interpretation",
                "status": "warning"
                if any(
                    r.get("cost_per_app") == 0 and r.get("list_cost_per_app") is None
                    for r in data.get("model_outcomes") or []
                )
                else "info",
                "detail": "Recorded zero costs may reflect subscription or accounting policy, not free generation. "
                "Catalog/list prices are not necessarily realized marginal costs; inspect pricing provenance."
                + (
                    " The comparable cost at list price (list_cost_per_app) prices every run alike; its basis is in "
                    "cost_basis.json and cost_basis_per_model.csv."
                    if protocol.get("cost_basis")
                    else ""
                ),
            },
            {
                "check": "supplemental_feature_validation",
                "status": "recorded"
                if (data.get("supplemental_evidence") or {}).get("feature_lifecycle", {}).get("complete")
                else "unavailable",
                "detail": "Separate stateful API checks, when included, cover only their declared selected tasks. "
                "Blocked prerequisites do not prove feature absence. Interactive UI requirements remain unvalidated.",
            },
            {
                "check": "analysis_plan",
                "status": "recorded" if (protocol.get("study") or {}).get("analysis_plan_hash") else "unavailable",
                "detail": json.dumps(protocol.get("confirmatory") or {"reason": "No confirmatory design information"}),
            },
            {
                "check": "repeat_counts",
                "status": "recorded" if not apps.empty else "unavailable",
                "detail": json.dumps(apps["repeats"].value_counts().sort_index().to_dict())
                if not apps.empty
                else "No app observations",
            },
            {
                "check": "stochastic_repeat_variability",
                "status": "warning" if not apps.empty and apps["repeats"].max() == 1 else "info",
                "detail": "One run per app cannot estimate within-model stochastic repeat variability. "
                "Intervals across app/task units do not substitute for repeated generations; inspect repeat counts.",
            },
            {
                "check": "instrument_coverage",
                "status": "recorded" if protocol.get("instrument_coverage") else "unavailable",
                "detail": "Coverage and excluded instruments are exported in protocol.json.",
            },
            {
                "check": "zero_security_findings",
                "status": "recorded" if data.get("security_bounds") else "unavailable",
                "detail": (
                    "Zero findings bound the rate only in measured apps under the recorded instruments; "
                    "see security_bounds.json."
                ),
            },
        ]
        selection = data.get("selection")
        if isinstance(selection, dict) and (selection.get("matrix") or selection.get("groups")):
            selection_counts = selection.get("counts") or {}
            groups = [g for g in selection.get("groups") or [] if isinstance(g, dict)]
            task_counts = sorted({g.get("tasks_selected") for g in groups if g.get("tasks_selected") is not None})
            unequal = len(task_counts) > 1 and not (selection.get("filters") or {}).get("common_tasks_only")
            records.append(
                {
                    "check": "sample_selection",
                    "status": "warning" if unequal else "recorded",
                    "detail": f"Newest usable run per group and task across {len(selection.get('studies') or [])} "
                    f"experiments; selected={selection_counts.get('selected')}, "
                    f"superseded={selection_counts.get('superseded')}, "
                    f"attrition_cells={selection_counts.get('attrition_cells')}, "
                    f"missing_cells={selection_counts.get('missing_cells')}. "
                    + (
                        f"Groups cover different numbers of tasks ({task_counts[0]}-{task_counts[-1]}); "
                        "means and rankings compare unequal task sets unless complete cases are used."
                        if unequal
                        else "See selection_matrix.csv and selection_superseded.csv."
                    ),
                },
            )
        for warning in data.get("validity") or []:
            records.append(
                {
                    "check": warning.get("code", "report_validity"),
                    "status": warning.get("level", "warning"),
                    "detail": warning.get("message") or warning.get("detail") or json.dumps(warning),
                },
            )
        table("evidence_checks", _frame(records))
        manifest["evidence_checks"] = records

    step("evidence_checks", checks)
    manifest["status"] = "error" if any(s["status"] == "error" for s in manifest["steps"]) else "completed"
    manifest["steps_skipped"] = sum(s["status"] == "skipped" for s in manifest["steps"])
    manifest["files"].append("manifest.json")
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in dict.fromkeys(manifest["files"]):
            bundle.write(output / name, name)
    return {"manifest": manifest, "archive": str(archive), "output_dir": str(output)}
