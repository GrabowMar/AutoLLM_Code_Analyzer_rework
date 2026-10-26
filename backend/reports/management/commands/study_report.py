"""Generate the study report from the shell and write it as Markdown, LaTeX or JSON.

Usage:
    python manage.py study_report --experiment <uuid> --out results/study.md [--format md|tex|json]
        [--user <username>] [--seed 0] [--include-ai] [--no-bootstrap] [--save]

Without ``--user`` the report covers every user's jobs in scope (staff view).
``--save`` also stores it as a Report row, so it appears in the UI.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from backend.reports.models import Report
from backend.reports.services.renderers import render_latex
from backend.reports.services.renderers import render_markdown
from backend.reports.services.report_service import _make_summary
from backend.reports.services.study_report import generate_study_report


class Command(BaseCommand):
    help = "Write the study report (funnel, issues, correlations, rankings, validity) to a file."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True)
        parser.add_argument("--format", choices=("md", "tex", "json"), default=None, help="Default: from --out suffix")
        parser.add_argument("--experiment", default=None)
        parser.add_argument("--batch", default=None)
        parser.add_argument("--user", default=None, help="Scope to this username's jobs")
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--include-ai", action="store_true")
        parser.add_argument("--no-bootstrap", action="store_true")
        parser.add_argument("--save", action="store_true", help="Also store a Report row")

    def handle(self, *args, **opts):
        user = None
        if opts["user"]:
            try:
                user = get_user_model().objects.get(username=opts["user"])
            except get_user_model().DoesNotExist as exc:
                msg = f"No user {opts['user']!r}"
                raise CommandError(msg) from exc
        elif opts["experiment"]:
            # owned_experiment needs a user who may see it: act as its owner.
            from backend.generation.models import Experiment

            experiment = Experiment.objects.filter(id=opts["experiment"]).select_related("created_by").first()
            if experiment is None:
                msg = f"No experiment {opts['experiment']}"
                raise CommandError(msg)
            user = experiment.created_by

        config = {
            "experiment_id": opts["experiment"],
            "batch_id": opts["batch"],
            "seed": opts["seed"],
            "include_ai": opts["include_ai"],
            "bootstrap": not opts["no_bootstrap"],
        }
        try:
            data = generate_study_report(config, user=user)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        scope = opts["experiment"] or opts["batch"] or "all apps"
        report = Report(
            report_type=Report.Type.STUDY_REPORT,
            title=f"Study report: {scope}",
            config=config,
            report_data=data,
            summary=_make_summary("study_report", data),
            status=Report.Status.COMPLETED,
            created_by=user,
            completed_at=timezone.now(),
        )
        if opts["save"]:
            report.save()

        out = Path(opts["out"])
        fmt = opts["format"] or {".md": "md", ".tex": "tex", ".json": "json"}.get(out.suffix, "md")
        body = {
            "md": render_markdown,
            "tex": render_latex,
            "json": lambda r: json.dumps(r.report_data, indent=2, default=str),
        }[fmt](report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        warnings = [v for v in data["validity"] if v["level"] == "warning"]
        self.stdout.write(self.style.SUCCESS(f"Wrote {out} ({fmt}); {len(warnings)} validity warning(s)."))
        for v in warnings:
            self.stdout.write(f"  - {v['message']}")
