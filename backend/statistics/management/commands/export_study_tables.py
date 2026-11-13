"""Write the study tables a paper reports as CSV + JSON.

Usage:
    python manage.py export_study_tables --out results/ [--study <uuid>] [--stage main|pilot]
        [--include-ai] [--line-tolerance 2] [--all-tools] [--no-spreadsheet-safe]

Scope: the study's current runs (the first usable attempt per cell at ``--stage``, beyond-plan runs
excluded; ``runs.view.current_runs``), else every run's current attempt.

Files (one row per entity, deduplicated issues next to raw findings):
    issues_per_app.csv, issues_by_model.csv, issues_by_condition.csv,
    issues_by_task.csv, issues_by_model_task.csv, issues_by_tool.csv,
    issues_by_tool_rule.csv, tool_overlap.csv, issue_clusters.csv,
    tool_coverage.csv, tool_inventory.csv, tool_executions.csv,
    code_metrics_per_app.csv (radon / lizard / jscpd metrics of each tool's latest result, with
    the instrument behind them; empty cells: the tool did not measure the app),
    study_summary.json (scope, totals, coverage, inventory counts, accounting)

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

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.export.services.csv_safe import safe_cell
from backend.reports.services.loc import CODE_SCOPES
from backend.reports.services.loc import use_code_scope
from backend.statistics.services.accounting import run_accounting
from backend.statistics.services.accounting import tool_inventory
from backend.statistics.services.study import BY_TOOL_KEYS
from backend.statistics.services.study import BY_TOOL_RULE_KEYS
from backend.statistics.services.study import CODE_METRIC_PER_APP_KEYS
from backend.statistics.services.study import GROUP_SUMMARY_KEYS
from backend.statistics.services.study import ISSUE_KEYS
from backend.statistics.services.study import PER_APP_KEYS
from backend.statistics.services.study import TOOL_PAIR_KEYS
from backend.statistics.services.study import code_metrics_per_app
from backend.statistics.services.study import issue_breakdowns
from backend.statistics.services.study import study_runs

TOOL_EXECUTION_KEYS = ("tool", "category", "executions", "completed", "failed", "skipped", "unfinished", "apps")
# Coverage counts runs: repeats of one app are measured separately.
TOOL_COVERAGE_KEYS = ("tool", "counted", "runs_measured", "runs_missing", "reasons")


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
    help = "Export deduplicated issue breakdowns, code metrics and tool accounting as CSV/JSON."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Output directory (created if missing)")
        parser.add_argument("--study", default=None, help="Study id to scope to (its current runs)")
        parser.add_argument(
            "--stage",
            default="main",
            choices=("main", "pilot"),
            help="Study stage to export (pilot and main are never pooled; default main)",
        )
        parser.add_argument("--include-ai", action="store_true", help="Count AI-reviewer findings too")
        parser.add_argument("--line-tolerance", type=int, default=2, help="Line distance for cross-tool merges")
        parser.add_argument(
            "--code-scope",
            default=None,
            choices=CODE_SCOPES,
            help=(
                "What counts as the delivered app: app (default; tests and scratch scripts apart), app_named "
                "(scripts matched only by name stay app code), with_scratch (only tests apart), all"
            ),
        )
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
        # Every table of one export is computed with the same scope (findings and LOC alike).
        with use_code_scope(opts.get("code_scope")) as scope:
            self._export(scope, **opts)

    def _export(self, scope: str, **opts):
        out = Path(opts["out"])
        out.mkdir(parents=True, exist_ok=True)
        study_id = opts["study"]
        if study_id:
            from backend.generation.models import Study

            try:
                exists = Study.objects.filter(pk=study_id).exists()
            except (ValueError, ValidationError) as exc:
                msg = f"--study must be a study id: {exc}"
                raise CommandError(msg) from exc
            if not exists:
                msg = f"no study {study_id}"
                raise CommandError(msg)
        runs = list(study_runs(study_id=study_id, stage=opts["stage"]))

        def write(name: str, rows: list[dict[str, Any]], header=()) -> None:
            _write_csv(out / name, rows, header, spreadsheet_safe=opts["spreadsheet_safe"])

        issues = issue_breakdowns(
            runs,
            include_ai=opts["include_ai"],
            line_tolerance=opts["line_tolerance"],
            common_tools_only=not opts["all_tools"],
        )
        for key, name, header in (
            ("per_app", "issues_per_app", PER_APP_KEYS),
            ("by_model", "issues_by_model", ("model", *GROUP_SUMMARY_KEYS)),
            ("by_condition", "issues_by_condition", ("condition_id", "condition_label", *GROUP_SUMMARY_KEYS)),
            ("by_task", "issues_by_task", ("task", *GROUP_SUMMARY_KEYS)),
            ("by_model_task", "issues_by_model_task", ("model", "task", *GROUP_SUMMARY_KEYS)),
            ("by_tool", "issues_by_tool", BY_TOOL_KEYS),
            ("by_tool_rule", "issues_by_tool_rule", BY_TOOL_RULE_KEYS),
            ("tool_pairs", "tool_overlap", TOOL_PAIR_KEYS),
            ("issues", "issue_clusters", ISSUE_KEYS),
        ):
            write(f"{name}.csv", issues[key], header)
        write("tool_coverage.csv", coverage_rows(issues), TOOL_COVERAGE_KEYS)
        write("code_metrics_per_app.csv", code_metrics_per_app(runs), CODE_METRIC_PER_APP_KEYS)

        inventory = tool_inventory()
        write(
            "tool_inventory.csv",
            [{**t, "source": "catalog"} for t in inventory["analyzers"]]
            + [{**e, "source": "platform", "is_enabled": True} for e in inventory["evaluators"]],
        )
        accounting = run_accounting(runs)
        write("tool_executions.csv", accounting["tools"]["latest"], TOOL_EXECUTION_KEYS)

        summary = {
            "scope": {"study": study_id, "stage": opts["stage"], "runs": len(runs), "code_scope": scope},
            "issues": {"params": issues["params"], "coverage": issues["coverage"], "totals": issues["totals"]},
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
