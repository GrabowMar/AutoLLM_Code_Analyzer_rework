"""Write the study tables a paper reports as CSV + JSON.

Usage:
    python manage.py export_study_tables --out results/ [--experiment <uuid>] [--batch <uuid>]
        [--include-ai] [--line-tolerance 2]

Files (one row per entity, deduplicated issues next to raw findings):
    issues_per_app.csv, issues_by_model.csv, issues_by_template.csv,
    issues_by_model_template.csv, issues_by_tool.csv, issues_by_tool_rule.csv,
    tool_overlap.csv, issue_clusters.csv, healing_by_model.csv,
    healing_per_app.csv, tool_inventory.csv, tool_executions.csv,
    study_summary.json (totals, healing, inventory counts, accounting)
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from backend.statistics.services.accounting import run_accounting
from backend.statistics.services.accounting import tool_inventory
from backend.statistics.services.study import healing_impact
from backend.statistics.services.study import issue_breakdowns
from backend.statistics.services.study import study_jobs


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns: list[str] = []
    for row in rows:
        columns.extend(k for k in row if k not in columns)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns or ["empty"])
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ";".join(map(str, v)) if isinstance(v, list) else v for k, v in row.items()})


class Command(BaseCommand):
    help = "Export deduplicated issue breakdowns, healing ablation and tool accounting as CSV/JSON."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True, help="Output directory (created if missing)")
        parser.add_argument("--experiment", default=None, help="Experiment id to scope to")
        parser.add_argument("--batch", default=None, help="Generation batch id to scope to")
        parser.add_argument(
            "--include-ai",
            action="store_true",
            help="Also write AI consensus issues (ai_issue_clusters.csv); never counted as static issues",
        )
        parser.add_argument(
            "--line-tolerance",
            type=int,
            default=2,
            help="Kept for compatibility: issues are the analysis engine's stored grouping",
        )
        parser.add_argument("--plan-hash", default=None, help="Analysis plan to read (default: auto-selected)")

    def handle(self, *args, **opts):
        out = Path(opts["out"])
        out.mkdir(parents=True, exist_ok=True)
        jobs = study_jobs(experiment_id=opts["experiment"], batch_id=opts["batch"])

        issues = issue_breakdowns(
            jobs,
            include_ai=opts["include_ai"],
            line_tolerance=opts["line_tolerance"],
            plan_hash=opts["plan_hash"],
        )
        if opts["include_ai"]:
            _write_csv(out / "ai_issue_clusters.csv", issues["ai_issues"])
        for key, name in (
            ("per_app", "issues_per_app"),
            ("by_model", "issues_by_model"),
            ("by_template", "issues_by_template"),
            ("by_model_template", "issues_by_model_template"),
            ("by_tool", "issues_by_tool"),
            ("by_tool_rule", "issues_by_tool_rule"),
            ("tool_pairs", "tool_overlap"),
            ("issues", "issue_clusters"),
        ):
            _write_csv(out / f"{name}.csv", issues[key])

        healing = healing_impact(jobs)
        _write_csv(out / "healing_by_model.csv", healing["by_model"])
        _write_csv(out / "healing_by_template.csv", healing["by_template"])
        _write_csv(out / "healing_per_app.csv", healing["per_app"])

        inventory = tool_inventory()
        _write_csv(
            out / "tool_inventory.csv",
            [{**t, "source": "catalog"} for t in inventory["analyzers"]]
            + [{**e, "source": "platform", "is_enabled": True} for e in inventory["evaluators"]],
        )
        # The runs the issue tables read (canonical, one plan), not each job's latest run.
        accounting = run_accounting(jobs, run_ids=[a["run_id"] for a in issues["per_app"]])
        _write_csv(out / "tool_executions.csv", accounting["tools"]["latest"])

        summary = {
            "scope": {"experiment": opts["experiment"], "batch": opts["batch"]},
            "issues": {
                "params": issues["params"],
                "plan_hash": issues["plan_hash"],
                "coverage": issues["coverage"],
                "totals": issues["totals"],
            },
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
                f"{t['apps']} measured apps ({t['static_measured_apps']} with static data): "
                f"{t['raw_findings']} counted findings -> {t['unique_issues']} unique issues "
                f"({issues['totals']['multi_tool_issues']} reported by >1 tool). "
                f"Tools: {inventory['counts']['total_enabled']} enabled; "
                f"executions recorded: {accounting['executions']['total']}. Wrote {out}/",
            ),
        )
