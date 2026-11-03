"""Generate the study report from the shell and write it as Markdown, LaTeX or JSON.

Usage:
    python manage.py study_report --study <uuid> --out results/study.md [--format md|tex|json]
        [--user <username>] [--seed 0] [--include-ai] [--exclude-truncated] [--no-bootstrap] [--save]

Without ``--study`` the report covers every sample run in scope. Without ``--user`` it covers every
user's runs (staff view); with ``--study`` and no ``--user`` it acts as the study's owner.
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
        parser.add_argument("--study", default=None, help="Study id: its current runs (main stage)")
        parser.add_argument("--user", default=None, help="Scope to this username's runs")
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--include-ai", action="store_true")
        parser.add_argument(
            "--exclude-truncated",
            action="store_true",
            help="Sensitivity analysis: leave truncated runs out (a locked analysis plan's rule wins)",
        )
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
        elif opts["study"]:
            # owned_study needs a user who may see it: act as its owner.
            from django.core.exceptions import ValidationError

            from backend.generation.models import Study

            try:
                study = Study.objects.filter(id=opts["study"]).select_related("created_by").first()
            except ValidationError:
                study = None
            if study is None:
                msg = f"No study {opts['study']}"
                raise CommandError(msg)
            user = study.created_by

        config = {
            "study_id": opts["study"],
            "seed": opts["seed"],
            "include_ai": opts["include_ai"],
            "bootstrap": not opts["no_bootstrap"],
        }
        if opts["exclude_truncated"]:
            config["exclude_truncated"] = True
        try:
            data = generate_study_report(config, user=user)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        # The study's name, not its uuid, when the report covers one.
        scope = ((data.get("protocol") or {}).get("study") or {}).get("name")
        scope = scope or opts["study"] or "all apps"
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
