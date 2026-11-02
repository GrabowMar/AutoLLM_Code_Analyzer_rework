"""Write the study tables a paper reports as CSV + JSON.

Usage:
    python manage.py export_study_tables --out results/ [--experiment <uuid>] [--batch <uuid>]
        [--include-ai] [--line-tolerance 2] [--all-tools] [--no-spreadsheet-safe]

Files (one row per entity, deduplicated issues next to raw findings):
    issues_per_app.csv, issues_by_model.csv, issues_by_condition.csv,
    issues_by_template.csv, issues_by_model_template.csv, issues_by_tool.csv,
    issues_by_tool_rule.csv, tool_overlap.csv, issue_clusters.csv,
    tool_coverage.csv, healing_by_model.csv, healing_by_condition.csv,
    healing_by_template.csv, healing_per_app.csv, tool_inventory.csv, tool_executions.csv,
    code_metrics_per_app.csv (radon / lizard / jscpd metrics of each tool's latest result, with
    the instrument behind them; empty cells: the tool did not measure the app),
    study_summary.json (totals, coverage, healing, inventory counts, accounting)

A table with no rows still gets its real header. Text cells a spreadsheet
would read as a formula (``=``, ``+``, ``-``, ``@``) get a leading apostrophe
(``backend.export.services.csv_safe``); ``--no-spreadsheet-safe`` writes them
verbatim (for pandas/R). Severity columns named ``*_normalized_*`` hold the
platform's normalized severity (a heuristic), not a level a tool printed
(``reported_levels``).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from backend.export.services.csv_safe import safe_cell
from backend.statistics.services.accounting import run_accounting
from backend.statistics.services.accounting import tool_inventory
from backend.statistics.services.study import BY_TOOL_KEYS
from backend.statistics.services.study import BY_TOOL_RULE_KEYS
from backend.statistics.services.study import CODE_METRIC_PER_APP_KEYS
from backend.statistics.services.study import GROUP_SUMMARY_KEYS
from backend.statistics.services.study import HEALING_PER_APP_KEYS
from backend.statistics.services.study import ISSUE_KEYS
from backend.statistics.services.study import PER_APP_KEYS
from backend.statistics.services.study import TOOL_PAIR_KEYS
from backend.statistics.services.study import code_metrics_per_app
from backend.statistics.services.study import healing_impact
from backend.statistics.services.study import issue_breakdowns
from backend.statistics.services.study import study_jobs

TOOL_EXECUTION_KEYS = ("tool", "category", "executions", "completed", "failed", "skipped", "unfinished", "apps")
# Coverage counts jobs (runs): repeats of one app are measured separately.
TOOL_COVERAGE_KEYS = ("tool", "counted", "runs_measured", "runs_missing", "reasons")
# Healing pairs are runs; apps are the distinct (condition, app) units among them.
HEALING_GROUP_KEYS = ("apps", "runs")


def _cell(value: Any) -> Any:
    if isinstance(value, list):
        return ";".join(map(str, value))
    if isinstance(value, dict):
        return ";".join(f"{k}={v}" for k, v in value.items())
    return value


def _shown(value: Any) -> str:
    return "-" if value is None else str(value)


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    header: tuple[str, ...] | list[str] = (),
    *,
    spreadsheet_safe: bool = True,
) -> None:
    """Rows as CSV; *header* names the columns when there are no rows (never a placeholder).

    With *spreadsheet_safe* a text cell (lists already joined) that a
    spreadsheet would run as a formula is prefixed with an apostrophe;
    numbers are left alone.
    """
    columns: list[str] = []
    for row in rows:
        columns.extend(k for k in row if k not in columns)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns or list(header))
        writer.writeheader()
        for row in rows:
            cells = {k: _cell(v) for k, v in row.items()}
            writer.writerow({k: safe_cell(v) for k, v in cells.items()} if spreadsheet_safe else cells)


def coverage_rows(issues: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per tool: whether its findings were counted, how many runs it measured, and why it missed others."""
    coverage = issues["coverage"]
    all_tools = not issues["params"]["common_tools_only"]
    rows = [
        {"tool": t, "counted": True, "runs_measured": coverage["apps"], "runs_missing": 0, "reasons": ""}
        for t in coverage["tools_common"]
    ]
    for tool, entry in coverage["tools_partial"].items():
        reasons = {k: v for k, v in entry.items() if k not in ("apps_measured", "apps_missing")}
        rows.append(
            {
                "tool": tool,
                "counted": all_tools and entry["apps_measured"] > 0,
                "runs_measured": entry["apps_measured"],
                "runs_missing": entry["apps_missing"],
                "reasons": _cell(reasons),
            },
        )
    return sorted(rows, key=lambda r: r["tool"])


class Command(BaseCommand):
    help = "Export deduplicated issue breakdowns, healing ablation and tool accounting as CSV/JSON."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Output directory (created if missing)")
        parser.add_argument("--experiment", default=None, help="Experiment id to scope to")
        parser.add_argument("--batch", default=None, help="Generation batch id to scope to")
        parser.add_argument("--include-ai", action="store_true", help="Count AI-reviewer findings too")
        parser.add_argument("--line-tolerance", type=int, default=2, help="Line distance for cross-tool merges")
        parser.add_argument(
            "--all-tools",
            action="store_true",
            help="Count every tool's findings, not only tools that measured every app in scope",
        )
        parser.add_argument(
            "--spreadsheet-safe",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Prefix text cells a spreadsheet would read as a formula with an apostrophe (default on)",
        )

    def handle(self, *args, **opts):
        out = Path(opts["out"])
        out.mkdir(parents=True, exist_ok=True)
        jobs = study_jobs(experiment_id=opts["experiment"], batch_id=opts["batch"])

        def write(name: str, rows: list[dict[str, Any]], header=()) -> None:
            _write_csv(out / name, rows, header, spreadsheet_safe=opts["spreadsheet_safe"])

        issues = issue_breakdowns(
            jobs,
            include_ai=opts["include_ai"],
            line_tolerance=opts["line_tolerance"],
            common_tools_only=not opts["all_tools"],
        )
        for key, name, header in (
            ("per_app", "issues_per_app", PER_APP_KEYS),
            ("by_model", "issues_by_model", ("model", *GROUP_SUMMARY_KEYS)),
            ("by_condition", "issues_by_condition", ("condition_id", "condition_label", *GROUP_SUMMARY_KEYS)),
            ("by_template", "issues_by_template", ("template", *GROUP_SUMMARY_KEYS)),
            ("by_model_template", "issues_by_model_template", ("model", "template", *GROUP_SUMMARY_KEYS)),
            ("by_tool", "issues_by_tool", BY_TOOL_KEYS),
            ("by_tool_rule", "issues_by_tool_rule", BY_TOOL_RULE_KEYS),
            ("tool_pairs", "tool_overlap", TOOL_PAIR_KEYS),
            ("issues", "issue_clusters", ISSUE_KEYS),
        ):
            write(f"{name}.csv", issues[key], header)
        write("tool_coverage.csv", coverage_rows(issues), TOOL_COVERAGE_KEYS)
        write("code_metrics_per_app.csv", code_metrics_per_app(jobs), CODE_METRIC_PER_APP_KEYS)

        healing = healing_impact(jobs)
        # Agent builds are never healed: their scopes have no rows, only headers.
        write("healing_by_model.csv", healing["by_model"], ("model", *HEALING_GROUP_KEYS))
        write(
            "healing_by_condition.csv",
            healing["by_condition"],
            ("condition_id", "condition_label", *HEALING_GROUP_KEYS),
        )
        write("healing_by_template.csv", healing["by_template"], ("template", *HEALING_GROUP_KEYS))
        write("healing_per_app.csv", healing["per_app"], HEALING_PER_APP_KEYS)

        inventory = tool_inventory()
        write(
            "tool_inventory.csv",
            [{**t, "source": "catalog"} for t in inventory["analyzers"]]
            + [{**e, "source": "platform", "is_enabled": True} for e in inventory["evaluators"]],
        )
        accounting = run_accounting(jobs)
        write("tool_executions.csv", accounting["tools"]["latest"], TOOL_EXECUTION_KEYS)

        summary = {
            "scope": {"experiment": opts["experiment"], "batch": opts["batch"]},
            "issues": {"params": issues["params"], "coverage": issues["coverage"], "totals": issues["totals"]},
            "healing": {
                k: healing[k]
                for k in ("baseline", "modes", "not_applicable", "totals", "added_requirements", "patches")
            },
            "tool_counts": inventory["counts"],
            "accounting": accounting,
        }
        (out / "study_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

        t = issues["totals"]
        self.stdout.write(
            self.style.SUCCESS(
                f"{t['apps']} analyzed apps ({t['runs']} runs): {_shown(t['raw_findings'])} raw findings -> "
                f"{_shown(t['unique_issues'])} unique issues ({_shown(t['multi_tool_issues'])} reported by >1 tool). "
                f"Tools: {inventory['counts']['total_enabled']} enabled; "
                f"executions recorded: {accounting['executions']['total']}. Wrote {out}/",
            ),
        )
